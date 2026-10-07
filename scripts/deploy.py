"""Despliegue completo en AWS (us-east-1). Lo invocan `deploy.ps1` y `deploy.sh`.

Orden (Req. 22.4): storage → auth → bedrock → compute → frontend; después la imagen del agente en ECR, el
Runtime_Agente en AgentCore, la SPA en S3 y la prueba de humo.

ANTES DE TOCAR LA CUENTA muestra qué se va a crear y pide confirmación (`--yes` la omite; `--plan` solo imprime
los comandos y no ejecuta nada). Si un stack falla, se detiene y nombra el stack y la causa.

Uso:
  python scripts/deploy.py --plan --budget-email ti@ejemplo.com
  python scripts/deploy.py --budget-email ti@ejemplo.com [--store-domain tienda.ejemplo.com] [--yes]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
CFN = ROOT / "cloudformation"
REGION = "us-east-1"

STACKS: list[tuple[str, str, str]] = [
    # (clave, nombre del stack, archivo)
    ("storage", "ultra-skincare-storage", "01-base-storage-db.yaml"),
    ("auth", "ultra-skincare-auth", "02-auth-cognito.yaml"),
    ("bedrock", "ultra-skincare-bedrock", "03-bedrock-kb-guardrail.yaml"),
    ("compute", "ultra-skincare-compute", "04-api-and-lambdas.yaml"),
    ("frontend", "ultra-skincare-frontend", "05-frontend-hosting.yaml"),
]
NAMED_IAM = {"storage", "compute"}  # Req. 22.5
AGENT_RUNTIME_NAME = "ultra_skincare_agent"  # AgentCore solo admite letras, números y guion bajo

PLAN = """\
Se van a crear o actualizar estos recursos en la cuenta {account} ({region}):

  1. ultra-skincare-storage   llave KMS, 4 buckets S3 (hosting, logs, raw data, KB source),
                              4 tablas DynamoDB on-demand, Budget mensual con alerta por correo.
  2. ultra-skincare-auth      User Pool ultra-skincare-userpool, grupos kiosco y caja, KioscoClient y CajaClient.
  3. ultra-skincare-bedrock   Guardrail ultra-skincare-guardrail (+ versión), bucket e índice de S3 Vectors,
                              Knowledge Base (Titan Text Embeddings V2) y su data source.
  4. ultra-skincare-compute   ETL_Lambda, ultra-caja-lambda, HTTP API con JWT Authorizer, repositorio ECR,
                              tópico SNS de derivaciones, 3 roles IAM con nombre{secret}.
  5. ultra-skincare-frontend  CloudFront + OAC + cabeceras de seguridad sobre el bucket de hosting.
  6. Imagen del agente (ARM64) en ECR y Runtime_Agente en Bedrock AgentCore (Inbound Auth con Cognito).
  7. Compilación de la SPA y copia al bucket de hosting; prueba de humo del agente.

Esto genera costos (CloudFront, Bedrock, DynamoDB, Lambda, ECR, AgentCore) y crea roles IAM.
Los buckets, las tablas de datos, el User Pool y la llave KMS se CONSERVAN si borras los stacks.
"""


class DeployError(RuntimeError):
    """Un paso falló; el mensaje nombra el stack o comando y la causa."""


@dataclass
class Options:
    budget_email: str
    budget_limit_usd: int = 100
    store_domain: str = "tienda.example.com"
    handoff_email: str = ""
    domain_name: str = ""
    certificate_arn: str = ""
    image_sources: str = "https:"
    pubmed_api_key: str = ""
    image_tag: str = "latest"
    plan_only: bool = False
    assume_yes: bool = False
    skip_agent: bool = False
    skip_frontend_build: bool = False
    smoke_username: str = ""
    smoke_password: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---- ejecución de comandos -----------------------------------------------------------------------------------------

class Runner:
    """Ejecuta comandos; en modo `--plan` solo los imprime."""

    def __init__(self, dry_run: bool, echo: Callable[[str], None] = print) -> None:
        self.dry_run = dry_run
        self.echo = echo
        self.history: list[list[str]] = []

    def run(self, cmd: list[str], *, capture: bool = False, cwd: Path | None = None, check: bool = True) -> str:
        self.history.append(cmd)
        shown = " ".join(_quote(part) for part in mask_secrets(cmd))
        if self.dry_run:
            self.echo(f"[plan] {shown}")
            return ""
        self.echo(f"$ {shown}")
        proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture, encoding="utf-8")
        if check and proc.returncode != 0:
            raise DeployError(f"falló el comando ({proc.returncode}): {shown}\n{(proc.stderr or '').strip()}")
        return proc.stdout if capture else ""


SECRET_PARAMETERS = ("PubMedApiKey=",)
SECRET_FLAGS = ("--password",)


def mask_secrets(cmd: list[str]) -> list[str]:
    """Copia del comando sin claves ni contraseñas, para imprimirlo."""
    masked: list[str] = []
    hide_next = False
    for part in cmd:
        if hide_next:
            masked.append("****")
            hide_next = False
            continue
        if part in SECRET_FLAGS:
            hide_next = True
        for prefix in SECRET_PARAMETERS:
            if part.startswith(prefix) and len(part) > len(prefix):
                part = prefix + "****"
        masked.append(part)
    return masked


def _quote(part: str) -> str:
    return f'"{part}"' if (" " in part or not part) else part


def aws(*args: str) -> list[str]:
    return ["aws", *args, "--region", REGION, "--output", "json", "--no-cli-pager"]


# ---- stacks -----------------------------------------------------------------------------------------------------------------

def stack_parameters(key: str, o: Options, extra: dict[str, str] | None = None) -> dict[str, str]:
    params: dict[str, dict[str, str]] = {
        "storage": {"BudgetLimitUsd": str(o.budget_limit_usd), "BudgetAlertEmail": o.budget_email},
        "auth": {},
        "bedrock": {},
        "compute": {
            "StoreDomain": o.store_domain,
            "HandoffEmail": o.handoff_email,
            "PubMedApiKey": o.pubmed_api_key,
        },
        "frontend": {"DomainName": o.domain_name, "CertificateArn": o.certificate_arn, "ImageSources": o.image_sources},
    }
    merged = dict(params[key])
    merged.update(extra or {})
    return {k: v for k, v in merged.items() if v != "" or k in {"DomainName", "CertificateArn", "HandoffEmail", "PubMedApiKey"}}


def deploy_stack(r: Runner, key: str, o: Options, extra: dict[str, str] | None = None) -> None:
    _, name, filename = next(s for s in STACKS if s[0] == key)
    cmd = aws(
        "cloudformation", "deploy",
        "--stack-name", name,
        "--template-file", str(CFN / filename),
        "--no-fail-on-empty-changeset",
    )
    if key in NAMED_IAM:
        cmd += ["--capabilities", "CAPABILITY_NAMED_IAM"]
    overrides = [f"{k}={v}" for k, v in stack_parameters(key, o, extra).items()]
    if overrides:
        cmd += ["--parameter-overrides", *overrides]
    try:
        r.run(cmd)
    except DeployError as exc:
        raise DeployError(f"falló el stack {name}: {failure_reasons(r, name) or exc}") from exc


def failure_reasons(r: Runner, stack: str) -> str:
    """Eventos FAILED del stack (recurso y motivo), para nombrar la causa."""
    if r.dry_run:
        return ""
    try:
        out = r.run(
            aws("cloudformation", "describe-stack-events", "--stack-name", stack,
                "--query", "StackEvents[?contains(ResourceStatus, `FAILED`)].[LogicalResourceId,ResourceStatusReason]"),
            capture=True, check=False,
        )
        rows = json.loads(out or "[]")
    except (ValueError, DeployError):
        return ""
    return "; ".join(f"{logical}: {reason}" for logical, reason in rows[:5])


class Placeholders(dict):
    """Salidas simuladas del modo --plan: cada clave devuelve <Clave>."""

    def __missing__(self, key: str) -> str:
        return f"<{key}>"


def stack_outputs(r: Runner, stack: str) -> dict[str, str]:
    if r.dry_run:
        return Placeholders()
    out = r.run(
        aws("cloudformation", "describe-stacks", "--stack-name", stack, "--query", "Stacks[0].Outputs"), capture=True
    )
    return {item["OutputKey"]: item["OutputValue"] for item in json.loads(out or "[]")}


# ---- piezas de la publicación -------------------------------------------------------------------------------------------------

def runtime_environment(o: Options, outputs: dict[str, dict[str, str]], site_url: str) -> dict[str, str]:
    """Variables de entorno del Runtime_Agente (Req. 22.9). Las claves vienen de `advisor/config.py`."""
    storage, bedrock, compute = outputs["storage"], outputs["bedrock"], outputs["compute"]
    env = {
        "PRODUCTION": "true",
        "AWS_REGION": REGION,
        "GUARDRAIL_ID": bedrock["GuardrailId"],
        "GUARDRAIL_VERSION": bedrock["GuardrailVersion"],
        "KB_ID": bedrock["KnowledgeBaseId"],
        "PRODUCTS_TABLE": storage["ProductsTableName"],
        "RECOMMENDATIONS_TABLE": storage["RecommendationsTableName"],
        "SESSIONS_TABLE": storage["SessionsTableName"],
        "EVIDENCE_TABLE": storage["EvidenceTableName"],
        "HANDOFF_TOPIC_ARN": compute["HandoffTopicArn"],
        "HANDOFF_CONFIRM_URL": site_url,
        "STORE_DOMAIN": site_url,
    }
    if "PubMedSecretId" in compute:
        env.update({"PUBMED_ENABLED": "true", "PUBMED_SECRET_ID": compute["PubMedSecretId"]})
    return env


def authorizer_configuration(outputs: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Inbound Auth de AgentCore: solo el KioscoClient puede abrir sesiones de voz (Req. 21.2, 21.12)."""
    auth = outputs["auth"]
    return {"customJWTAuthorizer": {"discoveryUrl": auth["DiscoveryUrl"], "allowedClients": [auth["KioscoClientId"]]}}


def websocket_url(runtime_arn: str) -> str:
    return f"wss://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{urllib.parse.quote(runtime_arn, safe='')}/ws?qualifier=DEFAULT"


def frontend_env(outputs: dict[str, dict[str, str]], site_url: str, ws_url: str) -> dict[str, str]:
    return {
        "VITE_CAJA_API_URL": outputs["compute"]["ApiUrl"],
        "VITE_COGNITO_USER_POOL_ID": outputs["auth"]["UserPoolId"],
        "VITE_COGNITO_CAJA_CLIENT_ID": outputs["auth"]["CajaClientId"],
        "VITE_COGNITO_KIOSCO_CLIENT_ID": outputs["auth"]["KioscoClientId"],
        "VITE_AGENT_WS_URL": ws_url,
        "VITE_STORE_DOMAIN": urllib.parse.urlparse(site_url).netloc,
    }


def publish_image(r: Runner, repo_uri: str, tag: str) -> str:
    registry = repo_uri.split("/", 1)[0]
    image = f"{repo_uri}:{tag}"
    if r.dry_run:
        r.run(["aws", "ecr", "get-login-password", "--region", REGION, "|", "docker", "login", "--username", "AWS", "--password-stdin", registry])
    else:
        password = r.run(["aws", "ecr", "get-login-password", "--region", REGION], capture=True).strip()
        proc = subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry], input=password, text=True, capture_output=True)
        if proc.returncode != 0:
            raise DeployError(f"docker login falló: {proc.stderr.strip()}")
    r.run(["docker", "buildx", "build", "--platform", "linux/arm64", "-f", "src/agent/Dockerfile", "-t", image, "--push", "."], cwd=ROOT)
    return image


def upsert_runtime(r: Runner, image: str, role_arn: str, outputs: dict[str, dict[str, str]], env: dict[str, str]) -> str:
    """Crea el Runtime_Agente o lo actualiza si ya existe. Devuelve su ARN."""
    base = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "authorizerConfiguration": authorizer_configuration(outputs),
        "environmentVariables": env,
    }
    if r.dry_run:
        r.run(aws("bedrock-agentcore-control", "create-agent-runtime", "--agent-runtime-name", AGENT_RUNTIME_NAME, "--cli-input-json", "<json>"))
        return "arn:aws:bedrock-agentcore:us-east-1:<cuenta>:runtime/<id>"
    listing = json.loads(r.run(aws("bedrock-agentcore-control", "list-agent-runtimes"), capture=True) or "{}")
    existing = next((x for x in listing.get("agentRuntimes", []) if x.get("agentRuntimeName") == AGENT_RUNTIME_NAME), None)
    payload = Path(ROOT / "out" / "agent-runtime.json")
    payload.parent.mkdir(parents=True, exist_ok=True)
    try:
        if existing:
            payload.write_text(json.dumps({**base, "agentRuntimeId": existing["agentRuntimeId"]}), encoding="utf-8")
            r.run(aws("bedrock-agentcore-control", "update-agent-runtime", "--cli-input-json", f"file://{payload.as_posix()}"))
            arn = existing["agentRuntimeArn"]
        else:
            payload.write_text(json.dumps({**base, "agentRuntimeName": AGENT_RUNTIME_NAME}), encoding="utf-8")
            created = json.loads(r.run(aws("bedrock-agentcore-control", "create-agent-runtime", "--cli-input-json", f"file://{payload.as_posix()}"), capture=True))
            arn = created["agentRuntimeArn"]
    finally:
        payload.unlink(missing_ok=True)  # contiene las variables de entorno
    return arn


def build_and_upload_site(r: Runner, env: dict[str, str], hosting_bucket: str, distribution_id: str, skip_build: bool) -> None:
    web = ROOT / "src" / "frontend"
    if not skip_build:
        full_env = {**os.environ, **env}
        if r.dry_run:
            r.run(["npm", "ci"], cwd=web)
            r.run(["npm", "run", "build"], cwd=web)
        else:
            for cmd in (["npm", "ci"], ["npm", "run", "build"]):
                r.echo(f"$ {' '.join(cmd)}")
                proc = subprocess.run(cmd, cwd=web, env=full_env, shell=(os.name == "nt"), text=True)
                if proc.returncode != 0:
                    raise DeployError(f"falló {' '.join(cmd)} en src/frontend")
    r.run(["aws", "s3", "sync", str(web / "dist"), f"s3://{hosting_bucket}", "--delete", "--region", REGION])
    r.run(aws("cloudfront", "create-invalidation", "--distribution-id", distribution_id, "--paths", "/*"))


# ---- flujo principal -------------------------------------------------------------------------------------------------------------

def confirm(o: Options, account: str, has_secret: bool, ask: Callable[[str], str] = input, echo: Callable[[str], None] = print) -> bool:
    echo(PLAN.format(account=account, region=REGION, secret=", secreto de PubMed" if has_secret else ""))
    if o.assume_yes:
        return True
    return ask("¿Continuar? Escribe «si» para desplegar: ").strip().lower() in {"si", "sí", "s", "yes", "y"}


def run_deploy(o: Options, r: Runner | None = None, ask: Callable[[str], str] = input) -> int:
    r = r or Runner(dry_run=o.plan_only)
    if not shutil.which("aws") and not o.plan_only:
        raise DeployError("no se encontró el AWS CLI en el PATH")

    account = "<cuenta>"
    if not o.plan_only:
        who = json.loads(r.run(aws("sts", "get-caller-identity"), capture=True))
        account = who["Account"]
    if o.plan_only:
        r.echo(PLAN.format(account=account, region=REGION, secret=", secreto de PubMed" if o.pubmed_api_key else ""))
    elif not confirm(o, account, bool(o.pubmed_api_key), ask=ask, echo=r.echo):
        r.echo("Cancelado: no se cambió nada.")
        return 1

    r.run([sys.executable, str(ROOT / "scripts" / "package_lambdas.py")])

    outputs: dict[str, dict[str, str]] = {}
    for key in ("storage", "auth", "bedrock"):
        deploy_stack(r, key, o)
        outputs[key] = stack_outputs(r, next(s[1] for s in STACKS if s[0] == key))

    raw_bucket = outputs["storage"].get("RawDataBucketName", "<bucket-raw>")
    for name, filename in (("etl.zip", "etl.zip"), ("caja.zip", "caja.zip")):
        r.run(["aws", "s3", "cp", str(ROOT / "out" / "lambda" / filename), f"s3://{raw_bucket}/code/{name}", "--region", REGION])

    compute_extra = {"CodeBucket": raw_bucket}
    deploy_stack(r, "compute", o, compute_extra)
    outputs["compute"] = stack_outputs(r, "ultra-skincare-compute")
    deploy_stack(r, "frontend", o)
    outputs["frontend"] = stack_outputs(r, "ultra-skincare-frontend")

    site_url = outputs["frontend"].get("SiteUrl", "https://<cloudfront>")
    # Segunda pasada: ahora que existe el dominio de la SPA, CORS deja de usar el valor provisional.
    deploy_stack(r, "compute", o, {**compute_extra, "AllowedOrigin": site_url})

    ws_url = "wss://<runtime>"
    if not o.skip_agent:
        image = publish_image(r, outputs["compute"].get("AgentRepositoryUri", "<repo>"), o.image_tag)
        env = runtime_environment(o, outputs, site_url)
        arn = upsert_runtime(r, image, outputs["compute"].get("AgentRuntimeRoleArn", "<rol>"), outputs, env)
        ws_url = websocket_url(arn)
        r.echo(f"Runtime_Agente: {arn}")

    env_web = frontend_env(outputs, site_url, ws_url)
    build_and_upload_site(r, env_web, outputs["storage"].get("HostingBucketName", "<hosting>"), outputs["frontend"].get("DistributionId", "<id>"), o.skip_frontend_build)

    smoke = [sys.executable, str(ROOT / "scripts" / "smoke_test.py"), "--url", ws_url]
    if o.smoke_username and o.smoke_password and not r.dry_run:
        smoke += ["--client-id", outputs["auth"]["KioscoClientId"], "--username", o.smoke_username, "--password", o.smoke_password]
    if o.skip_agent:
        r.echo("Prueba de humo omitida (--skip-agent).")
    elif r.dry_run or (o.smoke_username and o.smoke_password) or os.environ.get("SMOKE_TOKEN"):
        r.run(smoke)
    else:
        r.echo(
            "Prueba de humo pendiente: crea un usuario del grupo kiosco y ejecuta\n  "
            + " ".join(_quote(p) for p in smoke)
            + " --client-id <KioscoClientId> --username <usuario> --password <contraseña>"
        )
    r.echo(f"\nListo. Sube el CSV a s3://{raw_bucket}/raw/ para cargar el catálogo. Sitio: {site_url}")
    return 0


def parse_args(argv: list[str]) -> Options:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--budget-email", required=True, help="correo de la alerta del Budget")
    p.add_argument("--budget-limit-usd", type=int, default=100)
    p.add_argument("--store-domain", default="tienda.example.com", help="dominio de la URL del QR")
    p.add_argument("--handoff-email", default="", help="correo que recibe las derivaciones al asesor")
    p.add_argument("--domain-name", default="", help="dominio propio de la SPA (permite TLS 1.2 mínimo)")
    p.add_argument("--certificate-arn", default="", help="certificado de ACM en us-east-1 para --domain-name")
    p.add_argument("--image-sources", default="https:", help="orígenes de img-src en la CSP")
    p.add_argument("--pubmed-api-key", default=os.environ.get("PUBMED_API_KEY", ""))
    p.add_argument("--image-tag", default="latest")
    p.add_argument("--plan", action="store_true", help="solo imprimir lo que se haría")
    p.add_argument("--yes", action="store_true", help="no pedir confirmación")
    p.add_argument("--skip-agent", action="store_true", help="no publicar la imagen ni crear el Runtime_Agente")
    p.add_argument("--skip-frontend-build", action="store_true")
    p.add_argument("--smoke-username", default=os.environ.get("SMOKE_USERNAME", ""))
    p.add_argument("--smoke-password", default=os.environ.get("SMOKE_PASSWORD", ""))
    a = p.parse_args(argv)
    if bool(a.domain_name) != bool(a.certificate_arn):
        p.error("--domain-name y --certificate-arn van juntos")
    return Options(
        budget_email=a.budget_email, budget_limit_usd=a.budget_limit_usd, store_domain=a.store_domain,
        handoff_email=a.handoff_email, domain_name=a.domain_name, certificate_arn=a.certificate_arn,
        image_sources=a.image_sources, pubmed_api_key=a.pubmed_api_key, image_tag=a.image_tag,
        plan_only=a.plan, assume_yes=a.yes, skip_agent=a.skip_agent, skip_frontend_build=a.skip_frontend_build,
        smoke_username=a.smoke_username, smoke_password=a.smoke_password,
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(run_deploy(parse_args(sys.argv[1:])))
    except DeployError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

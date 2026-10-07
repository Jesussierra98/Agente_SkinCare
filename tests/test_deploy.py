"""Scripts de despliegue: orden, confirmación, secretos y empaquetado (Req. 22.4, 22.9 a 22.11, 22.13)."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


deploy = load_script("deploy")
package = load_script("package_lambdas")


def options(**overrides):
    base = dict(budget_email="ti@ejemplo.com", plan_only=True)
    base.update(overrides)
    return deploy.Options(**base)


def stack_calls(runner) -> list[tuple[str, list[str]]]:
    calls = []
    for cmd in runner.history:
        if cmd[:3] == ["aws", "cloudformation", "deploy"]:
            calls.append((cmd[cmd.index("--stack-name") + 1], cmd))
    return calls


# ---- orden y capacidades ----------------------------------------------------------------------------------------------

def test_los_stacks_se_despliegan_en_el_orden_del_diseno() -> None:
    runner = deploy.Runner(dry_run=True, echo=lambda _: None)
    assert deploy.run_deploy(options(), runner) == 0
    names = [name for name, _ in stack_calls(runner)]
    # compute se repite: la segunda pasada actualiza CORS con el dominio real de la SPA.
    assert names == [
        "ultra-skincare-storage", "ultra-skincare-auth", "ultra-skincare-bedrock",
        "ultra-skincare-compute", "ultra-skincare-frontend", "ultra-skincare-compute",
    ]


def test_solo_storage_y_compute_piden_capacidad_de_iam_con_nombre() -> None:
    runner = deploy.Runner(dry_run=True, echo=lambda _: None)
    deploy.run_deploy(options(), runner)
    for name, cmd in stack_calls(runner):
        has = "CAPABILITY_NAMED_IAM" in cmd
        assert has == (name in {"ultra-skincare-storage", "ultra-skincare-compute"}), name


def test_la_segunda_pasada_de_compute_fija_el_origen_de_cors() -> None:
    runner = deploy.Runner(dry_run=True, echo=lambda _: None)
    deploy.run_deploy(options(), runner)
    first, second = [cmd for name, cmd in stack_calls(runner) if name == "ultra-skincare-compute"]
    assert not any(p.startswith("AllowedOrigin=") for p in first)
    assert any(p.startswith("AllowedOrigin=https://") for p in second)


def test_el_runtime_se_crea_despues_de_la_imagen_y_la_spa_despues_del_runtime() -> None:
    runner = deploy.Runner(dry_run=True, echo=lambda _: None)
    deploy.run_deploy(options(), runner)
    flat = [" ".join(c) for c in runner.history]
    index = lambda needle: next(i for i, c in enumerate(flat) if needle in c)  # noqa: E731
    assert index("buildx build") < index("create-agent-runtime") < index("npm run build") < index("s3 sync") < index("smoke_test.py")


def test_skip_agent_no_publica_imagen_ni_crea_runtime() -> None:
    runner = deploy.Runner(dry_run=True, echo=lambda _: None)
    deploy.run_deploy(options(skip_agent=True), runner)
    flat = " ".join(" ".join(c) for c in runner.history)
    assert "buildx" not in flat and "create-agent-runtime" not in flat and "smoke_test" not in flat


# ---- confirmación antes de tocar la cuenta --------------------------------------------------------------------------------

def test_sin_confirmacion_no_se_ejecuta_nada() -> None:
    out: list[str] = []
    assert deploy.confirm(options(plan_only=False), "123456789012", False, ask=lambda _: "no", echo=out.append) is False
    assert "123456789012" in out[0] and "ultra-skincare-storage" in out[0]


@pytest.mark.parametrize("answer", ["si", "SÍ", " yes "])
def test_confirmar_permite_continuar(answer: str) -> None:
    assert deploy.confirm(options(plan_only=False), "1", False, ask=lambda _: answer, echo=lambda _: None) is True


def test_yes_omite_la_pregunta_pero_igual_muestra_el_plan() -> None:
    out: list[str] = []

    def never(_: str) -> str:
        raise AssertionError("no debía preguntar")

    assert deploy.confirm(options(plan_only=False, assume_yes=True), "1", True, ask=never, echo=out.append)
    assert "secreto de PubMed" in out[0]


def test_declinar_devuelve_1_y_no_lanza_comandos(monkeypatch: pytest.MonkeyPatch) -> None:
    class Spy(deploy.Runner):
        def run(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
            self.history.append(cmd)
            if cmd[:3] == ["aws", "sts", "get-caller-identity"] or "get-caller-identity" in cmd:
                return json.dumps({"Account": "123456789012"})
            raise AssertionError(f"no debía ejecutar: {cmd}")

    monkeypatch.setattr(deploy.shutil, "which", lambda _: "aws")
    runner = Spy(dry_run=False, echo=lambda _: None)
    assert deploy.run_deploy(options(plan_only=False), runner, ask=lambda _: "no") == 1
    assert len(runner.history) == 1  # solo la identidad de la cuenta


# ---- secretos ----------------------------------------------------------------------------------------------------------------

def test_los_secretos_no_se_imprimen() -> None:
    out: list[str] = []
    runner = deploy.Runner(dry_run=True, echo=out.append)
    deploy.run_deploy(options(pubmed_api_key="clave-ncbi", smoke_username="u", smoke_password="pw-secreta"), runner)
    text = "\n".join(out)
    assert "clave-ncbi" not in text and "pw-secreta" not in text
    assert "PubMedApiKey=****" in text


def test_mask_secrets() -> None:
    assert deploy.mask_secrets(["x", "--password", "abc", "--username", "u"]) == ["x", "--password", "****", "--username", "u"]
    assert deploy.mask_secrets(["PubMedApiKey=abc", "PubMedApiKey="]) == ["PubMedApiKey=****", "PubMedApiKey="]


# ---- fallos: nombrar stack y causa -------------------------------------------------------------------------------------------

def test_un_stack_que_falla_se_nombra_con_su_causa() -> None:
    class Failing(deploy.Runner):
        def run(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
            if cmd[:3] == ["aws", "cloudformation", "deploy"]:
                raise deploy.DeployError("exit 255")
            if "describe-stack-events" in cmd:
                return json.dumps([["Guardrail", "Topic definition too long"], ["Index", "Already exists"]])
            return ""

    with pytest.raises(deploy.DeployError) as info:
        deploy.deploy_stack(Failing(dry_run=False, echo=lambda _: None), "bedrock", options(plan_only=False))
    message = str(info.value)
    assert "ultra-skincare-bedrock" in message and "Guardrail: Topic definition too long" in message


def test_los_parametros_vacios_opcionales_se_envian_pero_no_los_demas() -> None:
    params = deploy.stack_parameters("compute", options(), {"CodeBucket": "b"})
    assert params["CodeBucket"] == "b" and params["HandoffEmail"] == "" and params["PubMedApiKey"] == ""
    assert "BudgetAlertEmail" in deploy.stack_parameters("storage", options())


# ---- Runtime_Agente ---------------------------------------------------------------------------------------------------------

OUTPUTS = {
    "storage": {"ProductsTableName": "ultra-productos", "RecommendationsTableName": "ultra-recomendaciones",
                "SessionsTableName": "ultra-sesiones", "EvidenceTableName": "ultra-evidencias-ingredientes"},
    "auth": {"DiscoveryUrl": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_X/.well-known/openid-configuration",
             "KioscoClientId": "kiosco123", "CajaClientId": "caja456", "UserPoolId": "us-east-1_X"},
    "bedrock": {"GuardrailId": "gr1", "GuardrailVersion": "1", "KnowledgeBaseId": "KB1"},
    "compute": {"HandoffTopicArn": "arn:aws:sns:us-east-1:1:t", "ApiUrl": "https://api.example", "PubMedSecretId": "arn:secret"},
}


def test_el_runtime_solo_acepta_tokens_del_kiosco_client() -> None:
    config = deploy.authorizer_configuration(OUTPUTS)["customJWTAuthorizer"]
    assert config["allowedClients"] == ["kiosco123"] and config["discoveryUrl"].endswith("/.well-known/openid-configuration")


def test_las_variables_del_runtime_son_las_que_lee_el_agente() -> None:
    env = deploy.runtime_environment(options(), OUTPUTS, "https://tienda.example")
    assert env["PRODUCTION"] == "true" and env["GUARDRAIL_VERSION"] == "1" and env["PUBMED_ENABLED"] == "true"
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "agent").rglob("*.py"))
    for key in env:
        assert f'"{key}"' in sources, f"ningún módulo del agente lee {key}"


def test_sin_secreto_de_pubmed_no_se_activa() -> None:
    outputs = {**OUTPUTS, "compute": {k: v for k, v in OUTPUTS["compute"].items() if k != "PubMedSecretId"}}
    assert "PUBMED_ENABLED" not in deploy.runtime_environment(options(), outputs, "https://x")


def test_url_del_websocket_y_variables_del_frontend() -> None:
    arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/ultra_skincare_agent-AbC"
    url = deploy.websocket_url(arn)
    assert url.startswith("wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore")
    assert url.endswith("/ws?qualifier=DEFAULT")
    env = deploy.frontend_env(OUTPUTS, "https://d111.cloudfront.net", url)
    assert env["VITE_STORE_DOMAIN"] == "d111.cloudfront.net" and env["VITE_COGNITO_KIOSCO_CLIENT_ID"] == "kiosco123"
    assert env["VITE_AGENT_WS_URL"] == url and env["VITE_CAJA_API_URL"] == "https://api.example"


def test_el_nombre_del_runtime_es_valido_para_agentcore() -> None:
    import re

    assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", deploy.AGENT_RUNTIME_NAME)


def test_argumentos_dominio_y_certificado_van_juntos() -> None:
    with pytest.raises(SystemExit):
        deploy.parse_args(["--budget-email", "a@b.co", "--domain-name", "tienda.example.com"])
    parsed = deploy.parse_args(["--budget-email", "a@b.co", "--plan"])
    assert parsed.plan_only and parsed.store_domain == "tienda.example.com"


# ---- empaquetado -----------------------------------------------------------------------------------------------------------

def test_el_zip_de_caja_lleva_solo_sus_dos_modulos(tmp_path: Path) -> None:
    target = package.build_caja(tmp_path)
    assert sorted(zipfile.ZipFile(target).namelist()) == ["caja_core.py", "caja_handler.py"]


def test_el_zip_de_la_etl_lleva_el_codigo_y_la_configuracion_sin_la_guia(tmp_path: Path) -> None:
    target = package.build_etl(tmp_path, install_dependencies=False)
    names = set(zipfile.ZipFile(target).namelist())
    assert {"handler.py", "pipeline.py", "sinks.py", "core/product.py", "config/funcion_map.json", "config/column_map.json"} <= names
    assert "config/guide.json" not in names
    assert not any(n.endswith(".pyc") or "__pycache__" in n for n in names)


def test_el_empaquetado_es_reproducible(tmp_path: Path) -> None:
    first = package.build_caja(tmp_path / "a").read_bytes()
    second = package.build_caja(tmp_path / "b").read_bytes()
    assert first == second

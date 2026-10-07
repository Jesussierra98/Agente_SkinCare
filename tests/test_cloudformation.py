"""Plantillas de CloudFormation (Req. 21 y 22): `cfn-lint` y aserciones sobre nombres, cifrado, IAM y HTTPS.

No llama a AWS. Las aserciones protegen lo que cfn-lint no sabe: que los nombres coincidan con el código del agente,
que los IAM no usen comodines y que cada `ImportValue` tenga quien lo exporte.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from advisor.guardrail import TOPIC_CHEMICAL, TOPIC_MEDICAL

ROOT = Path(__file__).resolve().parents[1]
CFN = ROOT / "cloudformation"

STACKS = {
    "ultra-skincare-storage": "01-base-storage-db.yaml",
    "ultra-skincare-auth": "02-auth-cognito.yaml",
    "ultra-skincare-bedrock": "03-bedrock-kb-guardrail.yaml",
    "ultra-skincare-compute": "04-api-and-lambdas.yaml",
    "ultra-skincare-frontend": "05-frontend-hosting.yaml",
}


class _Loader(yaml.SafeLoader):
    """Lee YAML de CloudFormation: `!Ref x` → {"Ref": "x"}, `!Sub x` → {"Fn::Sub": "x"}, etc."""


def _tag(loader: _Loader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {"Ref": value} if suffix == "Ref" else {"Condition": value} if suffix == "Condition" else {f"Fn::{suffix}": value}


_Loader.add_multi_constructor("!", _tag)


def load(file: str) -> dict[str, Any]:
    return yaml.load((CFN / file).read_text(encoding="utf-8"), Loader=_Loader)


@pytest.fixture(scope="module")
def templates() -> dict[str, dict[str, Any]]:
    return {stack: load(file) for stack, file in STACKS.items()}


def resources_of(template: dict[str, Any], type_: str) -> dict[str, dict[str, Any]]:
    return {name: res for name, res in template["Resources"].items() if res["Type"] == type_}


def walk(node: Any):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


# ---- cfn-lint ---------------------------------------------------------------------------------------------------------

def test_cfn_lint_no_encuentra_problemas() -> None:
    exe = Path(sys.executable).parent / ("cfn-lint.exe" if sys.platform == "win32" else "cfn-lint")
    if not exe.exists():
        pytest.skip("cfn-lint no está instalado")
    result = subprocess.run([str(exe), *[str(CFN / f) for f in STACKS.values()]], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_estan_los_cinco_stacks_en_el_orden_del_requerimiento(templates) -> None:
    assert list(STACKS.values()) == sorted(STACKS.values())  # el prefijo numérico fija el orden de despliegue
    assert list(STACKS) == [
        "ultra-skincare-storage", "ultra-skincare-auth", "ultra-skincare-bedrock", "ultra-skincare-compute", "ultra-skincare-frontend",
    ]


def test_ninguna_plantilla_usa_anclas_de_yaml() -> None:
    # CloudFormation rechaza los alias de YAML (&ancla / *alias).
    for file in STACKS.values():
        text = (CFN / file).read_text(encoding="utf-8")
        assert not re.search(r"(^|\s)[&*][A-Za-z_]\w*\s*$", text, re.MULTILINE), file


# ---- 01: almacenamiento ---------------------------------------------------------------------------------------------

def test_storage_tiene_exactamente_4_buckets_todos_privados_y_cifrados(templates) -> None:
    buckets = resources_of(templates["ultra-skincare-storage"], "AWS::S3::Bucket")
    assert len(buckets) == 4
    for name, bucket in buckets.items():
        props = bucket["Properties"]
        assert set(props["PublicAccessBlockConfiguration"].values()) == {True}, name
        assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"], name
        assert bucket["DeletionPolicy"] == "Retain", name  # borrar el stack no borra los datos


def test_los_buckets_de_datos_usan_la_llave_kms_y_exigen_https(templates) -> None:
    storage = templates["ultra-skincare-storage"]
    for name in ("RawBucket", "KbSourceBucket"):
        rule = storage["Resources"][name]["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
    policies = resources_of(storage, "AWS::S3::BucketPolicy")
    for policy in policies.values():
        statement = policy["Properties"]["PolicyDocument"]["Statement"][0]
        assert statement["Effect"] == "Deny" and statement["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_storage_tiene_las_4_tablas_con_sus_claves_ttl_e_indice(templates) -> None:
    tables = {t["Properties"]["TableName"]: t["Properties"] for t in resources_of(templates["ultra-skincare-storage"], "AWS::DynamoDB::Table").values()}
    assert set(tables) == {"ultra-productos", "ultra-recomendaciones", "ultra-sesiones", "ultra-evidencias-ingredientes"}
    for name, props in tables.items():
        assert props["BillingMode"] == "PAY_PER_REQUEST", name
        assert props["SSESpecification"]["SSEType"] == "KMS", name
    keys = {name: props["KeySchema"][0]["AttributeName"] for name, props in tables.items()}
    assert keys == {"ultra-productos": "sku", "ultra-recomendaciones": "rec_id", "ultra-sesiones": "session_id", "ultra-evidencias-ingredientes": "ingrediente"}
    [gsi] = tables["ultra-recomendaciones"]["GlobalSecondaryIndexes"]
    assert gsi["IndexName"] == "codigo_corto-index" and gsi["Projection"]["ProjectionType"] == "ALL"
    assert gsi["KeySchema"] == [{"AttributeName": "codigo_corto", "KeyType": "HASH"}]
    for name in ("ultra-sesiones", "ultra-evidencias-ingredientes"):
        assert tables[name]["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}


def test_storage_tiene_llave_con_rotacion_y_un_budget_con_alerta(templates) -> None:
    storage = templates["ultra-skincare-storage"]
    [key] = resources_of(storage, "AWS::KMS::Key").values()
    assert key["Properties"]["EnableKeyRotation"] is True
    [budget] = resources_of(storage, "AWS::Budgets::Budget").values()
    assert len(budget["Properties"]["NotificationsWithSubscribers"]) >= 1


# ---- 02: Cognito ---------------------------------------------------------------------------------------------------------

def test_cognito_tiene_dos_grupos_y_dos_clientes_publicos_con_sus_flujos(templates) -> None:
    auth = templates["ultra-skincare-auth"]
    [pool] = resources_of(auth, "AWS::Cognito::UserPool").values()
    assert pool["Properties"]["UserPoolName"] == "ultra-skincare-userpool"
    assert pool["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True  # sin autorregistro
    groups = {g["Properties"]["GroupName"] for g in resources_of(auth, "AWS::Cognito::UserPoolGroup").values()}
    assert groups == {"kiosco", "caja"}
    clients = {c["Properties"]["ClientName"]: c["Properties"] for c in resources_of(auth, "AWS::Cognito::UserPoolClient").values()}
    assert set(clients) == {"KioscoClient", "CajaClient"}
    assert all(c["GenerateSecret"] is False for c in clients.values())
    assert set(clients["KioscoClient"]["ExplicitAuthFlows"]) == {"ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"}
    assert set(clients["CajaClient"]["ExplicitAuthFlows"]) == {"ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"}
    assert clients["KioscoClient"]["AccessTokenValidity"] == 60 and clients["KioscoClient"]["RefreshTokenValidity"] == 365


# ---- 03: Guardrail y Knowledge Base -------------------------------------------------------------------------------------

def test_guardrail_deniega_los_temas_que_el_agente_espera(templates) -> None:
    [guardrail] = resources_of(templates["ultra-skincare-bedrock"], "AWS::Bedrock::Guardrail").values()
    assert guardrail["Properties"]["Name"] == "ultra-skincare-guardrail"
    topics = guardrail["Properties"]["TopicPolicyConfig"]["TopicsConfig"]
    assert {t["Name"] for t in topics} == {TOPIC_MEDICAL, TOPIC_CHEMICAL}  # los mismos nombres que usa advisor/guardrail.py
    for topic in topics:
        assert topic["Type"] == "DENY" and len(" ".join(topic["Definition"].split())) <= 200 and topic["Examples"]


def test_la_base_de_conocimiento_usa_titan_v2_s3_vectors_y_un_fragmento_por_sku(templates) -> None:
    bedrock = templates["ultra-skincare-bedrock"]
    [kb] = resources_of(bedrock, "AWS::Bedrock::KnowledgeBase").values()
    text = str(kb["Properties"]["KnowledgeBaseConfiguration"])
    assert "amazon.titan-embed-text-v2:0" in text
    assert kb["Properties"]["StorageConfiguration"]["Type"] == "S3_VECTORS"
    [source] = resources_of(bedrock, "AWS::Bedrock::DataSource").values()
    assert source["Properties"]["DataSourceConfiguration"]["S3Configuration"]["InclusionPrefixes"] == ["productos/"]
    assert source["Properties"]["VectorIngestionConfiguration"]["ChunkingConfiguration"]["ChunkingStrategy"] == "NONE"
    [index] = resources_of(bedrock, "AWS::S3Vectors::Index").values()
    assert index["Properties"]["Dimension"] == 1024 and index["Properties"]["DistanceMetric"] == "cosine"


# ---- 04: cómputo e IAM ------------------------------------------------------------------------------------------------------

def statements(template: dict[str, Any]):
    for role_name, role in resources_of(template, "AWS::IAM::Role").items():
        for policy in role["Properties"].get("Policies", []):
            for statement in as_list(policy["PolicyDocument"]["Statement"]):
                yield role_name, statement


def test_los_tres_roles_tienen_el_nombre_del_diseno(templates) -> None:
    names = {r["Properties"]["RoleName"] for r in resources_of(templates["ultra-skincare-compute"], "AWS::IAM::Role").values()}
    assert names == {"ultra-skincare-etl-role", "ultra-skincare-caja-role", "ultra-skincare-agent-runtime-role"}


def test_ningun_permiso_de_iam_usa_comodines_de_accion(templates) -> None:
    for template in templates.values():
        for role_name, statement in statements(template):
            for action in as_list(statement["Action"]):
                assert action != "*" and not action.endswith(":*"), f"{role_name}: {action}"


def test_el_recurso_asterisco_solo_se_permite_donde_el_servicio_no_admite_otra_cosa(templates) -> None:
    for template in templates.values():
        for role_name, statement in statements(template):
            if "*" in as_list(statement["Resource"]):
                assert as_list(statement["Action"]) == ["ecr:GetAuthorizationToken"], f"{role_name}: {statement}"


def test_el_rol_de_caja_solo_toca_lo_que_necesita(templates) -> None:
    compute = templates["ultra-skincare-compute"]
    caja = compute["Resources"]["CajaRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    actions = {a for s in caja for a in as_list(s["Action"]) if a.startswith("dynamodb:")}
    assert actions == {"dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"}


def test_las_variables_de_entorno_de_las_lambdas_coinciden_con_el_codigo(templates) -> None:
    functions = {f["Properties"]["FunctionName"]: f["Properties"] for f in resources_of(templates["ultra-skincare-compute"], "AWS::Lambda::Function").values()}
    etl, caja = functions["ultra-skincare-etl"], functions["ultra-caja-lambda"]
    assert set(etl["Environment"]["Variables"]) == {"PRODUCTS_TABLE", "KB_BUCKET", "KB_ID", "KB_DATA_SOURCE_ID"}
    assert set(caja["Environment"]["Variables"]) == {"RECOMMENDATIONS_TABLE", "SESSIONS_TABLE"}
    assert (etl["Timeout"], etl["MemorySize"], caja["Timeout"]) == (300, 1024, 10)
    assert etl["Runtime"] == caja["Runtime"] == "python3.12"


def test_los_handlers_apuntan_a_funciones_que_existen() -> None:
    etl_module, etl_function = "handler", "handler"
    assert re.search(rf"^def {etl_function}\(", (ROOT / "src" / "etl" / f"{etl_module}.py").read_text(encoding="utf-8"), re.MULTILINE)
    assert re.search(r"^def lambda_handler\(", (ROOT / "src" / "caja_api" / "caja_handler.py").read_text(encoding="utf-8"), re.MULTILINE)


def test_el_etl_no_se_reintenta_y_se_dispara_solo_con_csv_en_raw(templates) -> None:
    compute = templates["ultra-skincare-compute"]
    [invoke] = resources_of(compute, "AWS::Lambda::EventInvokeConfig").values()
    assert invoke["Properties"]["MaximumRetryAttempts"] == 0
    [rule] = resources_of(compute, "AWS::Events::Rule").values()
    assert rule["Properties"]["EventPattern"]["detail"]["object"]["key"] == [{"wildcard": "raw/*.csv"}]
    raw = templates["ultra-skincare-storage"]["Resources"]["RawBucket"]["Properties"]["NotificationConfiguration"]
    assert raw["EventBridgeConfiguration"]["EventBridgeEnabled"] is True  # sin esto EventBridge no recibe los eventos del bucket


def test_la_api_tiene_las_tres_rutas_todas_con_jwt_y_cors_sin_comodines(templates) -> None:
    compute = templates["ultra-skincare-compute"]
    routes = {r["Properties"]["RouteKey"]: r["Properties"] for r in resources_of(compute, "AWS::ApiGatewayV2::Route").values()}
    assert set(routes) == {"GET /recomendacion/{id}", "POST /recomendacion/{id}/atendida", "POST /derivaciones/{session_id}/confirmar"}
    assert all(r["AuthorizationType"] == "JWT" for r in routes.values())
    [api] = resources_of(compute, "AWS::ApiGatewayV2::Api").values()
    assert "*" not in str(api["Properties"]["CorsConfiguration"]["AllowOrigins"])
    assert compute["Parameters"]["FrontendOrigin"]["AllowedPattern"]  # el origen se valida (sin comodín ni barra)
    [authorizer] = resources_of(compute, "AWS::ApiGatewayV2::Authorizer").values()
    assert len(authorizer["Properties"]["JwtConfiguration"]["Audience"]) == 2  # KioscoClient y CajaClient


def test_sns_y_ecr(templates) -> None:
    compute = templates["ultra-skincare-compute"]
    [topic] = resources_of(compute, "AWS::SNS::Topic").values()
    assert topic["Properties"]["TopicName"] == "ultra-skincare-handoff" and "KmsMasterKeyId" in topic["Properties"]
    [repo] = resources_of(compute, "AWS::ECR::Repository").values()
    assert repo["Properties"]["RepositoryName"] == "ultra-skincare-agent"
    assert repo["Properties"]["ImageScanningConfiguration"]["ScanOnPush"] is True


# ---- 05: frontend ---------------------------------------------------------------------------------------------------------

def test_cloudfront_usa_oac_https_y_tls_minimo_cuando_hay_certificado_propio(templates) -> None:
    frontend = templates["ultra-skincare-frontend"]
    [oac] = resources_of(frontend, "AWS::CloudFront::OriginAccessControl").values()
    assert oac["Properties"]["OriginAccessControlConfig"]["SigningBehavior"] == "always"
    [distribution] = resources_of(frontend, "AWS::CloudFront::Distribution").values()
    config = distribution["Properties"]["DistributionConfig"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    custom = config["ViewerCertificate"]["Fn::If"][1]
    assert custom["MinimumProtocolVersion"] == "TLSv1.2_2021"


def test_las_cabeceras_de_seguridad_piden_microfono_camara_y_wss_de_agentcore(templates) -> None:
    [policy] = resources_of(templates["ultra-skincare-frontend"], "AWS::CloudFront::ResponseHeadersPolicy").values()
    config = policy["Properties"]["ResponseHeadersPolicyConfig"]
    headers = {h["Header"]: h["Value"] for h in config["CustomHeadersConfig"]["Items"]}
    assert headers["Permissions-Policy"] == "microphone=(self), camera=(self)"
    csp = str(config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"])
    assert "wss://bedrock-agentcore." in csp and "frame-ancestors 'none'" in csp and "cognito-idp." in csp
    assert config["SecurityHeadersConfig"]["StrictTransportSecurity"]["AccessControlMaxAgeSec"] >= 31536000


def test_la_politica_del_bucket_de_hosting_solo_abre_la_lectura_a_cloudfront(templates) -> None:
    [policy] = resources_of(templates["ultra-skincare-frontend"], "AWS::S3::BucketPolicy").values()
    allow = [s for s in policy["Properties"]["PolicyDocument"]["Statement"] if s["Effect"] == "Allow"]
    assert len(allow) == 1 and allow[0]["Principal"] == {"Service": "cloudfront.amazonaws.com"}
    assert "AWS:SourceArn" in allow[0]["Condition"]["StringEquals"]
    # El stack storage no debe crear otra política para el bucket de hosting (solo puede haber una).
    storage_policies = resources_of(templates["ultra-skincare-storage"], "AWS::S3::BucketPolicy")
    assert all(p["Properties"]["Bucket"] != {"Ref": "HostingBucket"} for p in storage_policies.values())


# ---- entre stacks ---------------------------------------------------------------------------------------------------------------

def test_cada_importvalue_tiene_un_stack_que_lo_exporta(templates) -> None:
    exported = {
        f"{stack}:{key}" for stack, template in templates.items() for key in template.get("Outputs", {}) if "Export" in template["Outputs"][key]
    }
    for stack, template in templates.items():
        for node in walk(template):
            if isinstance(node, dict) and "Fn::ImportValue" in node:
                name = node["Fn::ImportValue"]
                assert name in exported, f"{stack} importa {name!r}, que nadie exporta"
                assert name.split(":")[0] != stack  # un stack no se importa a sí mismo


def test_los_stacks_solo_importan_de_los_anteriores(templates) -> None:
    order = list(STACKS)
    for stack, template in templates.items():
        for node in walk(template):
            if isinstance(node, dict) and "Fn::ImportValue" in node:
                source = node["Fn::ImportValue"].split(":")[0]
                assert order.index(source) < order.index(stack), f"{stack} depende de {source}, que se despliega después"


# ---- empaquetado de las Lambdas ----------------------------------------------------------------------------------------------

def test_el_paquete_del_etl_lleva_lo_necesario_y_nada_de_datos(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import package_lambdas as pkg
    finally:
        sys.path.pop(0)
    etl = pkg.collect_etl_files()
    assert {"handler.py", "pipeline.py", "sinks.py", "core/product.py", "core/encoding.py", "config/funcion_map.json"} <= set(etl)
    assert not any(name.endswith((".csv", ".xlsx", ".pyc")) for name in etl) and "requirements.txt" not in etl
    assert set(pkg.collect_caja_files()) == {"caja_core.py", "caja_handler.py"}

    digest = pkg.write_zip(pkg.collect_caja_files(), tmp_path / "caja.zip")
    again = pkg.write_zip(pkg.collect_caja_files(), tmp_path / "caja2.zip")
    assert digest == again  # el mismo código da el mismo hash (la clave de S3 solo cambia si cambia el código)
    with zipfile.ZipFile(tmp_path / "caja.zip") as archive:
        assert sorted(archive.namelist()) == ["caja_core.py", "caja_handler.py"]
        assert all("\\" not in name for name in archive.namelist())


def test_el_etl_acepta_el_evento_de_eventbridge_y_el_de_s3() -> None:
    from handler import objects_in

    assert objects_in({"detail": {"bucket": {"name": "b"}, "object": {"key": "raw/catalogo 1.csv"}}}) == [("b", "raw/catalogo 1.csv")]
    s3_event = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "raw/catalogo+1.csv"}}}]}
    assert objects_in(s3_event) == [("b", "raw/catalogo 1.csv")]  # la notificación clásica codifica la clave como URL
    assert objects_in({}) == []

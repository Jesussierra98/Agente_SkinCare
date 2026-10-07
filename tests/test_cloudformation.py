"""Plantillas de CloudFormation: aserciones de diseño y seguridad (Req. 21, 22; tarea 13.7).

cfn-lint se corre aparte (`cfn-lint cloudformation/*.yaml`); aquí se comprueba lo que un linter no sabe: nombres,
cantidades, reglas de seguridad y que cada ImportValue exista como salida de un stack anterior.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFN = ROOT / "cloudformation"

FILES = {
    "storage": "01-base-storage-db.yaml",
    "auth": "02-auth-cognito.yaml",
    "bedrock": "03-bedrock-kb-guardrail.yaml",
    "compute": "04-api-and-lambdas.yaml",
    "frontend": "05-frontend-hosting.yaml",
}
STACK_NAMES = {
    "storage": "ultra-skincare-storage",
    "auth": "ultra-skincare-auth",
    "bedrock": "ultra-skincare-bedrock",
    "compute": "ultra-skincare-compute",
    "frontend": "ultra-skincare-frontend",
}
# Parámetro que apunta al stack del que se importa -> clave en FILES.
STACK_PARAMS = {"StorageStackName": "storage", "AuthStackName": "auth", "BedrockStackName": "bedrock", "ComputeStackName": "compute"}
ORDER = ["storage", "auth", "bedrock", "compute", "frontend"]


class CfnLoader(yaml.SafeLoader):
    """YAML con las etiquetas cortas de CloudFormation convertidas a su forma larga."""


def _tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if suffix == "Ref":
        return {"Ref": value}
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {f"Fn::{suffix}": value}


CfnLoader.add_multi_constructor("!", _tag)


def load(key: str) -> dict[str, Any]:
    return yaml.load((CFN / FILES[key]).read_text(encoding="utf-8"), Loader=CfnLoader)


T = {key: load(key) for key in FILES}


def resources(key: str, rtype: str) -> dict[str, dict[str, Any]]:
    return {n: r for n, r in T[key]["Resources"].items() if r["Type"] == rtype}


def walk(node: Any):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def policy_statements(key: str):
    for name, role in resources(key, "AWS::IAM::Role").items():
        for policy in role["Properties"].get("Policies", []):
            for stmt in policy["PolicyDocument"]["Statement"]:
                if isinstance(stmt, dict) and "Fn::If" in stmt:
                    stmt = stmt["Fn::If"][1]
                yield name, stmt


# ---- estructura general ---------------------------------------------------------------------------------------------

def test_hay_cinco_plantillas_con_descripcion() -> None:
    assert sorted(p.name for p in CFN.glob("*.yaml")) == sorted(FILES.values())
    for key, template in T.items():
        assert template["AWSTemplateFormatVersion"] == "2010-09-09"
        assert template["Description"].strip(), key


def test_cada_importvalue_es_una_salida_exportada_de_un_stack_anterior() -> None:
    exports: dict[str, set[str]] = {
        k: {re.sub(r"^\$\{AWS::StackName\}-", "", out["Export"]["Name"]["Fn::Sub"]) for out in t.get("Outputs", {}).values() if "Export" in out}
        for k, t in T.items()
    }
    for key, template in T.items():
        for node in walk(template):
            if isinstance(node, dict) and "Fn::ImportValue" in node:
                value = node["Fn::ImportValue"]["Fn::Sub"]
                match = re.fullmatch(r"\$\{(\w+)\}-(\w+)", value)
                assert match, f"{key}: importación inesperada {value}"
                param, export = match.groups()
                source = STACK_PARAMS[param]
                assert ORDER.index(source) < ORDER.index(key), f"{key} importa de {source}, que se despliega después"
                assert export in exports[source], f"{key} importa {export}, que {source} no exporta"
                default = T[key]["Parameters"][param]["Default"]
                assert default == STACK_NAMES[source]


def test_los_nombres_de_stack_por_defecto_son_los_del_diseno() -> None:
    for param, source in STACK_PARAMS.items():
        for template in T.values():
            if param in template.get("Parameters", {}):
                assert template["Parameters"][param]["Default"] == STACK_NAMES[source]


def test_sin_acciones_comodin_en_iam() -> None:
    for key in T:
        for name, stmt in policy_statements(key):
            for action in as_list(stmt["Action"]):
                assert action != "*" and not str(action).endswith(":*"), f"{key}/{name}: {action}"


def test_recurso_comodin_solo_donde_el_servicio_no_admite_permisos_por_recurso() -> None:
    allowed = {"ecr:GetAuthorizationToken", "xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"}
    for key in T:
        for name, stmt in policy_statements(key):
            if "*" in as_list(stmt.get("Resource")):
                assert set(as_list(stmt["Action"])) <= allowed, f"{key}/{name}: {stmt['Action']}"


def test_ningun_principal_abierto_en_las_politicas_de_confianza() -> None:
    for key in T:
        for name, role in resources(key, "AWS::IAM::Role").items():
            for stmt in role["Properties"]["AssumeRolePolicyDocument"]["Statement"]:
                assert stmt["Principal"] != "*" and stmt["Principal"] != {"AWS": "*"}, f"{key}/{name}"
                assert "Service" in stmt["Principal"]


def test_las_politicas_de_bucket_niegan_el_trafico_sin_https() -> None:
    for key in ("storage", "frontend"):
        policies = resources(key, "AWS::S3::BucketPolicy")
        assert policies
        for name, policy in policies.items():
            deny = [s for s in policy["Properties"]["PolicyDocument"]["Statement"] if s["Effect"] == "Deny"]
            assert any(s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}} for s in deny), f"{key}/{name}"


# ---- 01 almacenamiento -----------------------------------------------------------------------------------------------

def test_storage_tiene_cuatro_buckets_sin_acceso_publico() -> None:
    buckets = resources("storage", "AWS::S3::Bucket")
    assert len(buckets) == 4
    for name, bucket in buckets.items():
        block = bucket["Properties"]["PublicAccessBlockConfiguration"]
        assert block == {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}, name
        assert bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"], name
        assert bucket["DeletionPolicy"] == "Retain", name


def test_los_datos_del_catalogo_se_cifran_con_la_llave_kms() -> None:
    for name in ("RawDataBucket", "KbSourceBucket"):
        rule = T["storage"]["Resources"][name]["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
    assert T["storage"]["Resources"]["DataKey"]["Properties"]["EnableKeyRotation"] is True
    for name, table in resources("storage", "AWS::DynamoDB::Table").items():
        assert table["Properties"]["SSESpecification"]["SSEType"] == "KMS", name


def test_storage_tiene_las_cuatro_tablas_on_demand() -> None:
    tables = {t["Properties"]["TableName"]: t["Properties"] for t in resources("storage", "AWS::DynamoDB::Table").values()}
    assert set(tables) == {"ultra-productos", "ultra-recomendaciones", "ultra-sesiones", "ultra-evidencias-ingredientes"}
    assert all(t["BillingMode"] == "PAY_PER_REQUEST" for t in tables.values())
    assert tables["ultra-productos"]["KeySchema"][0]["AttributeName"] == "sku"
    assert tables["ultra-recomendaciones"]["KeySchema"][0]["AttributeName"] == "rec_id"
    gsi = tables["ultra-recomendaciones"]["GlobalSecondaryIndexes"][0]
    assert gsi["IndexName"] == "codigo_corto-index" and gsi["KeySchema"][0]["AttributeName"] == "codigo_corto"
    assert gsi["Projection"]["ProjectionType"] == "ALL"
    for name in ("ultra-sesiones", "ultra-evidencias-ingredientes"):
        assert tables[name]["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}


def test_storage_tiene_un_budget_con_alerta_por_correo() -> None:
    [budget] = resources("storage", "AWS::Budgets::Budget").values()
    subscribers = budget["Properties"]["NotificationsWithSubscribers"][0]["Subscribers"]
    assert subscribers[0]["SubscriptionType"] == "EMAIL"


def test_el_bucket_raw_avisa_a_eventbridge_para_la_etl() -> None:
    notification = T["storage"]["Resources"]["RawDataBucket"]["Properties"]["NotificationConfiguration"]
    assert notification["EventBridgeConfiguration"]["EventBridgeEnabled"] is True


# ---- 02 autenticación ------------------------------------------------------------------------------------------------------

def test_cognito_sin_autorregistro_dos_grupos_y_dos_clientes_publicos() -> None:
    [pool] = resources("auth", "AWS::Cognito::UserPool").values()
    assert pool["Properties"]["UserPoolName"] == "ultra-skincare-userpool"
    assert pool["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    groups = {g["Properties"]["GroupName"] for g in resources("auth", "AWS::Cognito::UserPoolGroup").values()}
    assert groups == {"kiosco", "caja"}
    clients = {c["Properties"]["ClientName"]: c["Properties"] for c in resources("auth", "AWS::Cognito::UserPoolClient").values()}
    assert set(clients) == {"KioscoClient", "CajaClient"}
    assert all(c["GenerateSecret"] is False for c in clients.values())
    assert set(clients["KioscoClient"]["ExplicitAuthFlows"]) == {"ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"}
    assert set(clients["CajaClient"]["ExplicitAuthFlows"]) == {"ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"}
    assert clients["KioscoClient"]["AccessTokenValidity"] == 60
    assert clients["KioscoClient"]["TokenValidityUnits"]["AccessToken"] == "minutes"


# ---- 03 Bedrock ----------------------------------------------------------------------------------------------------------------

def test_los_temas_del_guardrail_coinciden_con_guardrail_topics_json() -> None:
    source = json.loads((ROOT / "src" / "agent" / "guardrail_topics.json").read_text(encoding="utf-8"))
    [guardrail] = resources("bedrock", "AWS::Bedrock::Guardrail").values()
    props = guardrail["Properties"]
    assert props["Name"] == "ultra-skincare-guardrail"
    assert props["BlockedInputMessaging"] == source["blockedInputMessaging"]
    assert props["BlockedOutputsMessaging"] == source["blockedOutputsMessaging"]
    topics = props["TopicPolicyConfig"]["TopicsConfig"]
    expected = [{"Name": t["name"], "Type": t["type"], "Definition": t["definition"], "Examples": t["examples"]} for t in source["topics"]]
    assert topics == expected


def test_la_kb_usa_titan_v2_s3_vectors_y_un_documento_por_sku() -> None:
    [kb] = resources("bedrock", "AWS::Bedrock::KnowledgeBase").values()
    assert kb["Properties"]["StorageConfiguration"]["Type"] == "S3_VECTORS"
    assert "amazon.titan-embed-text-v2:0" in json.dumps(kb["Properties"]["KnowledgeBaseConfiguration"])
    [source] = resources("bedrock", "AWS::Bedrock::DataSource").values()
    assert source["Properties"]["DataSourceConfiguration"]["S3Configuration"]["InclusionPrefixes"] == ["productos/"]
    assert source["Properties"]["VectorIngestionConfiguration"]["ChunkingConfiguration"]["ChunkingStrategy"] == "NONE"
    [index] = resources("bedrock", "AWS::S3Vectors::Index").values()
    assert index["Properties"]["DistanceMetric"] == "cosine" and index["Properties"]["DataType"] == "float32"


def test_el_guardrail_tiene_version_numerada() -> None:
    assert resources("bedrock", "AWS::Bedrock::GuardrailVersion")
    assert "GuardrailVersion" in T["bedrock"]["Outputs"]


# ---- 04 cómputo ----------------------------------------------------------------------------------------------------------------

def test_roles_con_nombre_de_minimo_privilegio() -> None:
    names = {r["Properties"]["RoleName"] for r in resources("compute", "AWS::IAM::Role").values()}
    assert names == {"ultra-skincare-etl-role", "ultra-skincare-caja-role", "ultra-skincare-agent-runtime-role"}
    runtime = T["compute"]["Resources"]["AgentRuntimeRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert runtime["Principal"]["Service"] == "bedrock-agentcore.amazonaws.com"
    assert "aws:SourceAccount" in runtime["Condition"]["StringEquals"]


def test_la_etl_tiene_1024_mb_300_s_y_cero_reintentos() -> None:
    props = T["compute"]["Resources"]["EtlFunction"]["Properties"]
    assert props["Runtime"] == "python3.12" and props["Handler"] == "handler.handler"
    assert T["compute"]["Parameters"]["EtlMemoryMb"]["Default"] == 1024
    assert T["compute"]["Parameters"]["EtlTimeoutSeconds"]["Default"] == 300
    assert T["compute"]["Resources"]["EtlEventInvokeConfig"]["Properties"]["MaximumRetryAttempts"] == 0


def test_la_etl_solo_se_dispara_con_csv_en_raw() -> None:
    pattern = T["compute"]["Resources"]["EtlRule"]["Properties"]["EventPattern"]
    assert pattern["source"] == ["aws.s3"] and pattern["detail-type"] == ["Object Created"]
    assert {"wildcard": "raw/*.csv"} in pattern["detail"]["object"]["key"]


def test_la_lambda_de_caja_tiene_timeout_de_10_s_y_tres_rutas_con_jwt() -> None:
    caja = T["compute"]["Resources"]["CajaFunction"]["Properties"]
    assert caja["Timeout"] == 10 and caja["Handler"] == "caja_handler.lambda_handler"
    routes = {r["Properties"]["RouteKey"]: r["Properties"] for r in resources("compute", "AWS::ApiGatewayV2::Route").values()}
    assert set(routes) == {"GET /recomendacion/{id}", "POST /recomendacion/{id}/atendida", "POST /derivaciones/{session_id}/confirmar"}
    assert all(r["AuthorizationType"] == "JWT" for r in routes.values())


def test_el_authorizer_acepta_los_dos_clientes_y_cors_no_es_comodin() -> None:
    [auth] = resources("compute", "AWS::ApiGatewayV2::Authorizer").values()
    assert auth["Properties"]["AuthorizerType"] == "JWT"
    assert len(auth["Properties"]["JwtConfig" if "JwtConfig" in auth["Properties"] else "JwtConfiguration"]["Audience"]) == 2
    [api] = resources("compute", "AWS::ApiGatewayV2::Api").values()
    origins = api["Properties"]["CorsConfiguration"]["AllowOrigins"]
    assert "*" not in origins
    assert T["compute"]["Parameters"]["AllowedOrigin"]["AllowedPattern"].startswith("^https://")


def test_sns_ecr_y_secreto() -> None:
    [topic] = resources("compute", "AWS::SNS::Topic").values()
    assert topic["Properties"]["TopicName"] == "ultra-skincare-handoff"
    [repo] = resources("compute", "AWS::ECR::Repository").values()
    assert repo["Properties"]["RepositoryName"] == "ultra-skincare-agent"
    assert repo["Properties"]["ImageScanningConfiguration"]["ScanOnPush"] is True
    assert T["compute"]["Parameters"]["PubMedApiKey"]["NoEcho"] is True
    assert T["compute"]["Resources"]["PubMedSecret"]["Condition"] == "HasPubMedKey"


# ---- 05 hosting ----------------------------------------------------------------------------------------------------------------

def test_cloudfront_redirige_a_https_usa_oac_y_tls_minimo_con_dominio_propio() -> None:
    [dist] = resources("frontend", "AWS::CloudFront::Distribution").values()
    config = dist["Properties"]["DistributionConfig"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    origin = config["Origins"][0]
    assert "OriginAccessControlId" in origin and origin["S3OriginConfig"]["OriginAccessIdentity"] == ""
    with_domain = config["ViewerCertificate"]["Fn::If"][1]
    assert with_domain["MinimumProtocolVersion"] == "TLSv1.2_2021" and with_domain["SslSupportMethod"] == "sni-only"


def test_el_bucket_de_hosting_solo_lo_lee_cloudfront() -> None:
    [policy] = resources("frontend", "AWS::S3::BucketPolicy").values()
    allow = [s for s in policy["Properties"]["PolicyDocument"]["Statement"] if s["Effect"] == "Allow"]
    assert len(allow) == 1
    assert allow[0]["Principal"] == {"Service": "cloudfront.amazonaws.com"} and allow[0]["Action"] == "s3:GetObject"
    assert "AWS:SourceArn" in allow[0]["Condition"]["StringEquals"]


def test_cabeceras_de_seguridad_csp_y_permisos_de_microfono_y_camara() -> None:
    [policy] = resources("frontend", "AWS::CloudFront::ResponseHeadersPolicy").values()
    config = policy["Properties"]["ResponseHeadersPolicyConfig"]
    csp = json.dumps(config["SecurityHeadersConfig"]["ContentSecurityPolicy"])
    for fragment in ("wss://bedrock-agentcore.", "cognito-idp.", "frame-ancestors 'none'", "object-src 'none'"):
        assert fragment in csp
    assert "unsafe-eval" not in csp
    assert config["SecurityHeadersConfig"]["StrictTransportSecurity"]["AccessControlMaxAgeSec"] >= 31536000
    [custom] = config["CustomHeadersConfig"]["Items"]
    assert custom["Header"] == "Permissions-Policy" and "microphone=(self)" in custom["Value"] and "camera=(self)" in custom["Value"]

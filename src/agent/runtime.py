"""Ensamblado del agente: elige las implementaciones locales o las de AWS según la configuración.

Local (predeterminado): catálogo en archivo, recomendaciones en memoria, derivación solo en el log.
Producción (`PRODUCTION=true`): Knowledge Base + DynamoDB, `ultra-recomendaciones`, `ultra-sesiones` y SNS.
El agente y las herramientas solo conocen las interfaces de `advisor/ports.py`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

import boto3
from botocore.config import Config as BotoConfig

from advisor.config import Config, load_config
from advisor.dynamo import DynamoRecommendations
from advisor.guardrail import GuardrailClient, NullGuardrail
from advisor.guide import Guide
from advisor.handoff import SnsHandoffNotifier
from advisor.kb import KnowledgeBaseCatalog
from advisor.local import InMemoryRecommendations, JsonCatalog, LogNotifier
from advisor.pubmed import DynamoEvidenceCache, PubMedConsultant
from advisor.routine import RoutineEngine
from advisor.safety_audio import SafetyAudio
from advisor.session_store import DynamoSessionStore, InMemorySessionStore, SessionStore
from advisor.tools import Deps

log = logging.getLogger("runtime")


@dataclass
class Runtime:
    cfg: Config
    deps: Deps
    guardrail: Any  # GuardrailClient | NullGuardrail
    gate_enabled: bool  # TurnGate activo: solo con Guardrail configurado
    sessions: SessionStore
    audio: SafetyAudio
    production: bool


def _secret_provider(secret_id: str, region: str) -> Callable[[], str | None]:
    """API key de NCBI desde Secrets Manager (se lee una vez y se recuerda). Sin secreto: `None`."""
    cache: dict[str, str | None] = {}

    def get() -> str | None:
        if "key" not in cache:
            try:
                client = boto3.client("secretsmanager", region_name=region)
                cache["key"] = client.get_secret_value(SecretId=secret_id)["SecretString"].strip() or None
            except Exception as exc:  # noqa: BLE001 - PubMed opera sin clave o devuelve vacío
                log.warning("no se pudo leer el secreto de PubMed (%s)", type(exc).__name__)
                return None
        return cache["key"]

    return get


def build_runtime(cfg: Config | None = None) -> Runtime:
    cfg = cfg or load_config()
    bedrock = boto3.client(
        "bedrock-runtime", region_name=cfg.region, config=BotoConfig(read_timeout=10, retries={"max_attempts": 1})
    )
    guardrail_cfg = (
        {"guardrailIdentifier": cfg.guardrail_id, "guardrailVersion": cfg.guardrail_version}
        if cfg.guardrail_id and cfg.guardrail_version
        else None
    )
    if cfg.production and not guardrail_cfg:
        raise RuntimeError("PRODUCTION=true exige GUARDRAIL_ID y GUARDRAIL_VERSION (Req. 12)")

    if cfg.production:
        if not cfg.kb_id:
            raise RuntimeError("PRODUCTION=true exige KB_ID")
        catalog: Any = KnowledgeBaseCatalog.from_env(cfg.kb_id, cfg.products_table, cfg.region)
        store: Any = DynamoRecommendations.from_env()
        sessions: SessionStore = DynamoSessionStore.from_env()
        notifier: Any = (
            SnsHandoffNotifier.from_env(cfg.handoff_topic_arn, sessions, cfg.handoff_confirm_url)
            if cfg.handoff_topic_arn
            else LogNotifier()
        )
        if not cfg.handoff_topic_arn:
            log.error("HANDOFF_TOPIC_ARN no está definido: las derivaciones solo se registran en el log")
    else:
        catalog = JsonCatalog(cfg.catalog_path)
        store = InMemoryRecommendations()
        sessions = InMemorySessionStore()
        notifier = LogNotifier()

    pubmed = None
    if cfg.pubmed_enabled:
        table = boto3.resource("dynamodb", region_name=cfg.region).Table(
            os.environ.get("EVIDENCE_TABLE", "ultra-evidencias-ingredientes")
        )
        pubmed = PubMedConsultant(
            DynamoEvidenceCache(table), api_key=_secret_provider(os.environ.get("PUBMED_SECRET_ID", ""), cfg.region)
        )

    engine = RoutineEngine(catalog, cfg.routine_model_id, bedrock, guardrail=guardrail_cfg)
    deps = Deps(
        catalog=catalog,
        store=store,
        notifier=notifier,
        engine=engine,
        budget_bounds=cfg.budget_bounds,
        store_domain=cfg.store_domain,
        guide=Guide.load(cfg.guide_path),
        pubmed=pubmed,
    )
    guardrail: Any = (
        GuardrailClient.from_env(cfg.guardrail_id, cfg.guardrail_version, cfg.region) if guardrail_cfg else NullGuardrail()
    )
    audio = SafetyAudio(expected_rate=cfg.output_sample_rate)
    if audio.missing():
        log.warning("faltan audios pregrabados (se enviará solo texto): %s", ", ".join(audio.missing()))
    return Runtime(
        cfg=cfg, deps=deps, guardrail=guardrail, gate_enabled=guardrail_cfg is not None,
        sessions=sessions, audio=audio, production=cfg.production,
    )

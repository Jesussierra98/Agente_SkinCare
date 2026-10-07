"""Arma las piezas del agente según el entorno.

Sin variables de AWS todo sigue siendo local (como en el prototipo); cada pieza de producción se activa solo cuando
están TODAS las variables que necesita, así un valor a medias nunca deja una pieza a medio configurar.

| Pieza                       | Se activa con                                              |
|-----------------------------|------------------------------------------------------------|
| Recomendaciones (DynamoDB)  | `RECOMMENDATIONS_TABLE`                                    |
| Aviso al asesor (SNS)       | `HANDOFF_TOPIC_ARN` y `SESSIONS_TABLE`                     |
| Sesión persistente          | `SESSIONS_TABLE`                                           |
| Guardrail y TurnGate        | `GUARDRAIL_ID` y `GUARDRAIL_VERSION`                       |
| PubMed                      | `PUBMED_ENABLED=true` (`EVIDENCE_TABLE` y `NCBI_API_KEY` opcionales) |
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .config import Config
from .dynamo import DynamoRecommendations
from .guardrail import GuardrailClient, TurnGate
from .handoff import SnsHandoffNotifier, dynamo_confirmation_check
from .local import InMemoryRecommendations, LogNotifier
from .ports import HandoffNotifier, RecommendationStore
from .prompts import PUBMED_ADDENDUM, SYSTEM_PROMPT
from .pubmed import DynamoEvidenceCache, InMemoryEvidenceCache, PubMedConsultant
from .session import now_iso
from .session_store import SessionStore

log = logging.getLogger("advisor.runtime")

ConfirmationCheck = Callable[[str], Callable[[], Awaitable[bool]]]


@dataclass
class Runtime:
    recommendations: RecommendationStore
    notifier: HandoffNotifier
    gate: TurnGate | None
    session_store: SessionStore | None
    confirmation_check: ConfirmationCheck | None
    pubmed: PubMedConsultant | None
    system_prompt: str
    #: `guardrailConfig` para TODAS las llamadas de Converse del Motor_Rutina (Req. 12.5); `None` sin Guardrail.
    routine_guardrail: dict[str, str] | None

    def describe(self) -> dict[str, bool]:
        """Qué piezas de producción están activas (para `/health` y el arranque)."""
        return {
            "recomendaciones_dynamodb": isinstance(self.recommendations, DynamoRecommendations),
            "aviso_sns": isinstance(self.notifier, SnsHandoffNotifier),
            "guardrail": self.gate is not None,
            "sesion_persistente": self.session_store is not None,
            "pubmed": self.pubmed is not None,
        }


def build_runtime(
    cfg: Config,
    env: Mapping[str, str],
    *,
    bedrock: Any = None,
    dynamodb: Any = None,
    sns: Any = None,
) -> Runtime:
    def need_dynamodb() -> Any:
        nonlocal dynamodb
        if dynamodb is None:
            import boto3
            from botocore.config import Config as BotoConfig

            # Timeouts cortos: el guardado completo debe caber en 3 s (Req. 13.4).
            dynamodb = boto3.resource(
                "dynamodb", region_name=cfg.region, config=BotoConfig(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1})
            )
        return dynamodb

    recs_table = env.get("RECOMMENDATIONS_TABLE")
    sessions_table_name = env.get("SESSIONS_TABLE")
    topic_arn = env.get("HANDOFF_TOPIC_ARN")

    recommendations: RecommendationStore = (
        DynamoRecommendations(need_dynamodb().Table(recs_table)) if recs_table else InMemoryRecommendations()
    )

    sessions = need_dynamodb().Table(sessions_table_name) if sessions_table_name else None
    session_store = SessionStore(sessions) if sessions is not None else None
    confirmation_check: ConfirmationCheck | None = (
        (lambda session_id: dynamo_confirmation_check(sessions, session_id)) if sessions is not None else None
    )

    notifier: HandoffNotifier
    if topic_arn and sessions is not None:
        if sns is None:
            import boto3

            sns = boto3.client("sns", region_name=cfg.region)
        notifier = SnsHandoffNotifier(sns, topic_arn, sessions, env.get("HANDOFF_CONFIRM_BASE_URL") or cfg.store_domain, now_iso)
    else:
        if topic_arn and sessions is None:
            log.warning("HANDOFF_TOPIC_ARN sin SESSIONS_TABLE: las derivaciones solo se registran en el log")
        notifier = LogNotifier()

    gate: TurnGate | None = None
    routine_guardrail: dict[str, str] | None = None
    if cfg.guardrail_id and cfg.guardrail_version:
        if bedrock is None:
            import boto3

            bedrock = boto3.client("bedrock-runtime", region_name=cfg.region)
        gate = TurnGate(GuardrailClient(bedrock, cfg.guardrail_id, cfg.guardrail_version))
        routine_guardrail = {"guardrailIdentifier": cfg.guardrail_id, "guardrailVersion": cfg.guardrail_version}
    elif cfg.guardrail_id or cfg.guardrail_version:
        log.warning("Guardrail a medias (hacen falta GUARDRAIL_ID y GUARDRAIL_VERSION): se ejecuta SIN Guardrail")

    pubmed: PubMedConsultant | None = None
    system_prompt = SYSTEM_PROMPT
    if cfg.pubmed_enabled:
        evidence_table = env.get("EVIDENCE_TABLE")
        cache = DynamoEvidenceCache(need_dynamodb().Table(evidence_table)) if evidence_table else InMemoryEvidenceCache()
        api_key = env.get("NCBI_API_KEY")
        pubmed = PubMedConsultant(cache, api_key=lambda: api_key)
        system_prompt = SYSTEM_PROMPT + PUBMED_ADDENDUM

    return Runtime(
        recommendations=recommendations,
        notifier=notifier,
        gate=gate,
        session_store=session_store,
        confirmation_check=confirmation_check,
        pubmed=pubmed,
        system_prompt=system_prompt,
        routine_guardrail=routine_guardrail,
    )

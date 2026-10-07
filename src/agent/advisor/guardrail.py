"""Guardrail de Bedrock y `TurnGate` (Req. 12.3, 12.4, 12.6).

`TurnGate` decide si el turno del modelo puede llegar al cliente. Falla cerrado: solo un resultado `approved` del
Guardrail, sin condición sensible detectada, autoriza el turno. Cualquier intervención, tiempo agotado o error lo bloquea.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from . import sensitive

log = logging.getLogger("advisor.guardrail")

GUARDRAIL_TIMEOUT_S = 3.0

# Nombres de los temas denegados del Guardrail `ultra-skincare-guardrail` (los crea el stack 03 con estos nombres).
TOPIC_MEDICAL = "diagnostico_medico"
TOPIC_CHEMICAL = "incompatibilidad_quimica"

# Mensaje amable de bloqueo (máx. 200 caracteres, sin repetir ni parafrasear lo bloqueado). Se usa también para
# generar el audio pregrabado del idioma vigente.
BLOCK_MESSAGES: dict[str, str] = {
    "es": "Para eso lo mejor es que te atienda una asesora de la tienda. Ya le avisé y en un momento te ayuda.",
    "en": "For that, it's best to have one of our in-store advisors help you. I've let them know and they'll be with you shortly.",
}

Status = Literal["approved", "intervened", "error"]
Source = Literal["INPUT", "OUTPUT"]


@dataclass(frozen=True)
class Verdict:
    status: Status
    topics: tuple[str, ...] = ()  # temas denegados que activaron la intervención


class GuardrailChecker(Protocol):
    async def check(self, text: str, source: Source) -> Verdict: ...


class GuardrailClient:
    """`bedrock-runtime:ApplyGuardrail` con tiempo límite de 3 s. Nunca lanza: un fallo es `Verdict("error")`."""

    def __init__(self, client: Any, guardrail_id: str, guardrail_version: str, timeout_s: float = GUARDRAIL_TIMEOUT_S) -> None:
        self._client = client
        self._id = guardrail_id
        self._version = guardrail_version
        self._timeout_s = timeout_s

    def _apply(self, text: str, source: Source) -> dict[str, Any]:
        return self._client.apply_guardrail(
            guardrailIdentifier=self._id,
            guardrailVersion=self._version,
            source=source,
            content=[{"text": {"text": text}}],
        )

    async def check(self, text: str, source: Source) -> Verdict:
        try:
            response = await asyncio.wait_for(asyncio.to_thread(self._apply, text, source), timeout=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - tiempo agotado, red, permisos...: falla cerrada
            log.warning("ApplyGuardrail falló (%s)", type(exc).__name__)
            return Verdict("error")
        if response.get("action") == "NONE":
            return Verdict("approved")
        if response.get("action") == "GUARDRAIL_INTERVENED":
            return Verdict("intervened", _blocked_topics(response))
        return Verdict("error")  # respuesta inesperada: no se asume aprobación


def _blocked_topics(response: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for assessment in response.get("assessments", []):
        for topic in assessment.get("topicPolicy", {}).get("topics", []):
            if topic.get("action") == "BLOCKED" and topic.get("name"):
                names.append(str(topic["name"]))
    return tuple(names)


def motivo_for_topics(topics: tuple[str, ...]) -> str:
    """Motivo de derivación según el tema bloqueado. Sin tema conocido se usa `diagnostico` (no suspende recomendaciones)."""
    return "compatibilidad" if TOPIC_CHEMICAL in topics else "diagnostico"


Reason = Literal["aprobado", "sensible", "guardrail", "error"]


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: Reason
    #: Motivo para `HandoffService`. Puede venir con `allowed=True` (alergia a un producto: se avisa pero se sigue).
    handoff_motivo: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def must_block(self) -> bool:
        return not self.allowed


class TurnGate:
    def __init__(
        self,
        guardrail: GuardrailChecker,
        detect: Callable[[str], tuple[str, str] | None] = sensitive.detect_with_term,
        timeout_s: float = GUARDRAIL_TIMEOUT_S,
    ) -> None:
        self._guardrail = guardrail
        self._detect = detect
        self._timeout_s = timeout_s

    async def _verdict(self, text: str, source: Source) -> Verdict:
        try:
            return await asyncio.wait_for(self._guardrail.check(text, source), timeout=self._timeout_s)
        except Exception:  # noqa: BLE001 - incluye TimeoutError: falla cerrada
            return Verdict("error")

    async def evaluate_input(self, text: str) -> GateDecision:
        """Transcripción final del cliente: Guardrail (INPUT) y detector léxico en paralelo."""
        verdict_task = asyncio.create_task(self._verdict(text, "INPUT"))
        found = self._detect(text)  # síncrono y rápido: corre mientras el Guardrail responde
        verdict = await verdict_task

        if found is not None and found[0] != "alergia_producto":
            return GateDecision(False, "sensible", found[0], {"termino": found[1]})
        if verdict.status == "intervened":
            return GateDecision(False, "guardrail", motivo_for_topics(verdict.topics), {"temas": list(verdict.topics)})
        if verdict.status != "approved":
            return GateDecision(False, "error", "diagnostico")
        # Aprobado. Una alergia a un producto avisa al asesor, pero el cliente puede seguir.
        return GateDecision(True, "aprobado", found[0] if found else None)

    async def evaluate_output(self, sentence: str) -> GateDecision:
        """Oración completa de la transcripción del asesor (Guardrail OUTPUT)."""
        verdict = await self._verdict(sentence, "OUTPUT")
        if verdict.status == "approved":
            return GateDecision(True, "aprobado")
        if verdict.status == "intervened":
            return GateDecision(False, "guardrail", motivo_for_topics(verdict.topics), {"temas": list(verdict.topics)})
        return GateDecision(False, "error", "diagnostico")

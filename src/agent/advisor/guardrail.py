"""Guardrail de Bedrock y `TurnGate` (Req. 12.3, 12.4, 12.6, 9.2).

Nova 2 Sonic no aplica Guardrails por sí mismo (DD-05), así que el servidor llama a `ApplyGuardrail` sobre
la transcripción final del cliente (INPUT) y sobre cada oración del asesor (OUTPUT).

El `TurnGate` falla cerrado: solo el resultado `APROBADO` de TODAS las comprobaciones (Guardrail y detector
local de condiciones sensibles, en paralelo y dentro de 3 s) deja pasar el audio del modelo. Una intervención,
una detección sensible, un timeout o cualquier error descarta el audio y deriva al asesor.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from . import sensitive

log = logging.getLogger("advisor.guardrail")

GUARDRAIL_TIMEOUT_S = 3.0
GATE_TIMEOUT_S = 3.0


class Verdict(str, Enum):
    APPROVED = "aprobado"
    INTERVENED = "intervencion"
    ERROR = "error"  # timeout o falla del servicio


class GuardrailClient:
    """`ApplyGuardrail` con timeout de 3 s. Cualquier falla devuelve `Verdict.ERROR` (nunca aprueba)."""

    def __init__(
        self, client: Any, guardrail_id: str, guardrail_version: str, timeout_s: float = GUARDRAIL_TIMEOUT_S
    ) -> None:
        self._client = client
        self._id = guardrail_id
        self._version = guardrail_version
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, guardrail_id: str, guardrail_version: str, region: str) -> "GuardrailClient":
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(connect_timeout=1, read_timeout=3, retries={"max_attempts": 1}),
        )
        return cls(client, guardrail_id, guardrail_version)

    async def check(self, text: str, source: str) -> Verdict:
        """`source` es `INPUT` (cliente) u `OUTPUT` (asesor)."""
        if source not in {"INPUT", "OUTPUT"}:
            raise ValueError(f"source inválido: {source}")
        if not text.strip():
            return Verdict.APPROVED

        def call() -> dict[str, Any]:
            return self._client.apply_guardrail(
                guardrailIdentifier=self._id,
                guardrailVersion=self._version,
                source=source,
                content=[{"text": {"text": text}}],
            )

        try:
            response = await asyncio.wait_for(asyncio.to_thread(call), timeout=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - timeout, red o permisos: falla cerrado
            log.error("ApplyGuardrail(%s) falló: %s", source, type(exc).__name__)
            return Verdict.ERROR
        action = response.get("action")
        if action == "NONE":
            return Verdict.APPROVED
        if action == "GUARDRAIL_INTERVENED":
            return Verdict.INTERVENED
        log.error("ApplyGuardrail(%s) devolvió una acción desconocida: %r", source, action)
        return Verdict.ERROR


class NullGuardrail:
    """Sin Guardrail configurado (desarrollo local): aprueba todo. No usar en producción."""

    async def check(self, text: str, source: str) -> Verdict:  # noqa: ARG002
        return Verdict.APPROVED


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    reason: str  # "ok" | "guardrail" | "sensitive" | "error" | "timeout"
    motivo: str | None = None  # motivo de derivación cuando reason == "sensitive"


Detector = Callable[[str], tuple[str, str] | None]


class TurnGate:
    """Decide si el turno del cliente puede recibir respuesta hablada."""

    def __init__(
        self,
        guardrail: Any,
        detector: Detector = sensitive.detect_with_term,
        timeout_s: float = GATE_TIMEOUT_S,
    ) -> None:
        self._guardrail = guardrail
        self._detector = detector
        self._timeout_s = timeout_s

    async def evaluate(self, text: str) -> GateDecision:
        async def run() -> GateDecision:
            verdict_task = asyncio.ensure_future(self._guardrail.check(text, "INPUT"))
            try:
                found = self._detector(text)
            except Exception:  # noqa: BLE001 - un detector roto no puede abrir la puerta
                verdict_task.cancel()
                return GateDecision(False, "error")
            verdict = await verdict_task
            if verdict is Verdict.INTERVENED:
                return GateDecision(False, "guardrail", found[0] if found else None)
            if verdict is not Verdict.APPROVED:
                return GateDecision(False, "error", found[0] if found else None)
            if found:
                return GateDecision(False, "sensitive", found[0])
            return GateDecision(True, "ok")

        try:
            return await asyncio.wait_for(run(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            return GateDecision(False, "timeout")
        except Exception:  # noqa: BLE001
            return GateDecision(False, "error")


class AudioGate:
    """Retiene el audio del modelo mientras el `TurnGate` decide.

    `push` devuelve los fragmentos que deben enviarse ahora: todos si el turno está abierto, ninguno si está
    retenido o rechazado. `approve` libera lo retenido; `reject` lo descarta y mantiene cerrado el turno.
    """

    def __init__(self) -> None:
        self._state = "open"  # "open" | "held" | "rejected"
        self._buffer: list[Any] = []

    @property
    def state(self) -> str:
        return self._state

    def hold(self) -> None:
        self._state = "held"
        self._buffer.clear()

    def push(self, chunk: Any) -> list[Any]:
        if self._state == "open":
            return [chunk]
        if self._state == "held":
            self._buffer.append(chunk)
        return []

    def approve(self) -> list[Any]:
        released, self._buffer = self._buffer, []
        self._state = "open"
        return released

    def reject(self) -> None:
        self._buffer.clear()
        self._state = "rejected"

    def reset(self) -> None:
        """Nuevo turno sin evaluación pendiente (por ejemplo, el saludo)."""
        self._buffer.clear()
        self._state = "open"


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_sentences(text: str) -> tuple[list[str], str]:
    """Oraciones completas del texto y el resto todavía incompleto."""
    parts = [p for p in _SENTENCE_END.split(text) if p is not None]
    if not parts:
        return [], ""
    if re.search(r"[.!?…]\s*$|\n\s*$", text):
        return [p.strip() for p in parts if p.strip()], ""
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


async def first_blocked_sentence(
    guardrail: Any, sentences: list[str]
) -> tuple[str, Verdict] | None:
    """Primera oración del asesor que el Guardrail no aprueba (OUTPUT), o `None` si todas pasan."""
    for sentence in sentences:
        verdict = await guardrail.check(sentence, "OUTPUT")
        if verdict is not Verdict.APPROVED:
            return sentence, verdict
    return None

"""Derivación al asesor humano en producción (Req. 9.7, 9.8, 12.7).

`SnsHandoffNotifier` cumple `HandoffNotifier`: escribe el registro en `ultra-sesiones` y publica en el
tópico SNS `ultra-skincare-handoff` (en 3 s como máximo) con motivo, perfil, `session_id` y el enlace de
confirmación. El plazo de 10 s para avisar y la suspensión de recomendaciones los aplica `start_handoff`.

`ConfirmationWatcher` consulta cada 2 s si un asesor confirmó la derivación; si pasan 30 s sin confirmación
avisa una sola vez (para reproducir "un asesor lo atenderá en breve") y la derivación sigue activa.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from .session import now_iso
from .session_store import SessionStore

log = logging.getLogger("advisor.handoff")

PUBLISH_TIMEOUT_S = 3.0
POLL_INTERVAL_S = 2.0
CONFIRM_TIMEOUT_S = 30.0

SUBJECTS = {
    "condicion_sensible": "Cliente con condición sensible",
    "requiere_asesor": "Cliente que requiere asesor",
    "diagnostico": "Cliente pidió un diagnóstico",
    "compatibilidad": "Cliente preguntó por compatibilidad",
    "alergia_producto": "Cliente con alergia a un producto",
}


def confirmation_link(base_url: str, session_id: str) -> str:
    """Enlace que abre la pantalla de confirmación de la Vista_Caja (`/derivacion/{session_id}`)."""
    return f"{base_url.rstrip('/')}/derivacion/{session_id}" if base_url else ""


def build_message(session_id: str, motivo: str, perfil: dict[str, Any], base_url: str) -> dict[str, str]:
    """Asunto y cuerpo de la notificación. El perfil va sin cambios."""
    body = {
        "session_id": session_id,
        "motivo": motivo,
        "perfil": perfil,
        "enlace_confirmacion": confirmation_link(base_url, session_id),
    }
    return {
        "Subject": SUBJECTS.get(motivo, "Cliente requiere asesor")[:100],
        "Message": json.dumps(body, ensure_ascii=False, default=str),
    }


class SnsHandoffNotifier:
    """Registro en `ultra-sesiones` + publicación en SNS. Lanza si no termina en 3 s."""

    def __init__(self, sns: Any, topic_arn: str, store: SessionStore, confirm_base_url: str = "") -> None:
        self._sns = sns
        self._topic_arn = topic_arn
        self._store = store
        self._base_url = confirm_base_url

    @classmethod
    def from_env(cls, topic_arn: str, store: SessionStore, confirm_base_url: str) -> "SnsHandoffNotifier":
        import boto3
        from botocore.config import Config

        sns = boto3.client("sns", config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}))
        return cls(sns, topic_arn, store, confirm_base_url)

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        record = {"motivo": motivo, "perfil": perfil, "fecha": now_iso(), "estado": "pendiente"}
        message = build_message(session_id, motivo, perfil, self._base_url)

        async def run() -> None:
            await self._store.record_handoff(session_id, record)
            await asyncio.to_thread(self._sns.publish, TopicArn=self._topic_arn, **message)

        await asyncio.wait_for(run(), timeout=PUBLISH_TIMEOUT_S)
        log.info("derivación notificada: sesión=%s motivo=%s", session_id, motivo)


class ConfirmationWatcher:
    """Espera la confirmación de un asesor. Avisa una sola vez si pasan `timeout_s` sin ella."""

    def __init__(
        self,
        store: SessionStore,
        session_id: str,
        on_timeout: Callable[[], Awaitable[None]],
        *,
        poll_s: float = POLL_INTERVAL_S,
        timeout_s: float = CONFIRM_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._on_timeout = on_timeout
        self._poll_s = poll_s
        self._timeout_s = timeout_s
        self._clock = clock
        self._sleep = sleep
        self.notified_timeout = False

    async def run(self) -> str:
        """Devuelve `"confirmada"` o `"sin_confirmar"` (tras avisar la espera). Un fallo de lectura no la detiene."""
        started = self._clock()
        while True:
            try:
                if await self._store.handoff_state(self._session_id) == "confirmada":
                    return "confirmada"
            except Exception as exc:  # noqa: BLE001 - se reintenta en la siguiente consulta
                log.warning("no se pudo consultar la confirmación: %s", exc)
            if self._clock() - started >= self._timeout_s:
                break
            await self._sleep(self._poll_s)
        if not self.notified_timeout:
            self.notified_timeout = True
            await self._on_timeout()
        return "sin_confirmar"

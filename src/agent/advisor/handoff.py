"""Derivación al asesor humano por SNS y espera de su confirmación (Req. 9, 12.7).

- `SnsHandoffNotifier` es un `HandoffNotifier`: escribe el registro en `ultra-sesiones` y publica en SNS con el motivo, el
  perfil, el `session_id` y un enlace de confirmación. Si cualquiera de las dos cosas falla, lanza (la derivación queda
  `sin_notificar` y se le pide al cliente ir al mostrador).
- `wait_for_confirmation` consulta cada 2 s hasta 30 s si el asesor confirmó.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import quote

log = logging.getLogger("advisor.handoff")

CONFIRM_POLL_S = 2.0
CONFIRM_TIMEOUT_S = 30.0


def confirmation_link(base_url: str, session_id: str) -> str:
    """Enlace que abre el asesor para confirmar (`POST /derivaciones/{session_id}/confirmar` desde la Vista_Caja)."""
    return f"{base_url.rstrip('/')}/caja/derivacion?session={quote(session_id, safe='')}"


def build_message(session_id: str, motivo: str, perfil: dict[str, Any], base_url: str, fecha: str) -> dict[str, Any]:
    """Mensaje de SNS: motivo y perfil tal como se recibieron, sin cambios."""
    return {
        "session_id": session_id,
        "motivo": motivo,
        "perfil": perfil,
        "fecha": fecha,
        "enlace_confirmacion": confirmation_link(base_url, session_id),
    }


class SnsHandoffNotifier:
    def __init__(self, sns: Any, topic_arn: str, sessions_table: Any, confirm_base_url: str, now_iso: Callable[[], str]) -> None:
        self._sns = sns
        self._topic_arn = topic_arn
        self._table = sessions_table
        self._base = confirm_base_url
        self._now_iso = now_iso

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        fecha = self._now_iso()
        message = build_message(session_id, motivo, perfil, self._base, fecha)
        record = {"motivo": motivo, "estado": "pendiente", "perfil": perfil, "fecha": fecha}

        def write() -> None:
            self._table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET handoff = :h",
                ExpressionAttributeValues={":h": record},
            )

        def publish() -> None:
            self._sns.publish(
                TopicArn=self._topic_arn,
                Subject="Derivación de cliente en piso",
                Message=json.dumps(message, ensure_ascii=False, default=str),
            )

        await asyncio.gather(asyncio.to_thread(write), asyncio.to_thread(publish))


Outcome = Literal["confirmada", "sin_confirmacion"]


async def wait_for_confirmation(
    is_confirmed: Callable[[], Awaitable[bool]],
    *,
    poll_s: float = CONFIRM_POLL_S,
    timeout_s: float = CONFIRM_TIMEOUT_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Consulta cada `poll_s` hasta `timeout_s`. Una falla de la consulta se toma como "aún no" y se reintenta."""
    started = monotonic()
    while True:
        try:
            if await is_confirmed():
                return "confirmada"
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo consultar la confirmación (%s)", type(exc).__name__)
        if monotonic() - started >= timeout_s:
            return "sin_confirmacion"
        await sleep(poll_s)


def dynamo_confirmation_check(sessions_table: Any, session_id: str) -> Callable[[], Awaitable[bool]]:
    """`is_confirmed` para `wait_for_confirmation` sobre `ultra-sesiones` (`handoff.estado == "confirmada"`)."""

    async def check() -> bool:
        item = await asyncio.to_thread(lambda: sessions_table.get_item(Key={"session_id": session_id}).get("Item"))
        return bool(item) and item.get("handoff", {}).get("estado") == "confirmada"

    return check

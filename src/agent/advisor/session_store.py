"""Persistencia de la sesión en `ultra-sesiones` (Req. 21.8, 21.11, 24.5 y 24.6).

La sesión se guarda en cada cambio para poder restaurarla tras una renovación de la conexión de voz. Vence a las 24 h:
`load` trata como inexistente una sesión con `now >= created_at + 86400` aunque DynamoDB todavía no haya borrado el ítem.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

from .models import PASOS
from .ports import CatalogRepository
from .profile import ProfileState
from .session import Session

log = logging.getLogger("advisor.session_store")

SESSION_SECONDS = 86_400
MAX_HISTORY_BYTES = 200 * 1024
MAX_MESSAGE_BYTES = 50 * 1024


class SessionExpired(Exception):
    """La sesión existe pero pasó de 24 h: hay que iniciar una nueva."""


def is_valid_now(created_at: int, now: float) -> bool:
    """Una sesión es válida exactamente durante sus primeras 24 h."""
    return now < created_at + SESSION_SECONDS


def _size(message: Any) -> int:
    return len(json.dumps(message, ensure_ascii=False).encode("utf-8"))


def _clip_text(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", errors="ignore")


def trim_messages(
    messages: list[dict[str, Any]], max_total: int = MAX_HISTORY_BYTES, max_message: int = MAX_MESSAGE_BYTES
) -> list[dict[str, Any]]:
    """Historial reciente: cada mensaje se recorta a `max_message` y se descartan los más viejos hasta caber en `max_total`."""
    clipped: list[dict[str, Any]] = []
    for message in messages:
        blocks = [
            {**block, "text": _clip_text(block["text"], max_message)} if isinstance(block.get("text"), str) else block
            for block in message.get("content", [])
        ]
        clipped.append({**message, "content": blocks})
    kept: list[dict[str, Any]] = []
    total = 0
    for message in reversed(clipped):
        size = _size(message)
        if total + size > max_total:
            break
        kept.append(message)
        total += size
    kept.reverse()
    return kept


def from_dynamo(value: Any) -> Any:
    """DynamoDB devuelve todos los números como `Decimal`: los enteros vuelven a `int` (p. ej. `paso`), el resto a `float`."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def session_to_record(session: Session, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Registro de `ultra-sesiones`. Los candidatos se guardan reducidos (solo SKU) y se rehidratan del catálogo."""
    profile = session.profile
    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "ttl": session.created_at + SESSION_SECONDS,
        "language": session.language.current,
        "profile": {
            "valores": dict(profile.valores),
            "intentos_fallidos": dict(profile.intentos_fallidos),
            "no_proporcionado": sorted(profile.no_proporcionado),
            "indicadores_sensibles": list(profile.indicadores_sensibles),
            "exchange_count": profile.exchange_count,
            "min_exchanges": profile.min_exchanges,
            "tope_mxn": profile.tope_mxn,
        },
        "candidates": {paso: [{"sku": p.sku} for p in products] for paso, products in session.candidates.items()},
        "routine": session.routine,
        "readings": session.readings,
        "messages": trim_messages(messages or []),
        "handoff": session.handoff,
        "recommendations_suspended": session.recommendations_suspended,
        "saved": session.saved,
        "level": session.level,
        "rejected_skus": sorted(session.rejected_skus),
        "avoided_brands": sorted(session.avoided_brands),
        "adjustments": session.adjustments,
        "routine_change": session.routine_change,
        "total_cap": str(session.total_cap) if session.total_cap is not None else None,
        "product_cap": str(session.product_cap) if session.product_cap is not None else None,
    }


async def restore_session(record: dict[str, Any], session: Session, catalog: CatalogRepository) -> Session:
    """Vuelca un registro guardado en una `Session` nueva (misma identidad y contexto)."""
    record = from_dynamo(record)
    stored = record["profile"]
    session.session_id = record["session_id"]
    session.created_at = int(record["created_at"])
    session.language.current = record.get("language", "es")
    session.profile = ProfileState(
        valores=dict(stored["valores"]),
        intentos_fallidos=dict(stored["intentos_fallidos"]),
        no_proporcionado=set(stored["no_proporcionado"]),
        indicadores_sensibles=list(stored["indicadores_sensibles"]),
        exchange_count=int(stored["exchange_count"]),
        min_exchanges=int(stored["min_exchanges"]),
        tope_mxn=int(stored["tope_mxn"]),
    )
    for paso in PASOS:
        skus = [c["sku"] for c in record.get("candidates", {}).get(paso, [])]
        found = await catalog.get_many(skus)
        session.candidates[paso] = [found[s] for s in skus if s in found]
    session.routine = record.get("routine")
    session.readings = list(record.get("readings", []))
    session.handoff = record.get("handoff")
    session.recommendations_suspended = bool(record.get("recommendations_suspended", False))
    session.saved = record.get("saved")
    session.level = record.get("level")
    session.rejected_skus = set(record.get("rejected_skus", []))
    session.avoided_brands = set(record.get("avoided_brands", []))
    session.adjustments = int(record.get("adjustments", 0))
    session.routine_change = record.get("routine_change", "inicial")
    session.total_cap = Decimal(record["total_cap"]) if record.get("total_cap") else None
    session.product_cap = Decimal(record["product_cap"]) if record.get("product_cap") else None
    return session


class SessionStore:
    """`ultra-sesiones` sobre una tabla `Table` de boto3."""

    def __init__(self, table: Any, clock: Any = None) -> None:
        import time

        self._table = table
        self._clock = clock or time.time

    async def save(self, session: Session, messages: list[dict[str, Any]] | None = None) -> None:
        record = session_to_record(session, messages)
        await asyncio.to_thread(lambda: self._table.put_item(Item=record))

    async def load(self, session_id: str) -> dict[str, Any] | None:
        """Registro vigente, `None` si no existe. Lanza `SessionExpired` si ya pasaron las 24 h."""
        item = await asyncio.to_thread(lambda: self._table.get_item(Key={"session_id": session_id}).get("Item"))
        if item is None or "created_at" not in item:
            return None
        if not is_valid_now(int(item["created_at"]), self._clock()):
            raise SessionExpired(session_id)
        return item

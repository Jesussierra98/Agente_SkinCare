"""Persistencia de la sesión en `ultra-sesiones` (Req. 21.8, 21.11, 24.5, 24.6).

Lo que se guarda alcanza para restaurar la conversación tras una renovación de la conexión con
Nova 2 Sonic: perfil, intercambios, rutina, lecturas, idioma, derivación y los mensajes recientes
(acotados a 200 KiB en total y 50 KiB por mensaje). La sesión expira a las 24 horas.

Las partes puras (`snapshot`, `restore`, `trim_messages`, `is_expired`) se prueban sin AWS. La tabla
usa un solo atributo JSON (`estado`) para no depender de tipos de DynamoDB (`float`, `Decimal`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from .language import LanguageTracker
from .profile import ProfileState
from .session import Session

log = logging.getLogger("advisor.session_store")

SESSION_TTL_S = 24 * 3600
MAX_MESSAGES_BYTES = 200 * 1024
MAX_MESSAGE_BYTES = 50 * 1024


class SessionExpired(LookupError):
    """La sesión no existe o pasó de 24 horas: hay que iniciar una nueva."""


# ---- funciones puras ---------------------------------------------------------------------------------

def is_expired(created_at: float, now: float) -> bool:
    """Válida exactamente durante sus primeras 24 h: a partir de `created_at + 86400` ya no existe."""
    return now >= created_at + SESSION_TTL_S


def _truncate_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")


def _size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, default=str).encode("utf-8"))


def trim_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mensajes `{role, text}` con a lo más 50 KiB de texto cada uno y 200 KiB en total.

    Si el total se pasa, se descartan los más antiguos (se conservan los recientes).
    """
    clipped = [{**m, "text": _truncate_bytes(str(m.get("text", "")), MAX_MESSAGE_BYTES)} for m in messages]
    total = sum(_size(m) for m in clipped)
    start = 0
    while start < len(clipped) and total > MAX_MESSAGES_BYTES:
        total -= _size(clipped[start])
        start += 1
    return clipped[start:]


def _profile_to_dict(p: ProfileState) -> dict[str, Any]:
    return {
        "valores": dict(p.valores),
        "intentos_fallidos": dict(p.intentos_fallidos),
        "no_proporcionado": sorted(p.no_proporcionado),
        "indicadores_sensibles": list(p.indicadores_sensibles),
        "exchange_count": p.exchange_count,
        "min_exchanges": p.min_exchanges,
        "tope_mxn": p.tope_mxn,
    }


def _profile_from_dict(d: dict[str, Any]) -> ProfileState:
    return ProfileState(
        valores=dict(d.get("valores", {})),
        intentos_fallidos={k: int(v) for k, v in d.get("intentos_fallidos", {}).items()},
        no_proporcionado=set(d.get("no_proporcionado", [])),
        indicadores_sensibles=list(d.get("indicadores_sensibles", [])),
        exchange_count=int(d.get("exchange_count", 0)),
        min_exchanges=int(d.get("min_exchanges", ProfileState().min_exchanges)),
        tope_mxn=int(d.get("tope_mxn", 0)),
    )


def snapshot(session: Session, messages: list[dict[str, Any]], created_at: float) -> dict[str, Any]:
    """Estado serializable de la sesión. Es lo que se escribe en `ultra-sesiones`."""
    return {
        "session_id": session.session_id,
        "created_at": int(created_at),
        "ttl": int(created_at) + SESSION_TTL_S,
        "perfil": _profile_to_dict(session.profile),
        "idioma": session.language.current,
        "rutina": session.routine,
        "guardado": session.saved,
        "lecturas": list(session.readings),
        "derivacion": session.handoff,
        "recomendaciones_suspendidas": session.recommendations_suspended,
        "nivel": session.level,
        "ajustes": session.adjustments,
        "productos_rechazados": sorted(session.rejected_skus),
        "marcas_evitadas": sorted(session.avoided_brands),
        "tope_total": None if session.total_cap is None else str(session.total_cap),
        "tope_producto": None if session.product_cap is None else str(session.product_cap),
        "mensajes": trim_messages(messages),
    }


def restore(data: dict[str, Any], emit: Any) -> tuple[Session, list[dict[str, Any]]]:
    """Reconstruye un `Session` (sin candidatos: se vuelven a buscar) y sus mensajes."""
    session = Session(emit=emit, session_id=data["session_id"])
    session.profile = _profile_from_dict(data.get("perfil", {}))
    session.language = LanguageTracker(data.get("idioma", "es"))
    session.routine = data.get("rutina")
    session.saved = data.get("guardado")
    session.readings = list(data.get("lecturas", []))
    session.handoff = data.get("derivacion")
    session.recommendations_suspended = bool(data.get("recomendaciones_suspendidas", False))
    session.level = data.get("nivel")
    session.adjustments = int(data.get("ajustes", 0))
    session.rejected_skus = set(data.get("productos_rechazados", []))
    session.avoided_brands = set(data.get("marcas_evitadas", []))
    cap = data.get("tope_total")
    session.total_cap = None if cap is None else Decimal(cap)
    pcap = data.get("tope_producto")
    session.product_cap = None if pcap is None else Decimal(pcap)
    return session, list(data.get("mensajes", []))


# ---- almacenamiento ----------------------------------------------------------------------------------

class SessionStore(Protocol):
    async def save(self, data: dict[str, Any]) -> None: ...

    async def load(self, session_id: str, now: float | None = None) -> dict[str, Any]:
        """Estado guardado. Lanza `SessionExpired` si no existe o ya pasaron 24 horas."""

    async def record_handoff(self, session_id: str, record: dict[str, Any]) -> None:
        """Escribe el registro de derivación (atributo `handoff`). No pisa una confirmación ya hecha."""

    async def handoff_state(self, session_id: str) -> str | None:
        """`handoff.estado` (`pendiente`, `confirmada`...) o `None` si no hay derivación."""


class InMemorySessionStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.handoffs: dict[str, dict[str, Any]] = {}

    async def save(self, data: dict[str, Any]) -> None:
        self.items[data["session_id"]] = json.loads(json.dumps(data, default=str))

    async def load(self, session_id: str, now: float | None = None) -> dict[str, Any]:
        data = self.items.get(session_id)
        current = time.time() if now is None else now
        if data is None or is_expired(data["created_at"], current):
            raise SessionExpired(session_id)
        return json.loads(json.dumps(data))

    async def record_handoff(self, session_id: str, record: dict[str, Any]) -> None:
        self.handoffs.setdefault(session_id, json.loads(json.dumps(record, default=str)))

    async def handoff_state(self, session_id: str) -> str | None:
        return self.handoffs.get(session_id, {}).get("estado")

    def confirm(self, session_id: str) -> None:
        """Lo que hace la API_Caja al confirmar la derivación."""
        self.handoffs[session_id]["estado"] = "confirmada"


class DynamoSessionStore:
    """`SessionStore` sobre `ultra-sesiones` (clave `session_id`, TTL en `ttl`)."""

    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def from_env(cls) -> "DynamoSessionStore":
        import boto3
        from botocore.config import Config

        resource = boto3.resource(
            "dynamodb", config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1})
        )
        return cls(resource.Table(os.environ.get("SESSIONS_TABLE", "ultra-sesiones")))

    async def save(self, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str)

        def write() -> None:
            # La derivación se guarda también como atributo propio: la API_Caja la actualiza (`handoff.estado`).
            item: dict[str, Any] = {
                "session_id": data["session_id"],
                "created_at": int(data["created_at"]),
                "ttl": int(data["ttl"]),
                "estado": body,
            }
            self._table.update_item(
                Key={"session_id": item["session_id"]},
                UpdateExpression="SET created_at = if_not_exists(created_at, :c), #t = :t, estado = :e",
                ExpressionAttributeNames={"#t": "ttl"},
                ExpressionAttributeValues={":c": item["created_at"], ":t": item["ttl"], ":e": item["estado"]},
            )

        await asyncio.to_thread(write)

    async def load(self, session_id: str, now: float | None = None) -> dict[str, Any]:
        def read() -> dict[str, Any] | None:
            return self._table.get_item(Key={"session_id": session_id}).get("Item")

        item = await asyncio.to_thread(read)
        current = time.time() if now is None else now
        if item is None or "estado" not in item or is_expired(float(item["created_at"]), current):
            raise SessionExpired(session_id)
        data = json.loads(item["estado"])
        data["created_at"] = int(item["created_at"])
        return data

    async def record_handoff(self, session_id: str, record: dict[str, Any]) -> None:
        """`SET handoff = if_not_exists(...)`: una confirmación de la Caja no se sobrescribe con `pendiente`."""
        clean = json.loads(json.dumps(record, ensure_ascii=False, default=str))
        now = int(time.time())

        def write() -> None:
            self._table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET handoff = if_not_exists(handoff, :h), created_at = if_not_exists(created_at, :c), "
                "#t = if_not_exists(#t, :t)",
                ExpressionAttributeNames={"#t": "ttl"},
                ExpressionAttributeValues={":h": clean, ":c": now, ":t": now + SESSION_TTL_S},
            )

        await asyncio.to_thread(write)

    async def handoff_state(self, session_id: str) -> str | None:
        def read() -> str | None:
            item = self._table.get_item(Key={"session_id": session_id}, ProjectionExpression="handoff").get("Item")
            return (item or {}).get("handoff", {}).get("estado")

        return await asyncio.to_thread(read)

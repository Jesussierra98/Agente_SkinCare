"""Estado de una sesión de voz y utilidades de derivación."""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from .language import LanguageTracker
from .models import Product
from .ports import HandoffNotifier
from .profile import ProfileState

log = logging.getLogger("advisor.session")

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # sin I ni O
_DIGITS = "23456789"


def generate_codigo_corto() -> str:
    """Código `LLL-DDD` (3 letras sin I/O y 3 dígitos del 2 al 9)."""
    letters = "".join(secrets.choice(_LETTERS) for _ in range(3))
    digits = "".join(secrets.choice(_DIGITS) for _ in range(3))
    return f"{letters}-{digits}"


def now_iso() -> str:
    """ISO 8601 UTC con sufijo `Z`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Session:
    emit: Emit
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    profile: ProfileState = field(default_factory=ProfileState)
    language: LanguageTracker = field(default_factory=LanguageTracker)
    candidates: dict[str, list[Product]] = field(default_factory=dict)
    routine: list[dict[str, Any]] | None = None
    saved: dict[str, Any] | None = None
    recommendations_suspended: bool = False
    handoff: dict[str, Any] | None = None
    tools_running: int = 0  # herramientas largas en ejecución (el vigilante no interviene)
    rejected_skus: set[str] = field(default_factory=set)  # productos que el cliente no quiso (no se repiten)
    avoided_brands: set[str] = field(default_factory=set)  # marcas que el cliente no quiere (plegadas: minúsculas, sin acentos)
    adjustments: int = 0
    level: str | None = None  # nivel de precio vigente de la guía: "$", "$$" o "$$$"
    total_cap: Decimal | None = None  # tope del total que dijo el cliente (persiste entre ajustes)
    product_cap: Decimal | None = None  # tope por producto que dijo el cliente en un ajuste
    routine_change: str = "inicial"  # "inicial" | "ajuste": qué cambió la última vez
    routine_at: float | None = None  # instante (monotónico) en que se armó la rutina

    def new_rec_id(self) -> str:
        return str(uuid.uuid4())


async def start_handoff(
    session: Session, notifier: HandoffNotifier, motivo: str, notify_timeout_s: float = 10.0
) -> dict[str, Any]:
    """Registra la derivación, avisa al asesor y suspende recomendaciones si corresponde.

    `condicion_sensible` y `requiere_asesor` suspenden las recomendaciones durante el resto
    de la sesión. Si el aviso no se completa en `notify_timeout_s`, también se suspenden.
    """
    if session.handoff and session.handoff.get("motivo") == motivo:
        return session.handoff
    perfil = session.profile.to_dict()
    record = {"motivo": motivo, "estado": "pendiente", "perfil": perfil, "fecha": now_iso()}
    session.handoff = record
    if motivo in {"condicion_sensible", "requiere_asesor"}:
        session.recommendations_suspended = True

    notified = True
    try:
        await asyncio.wait_for(notifier.notify(session.session_id, motivo, perfil), timeout=notify_timeout_s)
    except Exception as exc:  # noqa: BLE001
        notified = False
        session.recommendations_suspended = True
        record["estado"] = "sin_notificar"
        log.error("no se pudo notificar la derivación: %s", exc)

    record["notificado"] = notified
    await session.emit({"type": "handoff", "motivo": motivo, "estado": record["estado"]})
    return record


def price_tier_bounds(raw: str) -> tuple[Decimal, Decimal]:
    """Lee `BUDGET_TIER_BOUNDS_MXN` (`800,2000`). Valores inválidos usan 800 y 2000."""
    try:
        a, b = (Decimal(x.strip()) for x in raw.split(","))
        if 0 < a < b:
            return a, b
    except Exception:  # noqa: BLE001
        pass
    log.warning("BUDGET_TIER_BOUNDS_MXN inválido (%r); usando 800,2000", raw)
    return Decimal("800"), Decimal("2000")

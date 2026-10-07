"""Lógica pura de la API_Caja (sin I/O). Req. 13.10, 15.1, 15.2, 15.8, 15.9 y 21.4."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal, Mapping

CAJA_GROUP = "caja"
_UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_CODIGO = re.compile(r"^[A-Z]{3}-\d{3}$")
_UUID_ANY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

IdKind = Literal["rec_id", "codigo_corto"]


def classify_id(raw: Any) -> tuple[IdKind, str] | None:
    """Tipo y valor normalizado del identificador, o `None` si el formato no es válido.

    - UUID v4 (sin importar mayúsculas) → `rec_id`, en minúsculas.
    - Tras `strip().upper()`, `LLL-DDD` → `codigo_corto`. El patrón es más amplio que el alfabeto de generación a
      propósito: un código bien formado pero inexistente responde 404, no 400.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if _UUID_V4.match(text.lower()):
        return "rec_id", text.lower()
    code = text.upper()
    if _CODIGO.match(code):
        return "codigo_corto", code
    return None


def is_session_id(raw: Any) -> bool:
    """`session_id` con forma de UUID."""
    return isinstance(raw, str) and _UUID_ANY.match(raw.strip().lower()) is not None


def groups_of(claims: Mapping[str, Any] | None) -> set[str]:
    """Grupos del token. El Authorizer HTTP puede entregarlos como lista, `"[a b]"` o `"a,b"`."""
    raw = (claims or {}).get("cognito:groups")
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(g).strip() for g in raw if str(g).strip()}
    text = str(raw).strip().strip("[]")
    return {part for part in re.split(r"[\s,]+", text) if part}


def is_cashier(claims: Mapping[str, Any] | None) -> bool:
    """Solo el grupo exacto `caja` puede operar sobre recomendaciones."""
    return CAJA_GROUP in groups_of(claims)


def cashier_id(claims: Mapping[str, Any] | None) -> str:
    """Identificador del cajero: `username` (o `cognito:username`) y, si falta, `sub`."""
    claims = claims or {}
    for key in ("username", "cognito:username", "sub"):
        value = claims.get(key)
        if value:
            return str(value)
    return "desconocido"


def price_str(value: Any) -> str:
    """Dinero como cadena decimal con 2 decimales (`"1234.50"`), venga como `Decimal`, `int` o `str`."""
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):f}"


def build_response(item: Mapping[str, Any]) -> dict[str, Any]:
    """Respuesta de `GET /recomendacion/{id}` a partir del registro guardado (snapshot, no el catálogo)."""
    steps = sorted(item.get("rutina", []), key=lambda step: int(step["paso"]))
    products = [
        {
            "paso": int(step["paso"]),
            "sku": step["sku"],
            "nombre": step["nombre"],
            "marca": step["marca"],
            "precio": price_str(step["precio"]),
            "imagen_url": step.get("imagen_url", ""),
        }
        for step in steps
    ]
    total = sum((Decimal(p["precio"]) for p in products), Decimal("0"))
    return {
        "rec_id": item["rec_id"],
        "codigo_corto": item["codigo_corto"],
        "fecha_creacion": item["fecha_creacion"],
        "estado": str(item.get("estado", "pendiente")).upper(),
        "fecha_atendida": item.get("fecha_atendida"),
        "productos": products,
        "total_sugerido": price_str(total),
    }

"""Validación de la rutina antes de guardarla (Herramienta_Guardar, pura). Req. 13.5, 13.6, 23.2, 23.7 y 23.9."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_PRICE = Decimal("999999.99")
TEXT_FIELDS = ("sku", "nombre", "marca", "imagen_url", "razon_catalogo", "modo_uso")
PASOS_VALIDOS = (1, 2, 3, 4)


def _price_problem(value: Any) -> bool:
    """`True` si el precio no es numérico, está fuera de 0 a 999,999.99 o trae más de 2 decimales."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str, float)):
        return True
    try:
        price = Decimal(str(value).strip())
    except InvalidOperation:
        return True
    if not price.is_finite() or price < 0 or price > MAX_PRICE:
        return True
    return price != price.quantize(Decimal("0.01"))


def validate_routine_for_save(routine: Any) -> list[str]:
    """Campos inválidos de la rutina; una lista vacía significa que se puede guardar.

    Exige exactamente 4 objetos, cada uno con `paso` entero de 1 a 4 (sin repetidos), los campos de texto como `str`
    y `precio` numérico entre 0 y 999,999.99 con máximo 2 decimales. No modifica la rutina.
    """
    if not isinstance(routine, (list, tuple)) or len(routine) != 4:
        return ["rutina:se_esperan_4_pasos"]
    problems: list[str] = []
    seen: set[int] = set()
    for index, step in enumerate(routine):
        if not isinstance(step, dict):
            problems.append(f"paso[{index}]:no_es_objeto")
            continue
        paso = step.get("paso")
        if isinstance(paso, bool) or not isinstance(paso, int) or paso not in PASOS_VALIDOS:
            problems.append(f"paso[{index}].paso")
        elif paso in seen:
            problems.append(f"paso[{index}].paso_repetido")
        else:
            seen.add(paso)
        for name in TEXT_FIELDS:
            if not isinstance(step.get(name), str):
                problems.append(f"paso[{index}].{name}")
        if _price_problem(step.get("precio")):
            problems.append(f"paso[{index}].precio")
    return problems

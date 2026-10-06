"""Fusión de filas duplicadas del PIM.

El export trae productos repetidos: una fila "padre" con el SKU duplicado (`000291493-000291493`), que
tiene `Funcion`, tipo de piel y textos pero NO precio, y una fila "hija" con el SKU normal, que tiene
precio e inventario pero a veces no `Funcion`. Se fusionan en una sola fila con el SKU normal.
"""

from __future__ import annotations

import re
from typing import Mapping

_DOUBLE_SKU = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def base_sku(raw: str) -> str:
    """`000291493-000291493` → `000291493`. Cualquier otro valor se devuelve recortado."""
    m = _DOUBLE_SKU.match(raw or "")
    if m and m.group(1) == m.group(2):
        return m.group(1)
    return (raw or "").strip()


def merge_rows(
    rows: list[dict[str, str]], headers: Mapping[str, str]
) -> tuple[list[dict[str, str]], int]:
    """Devuelve `(filas_fusionadas, cuántas_fusiones)`.

    Para cada SKU base, la fila principal es la que tiene precio (y SKU sin duplicar); a sus campos
    vacíos se les copia el valor de las otras filas del mismo SKU.
    """
    sku_col = headers["sku"]
    price_col = headers.get("precio")
    groups: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for row in rows:
        key = base_sku(row.get(sku_col, ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    merged: list[dict[str, str]] = []
    merges = 0
    for key in order:
        group = groups[key]
        primary = max(
            group,
            key=lambda r: (
                bool(price_col and (r.get(price_col) or "").strip()),
                not _DOUBLE_SKU.match(r.get(sku_col, "") or ""),
            ),
        )
        result = dict(primary)
        result[sku_col] = key
        for other in group:
            if other is primary:
                continue
            merges += 1
            for column, value in other.items():
                if column != sku_col and not (result.get(column) or "").strip() and (value or "").strip():
                    result[column] = value
        merged.append(result)
    return merged, merges

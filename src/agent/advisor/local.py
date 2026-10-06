"""Implementaciones locales (prototipo): catálogo en archivo JSON y datos en memoria."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import Product

log = logging.getLogger("advisor.local")


def load_catalog(path: Path) -> dict[str, Product]:
    """Lee un catálogo JSON. El SKU se conserva como texto (ceros a la izquierda)."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    products: dict[str, Product] = {}
    for row in rows:
        p = Product(
            sku=str(row["sku"]),
            nombre=row["nombre"],
            marca=row["marca"],
            paso_rutina=row["paso_rutina"],
            tipo_piel=row.get("tipo_piel", "Todo tipo de piel"),
            precio=Decimal(str(row["precio"])),
            beneficios=row.get("beneficios", ""),
            ingredientes=row.get("ingredientes", ""),
            modo_uso=row.get("modo_uso", ""),
            imagen_url=row.get("imagen_url", ""),
            detalle=row.get("detalle", ""),
        )
        products[p.sku] = p
    return products


class JsonCatalog:
    """`CatalogRepository` sobre un archivo JSON cargado en memoria."""

    def __init__(self, path: Path) -> None:
        self._products = load_catalog(path)
        log.info("catálogo de muestra cargado: %d productos", len(self._products))

    async def search(self, paso: str, consulta: str | None = None) -> list[Product]:
        return [p for p in self._products.values() if p.paso_rutina == paso]

    async def get_many(self, skus: list[str]) -> dict[str, Product]:
        return {s: self._products[s] for s in skus if s in self._products}


class InMemoryRecommendations:
    """`RecommendationStore` en memoria."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def code_exists(self, codigo_corto: str) -> bool:
        return any(r["codigo_corto"] == codigo_corto for r in self.items.values())

    async def put(self, recommendation: dict[str, Any]) -> None:
        rec_id = recommendation["rec_id"]
        if rec_id in self.items:
            raise ValueError("rec_id duplicado")
        self.items[rec_id] = recommendation
        log.info("recomendación guardada: %s (%s)", recommendation["codigo_corto"], rec_id)


class LogNotifier:
    """`HandoffNotifier` que solo registra la derivación en el log."""

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        log.warning("DERIVACIÓN a asesor humano | sesión=%s motivo=%s perfil=%s", session_id, motivo, perfil)

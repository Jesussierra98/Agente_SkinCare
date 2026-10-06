"""Contratos de acceso a datos y servicios.

El agente y las herramientas solo dependen de estas interfaces. El prototipo usa las
implementaciones locales (`local.py`); la versión de producción las reemplaza por
DynamoDB, la Knowledge Base de Bedrock y SNS sin tocar el agente.
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import Product


class CatalogRepository(Protocol):
    async def search(self, paso: str, consulta: str | None = None) -> list[Product]:
        """Productos del paso dado, sin ordenar."""

    async def get_many(self, skus: list[str]) -> dict[str, Product]:
        """Productos existentes para los SKUs dados (los inexistentes se omiten)."""


class RecommendationStore(Protocol):
    async def code_exists(self, codigo_corto: str) -> bool: ...

    async def put(self, recommendation: dict[str, Any]) -> None:
        """Guarda una recomendación completa. Debe fallar si el `rec_id` ya existe."""


class HandoffNotifier(Protocol):
    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        """Avisa al asesor humano. Lanza una excepción si no se pudo notificar."""

"""`CatalogRepository` de producción: Knowledge Base de Bedrock (búsqueda) + `ultra-productos` (datos).

La Knowledge Base solo decide QUÉ productos son relevantes; nombre, marca, precio y demás campos se leen
siempre de DynamoDB (`BatchGetItem`), que es la fuente de verdad (Req. 8.1). El SKU se toma de
`location.s3Location.uri` (`s3://bucket/productos/{sku}.md`), nunca del texto recuperado.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from decimal import Decimal
from typing import Any

from .models import Product

log = logging.getLogger("advisor.kb")

RETRIEVE_TIMEOUT_S = 5.0
RESULTS_PER_SEARCH = 25
BATCH_SIZE = 100
MAX_UNPROCESSED_RETRIES = 3

_SKU_FROM_URI = re.compile(r"/productos/([^/]+)\.md$")


def sku_from_uri(uri: str) -> str | None:
    """`s3://kb/productos/000375947.md` → `000375947`. Cualquier otra clave devuelve `None`."""
    match = _SKU_FROM_URI.search(uri or "")
    return match.group(1) if match else None


def product_from_item(item: dict[str, Any]) -> Product:
    """Registro de `ultra-productos` → `Product`. El SKU se conserva como texto."""
    inventory = item.get("inventario")
    return Product(
        sku=str(item["sku"]),
        nombre=item.get("nombre", ""),
        marca=item.get("marca", ""),
        paso_rutina=item.get("paso_rutina", ""),
        tipo_piel=item.get("tipo_piel", "Todo tipo de piel"),
        precio=Decimal(str(item["precio"])),
        beneficios=item.get("beneficios", ""),
        ingredientes=item.get("ingredientes", ""),
        modo_uso=item.get("modo_uso", ""),
        imagen_url=item.get("imagen_url", ""),
        detalle=item.get("detalle", ""),
        funcion_original=item.get("funcion_original", ""),
        tipo_producto=item.get("tipo_producto", ""),
        sublinea=item.get("sublinea", ""),
        coleccion=item.get("coleccion", ""),
        genero=item.get("genero", ""),
        inventario=None if inventory in (None, "") else int(inventory),
    )


class KnowledgeBaseCatalog:
    """Búsqueda semántica con filtro por `paso_rutina` + lectura de productos en DynamoDB."""

    def __init__(
        self,
        *,
        knowledge_base_id: str,
        products_table: str,
        agent_runtime: Any,
        dynamodb_client: Any,
        retrieve_timeout_s: float = RETRIEVE_TIMEOUT_S,
    ) -> None:
        self._kb_id = knowledge_base_id
        self._table = products_table
        self._agent = agent_runtime
        self._ddb = dynamodb_client
        self._timeout_s = retrieve_timeout_s

    @classmethod
    def from_env(cls, knowledge_base_id: str, products_table: str, region: str) -> "KnowledgeBaseCatalog":
        import boto3
        from botocore.config import Config

        cfg = Config(connect_timeout=1, read_timeout=5, retries={"max_attempts": 1})
        return cls(
            knowledge_base_id=knowledge_base_id,
            products_table=products_table,
            agent_runtime=boto3.client("bedrock-agent-runtime", region_name=region, config=cfg),
            dynamodb_client=boto3.client("dynamodb", region_name=region, config=cfg),
        )

    # ---- CatalogRepository -----------------------------------------------------------------------
    async def search(self, paso: str, consulta: str | None = None) -> list[Product]:
        """Productos del paso dado, en el orden de relevancia de la KB. Lanza si la KB o DynamoDB fallan."""

        def retrieve() -> dict[str, Any]:
            return self._agent.retrieve(
                knowledgeBaseId=self._kb_id,
                retrievalQuery={"text": (consulta or paso)[:1000]},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": RESULTS_PER_SEARCH,
                        "filter": {"equals": {"key": "paso_rutina", "value": paso}},
                    }
                },
            )

        response = await asyncio.wait_for(asyncio.to_thread(retrieve), timeout=self._timeout_s)
        skus: list[str] = []
        for result in response.get("retrievalResults", []):
            sku = sku_from_uri(result.get("location", {}).get("s3Location", {}).get("uri", ""))
            if sku and sku not in skus:
                skus.append(sku)
        products = await self.get_many(skus)
        # Solo productos de ese paso (defensa en profundidad: el filtro ya lo garantiza) y en orden de relevancia.
        return [products[s] for s in skus if s in products and products[s].paso_rutina == paso]

    async def get_many(self, skus: list[str]) -> dict[str, Product]:
        """`BatchGetItem` en bloques de 100 con reintento de claves no procesadas."""
        unique = list(dict.fromkeys(skus))
        found: dict[str, Product] = {}
        for start in range(0, len(unique), BATCH_SIZE):
            chunk = unique[start : start + BATCH_SIZE]
            for item in await asyncio.to_thread(self._batch_get, chunk):
                product = product_from_item(item)
                found[product.sku] = product
        return found

    def _batch_get(self, skus: list[str]) -> list[dict[str, Any]]:
        from boto3.dynamodb.types import TypeDeserializer

        deserialize = TypeDeserializer().deserialize
        request: dict[str, Any] = {self._table: {"Keys": [{"sku": {"S": s}} for s in skus]}}
        items: list[dict[str, Any]] = []
        for attempt in range(MAX_UNPROCESSED_RETRIES + 1):
            response = self._ddb.batch_get_item(RequestItems=request)
            for raw in response.get("Responses", {}).get(self._table, []):
                items.append({k: deserialize(v) for k, v in raw.items()})
            request = response.get("UnprocessedKeys") or {}
            if not request:
                return items
            log.warning("BatchGetItem con claves sin procesar (intento %d)", attempt + 1)
        raise RuntimeError("BatchGetItem dejó claves sin procesar")


def catalog_from_env(kb_id: str, products_table: str | None = None, region: str = "us-east-1") -> KnowledgeBaseCatalog:
    return KnowledgeBaseCatalog.from_env(kb_id, products_table or os.environ.get("PRODUCTS_TABLE", "ultra-productos"), region)

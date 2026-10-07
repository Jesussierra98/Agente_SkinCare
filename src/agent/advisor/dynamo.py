"""Implementación de producción de `RecommendationStore` sobre DynamoDB (`ultra-recomendaciones`).

Misma interfaz que `InMemoryRecommendations`: el agente y las herramientas no cambian al reemplazarla.
Las llamadas de boto3 son síncronas, así que se ejecutan en un hilo para no bloquear el bucle de eventos.
"""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

log = logging.getLogger("advisor.dynamo")

GSI_NAME = "codigo_corto-index"


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _with_decimal_prices(rutina: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DynamoDB no acepta `float` y el precio se guarda como número: `"1234.50"` → `Decimal("1234.50")`."""
    return [{**step, "precio": Decimal(str(step["precio"]))} for step in rutina]


class DynamoRecommendations:
    """`RecommendationStore` sobre una tabla `Table` de boto3."""

    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def from_env(cls) -> "DynamoRecommendations":
        """Tabla `RECOMMENDATIONS_TABLE` (por defecto `ultra-recomendaciones`) con timeouts cortos (Req. 13.4)."""
        import boto3
        from botocore.config import Config

        resource = boto3.resource(
            "dynamodb", config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1})
        )
        return cls(resource.Table(os.environ.get("RECOMMENDATIONS_TABLE", "ultra-recomendaciones")))

    # ---- RecommendationStore ---------------------------------------------------------------------------
    async def code_exists(self, codigo_corto: str) -> bool:
        from boto3.dynamodb.conditions import Key

        def query() -> bool:
            response = self._table.query(
                IndexName=GSI_NAME, KeyConditionExpression=Key("codigo_corto").eq(codigo_corto), Limit=1
            )
            return bool(response.get("Items"))

        return await asyncio.to_thread(query)

    async def put(self, recommendation: dict[str, Any]) -> None:
        """`PutItem` condicionado a que el `rec_id` no exista. Falla con `ValueError` si ya existe."""
        item = {**recommendation, "rutina": _with_decimal_prices(recommendation["rutina"])}

        def write() -> None:
            try:
                self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(rec_id)")
            except ClientError as exc:
                if _is_conditional_failure(exc):
                    raise ValueError("rec_id duplicado") from exc
                raise

        await asyncio.to_thread(write)
        log.info("recomendación guardada: %s (%s)", recommendation["codigo_corto"], recommendation["rec_id"])

    async def update_routine(self, rec_id: str, rutina: list[dict[str, Any]], fecha: str) -> None:
        """Reemplaza la rutina mientras siga `pendiente`. `KeyError` si no existe, `ValueError` si ya se atendió."""
        values = {":r": _with_decimal_prices(rutina), ":f": fecha, ":uno": 1, ":pendiente": "pendiente"}

        def write() -> None:
            try:
                self._table.update_item(
                    Key={"rec_id": rec_id},
                    UpdateExpression="SET rutina = :r, fecha_actualizacion = :f, version = if_not_exists(version, :uno) + :uno",
                    ConditionExpression="attribute_exists(rec_id) AND #e = :pendiente",
                    ExpressionAttributeNames={"#e": "estado"},
                    ExpressionAttributeValues=values,
                )
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
                if self._table.get_item(Key={"rec_id": rec_id}).get("Item") is None:
                    raise KeyError("recomendación inexistente") from exc
                raise ValueError("la recomendación ya no está pendiente") from exc

        await asyncio.to_thread(write)

    async def set_readings(self, rec_id: str, readings: list[dict[str, Any]]) -> None:
        """Agrega `lecturas_pubmed` a una recomendación existente (segunda escritura; Req. 17.8)."""

        def write() -> None:
            self._table.update_item(
                Key={"rec_id": rec_id},
                UpdateExpression="SET lecturas_pubmed = :l",
                ConditionExpression="attribute_exists(rec_id)",
                ExpressionAttributeValues={":l": readings},
            )

        await asyncio.to_thread(write)

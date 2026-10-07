"""`ultra-caja-lambda`: API_Caja detrás de un HTTP API con JWT Authorizer de Cognito. Req. 13.12, 14.7, 15, 21.4.

Rutas:
  GET  /recomendacion/{id}                   consulta por `rec_id` (UUID v4) o `codigo_corto`
  POST /recomendacion/{id}/atendida          marca como atendida (idempotente)
  POST /derivaciones/{session_id}/confirmar  el asesor confirma una derivación

Variables de entorno: RECOMMENDATIONS_TABLE (ultra-recomendaciones), SESSIONS_TABLE (ultra-sesiones).
La ausencia o invalidez del JWT la resuelve el Authorizer con 401 sin invocar esta función.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from caja_core import build_response, cashier_id, classify_id, is_cashier, is_session_id

log = logging.getLogger("caja_api")
log.setLevel(logging.INFO)

GSI_NAME = "codigo_corto-index"


def _reply(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class CajaApi:
    """Rutas de la API con las tablas inyectadas (recursos `Table` de boto3)."""

    def __init__(self, recommendations: Any, sessions: Any, now: Any = _now_iso) -> None:
        self._recs = recommendations
        self._sessions = sessions
        self._now = now

    # ---- acceso a datos --------------------------------------------------------------------------------
    def _find(self, kind: str, value: str) -> dict[str, Any] | None:
        if kind == "rec_id":
            return self._recs.get_item(Key={"rec_id": value}).get("Item")
        from boto3.dynamodb.conditions import Key

        items = self._recs.query(IndexName=GSI_NAME, KeyConditionExpression=Key("codigo_corto").eq(value)).get("Items", [])
        return items[0] if items else None

    # ---- rutas -----------------------------------------------------------------------------------------
    def get_recommendation(self, raw_id: str) -> dict[str, Any]:
        parsed = classify_id(raw_id)
        if parsed is None:
            return _reply(400, {"error": "codigo_invalido"})
        item = self._find(*parsed)
        if item is None:
            return _reply(404, {"error": "no_encontrada"})
        return _reply(200, build_response(item))

    def mark_attended(self, raw_id: str, claims: dict[str, Any]) -> dict[str, Any]:
        parsed = classify_id(raw_id)
        if parsed is None:
            return _reply(400, {"error": "codigo_invalido"})
        item = self._find(*parsed)
        if item is None:
            return _reply(404, {"error": "no_encontrada"})
        try:
            updated = self._recs.update_item(
                Key={"rec_id": item["rec_id"]},
                UpdateExpression="SET #e = :atendida, fecha_atendida = :fecha, cajero_id = :cajero",
                ConditionExpression="#e = :pendiente",
                ExpressionAttributeNames={"#e": "estado"},
                ExpressionAttributeValues={
                    ":atendida": "atendida",
                    ":pendiente": "pendiente",
                    ":fecha": self._now(),
                    ":cajero": cashier_id(claims),
                },
                ReturnValues="ALL_NEW",
            )
            return _reply(200, build_response(updated["Attributes"]))
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        # Ya estaba atendida: idempotente, se devuelven los valores originales sin cambiarlos.
        current = self._recs.get_item(Key={"rec_id": item["rec_id"]}).get("Item")
        return _reply(200, build_response(current or item))

    def confirm_handoff(self, session_id: str) -> dict[str, Any]:
        if not is_session_id(session_id):
            return _reply(400, {"error": "sesion_invalida"})
        key = session_id.strip().lower()
        try:
            self._sessions.update_item(
                Key={"session_id": key},
                UpdateExpression="SET handoff.#e = :confirmada",
                ConditionExpression="attribute_exists(handoff)",
                ExpressionAttributeNames={"#e": "estado"},
                ExpressionAttributeValues={":confirmada": "confirmada"},
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return _reply(404, {"error": "no_encontrada"})
            raise
        return _reply(200, {"session_id": key, "estado": "confirmada"})

    # ---- entrada ---------------------------------------------------------------------------------------
    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
        if not is_cashier(claims):
            return _reply(403, {"error": "prohibido"})
        route = event.get("routeKey", "")
        params = event.get("pathParameters") or {}
        try:
            if route == "GET /recomendacion/{id}":
                return self.get_recommendation(params.get("id", ""))
            if route == "POST /recomendacion/{id}/atendida":
                return self.mark_attended(params.get("id", ""), claims)
            if route == "POST /derivaciones/{session_id}/confirmar":
                return self.confirm_handoff(params.get("session_id", ""))
        except ClientError as exc:
            log.error("error de DynamoDB en %s: %s", route, exc)
            return _reply(500, {"error": "error_interno"})
        except Exception:  # noqa: BLE001 - nunca filtrar detalles internos al cliente
            log.exception("error inesperado en %s", route)
            return _reply(500, {"error": "error_interno"})
        return _reply(404, {"error": "ruta_desconocida"})


_api: CajaApi | None = None


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    global _api
    if _api is None:
        import boto3

        dynamodb = boto3.resource("dynamodb")
        _api = CajaApi(
            dynamodb.Table(os.environ.get("RECOMMENDATIONS_TABLE", "ultra-recomendaciones")),
            dynamodb.Table(os.environ.get("SESSIONS_TABLE", "ultra-sesiones")),
        )
    return _api.handle(event)

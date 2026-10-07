"""Punto de entrada para AgentCore Runtime (puerto 8080, ARM64).

AgentCore valida el JWT de Cognito (Inbound Auth, `allowedClients` de KioscoClient) antes de dejar pasar el
handshake: una conexión sin token válido nunca llega a este código ni crea registros (Req. 21.12). El
identificador de sesión llega en `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` (cabecera o parámetro de consulta).

Variables de entorno obligatorias en producción: PRODUCTION=true, GUARDRAIL_ID, GUARDRAIL_VERSION, KB_ID,
HANDOFF_TOPIC_ARN. Ver `advisor/config.py` para el resto.
"""

from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from runtime import build_runtime
from ws_handler import handle_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent")

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"

rt = build_runtime()
app = BedrockAgentCoreApp()


def _session_id(websocket) -> str | None:  # type: ignore[no-untyped-def]
    """Identificador de sesión de la conexión (cabecera o parámetro de consulta), o `None`."""
    value = websocket.headers.get(SESSION_HEADER) or websocket.query_params.get(SESSION_HEADER)
    return value or websocket.query_params.get("session_id")


@app.websocket
async def ws(websocket, context):  # type: ignore[no-untyped-def]  # noqa: ARG001
    await websocket.accept()
    await handle_session(websocket, rt, _session_id(websocket))


if __name__ == "__main__":
    app.run(port=8080)

"""Servidor local del agente de voz (desarrollo).

WebSocket `ws://localhost:8080/ws` entre el Kiosco y Nova 2 Sonic (vía Strands BidiAgent).
SIN autenticación: solo para desarrollo local. No exponer a la red. En producción se usa `agent.py`
(AgentCore Runtime), que valida el JWT de Cognito antes de aceptar la conexión.

Ejecutar:  python -m uvicorn server:app --app-dir src/agent --host 127.0.0.1 --port 8080
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, WebSocket

from runtime import build_runtime
from ws_handler import handle_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

rt = build_runtime()
app = FastAPI(title="Skincare voice advisor (local)")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model": rt.cfg.nova_model_id,
        "voice": rt.cfg.voice,
        "guardrail": rt.gate_enabled,
        "production": rt.production,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    requested = ws.query_params.get("session_id")
    await handle_session(ws, rt, requested)

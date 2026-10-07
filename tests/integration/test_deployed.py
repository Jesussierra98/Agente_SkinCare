"""Pruebas de integración contra la infraestructura DESPLEGADA (tarea 14.1). Se omiten si no hay entorno.

Requieren un despliegue real (`scripts/deploy.ps1`) y credenciales AWS. Variables:
  SKINCARE_INTEGRATION=1        habilita estas pruebas
  RAW_BUCKET                    bucket feeder-skincare-catalog-<cuenta>
  PRODUCTS_TABLE                por defecto ultra-productos
  SESSIONS_TABLE                por defecto ultra-sesiones
  API_URL                       salida ApiUrl del stack compute
  AGENT_WS_URL                  wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/<arn>/ws?qualifier=DEFAULT
  CAJA_TOKEN / KIOSCO_TOKEN     access tokens vigentes de un usuario del grupo caja y uno del grupo kiosco
  EXPIRED_TOKEN                 un access token ya vencido (opcional)
  SAMPLE_CSV                    CSV pequeño para la prueba de la ETL (por defecto catalog/sample_feeder.csv)

Ejecutar:  pytest tests/integration -m integration -s
La prueba de 15 minutos (Req. 24.1) es lenta; se activa además con SKINCARE_LONG=1.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SKINCARE_INTEGRATION") != "1", reason="SKINCARE_INTEGRATION=1 no está definido"),
]


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"falta la variable {name}")
    return value


def http(method: str, url: str, token: str | None = None) -> int:
    request = urllib.request.Request(url, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


# ---- ETL (Req. 1.8, 4.4) ---------------------------------------------------------------------------------------------------

def test_la_etl_empieza_en_60_s_y_termina_en_300_s() -> None:
    import boto3

    bucket = need("RAW_BUCKET")
    table = boto3.resource("dynamodb").Table(os.environ.get("PRODUCTS_TABLE", "ultra-productos"))
    logs = boto3.client("logs")
    csv_path = Path(os.environ.get("SAMPLE_CSV", ROOT / "catalog" / "sample_feeder.csv"))
    key = f"raw/integration-{uuid.uuid4().hex[:8]}.csv"
    s3 = boto3.client("s3")

    started_at = time.time()
    s3.put_object(Bucket=bucket, Key=key, Body=csv_path.read_bytes())

    first_log: float | None = None
    normalized = f"normalized/{Path(key).name}"
    deadline = started_at + 300
    done = False
    while time.time() < deadline and not done:
        time.sleep(3)
        if first_log is None:
            events = logs.filter_log_events(
                logGroupName="/aws/lambda/ultra-etl-lambda", startTime=int(started_at * 1000), filterPattern=f'"{Path(key).name}"'
            )["events"]
            if events:
                first_log = events[0]["timestamp"] / 1000
        try:
            s3.head_object(Bucket=bucket, Key=normalized)
            done = True
        except s3.exceptions.ClientError:
            continue
    assert first_log is not None and first_log - started_at <= 60, "la ETL no empezó en 60 s"
    assert done, "la ETL no terminó en 300 s"
    assert table.scan(Limit=1)["Items"], "ultra-productos quedó vacía"


# ---- API_Caja (Req. 21.5, 22.12) ----------------------------------------------------------------------------------------------

def test_api_sin_token_o_con_token_malformado_responde_401() -> None:
    url = f"{need('API_URL').rstrip('/')}/recomendacion/ABC-234"
    assert http("GET", url) == 401
    assert http("GET", url, token="esto-no-es-un-jwt") == 401


def test_api_con_token_vencido_responde_401() -> None:
    url = f"{need('API_URL').rstrip('/')}/recomendacion/ABC-234"
    assert http("GET", url, token=need("EXPIRED_TOKEN")) == 401


def test_api_con_token_del_kiosco_responde_403_y_con_el_de_caja_404_o_200() -> None:
    url = f"{need('API_URL').rstrip('/')}/recomendacion/ZZZ-999"
    assert http("GET", url, token=need("KIOSCO_TOKEN")) == 403
    assert http("GET", url, token=need("CAJA_TOKEN")) == 404  # autenticado, pero el código no existe


# ---- WebSocket del agente (Req. 21.2, 21.12) --------------------------------------------------------------------------------------

def _connect(url: str, token: str | None):
    import websockets

    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from smoke_test import SESSION_PARAM, bearer_subprotocols  # type: ignore[import-not-found]

    session_id = str(uuid.uuid4())
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query[SESSION_PARAM] = session_id
    full = urlunparse(parts._replace(query=urlencode(query)))
    return session_id, websockets.connect(full, subprotocols=bearer_subprotocols(token) if token else None, max_size=None)


def test_un_handshake_sin_token_se_rechaza_y_no_crea_registros() -> None:
    import boto3

    url = need("AGENT_WS_URL")
    session_id, connection = _connect(url, token=None)

    async def attempt() -> bool:
        try:
            async with connection as ws:
                await asyncio.wait_for(ws.recv(), timeout=10)
            return True
        except Exception:  # noqa: BLE001 - cualquier rechazo del handshake es lo esperado
            return False

    assert asyncio.run(attempt()) is False
    table = boto3.resource("dynamodb").Table(os.environ.get("SESSIONS_TABLE", "ultra-sesiones"))
    assert "Item" not in table.get_item(Key={"session_id": session_id})


def test_handshake_con_token_del_kiosco_entrega_session_ready() -> None:
    url, token = need("AGENT_WS_URL"), need("KIOSCO_TOKEN")
    _, connection = _connect(url, token)

    async def attempt() -> dict:
        async with connection as ws:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if msg.get("type") == "session_ready":
                    return msg

    assert asyncio.run(attempt())["type"] == "session_ready"


@pytest.mark.skipif(os.environ.get("SKINCARE_LONG") != "1", reason="prueba de 15 minutos: SKINCARE_LONG=1")
def test_sesion_de_15_minutos_con_al_menos_una_renovacion() -> None:
    import base64

    url, token = need("AGENT_WS_URL"), need("KIOSCO_TOKEN")
    silence = base64.b64encode(b"\x00" * 3200).decode()
    _, connection = _connect(url, token)

    async def hold() -> tuple[int, list[str]]:
        audio_frames_after_restart = 0
        errors: list[str] = []
        async with connection as ws:

            async def pump() -> None:
                while True:
                    await ws.send(json.dumps({"type": "audio", "data": silence}))
                    await asyncio.sleep(0.1)

            async def talk() -> None:
                # Una frase cada 2 minutos mantiene la conversación viva más allá de la renovación (restart_after_s = 420).
                for i in range(7):
                    await asyncio.sleep(120)
                    await ws.send(json.dumps({"type": "text", "text": f"Cuéntame algo sobre cuidar la piel, parte {i + 1}"}))

            tasks = [asyncio.create_task(pump()), asyncio.create_task(talk())]
            started = time.time()
            try:
                async with asyncio.timeout(15 * 60):
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "connection_error":
                            errors.append(msg.get("reason", "?"))
                        if msg.get("type") == "audio" and time.time() - started > 8 * 60:
                            audio_frames_after_restart += 1
            except TimeoutError:
                pass
            finally:
                for task in tasks:
                    task.cancel()
        return audio_frames_after_restart, errors

    frames, errors = asyncio.run(hold())
    assert errors == [], f"la renovación falló: {errors}"
    assert frames > 0, "no hubo audio después de la primera renovación"

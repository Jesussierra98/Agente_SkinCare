"""Prueba de humo del Runtime_Agente desplegado (o del servidor local).

Comprueba, en este orden, y dice cuál falló (`handshake`, `inglés` o `español`):
  1. handshake WebSocket y `session_ready` en <= 10 s;
  2. un mensaje en inglés recibe una respuesta no vacía, en inglés, en <= 30 s;
  3. un mensaje en español recibe una respuesta no vacía, en español, en <= 30 s.

Uso (local, sin autenticación):
  python scripts/smoke_test.py --url ws://127.0.0.1:8080/ws

Uso (AgentCore): el JWT va en el subprotocolo `base64UrlBearerAuthorization.<token>` (DD-02).
  python scripts/smoke_test.py --url "wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/<arn-codificado>/ws?qualifier=DEFAULT" \
      --client-id <KioscoClient> --username <usuario> --password <contraseña>
  (o bien SMOKE_TOKEN=<access token> en el entorno)

Código de salida: 0 todo bien; 1 falló alguna validación; 2 uso incorrecto.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

import websockets  # noqa: E402

from advisor.language import detect  # noqa: E402

SESSION_PARAM = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
HANDSHAKE_TIMEOUT_S = 10
RESPONSE_TIMEOUT_S = 30
SILENCE = base64.b64encode(b"\x00" * 3200).decode()  # 100 ms de silencio PCM16 a 16 kHz

PROMPTS = {
    "inglés": ("Hello, I have dry skin and I would like a moisturizer", "en"),
    "español": ("Hola, tengo la piel seca y quiero una crema hidratante", "es"),
}


def bearer_subprotocols(token: str) -> list[str]:
    """`["base64UrlBearerAuthorization.<token-base64url>", "base64UrlBearerAuthorization"]` (DD-02)."""
    encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
    return [f"base64UrlBearerAuthorization.{encoded}", "base64UrlBearerAuthorization"]


def with_session_id(url: str, session_id: str) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.setdefault(SESSION_PARAM, session_id)
    return urlunparse(parts._replace(query=urlencode(query)))


def cognito_token(client_id: str, username: str, password: str) -> str:
    import boto3

    client = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    result = client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return result["AuthenticationResult"]["AccessToken"]


async def next_advisor_text(ws: websockets.ClientConnection, timeout: float) -> str:
    """Primer texto final del asesor que llegue (ignora audio y parciales)."""
    async with asyncio.timeout(timeout):
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "transcript" and msg.get("role") == "asesor" and msg.get("final"):
                return str(msg.get("text", "")).strip()
    return ""


async def run(url: str, token: str | None) -> str | None:
    """Devuelve el nombre de la validación que falló, o `None` si todo pasó."""
    subprotocols = bearer_subprotocols(token) if token else None
    try:
        async with asyncio.timeout(HANDSHAKE_TIMEOUT_S):
            ws = await websockets.connect(with_session_id(url, str(uuid.uuid4())), subprotocols=subprotocols, max_size=None)
            first = json.loads(await ws.recv())
            while first.get("type") != "session_ready":
                first = json.loads(await ws.recv())
    except Exception as exc:  # noqa: BLE001
        print(f"handshake: {type(exc).__name__}: {exc}")
        return "handshake"
    print("handshake: OK (session_ready)")

    async def keep_audio_open() -> None:
        while True:
            await ws.send(json.dumps({"type": "audio", "data": SILENCE}))
            await asyncio.sleep(0.1)

    pump = asyncio.create_task(keep_audio_open())
    try:
        try:
            await next_advisor_text(ws, RESPONSE_TIMEOUT_S)  # saludo inicial
        except TimeoutError:
            pass  # si no hubo saludo, las validaciones de abajo lo dirán
        for name, (text, lang) in PROMPTS.items():
            await ws.send(json.dumps({"type": "text", "text": text}))
            try:
                reply = await next_advisor_text(ws, RESPONSE_TIMEOUT_S)
            except TimeoutError:
                print(f"{name}: sin respuesta en {RESPONSE_TIMEOUT_S} s")
                return name
            if not reply or detect(reply) != lang:
                print(f"{name}: respuesta vacía o en otro idioma: {reply[:120]!r}")
                return name
            print(f"{name}: OK ({reply[:80]!r})")
    finally:
        pump.cancel()
        try:
            await ws.send(json.dumps({"type": "hangup"}))
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="ws:// o wss:// del endpoint /ws")
    parser.add_argument("--client-id", help="ID del app client de Cognito (KioscoClient)")
    parser.add_argument("--username")
    parser.add_argument("--password")
    args = parser.parse_args(argv)

    token = os.environ.get("SMOKE_TOKEN")
    if not token and args.client_id:
        if not (args.username and args.password):
            print("--client-id requiere --username y --password")
            return 2
        token = cognito_token(args.client_id, args.username, args.password)
    if args.url.startswith("wss://") and not token:
        print("un endpoint wss:// de AgentCore exige token: usa SMOKE_TOKEN o --client-id/--username/--password")
        return 2

    failed = asyncio.run(run(args.url, token))
    if failed:
        print(f"\nFALLÓ la validación: {failed}")
        return 1
    print("\nTodas las validaciones pasaron")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))

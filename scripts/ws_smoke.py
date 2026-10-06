"""Cliente de prueba del agente local: abre /ws, mantiene audio en silencio y muestra los eventos.

Uso:  python scripts/ws_smoke.py [segundos] [texto_del_cliente ...]
Los textos se envían como mensajes escritos (uno cada ~12 s) para probar el flujo sin micrófono.
"""

import asyncio
import base64
import json
import sys

import websockets

URL = "ws://127.0.0.1:8080/ws"
SILENCE = base64.b64encode(b"\x00" * 3200).decode()  # 100 ms PCM16 16 kHz


async def main(seconds: float, texts: list[str]) -> None:
    audio_bytes = 0
    async with websockets.connect(URL, max_size=None) as ws:

        async def pump() -> None:
            while True:
                await ws.send(json.dumps({"type": "audio", "data": SILENCE}))
                await asyncio.sleep(0.1)

        async def talk() -> None:
            await asyncio.sleep(12)
            for text in texts:
                print(f">>> cliente escribe: {text}")
                await ws.send(json.dumps({"type": "text", "text": text}))
                await asyncio.sleep(14)

        tasks = [asyncio.create_task(pump()), asyncio.create_task(talk())]
        try:
            async with asyncio.timeout(seconds):
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "audio":
                        audio_bytes += len(msg["data"]) * 3 // 4
                        continue
                    if kind == "transcript" and not msg.get("final"):
                        continue
                    print(json.dumps(msg, ensure_ascii=False)[:400])
        except TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await ws.send(json.dumps({"type": "hangup"}))
    print(f"-- fin. audio recibido ≈ {audio_bytes} bytes")


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    asyncio.run(main(secs, sys.argv[2:]))

"""Mide la renovación de la conexión con Nova 2 Sonic (spike 7.17, Req. 24.9 y 24.10).

Mantiene una sesión abierta enviando audio de silencio y un mensaje de texto cada `--every` segundos, y registra:
  - cada `session_ready` / `connection_error` recibido;
  - el hueco más largo sin ningún evento del servidor (audio o transcripción) alrededor de cada renovación.

Para no esperar 7 minutos, arranca el agente con una renovación corta:
  $env:NOVA_RESTART_AFTER_S = "45"
  python -m uvicorn server:app --app-dir src/agent --port 8081
  python scripts/measure_restart.py --url ws://127.0.0.1:8081/ws --seconds 240

Código de salida: 1 si hubo `connection_error`, 0 en caso contrario. El hueco se informa; el umbral de 2 s se juzga en el piloto.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time

import websockets

SILENCE = base64.b64encode(b"\x00" * 3200).decode()
PROMPTS = [
    "Cuéntame en dos frases por qué es importante limpiar la piel.",
    "Y ahora dime en dos frases para qué sirve un protector solar.",
    "Dame un consejo corto sobre hidratación.",
    "Explícame en una frase qué es una rutina de cuatro pasos.",
]


async def main(url: str, seconds: float, every: float) -> int:
    t0 = time.monotonic()
    stamps: list[tuple[float, str]] = []
    errors: list[str] = []
    ready = 0
    async with websockets.connect(url, max_size=None) as ws:

        async def pump() -> None:
            while True:
                await ws.send(json.dumps({"type": "audio", "data": SILENCE}))
                await asyncio.sleep(0.1)

        async def talk() -> None:
            await asyncio.sleep(10)
            i = 0
            while True:
                await ws.send(json.dumps({"type": "text", "text": PROMPTS[i % len(PROMPTS)] + " (Responde en español.)"}))
                i += 1
                await asyncio.sleep(every)

        tasks = [asyncio.create_task(pump()), asyncio.create_task(talk())]
        try:
            async with asyncio.timeout(seconds):
                async for raw in ws:
                    msg = json.loads(raw)
                    now = time.monotonic() - t0
                    kind = msg.get("type")
                    if kind == "session_ready":
                        ready += 1
                        print(f"[{now:6.1f}s] session_ready")
                    elif kind == "connection_error":
                        errors.append(msg.get("reason", "?"))
                        print(f"[{now:6.1f}s] connection_error: {msg.get('reason')}")
                    elif kind in {"audio", "transcript"}:
                        stamps.append((now, kind))
        except TimeoutError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            try:
                await ws.send(json.dumps({"type": "hangup"}))
            except Exception:  # noqa: BLE001
                pass

    gaps = sorted(((b[0] - a[0], a[0]) for a, b in zip(stamps, stamps[1:])), reverse=True)[:5]
    print(f"\nsession_ready recibidos: {ready} | connection_error: {errors or 'ninguno'}")
    print("Mayores huecos entre eventos del servidor (s, inicio en s): " + ", ".join(f"{g:.1f}@{s:.0f}" for g, s in gaps))
    print("Nota: un hueco largo también es silencio normal entre turnos; compara con los instantes de renovación del log del agente.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="ws://127.0.0.1:8081/ws")
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--every", type=float, default=25)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.url, args.seconds, args.every)))

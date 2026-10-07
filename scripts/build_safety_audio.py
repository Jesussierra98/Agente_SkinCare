"""Genera los mensajes de seguridad pregrabados ES/EN con la misma voz de Nova 2 Sonic (DD-08).

Uso:  python scripts/build_safety_audio.py [--voice tiffany] [--only clave,clave] [--force]

Escribe `src/agent/audio/{clave}_{es|en}.pcm` (PCM16 mono a 24 kHz) y `manifest.json`. Los textos salen de
`advisor/safety_audio.py`. Requiere credenciales AWS y acceso a Nova 2 Sonic en us-east-1.
Los audios generados se versionan con el código para que estén disponibles aunque Nova esté caído.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

from strands.bidi import BidiAgent  # noqa: E402
from strands.bidi.models import BedrockNovaSonicModel  # noqa: E402
from strands.bidi.types.events import BidiAudioDeltaEvent, BidiResponseStopEvent  # noqa: E402

from advisor.safety_audio import AUDIO_DIR, LANGS, TEXTS  # noqa: E402

SAMPLE_RATE_IN = 16000
SAMPLE_RATE_OUT = 24000
CHUNK_MS = 100
PROMPT = (
    "You are a text-to-speech reader. When you receive a message that starts with READ:, say exactly the text after it, "
    "word for word, in the language it is written in. Add nothing before or after and do not answer it."
)


def silence() -> dict:
    return {"audio_delta": {"format": "pcm", "source": {"bytes": b"\x00" * (SAMPLE_RATE_IN * CHUNK_MS // 1000 * 2)}}}


async def synthesize(text: str, voice: str) -> bytes:
    model = BedrockNovaSonicModel(
        model_id="amazon.nova-2-sonic-v1:0",
        region="us-east-1",
        voice=voice,
        audio={"input": {"sample_rate": SAMPLE_RATE_IN}, "output": {"sample_rate": SAMPLE_RATE_OUT}},
        params={"turnDetectionConfiguration": {"endpointingSensitivity": "MEDIUM"}},
    )
    agent = BidiAgent(model=model, system_prompt=PROMPT)
    pcm = bytearray()

    async def keep_open() -> None:
        while True:
            await agent.send(silence())
            await asyncio.sleep(CHUNK_MS / 1000)

    async with agent:
        pump = asyncio.create_task(keep_open())
        try:
            await asyncio.sleep(1.0)
            await agent.send(f"READ: {text}")
            async with asyncio.timeout(40):
                async for event in agent.receive():
                    if isinstance(event, BidiAudioDeltaEvent):
                        pcm.extend(base64.b64decode(event.audio))
                    elif isinstance(event, BidiResponseStopEvent):
                        break
        finally:
            pump.cancel()
    return bytes(pcm)


async def main(voice: str, only: set[str] | None, force: bool) -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for key, by_lang in TEXTS.items():
        if only and key not in only:
            continue
        for lang in LANGS:
            target = AUDIO_DIR / f"{key}_{lang}.pcm"
            if target.exists() and not force:
                print(f"= {target.name} ya existe (usa --force para regenerarlo)")
                continue
            try:
                pcm = await synthesize(by_lang[lang], voice)
            except Exception as exc:  # noqa: BLE001
                print(f"! {target.name}: {type(exc).__name__}: {exc}")
                failures += 1
                continue
            if not pcm:
                print(f"! {target.name}: Nova no devolvió audio")
                failures += 1
                continue
            target.write_bytes(pcm)
            print(f"+ {target.name}: {len(pcm) / (SAMPLE_RATE_OUT * 2):.1f} s  «{by_lang[lang]}»")
    (AUDIO_DIR / "manifest.json").write_text(
        json.dumps({"sample_rate": SAMPLE_RATE_OUT, "voice": voice, "format": "pcm_s16le_mono"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="tiffany", choices=["tiffany", "matthew"])
    parser.add_argument("--only", help="claves separadas por coma (p. ej. blocked,soon)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    only = {k.strip() for k in args.only.split(",")} if args.only else None
    raise SystemExit(asyncio.run(main(args.voice, only, args.force)))

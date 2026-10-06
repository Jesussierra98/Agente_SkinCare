"""Prueba mínima de conexión con Nova Sonic: abre el canal de audio (silencio), envía un texto
y espera la respuesta hablada.

Uso:  python scripts/smoke_nova.py [model_id] [voice]
"""

import asyncio
import sys

from strands.bidi import BidiAgent
from strands.bidi.models import BedrockNovaSonicModel
from strands.bidi.types.events import (
    BidiAudioDeltaEvent,
    BidiResponseStopEvent,
    BidiTranscriptBlockEvent,
)

SAMPLE_RATE = 16000
CHUNK_MS = 100


def silence_chunk() -> dict:
    size = SAMPLE_RATE * CHUNK_MS // 1000 * 2  # PCM16 mono
    return {"audio_delta": {"format": "pcm", "source": {"bytes": b"\x00" * size}}}


async def keep_audio_open(agent: BidiAgent) -> None:
    while True:
        await agent.send(silence_chunk())
        await asyncio.sleep(CHUNK_MS / 1000)


async def main(model_id: str, voice: str) -> None:
    model = BedrockNovaSonicModel(
        model_id=model_id,
        region="us-east-1",
        voice=voice,
        audio={"input": {"sample_rate": SAMPLE_RATE}, "output": {"sample_rate": 24000}},
        params={"turnDetectionConfiguration": {"endpointingSensitivity": "MEDIUM"}},
    )
    agent = BidiAgent(model=model, system_prompt="Eres un asesor de skincare. Responde en una frase corta.")
    audio_bytes = 0
    async with agent:
        pump = asyncio.create_task(keep_audio_open(agent))
        try:
            await asyncio.sleep(1.0)
            await agent.send("Hola, saluda en español en una frase.")
            async with asyncio.timeout(40):
                async for event in agent.receive():
                    if isinstance(event, BidiAudioDeltaEvent):
                        audio_bytes += len(event.audio) * 3 // 4  # base64 → bytes aprox.
                    elif isinstance(event, BidiTranscriptBlockEvent):
                        print(f"[{event.role}] {event.transcript}")
                    elif isinstance(event, BidiResponseStopEvent):
                        break
        finally:
            pump.cancel()
    print(f"OK model={model_id} voice={voice} audio_bytes~={audio_bytes}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "amazon.nova-2-sonic-v1:0"
    voice = sys.argv[2] if len(sys.argv) > 2 else "tiffany"
    asyncio.run(main(model, voice))

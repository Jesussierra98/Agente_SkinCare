"""Mensajes de seguridad pregrabados (DD-08): suenan en <= 2 s aunque Nova 2 Sonic o el Guardrail fallen.

Cada mensaje tiene su texto en español e inglés (<= 200 caracteres, sin repetir lo que se bloqueó) y, si ya se
generó con `scripts/build_safety_audio.py`, un archivo PCM16 mono con la misma voz. Si falta el audio, el
servidor envía solo el texto para que el Kiosco lo muestre y la conversación siga.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("advisor.safety_audio")

MAX_TEXT_CHARS = 200
AUDIO_DIR = Path(__file__).resolve().parents[1] / "audio"

TEXTS: dict[str, dict[str, str]] = {
    # El Guardrail bloqueó el turno: no se repite ni se resume lo que se dijo.
    "blocked": {
        "es": "Eso es algo que prefiero que te atienda un asesor de la tienda. Puedes acercarte al mostrador de asesoría.",
        "en": "I'd rather have a store advisor help you with that. You can go to the advice counter.",
    },
    "handoff": {
        "es": "Voy a avisar a un asesor de la tienda para que te atienda con gusto.",
        "en": "I'm letting a store advisor know so they can help you.",
    },
    "no_hear": {
        "es": "No te escucho. ¿Puedes repetirlo, por favor?",
        "en": "I can't hear you. Could you say that again, please?",
    },
    "ending": {
        "es": "La conversación va a terminar en unos segundos.",
        "en": "The conversation is about to end in a few seconds.",
    },
    "counter": {
        "es": "Por favor acude al mostrador de asesoría en piso.",
        "en": "Please go to the advice counter on the floor.",
    },
    "soon": {
        "es": "Un asesor lo atenderá en breve.",
        "en": "An advisor will be with you shortly.",
    },
}
LANGS = ("es", "en")


def text_for(key: str, lang: str) -> str:
    """Texto del mensaje en el idioma dado (cualquier idioma distinto de `en` usa español)."""
    return TEXTS[key]["en" if lang == "en" else "es"]


class SafetyAudio:
    """Audios pregrabados en una carpeta: `{clave}_{idioma}.pcm` y `manifest.json` con la frecuencia."""

    def __init__(self, directory: Path = AUDIO_DIR, expected_rate: int = 24000) -> None:
        self._dir = directory
        self.sample_rate = self._read_rate()
        self._usable = self.sample_rate == expected_rate
        if self.sample_rate and not self._usable:
            log.warning(
                "los audios pregrabados son de %s Hz y la salida es de %s Hz: se enviará solo el texto",
                self.sample_rate, expected_rate,
            )

    def _read_rate(self) -> int:
        manifest = self._dir / "manifest.json"
        try:
            return int(json.loads(manifest.read_text(encoding="utf-8"))["sample_rate"])
        except (OSError, ValueError, KeyError):
            return 0

    def get(self, key: str, lang: str) -> bytes | None:
        """PCM16 del mensaje, o `None` si no existe (o su frecuencia no coincide con la salida)."""
        if not self._usable:
            return None
        path = self._dir / f"{key}_{'en' if lang == 'en' else 'es'}.pcm"
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return data or None

    def missing(self) -> list[str]:
        """Archivos que faltan por generar."""
        return [f"{k}_{lang}.pcm" for k in TEXTS for lang in LANGS if not (self._dir / f"{k}_{lang}.pcm").is_file()]

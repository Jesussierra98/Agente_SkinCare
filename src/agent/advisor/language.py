"""Detección del idioma vigente de la conversación (puro)."""

from __future__ import annotations

import re

_ES = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "qué", "y", "en", "es", "con", "para",
    "por", "mi", "mis", "tengo", "quiero", "busco", "piel", "hola", "gracias", "me", "se", "no", "si",
    "sí", "muy", "más", "pero", "como", "cómo", "está", "estoy", "necesito", "puedes", "por", "favor",
    "rutina", "completa", "seca", "grasa", "mixta", "día", "noche",
}
_EN = {
    "the", "a", "an", "of", "and", "in", "is", "with", "for", "my", "i", "have", "want", "looking",
    "skin", "hello", "hi", "thanks", "thank", "me", "it", "not", "yes", "very", "more", "but", "how",
    "am", "need", "can", "you", "please", "routine", "full", "dry", "oily", "combination", "day", "night",
    "what", "do", "to", "that", "this", "would", "like",
}
_WORD = re.compile(r"[a-záéíóúñü']+", re.IGNORECASE)


def detect(text: str) -> str | None:
    """`'es'`, `'en'` o `None` si el texto no permite decidir."""
    words = [w.lower() for w in _WORD.findall(text)]
    es = sum(1 for w in words if w in _ES)
    en = sum(1 for w in words if w in _EN)
    # Acentos y ñ son indicio fuerte de español.
    if re.search(r"[áéíóúñ¿¡]", text.lower()):
        es += 2
    if es == en:
        return None
    return "es" if es > en else "en"


class LanguageTracker:
    """Idioma vigente: el del último turno claro del cliente; en empate conserva el anterior."""

    def __init__(self, initial: str = "es") -> None:
        self.current = initial

    def update(self, text: str) -> str:
        found = detect(text)
        if found is not None:
            self.current = found
        return self.current

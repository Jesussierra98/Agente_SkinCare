"""Detector de condiciones sensibles y de temas que requieren un asesor humano (puro)."""

from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    """Minúsculas y sin acentos."""
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# Condiciones sensibles (Req. 9.1): se prefieren falsos positivos a falsos negativos.
SENSITIVE_TERMS: tuple[str, ...] = (
    "embaraz", "estoy esperando bebe", "lactanc", "amamant",
    "pregnan", "breastfeed", "nursing",
    "alergia", "alergic", "reaccion alergica", "me da alergia",
    "allergy", "allergic", "allergies",
    "acne quistico", "acne severo", "acne cistico", "cystic acne", "severe acne",
    "herida", "lastimad", "sangr", "pus ", " pus", "infeccion", "infectad",
    "wound", "bleeding", "infection", "infected",
    "psoriasis", "dermatitis", "rosacea", "eczema", "eccema", "melanoma", "lunar sospechos",
    "quemadura", "burn ",
)

# Solicitud de diagnóstico / tratamiento clínico (Req. 9.2).
DIAGNOSIS_TERMS: tuple[str, ...] = (
    "diagnostic", "que enfermedad", "que tengo en la piel", "es cancer", "es grave",
    "diagnose", "diagnosis", "what disease", "is it cancer",
    "tratamiento medico", "medicamento", "receta medica", "medication", "prescription",
)

# Compatibilidad química / mezclar activos (Req. 9.3).
COMPATIBILITY_TERMS: tuple[str, ...] = (
    "puedo mezclar", "se pueden mezclar", "es seguro mezclar", "mezclar retinol", "mezclar acidos",
    "combinar retinol", "combinar acidos", "es compatible", "son compatibles", "compatibilidad",
    "can i mix", "can i combine", "is it safe to mix", "safe to combine", "compatible with",
    "interactu", "interact with",
)


def detect(text: str) -> str | None:
    """Devuelve el motivo de derivación si el texto lo requiere, o `None`.

    Motivos: `condicion_sensible`, `diagnostico`, `compatibilidad`.
    """
    t = f" {_norm(text)} "
    if any(term in t for term in SENSITIVE_TERMS):
        return "condicion_sensible"
    if any(term in t for term in DIAGNOSIS_TERMS):
        return "diagnostico"
    if any(term in t for term in COMPATIBILITY_TERMS):
        return "compatibilidad"
    return None


_WORD = re.compile(r"[a-záéíóúñü']+")

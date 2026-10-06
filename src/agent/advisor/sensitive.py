"""Detector de condiciones sensibles y de temas que requieren un asesor humano (puro)."""

from __future__ import annotations

import unicodedata


def _norm(text: str) -> str:
    """Minúsculas y sin acentos."""
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# Condiciones sensibles (Req. 9.1): embarazo, lactancia, heridas y afecciones de la piel. Se prefieren
# falsos positivos a falsos negativos. La alergia va aparte (ver ALLERGY_TERMS).
SENSITIVE_TERMS: tuple[str, ...] = (
    "embaraz", "estoy esperando bebe", "lactanc", "amamant",
    "pregnan", "breastfeed", "nursing",
    "acne quistico", "acne severo", "acne cistico", "cystic acne", "severe acne",
    "herida", "lastimad", "sangr", "pus ", " pus", "infeccion", "infectad",
    "wound", "bleeding", "infection", "infected",
    "psoriasis", "dermatitis", "rosacea", "eczema", "eccema", "melanoma", "lunar sospechos",
    "quemadura", "burn ",
)

# Alergia mencionada: solo es una condición grave si viene con señales de gravedad; si no, suele ser
# "esa marca o ingrediente me da alergia" y el cliente quiere otra opción.
ALLERGY_TERMS: tuple[str, ...] = ("alergia", "alergic", "allerg")
SEVERITY_TERMS: tuple[str, ...] = (
    "severa", "grave", "fuerte", "hinch", "ronch", "urticaria", "anafil", "respirar", "inflam", "ampolla", "dolor",
    "severe", "serious", "swell", "hive", "breath", "blister", "pain",
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


def detect_with_term(text: str) -> tuple[str, str] | None:
    """`(motivo, término)` si el texto requiere derivación, o `None`.

    Motivos: `condicion_sensible` (suspende las recomendaciones), `alergia_producto` (avisa al asesor pero
    el cliente puede seguir cambiando de producto), `diagnostico` y `compatibilidad`.
    """
    t = f" {_norm(text)} "
    for term in SENSITIVE_TERMS:
        if term in t:
            return "condicion_sensible", term.strip()
    allergy = next((term for term in ALLERGY_TERMS if term in t), None)
    if allergy:
        severe = next((s for s in SEVERITY_TERMS if s in t), None)
        return ("condicion_sensible", f"{allergy}+{severe}") if severe else ("alergia_producto", allergy)
    for term in DIAGNOSIS_TERMS:
        if term in t:
            return "diagnostico", term
    for term in COMPATIBILITY_TERMS:
        if term in t:
            return "compatibilidad", term
    return None


def detect(text: str) -> str | None:
    """Solo el motivo de derivación (o `None`)."""
    found = detect_with_term(text)
    return found[0] if found else None

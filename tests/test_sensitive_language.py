"""Detector de condiciones sensibles (Req. 9) e idioma vigente (Req. 5.4)."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from advisor import sensitive
from advisor.language import LanguageTracker, detect as detect_language
from advisor.sensitive import (
    ALLERGY_TERMS,
    COMPATIBILITY_TERMS,
    DIAGNOSIS_TERMS,
    SENSITIVE_TERMS,
    detect,
    detect_with_term,
)


# ---- Feature: skincare-voice-advisor, Property 22: el detector reconoce todo el léxico ---------------

@pytest.mark.parametrize("term", SENSITIVE_TERMS)
def test_cada_termino_sensible_se_detecta_en_cualquier_capitalizacion(term: str) -> None:
    for text in (f"hola {term} adios", f"HOLA {term.upper()} ADIOS", f"Hola {term.title()}"):
        assert detect(text) == "condicion_sensible", text


@pytest.mark.parametrize("term", DIAGNOSIS_TERMS)
def test_cada_termino_de_diagnostico_se_detecta(term: str) -> None:
    assert detect(f"oye {term} por favor") == "diagnostico"


@pytest.mark.parametrize("term", COMPATIBILITY_TERMS)
def test_cada_termino_de_compatibilidad_se_detecta(term: str) -> None:
    assert detect(f"una duda: {term} con otro producto") == "compatibilidad"


@pytest.mark.parametrize(
    "text",
    [
        "Estoy EMBARAZADA",
        "estoy embarazada",
        "tengo rosácea en las mejillas",
        "TENGO ROSACEA",
        "I am pregnant",
        "I'm breastfeeding",
        "tengo dermatitis atópica",
        "me salió una herida",
    ],
)
def test_frases_sensibles_con_y_sin_acentos(text: str) -> None:
    assert detect(text) == "condicion_sensible"


def test_sin_acentos_y_con_acentos_dan_el_mismo_resultado() -> None:
    assert detect("tengo acné quístico") == detect("tengo acne quistico") == "condicion_sensible"
    assert detect("¿puedo mezclar retinol con ácidos?") == "compatibilidad"
    assert detect("puedo mezclar retinol con acidos") == "compatibilidad"


@pytest.mark.parametrize(
    "text",
    [
        "Tengo la piel seca y quiero una rutina completa",
        "I have dry skin and want a full routine",
        "busco algo con vitamina C",
        "",
        "   ",
    ],
)
def test_texto_normal_no_deriva(text: str) -> None:
    assert detect(text) is None
    assert detect_with_term(text) is None


# ---- alergia: solo es condición grave con señales de gravedad --------------------------------------

@pytest.mark.parametrize("term", ALLERGY_TERMS)
def test_alergia_sin_gravedad_solo_avisa_al_asesor(term: str) -> None:
    assert detect(f"me da {term} esa marca") == "alergia_producto"


def test_alergia_con_gravedad_es_condicion_sensible() -> None:
    motivo, term = detect_with_term("tengo una alergia severa a esa crema") or ("", "")
    assert motivo == "condicion_sensible"
    assert term.startswith("alergia+")
    assert detect("I had an allergic reaction, my face swelled") == "condicion_sensible"


def test_un_termino_sensible_tiene_prioridad_sobre_la_alergia() -> None:
    assert detect("estoy embarazada y tengo alergia") == "condicion_sensible"


def test_detect_with_term_devuelve_el_termino_sin_espacios_de_borde() -> None:
    assert detect_with_term("mira, hay pus en la zona") == ("condicion_sensible", "pus")


@given(st.text(max_size=200))
def test_detect_nunca_falla_y_devuelve_un_motivo_conocido(text: str) -> None:
    assert detect(text) in {None, "condicion_sensible", "alergia_producto", "diagnostico", "compatibilidad"}


def test_modulo_expone_normalizacion_sin_acentos() -> None:
    assert sensitive._norm("ÁÉÍÓÚ Ñ") == "aeiou n"


# ---- idioma vigente -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hola, tengo la piel seca", "es"),
        ("Quiero una rutina completa", "es"),
        ("Hello, I want a full routine", "en"),
        ("I have dry skin", "en"),
        ("", None),
        ("ok", None),
    ],
)
def test_detect_language(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected


def test_acentos_inclinan_a_espanol() -> None:
    assert detect_language("¿cómo?") == "es"


def test_el_idioma_sigue_al_ultimo_turno_claro() -> None:
    tracker = LanguageTracker()
    assert tracker.current == "es"
    assert tracker.update("Hello, I need help with my skin") == "en"
    assert tracker.update("Ahora quiero hablar en español, por favor") == "es"


def test_en_empate_conserva_el_idioma_anterior() -> None:
    tracker = LanguageTracker(initial="en")
    assert tracker.update("ok") == "en"
    tracker.update("Quiero una rutina")
    assert tracker.update("ok") == "es"


# ---- Feature: skincare-voice-advisor, Property 13: el idioma siempre es es o en ------------------------

@given(st.lists(st.text(max_size=60), max_size=15))
def test_el_idioma_vigente_siempre_es_es_o_en(turns: list[str]) -> None:
    tracker = LanguageTracker()
    for turn in turns:
        previous = tracker.current
        current = tracker.update(turn)
        assert current in {"es", "en"}
        if detect_language(turn) is None:
            assert current == previous
        else:
            assert current == detect_language(turn)

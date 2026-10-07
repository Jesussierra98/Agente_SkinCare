"""Prompts del asesor y temas denegados del Guardrail (Req. 8.2, 8.5, 9, 12.1, 12.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from advisor import prompts
from advisor import routine

TOPICS_PATH = Path(__file__).resolve().parents[1] / "src" / "agent" / "guardrail_topics.json"
TOPICS = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))

REQUIRED_TOPICS = {"consejo_medico", "diagnostico", "compatibilidad_quimica"}


def test_el_guardrail_define_los_tres_temas_denegados() -> None:
    assert {t["name"] for t in TOPICS["topics"]} == REQUIRED_TOPICS
    assert all(t["type"] == "DENY" for t in TOPICS["topics"])


@pytest.mark.parametrize("topic", TOPICS["topics"], ids=lambda t: t["name"])
def test_los_temas_respetan_los_limites_de_bedrock(topic: dict) -> None:
    assert len(topic["name"]) <= 100
    assert 0 < len(topic["definition"]) <= 200
    assert 1 <= len(topic["examples"]) <= 100
    assert all(0 < len(e) <= 100 for e in topic["examples"])


@pytest.mark.parametrize("topic", TOPICS["topics"], ids=lambda t: t["name"])
def test_cada_tema_tiene_ejemplos_en_espanol_y_en_ingles(topic: dict) -> None:
    joined = " ".join(topic["examples"])
    assert "¿" in joined
    assert any(w in joined.lower() for w in ("what", "is it", "can you", "will these", "is this"))


def test_los_mensajes_de_bloqueo_no_estan_vacios() -> None:
    assert TOPICS["blockedInputMessaging"].strip()
    assert TOPICS["blockedOutputsMessaging"].strip()


def test_el_prompt_del_asesor_trae_la_frase_textual_en_espanol_e_ingles() -> None:
    assert "Esa información no está disponible en nuestro catálogo, te sugiero consultarlo con un asesor de la tienda" in prompts.SYSTEM_PROMPT
    assert "That information isn't available in our catalog, I suggest asking a store advisor" in prompts.SYSTEM_PROMPT


def test_el_prompt_cubre_idioma_catalogo_cerrado_y_seguridad() -> None:
    text = prompts.SYSTEM_PROMPT
    for fragment in ("español o inglés", "CATÁLOGO CERRADO", "no eres médico", "derivar_asesor", "recomendacion_suspendida"):
        assert fragment in text


def test_el_prompt_de_pubmed_prohibe_leer_las_lecturas_y_solo_se_agrega_si_hay_consultor() -> None:
    text = prompts.PUBMED_PROMPT
    assert "evidencia_ingrediente" in text and "NUNCA leas en voz alta" in text and "lecturas_en_pantalla=false" in text
    assert "evidencia_ingrediente" not in prompts.SYSTEM_PROMPT  # sin consultor, el modelo no conoce la herramienta


@pytest.mark.parametrize("motivo", ["condicion_sensible", "alergia_producto", "diagnostico", "compatibilidad"])
def test_cada_motivo_de_derivacion_tiene_instruccion_en_los_dos_idiomas(motivo: str) -> None:
    assert set(prompts.HANDOFF_INSTRUCTIONS[motivo]) == {"es", "en"}
    assert all(text.strip() for text in prompts.HANDOFF_INSTRUCTIONS[motivo].values())


def test_el_prompt_del_motor_pide_requiere_asesor_ante_alergia_acne_o_embarazo() -> None:
    text = routine.SYSTEM_PROMPT
    assert "requiere_asesor=true" in text
    assert "alergia" in text and "embarazo" in text
    assert "No inventes productos" in text

"""Vigilante de la conversación, utilidades puras de las herramientas y guía del negocio."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from advisor.guide import Guide, p3_answer
from advisor.models import PASOS
from advisor.nudges import MAX_NUDGES, MIN_SECONDS_BETWEEN, PRESENT_AFTER_S, TEXT, decide_nudge
from advisor.session import Session
from advisor.tools import _spell, name_key, parse_pasos, shift_level, step_phrase
from factories import make_product


async def _noop(_: dict[str, Any]) -> None:
    return None


def ready_session() -> Session:
    session = Session(emit=_noop)
    for campo, valor in {"tipo_piel": "seca/tensa", "inquietud": "brotes", "textura": "brillante", "presupuesto": "$"}.items():
        session.profile.apply(campo, valor)
    session.profile.exchange_count = 5
    return session


def nudge(session: Session, **overrides: Any) -> str | None:
    params: dict[str, Any] = {"now": 1000.0, "nudges_sent": 0, "last_nudge_at": None, "spoke_after_routine": False}
    params.update(overrides)
    return decide_nudge(session, **params)


# ---- vigilante ----------------------------------------------------------------------------------------

def test_pide_armar_la_rutina_cuando_hay_datos_y_no_hay_rutina() -> None:
    assert nudge(ready_session()) == "armar"


def test_no_insiste_si_el_perfil_aun_no_esta_listo() -> None:
    session = ready_session()
    session.profile.exchange_count = 1
    assert nudge(session) is None


def test_no_interviene_con_recomendaciones_suspendidas_ni_herramientas_en_curso() -> None:
    suspended = ready_session()
    suspended.recommendations_suspended = True
    assert nudge(suspended) is None
    busy = ready_session()
    busy.tools_running = 1
    assert nudge(busy) is None


def test_respeta_el_maximo_y_el_intervalo_entre_instrucciones() -> None:
    assert nudge(ready_session(), nudges_sent=MAX_NUDGES) is None
    assert nudge(ready_session(), last_nudge_at=1000.0 - (MIN_SECONDS_BETWEEN - 1)) is None
    assert nudge(ready_session(), last_nudge_at=1000.0 - MIN_SECONDS_BETWEEN) == "armar"


def test_pide_presentar_la_rutina_si_el_asesor_no_hablo_tras_mostrarla() -> None:
    session = ready_session()
    session.routine = [{"paso": 1}]
    session.routine_at = 1000.0 - PRESENT_AFTER_S
    assert nudge(session) == "presentar"
    assert nudge(session, spoke_after_routine=True) is None


def test_espera_unos_segundos_antes_de_pedir_que_presente() -> None:
    session = ready_session()
    session.routine = [{"paso": 1}]
    session.routine_at = 1000.0 - (PRESENT_AFTER_S - 1)
    assert nudge(session) is None


def test_tras_un_ajuste_pide_contar_el_cambio() -> None:
    session = ready_session()
    session.routine = [{"paso": 1}]
    session.routine_at = 0.0
    session.routine_change = "ajuste"
    assert nudge(session) == "ajuste"


def test_hay_texto_en_ambos_idiomas_para_cada_instruccion() -> None:
    for kind in ("armar", "presentar", "ajuste"):
        assert set(TEXT[kind]) == {"es", "en"}
        assert all(TEXT[kind][lang].strip() for lang in ("es", "en"))


# ---- herramientas (funciones puras) ---------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "auto", "Automático", "  AUTOMATICO ", "xyz", "nada"])
def test_parse_pasos_sin_pedido_claro_elige_automaticamente(text: str) -> None:
    assert parse_pasos(text) is None


@pytest.mark.parametrize("text", ["todos", "todas", "todo", "Toda la rutina", "la rutina completa", "all"])
def test_parse_pasos_todos(text: str) -> None:
    assert parse_pasos(text) == list(PASOS)


def test_parse_pasos_devuelve_los_pasos_en_el_orden_de_la_rutina() -> None:
    assert parse_pasos("hidratación, limpieza") == ["Limpieza", "Hidratación"]
    assert parse_pasos("Protección solar") == ["Protección solar"]
    assert parse_pasos("tratamiento y protector solar") == ["Tratamiento", "Protección solar"]


@pytest.mark.parametrize(
    ("level", "change", "expected"),
    [
        ("$$", "mas_barato", "$"),
        ("$", "mas_barato", "$"),
        ("$$", "mas_premium", "$$$"),
        ("$$$", "mas_premium", "$$$"),
        ("$$", "otro_producto", "$$"),
        ("?", "mas_barato", "$"),
        ("?", "otro_producto", "$$"),
    ],
)
def test_shift_level(level: str, change: str, expected: str) -> None:
    assert shift_level(level, change) == expected


def test_name_key_ignora_numeros_y_tamanos_pero_no_la_marca() -> None:
    a = make_product("1", nombre="Clarifying Lotion 2", marca="CLINIQUE")
    b = make_product("2", nombre="Clarifying Lotion 3", marca="CLINIQUE")
    c = make_product("3", nombre="Sérum C 30 ml", marca="X")
    d = make_product("4", nombre="Serum C 100 ml", marca="X")
    e = make_product("5", nombre="Clarifying Lotion 2", marca="OTRA")
    assert name_key(a) == name_key(b)
    assert name_key(c) == name_key(d)
    assert name_key(a) != name_key(e)


def test_step_phrase_usa_solo_datos_del_catalogo() -> None:
    step = {"paso": 1, "nombre": "Gel", "marca": "ALFA", "razon_catalogo": "Limpia sin resecar."}
    assert step_phrase(step) == "Limpieza: Gel, de ALFA. Limpia sin resecar."
    assert step_phrase(step | {"razon_catalogo": "  "}) == "Limpieza: Gel, de ALFA."


def test_spell_dicta_el_codigo_despacio() -> None:
    assert _spell("ABC-234") == "A B C guion 2 3 4"


# ---- guía del negocio -------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("textura", "tipo", "expected"),
    [
        ("muy seca y áspera", "grasa/acneica", "R4"),
        ("brillante", "seca/tensa", "R1"),
        ("marca líneas", "seca/tensa", "R3"),
        ("cómoda pero con ligera resequedad", "grasa/acneica", "R2"),
        ("", "seca/tensa", "R4"),
        ("", "normal/equilibrada", "R2"),
        ("algo raro", "inventado", None),
    ],
)
def test_p3_answer(textura: str, tipo: str, expected: str | None) -> None:
    assert p3_answer(textura, tipo) == expected


GUIDE_DATA = {
    "niveles_precio": {"$": {"desde_mxn": 0}, "$$": {"desde_mxn": 1000}, "$$$": {"desde_mxn": 4000}},
    "respuesta_a_segmento": {"R1": "S1"},
    "segmentos": {"S1": {"nombre": "Control de brotes"}},
    "combinaciones": [
        {
            "P1": "R4",
            "P2": "R1",
            "P3": "R4",
            "niveles": {
                "$": {"limpiador": "l1", "crema": "c1", "extra": "e1"},
                "$$": {"limpiador": "l2", "crema": "c2", "extra": "e2"},
            },
        }
    ],
}
PROFILE = {"tipo_piel": "seca/tensa", "inquietud": "brotes", "textura": "muy seca"}


def test_guia_vacia_no_esta_disponible_y_usa_las_fronteras_por_defecto() -> None:
    guide = Guide({})
    assert not guide.available
    assert guide.bounds() == (Decimal("1300"), Decimal("3500"))
    assert guide.preferred_skus(PROFILE, "$$") == []
    assert guide.segment(PROFILE) is None


def test_fronteras_de_la_guia() -> None:
    assert Guide(GUIDE_DATA).bounds() == (Decimal("1000"), Decimal("4000"))


def test_sugerencia_por_nivel_y_nivel_mas_cercano() -> None:
    guide = Guide(GUIDE_DATA)
    assert guide.available
    assert guide.answer_key(PROFILE) == ("R4", "R1", "R4")
    assert guide.preferred_skus(PROFILE, "$") == ["l1", "c1", "e1"]
    suggestion = guide.suggestion(PROFILE, "$$$")
    assert suggestion is not None and suggestion.level == "$$"
    assert guide.levels_for(PROFILE) == ["$", "$$"]


def test_sin_las_tres_respuestas_no_hay_sugerencia() -> None:
    guide = Guide(GUIDE_DATA)
    assert guide.answer_key({"tipo_piel": "seca/tensa"}) is None
    assert guide.suggestion({"tipo_piel": "seca/tensa"}, "$") is None


def test_productos_curados_de_niveles_vecinos() -> None:
    guide = Guide(GUIDE_DATA)
    assert guide.neighbor_skus(PROFILE, "$$", "abajo") == ["l1", "c1", "e1"]
    assert guide.neighbor_skus(PROFILE, "$$", "arriba") == []
    assert guide.neighbor_skus(PROFILE, "$", "abajo") == []
    assert guide.neighbor_skus(PROFILE, "$$$", "abajo") == []


def test_segmento_segun_la_preocupacion() -> None:
    assert Guide(GUIDE_DATA).segment(PROFILE) == {"nombre": "Control de brotes"}

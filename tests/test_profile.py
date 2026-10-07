"""Perfil del cliente (Req. 7): categorías válidas, reformulación única y momento de proponer."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, strategies as st

from advisor.profile import (
    CAMPOS,
    CATEGORIAS,
    MAX_EXCHANGES,
    MIN_EXCHANGES,
    ProfileState,
    profile_query,
    tier_for_price,
)

VALID = {
    "tipo_piel": "seca/tensa",
    "inquietud": "brotes",
    "textura": "brillante",
    "presupuesto": "$$",
}


def resolved_profile(exchanges: int) -> ProfileState:
    state = ProfileState()
    for campo, valor in VALID.items():
        assert state.apply(campo, valor).accepted
    state.exchange_count = exchanges
    return state


# ---- Feature: skincare-voice-advisor, Property 19: solo categorías válidas y una reformulación -----

@pytest.mark.parametrize("campo", ["tipo_piel", "inquietud", "presupuesto"])
def test_acepta_cada_categoria_valida(campo: str) -> None:
    for categoria in CATEGORIAS[campo]:
        state = ProfileState()
        result = state.apply(campo, categoria)
        assert result.accepted and not result.reformular
        assert state.valores[campo] == categoria


def test_tolera_glosa_del_modelo_pero_guarda_la_categoria_exacta() -> None:
    state = ProfileState()
    assert state.apply("presupuesto", "$$ (premium)").accepted
    assert state.valores["presupuesto"] == "$$"
    assert state.apply("tipo_piel", "Seca/Tensa, con descamación").accepted
    assert state.valores["tipo_piel"] == "seca/tensa"


@pytest.mark.parametrize("valor", ["$$$$", "", "   ", "mucho", "$ y $$$", "$$ o $$$"])
def test_presupuesto_ambiguo_no_se_acepta(valor: str) -> None:
    assert not ProfileState().apply("presupuesto", valor).accepted


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("$ (hasta $1,300)", "$"), ("$$ (entre $1,300 y $3,500)", "$$"), ("$$$ premium", "$$$")],
)
def test_presupuesto_con_glosa_y_precios_no_es_ambiguo(valor: str, esperado: str) -> None:
    state = ProfileState()
    assert state.apply("presupuesto", valor).accepted
    assert state.valores["presupuesto"] == esperado


def test_textura_rechaza_ambiguos_y_enumeraciones() -> None:
    state = ProfileState()
    assert not state.apply("textura", "no sé").accepted
    assert not ProfileState().apply("textura", "brillante, seca, mixta").accepted
    assert not ProfileState().apply("textura", "x" * 61).accepted


def test_campo_desconocido_lanza_error() -> None:
    with pytest.raises(ValueError):
        ProfileState().apply("edad", "30")


@given(campo=st.sampled_from(CAMPOS), valor=st.text(max_size=80))
def test_valor_invalido_reformula_una_vez_y_luego_no_proporcionado(campo: str, valor: str) -> None:
    state = ProfileState()
    first = state.apply(campo, valor)
    assume(not first.accepted)
    second = state.apply(campo, valor)
    assert first.reformular and not first.no_proporcionado
    assert second.no_proporcionado and not second.reformular
    assert campo not in state.valores
    assert campo in state.no_proporcionado


@given(campo=st.sampled_from(CAMPOS), valor=st.text(max_size=80))
def test_un_valor_aceptado_siempre_queda_guardado_y_resuelve_el_campo(campo: str, valor: str) -> None:
    state = ProfileState()
    result = state.apply(campo, valor)
    assume(result.accepted)
    assert campo in state.valores
    assert campo not in state.faltan()


def test_una_correccion_valida_reemplaza_el_valor_y_quita_no_proporcionado() -> None:
    state = ProfileState()
    state.apply("tipo_piel", "???")
    state.apply("tipo_piel", "???")
    assert "tipo_piel" in state.no_proporcionado
    assert state.apply("tipo_piel", "mixta/deshidratada").accepted
    assert state.valores["tipo_piel"] == "mixta/deshidratada"
    assert "tipo_piel" not in state.no_proporcionado


def test_una_correccion_invalida_no_cambia_un_dato_ya_resuelto() -> None:
    state = resolved_profile(0)
    assert not state.apply("tipo_piel", "???").accepted
    assert state.valores["tipo_piel"] == "seca/tensa"


# ---- Feature: skincare-voice-advisor, Property 20: listo para proponer según intercambios -----------

@given(n=st.integers(min_value=0, max_value=30))
def test_con_los_cuatro_datos_propone_desde_el_minimo(n: int) -> None:
    assert resolved_profile(n).listo_para_proponer() == (n >= MIN_EXCHANGES)


@given(n=st.integers(min_value=0, max_value=30), missing=st.sampled_from(CAMPOS))
def test_con_datos_faltantes_solo_propone_al_llegar_al_maximo(n: int, missing: str) -> None:
    state = ProfileState()
    for campo, valor in VALID.items():
        if campo != missing:
            state.apply(campo, valor)
    state.exchange_count = n
    assert state.listo_para_proponer() == (n >= MAX_EXCHANGES)


def test_no_proporcionado_cuenta_como_resuelto() -> None:
    state = ProfileState()
    for campo, valor in VALID.items():
        if campo != "textura":
            state.apply(campo, valor)
    state.apply("textura", "")  # primer intento: reformular
    state.apply("textura", "")  # segundo intento: no_proporcionado
    state.exchange_count = MIN_EXCHANGES
    assert state.resuelto()
    assert state.listo_para_proponer()
    assert state.basado_en_info_parcial()


def test_info_parcial_solo_si_esta_listo_y_falto_algo() -> None:
    assert not resolved_profile(MIN_EXCHANGES).basado_en_info_parcial()
    partial = ProfileState()
    partial.exchange_count = MAX_EXCHANGES
    assert partial.basado_en_info_parcial()
    assert not ProfileState().basado_en_info_parcial()


def test_min_exchanges_configurable() -> None:
    state = resolved_profile(3)
    state.min_exchanges = 3
    assert state.listo_para_proponer()


def test_register_exchange_y_faltan_en_orden() -> None:
    state = ProfileState()
    state.register_exchange()
    state.register_exchange()
    assert state.exchange_count == 2
    assert state.faltan() == list(CAMPOS)
    state.apply("inquietud", "manchas")
    assert state.faltan() == ["tipo_piel", "textura", "presupuesto"]


def test_indicadores_sensibles_sin_duplicados() -> None:
    state = ProfileState()
    state.add_sensitive_indicator("condicion_sensible")
    state.add_sensitive_indicator("condicion_sensible")
    assert state.indicadores_sensibles == ["condicion_sensible"]


def test_to_dict_incluye_valores_y_tope_solo_si_existe() -> None:
    state = resolved_profile(2)
    data = state.to_dict()
    assert data["tipo_piel"] == "seca/tensa"
    assert data["exchange_count"] == 2
    assert "tope_por_producto_mxn" not in data
    state.tope_mxn = 1000
    assert state.to_dict()["tope_por_producto_mxn"] == 1000


# ---- niveles de presupuesto y consulta --------------------------------------------------------------

BOUNDS = (Decimal("1300"), Decimal("3500"))


@pytest.mark.parametrize(
    ("precio", "nivel"),
    [
        ("0", "$"),
        ("1299.99", "$"),
        ("1300", "$$"),
        ("3500", "$$"),
        ("3500.01", "$$$"),
        ("99999", "$$$"),
    ],
)
def test_tier_for_price_fronteras(precio: str, nivel: str) -> None:
    assert tier_for_price(Decimal(precio), BOUNDS) == nivel


@given(
    a=st.decimals(min_value=0, max_value=100000, places=2),
    b=st.decimals(min_value=0, max_value=100000, places=2),
)
def test_tier_for_price_es_monotono(a: Decimal, b: Decimal) -> None:
    order = {"$": 0, "$$": 1, "$$$": 2}
    low, high = sorted((a, b))
    assert order[tier_for_price(low, BOUNDS)] <= order[tier_for_price(high, BOUNDS)]


def test_profile_query_usa_inquietud_y_textura() -> None:
    query = profile_query({"inquietud": "brotes", "textura": "brillante"})
    assert "acné" in query and "brillante" in query
    assert profile_query({}) == ""

"""Motor_Rutina: validación de la salida del modelo, razón del catálogo y ajuste del total (Req. 10, 11)."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, strategies as st

from advisor.models import PASO_KEYS, PASOS, split_beneficios
from advisor.routine import (
    MAX_RAZON,
    InvalidRoutineOutput,
    build_routine_schema,
    compose_razon,
    fit_total,
    min_possible_total,
    rank_candidates,
    total_price,
    validate_routine_output,
)
from factories import four_steps, make_product


def catalog_of(candidates: dict[str, list]) -> dict[str, Any]:
    return {p.sku: p for plist in candidates.values() for p in plist}


def good_output(candidates: dict[str, list]) -> dict[str, Any]:
    out: dict[str, Any] = {"requiere_asesor": False}
    for paso in PASOS:
        out[PASO_KEYS[paso]] = {"sku": candidates[paso][0].sku, "beneficios_idx": [0]}
    return out


# ---- Feature: skincare-voice-advisor, Property 27: solo acepta rutinas correctas -----------------------

def test_acepta_una_rutina_correcta_en_el_orden_de_los_pasos() -> None:
    cands = four_steps()
    chosen = validate_routine_output(good_output(cands), cands, catalog_of(cands))
    assert [paso for paso, _, _ in chosen] == list(PASOS)
    assert [p.sku for _, p, _ in chosen] == [cands[paso][0].sku for paso in PASOS]


@pytest.mark.parametrize("output", [None, [], "texto", 5])
def test_rechaza_salidas_que_no_son_objeto(output: Any) -> None:
    cands = four_steps()
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(output, cands, catalog_of(cands))


def test_requiere_asesor_impide_la_rutina() -> None:
    cands = four_steps()
    out = good_output(cands) | {"requiere_asesor": True}
    with pytest.raises(InvalidRoutineOutput, match="requiere_asesor"):
        validate_routine_output(out, cands, catalog_of(cands))


@pytest.mark.parametrize("paso", PASOS)
def test_rechaza_si_falta_un_paso(paso: str) -> None:
    cands = four_steps()
    out = good_output(cands)
    del out[PASO_KEYS[paso]]
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(out, cands, catalog_of(cands))


@pytest.mark.parametrize("paso", PASOS)
def test_rechaza_un_sku_fuera_de_los_candidatos(paso: str) -> None:
    cands = four_steps()
    out = good_output(cands)
    out[PASO_KEYS[paso]]["sku"] = "NO-EXISTE"
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(out, cands, catalog_of(cands))


def test_rechaza_un_sku_candidato_de_otro_paso() -> None:
    cands = four_steps()
    out = good_output(cands)
    out[PASO_KEYS["Limpieza"]]["sku"] = cands["Tratamiento"][0].sku
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(out, cands, catalog_of(cands))


def test_rechaza_si_el_catalogo_ya_no_tiene_el_sku() -> None:
    cands = four_steps()
    catalog = catalog_of(cands)
    del catalog[cands["Hidratación"][0].sku]
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(good_output(cands), cands, catalog)


def test_rechaza_si_el_paso_del_catalogo_no_coincide() -> None:
    cands = four_steps()
    catalog = catalog_of(cands)
    sku = cands["Limpieza"][0].sku
    catalog[sku] = make_product(sku, "Tratamiento")
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(good_output(cands), cands, catalog)


@pytest.mark.parametrize("idx", ["0", 1, None, [True], [1.5], ["0"]])
def test_rechaza_indices_que_no_son_lista_de_enteros(idx: Any) -> None:
    cands = four_steps()
    out = good_output(cands)
    out[PASO_KEYS["Limpieza"]]["beneficios_idx"] = idx
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(out, cands, catalog_of(cands))


def test_descarta_indices_fuera_de_rango_sin_rechazar_la_rutina() -> None:
    cands = four_steps()
    out = good_output(cands)
    out[PASO_KEYS["Limpieza"]]["beneficios_idx"] = [0, 99, -1]
    chosen = validate_routine_output(out, cands, catalog_of(cands))
    assert chosen[0][2] == [0]


@given(sku=st.text(max_size=30), paso=st.sampled_from(PASOS))
def test_cualquier_sku_inventado_es_rechazado(sku: str, paso: str) -> None:
    cands = four_steps()
    if sku in {p.sku for p in cands[paso]}:
        return
    out = copy.deepcopy(good_output(cands))
    out[PASO_KEYS[paso]]["sku"] = sku
    with pytest.raises(InvalidRoutineOutput):
        validate_routine_output(out, cands, catalog_of(cands))


def test_el_schema_limita_cada_paso_a_sus_candidatos() -> None:
    cands = four_steps()
    schema = build_routine_schema(cands)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"requiere_asesor", *PASO_KEYS.values()}
    for paso in PASOS:
        step = schema["properties"][PASO_KEYS[paso]]
        assert step["additionalProperties"] is False
        assert step["properties"]["sku"]["enum"] == [p.sku for p in cands[paso]]


# ---- Feature: skincare-voice-advisor, Property 30: razon_catalogo solo con fragmentos del catálogo ----

def test_sin_beneficios_devuelve_cadena_vacia_y_bandera() -> None:
    assert compose_razon([], [0]) == ("", True)


def test_une_los_fragmentos_elegidos_con_punto_final() -> None:
    assert compose_razon(["Hidrata profundamente", "Regenera", "Protege"], [0, 1]) == (
        "Hidrata profundamente. Regenera.",
        False,
    )


def test_no_repite_indices_duplicados_y_respeta_puntuacion_existente() -> None:
    assert compose_razon(["Ilumina.", "Unifica"], [0, 0, 1]) == ("Ilumina. Unifica.", False)


def test_sin_eleccion_valida_usa_el_primer_beneficio_completo_y_salta_introducciones() -> None:
    razon, sin = compose_razon(["Beneficios principales:", "Reduce poros"], [7])
    assert (razon, sin) == ("Reduce poros.", False)


def test_trunca_en_limite_de_palabra() -> None:
    long_text = " ".join(["hidratante"] * 40)
    razon, _ = compose_razon([long_text], [0])
    assert len(razon) <= MAX_RAZON
    assert razon.rstrip(".").split(" ")[-1] == "hidratante"  # no corta una palabra a la mitad


@given(
    beneficios=st.lists(
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=120).filter(
            lambda s: s.strip() != ""
        ),
        min_size=1,
        max_size=6,
    ),
    idx=st.lists(st.integers(min_value=-3, max_value=10), max_size=6),
)
def test_la_razon_es_una_linea_de_hasta_200_caracteres(beneficios: list[str], idx: list[int]) -> None:
    razon, sin = compose_razon(beneficios, idx)
    assert sin is False
    assert razon != ""
    assert len(razon) <= MAX_RAZON
    assert "\n" not in razon and "\r" not in razon


def test_split_beneficios_quita_vinetas_y_lineas_vacias() -> None:
    assert split_beneficios("- Uno\n\n• Dos\n  * Tres  \n") == ["Uno", "Dos", "Tres"]
    assert split_beneficios(None) == [] and split_beneficios("") == []


# ---- total de la rutina -------------------------------------------------------------------------------

def selection_and_options() -> tuple[dict[str, Any], dict[str, list[Any]]]:
    options = {
        paso: [make_product(f"{i}a", paso, precio="100"), make_product(f"{i}b", paso, precio="300"), make_product(f"{i}c", paso, precio="900")]
        for i, paso in enumerate(PASOS, start=1)
    }
    selection = {paso: options[paso][2] for paso in PASOS}  # todo lo más caro: 3600
    return selection, options


def test_total_price_suma_exacta() -> None:
    selection, _ = selection_and_options()
    assert total_price(selection) == Decimal("3600")
    assert total_price({}) == Decimal("0")


def test_min_possible_total_respeta_pasos_fijos() -> None:
    selection, options = selection_and_options()
    assert min_possible_total(selection, options, frozenset()) == Decimal("400")
    assert min_possible_total(selection, options, frozenset({"Limpieza"})) == Decimal("900") + Decimal("300")


def test_fit_total_no_cambia_lo_que_ya_cabe() -> None:
    selection, options = selection_and_options()
    assert fit_total(selection, options, frozenset(), Decimal("5000")) == selection


def test_fit_total_baja_el_total_sin_pasar_del_tope() -> None:
    selection, options = selection_and_options()
    fitted = fit_total(selection, options, frozenset(), Decimal("1500"))
    assert fitted is not None
    assert total_price(fitted) <= Decimal("1500")


def test_fit_total_devuelve_none_si_no_hay_solucion() -> None:
    selection, options = selection_and_options()
    assert fit_total(selection, options, frozenset(), Decimal("399")) is None
    assert fit_total(selection, options, frozenset(PASOS), Decimal("1000")) is None


@given(max_total=st.integers(min_value=0, max_value=5000), locked=st.sets(st.sampled_from(PASOS)))
def test_fit_total_cumple_el_tope_o_devuelve_none_y_no_toca_los_pasos_fijos(max_total: int, locked: set[str]) -> None:
    selection, options = selection_and_options()
    fitted = fit_total(selection, options, locked, Decimal(max_total))
    floor = min_possible_total(selection, options, locked)
    if fitted is None:
        assert floor > Decimal(max_total)  # solo falla si ni lo más barato posible cabe
        return
    assert total_price(fitted) <= Decimal(max_total)
    for paso in locked:
        assert fitted[paso] is selection[paso]
    for paso, product in fitted.items():
        assert product in options[paso]


# ---- ranking ------------------------------------------------------------------------------------------

def tier(p: Any) -> str:
    return "$"


def test_los_productos_curados_por_la_guia_van_primero() -> None:
    a, b, c = (make_product(s, "Limpieza") for s in ("a", "b", "c"))
    ranked = rank_candidates([a, b, c], None, None, tier, preferred={"c"})
    assert ranked[0].sku == "c"


def test_el_tipo_de_piel_coincidente_pasa_antes_que_el_resto() -> None:
    seca = make_product("seca", "Limpieza", tipo_piel="Seca")
    grasa = make_product("grasa", "Limpieza", tipo_piel="Grasa")
    todo = make_product("todo", "Limpieza", tipo_piel="Todo tipo de piel")
    ranked = rank_candidates([grasa, todo, seca], "Seca", None, tier)
    assert [p.sku for p in ranked] == ["seca", "todo", "grasa"]


def test_el_limite_recorta_y_nada_se_excluye_por_ordenar() -> None:
    products = [make_product(str(i), "Limpieza") for i in range(8)]
    assert len(rank_candidates(products, None, None, tier, limit=5)) == 5
    assert {p.sku for p in rank_candidates(products, None, None, tier, limit=99)} == {p.sku for p in products}


def test_con_producto_de_referencia_prefiere_el_mas_parecido_en_precio_y_marca() -> None:
    anchor = make_product("ref", "Tratamiento", marca="ALFA", precio="1000", tipo_producto="Suero")
    barato = make_product("barato", "Tratamiento", marca="BETA", precio="100", tipo_producto="Suero")
    cercano = make_product("cercano", "Tratamiento", marca="BETA", precio="900", tipo_producto="Suero")
    misma_marca = make_product("misma", "Tratamiento", marca="ALFA", precio="800", tipo_producto="Suero")
    ranked = rank_candidates([barato, cercano, misma_marca], None, None, tier, anchor=anchor)
    assert [p.sku for p in ranked] == ["misma", "cercano", "barato"]

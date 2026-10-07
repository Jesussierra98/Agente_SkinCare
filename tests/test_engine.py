"""RoutineEngine.armar con un Bedrock simulado (Req. 10): reintentos, Guardrail, derivación y tope del total."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, strategies as st

from advisor.models import PASO_KEYS, PASOS
from advisor.routine import MAX_INVOCATIONS, RoutineEngine
from agent_harness import FakeBedrock, FakeCatalog
from factories import four_steps, make_product


def engine_for(candidates: dict[str, list], bedrock: FakeBedrock, **kwargs: Any) -> RoutineEngine:
    products = [p for plist in candidates.values() for p in plist]
    return RoutineEngine(FakeCatalog(products), "modelo-de-prueba", bedrock, **kwargs)


def armar(engine: RoutineEngine, candidates: dict[str, list], sensibles: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(engine.armar({"tipo_piel": "seca/tensa"}, candidates, sensibles or [], **kwargs))


# ---- camino feliz -------------------------------------------------------------------------------------------

def test_arma_una_rutina_de_cuatro_pasos_con_datos_del_catalogo() -> None:
    cands = four_steps()
    result = armar(engine_for(cands, FakeBedrock("first")), cands)
    assert result["ok"] is True
    pasos = result["pasos"]
    assert [p["paso"] for p in pasos] == [1, 2, 3, 4]
    for paso, step in zip(PASOS, pasos):
        chosen = cands[paso][0]
        assert step["sku"] == chosen.sku and step["nombre"] == chosen.nombre and step["marca"] == chosen.marca
        assert step["precio"] == f"{chosen.precio:.2f}"
        assert step["razon_catalogo"] == "Hidrata profundamente."  # primer beneficio del catálogo
        assert step["sin_beneficios"] is False
        assert step["modo_uso"] == chosen.modo_uso and step["imagen_url"] == chosen.imagen_url


def test_un_producto_sin_beneficios_lo_marca_sin_inventar_razon() -> None:
    cands = four_steps()
    cands["Limpieza"][0] = make_product("sb", "Limpieza", beneficios="")
    result = armar(engine_for(cands, FakeBedrock("first")), cands)
    assert result["ok"] is True
    assert result["pasos"][0]["razon_catalogo"] == "" and result["pasos"][0]["sin_beneficios"] is True


def test_la_solicitud_a_bedrock_usa_salida_estructurada_y_el_modelo_indicado() -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first")
    armar(engine_for(cands, bedrock), cands)
    call = bedrock.calls[0]
    assert call["modelId"] == "modelo-de-prueba"
    assert call["inferenceConfig"]["temperature"] <= 0.3
    text_format = call["outputConfig"]["textFormat"]
    assert text_format["type"] == "json_schema"
    schema = json.loads(text_format["structure"]["jsonSchema"]["schema"])
    assert set(schema["required"]) == {"requiere_asesor", *PASO_KEYS.values()}
    assert "guardrailConfig" not in call


def test_el_guardrail_se_envia_en_cada_llamada_cuando_esta_configurado() -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first", script=["bad", "ok"])
    guardrail = {"guardrailIdentifier": "gr-1", "guardrailVersion": "1"}
    armar(engine_for(cands, bedrock, guardrail=guardrail), cands)
    assert len(bedrock.calls) == 2
    assert all(call["guardrailConfig"] == guardrail for call in bedrock.calls)


def test_el_payload_lleva_perfil_candidatos_y_preferencias_recortadas() -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first")
    armar(engine_for(cands, bedrock), cands, preferencias="x" * 500, max_total=Decimal("9000"), preferred={cands["Limpieza"][0].sku})
    payload = bedrock.payload()
    assert payload["perfil"] == {"tipo_piel": "seca/tensa"}
    assert len(payload["preferencias_del_cliente"]) == 200
    assert payload["tope_total_mxn"] == "9000.00"
    limpieza = payload["candidatos"]["Limpieza"]
    assert [c["sku"] for c in limpieza] == [p.sku for p in cands["Limpieza"]]
    assert limpieza[0]["recomendado_por_la_guia"] is True and "recomendado_por_la_guia" not in limpieza[1]


# ---- Feature: skincare-voice-advisor, Property 23 y 29: no se llama al LLM ------------------------------

def test_con_indicador_sensible_no_llama_al_modelo() -> None:
    cands = four_steps()
    bedrock = FakeBedrock()
    result = armar(engine_for(cands, bedrock), cands, sensibles=["condicion_sensible"])
    assert result == {"ok": False, "requiere_asesor": True}
    assert bedrock.calls == []


def test_pasos_sin_candidatos_se_reportan_exactos_sin_llamar_al_modelo() -> None:
    cands = four_steps()
    cands["Tratamiento"] = []
    cands["Protección solar"] = []
    bedrock = FakeBedrock()
    result = armar(engine_for(cands, bedrock), cands)
    assert result == {"ok": False, "error": "pasos_sin_candidatos", "pasos": ["Tratamiento", "Protección solar"]}
    assert bedrock.calls == []


# ---- Feature: skincare-voice-advisor, Property 28: a lo más 2 invocaciones y nunca rutinas parciales ---

def test_un_primer_intento_invalido_se_reintenta_una_vez() -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first", script=["bad", "ok"])
    assert armar(engine_for(cands, bedrock), cands)["ok"] is True
    assert len(bedrock.calls) == 2


@pytest.mark.parametrize("behavior", ["bad", "raise", "guardrail", "invented"])
def test_dos_fallos_seguidos_terminan_en_error_sin_pasos_parciales(behavior: str) -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first", script=[behavior, behavior])
    result = armar(engine_for(cands, bedrock), cands)
    assert result == {"ok": False, "error": "no_fue_posible_armar"}
    assert len(bedrock.calls) == MAX_INVOCATIONS == 2


def test_si_el_modelo_pide_asesor_no_se_reintenta() -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first", script=["asesor"])
    assert armar(engine_for(cands, bedrock), cands) == {"ok": False, "requiere_asesor": True}
    assert len(bedrock.calls) == 1


@given(st.lists(st.sampled_from(["ok", "bad", "raise", "guardrail", "invented", "asesor"]), min_size=2, max_size=2))
def test_cualquier_combinacion_de_respuestas_cumple_el_contrato(script: list[str]) -> None:
    cands = four_steps()
    bedrock = FakeBedrock("first", script=script)
    result = armar(engine_for(cands, bedrock), cands)
    assert len(bedrock.calls) <= MAX_INVOCATIONS
    if result["ok"]:
        assert len(result["pasos"]) == 4
    else:
        assert "pasos" not in result
        assert result.get("error") == "no_fue_posible_armar" or result.get("requiere_asesor") is True


# ---- tope del total --------------------------------------------------------------------------------------------

def test_si_ni_lo_mas_barato_cabe_responde_el_minimo_posible_sin_llamar_al_modelo() -> None:
    cands = four_steps()  # lo más barato de cada paso cuesta 400: mínimo 1600
    bedrock = FakeBedrock()
    result = armar(engine_for(cands, bedrock), cands, max_total=Decimal("1000"))
    assert result == {"ok": False, "error": "no_cabe_en_presupuesto", "minimo_posible_mxn": "1600.00"}
    assert bedrock.calls == []


def test_si_el_modelo_se_pasa_del_tope_el_codigo_lo_corrige() -> None:
    cands = four_steps()  # el modelo elige lo más caro: 4 × 900 = 3600
    result = armar(engine_for(cands, FakeBedrock("priciest")), cands, max_total=Decimal("2000"))
    assert result["ok"] is True
    total = sum(Decimal(p["precio"]) for p in result["pasos"])
    assert total <= Decimal("2000")


def test_pasos_fijos_no_cambian_al_corregir_el_total() -> None:
    cands = four_steps()
    locked_sku = cands["Limpieza"][1].sku  # el más caro de Limpieza, fijo
    cands["Limpieza"] = [cands["Limpieza"][1]]
    result = armar(engine_for(cands, FakeBedrock("priciest")), cands, max_total=Decimal("2500"), locked={"Limpieza"})
    assert result["ok"] is True
    assert result["pasos"][0]["sku"] == locked_sku
    assert sum(Decimal(p["precio"]) for p in result["pasos"]) <= Decimal("2500")

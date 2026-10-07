"""Herramientas del agente de voz (tools.py) con catálogo en memoria y Bedrock simulado."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any, Awaitable, Callable

import pytest

from advisor.tools import _spell, next_action_hint
from agent_harness import Env, default_products, make_env

CODE_RE = re.compile(r"^[A-HJ-NP-Z]{3}-[2-9]{3}$")
PRODUCT_VIEW_KEYS = {"sku", "nombre", "marca", "precio_mxn", "tipo_piel", "beneficios"}


def call(env: Env, name: str, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(env.tools[name](**kwargs))


def scenario(env: Env, body: Callable[[Env], Awaitable[Any]]) -> Any:
    return asyncio.run(body(env))


def types(env: Env) -> list[str]:
    return [e["type"] for e in env.events]


# ---- registrar_perfil ------------------------------------------------------------------------------------

def test_registrar_un_dato_lo_guarda_y_pide_el_siguiente() -> None:
    env = make_env(ready=False)
    result = call(env, "registrar_perfil", campo="tipo_piel", valor="seca/tensa")
    assert result["registrado"] is True and result["reformular"] is False
    assert result["faltan"] == ["inquietud", "textura", "presupuesto"]
    assert result["listo_para_proponer"] is False
    assert result["siguiente_accion"].startswith("Siguiente:")
    assert "segmento_del_cliente" not in result  # sin guía cargada
    assert env.session.profile.valores["tipo_piel"] == "seca/tensa"


def test_un_valor_ambiguo_pide_reformular_una_vez() -> None:
    env = make_env(ready=False)
    result = call(env, "registrar_perfil", campo="tipo_piel", valor="???")
    assert result["registrado"] is False and result["reformular"] is True
    assert "Reformula" in result["siguiente_accion"]
    assert "tipo_piel" not in env.session.profile.valores


def test_el_monto_solo_cuenta_si_el_presupuesto_fue_aceptado() -> None:
    env = make_env(ready=False)
    call(env, "registrar_perfil", campo="presupuesto", valor="mucho", monto_mxn=500)
    assert env.session.profile.tope_mxn == 0
    call(env, "registrar_perfil", campo="presupuesto", valor="$", monto_mxn=1000)
    assert env.session.profile.tope_mxn == 1000


def test_con_el_perfil_completo_indica_armar_la_rutina() -> None:
    env = make_env()
    result = call(env, "registrar_perfil", campo="tipo_piel", valor="mixta/deshidratada")
    assert result["listo_para_proponer"] is True
    assert "armar_rutina" in result["siguiente_accion"]


def test_la_siguiente_accion_segun_el_estado() -> None:
    suspended = make_env()
    suspended.session.recommendations_suspended = True
    assert "No recomiendes" in next_action_hint(suspended.session)

    armed = make_env()
    armed.session.routine = [{"paso": 1}]
    assert "ya está armada" in next_action_hint(armed.session)

    ready = make_env()
    assert "armar_rutina" in next_action_hint(ready.session)

    early = make_env()
    early.session.profile.exchange_count = 2
    assert "UNA pregunta" in next_action_hint(early.session)

    empty = make_env(ready=False)
    assert next_action_hint(empty.session).startswith("Siguiente:")


# ---- armar_rutina --------------------------------------------------------------------------------------------

def test_no_arma_la_rutina_antes_de_tiempo_y_lo_decide_el_codigo() -> None:
    env = make_env()
    env.session.profile.exchange_count = 2
    result = call(env, "armar_rutina")
    assert result["error"] == "aun_no_es_momento_de_proponer"
    assert result["intercambios"] == 2 and result["faltan"] == []
    assert env.events == [] and env.bedrock.calls == [] and env.saved_records() == []


def test_arma_guarda_y_publica_la_rutina_en_una_sola_llamada() -> None:
    env = make_env()
    result = call(env, "armar_rutina")
    assert result["ok"] is True
    assert result["total_mxn"] == "4600.00"
    assert len(result["pasos_resumen"]) == 4
    assert CODE_RE.match(result["codigo_corto"])
    assert result["codigo_para_dictar"] == _spell(result["codigo_corto"])
    assert types(env) == ["routine", "saved"]
    assert env.session.tools_running == 0

    assert [p["sku"] for p in env.session.routine or []] == ["L1", "T1", "H1", "S1"]
    [record] = env.saved_records()
    assert record["estado"] == "pendiente" and record["codigo_corto"] == result["codigo_corto"]
    assert record["session_id"] == env.session.session_id
    assert [p["sku"] for p in record["rutina"]] == ["L1", "T1", "H1", "S1"]
    saved_event = env.events[1]
    assert saved_event["rec_id"] == record["rec_id"]
    assert saved_event["qr_url"] == f"https://tienda.test/caja?rec={record['rec_id']}"


def test_armar_dos_veces_no_duplica_nada() -> None:
    env = make_env()

    async def body(e: Env) -> tuple[dict[str, Any], dict[str, Any]]:
        return await e.tools["armar_rutina"](), await e.tools["armar_rutina"]()

    first, second = scenario(env, body)
    assert second["ya_mostrada_en_pantalla"] is True
    assert second["codigo_corto"] == first["codigo_corto"]
    assert len(env.saved_records()) == 1 and len(env.bedrock.calls) == 1 and len(env.events) == 2


@pytest.mark.parametrize("name", ["armar_rutina", "guardar_recomendacion"])
def test_con_recomendaciones_suspendidas_no_se_arma_ni_se_guarda(name: str) -> None:
    env = make_env()
    env.session.recommendations_suspended = True
    assert call(env, name) == {"error": "recomendacion_suspendida"}
    assert env.bedrock.calls == [] and env.saved_records() == []


def test_buscar_y_ajustar_tambien_respetan_la_suspension() -> None:
    env = make_env()
    env.session.recommendations_suspended = True
    assert call(env, "buscar_productos", paso="Limpieza") == {"error": "recomendacion_suspendida"}
    assert call(env, "ajustar_rutina", cambio="mas_barato") == {"error": "recomendacion_suspendida"}


# ---- Feature: skincare-voice-advisor, Property 23: un indicador sensible impide generar la rutina ---

def test_un_indicador_sensible_deriva_y_no_presenta_rutina() -> None:
    env = make_env()
    env.session.profile.add_sensitive_indicator("condicion_sensible")
    assert call(env, "armar_rutina") == {"requiere_asesor": True}
    assert env.bedrock.calls == [] and env.session.routine is None and env.saved_records() == []
    assert env.session.recommendations_suspended is True
    assert [c[0] for c in env.notifier.calls] == ["requiere_asesor"]
    assert "handoff" in types(env) and "routine" not in types(env)


def test_si_el_catalogo_falla_responde_catalogo_no_disponible() -> None:
    env = make_env(fail_search=True)
    assert call(env, "armar_rutina") == {"error": "catalogo_no_disponible"}
    assert call(env, "buscar_productos", paso="Limpieza") == {"error": "catalogo_no_disponible"}
    assert env.session.tools_running == 0 and env.events == []


def test_si_el_modelo_falla_dos_veces_no_se_guarda_ni_se_muestra_nada() -> None:
    env = make_env(script=["raise", "raise"])
    assert call(env, "armar_rutina") == {"error": "no_fue_posible_armar", "ok": False}
    assert env.saved_records() == [] and env.events == [] and env.session.routine is None
    assert env.session.tools_running == 0


# ---- buscar_productos y detalle_producto ----------------------------------------------------------------

def test_buscar_devuelve_a_lo_mas_5_productos_con_vista_reducida() -> None:
    env = make_env()
    result = call(env, "buscar_productos", paso="Limpieza")
    assert result["paso"] == "Limpieza" and len(result["productos"]) == 5
    for view in result["productos"]:
        assert set(view) == PRODUCT_VIEW_KEYS  # sin ingredientes, modo de uso ni imagen
        assert len(view["beneficios"]) <= 4
    assert [p.sku for p in env.session.candidates["Limpieza"]] == [v["sku"] for v in result["productos"]]


def test_buscar_un_paso_sin_productos_lo_dice() -> None:
    products = [p for p in default_products() if p.paso_rutina != "Protección solar"]
    env = make_env(products=products)
    assert call(env, "buscar_productos", paso="Protección solar") == {"paso": "Protección solar", "sin_candidatos": True}


# ---- Feature: skincare-voice-advisor, Property 21: solo productos del catálogo devueltos en la sesión ---

def test_detalle_solo_de_productos_vistos_en_la_sesion() -> None:
    env = make_env()
    unknown = {"error": "producto_no_encontrado_en_esta_sesion"}
    assert call(env, "detalle_producto", sku="L1") == unknown  # aún no se ha buscado
    call(env, "buscar_productos", paso="Limpieza")
    detail = call(env, "detalle_producto", sku="L1")
    assert detail["nombre"] == "Gel Alfa" and detail["ingredientes"] == "Agua, glicerina"
    assert detail["modo_uso"] == "Aplicar por la mañana"
    assert call(env, "detalle_producto", sku="L7") == unknown  # existe, pero no salió en los 5 candidatos
    assert call(env, "detalle_producto", sku="NO-EXISTE") == unknown


def test_detalle_de_los_productos_de_la_rutina() -> None:
    env = make_env()
    call(env, "armar_rutina")
    assert call(env, "detalle_producto", sku="T1")["nombre"] == "Suero Uno"


# ---- guardar_recomendacion y derivar_asesor -----------------------------------------------------------------

def test_guardar_recomendacion_devuelve_el_mismo_codigo_sin_duplicar() -> None:
    env = make_env()
    assert call(env, "guardar_recomendacion") == {"error": "no_hay_rutina_armada"}

    async def body(e: Env) -> tuple[dict[str, Any], dict[str, Any]]:
        return await e.tools["armar_rutina"](), await e.tools["guardar_recomendacion"]()

    armed, saved = scenario(env, body)
    assert saved["ok"] is True and saved["codigo_corto"] == armed["codigo_corto"]
    assert saved["letras_y_numeros"] == _spell(armed["codigo_corto"])
    assert len(env.saved_records()) == 1


def test_derivar_al_asesor_avisa_y_no_suspende_por_un_diagnostico() -> None:
    env = make_env()
    result = call(env, "derivar_asesor", motivo="diagnostico")
    assert result["derivado"] is True
    assert env.notifier.calls[0][0] == "diagnostico"
    assert env.session.recommendations_suspended is False
    assert env.events[-1] == {"type": "handoff", "motivo": "diagnostico", "estado": "pendiente"}


def test_si_no_se_pudo_avisar_al_asesor_indica_ir_al_mostrador() -> None:
    env = make_env(notifier_fails=True)
    result = call(env, "derivar_asesor", motivo="condicion_sensible")
    assert result["derivado"] is False and "mostrador" in result["indicacion"]
    assert env.session.recommendations_suspended is True


# ---- ajustar_rutina ------------------------------------------------------------------------------------------

def test_no_se_puede_ajustar_una_rutina_que_no_existe() -> None:
    env = make_env()
    assert call(env, "ajustar_rutina", cambio="mas_barato")["error"] == "no_hay_rutina_que_ajustar"


def test_mas_barato_en_un_paso_cambia_solo_ese_paso_y_conserva_el_codigo() -> None:
    env = make_env()

    async def body(e: Env) -> tuple[dict[str, Any], dict[str, Any]]:
        return await e.tools["armar_rutina"](), await e.tools["ajustar_rutina"](cambio="mas_barato", pasos="Limpieza")

    armed, result = scenario(env, body)
    assert result["ok"] is True
    assert result["pasos_cambiados"] == ["Limpieza"]
    assert result["pasos_sin_cambio"] == ["Tratamiento", "Hidratación", "Protección solar"]
    assert result["codigo_corto"] == armed["codigo_corto"]
    assert (result["total_antes_mxn"], result["total_mxn"]) == ("4600.00", "4300.00")

    assert [p["sku"] for p in env.session.routine or []] == ["L2", "T1", "H1", "S1"]
    [record] = env.saved_records()  # misma recomendación, actualizada
    assert record["rutina"][0]["sku"] == "L2" and record["version"] == 2 and record["estado"] == "pendiente"
    assert types(env) == ["routine", "saved", "routine"]
    assert env.session.adjustments == 1 and "L1" in env.session.rejected_skus
    assert env.session.routine_change == "ajuste" and env.session.level == "$"

    [changed] = [s for s in result["pasos_resumen"] if s.get("cambio")]
    assert changed["paso"] == "Limpieza" and changed["antes"] == "Gel Alfa"


def test_evitar_una_marca_cambia_sus_productos_y_no_vuelve_a_ofrecerla() -> None:
    env = make_env()

    async def body(e: Env) -> dict[str, Any]:
        await e.tools["armar_rutina"]()
        return await e.tools["ajustar_rutina"](cambio="otro_producto", evitar_marca="alfa")

    result = scenario(env, body)
    assert result["ok"] is True and result["pasos_cambiados"] == ["Limpieza"]
    assert env.session.routine and env.session.routine[0]["marca"] != "ALFA"
    assert "alfa" in env.session.avoided_brands and "L1" in env.session.rejected_skus


def test_sin_alternativas_lo_dice_y_no_toca_la_rutina() -> None:
    env = make_env()

    async def body(e: Env) -> dict[str, Any]:
        await e.tools["armar_rutina"]()
        return await e.tools["ajustar_rutina"](cambio="mas_barato", pasos="Protección solar")

    result = scenario(env, body)
    assert result["ok"] is False and result["error"] == "sin_alternativas"
    assert result["pasos_sin_alternativa"] == ["Protección solar"]
    assert [p["sku"] for p in env.session.routine or []] == ["L1", "T1", "H1", "S1"]
    assert env.session.adjustments == 0 and types(env) == ["routine", "saved"] and len(env.bedrock.calls) == 1


def test_un_tope_imposible_responde_el_minimo_posible_y_deja_la_rutina_igual() -> None:
    env = make_env()

    async def body(e: Env) -> dict[str, Any]:
        await e.tools["armar_rutina"]()
        return await e.tools["ajustar_rutina"](cambio="mas_barato", total_maximo_mxn=10)

    result = scenario(env, body)
    assert result["ok"] is False and result["error"] == "no_cabe_en_presupuesto"
    assert result["minimo_posible_mxn"] == "2900.00"
    assert "minimo_posible_mxn" in result["instruccion"]
    assert [p["sku"] for p in env.session.routine or []] == ["L1", "T1", "H1", "S1"]
    assert env.session.adjustments == 0
    assert env.session.total_cap == Decimal("10")  # el tope dicho por el cliente se recuerda


def test_si_la_rutina_ya_cabe_en_el_tope_no_cambia_nada() -> None:
    env = make_env()

    async def body(e: Env) -> dict[str, Any]:
        await e.tools["armar_rutina"]()
        return await e.tools["ajustar_rutina"](cambio="mas_barato", total_maximo_mxn=100000)

    result = scenario(env, body)
    assert result["ok"] is True and result["sin_cambios"] is True
    assert result["total_mxn"] == "4600.00" and result["tope_total_vigente_mxn"] == "100000.00"
    assert env.session.adjustments == 0 and types(env) == ["routine", "saved"]


def test_hay_un_maximo_de_ajustes_por_sesion() -> None:
    env = make_env()

    async def body(e: Env) -> dict[str, Any]:
        await e.tools["armar_rutina"]()
        e.session.adjustments = 8
        return await e.tools["ajustar_rutina"](cambio="mas_barato", pasos="Limpieza")

    result = scenario(env, body)
    assert result["error"] == "limite_de_ajustes" and "asesor" in result["instruccion"]

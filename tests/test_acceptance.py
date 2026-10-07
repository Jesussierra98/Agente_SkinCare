"""Casos de aceptación TC-01 a TC-06 (requirements.md) en el entorno de pruebas: Nova y Bedrock simulados.

Lo que depende del LLM de voz (fluidez, acento, que no lea términos médicos en voz alta) se evalúa con
`scripts/eval_conversations.py` contra el agente real y en el piloto. Aquí se verifica todo lo que el código garantiza.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from advisor import prompts, sensitive
from advisor.dynamo import DynamoRecommendations
from advisor.language import LanguageTracker
from agent_harness import default_products, make_env
from caja_handler import CajaApi
from test_save_pubmed import FakeNcbi, tables, with_pubmed  # noqa: F401  (tables es una fixture)

CASHIER = {"cognito:groups": ["caja"], "username": "caja-01"}


def call(env: Any, name: str, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(env.tools[name](**kwargs))


def caja(api: CajaApi, route: str, **path: str) -> tuple[int, dict[str, Any]]:
    response = api.handle(
        {"routeKey": route, "pathParameters": path, "requestContext": {"authorizer": {"jwt": {"claims": CASHIER}}}}
    )
    return response["statusCode"], json.loads(response["body"])


# ---- TC-01: conversación bilingüe con cambio dinámico -----------------------------------------------------------------

def test_tc01_el_idioma_sigue_al_cliente_de_ingles_a_espanol() -> None:
    tracker = LanguageTracker()
    assert tracker.update("Hello, I have oily skin and acne") == "en"
    assert tracker.update("¿Qué protector solar tienes?") == "es"
    assert tracker.update("ok") == "es"  # un turno sin señal conserva el idioma
    assert tracker.update("I would like a moisturizer, please") == "en"


def test_tc01_el_prompt_pide_responder_en_el_idioma_del_ultimo_mensaje_y_trae_la_frase_en_los_dos() -> None:
    assert "Habla en el idioma del último mensaje del cliente" in prompts.SYSTEM_PROMPT
    assert "That information isn't available in our catalog" in prompts.SYSTEM_PROMPT
    assert "Esa información no está disponible en nuestro catálogo" in prompts.SYSTEM_PROMPT


# ---- TC-02: rutina 100 % cerrada al catálogo ----------------------------------------------------------------------------

def test_tc02_los_cuatro_productos_son_del_catalogo_y_no_aparece_nada_ajeno() -> None:
    env = make_env()
    result = call(env, "armar_rutina")
    assert result["ok"] is True
    catalog = {p.sku: p for p in default_products()}
    routine = env.session.routine
    assert routine is not None and len(routine) == 4
    assert [step["paso"] for step in routine] == [1, 2, 3, 4]
    for step in routine:
        product = catalog[step["sku"]]  # un SKU ajeno lanzaría KeyError
        assert (step["nombre"], step["marca"]) == (product.nombre, product.marca)
        assert product.paso_rutina == ["Limpieza", "Tratamiento", "Hidratación", "Protección solar"][step["paso"] - 1]
    # Lo que se le dice al modelo de voz tampoco trae marcas ni SKUs de fuera del catálogo.
    known_brands = {p.marca for p in catalog.values()}
    spoken = json.dumps(result, ensure_ascii=False)
    assert all(step["marca"] in known_brands for step in routine) and "NO-EXISTE" not in spoken


def test_tc02_si_el_modelo_inventa_un_producto_se_rechaza_y_no_se_guarda_nada_parcial() -> None:
    env = make_env(script=["invented", "invented"])
    result = call(env, "armar_rutina")
    assert result.get("ok") is not True
    assert env.saved_records() == [] and env.session.routine is None


# ---- TC-03: Guardrail ante un caso clínico -------------------------------------------------------------------------------

def test_tc03_un_cuadro_clinico_deriva_y_no_recomienda_nada() -> None:
    text = "Tengo un sarpullido severo con pus y sangrado, ¿qué crema me cura?"
    assert sensitive.detect(text) == "condicion_sensible"

    env = make_env()
    env.session.profile.add_sensitive_indicator("condicion_sensible")
    from advisor.session import start_handoff

    asyncio.run(start_handoff(env.session, env.notifier, "condicion_sensible"))
    assert env.session.recommendations_suspended is True
    assert [motivo for motivo, _ in env.notifier.calls] == ["condicion_sensible"]
    # Con la derivación activa no se arma ni se guarda ninguna rutina.
    result = call(env, "armar_rutina")
    assert result.get("ok") is not True and env.saved_records() == []
    assert "routine" not in [e["type"] for e in env.events]
    assert "diagnóstico" in prompts.HANDOFF_INSTRUCTIONS["diagnostico"]["es"] or "diagnoses" in prompts.HANDOFF_INSTRUCTIONS["diagnostico"]["en"]


# ---- TC-04: Guardrail ante mezcla de activos -----------------------------------------------------------------------------------

def test_tc04_la_mezcla_de_activos_se_remite_al_asesor_sin_opinion_quimica() -> None:
    for text in ("¿Puedo ponerme retinol puro y ácido glicólico juntos?", "Can I use retinol and glycolic acid together?"):
        found = sensitive.detect_with_term(text)
        assert found is not None and found[0] == "compatibilidad", text
    es = prompts.HANDOFF_INSTRUCTIONS["compatibilidad"]["es"]
    assert "No des ninguna opinión" in es
    assert "Give no opinion" in prompts.HANDOFF_INSTRUCTIONS["compatibilidad"]["en"]

    env = make_env()
    from advisor.session import start_handoff

    asyncio.run(start_handoff(env.session, env.notifier, "compatibilidad"))
    assert [m for m, _ in env.notifier.calls] == ["compatibilidad"]
    assert env.session.recommendations_suspended is False  # la conversación puede seguir con otros temas


# ---- TC-05: flujo completo de entrega en Caja --------------------------------------------------------------------------------------

def test_tc05_el_cajero_consulta_por_codigo_ve_los_cuatro_productos_con_total_y_marca_atendida(tables) -> None:  # noqa: F811
    recs, _, sessions = tables
    env = make_env()
    env.deps.store = DynamoRecommendations(recs)
    armed = call(env, "armar_rutina")
    code = armed["codigo_corto"]
    api = CajaApi(recs, sessions)

    status, body = caja(api, "GET /recomendacion/{id}", id=code)
    assert status == 200 and body["estado"] == "PENDIENTE"
    assert len(body["productos"]) == 4 and [p["paso"] for p in body["productos"]] == [1, 2, 3, 4]
    assert body["total_sugerido"] == armed["total_mxn"]

    status, attended = caja(api, "POST /recomendacion/{id}/atendida", id=body["rec_id"])
    assert status == 200 and attended["estado"] == "ATENDIDA"
    stored = recs.get_item(Key={"rec_id": body["rec_id"]})["Item"]
    assert stored["estado"] == "atendida" and stored["cajero_id"] == "caja-01" and stored["fecha_atendida"].endswith("Z")

    # Marcar otra vez no cambia nada (idempotente).
    _, again = caja(api, "POST /recomendacion/{id}/atendida", id=body["rec_id"])
    assert again["fecha_atendida"] == attended["fecha_atendida"]


# ---- TC-06: consulta controlada de PubMed -----------------------------------------------------------------------------------------------

def test_tc06_la_pantalla_recibe_dos_articulos_y_el_modelo_solo_una_bandera() -> None:
    env = with_pubmed(make_env(), FakeNcbi(ids=("111", "222"), titles={"111": "Salicylic acid in acne", "222": "Keratolytics review"}))
    call(env, "armar_rutina")
    # El ingrediente debe estar en la rutina: los productos de prueba traen glicerina.
    result = call(env, "evidencia_ingrediente", ingrediente="glicerina")
    assert result == {"lecturas_en_pantalla": True}  # el modelo no ve títulos: no puede leerlos en voz alta
    readings = [e for e in env.events if e["type"] == "readings"]
    assert len(readings) == 1 and [a["titulo"] for a in readings[0]["articulos"]] == ["Salicylic acid in acne", "Keratolytics review"]


def test_tc06_un_ingrediente_que_no_es_de_la_rutina_no_consulta_pubmed() -> None:
    ncbi = FakeNcbi()
    env = with_pubmed(make_env(), ncbi)
    call(env, "armar_rutina")
    assert call(env, "evidencia_ingrediente", ingrediente="ácido salicílico") == {"lecturas_en_pantalla": False}
    assert ncbi.calls == []

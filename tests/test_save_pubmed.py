"""Herramienta_Guardar (Req. 13, 23) y Consultor_PubMed (Req. 16, 17): lógica pura, DynamoDB simulado y herramientas."""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
import pytest
from hypothesis import given, settings, strategies as st
from moto import mock_aws

from advisor.dynamo import DynamoRecommendations
from advisor.pubmed import (
    CACHE_DAYS,
    DynamoEvidenceCache,
    InMemoryEvidenceCache,
    PubMedConsultant,
    ingredient_in_routine,
    normalize_ingredient,
    split_ingredients,
)
from advisor.save import validate_routine_for_save
from advisor.tools import build_tools
from agent_harness import Env, default_products, make_env
from caja_handler import CajaApi
from factories import make_product

T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def step(paso: int = 1, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "paso": paso, "sku": f"S{paso}", "nombre": "N", "marca": "M", "precio": "100.50",
        "imagen_url": "https://x/i.jpg", "razon_catalogo": "Hidrata.", "modo_uso": "Aplicar",
    }
    item.update(overrides)
    return item


def routine() -> list[dict[str, Any]]:
    return [step(n) for n in (1, 2, 3, 4)]


# ---- Feature: skincare-voice-advisor, Property 31: validar la rutina completa antes de escribir ---------

def test_una_rutina_correcta_no_tiene_problemas_y_no_se_modifica() -> None:
    original = routine()
    snapshot = copy.deepcopy(original)
    assert validate_routine_for_save(original) == []
    assert original == snapshot


@pytest.mark.parametrize("bad", [None, {}, "x", [], routine()[:3], routine() + [step(1)]])
def test_exige_exactamente_cuatro_pasos(bad: Any) -> None:
    assert validate_routine_for_save(bad) == ["rutina:se_esperan_4_pasos"]


def test_un_elemento_que_no_es_objeto() -> None:
    bad = routine()
    bad[2] = "no soy un objeto"
    assert validate_routine_for_save(bad) == ["paso[2]:no_es_objeto"]


@pytest.mark.parametrize("paso", [0, 5, -1, "1", 1.0, True, None])
def test_paso_debe_ser_entero_de_1_a_4(paso: Any) -> None:
    bad = routine()
    bad[0]["paso"] = paso
    assert "paso[0].paso" in validate_routine_for_save(bad)


def test_pasos_repetidos() -> None:
    bad = routine()
    bad[3]["paso"] = 1
    assert validate_routine_for_save(bad) == ["paso[3].paso_repetido"]


@pytest.mark.parametrize("field", ["sku", "nombre", "marca", "imagen_url", "razon_catalogo", "modo_uso"])
@pytest.mark.parametrize("value", [None, 5, ["x"]])
def test_los_campos_de_texto_deben_ser_str(field: str, value: Any) -> None:
    bad = routine()
    bad[1][field] = value
    assert validate_routine_for_save(bad) == [f"paso[1].{field}"]
    missing = routine()
    del missing[1][field]
    assert validate_routine_for_save(missing) == [f"paso[1].{field}"]


def test_los_textos_vacios_son_validos() -> None:
    ok = routine()
    ok[0]["razon_catalogo"] = ""
    ok[0]["modo_uso"] = ""
    assert validate_routine_for_save(ok) == []


@pytest.mark.parametrize("price", [0, "0", 10, "10.5", "1.500", Decimal("1234.5"), "999999.99", 999999.99])
def test_precios_validos(price: Any) -> None:
    good = routine()
    good[0]["precio"] = price
    assert validate_routine_for_save(good) == []


@pytest.mark.parametrize(
    "price", [-1, "-0.01", "abc", "", None, True, [], "10.123", 1000000, "999999.991", Decimal("NaN"), Decimal("Infinity")]
)
def test_precios_invalidos(price: Any) -> None:
    bad = routine()
    bad[2]["precio"] = price
    assert validate_routine_for_save(bad) == ["paso[2].precio"]


@given(
    prices=st.lists(st.decimals(min_value=0, max_value=Decimal("999999.99"), places=2), min_size=4, max_size=4),
    order=st.permutations([1, 2, 3, 4]),
)
def test_toda_rutina_bien_formada_es_valida(prices: list[Decimal], order: list[int]) -> None:
    good = [step(paso, precio=price) for paso, price in zip(order, prices)]
    assert validate_routine_for_save(good) == []


@given(st.decimals(min_value=0, max_value=Decimal("999999.99"), places=3).filter(lambda d: d != d.quantize(Decimal("0.01"))))
def test_mas_de_dos_decimales_siempre_se_rechaza(price: Decimal) -> None:
    bad = routine()
    bad[0]["precio"] = price
    assert validate_routine_for_save(bad) == ["paso[0].precio"]


# ---- herramientas: validación, lecturas y DynamoDB ----------------------------------------------------------------

def call(env: Env, name: str, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(env.tools[name](**kwargs))


def test_una_rutina_con_precio_fuera_de_rango_no_se_guarda() -> None:
    # Primero en el catálogo para entrar a los 5 candidatos (el ranking es estable en los empates).
    products = [make_product("CARO", "Limpieza", nombre="Lujo", marca="X", precio="1000000")] + default_products()
    env = make_env(products=products)
    result = call(env, "armar_rutina")
    assert result["ok"] is True and "codigo_corto" not in result and "aviso" in result
    assert env.saved_records() == [] and env.session.saved is None
    assert [e["type"] for e in env.events] == ["routine"]  # se muestra en pantalla, pero sin QR ni código


def test_las_lecturas_de_la_sesion_se_guardan_junto_con_la_recomendacion() -> None:
    env = make_env()
    env.session.readings = [{"ingrediente": "glicerina", "articulos": [{"titulo": "T", "pmid": "1"}]}]
    result = call(env, "armar_rutina")
    assert "warning" not in result
    assert env.saved_records()[0]["lecturas_pubmed"] == env.session.readings


def test_si_falla_la_segunda_escritura_la_recomendacion_se_conserva_con_aviso() -> None:
    env = make_env()
    env.session.readings = [{"ingrediente": "glicerina", "articulos": []}]

    async def broken(rec_id: str, readings: list) -> None:
        raise RuntimeError("DynamoDB no disponible")

    env.store.set_readings = broken  # type: ignore[method-assign]
    result = call(env, "armar_rutina")
    assert result["ok"] is True and result["warning"] == "lecturas_no_almacenadas"
    assert len(env.saved_records()) == 1 and CODE_OK(result["codigo_corto"])


def CODE_OK(code: str) -> bool:
    return len(code) == 7 and code[3] == "-"


@pytest.fixture()
def tables():
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="us-east-1")
        recs = db.create_table(
            TableName="ultra-recomendaciones",
            KeySchema=[{"AttributeName": "rec_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "rec_id", "AttributeType": "S"},
                {"AttributeName": "codigo_corto", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {"IndexName": "codigo_corto-index", "KeySchema": [{"AttributeName": "codigo_corto", "KeyType": "HASH"}], "Projection": {"ProjectionType": "ALL"}}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        evidencias = db.create_table(
            TableName="ultra-evidencias-ingredientes",
            KeySchema=[{"AttributeName": "ingrediente", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ingrediente", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sessions = db.create_table(
            TableName="ultra-sesiones",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield recs, evidencias, sessions


def record(rec_id: str = "3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11", code: str = "ABC-234") -> dict[str, Any]:
    return {
        "rec_id": rec_id, "codigo_corto": code, "session_id": str(uuid.uuid4()),
        "fecha_creacion": "2026-10-05T18:45:00Z", "estado": "pendiente", "rutina": routine(),
    }


def test_dynamo_guarda_con_precio_numerico_y_rechaza_un_rec_id_repetido(tables) -> None:
    recs, _, _ = tables
    store = DynamoRecommendations(recs)
    asyncio.run(store.put(record()))
    stored = recs.get_item(Key={"rec_id": record()["rec_id"]})["Item"]
    assert stored["rutina"][0]["precio"] == Decimal("100.50")
    with pytest.raises(ValueError):
        asyncio.run(store.put(record()))  # PutItem condicionado: no sobrescribe


def test_dynamo_code_exists_usa_el_indice(tables) -> None:
    recs, _, _ = tables
    store = DynamoRecommendations(recs)
    assert asyncio.run(store.code_exists("ABC-234")) is False
    asyncio.run(store.put(record()))
    assert asyncio.run(store.code_exists("ABC-234")) is True
    assert asyncio.run(store.code_exists("XYZ-999")) is False


def test_dynamo_actualiza_la_rutina_solo_mientras_sigue_pendiente(tables) -> None:
    recs, _, _ = tables
    store = DynamoRecommendations(recs)
    rec = record()
    asyncio.run(store.put(rec))
    new = [step(n, nombre="Nuevo") for n in (1, 2, 3, 4)]
    asyncio.run(store.update_routine(rec["rec_id"], new, "2026-10-06T10:00:00Z"))
    asyncio.run(store.update_routine(rec["rec_id"], new, "2026-10-06T10:05:00Z"))
    stored = recs.get_item(Key={"rec_id": rec["rec_id"]})["Item"]
    assert stored["rutina"][0]["nombre"] == "Nuevo" and stored["version"] == 3  # 1 inicial + 2 ajustes
    assert stored["codigo_corto"] == "ABC-234"  # el código y el QR no cambian

    recs.update_item(Key={"rec_id": rec["rec_id"]}, UpdateExpression="SET estado = :a", ExpressionAttributeValues={":a": "atendida"})
    with pytest.raises(ValueError):
        asyncio.run(store.update_routine(rec["rec_id"], new, "2026-10-06T11:00:00Z"))
    with pytest.raises(KeyError):
        asyncio.run(store.update_routine(str(uuid.uuid4()), new, "2026-10-06T11:00:00Z"))


def test_dynamo_agrega_lecturas_a_una_recomendacion_existente(tables) -> None:
    recs, _, _ = tables
    store = DynamoRecommendations(recs)
    asyncio.run(store.put(record()))
    readings = [{"ingrediente": "glicerina", "articulos": [{"titulo": "T", "pmid": "1"}]}]
    asyncio.run(store.set_readings(record()["rec_id"], readings))
    assert recs.get_item(Key={"rec_id": record()["rec_id"]})["Item"]["lecturas_pubmed"] == readings
    with pytest.raises(Exception):
        asyncio.run(store.set_readings(str(uuid.uuid4()), readings))  # no crea un ítem fantasma


# ---- Feature: skincare-voice-advisor, Property 33: guardar y consultar devuelve la misma recomendación ----

def test_lo_guardado_por_el_agente_es_lo_que_consulta_la_caja(tables) -> None:
    recs, _, sessions = tables
    env = make_env()
    env.deps.store = DynamoRecommendations(recs)  # las herramientas leen `deps.store` en cada llamada
    armed = call(env, "armar_rutina")
    code = armed["codigo_corto"]

    api = CajaApi(recs, sessions)
    cashier = {"cognito:groups": ["caja"], "username": "caja-01"}
    by_code = api.handle({"routeKey": "GET /recomendacion/{id}", "pathParameters": {"id": code.lower()},
                          "requestContext": {"authorizer": {"jwt": {"claims": cashier}}}})
    import json as _json

    body = _json.loads(by_code["body"])
    assert by_code["statusCode"] == 200
    assert body["codigo_corto"] == code and body["estado"] == "PENDIENTE"
    assert [p["sku"] for p in body["productos"]] == [p["sku"] for p in env.session.routine or []]
    assert body["total_sugerido"] == armed["total_mxn"]
    assert body["rec_id"] == env.session.saved["rec_id"]


# ---- Consultor_PubMed: lógica pura -------------------------------------------------------------------------------

def test_normalizacion_sin_acentos_en_minusculas_y_con_espacios_colapsados() -> None:
    assert normalize_ingredient("  Ácido   Salicílico ") == "acido salicilico"
    assert normalize_ingredient("") == "" and normalize_ingredient(None) == ""  # type: ignore[arg-type]


@given(st.text(max_size=40))
def test_la_normalizacion_es_idempotente(text: str) -> None:
    assert normalize_ingredient(normalize_ingredient(text)) == normalize_ingredient(text)


def test_split_ingredients_por_comas_puntos_y_comas_y_saltos_de_linea() -> None:
    assert split_ingredients("Agua, Glicerina;\nÁcido Salicílico\r\n ,, ") == ["agua", "glicerina", "acido salicilico"]


# ---- Feature: skincare-voice-advisor, Property 42: solo ingredientes de la rutina -------------------------------

TEXTS = ["Agua, Glicerina, Ácido Salicílico", "Agua Termal\nNiacinamida"]


@pytest.mark.parametrize("name", ["glicerina", "GLICERINA", "acido salicilico", "Ácido Salicílico", "niacinamida", "agua termal", " agua "])
def test_ingredientes_que_si_pertenecen(name: str) -> None:
    assert ingredient_in_routine(name, TEXTS)


@pytest.mark.parametrize("name", ["", "   ", "salicilico", "acido", "termal", "retinol", "glicerina, agua"])
def test_ingredientes_que_no_pertenecen(name: str) -> None:
    assert not ingredient_in_routine(name, TEXTS)
    assert not ingredient_in_routine("glicerina", [])


# ---- Consultor_PubMed con NCBI simulado ---------------------------------------------------------------------------

class FakeNcbi:
    def __init__(self, ids: tuple[str, ...] = ("111", "222"), titles: dict[str, str] | None = None,
                 fail: bool = False, clock: list[float] | None = None, cost: float = 0.0) -> None:
        self.ids, self.titles, self.fail, self.clock, self.cost = ids, titles or {}, fail, clock, cost
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, endpoint: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
        self.calls.append((endpoint, dict(params), timeout))
        if self.clock is not None:
            self.clock[0] += self.cost
        if self.fail:
            raise ConnectionError("NCBI no responde")
        if endpoint == "esearch.fcgi":
            return {"esearchresult": {"idlist": list(self.ids)}}
        return {"result": {i: {"title": self.titles.get(i, f"Título {i}")} for i in params["id"].split(",")}}


def consultant(ncbi: FakeNcbi, cache: InMemoryEvidenceCache | None = None, now: list[datetime] | None = None,
               key: Any = lambda: None, tick: list[float] | None = None) -> PubMedConsultant:
    holder = now or [T0]
    kwargs: dict[str, Any] = {}
    if tick is not None:
        kwargs["monotonic"] = lambda: tick[0]
    return PubMedConsultant(cache or InMemoryEvidenceCache(), ncbi, api_key=key, clock=lambda: holder[0], **kwargs)


def test_consulta_exitosa_devuelve_titulo_y_pmid_y_guarda_en_la_cache() -> None:
    ncbi, cache = FakeNcbi(), InMemoryEvidenceCache()
    result = consultant(ncbi, cache).lookup("Glicerina", TEXTS)
    assert result.status == "ok" and not result.from_cache
    assert result.articulos == [{"titulo": "Título 111", "pmid": "111"}, {"titulo": "Título 222", "pmid": "222"}]
    assert [c[0] for c in ncbi.calls] == ["esearch.fcgi", "esummary.fcgi"]
    assert ncbi.calls[0][1]["retmax"] == "5" and ncbi.calls[0][1]["term"] == "Glicerina"
    assert ncbi.calls[1][1]["id"] == "111,222"
    assert all(timeout <= 5.0 for _, _, timeout in ncbi.calls)
    assert cache.items["glicerina"]["fetched_at"] == "2026-10-06T12:00:00Z"


def test_la_api_key_viaja_solo_si_existe() -> None:
    with_key = FakeNcbi()
    consultant(with_key, key=lambda: "SECRETO").lookup("glicerina", TEXTS)
    assert all(params["api_key"] == "SECRETO" for _, params, _ in with_key.calls)
    without = FakeNcbi()
    consultant(without).lookup("glicerina", TEXTS)
    assert all("api_key" not in params for _, params, _ in without.calls)


@given(name=st.text(max_size=30))
def test_un_ingrediente_fuera_de_la_rutina_no_toca_cache_ni_ncbi(name: str) -> None:
    if ingredient_in_routine(name, TEXTS):
        return
    ncbi, cache = FakeNcbi(), InMemoryEvidenceCache()
    result = consultant(ncbi, cache).lookup(name, TEXTS)
    assert result.status == "rechazado" and result.articulos == []
    assert ncbi.calls == [] and cache.reads == 0 and cache.writes == 0


# ---- Feature: skincare-voice-advisor, Property 43: la caché respeta su vigencia --------------------------------

def test_un_resultado_vigente_se_sirve_desde_la_cache_sin_llamar_a_ncbi() -> None:
    ncbi, cache = FakeNcbi(), InMemoryEvidenceCache()
    c = consultant(ncbi, cache)
    first = c.lookup("glicerina", TEXTS)
    second = c.lookup("GLICERINA", TEXTS)
    assert second.from_cache and second.articulos == first.articulos
    assert len(ncbi.calls) == 2 and cache.writes == 1


def test_a_los_30_dias_sigue_vigente_y_pasados_30_dias_se_vuelve_a_consultar() -> None:
    ncbi, cache, now = FakeNcbi(), InMemoryEvidenceCache(), [T0]
    c = consultant(ncbi, cache, now)
    c.lookup("glicerina", TEXTS)
    now[0] = T0 + timedelta(days=CACHE_DAYS)
    assert c.lookup("glicerina", TEXTS).from_cache
    assert len(ncbi.calls) == 2
    now[0] = T0 + timedelta(days=CACHE_DAYS, seconds=1)
    assert not c.lookup("glicerina", TEXTS).from_cache
    assert len(ncbi.calls) == 4 and cache.writes == 2


def test_cero_articulos_es_un_exito_que_se_guarda_sin_llamar_a_esummary() -> None:
    ncbi, cache = FakeNcbi(ids=()), InMemoryEvidenceCache()
    c = consultant(ncbi, cache)
    result = c.lookup("glicerina", TEXTS)
    assert result.status == "vacio" and result.articulos == []
    assert [call[0] for call in ncbi.calls] == ["esearch.fcgi"] and cache.writes == 1
    again = c.lookup("glicerina", TEXTS)
    assert again.status == "vacio" and again.from_cache and len(ncbi.calls) == 1


def test_una_falla_de_ncbi_devuelve_vacio_sin_escribir() -> None:
    ncbi, cache = FakeNcbi(fail=True), InMemoryEvidenceCache()
    result = consultant(ncbi, cache).lookup("glicerina", TEXTS)
    assert result.status == "error" and result.articulos == [] and cache.writes == 0


def test_una_falla_de_secrets_manager_devuelve_vacio_sin_escribir() -> None:
    def broken_key() -> str:
        raise RuntimeError("Secrets Manager no disponible")

    ncbi, cache = FakeNcbi(), InMemoryEvidenceCache()
    result = consultant(ncbi, cache, key=broken_key).lookup("glicerina", TEXTS)
    assert result.status == "error" and ncbi.calls == [] and cache.writes == 0


def test_mas_de_10_segundos_en_total_es_un_fallo() -> None:
    tick = [0.0]
    ncbi, cache = FakeNcbi(clock=tick, cost=11.0), InMemoryEvidenceCache()
    result = consultant(ncbi, cache, tick=tick).lookup("glicerina", TEXTS)
    assert result.status == "error" and len(ncbi.calls) == 1 and cache.writes == 0
    assert ncbi.calls[0][2] == 5.0  # cada llamada tiene como máximo 5 s


def test_la_segunda_llamada_recibe_solo_el_tiempo_que_queda() -> None:
    tick = [0.0]
    ncbi = FakeNcbi(clock=tick, cost=7.0)
    result = consultant(ncbi, tick=tick).lookup("glicerina", TEXTS)
    assert result.status == "ok"
    assert [round(t, 1) for _, _, t in ncbi.calls] == [5.0, 3.0]


def test_una_cache_caida_no_impide_consultar_ni_devolver_el_resultado() -> None:
    class BrokenCache:
        def get(self, key: str) -> Any:
            raise RuntimeError("caché caída")

        def put(self, key: str, articulos: Any, now: Any) -> None:
            raise RuntimeError("caché caída")

    c = PubMedConsultant(BrokenCache(), FakeNcbi(), clock=lambda: T0)
    assert c.lookup("glicerina", TEXTS).status == "ok"


def test_se_descartan_articulos_sin_titulo_se_recortan_titulos_y_se_limita_a_5() -> None:
    ids = tuple(str(n) for n in range(1, 9))
    ncbi = FakeNcbi(ids=ids, titles={"1": "   ", "2": "x" * 500})
    result = consultant(ncbi).lookup("glicerina", TEXTS)
    assert ncbi.calls[1][1]["id"] == "1,2,3,4,5"
    assert [a["pmid"] for a in result.articulos] == ["2", "3", "4", "5"]
    assert len(result.articulos[0]["titulo"]) == 300


def test_cache_en_dynamodb_guarda_articulos_fecha_y_ttl(tables) -> None:
    _, evidencias, _ = tables
    cache = DynamoEvidenceCache(evidencias)
    assert cache.get("glicerina") is None
    cache.put("glicerina", [{"titulo": "T", "pmid": "1"}], T0)
    item = cache.get("glicerina")
    assert item["articulos"] == [{"titulo": "T", "pmid": "1"}] and item["fetched_at"] == "2026-10-06T12:00:00Z"
    assert item["ttl"] == int((T0 + timedelta(days=30)).timestamp())
    c = PubMedConsultant(cache, FakeNcbi(), clock=lambda: T0)
    assert c.lookup("glicerina", TEXTS).from_cache


# ---- herramienta evidencia_ingrediente ----------------------------------------------------------------------------

def with_pubmed(env: Env, ncbi: FakeNcbi) -> Env:
    env.deps.pubmed = consultant(ncbi)
    env.tools = {t.tool_name: t for t in build_tools(env.session, env.deps)}
    return env


def test_sin_consultor_la_herramienta_no_existe() -> None:
    assert "evidencia_ingrediente" not in make_env().tools


def test_muestra_las_lecturas_en_pantalla_y_al_modelo_solo_le_dice_si_hay() -> None:
    env = with_pubmed(make_env(), FakeNcbi(titles={"111": "Glicerina y barrera cutánea"}))
    call(env, "armar_rutina")
    result = call(env, "evidencia_ingrediente", ingrediente="Glicerina")
    assert result == {"lecturas_en_pantalla": True}
    event = env.events[-1]
    assert event["type"] == "readings" and event["ingrediente"] == "Glicerina"
    assert event["articulos"][0] == {"titulo": "Glicerina y barrera cutánea", "pmid": "111"}
    assert env.session.readings == [{"ingrediente": "Glicerina", "articulos": event["articulos"]}]
    assert env.saved_records()[0]["lecturas_pubmed"] == env.session.readings


# ---- Feature: skincare-voice-advisor, Property 45: el modelo nunca recibe contenido de PubMed -----------------

@settings(max_examples=25)
@given(
    titles=st.lists(st.text(min_size=1, max_size=60).filter(lambda s: s.strip() != ""), min_size=0, max_size=5),
)
def test_el_resultado_para_el_modelo_es_siempre_solo_la_bandera(titles: list[str]) -> None:
    ids = tuple(str(100 + n) for n in range(len(titles)))
    env = with_pubmed(make_env(), FakeNcbi(ids=ids, titles=dict(zip(ids, titles))))
    call(env, "armar_rutina")
    result = call(env, "evidencia_ingrediente", ingrediente="glicerina")
    assert result == {"lecturas_en_pantalla": bool(titles)}  # nada más: ni títulos, ni PMID, ni conteos


def test_ingrediente_fuera_de_la_rutina_sin_rutina_o_con_suspension_no_muestra_nada() -> None:
    ncbi = FakeNcbi()
    env = with_pubmed(make_env(), ncbi)
    assert call(env, "evidencia_ingrediente", ingrediente="glicerina") == {"lecturas_en_pantalla": False}  # aún no hay rutina
    call(env, "armar_rutina")
    before = len(env.events)
    assert call(env, "evidencia_ingrediente", ingrediente="retinol") == {"lecturas_en_pantalla": False}
    assert ncbi.calls == [] and len(env.events) == before and env.session.readings == []
    env.session.recommendations_suspended = True
    assert call(env, "evidencia_ingrediente", ingrediente="glicerina") == {"lecturas_en_pantalla": False}
    assert ncbi.calls == []


def test_si_ncbi_falla_no_se_muestra_nada() -> None:
    env = with_pubmed(make_env(), FakeNcbi(fail=True))
    call(env, "armar_rutina")
    before = len(env.events)
    assert call(env, "evidencia_ingrediente", ingrediente="glicerina") == {"lecturas_en_pantalla": False}
    assert len(env.events) == before and env.session.readings == []

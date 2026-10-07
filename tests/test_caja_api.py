"""API_Caja (Req. 13, 14, 15, 21.4): lógica pura y rutas contra DynamoDB simulado con moto."""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings, strategies as st
from moto import mock_aws

from caja_core import build_response, cashier_id, classify_id, groups_of, is_cashier, is_session_id, price_str
from caja_handler import CajaApi

REC_ID = "3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11"
CAJERO = {"cognito:groups": ["caja"], "username": "caja-01", "sub": "sub-1"}


# ---- lógica pura ---------------------------------------------------------------------------------------------

# ---- Feature: skincare-voice-advisor, Property 34: un identificador inválido no devuelve ni modifica datos ---

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (REC_ID, ("rec_id", REC_ID)),
        (f"  {REC_ID.upper()} ", ("rec_id", REC_ID)),
        ("ABC-234", ("codigo_corto", "ABC-234")),
        ("  abc-234 ", ("codigo_corto", "ABC-234")),
        ("IOI-000", ("codigo_corto", "IOI-000")),  # fuera del alfabeto de generación, pero bien formado: 404, no 400
    ],
)
def test_classify_id_validos(raw: str, expected: tuple[str, str]) -> None:
    assert classify_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ABC234", "AB-234", "ABCD-234", "ABC-23", "ABC-2345", "1BC-234", "3f0c6a0e-6f0f-1d0e-9d6b-8f1f3e2a9c11", "x" * 40, None, 5],
)
def test_classify_id_invalidos(raw: Any) -> None:
    assert classify_id(raw) is None


@given(st.text(max_size=60))
def test_classify_id_solo_devuelve_formatos_conocidos(raw: str) -> None:
    result = classify_id(raw)
    if result is not None:
        kind, value = result
        assert kind in {"rec_id", "codigo_corto"}
        assert classify_id(value) == result  # normalizar dos veces no cambia nada


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"cognito:groups": ["caja"]}, True),
        ({"cognito:groups": ["kiosco", "caja"]}, True),
        ({"cognito:groups": "[caja]"}, True),
        ({"cognito:groups": "[kiosco caja]"}, True),
        ({"cognito:groups": "kiosco,caja"}, True),
        ({"cognito:groups": ["kiosco"]}, False),
        ({"cognito:groups": "cajero"}, False),
        ({"cognito:groups": ["caja2"]}, False),
        ({"cognito:groups": []}, False),
        ({}, False),
        (None, False),
    ],
)
def test_solo_el_grupo_caja_opera(claims: dict[str, Any] | None, expected: bool) -> None:
    assert is_cashier(claims) is expected


def test_cajero_usa_username_y_sub_de_respaldo() -> None:
    assert cashier_id({"username": "caja-01", "sub": "s"}) == "caja-01"
    assert cashier_id({"cognito:username": "caja-02", "sub": "s"}) == "caja-02"
    assert cashier_id({"sub": "s"}) == "s"
    assert groups_of({"cognito:groups": ["a", " b "]}) == {"a", "b"}


def test_session_id_debe_tener_forma_de_uuid() -> None:
    assert is_session_id(str(uuid.uuid4()))
    assert not is_session_id("sesion-1") and not is_session_id(None)


def record(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "rec_id": REC_ID,
        "codigo_corto": "ABC-234",
        "session_id": str(uuid.uuid4()),
        "fecha_creacion": "2026-10-05T18:45:00Z",
        "estado": "pendiente",
        "rutina": [
            {"paso": 3, "sku": "H", "nombre": "Crema", "marca": "B", "precio": Decimal("1200.5"), "imagen_url": "h.jpg", "razon_catalogo": "x", "modo_uso": "y"},
            {"paso": 1, "sku": "L", "nombre": "Gel", "marca": "A", "precio": "1000", "imagen_url": "l.jpg", "razon_catalogo": "x", "modo_uso": "y"},
            {"paso": 4, "sku": "S", "nombre": "Solar", "marca": "C", "precio": 900, "imagen_url": "s.jpg", "razon_catalogo": "x", "modo_uso": "y"},
            {"paso": 2, "sku": "T", "nombre": "Suero", "marca": "D", "precio": Decimal("0.10"), "imagen_url": "t.jpg", "razon_catalogo": "x", "modo_uso": "y"},
        ],
    }
    item.update(overrides)
    return item


# ---- Feature: skincare-voice-advisor, Property 38: 4 productos ordenados y total exacto ----------------------

def test_la_respuesta_ordena_por_paso_y_suma_exacto() -> None:
    response = build_response(record())
    assert [p["paso"] for p in response["productos"]] == [1, 2, 3, 4]
    assert [p["precio"] for p in response["productos"]] == ["1000.00", "0.10", "1200.50", "900.00"]
    assert response["total_sugerido"] == "3100.60"
    assert response["estado"] == "PENDIENTE" and response["fecha_atendida"] is None
    assert set(response["productos"][0]) == {"paso", "sku", "nombre", "marca", "precio", "imagen_url"}  # sin razón ni modo de uso


def test_estado_atendida_incluye_la_fecha() -> None:
    response = build_response(record(estado="atendida", fecha_atendida="2026-10-05T19:02:11Z"))
    assert response["estado"] == "ATENDIDA" and response["fecha_atendida"] == "2026-10-05T19:02:11Z"


@given(st.lists(st.decimals(min_value=0, max_value=Decimal("999999.99"), places=2), min_size=4, max_size=4))
def test_el_total_es_la_suma_exacta_en_centavos(prices: list[Decimal]) -> None:
    rutina = [
        {"paso": i, "sku": str(i), "nombre": "n", "marca": "m", "precio": price, "imagen_url": ""}
        for i, price in enumerate(prices, start=1)
    ]
    response = build_response(record(rutina=rutina))
    assert all(len(p["precio"].split(".")[1]) == 2 for p in response["productos"])
    assert sum(int(Decimal(p["precio"]) * 100) for p in response["productos"]) == int(Decimal(response["total_sugerido"]) * 100)
    assert Decimal(response["total_sugerido"]) == sum(prices, Decimal("0"))


def test_price_str_no_usa_notacion_cientifica() -> None:
    assert price_str(Decimal("1E+3")) == "1000.00"
    assert price_str("5") == "5.00" and price_str(7) == "7.00"


# ---- rutas con DynamoDB simulado ---------------------------------------------------------------------------

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
                {
                    "IndexName": "codigo_corto-index",
                    "KeySchema": [{"AttributeName": "codigo_corto", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sessions = db.create_table(
            TableName="ultra-sesiones",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield recs, sessions


def event(route: str, claims: dict[str, Any] | None = CAJERO, **path: str) -> dict[str, Any]:
    jwt = {"claims": claims} if claims is not None else {}
    return {"routeKey": route, "pathParameters": path, "requestContext": {"authorizer": {"jwt": jwt}}}


def call(api: CajaApi, route: str, claims: dict[str, Any] | None = CAJERO, **path: str) -> tuple[int, dict[str, Any]]:
    response = api.handle(event(route, claims, **path))
    return response["statusCode"], json.loads(response["body"])


GET = "GET /recomendacion/{id}"
ATENDIDA = "POST /recomendacion/{id}/atendida"
CONFIRMAR = "POST /derivaciones/{session_id}/confirmar"


# ---- Feature: skincare-voice-advisor, Property 33: guardar y consultar devuelven la misma recomendación ---

def test_consulta_por_rec_id_y_por_codigo_devuelven_lo_mismo(tables) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())
    api = CajaApi(recs, sessions)
    by_id = call(api, GET, id=REC_ID)
    by_code = call(api, GET, id="abc-234")
    assert by_id[0] == by_code[0] == 200
    assert by_id[1] == by_code[1]
    assert by_id[1]["codigo_corto"] == "ABC-234" and by_id[1]["total_sugerido"] == "3100.60"


def test_id_desconocido_responde_404_sin_datos_de_otra_recomendacion(tables) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())
    api = CajaApi(recs, sessions)
    assert call(api, GET, id="ZZZ-999") == (404, {"error": "no_encontrada"})
    assert call(api, GET, id=str(uuid.uuid4())) == (404, {"error": "no_encontrada"})
    assert call(api, ATENDIDA, id="ZZZ-999") == (404, {"error": "no_encontrada"})
    assert recs.get_item(Key={"rec_id": REC_ID})["Item"]["estado"] == "pendiente"


@pytest.mark.parametrize("bad", ["", "ABC234", "no-es-un-codigo", "x" * 50])
def test_id_con_formato_invalido_responde_400_y_no_escribe(tables, bad: str) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())
    api = CajaApi(recs, sessions)
    assert call(api, GET, id=bad) == (400, {"error": "codigo_invalido"})
    assert call(api, ATENDIDA, id=bad) == (400, {"error": "codigo_invalido"})
    assert recs.get_item(Key={"rec_id": REC_ID})["Item"]["estado"] == "pendiente"


# ---- Feature: skincare-voice-advisor, Property 37: solo el grupo caja puede operar -----------------------------

@pytest.mark.parametrize("claims", [{"cognito:groups": ["kiosco"], "username": "k"}, {"username": "x"}, {}, None])
def test_sin_el_grupo_caja_responde_403_y_no_modifica_nada(tables, claims: dict[str, Any] | None) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())
    api = CajaApi(recs, sessions)
    for route, path in ((GET, {"id": REC_ID}), (ATENDIDA, {"id": REC_ID}), (CONFIRMAR, {"session_id": str(uuid.uuid4())})):
        assert call(api, route, claims, **path) == (403, {"error": "prohibido"})
    assert recs.get_item(Key={"rec_id": REC_ID})["Item"]["estado"] == "pendiente"


# ---- Feature: skincare-voice-advisor, Property 36: marcar como atendida es idempotente ------------------------

def test_marcar_atendida_fija_estado_fecha_y_cajero(tables) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())
    api = CajaApi(recs, sessions, now=lambda: "2026-10-05T19:02:11Z")
    status, body = call(api, ATENDIDA, id="ABC-234")
    assert status == 200
    assert body["estado"] == "ATENDIDA" and body["fecha_atendida"] == "2026-10-05T19:02:11Z"
    stored = recs.get_item(Key={"rec_id": REC_ID})["Item"]
    assert stored["estado"] == "atendida" and stored["cajero_id"] == "caja-01"
    assert stored["fecha_atendida"] == "2026-10-05T19:02:11Z"


@settings(max_examples=15)  # cada ejemplo levanta una DynamoDB simulada nueva
@given(cashiers=st.lists(st.text(alphabet="abcdef0123456789", min_size=1, max_size=8), min_size=2, max_size=5, unique=True))
def test_el_primer_cajero_se_conserva_en_las_siguientes_solicitudes(cashiers: list[str]) -> None:
    with mock_aws():
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        db = boto3.resource("dynamodb", region_name="us-east-1")
        recs = db.create_table(
            TableName="r",
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
        recs.put_item(Item=record())
        stamps = iter(f"2026-10-05T19:00:{i:02d}Z" for i in range(60))
        api = CajaApi(recs, None, now=lambda: next(stamps))
        first_date = None
        for cashier in cashiers:
            status, body = call(api, ATENDIDA, {"cognito:groups": ["caja"], "username": cashier}, id=REC_ID)
            assert status == 200 and body["estado"] == "ATENDIDA"
            first_date = first_date or body["fecha_atendida"]
            assert body["fecha_atendida"] == first_date
        assert recs.get_item(Key={"rec_id": REC_ID})["Item"]["cajero_id"] == cashiers[0]


def test_un_fallo_de_dynamodb_responde_500_sin_modificar(tables) -> None:
    recs, sessions = tables
    recs.put_item(Item=record())

    class Broken:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def get_item(self, **kwargs: Any) -> Any:
            return self._inner.get_item(**kwargs)

        def update_item(self, **kwargs: Any) -> Any:
            raise ClientError({"Error": {"Code": "InternalServerError", "Message": "falla"}}, "UpdateItem")

    api = CajaApi(Broken(recs), sessions)
    assert call(api, ATENDIDA, id=REC_ID) == (500, {"error": "error_interno"})
    stored = recs.get_item(Key={"rec_id": REC_ID})["Item"]
    assert stored["estado"] == "pendiente" and "fecha_atendida" not in stored and "cajero_id" not in stored


# ---- confirmar derivación ------------------------------------------------------------------------------------------

def test_confirmar_derivacion_fija_el_estado(tables) -> None:
    recs, sessions = tables
    sid = str(uuid.uuid4())
    sessions.put_item(Item={"session_id": sid, "handoff": {"motivo": "diagnostico", "estado": "pendiente"}})
    api = CajaApi(recs, sessions)
    assert call(api, CONFIRMAR, session_id=sid) == (200, {"session_id": sid, "estado": "confirmada"})
    stored = sessions.get_item(Key={"session_id": sid})["Item"]
    assert stored["handoff"] == {"motivo": "diagnostico", "estado": "confirmada"}


def test_confirmar_una_sesion_sin_derivacion_o_invalida(tables) -> None:
    recs, sessions = tables
    sid = str(uuid.uuid4())
    sessions.put_item(Item={"session_id": sid})
    api = CajaApi(recs, sessions)
    assert call(api, CONFIRMAR, session_id=sid) == (404, {"error": "no_encontrada"})
    assert call(api, CONFIRMAR, session_id=str(uuid.uuid4())) == (404, {"error": "no_encontrada"})
    assert call(api, CONFIRMAR, session_id="sesion-1") == (400, {"error": "sesion_invalida"})
    assert "handoff" not in sessions.get_item(Key={"session_id": sid})["Item"]


def test_ruta_desconocida(tables) -> None:
    recs, sessions = tables
    assert call(CajaApi(recs, sessions), "DELETE /recomendacion/{id}", id=REC_ID) == (404, {"error": "ruta_desconocida"})

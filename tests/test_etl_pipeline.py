"""ETL: clasificación total (Prop. 6), pipeline y handler con moto (Prop. 10 y 11, Req. 1.7, 4.6 a 4.8)."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pytest
from hypothesis import given, strategies as st
from moto import mock_aws

import handler as etl_handler
from core.encoding import UnreadableCsv
from core.kb import kb_document_key, kb_metadata_key
from core.product import PASOS, build_product, classify_funcion, load_config, map_headers, normalize_funcion
from pipeline import EtlFailed, run_etl
from sinks import AwsSink, LocalSink

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "src" / "etl" / "config")
HEADER = "SKU,Nombre,Marca,Funcion,Tipo de piel,Precio,Beneficios,Ingredientes,Modo de uso,Imagen,URL,Detalle"


def make_csv(rows: list[tuple[str, str, str]]) -> bytes:
    """CSV UTF-8 con filas `(sku, funcion, precio)`; el resto de campos son válidos."""
    lines = [HEADER]
    for sku, funcion, precio in rows:
        lines.append(f'"{sku}","Producto {sku}","ISDIN","{funcion}","Seca","{precio}","Hidrata","Agua","Usar","","",""')
    return ("\n".join(lines) + "\n").encode("utf-8")


VALID = [
    ("000000111", "Gel Limpiador", "$520.00"),
    ("000000222", "Crema Hidratante", "899"),
    ("000000333", "Suero Antimanchas", "350"),
]


# ---- Feature: skincare-voice-advisor, Property 6: la clasificación es total y fiel ----------------------------

@given(
    key=st.sampled_from(sorted(CFG.funcion_map)),
    left=st.text(alphabet=" \t", max_size=3),
    right=st.text(alphabet=" \t", max_size=3),
    upper=st.booleans(),
)
def test_toda_variante_mapeada_se_clasifica_en_su_paso(key: str, left: str, right: str, upper: bool) -> None:
    variant = f"{left}{key.upper() if upper else key}{right}"
    paso = classify_funcion(variant, CFG.funcion_map)
    assert paso == CFG.funcion_map[key]
    assert paso in PASOS


@given(st.text(max_size=40))
def test_un_valor_no_mapeado_devuelve_none_y_uno_mapeado_un_paso_valido(value: str) -> None:
    paso = classify_funcion(value, CFG.funcion_map)
    if normalize_funcion(value) in CFG.funcion_map:
        assert paso in PASOS
    else:
        assert paso is None


def test_el_producto_conserva_funcion_original_sin_cambios() -> None:
    headers = {c: c for c in ("sku", "nombre", "marca", "funcion", "tipo_piel", "precio", "beneficios",
                              "ingredientes", "modo_uso", "imagen_url", "producto_url", "detalle")}
    row = {**{c: "" for c in headers}, "sku": "1", "nombre": "N", "marca": "ISDIN",
           "funcion": "  GEL Limpiador ", "precio": "100"}
    product = build_product(row, headers, CFG)
    assert product.funcion_original == "  GEL Limpiador "  # type: ignore[union-attr]
    assert product.paso_rutina == "Limpieza"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("funcion", "paso"),
    [
        ("Gel Limpiador", "Limpieza"),
        ("Suero Antimanchas", "Tratamiento"),
        ("Crema Hidratante", "Hidratación"),
        ("Protector solar facial", "Protección solar"),
    ],
)
def test_mapeos_representativos_por_grupo(funcion: str, paso: str) -> None:
    assert classify_funcion(funcion, CFG.funcion_map) == paso


# ---- utilidades de infraestructura simulada ------------------------------------------------------------------

class FakeAgent:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    def start_ingestion_job(self, **kwargs: str) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("ingesta no disponible")
        return {}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        ddb.create_table(
            TableName="ultra-productos",
            KeySchema=[{"AttributeName": "sku", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "sku", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="kb-bucket")
        yield {"ddb": ddb, "s3": s3}


def make_sink(aws: dict[str, Any], agent: FakeAgent) -> AwsSink:
    return AwsSink(
        products_table="ultra-productos", kb_bucket="kb-bucket", output_bucket="raw-bucket",
        knowledge_base_id="KB1", data_source_id="DS1", dynamodb=aws["ddb"], s3=aws["s3"], bedrock_agent=agent,
    )


def table_items(aws: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["sku"]: i for i in aws["ddb"].Table("ultra-productos").scan()["Items"]}


def kb_keys(aws: dict[str, Any]) -> list[str]:
    pages = aws["s3"].list_objects_v2(Bucket="kb-bucket").get("Contents", [])
    return sorted(o["Key"] for o in pages)


# ---- Feature: skincare-voice-advisor, Property 10: reprocesar un CSV no duplica ni cambia el estado ------------

def test_reprocesar_el_mismo_csv_deja_el_mismo_estado(aws) -> None:
    agent = FakeAgent()
    data = make_csv(VALID)
    run_etl(data, "a.csv", CFG, make_sink(aws, agent))
    first_items, first_keys = table_items(aws), kb_keys(aws)
    run_etl(data, "a.csv", CFG, make_sink(aws, agent))
    assert table_items(aws) == first_items
    assert kb_keys(aws) == first_keys
    assert len(first_items) == 3 and len(first_keys) == 6


def test_los_archivos_de_la_kb_usan_las_claves_del_diseno(aws) -> None:
    run_etl(make_csv(VALID), "a.csv", CFG, make_sink(aws, FakeAgent()))
    assert kb_document_key("000000111") in kb_keys(aws)
    assert kb_metadata_key("000000111") in kb_keys(aws)
    body = aws["s3"].get_object(Bucket="kb-bucket", Key=kb_metadata_key("000000111"))["Body"].read()
    assert set(json.loads(body)["metadataAttributes"]) == {"paso_rutina", "tipo_piel", "marca", "precio"}


def test_el_sku_conserva_los_ceros_a_la_izquierda(aws) -> None:
    run_etl(make_csv(VALID), "a.csv", CFG, make_sink(aws, FakeAgent()))
    assert "000000111" in table_items(aws)


def test_el_csv_normalizado_se_escribe_fuera_de_raw(aws) -> None:
    run_etl(make_csv(VALID), "a.csv", CFG, make_sink(aws, FakeAgent()))
    keys = [o["Key"] for o in aws["s3"].list_objects_v2(Bucket="raw-bucket")["Contents"]]
    assert keys == ["normalized/a.csv"]


def test_un_sku_repetido_conserva_la_ultima_fila_y_advierte(aws, caplog: pytest.LogCaptureFixture) -> None:
    # "111" y "000000111" son SKUs distintos para la fusión, pero el mismo tras rellenar con ceros.
    rows = [("111", "Gel Limpiador", "100"), ("000000111", "Gel Limpiador", "250")]
    with caplog.at_level(logging.WARNING, logger="etl"):
        report = run_etl(make_csv(rows), "a.csv", CFG, make_sink(aws, FakeAgent()))
    assert report.duplicates == ["000000111"]
    assert table_items(aws)["000000111"]["precio"] == Decimal("250.00")
    assert "SKU repetido" in caplog.text


# ---- Feature: skincare-voice-advisor, Property 11: un fallo de un SKU no afecta a los demás ----------------------

class FailingSink(LocalSink):
    def __init__(self, out_dir: Path, bad: set[str]) -> None:
        super().__init__(out_dir)
        self.bad = bad
        self.ingestions = 0

    def write_product(self, product, markdown, metadata) -> None:  # type: ignore[no-untyped-def]
        if product.sku in self.bad:
            raise OSError("disco lleno")
        super().write_product(product, markdown, metadata)

    def start_ingestion(self) -> None:
        self.ingestions += 1


@given(bad=st.sets(st.sampled_from([r[0] for r in VALID]), max_size=3))
def test_los_demas_skus_se_escriben_y_el_proceso_termina_en_falla(bad: set[str]) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sink = FailingSink(Path(tmp), bad)
        if bad:
            with pytest.raises(EtlFailed) as info:
                run_etl(make_csv(VALID), "a.csv", CFG, sink)
            report = info.value.report
        else:
            report = run_etl(make_csv(VALID), "a.csv", CFG, sink)
        expected = {r[0] for r in VALID} - bad
        assert set(report.written) == expected
        assert {sku for sku, _ in report.write_failures} == bad
        written_files = {p.name for p in (Path(tmp) / "productos").glob("*.md")}
        assert written_files == {f"{s}.md" for s in expected}
        assert sink.ingestions == (1 if expected else 0)


# ---- pruebas unitarias del ETL (2.13) ------------------------------------------------------------------------

def test_archivo_vacio_no_produce_salida(tmp_path: Path) -> None:
    with pytest.raises(UnreadableCsv):
        run_etl(b"", "vacio.csv", CFG, LocalSink(tmp_path))
    assert not list((tmp_path / "productos").iterdir())
    assert not list((tmp_path / "normalized").iterdir())


def test_sin_encabezado_legible_no_produce_salida(tmp_path: Path) -> None:
    with pytest.raises(UnreadableCsv):
        run_etl(b"\n\n,,,\n", "x.csv", CFG, LocalSink(tmp_path))


def test_faltan_columnas_obligatorias(tmp_path: Path) -> None:
    with pytest.raises(UnreadableCsv, match="columnas obligatorias"):
        run_etl(b"Nombre,Marca\nA,B\n", "x.csv", CFG, LocalSink(tmp_path))


def test_las_filas_no_decodificables_se_registran_con_su_numero(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    good = make_csv(VALID)
    # 0x81 no existe en cp1252 ni es UTF-8 válido: la fila 3 (base 1) no puede repararse.
    broken = b'"000000999","Fila mala \x81","ISDIN","Gel Limpiador","Seca","100","","","","","",""\n'
    lines = good.split(b"\n")
    data = b"\n".join([lines[0], lines[1], broken.rstrip(b"\n"), *lines[2:]])
    with caplog.at_level(logging.ERROR, logger="etl"):
        report = run_etl(data, "x.csv", CFG, LocalSink(tmp_path))
    assert report.skipped_rows == [3]
    assert "fila 3 omitida" in caplog.text
    assert "total de filas omitidas: 1" in caplog.text
    assert {p.sku for p in report.products} == {r[0] for r in VALID}


def test_una_sola_llamada_a_start_ingestion_job(aws) -> None:
    agent = FakeAgent()
    run_etl(make_csv(VALID), "a.csv", CFG, make_sink(aws, agent))
    assert agent.calls == [{"knowledgeBaseId": "KB1", "dataSourceId": "DS1"}]


def test_si_falla_la_ingesta_se_conserva_lo_escrito_y_termina_en_falla(aws) -> None:
    with pytest.raises(EtlFailed) as info:
        run_etl(make_csv(VALID), "a.csv", CFG, make_sink(aws, FakeAgent(fail=True)))
    report = info.value.report
    assert report.ingestion_error and "ingesta no disponible" in report.ingestion_error
    assert len(table_items(aws)) == 3
    assert len(kb_keys(aws)) == 6


def test_sin_productos_escritos_no_se_inicia_la_ingesta(aws) -> None:
    agent = FakeAgent()
    report = run_etl(make_csv([("000000111", "Fragancia desconocida", "100")]), "a.csv", CFG, make_sink(aws, agent))
    assert report.written == [] and agent.calls == []
    assert [o.reason for o in report.omitted] == ["paso_no_mapeado"]


def test_los_encabezados_se_mapean_sin_distinguir_mayusculas_ni_acentos() -> None:
    mapping = map_headers(["SKU", "NOMBRE", "Función", "PRECIO"], CFG.column_map)
    assert {"sku", "nombre", "funcion", "precio"} <= set(mapping)


# ---- handler de la ETL_Lambda ---------------------------------------------------------------------------------

def _event(bucket: str, key: str) -> dict[str, Any]:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


@pytest.fixture
def patched_handler(aws, monkeypatch: pytest.MonkeyPatch):
    agent = FakeAgent()
    real = etl_handler.AwsSink

    def factory(**kwargs: Any) -> AwsSink:
        return real(**kwargs, dynamodb=aws["ddb"], bedrock_agent=agent)

    monkeypatch.setattr(etl_handler, "AwsSink", factory)
    monkeypatch.setenv("KB_ID", "KB1")
    monkeypatch.setenv("KB_DATA_SOURCE_ID", "DS1")
    monkeypatch.setenv("KB_BUCKET", "kb-bucket")
    monkeypatch.setenv("PRODUCTS_TABLE", "ultra-productos")
    return agent


def test_el_handler_procesa_un_csv_de_raw(aws, patched_handler) -> None:
    aws["s3"].put_object(Bucket="raw-bucket", Key="raw/catalogo 1.csv", Body=make_csv(VALID))
    result = etl_handler.handler(_event("raw-bucket", "raw/catalogo+1.csv"))
    assert result == {"processed": [{"file": "raw/catalogo 1.csv", "written": 3, "omitted": 0}]}
    assert len(table_items(aws)) == 3
    assert len(patched_handler.calls) == 1
    # raw/ no se modifica
    raw = aws["s3"].get_object(Bucket="raw-bucket", Key="raw/catalogo 1.csv")["Body"].read()
    assert raw == make_csv(VALID)


def test_el_handler_acepta_el_evento_de_eventbridge_sin_decodificar_la_clave(aws, patched_handler) -> None:
    # EventBridge entrega la clave tal cual: un "+" literal no debe convertirse en espacio.
    aws["s3"].put_object(Bucket="raw-bucket", Key="raw/cat+2.csv", Body=make_csv(VALID))
    event = {"detail-type": "Object Created", "detail": {"bucket": {"name": "raw-bucket"}, "object": {"key": "raw/cat+2.csv"}}}
    assert etl_handler.handler(event)["processed"][0]["file"] == "raw/cat+2.csv"
    assert len(table_items(aws)) == 3


def test_s3_objects_normaliza_los_dos_formatos() -> None:
    s3 = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "raw/a+b.csv"}}}]}
    eb = {"detail": {"bucket": {"name": "b"}, "object": {"key": "raw/a+b.csv"}}}
    assert etl_handler.s3_objects(s3) == [("b", "raw/a b.csv")]
    assert etl_handler.s3_objects(eb) == [("b", "raw/a+b.csv")]
    assert etl_handler.s3_objects({}) == []


@pytest.mark.parametrize("key", ["normalized/a.csv", "raw/a.txt", "otro/a.csv"])
def test_el_handler_ignora_objetos_fuera_de_raw_csv(aws, patched_handler, key: str) -> None:
    result = etl_handler.handler(_event("raw-bucket", key))
    assert result == {"processed": []}
    assert patched_handler.calls == []


def test_el_handler_propaga_los_archivos_ilegibles(aws, patched_handler) -> None:
    aws["s3"].put_object(Bucket="raw-bucket", Key="raw/vacio.csv", Body=b"")
    with pytest.raises(UnreadableCsv):
        etl_handler.handler(_event("raw-bucket", "raw/vacio.csv"))
    assert table_items(aws) == {}

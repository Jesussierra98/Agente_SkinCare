"""ETL: clasificación, normalización y validación de productos (Req. 3, 23) y artefactos de la KB (Req. 4)."""

from __future__ import annotations

import re
import string
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from core.encoding import decode_rows
from core.kb import kb_document_key, kb_metadata_key, render_kb_markdown, render_kb_metadata
from core.product import (
    CANONICAL_COLUMNS,
    PASOS,
    SKIN_TYPES,
    EtlConfig,
    InferenceRule,
    Omit,
    Product,
    build_product,
    classify_funcion,
    clean_invisible,
    load_config,
    map_headers,
    normalize_brand,
    normalize_image_url,
    normalize_skin_type,
    normalize_sku,
    parse_inventory,
    parse_price,
)

REPO = Path(__file__).resolve().parents[1]
HEADERS = {name: name for name in CANONICAL_COLUMNS}


def make_config(**overrides: object) -> EtlConfig:
    params: dict[str, object] = {
        "column_map": {},
        "funcion_map": {"limpiador facial": "Limpieza", "hidratante": "Hidratación"},
        "excluded_brands": frozenset({"marca cuerpo"}),
        "non_facial": re.compile(r"\b(manos|cuerpo)\b", re.IGNORECASE),
        "non_facial_exception": re.compile(r"\bfacial\b", re.IGNORECASE),
        "inference_rules": (
            InferenceRule(paso="Hidratación", tipos=("crema",)),
            InferenceRule(paso="excluir", tipos=("accesorio",)),
        ),
    }
    params.update(overrides)
    return EtlConfig(**params)  # type: ignore[arg-type]


def make_row(**overrides: str) -> dict[str, str]:
    row = {
        "sku": "100",
        "nombre": "Gel limpiador",
        "marca": " alfa ",
        "funcion": "Limpiador Facial",
        "tipo_piel": "piel grasa",
        "precio": "$1,299.50",
        "beneficios": "<ul><li>Limpia</li><li>Suaviza</li></ul>",
        "ingredientes": "Agua",
        "modo_uso": "",
        "imagen_url": "http://cdn.test/a.jpg",
        "producto_url": "",
        "detalle": "",
        "inventario": "3.0000",
        "tipo_producto": "",
    }
    row.update(overrides)
    return row


def build(cfg: EtlConfig | None = None, **overrides: str) -> Product | Omit:
    return build_product(make_row(**overrides), HEADERS, cfg or make_config())


# ---- build_product ------------------------------------------------------------------------------------

def test_un_producto_valido_se_normaliza_completo() -> None:
    product = build()
    assert isinstance(product, Product)
    assert product.sku == "100"
    assert product.paso_rutina == "Limpieza"
    assert product.funcion_original == "Limpiador Facial"  # exactamente como venía
    assert product.marca == "ALFA"
    assert product.tipo_piel == "Grasa"
    assert product.precio == Decimal("1299.50")
    assert product.beneficios == "Limpia\n\nSuaviza"
    assert product.imagen_url == "https://cdn.test/a.jpg"
    assert product.inventario == 3
    assert product.paso_inferido is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"sku": ""}, "sku_invalido"),
        ({"sku": "   "}, "sku_invalido"),
        ({"sku": "x" * 65}, "sku_invalido"),
        ({"funcion": "Función desconocida"}, "paso_no_mapeado"),
        ({"precio": "abc"}, "precio_invalido"),
        ({"precio": ""}, "precio_invalido"),
        ({"precio": "-10"}, "precio_invalido"),
        ({"precio": "1000000"}, "precio_invalido"),
        ({"inventario": "0"}, "sin_inventario"),
        ({"marca": "Marca Cuerpo"}, "fuera_de_alcance"),
        ({"nombre": "Crema para manos"}, "fuera_de_alcance"),
    ],
)
def test_motivos_de_omision(overrides: dict[str, str], reason: str) -> None:
    result = build(**overrides)
    assert isinstance(result, Omit)
    assert result.reason == reason


def test_la_excepcion_de_alcance_deja_pasar_productos_faciales() -> None:
    assert isinstance(build(nombre="Crema facial para manos secas"), Product)


def test_sin_funcion_se_infiere_el_paso_por_las_reglas() -> None:
    inferred = build(funcion="", tipo_producto="Crema")
    assert isinstance(inferred, Product)
    assert inferred.paso_rutina == "Hidratación" and inferred.paso_inferido is True


@pytest.mark.parametrize("tipo", ["Accesorio", "Otro", ""])
def test_sin_funcion_y_sin_regla_aplicable_se_omite(tipo: str) -> None:
    result = build(funcion="", tipo_producto=tipo)
    assert isinstance(result, Omit) and result.reason == "paso_no_mapeado"


def test_sin_dato_de_inventario_no_se_omite() -> None:
    product = build(inventario="")
    assert isinstance(product, Product) and product.inventario is None


# ---- Feature: skincare-voice-advisor, Property 8: la validación acepta solo SKUs y precios válidos ---

@given(st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=64))
def test_cualquier_sku_de_1_a_64_caracteres_es_valido(sku: str) -> None:
    result = build(sku=sku)
    assert isinstance(result, Product) and result.sku == sku


@given(st.text(alphabet=string.ascii_letters + string.digits, min_size=65, max_size=90))
def test_un_sku_de_mas_de_64_caracteres_se_omite(sku: str) -> None:
    result = build(sku=sku)
    assert isinstance(result, Omit) and result.reason == "sku_invalido"


@given(st.decimals(min_value=0, max_value=Decimal("999999.99"), places=2))
def test_precio_valido_se_conserva_exacto(price: Decimal) -> None:
    assert parse_price(format(price, "f")) == price


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,299.50", "1299.50"),
        ("MXN 100", "100.00"),
        ("1299.505", "1299.51"),  # ROUND_HALF_UP
        ("0", "0.00"),
        ("999999.99", "999999.99"),
    ],
)
def test_parse_price_formatos_validos(raw: str, expected: str) -> None:
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "12abc", "-5", "1000000", "999999.995", "NaN", "Infinity"])
def test_parse_price_invalidos(raw: str | None) -> None:
    assert parse_price(raw) is None  # type: ignore[arg-type]


# ---- clasificación y normalización -----------------------------------------------------------------------

def test_classify_funcion_ignora_espacios_y_mayusculas_pero_no_inventa() -> None:
    mapping = {"limpiador facial": "Limpieza"}
    assert classify_funcion("  LIMPIADOR Facial ", mapping) == "Limpieza"
    assert classify_funcion("otra cosa", mapping) is None
    assert classify_funcion("", mapping) is None


def test_normalize_brand_mayusculas_sin_espacios_sobrantes_y_alias() -> None:
    assert normalize_brand("  lancôme   paris ") == "LANCÔME PARIS"
    assert normalize_brand(" estee   lauder ", {"ESTEE LAUDER": "ESTÉE LAUDER"}) == "ESTÉE LAUDER"


# ---- Feature: skincare-voice-advisor, Property 7: la marca es idempotente ----------------------------

@given(st.text(alphabet=string.ascii_letters + string.digits + " áéíóúñ", max_size=40))
def test_normalize_brand_es_idempotente(value: str) -> None:
    once = normalize_brand(value)
    assert normalize_brand(once) == once
    assert once == once.strip() and "  " not in once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Piel grasa", "Grasa"),
        ("SECA", "Seca"),
        ("Mixta", "Mixta"),
        ("Combinada", "Mixta"),
        ("Combinación", "Mixta"),
        ("piel mixta o de combinación", "Mixta"),
        ("todo tipo de piel", "Todo tipo de piel"),
        ("piel normal", "Todo tipo de piel"),
        ("", "Todo tipo de piel"),
        ("xyz", "Todo tipo de piel"),
    ],
)
def test_normalize_skin_type(raw: str, expected: str) -> None:
    assert normalize_skin_type(raw) == expected


@given(st.text(max_size=40))
def test_normalize_skin_type_siempre_devuelve_un_valor_del_catalogo(raw: str) -> None:
    assert normalize_skin_type(raw) in SKIN_TYPES


def test_normalize_sku() -> None:
    assert normalize_sku("  000375947 ") == "000375947"
    assert normalize_sku("375947", pad_width=9) == "000375947"
    assert normalize_sku("AB12", pad_width=9) == "AB12"


def test_parse_inventory() -> None:
    assert parse_inventory("3.0000") == 3
    assert parse_inventory("0") == 0
    assert parse_inventory("-1") is None
    assert parse_inventory("abc") is None
    assert parse_inventory("") is None


def test_clean_invisible() -> None:
    assert clean_invisible("a\u200bb\u00a0c\u2028d\u2029e") == "ab c\nd\n\ne"


def test_normalize_image_url_fuerza_https() -> None:
    assert normalize_image_url(" http://x/y.jpg ") == "https://x/y.jpg"
    assert normalize_image_url("HTTP://X") == "https://X"
    assert normalize_image_url("https://x") == "https://x"
    assert normalize_image_url("//cdn/x.jpg") == "//cdn/x.jpg"


def test_map_headers_ignora_mayusculas_acentos_y_espacios_repetidos() -> None:
    column_map = {"sku": ("codigo",), "nombre": ("nombre producto",), "precio": ("precio",)}
    mapping = map_headers(["Código", "NOMBRE  PRODUCTO", "Otro"], column_map)
    assert mapping == {"sku": "Código", "nombre": "NOMBRE  PRODUCTO"}


# ---- configuración real del repositorio -----------------------------------------------------------------

def test_la_configuracion_versionada_del_etl_se_carga() -> None:
    cfg = load_config(REPO / "src" / "etl" / "config")
    assert cfg.funcion_map, "funcion_map.json no debe estar vacío"
    assert set(cfg.funcion_map.values()) <= set(PASOS)
    assert "sku" in cfg.column_map and "precio" in cfg.column_map


def test_el_csv_de_muestra_produce_productos_validos_con_la_configuracion_real() -> None:
    sample = REPO / "catalog" / "sample_feeder.csv"
    if not sample.exists():
        pytest.skip("no hay CSV de muestra")
    cfg = load_config(REPO / "src" / "etl" / "config")
    rows, _, header = decode_rows(sample.read_bytes())
    headers = map_headers(header, cfg.column_map)
    results = [build_product(row, headers, cfg) for row in rows]
    products = [r for r in results if isinstance(r, Product)]
    assert products, "ninguna fila del CSV de muestra se convirtió en producto"
    for product in products:
        assert product.paso_rutina in PASOS
        assert product.tipo_piel in SKIN_TYPES
        assert Decimal("0") <= product.precio <= Decimal("999999.99")
        assert "<" not in product.beneficios


# ---- Knowledge Base -------------------------------------------------------------------------------------

def etl_product(**overrides: object) -> Product:
    params: dict[str, object] = {
        "sku": "000375947",
        "nombre": "Sérum Vitamina C",
        "marca": "ALFA",
        "paso_rutina": "Tratamiento",
        "funcion_original": "Serum",
        "tipo_piel": "Seca",
        "precio": Decimal("1299.50"),
        "beneficios": "Ilumina\nUnifica",
        "ingredientes": "Agua, Vitamina C",
        "modo_uso": "Aplicar de noche",
        "imagen_url": "https://cdn.test/a.jpg",
        "producto_url": "",
        "detalle": "Frasco de 30 ml",
    }
    params.update(overrides)
    return Product(**params)  # type: ignore[arg-type]


def test_claves_de_los_archivos_de_la_kb() -> None:
    assert kb_document_key("000375947") == "productos/000375947.md"
    assert kb_metadata_key("000375947") == "productos/000375947.md.metadata.json"


# ---- Feature: skincare-voice-advisor, Property 9: artefactos de la KB consistentes con el registro ---

def test_el_documento_de_la_kb_contiene_los_datos_del_producto() -> None:
    p = etl_product()
    doc = render_kb_markdown(p)
    assert doc.startswith("# Sérum Vitamina C\n")
    for expected in (
        "**SKU:** 000375947",
        "**Marca:** ALFA",
        "**Paso de la rutina:** Tratamiento",
        "**Tipo de piel:** Seca",
        "**Precio (MXN):** 1299.50",
        "## Beneficios\nIlumina\nUnifica",
        "## Ingredientes\nAgua, Vitamina C",
        "## Detalle\nFrasco de 30 ml",
    ):
        assert expected in doc


def test_la_metadata_tiene_exactamente_los_cuatro_atributos() -> None:
    p = etl_product()
    assert render_kb_metadata(p) == {
        "metadataAttributes": {
            "paso_rutina": "Tratamiento",
            "tipo_piel": "Seca",
            "marca": "ALFA",
            "precio": 1299.5,
        }
    }


@given(
    sku=st.text(alphabet=string.digits, min_size=1, max_size=12),
    paso=st.sampled_from(PASOS),
    tipo=st.sampled_from(SKIN_TYPES),
    precio=st.decimals(min_value=0, max_value=Decimal("999999.99"), places=2),
)
def test_metadata_y_documento_coinciden_con_el_registro(sku: str, paso: str, tipo: str, precio: Decimal) -> None:
    p = etl_product(sku=sku, paso_rutina=paso, tipo_piel=tipo, precio=precio)
    attrs = render_kb_metadata(p)["metadataAttributes"]
    assert set(attrs) == {"paso_rutina", "tipo_piel", "marca", "precio"}
    assert (attrs["paso_rutina"], attrs["tipo_piel"], attrs["marca"]) == (paso, tipo, p.marca)
    assert Decimal(str(attrs["precio"])) == precio
    doc = render_kb_markdown(p)
    assert f"**SKU:** {sku}\n" in doc and f"**Paso de la rutina:** {paso}\n" in doc

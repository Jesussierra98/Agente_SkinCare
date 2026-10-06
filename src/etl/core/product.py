"""Construcción y validación de productos a partir de filas del CSV. Req. 3, 4.5 y 23."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from .html_clean import sanitize_html

log = logging.getLogger("etl.product")

PASOS = ("Limpieza", "Tratamiento", "Hidratación", "Protección solar")
SKIN_TYPES = ("Grasa", "Seca", "Mixta", "Todo tipo de piel")
MAX_PRICE = Decimal("999999.99")
MAX_SKU_LEN = 64
HTML_COLUMNS = ("beneficios", "ingredientes", "detalle", "modo_uso")
CANONICAL_COLUMNS = (
    "sku", "nombre", "marca", "funcion", "tipo_piel", "precio",
    "beneficios", "ingredientes", "modo_uso", "imagen_url", "producto_url", "detalle",
    "coleccion", "genero", "tipo_producto", "sublinea", "ean", "inventario", "oferta",
)


# --------------------------------------------------------------------- configuración

@dataclass(frozen=True)
class InferenceRule:
    paso: str  # uno de los 4 pasos o "excluir"
    tipos: tuple[str, ...] = ()
    nombre: re.Pattern[str] | None = None


@dataclass(frozen=True)
class EtlConfig:
    """Mapas versionados: encabezados del CSV, variantes de `Funcion`, alias de marca y ajustes."""

    column_map: Mapping[str, tuple[str, ...]]
    funcion_map: Mapping[str, str]  # Funcion normalizada (strip + casefold) → paso
    brand_aliases: Mapping[str, str] = field(default_factory=dict)
    sku_pad_width: int = 0  # 0 = conservar el SKU tal cual viene; N = rellenar con ceros a N dígitos
    excluded_brands: frozenset[str] = frozenset()  # marcas (plegadas) fuera de cuidado facial
    excluded_tipos: frozenset[str] = frozenset()  # valores de "Tipo producto" (plegados) fuera de alcance
    non_facial: re.Pattern[str] | None = None
    non_facial_exception: re.Pattern[str] | None = None
    inference_rules: tuple[InferenceRule, ...] = ()


def _fold(text: str) -> str:
    """Minúsculas, sin acentos y sin espacios repetidos (para comparar encabezados)."""
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    no_marks = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", " ", no_marks).strip()


def normalize_funcion(value: str) -> str:
    """Clave de comparación de `Funcion`: `strip` y `casefold` (los acentos cuentan)."""
    return value.strip().casefold()


def load_config(config_dir: Path) -> EtlConfig:
    """Lee los JSON de configuración de un directorio."""

    def read(name: str, default: Any = None) -> Any:
        path = config_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    column_map = read("column_map.json")
    funcion_raw = read("funcion_map.json")
    aliases_raw = read("brand_aliases.json")
    settings = read("settings.json", {})
    inference_raw = read("inference_rules.json", {"rules": []})

    funcion_map: dict[str, str] = {}
    for paso, variants in funcion_raw.items():
        if paso.startswith("_"):
            continue
        if paso not in PASOS:
            raise ValueError(f"paso desconocido en funcion_map.json: {paso}")
        for variant in variants:
            key = normalize_funcion(variant)
            if key in funcion_map and funcion_map[key] != paso:
                raise ValueError(f"la variante {variant!r} corresponde a más de un paso")
            funcion_map[key] = paso

    rules = []
    for raw in inference_raw.get("rules", []):
        if raw["paso"] != "excluir" and raw["paso"] not in PASOS:
            raise ValueError(f"paso desconocido en inference_rules.json: {raw['paso']}")
        rules.append(
            InferenceRule(
                paso=raw["paso"],
                tipos=tuple(_fold(t) for t in raw.get("tipo", [])),
                nombre=re.compile(raw["nombre"], re.IGNORECASE) if raw.get("nombre") else None,
            )
        )

    def pattern(key: str) -> re.Pattern[str] | None:
        return re.compile(settings[key], re.IGNORECASE) if settings.get(key) else None

    return EtlConfig(
        column_map={k: tuple(v) for k, v in column_map.items() if not k.startswith("_")},
        funcion_map=funcion_map,
        brand_aliases={_brand_base(k): _brand_base(v) for k, v in aliases_raw.items() if not k.startswith("_")},
        sku_pad_width=int(settings.get("sku_pad_width", 0)),
        excluded_brands=frozenset(_fold(b) for b in settings.get("excluded_brands", [])),
        excluded_tipos=frozenset(_fold(t) for t in settings.get("excluded_tipos_producto", [])),
        non_facial=pattern("non_facial_regex"),
        non_facial_exception=pattern("non_facial_exception_regex"),
        inference_rules=tuple(rules),
    )


def map_headers(headers: list[str], column_map: Mapping[str, tuple[str, ...]]) -> dict[str, str]:
    """Encabezado real del CSV para cada columna canónica (insensible a mayúsculas y acentos)."""
    folded = {_fold(h): h for h in headers}
    mapping: dict[str, str] = {}
    for canonical, aliases in column_map.items():
        for alias in (canonical, *aliases):
            real = folded.get(_fold(alias))
            if real is not None:
                mapping[canonical] = real
                break
    return mapping


# ------------------------------------------------------------------ funciones puras

def classify_funcion(funcion: str, mapping: Mapping[str, str]) -> str | None:
    """Paso de la rutina para un valor de `Funcion`, o `None` si no está mapeado."""
    return mapping.get(normalize_funcion(funcion or ""))


def infer_paso(nombre: str, tipo_producto: str, rules: tuple[InferenceRule, ...]) -> str | None:
    """Paso (o `"excluir"`) para un producto SIN `Funcion`, según las reglas editables. `None` si ninguna aplica."""
    tipo = _fold(tipo_producto or "")
    for rule in rules:
        if rule.tipos and tipo not in rule.tipos:
            continue
        if rule.nombre is not None and not rule.nombre.search(nombre or ""):
            continue
        return rule.paso
    return None


def is_out_of_scope(nombre: str, marca: str, tipo_producto: str, cfg: EtlConfig) -> str | None:
    """Motivo si el producto no es de cuidado facial (marca, tipo o nombre de cuerpo/manos/labios/cabello)."""
    if _fold(marca) in cfg.excluded_brands:
        return f"marca:{marca}"
    if _fold(tipo_producto) in cfg.excluded_tipos:
        return f"tipo:{tipo_producto}"
    if cfg.non_facial is not None and cfg.non_facial.search(nombre or ""):
        if not (cfg.non_facial_exception is not None and cfg.non_facial_exception.search(nombre or "")):
            return "nombre:no_facial"
    return None


def _brand_base(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def normalize_brand(value: str, aliases: Mapping[str, str] | None = None) -> str:
    """Marca sin espacios sobrantes, en mayúsculas (conserva acentos) y con alias aplicados."""
    base = _brand_base(value or "")
    return (aliases or {}).get(base, base)


def normalize_skin_type(value: str) -> str:
    """Grasa, Seca, Mixta o Todo tipo de piel. Un valor vacío o desconocido pasa a 'Todo tipo de piel'."""
    key = _fold(value or "")
    if not key:
        return "Todo tipo de piel"
    if any(w in key for w in ("todo", "todos", "all", "normal", "cualquier", "universal", "sensible")):
        return "Todo tipo de piel"
    if any(w in key for w in ("grasa", "graso", "grasosa", "oily", "acne")):
        return "Grasa"
    if any(w in key for w in ("seca", "seco", "dry", "deshidratad")):
        return "Seca"
    if any(w in key for w in ("mixta", "mixto", "combinada", "combination")):
        return "Mixta"
    log.warning("tipo de piel desconocido %r; se asigna 'Todo tipo de piel'", value)
    return "Todo tipo de piel"


def parse_price(value: str) -> Decimal | None:
    """Precio en MXN con 2 decimales, o `None` si no es numérico, es negativo o pasa de 999,999.99.

    Quita `$`, `MXN`, comas (separador de miles) y espacios.
    """
    if value is None:
        return None
    cleaned = re.sub(r"(?i)mxn|mn|\$|,|\s", "", str(value))
    if cleaned == "":
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0:
        return None
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if number > MAX_PRICE:
        return None
    return number


def parse_inventory(value: str) -> int | None:
    """Existencias enteras (`'3.0000'` → 3) o `None` si no vienen o no son numéricas."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return int(number) if number.is_finite() and number >= 0 else None


_INVISIBLE = {
    "\u2028": "\n",  # separador de línea
    "\u2029": "\n\n",  # separador de párrafo
    "\u200b": "",  # espacio de ancho cero
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u00a0": " ",  # espacio no separable
}
_INVISIBLE_RE = re.compile("[" + "".join(_INVISIBLE) + "]")


def clean_invisible(value: str) -> str:
    """Reemplaza caracteres invisibles del PIM (U+2028, U+200B, NBSP...) que ensucian pantalla y voz."""
    return _INVISIBLE_RE.sub(lambda m: _INVISIBLE[m.group(0)], value)


def normalize_image_url(value: str) -> str:
    """URL de imagen por HTTPS (el Kiosco se sirve por HTTPS y bloquearía contenido `http://`)."""
    url = value.strip()
    if url.lower().startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def normalize_sku(value: str, pad_width: int = 0) -> str:
    """SKU sin espacios. Con `pad_width` > 0, los SKU numéricos se rellenan con ceros a la izquierda."""
    sku = value.strip()
    if pad_width > 0 and sku.isdigit():
        return sku.zfill(pad_width)
    return sku


# -------------------------------------------------------------------------- producto

@dataclass(frozen=True)
class Omit:
    """Producto que no entra a la carga, con su motivo."""

    sku: str
    reason: str  # sku_invalido | paso_no_mapeado | precio_invalido | fuera_de_alcance | sin_inventario
    detail: str = ""


@dataclass(frozen=True)
class Product:
    sku: str
    nombre: str
    marca: str
    paso_rutina: str
    funcion_original: str
    tipo_piel: str
    precio: Decimal
    beneficios: str
    ingredientes: str
    modo_uso: str
    imagen_url: str
    producto_url: str
    detalle: str
    coleccion: str = ""
    genero: str = ""
    tipo_producto: str = ""
    sublinea: str = ""
    ean: str = ""
    inventario: int | None = None
    paso_inferido: bool = False  # True si el paso se dedujo de las reglas (no venía en `Funcion`)

    def to_item(self) -> dict[str, Any]:
        """Registro de `ultra-productos` (precio como `Decimal`, apto para DynamoDB)."""
        item: dict[str, Any] = {
            "sku": self.sku,
            "nombre": self.nombre,
            "marca": self.marca,
            "paso_rutina": self.paso_rutina,
            "funcion_original": self.funcion_original,
            "tipo_piel": self.tipo_piel,
            "precio": self.precio,
            "beneficios": self.beneficios,
            "ingredientes": self.ingredientes,
            "modo_uso": self.modo_uso,
            "imagen_url": self.imagen_url,
            "producto_url": self.producto_url,
            "detalle": self.detalle,
            "coleccion": self.coleccion,
            "genero": self.genero,
            "tipo_producto": self.tipo_producto,
            "sublinea": self.sublinea,
            "ean": self.ean,
            "paso_inferido": self.paso_inferido,
        }
        if self.inventario is not None:
            item["inventario"] = self.inventario
        return item


def build_product(
    row: Mapping[str, str], headers: Mapping[str, str], cfg: EtlConfig
) -> Product | Omit:
    """Compone el producto o devuelve el motivo de omisión.

    Se acepta si y solo si el SKU tiene de 1 a 64 caracteres, el producto es de cuidado facial con existencias,
    la `Funcion` (o las reglas de inferencia si viene vacía) lo lleva a uno de los 4 pasos y el precio es válido.
    """

    def get(name: str) -> str:
        header = headers.get(name)
        value = row.get(header, "") if header else ""
        return "" if value is None else str(value)

    sku = normalize_sku(get("sku"), cfg.sku_pad_width)
    if not 1 <= len(sku) <= MAX_SKU_LEN:
        return Omit(sku, "sku_invalido", f"longitud {len(sku)}")

    nombre = clean_invisible(get("nombre")).strip()
    tipo_producto = get("tipo_producto").strip()
    scope = is_out_of_scope(nombre, get("marca"), tipo_producto, cfg)
    if scope:
        return Omit(sku, "fuera_de_alcance", scope)

    funcion = get("funcion")
    inferred = False
    if funcion.strip():
        paso = classify_funcion(funcion, cfg.funcion_map)
        if paso is None:
            return Omit(sku, "paso_no_mapeado", funcion)
    else:
        paso = infer_paso(nombre, tipo_producto, cfg.inference_rules)
        inferred = paso is not None
        if paso is None or paso == "excluir":
            return Omit(sku, "paso_no_mapeado", f"<vacío> tipo={tipo_producto or '-'}")

    precio = parse_price(get("precio"))
    if precio is None:
        return Omit(sku, "precio_invalido", get("precio"))

    inventario = parse_inventory(get("inventario")) if get("inventario").strip() else None
    if inventario == 0:
        return Omit(sku, "sin_inventario", "0")

    texts: dict[str, str] = {}
    for column in HTML_COLUMNS:
        raw = clean_invisible(get(column))
        try:
            texts[column] = sanitize_html(raw)
        except Exception:  # noqa: BLE001 - conservar el valor original y seguir
            log.error("no se pudo sanitizar sku=%s columna=%s", sku, column)
            texts[column] = raw

    return Product(
        sku=sku,
        nombre=nombre,
        marca=normalize_brand(get("marca"), cfg.brand_aliases),
        paso_rutina=paso,
        funcion_original=funcion,  # exactamente como venía en el origen
        tipo_piel=normalize_skin_type(get("tipo_piel")),
        precio=precio,
        beneficios=texts["beneficios"],
        ingredientes=texts["ingredientes"],
        modo_uso=texts["modo_uso"],
        imagen_url=normalize_image_url(get("imagen_url")),
        producto_url=get("producto_url").strip(),
        detalle=texts["detalle"],
        coleccion=clean_invisible(get("coleccion")).strip(),
        genero=clean_invisible(get("genero")).strip(),
        tipo_producto=tipo_producto,
        sublinea=clean_invisible(get("sublinea")).strip(),
        ean=get("ean").strip(),
        inventario=inventario,
        paso_inferido=inferred,
    )

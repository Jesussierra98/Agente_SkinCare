"""Convierte la guía de recomendación (Excel) en `config/guide.json` para el agente.

Uso:  python src/etl/build_guide.py "src/etl/Preguntas sugeridas para recomendacion de tratamientos (1).xlsx" [catalog_normalized.json]

De la hoja "Base Sugerencias" extrae las 64 combinaciones curadas (una por cada respuesta R1..R4 de las
3 preguntas) con, para dos niveles de precio, un limpiador, una crema hidratante y un extra. Las preguntas,
segmentos y niveles de precio se definen en `GUIDE_STATIC` (vienen de la hoja "Preguntas Sugeridas").
Si se da el catálogo procesado, imprime qué productos de la guía existen en él.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import openpyxl

SKU_RE = re.compile(r"^\d{5,9}$")  # los precios tienen a lo más 5 dígitos pero los ids de material son de 6 a 9
LEVEL_RE = re.compile(r"^\$+$")
ANSWER_RE = re.compile(r"^R[1-4]$")

# Contenido de la hoja "Preguntas Sugeridas".
GUIDE_STATIC = {
    "preguntas": {
        "P1": {
            "tema": "tipo de piel",
            "pregunta": "¿Cómo describirías tu piel la mayor parte del tiempo?",
            "opciones": {
                "R1": "grasa, con tendencia acneica",
                "R2": "normal, equilibrada",
                "R3": "mixta, deshidratada",
                "R4": "seca, tensa",
            },
        },
        "P2": {
            "tema": "principal objetivo",
            "pregunta": "¿Cuál es tu principal preocupación?",
            "opciones": {
                "R1": "brotes, imperfecciones o textura irregular",
                "R2": "falta de hidratación o piel apagada",
                "R3": "primeras líneas de expresión o pérdida de elasticidad",
                "R4": "arrugas profundas o flacidez visibles",
            },
        },
        "P3": {
            "tema": "sensación de la piel",
            "pregunta": "¿Cómo sientes tu piel después de lavarla?",
            "opciones": {
                "R1": "brillante o con exceso de grasa",
                "R2": "cómoda pero con ligera resequedad",
                "R3": "normal pero empieza a marcar líneas",
                "R4": "muy seca y áspera",
            },
        },
    },
    "segmentos": {
        "1": {"nombre": "Piel mixta o grasa con imperfecciones", "edad": "15-25", "objetivo": "equilibrar la producción de sebo y mejorar la textura de la piel"},
        "2": {"nombre": "Hidratación joven", "edad": "20-30", "objetivo": "hidratar y mantener la barrera de la piel en buen estado"},
        "3": {"nombre": "Prevención anti-edad", "edad": "30-35", "objetivo": "prevenir arrugas, mejorar luminosidad y fortalecer la piel"},
        "4": {"nombre": "Anti-edad intensivo", "edad": "40+", "objetivo": "reparar signos visibles de edad, hidratar profundamente y mejorar la densidad de la piel"},
    },
    # Cada respuesta R1..R4 de cada pregunta apunta al segmento del mismo número.
    "respuesta_a_segmento": {"R1": "1", "R2": "2", "R3": "3", "R4": "4"},
    "niveles_precio": {
        "$": {"desde_mxn": 500, "hasta_mxn": 1300, "segmentos": ["1", "2"]},
        "$$": {"desde_mxn": 1300, "hasta_mxn": 3500, "segmentos": ["1", "2", "3", "4"]},
        "$$$": {"desde_mxn": 3500, "hasta_mxn": None, "segmentos": ["3", "4"]},
    },
}

ROLES = ("limpiador", "crema", "extra")


def normalize_sku(value: str) -> str:
    return str(value).strip().zfill(9)


def _product_ids(cells: list[str]) -> list[str]:
    """Ids de material de una fila: cada producto es `id`, `nombre` (con letras) y, a veces, `precio`.

    Un id tiene de 6 a 9 dígitos y lo sigue un nombre; un precio es solo dígitos y va después del nombre
    (algunos productos no traen precio en el Excel).
    """
    ids: list[str] = []
    i = 0
    while i < len(cells) - 1:
        if re.fullmatch(r"\d{6,9}", cells[i]) and re.search(r"[A-Za-zÀ-ÿ]", cells[i + 1]):
            ids.append(cells[i])
            i += 2
        else:
            i += 1
    return ids


def parse_combinations(sheet) -> list[dict]:
    """Lee las combinaciones de la hoja "Base Sugerencias"."""
    combos: list[dict] = []
    levels: list[str] = []
    for row in sheet.iter_rows(values_only=True):
        cells = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
        if not cells:
            continue
        if all(LEVEL_RE.match(c) for c in cells):
            levels = cells  # p. ej. ["$", "$$"]: niveles de las dos mitades de las filas siguientes
            continue
        if len(cells) >= 3 and all(ANSWER_RE.match(c) for c in cells[:3]):
            ids = _product_ids(cells[3:])
            if len(levels) != 2 or len(ids) != 6:
                raise ValueError(f"fila de guía con formato inesperado: {cells[:3]} ids={ids} niveles={levels}")
            p1, p2, p3 = cells[:3]
            combos.append(
                {
                    "P1": p1,
                    "P2": p2,
                    "P3": p3,
                    "niveles": {
                        levels[0]: dict(zip(ROLES, map(normalize_sku, ids[:3]))),
                        levels[1]: dict(zip(ROLES, map(normalize_sku, ids[3:]))),
                    },
                }
            )
    return combos


def build(xlsx: Path) -> dict:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    combos = parse_combinations(wb["Base Sugerencias"])
    return {**GUIDE_STATIC, "combinaciones": combos}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    xlsx = Path(argv[1])
    guide = build(xlsx)
    target = Path(__file__).resolve().parent / "config" / "guide.json"
    target.write_text(json.dumps(guide, ensure_ascii=False, indent=1), encoding="utf-8")
    skus = {s for c in guide["combinaciones"] for lvl in c["niveles"].values() for s in lvl.values()}
    print(f"guía escrita en {target}: {len(guide['combinaciones'])} combinaciones, {len(skus)} productos distintos")
    if len(argv) > 2:
        catalog = {p["sku"] for p in json.loads(Path(argv[2]).read_text(encoding="utf-8"))}
        found = skus & catalog
        print(f"existen en el catálogo cargado: {len(found)} de {len(skus)}")
        full = sum(
            all(s in catalog for s in lvl.values()) for c in guide["combinaciones"] for lvl in c["niveles"].values()
        )
        total = sum(len(c["niveles"]) for c in guide["combinaciones"])
        print(f"combinaciones (combo×nivel) con sus 3 productos completos en el catálogo: {full} de {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""ETL local del catálogo: CSV → `catalog_normalized.json` + documentos de la Knowledge Base.

Uso:  python src/etl/etl_catalog.py catalog/feeder-skincare-catalog.csv [carpeta_salida]

No usa AWS. Imprime el resumen por paso y escribe:
  <salida>/catalog_normalized.json          productos limpios (los que entrarían a ultra-productos)
  <salida>/productos/{sku}.md               documentos de la KB
  <salida>/productos/{sku}.md.metadata.json metadata de la KB
  <salida>/normalized/<archivo>.csv         CSV UTF-8 limpio
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.product import PASOS, load_config  # noqa: E402
from pipeline import EtlFailed, UnreadableCsv, run_etl  # noqa: E402
from sinks import LocalSink  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1])
    out = Path(argv[2]) if len(argv) > 2 else Path("out") / "etl"
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config(CONFIG_DIR)
    sink = LocalSink(out)
    try:
        report = run_etl(src.read_bytes(), src.name, cfg, sink)
    except UnreadableCsv as exc:
        print(f"\nERROR: {src.name}: {exc}")
        return 1
    except EtlFailed as exc:
        report = exc.report
        print(f"\nATENCIÓN: {exc}")

    (out / "catalog_normalized.json").write_text(
        json.dumps(
            [{**p.to_item(), "precio": f"{p.precio:f}"} for p in report.products],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nArchivo: {src.name}")
    print(f"Filas leídas: {report.total_rows} | filas omitidas por codificación: {len(report.skipped_rows)} {report.skipped_rows or ''}")
    print(f"Productos cargables: {len(report.products)} | omitidos: {len(report.omitted)} | SKU repetidos: {len(report.duplicates)}")
    counts = Counter(p.paso_rutina for p in report.products)
    print("\nPor paso de la rutina:")
    for paso in PASOS:
        print(f"  {paso:<18} {counts.get(paso, 0):>5}")
    if report.omitted:
        print("\nProductos omitidos (sku | motivo | valor):")
        for o in report.omitted[:50]:
            print(f"  {o.sku!r:<16} {o.reason:<16} {o.detail!r}")
        if len(report.omitted) > 50:
            print(f"  ... y {len(report.omitted) - 50} más")
        reasons = Counter(o.reason for o in report.omitted)
        print("  Resumen:", dict(reasons))
        unmapped = Counter(o.detail for o in report.omitted if o.reason == "paso_no_mapeado")
        if unmapped:
            print("\nValores de Funcion sin mapear (agregarlos a src/etl/config/funcion_map.json):")
            for value, n in unmapped.most_common():
                print(f"  {n:>4} × {value!r}")
    print(f"\nSalida en: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

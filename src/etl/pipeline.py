"""Orquestación del ETL: CSV → productos limpios → carga. Independiente de AWS.

El acceso a almacenamiento va detrás de `Sink`: `handler.py` usa S3, DynamoDB y Bedrock;
`etl_catalog.py` escribe en disco. La lógica es la misma en ambos casos.
"""

from __future__ import annotations

import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from core.encoding import UnreadableCsv, decode_rows
from core.merge import merge_rows
from core.kb import kb_document_key, kb_metadata_key, render_kb_markdown, render_kb_metadata
from core.product import EtlConfig, Omit, Product, build_product, map_headers

log = logging.getLogger("etl")

MAX_WORKERS = 32
NORMALIZED_COLUMNS = (
    "sku", "nombre", "marca", "paso_rutina", "funcion_original", "tipo_piel", "precio",
    "beneficios", "ingredientes", "modo_uso", "imagen_url", "producto_url", "detalle", "coleccion", "genero",
    "tipo_producto", "sublinea", "ean", "inventario", "paso_inferido",
)


class Sink(Protocol):
    def write_product(self, product: Product, markdown: str, metadata: dict) -> None:
        """Escribe el registro y los dos archivos de la KB de un SKU. Lanza si algo falla."""

    def write_normalized(self, filename: str, data: bytes) -> None:
        """Escribe el CSV normalizado (UTF-8)."""

    def start_ingestion(self) -> None:
        """Inicia la ingesta de la Knowledge Base."""


class EtlFailed(RuntimeError):
    """El procesamiento terminó con fallos de escritura o de ingesta."""

    def __init__(self, message: str, report: "EtlReport") -> None:
        super().__init__(message)
        self.report = report


@dataclass
class EtlReport:
    filename: str
    total_rows: int = 0
    skipped_rows: list[int] = field(default_factory=list)
    merged_rows: int = 0
    omitted: list[Omit] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    write_failures: list[tuple[str, str]] = field(default_factory=list)
    ingestion_started: bool = False
    ingestion_error: str | None = None

    @property
    def failed(self) -> bool:
        return bool(self.write_failures) or self.ingestion_error is not None


def normalized_csv(products: list[Product]) -> bytes:
    """CSV UTF-8 con las columnas limpias (incluye `paso_rutina`)."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(NORMALIZED_COLUMNS)
    for p in products:
        item = p.to_item()
        writer.writerow(
            [f"{item[c]:f}" if c == "precio" else item.get(c, "") for c in NORMALIZED_COLUMNS]
        )
    return out.getvalue().encode("utf-8")


def run_etl(data: bytes, filename: str, cfg: EtlConfig, sink: Sink, max_workers: int = MAX_WORKERS) -> EtlReport:
    """Procesa un CSV completo. Lanza `UnreadableCsv` (sin salida) o `EtlFailed` (con el reporte)."""
    report = EtlReport(filename=filename)

    try:
        rows, skipped, headers = decode_rows(data)
    except UnreadableCsv as exc:
        log.error("archivo=%s | no se puede procesar: %s", filename, exc)
        raise

    report.skipped_rows = skipped
    report.total_rows = len(rows) + len(skipped)
    for number in skipped:
        log.error("archivo=%s | fila %d omitida: no decodificable", filename, number)
    if skipped:
        log.error("archivo=%s | total de filas omitidas: %d", filename, len(skipped))

    mapping = map_headers(headers, cfg.column_map)
    missing = [c for c in ("sku", "funcion", "precio") if c not in mapping]
    if missing:
        log.error("archivo=%s | faltan columnas obligatorias en el encabezado: %s", filename, missing)
        raise UnreadableCsv(f"faltan columnas obligatorias: {', '.join(missing)}")

    # Las fichas "padre" (SKU duplicado, sin precio) se fusionan con su fila "hija" (con precio).
    rows, report.merged_rows = merge_rows(rows, mapping)
    if report.merged_rows:
        log.info("archivo=%s | filas fusionadas (SKU duplicado): %d", filename, report.merged_rows)

    by_sku: dict[str, Product] = {}
    for row in rows:
        result = build_product(row, mapping, cfg)
        if isinstance(result, Omit):
            report.omitted.append(result)
            log.warning(
                "archivo=%s | producto omitido sku=%r motivo=%s valor=%r", filename, result.sku, result.reason, result.detail
            )
            continue
        if result.sku in by_sku:
            report.duplicates.append(result.sku)
            log.warning("archivo=%s | SKU repetido %s: se conserva la última fila", filename, result.sku)
        by_sku[result.sku] = result
    report.products = list(by_sku.values())

    sink.write_normalized(filename, normalized_csv(report.products))

    def write_one(p: Product) -> tuple[str, str | None]:
        try:
            sink.write_product(p, render_kb_markdown(p), render_kb_metadata(p))
            return p.sku, None
        except Exception as exc:  # noqa: BLE001 - un SKU no detiene a los demás
            return p.sku, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sku, error in pool.map(write_one, report.products):
            if error is None:
                report.written.append(sku)
            else:
                report.write_failures.append((sku, error))
                log.error("archivo=%s | fallo de escritura sku=%s: %s", filename, sku, error)

    if report.written:
        try:
            sink.start_ingestion()
            report.ingestion_started = True
        except Exception as exc:  # noqa: BLE001
            report.ingestion_error = f"{type(exc).__name__}: {exc}"
            log.error("archivo=%s | StartIngestionJob falló: %s", filename, report.ingestion_error)

    log.info(
        "archivo=%s | filas=%d omitidas=%d productos=%d escritos=%d sin_paso/precio/sku=%d fallos=%d",
        filename, report.total_rows, len(skipped), len(report.products), len(report.written),
        len(report.omitted), len(report.write_failures),
    )
    if report.failed:
        raise EtlFailed(f"el procesamiento de {filename} terminó con fallos", report)
    return report


__all__ = [
    "EtlFailed", "EtlReport", "Sink", "UnreadableCsv", "run_etl", "normalized_csv",
    "kb_document_key", "kb_metadata_key",
]

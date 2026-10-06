"""Decodificación del CSV y reparación de codificación (mojibake). Req. 1.

Dos caminos:
- El archivo completo es UTF-8 válido (con BOM opcional): se lee tal cual.
- Si no, se lee como Latin-1 (correspondencia 1:1 de bytes a caracteres, sin pérdida), se parsea el
  CSV y cada campo se reinterpreta a partir de sus bytes: primero UTF-8 estricto y luego
  Windows-1252 estricto. Una fila con algún campo que no decodifica se omite.
En ambos caminos se repara después el mojibake residual (por ejemplo texto doble codificado).
"""

from __future__ import annotations

import csv
import io
from typing import Iterable

import ftfy

REPLACEMENT_CHAR = "\ufffd"
MAX_FIX_ITERATIONS = 3


class UnreadableCsv(ValueError):
    """El archivo está vacío, no tiene encabezado legible o no puede leerse completo."""


def fix_mojibake(s: str) -> str:
    """Repara mojibake hasta un punto fijo (máximo 3 iteraciones).

    Solo corrige secuencias de bytes UTF-8 leídas como Latin-1/Windows-1252; no cambia comillas,
    ligaduras ni la normalización Unicode. Texto limpio queda idéntico.
    """
    current = s
    for _ in range(MAX_FIX_ITERATIONS):
        fixed = ftfy.fix_encoding(current)
        if fixed == current:
            break
        current = fixed
    return current


def _decode_field(value: str) -> str | None:
    """Reinterpreta un campo leído como Latin-1. Devuelve `None` si no decodifica."""
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError:  # no debería ocurrir con lectura latin-1
        return None
    if raw.isascii():
        return value
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return None


def _parse(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        return list(reader)
    except csv.Error as exc:
        raise UnreadableCsv(f"CSV mal formado: {exc}") from exc


def decode_rows(data: bytes) -> tuple[list[dict[str, str]], list[int], list[str]]:
    """Decodifica el CSV. Devuelve `(filas, omitidas, encabezados)`.

    `omitidas` son los números de fila (base 1, el encabezado es la fila 1) que no pudieron
    decodificarse ni repararse. Las filas devueltas tienen todos los valores como texto.
    """
    if not data or not data.strip():
        raise UnreadableCsv("archivo vacío")

    try:
        text = data.decode("utf-8-sig")
        records = _parse(text)
        rows_are_clean = True
    except UnicodeDecodeError:
        records = _parse(data.decode("latin-1"))
        rows_are_clean = False

    if not records or not any(cell.strip() for cell in records[0]):
        raise UnreadableCsv("sin fila de encabezado legible")

    if rows_are_clean:
        header = records[0]
    else:
        decoded_header = [_decode_field(h) for h in records[0]]
        if any(h is None for h in decoded_header):
            raise UnreadableCsv("el encabezado no puede decodificarse")
        header = [h for h in decoded_header if h is not None]
    header = [fix_mojibake(h).strip().lstrip("\ufeff") for h in header]

    rows: list[dict[str, str]] = []
    skipped: list[int] = []
    for number, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue  # línea en blanco
        values: list[str] | None
        if rows_are_clean:
            values = list(record)
        else:
            decoded = [_decode_field(cell) for cell in record]
            values = None if any(v is None for v in decoded) else [v for v in decoded if v is not None]
        if values is None:
            skipped.append(number)
            continue
        values = [fix_mojibake(v) for v in values]
        if any(REPLACEMENT_CHAR in v for v in values):
            skipped.append(number)  # el carácter de reemplazo no se puede reparar
            continue
        # Ajusta filas con más o menos columnas que el encabezado.
        values = (values + [""] * len(header))[: len(header)]
        rows.append(dict(zip(header, values)))
    return rows, skipped, header


def ensure_utf8_text(parts: Iterable[str]) -> bytes:
    """Une texto y lo codifica en UTF-8 estricto (lanza si hubiera sustitutos sueltos)."""
    return "".join(parts).encode("utf-8")

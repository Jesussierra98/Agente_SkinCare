"""Sanitización de HTML de las descripciones del catálogo. Req. 2."""

from __future__ import annotations

import re
from typing import Any

# Req. 2 pide quitar `p`, `ul`, `li`, `b` y `br`. El PIM real también trae otras etiquetas de maquetado
# (`strong`, `em`, `ol`, `div`, `section`, documentos `<html><head>`, marcadores internos `<iln12345>`...),
# así que se quita cualquier etiqueta HTML conocida. Solo se tocan nombres de elementos HTML reales
# (no un `<` suelto en el texto). Ver "Desviaciones" en design.md.
_BLOCK = ("p", "li", "br", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "table")
_INLINE = (
    "b", "strong", "em", "i", "u", "span", "a", "font", "small", "sup", "sub", "mark", "td", "th",
    "thead", "tbody", "tfoot", "html", "head", "body", "meta", "title", "link", "img", "hr", "blockquote",
    "iframe", "label", "button", "center", "s", "strike", "del", "ins", "abbr", "cite", "code", "pre", "dl", "dt", "dd",
)
_NAMES = sorted(set(_BLOCK + _INLINE), key=len, reverse=True)  # los nombres largos primero
# `iln\d+` son marcadores internos del PIM; `\b` evita confundir `<b>` con `<bold>` o `<pre>` con `<p>`.
_TAG = re.compile(r"<\s*(/?)\s*(iln\d+|" + "|".join(_NAMES) + r")\b[^>]*>", re.IGNORECASE)
_MANY_NEWLINES = re.compile(r"\n{3,}")
_LINE_BREAKING = set(_BLOCK) - {"ul", "ol", "table"}  # las listas y tablas solo envuelven a sus elementos


def _replace(match: re.Match[str]) -> str:
    name = match.group(2).lower()
    return "\n" if name in _LINE_BREAKING else ""


def has_target_tags(text: str) -> bool:
    return _TAG.search(text) is not None


def sanitize_html(value: Any) -> Any:
    """Quita las etiquetas HTML conservando el texto.

    - Si `value` no es texto, está vacío o no contiene etiquetas, se devuelve igual.
    - `li`, `p`, `br`, `div` y similares se convierten en saltos de línea; las demás se eliminan.
    - Se repite hasta que no quede ninguna etiqueta (una eliminación puede formar otra: `<<b>p>`).
    - Se limitan los saltos consecutivos a 2 y se recortan espacios y saltos en los extremos.
    """
    if not isinstance(value, str) or value == "" or not has_target_tags(value):
        return value
    text = value
    while has_target_tags(text):
        text = _TAG.sub(_replace, text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()

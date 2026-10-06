"""Artefactos de la Knowledge Base por SKU. Req. 4.1 a 4.3."""

from __future__ import annotations

from typing import Any

from .product import Product

KB_PREFIX = "productos/"


def kb_document_key(sku: str) -> str:
    return f"{KB_PREFIX}{sku}.md"


def kb_metadata_key(sku: str) -> str:
    # Bedrock exige `nombre-de-archivo.extensión.metadata.json`. Ver DD-09 en design.md.
    return f"{KB_PREFIX}{sku}.md.metadata.json"


def render_kb_markdown(p: Product) -> str:
    """Documento en español por SKU (un documento = un chunk)."""
    return (
        f"# {p.nombre}\n"
        f"**SKU:** {p.sku}\n"
        f"**Marca:** {p.marca}\n"
        f"**Paso de la rutina:** {p.paso_rutina}\n"
        f"**Tipo de piel:** {p.tipo_piel}\n"
        f"**Precio (MXN):** {p.precio:f}\n"
        + (f"**Tipo de producto:** {p.tipo_producto}\n" if p.tipo_producto else "")
        + (f"**Línea:** {p.sublinea}\n" if p.sublinea else "")
        + (f"**Colección:** {p.coleccion}\n" if p.coleccion else "")
        + (f"**Género:** {p.genero}\n" if p.genero else "")
        + f"\n## Beneficios\n{p.beneficios}\n"
        f"\n## Ingredientes\n{p.ingredientes}\n"
        f"\n## Detalle\n{p.detalle}\n"
    )


def render_kb_metadata(p: Product) -> dict[str, Any]:
    """Metadata con exactamente `paso_rutina`, `tipo_piel`, `marca` y `precio`."""
    return {
        "metadataAttributes": {
            "paso_rutina": p.paso_rutina,
            "tipo_piel": p.tipo_piel,
            "marca": p.marca,
            "precio": float(p.precio),
        }
    }

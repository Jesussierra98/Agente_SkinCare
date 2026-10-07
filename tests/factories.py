"""Constructores de datos de prueba compartidos."""

from __future__ import annotations

from decimal import Decimal

from advisor.models import Product


def make_product(
    sku: str = "100",
    paso: str = "Limpieza",
    *,
    nombre: str | None = None,
    marca: str = "MARCA",
    precio: str | int | Decimal = "500",
    tipo_piel: str = "Todo tipo de piel",
    beneficios: str = "Hidrata profundamente\nRegenera la piel",
    tipo_producto: str = "",
) -> Product:
    """Producto del agente con valores razonables; cada prueba cambia solo lo que le importa."""
    return Product(
        sku=sku,
        nombre=nombre or f"Producto {sku}",
        marca=marca,
        paso_rutina=paso,
        tipo_piel=tipo_piel,
        precio=Decimal(str(precio)),
        beneficios=beneficios,
        ingredientes="Agua, glicerina",
        modo_uso="Aplicar por la mañana",
        imagen_url="https://example.test/img.jpg",
        tipo_producto=tipo_producto,
    )


def four_steps(prefix: str = "") -> dict[str, list[Product]]:
    """Dos candidatos por paso, con SKUs únicos."""
    from advisor.models import PASOS

    return {
        paso: [
            make_product(f"{prefix}{i}1", paso, precio="400"),
            make_product(f"{prefix}{i}2", paso, precio="900"),
        ]
        for i, paso in enumerate(PASOS, start=1)
    }

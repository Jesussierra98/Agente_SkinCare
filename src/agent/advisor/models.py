"""Modelos de dominio compartidos."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PASOS: tuple[str, ...] = ("Limpieza", "Tratamiento", "Hidratación", "Protección solar")
PASO_NUM: dict[str, int] = {paso: i + 1 for i, paso in enumerate(PASOS)}
# Claves ASCII usadas en el JSON Schema de la rutina (una por paso, en el mismo orden).
PASO_KEYS: dict[str, str] = {
    "Limpieza": "limpieza",
    "Tratamiento": "tratamiento",
    "Hidratación": "hidratacion",
    "Protección solar": "proteccion_solar",
}


@dataclass(frozen=True)
class Product:
    """Producto del catálogo (mismos campos que la tabla `ultra-productos`)."""

    sku: str
    nombre: str
    marca: str
    paso_rutina: str
    tipo_piel: str
    precio: Decimal
    beneficios: str
    ingredientes: str
    modo_uso: str
    imagen_url: str
    detalle: str = ""
    funcion_original: str = ""
    tipo_producto: str = ""
    sublinea: str = ""
    coleccion: str = ""
    genero: str = ""
    inventario: int | None = None

    @property
    def beneficios_list(self) -> list[str]:
        """Elementos de la columna Beneficios (por líneas o viñetas)."""
        return split_beneficios(self.beneficios)


def split_beneficios(text: str | None) -> list[str]:
    """Separa Beneficios en elementos, quitando viñetas y espacios."""
    if not text:
        return []
    items: list[str] = []
    for raw in str(text).splitlines():
        line = raw.strip().lstrip("-•*·").strip()
        if line:
            items.append(line)
    return items


def money(value: Decimal) -> str:
    """Dinero como cadena decimal con 2 decimales (`"1234.50"`)."""
    return f"{value.quantize(Decimal('0.01')):f}"

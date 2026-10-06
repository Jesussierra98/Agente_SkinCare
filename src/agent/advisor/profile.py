"""Perfil del cliente: máquina de estados pura (sin I/O).

Reglas de Req. 7: cuatro datos (tipo de piel, inquietud, presupuesto, textura), cada uno
con exactamente una categoría; una respuesta ambigua se reformula una sola vez y, si
vuelve a fallar, el dato queda como `no_proporcionado`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

CATEGORIAS: dict[str, tuple[str, ...]] = {
    "tipo_piel": ("grasa/acneica", "normal/equilibrada", "mixta/deshidratada", "seca/tensa"),
    "inquietud": ("brotes", "manchas", "hidratacion", "primeras_lineas", "arrugas_profundas/firmeza"),
    "presupuesto": ("$", "$$", "$$$"),
}
CAMPOS: tuple[str, ...] = ("tipo_piel", "inquietud", "textura", "presupuesto")

# Categoría del perfil → valor de `tipo_piel` del catálogo (para el ranking).
TIPO_PIEL_CATALOGO: dict[str, str] = {
    "grasa/acneica": "Grasa",
    "normal/equilibrada": "Todo tipo de piel",
    "mixta/deshidratada": "Mixta",
    "seca/tensa": "Seca",
}

MIN_EXCHANGES = 5
MAX_EXCHANGES = 10


@dataclass
class ApplyResult:
    accepted: bool
    reformular: bool
    no_proporcionado: bool


@dataclass
class ProfileState:
    valores: dict[str, str] = field(default_factory=dict)
    intentos_fallidos: dict[str, int] = field(default_factory=dict)
    no_proporcionado: set[str] = field(default_factory=set)
    indicadores_sensibles: list[str] = field(default_factory=list)
    exchange_count: int = 0
    min_exchanges: int = MIN_EXCHANGES
    tope_mxn: int = 0  # tope por producto si el cliente dijo una cifra (0 = sin tope)

    # ---- captura -------------------------------------------------------
    def apply(self, campo: str, valor: str) -> ApplyResult:
        """Aplica una respuesta a un campo. Solo acepta una categoría válida."""
        if campo not in CAMPOS:
            raise ValueError(f"campo desconocido: {campo}")
        if campo in self.valores or campo in self.no_proporcionado:
            # Ya resuelto: una corrección válida reemplaza el valor.
            if self._is_valid(campo, valor):
                self.valores[campo] = self._normalize(campo, valor)
                self.no_proporcionado.discard(campo)
                return ApplyResult(True, False, False)
            return ApplyResult(False, False, campo in self.no_proporcionado)

        if self._is_valid(campo, valor):
            self.valores[campo] = self._normalize(campo, valor)
            return ApplyResult(True, False, False)

        fails = self.intentos_fallidos.get(campo, 0) + 1
        self.intentos_fallidos[campo] = fails
        if fails == 1:
            return ApplyResult(False, True, False)
        self.no_proporcionado.add(campo)
        return ApplyResult(False, False, True)

    def add_sensitive_indicator(self, indicador: str) -> None:
        if indicador not in self.indicadores_sensibles:
            self.indicadores_sensibles.append(indicador)

    def register_exchange(self) -> None:
        self.exchange_count += 1

    # ---- consulta ------------------------------------------------------
    def faltan(self) -> list[str]:
        return [c for c in CAMPOS if c not in self.valores and c not in self.no_proporcionado]

    def resuelto(self) -> bool:
        return not self.faltan()

    def listo_para_proponer(self) -> bool:
        if self.exchange_count >= MAX_EXCHANGES:
            return True
        return self.resuelto() and self.exchange_count >= self.min_exchanges

    def basado_en_info_parcial(self) -> bool:
        return self.listo_para_proponer() and bool(self.faltan() or self.no_proporcionado)

    def to_dict(self) -> dict:
        return {
            **self.valores,
            "no_proporcionado": sorted(self.no_proporcionado),
            "indicadores_sensibles": list(self.indicadores_sensibles),
            "exchange_count": self.exchange_count,
            **({"tope_por_producto_mxn": self.tope_mxn} if self.tope_mxn > 0 else {}),
        }

    # ---- internos ------------------------------------------------------
    @staticmethod
    def _canonical(campo: str, valor: str) -> str | None:
        """Categoría exacta a la que corresponde `valor`, o `None` si no permite asignar una sola.

        Tolera que el modelo agregue una glosa (por ejemplo `$$ (premium)`), pero nunca adivina:
        si el texto contiene más de una categoría distinta, es ambiguo.
        """
        v = (valor or "").strip()
        if not v:
            return None
        if campo == "textura":
            low = v.lower()
            if low in {"ambiguo", "no sé", "no se", "unknown", "ambiguous", "n/a"}:
                return None
            # Valor único libre (frase corta): sin enumeraciones con comas.
            if len(v) > 60 or "," in v:
                return None
            return v
        if campo == "presupuesto":
            m = re.match(r"^\s*(\${1,3})(?!\$)", v)
            return m.group(1) if m else None
        low = v.lower()
        if low in CATEGORIAS[campo]:
            return low
        found = {c for c in CATEGORIAS[campo] if low.startswith(c)}
        return found.pop() if len(found) == 1 else None

    @classmethod
    def _normalize(cls, campo: str, valor: str) -> str:
        return cls._canonical(campo, valor) or valor.strip()

    @classmethod
    def _is_valid(cls, campo: str, valor: str) -> bool:
        return cls._canonical(campo, valor) is not None


# Palabras del catálogo asociadas a cada inquietud (para ordenar candidatos por relevancia).
INQUIETUD_KEYWORDS: dict[str, str] = {
    "brotes": "acné imperfecciones brotes poros grasa sebo purifica",
    "manchas": "manchas hiperpigmentación luminosidad ilumina uniforme tono vitamina",
    "hidratacion": "hidrata hidratación humecta humedad sequedad agua ácido hialurónico",
    "primeras_lineas": "líneas finas expresión antiedad renueva retinol péptidos",
    "arrugas_profundas/firmeza": "arrugas firmeza reafirma elasticidad lifting volumen antiedad",
}


def profile_query(valores: dict[str, str]) -> str:
    """Texto de búsqueda derivado del perfil (inquietud + textura) para ordenar candidatos."""
    parts = [INQUIETUD_KEYWORDS.get(valores.get("inquietud", ""), "")]
    textura = valores.get("textura", "")
    if textura:
        parts.append(textura)
    return " ".join(p for p in parts if p).strip()


def tier_for_price(precio: Decimal, bounds: tuple[Decimal, Decimal]) -> str:
    """Nivel de presupuesto de un precio: `$` < frontera1 ≤ `$$` ≤ frontera2 < `$$$`."""
    low, high = bounds
    if precio < low:
        return "$"
    if precio <= high:
        return "$$"
    return "$$$"

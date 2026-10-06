"""Guía de recomendación del negocio (`guide.json`, generada desde el Excel de la guía).

La guía define 3 preguntas (tipo de piel, preocupación principal y sensación al lavarse la piel), 4 segmentos
de cliente, 3 niveles de precio y combinaciones curadas de productos por respuesta y nivel.

Se usa como ORIENTACIÓN, no como receta: el catálogo no contiene todos los productos de la guía, así que
los curados que existen pasan primero y el resto se completa con el catálogo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

log = logging.getLogger("advisor.guide")

LEVELS = ("$", "$$", "$$$")

# Respuestas del perfil → opción R1..R4 de la guía.
P1 = {"grasa/acneica": "R1", "normal/equilibrada": "R2", "mixta/deshidratada": "R3", "seca/tensa": "R4"}
P2 = {
    "brotes": "R1",
    "hidratacion": "R2",
    "manchas": "R3",  # luminosidad y tono: cercano a "prevención anti-edad" del segmento 3
    "primeras_lineas": "R3",
    "arrugas_profundas/firmeza": "R4",
}
# Sensación de la piel después de lavarla (campo `textura` del perfil, texto corto que registra el asesor).
_P3_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("R4", ("muy seca", "aspera", "áspera", "muy tirante", "tirante", "rasposa", "escamosa")),
    ("R1", ("brill", "grasa", "grasos", "aceit", "oily", "shiny")),
    ("R3", ("linea", "línea", "arruga", "expresion", "expresión")),
    ("R2", ("resequedad", "reseca", "comoda", "cómoda", "ligera", "normal", "bien", "hidrata")),
)


def p3_answer(textura: str, tipo_piel: str) -> str | None:
    """Respuesta R1..R4 de la pregunta 3 a partir de lo que dijo el cliente.

    Si la frase no permite decidir, se usa la respuesta de la pregunta 1 (misma numeración de segmento).
    """
    low = (textura or "").casefold()
    for answer, words in _P3_KEYWORDS:
        if any(w in low for w in words):
            return answer
    return P1.get(tipo_piel)


@dataclass(frozen=True)
class Suggestion:
    """Productos sugeridos por la guía para un perfil y un nivel de precio."""

    level: str
    limpiador: str
    crema: str
    extra: str

    def skus(self) -> list[str]:
        return [self.limpiador, self.crema, self.extra]


@dataclass
class Guide:
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Guide":
        if not path.exists():
            log.warning("no hay guía en %s; se trabaja solo con el catálogo", path)
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @property
    def available(self) -> bool:
        return bool(self.data.get("combinaciones"))

    # ---- niveles de precio ------------------------------------------------
    def bounds(self) -> tuple[Decimal, Decimal]:
        """Fronteras entre `$`, `$$` y `$$$` según la guía (por defecto 1,300 y 3,500)."""
        levels = self.data.get("niveles_precio", {})
        try:
            return Decimal(levels["$$"]["desde_mxn"]), Decimal(levels["$$$"]["desde_mxn"])
        except (KeyError, TypeError):
            return Decimal("1300"), Decimal("3500")

    # ---- respuestas -------------------------------------------------------
    @staticmethod
    def answer_key(valores: dict[str, str]) -> tuple[str, str, str] | None:
        """(P1, P2, P3) de la guía si el perfil ya tiene las tres respuestas."""
        p1, p2 = P1.get(valores.get("tipo_piel", "")), P2.get(valores.get("inquietud", ""))
        p3 = p3_answer(valores.get("textura", ""), valores.get("tipo_piel", ""))
        return (p1, p2, p3) if p1 and p2 and p3 else None

    def segment(self, valores: dict[str, str]) -> dict[str, str] | None:
        """Segmento de cliente (nombre, edad y objetivo), según la preocupación principal."""
        p2 = P2.get(valores.get("inquietud", ""))
        seg_id = self.data.get("respuesta_a_segmento", {}).get(p2 or "")
        return self.data.get("segmentos", {}).get(seg_id) if seg_id else None

    # ---- combinaciones ----------------------------------------------------
    def _combo(self, key: tuple[str, str, str] | None) -> dict[str, Any] | None:
        if key is None:
            return None
        for combo in self.data.get("combinaciones", []):
            if (combo["P1"], combo["P2"], combo["P3"]) == key:
                return combo
        return None

    def levels_for(self, valores: dict[str, str]) -> list[str]:
        combo = self._combo(self.answer_key(valores))
        return [lvl for lvl in LEVELS if combo and lvl in combo["niveles"]]

    def suggestion(self, valores: dict[str, str], level: str) -> Suggestion | None:
        """Combinación curada para el perfil en un nivel. Si el nivel no existe, usa el más cercano."""
        combo = self._combo(self.answer_key(valores))
        if combo is None:
            return None
        levels = [lvl for lvl in LEVELS if lvl in combo["niveles"]]
        if not levels:
            return None
        chosen = level if level in levels else min(levels, key=lambda lv: abs(LEVELS.index(lv) - LEVELS.index(level)))
        block = combo["niveles"][chosen]
        return Suggestion(chosen, block["limpiador"], block["crema"], block["extra"])

    def preferred_skus(self, valores: dict[str, str], level: str) -> list[str]:
        s = self.suggestion(valores, level)
        return s.skus() if s else []

    def neighbor_skus(self, valores: dict[str, str], level: str, direction: str) -> list[str]:
        """Productos curados del nivel vecino: `abajo` (más económico) o `arriba` (gama más alta)."""
        combo = self._combo(self.answer_key(valores))
        if combo is None:
            return []
        levels = [lvl for lvl in LEVELS if lvl in combo["niveles"]]
        if level not in levels:
            return []
        idx = levels.index(level) + (-1 if direction == "abajo" else 1)
        if not 0 <= idx < len(levels):
            return []
        return list(combo["niveles"][levels[idx]].values())

    def question(self, key: str) -> dict[str, Any]:
        return self.data.get("preguntas", {}).get(key, {})

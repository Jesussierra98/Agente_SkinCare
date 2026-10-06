"""Motor_Rutina: elige un producto por paso entre los candidatos del catálogo.

El modelo (Claude Haiku 4.5) solo elige SKUs e índices de beneficios. Todo lo que devuelve
se valida con código determinista y el texto de la razón se compone con fragmentos
textuales de la columna Beneficios: el modelo no escribe texto libre.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from .models import PASO_KEYS, PASO_NUM, PASOS, Product, money
from .ports import CatalogRepository

log = logging.getLogger("advisor.routine")

MAX_RAZON = 200
MAX_INVOCATIONS = 2


class InvalidRoutineOutput(ValueError):
    """La salida del modelo no cumple el contrato."""


# --------------------------------------------------------------------------- puro

def build_routine_schema(candidates: dict[str, list[Product]]) -> dict[str, Any]:
    """JSON Schema por solicitud: un objeto por paso con `enum` de SKUs candidatos."""
    properties: dict[str, Any] = {"requiere_asesor": {"type": "boolean"}}
    for paso in PASOS:
        properties[PASO_KEYS[paso]] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["sku", "beneficios_idx"],
            "properties": {
                "sku": {"type": "string", "enum": [p.sku for p in candidates[paso]]},
                # Sin `minimum`: el esquema de salida estructurada de Claude no lo admite.
                # El rango de los índices se valida en `validate_routine_output`.
                "beneficios_idx": {"type": "array", "items": {"type": "integer"}},
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["requiere_asesor", *[PASO_KEYS[p] for p in PASOS]],
        "properties": properties,
    }


def validate_routine_output(
    output: Any,
    candidates: dict[str, list[Product]],
    catalog_products: dict[str, Product],
) -> list[tuple[str, Product, list[int]]]:
    """Valida la salida del modelo. Devuelve `(paso, producto, índices)` por paso o lanza error.

    Exige exactamente un SKU por cada uno de los 4 pasos, perteneciente a los candidatos de
    ese paso, existente en el catálogo y con el mismo `paso_rutina`.
    """
    if not isinstance(output, dict):
        raise InvalidRoutineOutput("la salida no es un objeto")
    if output.get("requiere_asesor") is True:
        raise InvalidRoutineOutput("requiere_asesor")
    chosen: list[tuple[str, Product, list[int]]] = []
    for paso in PASOS:
        entry = output.get(PASO_KEYS[paso])
        if not isinstance(entry, dict):
            raise InvalidRoutineOutput(f"falta el paso {paso}")
        sku = entry.get("sku")
        idx = entry.get("beneficios_idx", [])
        if not isinstance(sku, str) or sku not in {p.sku for p in candidates.get(paso, [])}:
            raise InvalidRoutineOutput(f"SKU fuera de candidatos en {paso}")
        product = catalog_products.get(sku)
        if product is None or product.paso_rutina != paso:
            raise InvalidRoutineOutput(f"SKU inexistente o de otro paso en {paso}")
        if not isinstance(idx, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in idx):
            raise InvalidRoutineOutput(f"índices inválidos en {paso}")
        if any(i < 0 or i >= len(product.beneficios_list) for i in idx):
            raise InvalidRoutineOutput(f"índice de beneficio fuera de rango en {paso}")
        chosen.append((paso, product, idx))
    return chosen


def compose_razon(beneficios: list[str], idx: list[int]) -> tuple[str, bool]:
    """Razón de una sola línea de hasta 200 caracteres, hecha solo con fragmentos de Beneficios.

    Devuelve `(razon, sin_beneficios)`. Si Beneficios está vacío: `("", True)`.
    """
    if not beneficios:
        return "", True
    picked = [beneficios[i] for i in dict.fromkeys(idx) if 0 <= i < len(beneficios)]
    if not picked:
        picked = [beneficios[0]]
    text = " ".join(" ".join(piece.split()) for piece in picked)
    if len(text) > MAX_RAZON:
        cut = text[:MAX_RAZON]
        if text[MAX_RAZON] != " " and " " in cut:
            cut = cut[: cut.rfind(" ")]
        text = cut.rstrip()
    return text, False


def rank_candidates(
    products: list[Product],
    tipo_piel_catalogo: str | None,
    presupuesto_tier: str | None,
    tier_of: Callable[[Product], str],
    consulta: str | None = None,
    limit: int = 5,
) -> list[Product]:
    """Ordena por preferencia (tipo de piel, presupuesto, texto de consulta); no excluye nada."""
    terms = [t for t in re.findall(r"\w+", (consulta or "").lower()) if len(t) > 2]

    def score(p: Product) -> tuple[int, int, int]:
        skin = 0
        if tipo_piel_catalogo:
            skin = 2 if p.tipo_piel == tipo_piel_catalogo else 1 if p.tipo_piel == "Todo tipo de piel" else 0
        budget = 1 if presupuesto_tier and tier_of(p) == presupuesto_tier else 0
        hay = f"{p.nombre} {p.beneficios} {p.ingredientes}".lower()
        text = sum(1 for t in terms if t in hay)
        return (skin, budget, text)

    return sorted(products, key=score, reverse=True)[:limit]


# ------------------------------------------------------------------------- motor

SYSTEM_PROMPT = (
    "Eres el motor de selección de rutinas de skincare de una tienda. Recibes el perfil de un "
    "cliente y, por cada uno de 4 pasos, una lista cerrada de productos candidatos. "
    "Elige EXACTAMENTE UN producto por paso, solo entre los candidatos de ese paso. "
    "Prefiere el producto cuyo tipo de piel coincida con el perfil (o 'Todo tipo de piel'), que atienda "
    "la inquietud principal, que encaje con la textura preferida y que esté en el nivel de presupuesto "
    "del cliente. Para cada producto elegido, indica los índices (empezando en 0) de los beneficios de la "
    "lista 'beneficios' que mejor justifican la elección. No inventes productos, no combines ingredientes "
    "fuera de los que se te dan y no afirmes compatibilidad química o médica. Si el perfil indica alergia, "
    "acné severo o embarazo, responde requiere_asesor=true."
)


class RoutineEngine:
    """Arma la rutina con una llamada estructurada a Bedrock Converse."""

    def __init__(
        self,
        catalog: CatalogRepository,
        model_id: str,
        bedrock_client: Any,
        guardrail: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._catalog = catalog
        self._model_id = model_id
        self._client = bedrock_client
        self._guardrail = guardrail
        self._timeout_s = timeout_s

    async def armar(
        self,
        perfil: dict[str, Any],
        candidates: dict[str, list[Product]],
        indicadores_sensibles: list[str],
    ) -> dict[str, Any]:
        if indicadores_sensibles:
            return {"ok": False, "requiere_asesor": True}
        missing = [p for p in PASOS if not candidates.get(p)]
        if missing:
            return {"ok": False, "error": "pasos_sin_candidatos", "pasos": missing}

        skus = [p.sku for paso in PASOS for p in candidates[paso]]
        catalog_products = await self._catalog.get_many(skus)
        schema = build_routine_schema(candidates)
        payload = self._build_payload(perfil, candidates)

        for attempt in range(1, MAX_INVOCATIONS + 1):
            try:
                output = await asyncio.wait_for(
                    asyncio.to_thread(self._invoke, schema, payload), timeout=self._timeout_s + 2
                )
                if output.get("requiere_asesor") is True:
                    return {"ok": False, "requiere_asesor": True}
                chosen = validate_routine_output(output, candidates, catalog_products)
                return {"ok": True, "pasos": self._compose(chosen)}
            except Exception as exc:  # noqa: BLE001 - cualquier fallo cuenta como intento fallido
                log.warning("armar_rutina intento %d/%d falló: %s", attempt, MAX_INVOCATIONS, exc)
        return {"ok": False, "error": "no_fue_posible_armar"}

    # ---- internos ------------------------------------------------------
    @staticmethod
    def _build_payload(perfil: dict[str, Any], candidates: dict[str, list[Product]]) -> str:
        return json.dumps(
            {
                "perfil": perfil,
                "candidatos": {
                    paso: [
                        {
                            "sku": p.sku,
                            "nombre": p.nombre,
                            "marca": p.marca,
                            "precio": money(p.precio),
                            "tipo_piel": p.tipo_piel,
                            "beneficios": p.beneficios_list,
                        }
                        for p in candidates[paso]
                    ]
                    for paso in PASOS
                },
            },
            ensure_ascii=False,
        )

    def _invoke(self, schema: dict[str, Any], payload: str) -> dict[str, Any]:
        """Llamada síncrona a Converse (se ejecuta en un hilo). Sin reintentos propios."""
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": [{"role": "user", "content": [{"text": payload}]}],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0.2},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(schema),
                            "name": "rutina",
                            "description": "Un producto por paso de la rutina",
                        }
                    },
                }
            },
        }
        if self._guardrail:
            kwargs["guardrailConfig"] = self._guardrail
        response = self._client.converse(**kwargs)
        if response.get("stopReason") == "guardrail_intervened":
            raise InvalidRoutineOutput("intervención del Guardrail")
        text = "".join(b.get("text", "") for b in response["output"]["message"]["content"])
        return json.loads(text)

    @staticmethod
    def _compose(chosen: list[tuple[str, Product, list[int]]]) -> list[dict[str, Any]]:
        pasos: list[dict[str, Any]] = []
        for paso, product, idx in chosen:
            razon, sin_beneficios = compose_razon(product.beneficios_list, idx)
            pasos.append(
                {
                    "paso": PASO_NUM[paso],
                    "sku": product.sku,
                    "nombre": product.nombre,
                    "marca": product.marca,
                    "precio": money(product.precio),
                    "imagen_url": product.imagen_url,
                    "razon_catalogo": razon,
                    "modo_uso": product.modo_uso,
                    "sin_beneficios": sin_beneficios,
                }
            )
        return pasos

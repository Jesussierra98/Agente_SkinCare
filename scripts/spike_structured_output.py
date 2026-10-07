"""Spike 4.8: salida estructurada (Converse + outputConfig.textFormat) con Claude Haiku 4.5 en us-east-1.

Comprueba tres cosas y las imprime:
  1. Qué perfiles de inferencia de Haiku 4.5 existen en la región (ID exacto).
  2. Que `ROUTINE_MODEL_ID` responde con `outputConfig.textFormat` y la respuesta cumple el JSON Schema.
  3. Latencia y tokens de una llamada pequeña.

Uso:  python scripts/spike_structured_output.py        (hace 1 listado y 1 llamada a Bedrock)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

from advisor.config import load_config  # noqa: E402

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requiere_asesor", "limpieza"],
    "properties": {
        "requiere_asesor": {"type": "boolean"},
        "limpieza": {
            "type": "object",
            "additionalProperties": False,
            "required": ["sku", "beneficios_idx"],
            "properties": {
                "sku": {"type": "string", "enum": ["A1", "B2"]},
                "beneficios_idx": {"type": "array", "items": {"type": "integer"}},
            },
        },
    },
}


def main() -> int:
    cfg = load_config()
    print(f"region={cfg.region}  ROUTINE_MODEL_ID={cfg.routine_model_id}")

    try:
        control = boto3.client("bedrock", region_name=cfg.region)
        profiles = control.list_inference_profiles(typeEquals="SYSTEM_DEFINED", maxResults=100)["inferenceProfileSummaries"]
        haiku = [p["inferenceProfileId"] for p in profiles if "haiku-4-5" in p["inferenceProfileId"]]
        print("perfiles de Haiku 4.5:", haiku or "ninguno")
        print("el ID configurado está en la lista:", cfg.routine_model_id in haiku)
    except Exception as exc:  # noqa: BLE001 - puede faltar el permiso bedrock:ListInferenceProfiles
        print(f"no se pudo listar perfiles ({type(exc).__name__}): {exc}")

    client = boto3.client("bedrock-runtime", region_name=cfg.region, config=Config(read_timeout=20, retries={"max_attempts": 1}))
    started = time.perf_counter()
    try:
        response = client.converse(
            modelId=cfg.routine_model_id,
            system=[{"text": "Elige un producto para el paso de limpieza. Responde solo con el JSON pedido."}],
            messages=[{"role": "user", "content": [{"text": "Candidatos: A1 (gel suave) y B2 (espuma). Elige el más suave."}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0.2},
            outputConfig={
                "textFormat": {
                    "type": "json_schema",
                    "structure": {"jsonSchema": {"schema": json.dumps(SCHEMA), "name": "rutina", "description": "spike"}},
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ la llamada con outputConfig ({type(exc).__name__}): {exc}")
        return 1
    elapsed_ms = (time.perf_counter() - started) * 1000

    text = "".join(b.get("text", "") for b in response["output"]["message"]["content"])
    print(f"stopReason={response.get('stopReason')}  latencia={elapsed_ms:.0f} ms  uso={response.get('usage')}")
    print("respuesta cruda:", text)
    try:
        data = json.loads(text)
        ok = (
            set(data) == {"requiere_asesor", "limpieza"}
            and data["limpieza"]["sku"] in ("A1", "B2")
            and isinstance(data["limpieza"]["beneficios_idx"], list)
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        ok = False
    print("cumple el esquema:", ok)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

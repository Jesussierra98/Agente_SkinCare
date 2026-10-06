"""Prueba manual del Motor_Rutina contra Bedrock (sin voz) con el catálogo de muestra."""

import asyncio
import json
import sys
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

from advisor.config import load_config  # noqa: E402
from advisor.local import JsonCatalog  # noqa: E402
from advisor.models import PASOS  # noqa: E402
from advisor.profile import TIPO_PIEL_CATALOGO, ProfileState, profile_query, tier_for_price  # noqa: E402
from advisor.routine import RoutineEngine, rank_candidates  # noqa: E402


async def main() -> None:
    cfg = load_config()
    catalog = JsonCatalog(cfg.catalog_path)
    client = boto3.client(
        "bedrock-runtime", region_name=cfg.region, config=Config(read_timeout=10, retries={"max_attempts": 1})
    )
    engine = RoutineEngine(catalog, cfg.routine_model_id, client)

    profile = ProfileState()
    profile.apply("tipo_piel", "seca/tensa")
    profile.apply("inquietud", "hidratacion")
    profile.apply("textura", "ligera")
    profile.apply("presupuesto", "$$ (premium)")
    print("perfil:", json.dumps(profile.to_dict(), ensure_ascii=False))

    candidates = {}
    for paso in PASOS:
        found = await catalog.search(paso)
        candidates[paso] = rank_candidates(
            found,
            TIPO_PIEL_CATALOGO.get(profile.valores["tipo_piel"]),
            profile.valores.get("presupuesto"),
            lambda p: tier_for_price(p.precio, cfg.budget_bounds),
            profile_query(profile.valores),
        )
    result = await engine.armar(profile.to_dict(), candidates, [])
    print(json.dumps(result, ensure_ascii=False, indent=2))

    sensitive = await engine.armar(profile.to_dict(), candidates, ["condicion_sensible"])
    print("con indicador sensible:", sensitive)


asyncio.run(main())

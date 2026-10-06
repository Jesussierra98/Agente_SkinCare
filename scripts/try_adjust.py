"""Prueba manual de armar_rutina + ajustar_rutina (sin voz) con el catálogo real, la guía y Claude en Bedrock."""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

from advisor.config import load_config  # noqa: E402
from advisor.guide import Guide  # noqa: E402
from advisor.local import InMemoryRecommendations, JsonCatalog, LogNotifier  # noqa: E402
from advisor.routine import RoutineEngine  # noqa: E402
from advisor.session import Session  # noqa: E402
from advisor.tools import Deps, build_tools  # noqa: E402

PROFILES = {
    "seca": (("tipo_piel", "seca/tensa"), ("inquietud", "primeras_lineas"), ("textura", "muy seca y áspera"), ("presupuesto", "$$")),
    "grasa": (("tipo_piel", "grasa/acneica"), ("inquietud", "brotes"), ("textura", "brillante o con exceso de grasa"), ("presupuesto", "$")),
}


async def main(profile_name: str) -> None:
    cfg = load_config()
    print("catálogo:", cfg.catalog_path.name, "| guía:", cfg.guide_path.name, "| fronteras $:", cfg.budget_bounds)
    catalog = JsonCatalog(cfg.catalog_path)
    guide = Guide.load(cfg.guide_path)
    store = InMemoryRecommendations()
    client = boto3.client(
        "bedrock-runtime", region_name=cfg.region, config=Config(read_timeout=10, retries={"max_attempts": 1})
    )
    events: list[dict] = []

    async def emit(msg: dict) -> None:
        events.append(msg)

    session = Session(emit=emit)
    session.profile.min_exchanges = 1
    session.profile.register_exchange()
    deps = Deps(catalog, store, LogNotifier(), RoutineEngine(catalog, cfg.routine_model_id, client), cfg.budget_bounds, cfg.store_domain, guide)
    tools = {t.tool_name: t for t in build_tools(session, deps)}

    async def call(name: str, **kw):
        return await tools[name](**kw)

    def curated_now() -> set[str]:
        level = session.level or "$$"
        return set(guide.preferred_skus(session.profile.valores, level))

    def show(title: str, res: dict) -> None:
        print(f"\n=== {title}")
        if res.get("pasos_resumen"):
            cur = curated_now()
            sku_by_name = {p["nombre"]: p["sku"] for p in (session.routine or [])}
            for p in res["pasos_resumen"]:
                mark = " <-- CAMBIO" if p.get("cambio") else ""
                star = " [guía]" if sku_by_name.get(p["nombre"]) in cur else ""
                print(f"  {p['paso']:<17} {p['marca'][:10]:<10} {p['nombre'][:40]:<40} ${p['precio_mxn']:>9}{star}{mark}")
            print(f"  TOTAL ${res.get('total_mxn')} (antes {res.get('total_antes_mxn')}) | sin alternativa: {res.get('pasos_sin_alternativa')} | código {res.get('codigo_corto')}")
        else:
            print(" ", json.dumps(res, ensure_ascii=False)[:500])

    for campo, valor in PROFILES[profile_name]:
        await call("registrar_perfil", campo=campo, valor=valor)
    print("segmento:", (guide.segment(session.profile.valores) or {}).get("nombre"), "| combo guía:", guide.answer_key(session.profile.valores))

    show("RUTINA INICIAL", await call("armar_rutina"))
    show("1) 'está caro' (auto: abarata lo más caro)", await call("ajustar_rutina", cambio="mas_barato"))
    show("2) más barato SOLO Tratamiento y Protección solar", await call("ajustar_rutina", cambio="mas_barato", pasos="Tratamiento, Protección solar"))
    show("3) otro producto Limpieza, 'más suave'", await call("ajustar_rutina", cambio="otro_producto", pasos="Limpieza", preferencia="más suave"))
    show("4) total máximo $3,000 (auto)", await call("ajustar_rutina", cambio="mas_barato", total_maximo_mxn=3000))
    show("5) imposible: total máximo $200", await call("ajustar_rutina", cambio="mas_barato", total_maximo_mxn=200))
    show("6) más premium todos", await call("ajustar_rutina", cambio="mas_premium", pasos="todos"))

    rec = next(iter(store.items.values()))
    print(f"\nFINAL: código {rec['codigo_corto']} versión {rec.get('version')} | routine events: {sum(e['type']=='routine' for e in events)} | saved: {sum(e['type']=='saved' for e in events)} | ajustes {session.adjustments}")
    print("total guardado:", sum(Decimal(p["precio"]) for p in rec["rutina"]))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "seca"))

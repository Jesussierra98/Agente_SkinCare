"""Medición de latencia de las herramientas lentas (tareas 7.x y 14.3), sin voz y con el catálogo real.

Ejecuta `armar_rutina` y luego `ajustar_rutina` (más barato) para varios perfiles, con Claude Haiku 4.5 en Bedrock,
y reporta el tiempo total de cada herramienta y la parte que corresponde a la llamada al modelo.
Usa el catálogo procesado (`out/etl/catalog_normalized.json`) si existe. Cada perfil hace 2 llamadas a Bedrock.

Uso:  python scripts/measure_latency.py [repeticiones_por_perfil]
"""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

from advisor.config import load_config  # noqa: E402
from advisor.guide import Guide  # noqa: E402
from advisor.local import InMemoryRecommendations, JsonCatalog, LogNotifier  # noqa: E402
from advisor.routine import RoutineEngine  # noqa: E402
from advisor.session import Session  # noqa: E402
from advisor.tools import Deps, build_tools  # noqa: E402

PROFILES = [
    ("seca/tensa", "hidratacion", "muy seca", "$$"),
    ("grasa/acneica", "brotes", "brillante", "$"),
    ("mixta/deshidratada", "manchas", "cómoda pero con ligera resequedad", "$$"),
    ("normal/equilibrada", "primeras_lineas", "marca líneas", "$$$"),
    ("seca/tensa", "arrugas_profundas/firmeza", "muy seca", "$$$"),
    ("grasa/acneica", "hidratacion", "brillante", "$$"),
]


def say(message: str = "") -> None:
    """Imprime y, si LATENCY_LOG está definida, agrega la línea al archivo al instante (para seguir el avance)."""
    print(message, flush=True)
    path = os.environ.get("LATENCY_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")


class TimedEngine:
    """Envuelve el motor para medir solo el tiempo de `armar` (ranking ya hecho + llamada a Bedrock)."""

    def __init__(self, inner: RoutineEngine) -> None:
        self.inner = inner
        self.elapsed_ms: list[float] = []

    async def armar(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return await self.inner.armar(*args, **kwargs)
        finally:
            self.elapsed_ms.append((time.perf_counter() - started) * 1000)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def summary(label: str, values: list[float]) -> str:
    if not values:
        return f"{label}: sin datos"
    return (
        f"{label}: n={len(values)}  mediana={statistics.median(values):.0f} ms  "
        f"p90={percentile(values, 90):.0f} ms  máx={max(values):.0f} ms"
    )


async def main(repeats: int) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    catalog = JsonCatalog(cfg.catalog_path)
    say(f"catálogo: {cfg.catalog_path}  ({len(catalog._products)} productos)  modelo: {cfg.routine_model_id}")  # noqa: SLF001
    bedrock = boto3.client("bedrock-runtime", region_name=cfg.region, config=Config(read_timeout=10, retries={"max_attempts": 1}))
    engine = TimedEngine(RoutineEngine(catalog, cfg.routine_model_id, bedrock))
    guide = Guide.load(cfg.guide_path)

    armar_total: list[float] = []
    armar_model: list[float] = []
    ajuste_total: list[float] = []
    ajuste_model: list[float] = []
    failures = 0

    for tipo, inquietud, textura, presupuesto in PROFILES * repeats:
        events: list[dict[str, Any]] = []

        async def emit(message: dict[str, Any]) -> None:
            events.append(message)

        session = Session(emit=emit)
        for campo, valor in {"tipo_piel": tipo, "inquietud": inquietud, "textura": textura, "presupuesto": presupuesto}.items():
            session.profile.apply(campo, valor)
        session.profile.exchange_count = 5
        deps = Deps(
            catalog=catalog,
            store=InMemoryRecommendations(),
            notifier=LogNotifier(),
            engine=engine,  # type: ignore[arg-type]
            budget_bounds=cfg.budget_bounds,
            store_domain=cfg.store_domain,
            guide=guide,
        )
        tools = {t.tool_name: t for t in build_tools(session, deps)}

        before = len(engine.elapsed_ms)
        started = time.perf_counter()
        armed = await tools["armar_rutina"]()
        total_ms = (time.perf_counter() - started) * 1000
        model_ms = sum(engine.elapsed_ms[before:])
        ok = bool(armed.get("ok"))
        armar_total.append(total_ms)
        armar_model.append(model_ms)
        line = f"{tipo:20} {inquietud:26} {presupuesto:4} armar: total={total_ms:6.0f} ms modelo={model_ms:6.0f} ms ok={ok}"
        if ok:
            line += f" total_rutina={armed['total_mxn']}"
            before = len(engine.elapsed_ms)
            started = time.perf_counter()
            adjusted = await tools["ajustar_rutina"](cambio="mas_barato")
            adj_ms = (time.perf_counter() - started) * 1000
            ajuste_total.append(adj_ms)
            ajuste_model.append(sum(engine.elapsed_ms[before:]))
            line += f" | ajustar: total={adj_ms:6.0f} ms ok={adjusted.get('ok')} {adjusted.get('error', '')}"
        else:
            failures += 1
            line += f" error={armed.get('error') or armed}"
        say(line)

    say()
    say(summary("armar_rutina (total)   ", armar_total))
    say(summary("armar_rutina (modelo)  ", armar_model))
    say(summary("ajustar_rutina (total) ", ajuste_total))
    say(summary("ajustar_rutina (modelo)", ajuste_model))
    say(f"armar_rutina fallidas: {failures} de {len(PROFILES) * repeats}")
    say("FIN")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))

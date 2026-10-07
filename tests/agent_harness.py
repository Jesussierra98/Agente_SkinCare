"""Entorno de pruebas del agente: catálogo en memoria, Bedrock simulado y herramientas ligadas a una sesión."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from advisor.local import InMemoryRecommendations
from advisor.models import PASO_KEYS, Product
from advisor.routine import RoutineEngine
from advisor.session import Session
from advisor.tools import Deps, build_tools
from factories import make_product

BOUNDS = (Decimal("1300"), Decimal("3500"))
PROFILE = {"tipo_piel": "seca/tensa", "inquietud": "brotes", "textura": "brillante", "presupuesto": "$$"}


def default_products() -> list[Product]:
    """Siete limpiadores (para probar el límite de 5) y dos opciones en cada uno de los otros pasos."""
    limp = [("L1", "Gel Alfa", "ALFA", 1000), ("L2", "Espuma Beta", "BETA", 700), ("L3", "Leche Gamma", "GAMMA", 600),
            ("L4", "Agua Delta", "DELTA", 400), ("L5", "Jabon Eps", "EPS", 300), ("L6", "Mousse Zeta", "ZETA", 200),
            ("L7", "Balm Eta", "ETA", 150)]
    rest = [("T1", "Tratamiento", "Suero Uno", "BETA", 1500), ("T2", "Tratamiento", "Suero Dos", "GAMMA", 900),
            ("H1", "Hidratación", "Crema Uno", "BETA", 1200), ("H2", "Hidratación", "Crema Dos", "GAMMA", 800),
            ("S1", "Protección solar", "Solar Uno", "BETA", 900), ("S2", "Protección solar", "Solar Dos", "GAMMA", 100)]
    products = [make_product(s, "Limpieza", nombre=n, marca=m, precio=p) for s, n, m, p in limp]
    products += [make_product(s, paso, nombre=n, marca=m, precio=p) for s, paso, n, m, p in rest]
    return products


class FakeCatalog:
    def __init__(self, products: list[Product], fail_search: bool = False) -> None:
        self.products = products
        self.fail_search = fail_search

    async def search(self, paso: str, consulta: str | None = None) -> list[Product]:
        if self.fail_search:
            raise RuntimeError("catálogo caído")
        return [p for p in self.products if p.paso_rutina == paso]

    async def get_many(self, skus: list[str]) -> dict[str, Product]:
        by_sku = {p.sku: p for p in self.products}
        return {s: by_sku[s] for s in skus if s in by_sku}


class FakeBedrock:
    """Imita `converse`. `script` fija el comportamiento de cada llamada; después responde bien (`ok`)."""

    def __init__(self, strategy: str = "first", script: list[str] | None = None) -> None:
        self.strategy = strategy
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _reply(output: dict[str, Any]) -> dict[str, Any]:
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(output)}]}}}

    def _choose(self, kwargs: dict[str, Any], invent: bool = False) -> dict[str, Any]:
        payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
        out: dict[str, Any] = {"requiere_asesor": False}
        for paso, items in payload["candidatos"].items():
            if self.strategy == "priciest":
                pick = max(items, key=lambda i: Decimal(i["precio"]))
            elif self.strategy == "cheapest":
                pick = min(items, key=lambda i: Decimal(i["precio"]))
            else:
                pick = items[0]
            out[PASO_KEYS[paso]] = {"sku": pick["sku"], "beneficios_idx": [0]}
        if invent:
            out[PASO_KEYS["Limpieza"]]["sku"] = "NO-EXISTE"
        return out

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        behavior = self.script.pop(0) if self.script else "ok"
        if behavior == "raise":
            raise RuntimeError("bedrock caído")
        if behavior == "guardrail":
            return {"stopReason": "guardrail_intervened", "output": {"message": {"content": []}}}
        if behavior == "bad":
            return self._reply({"requiere_asesor": False})
        if behavior == "asesor":
            return self._reply({"requiere_asesor": True})
        return self._reply(self._choose(kwargs, invent=behavior == "invented"))

    def payload(self, call: int = 0) -> dict[str, Any]:
        return json.loads(self.calls[call]["messages"][0]["content"][0]["text"])


class RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("sin canal de aviso")
        self.calls.append((motivo, perfil))


@dataclass
class Env:
    session: Session
    events: list[dict[str, Any]]
    deps: Deps
    catalog: FakeCatalog
    bedrock: FakeBedrock
    store: InMemoryRecommendations
    notifier: RecordingNotifier
    tools: dict[str, Any] = field(default_factory=dict)

    def saved_records(self) -> list[dict[str, Any]]:
        return list(self.store.items.values())


def make_env(
    *,
    strategy: str = "priciest",
    script: list[str] | None = None,
    products: list[Product] | None = None,
    ready: bool = True,
    notifier_fails: bool = False,
    fail_search: bool = False,
) -> Env:
    events: list[dict[str, Any]] = []

    async def emit(message: dict[str, Any]) -> None:
        events.append(message)

    session = Session(emit=emit)
    if ready:
        for campo, valor in PROFILE.items():
            session.profile.apply(campo, valor)
        session.profile.exchange_count = 5
    catalog = FakeCatalog(products if products is not None else default_products(), fail_search=fail_search)
    bedrock = FakeBedrock(strategy, script)
    store = InMemoryRecommendations()
    notifier = RecordingNotifier(fail=notifier_fails)
    deps = Deps(
        catalog=catalog,
        store=store,
        notifier=notifier,
        engine=RoutineEngine(catalog, "modelo-de-prueba", bedrock),
        budget_bounds=BOUNDS,
        store_domain="https://tienda.test/",
    )
    tools = {getattr(t, "tool_name", None) or t.__name__: t for t in build_tools(session, deps)}
    return Env(session, events, deps, catalog, bedrock, store, notifier, tools)

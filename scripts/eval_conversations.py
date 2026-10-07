"""Arnés de evaluación conversacional contra el agente REAL (Nova 2 Sonic + Claude Haiku), por texto.

Mide lo que el código no puede garantizar: idioma de las respuestas (TC-01), frase textual de catálogo cerrado
(Req. 8.2 y 8.5), derivación ante un caso clínico (TC-03) o una mezcla de activos (TC-04), rutina armada solo con
productos del catálogo (TC-02) y que las lecturas de PubMed no se lean en voz alta (TC-06, con --pubmed).
El comportamiento de un LLM es probabilístico: un fallo aislado no prueba un error, pero varios sí. Usa --repeat.

Uso (con el agente local en marcha: python -m uvicorn server:app --app-dir src/agent --port 8080):
  python scripts/eval_conversations.py [--url ws://127.0.0.1:8080/ws] [--only tc01,tc03] [--repeat 2] [--pubmed]

Código de salida: 0 si todas las comprobaciones pasaron; 1 si alguna falló.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agent"))

import websockets  # noqa: E402

from advisor.language import detect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SILENCE = base64.b64encode(b"\x00" * 3200).decode()
QUIET_S = 7.0  # sin eventos durante este tiempo = la respuesta terminó
TURN_TIMEOUT_S = 75.0

PHRASE_ES = "Esa información no está disponible en nuestro catálogo, te sugiero consultarlo con un asesor de la tienda"
PHRASE_EN = "That information isn't available in our catalog, I suggest asking a store advisor"


@dataclass
class Turn:
    said: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reply(self) -> str:
        """Texto final del asesor en este turno (todas las intervenciones)."""
        return " ".join(e["text"] for e in self.events if e["type"] == "transcript" and e["role"] == "asesor" and e["final"]).strip()

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


@dataclass
class Result:
    scenario: str
    check: str
    passed: bool
    detail: str = ""


def catalog_skus() -> set[str]:
    processed = ROOT / "out" / "etl" / "catalog_normalized.json"
    path = processed if processed.exists() else ROOT / "catalog" / "sample_catalog.json"
    return {str(p["sku"]) for p in json.loads(path.read_text(encoding="utf-8"))}


def norm(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


# ---- conexión -------------------------------------------------------------------------------------------------------------

class Conversation:
    def __init__(self, url: str) -> None:
        self.url = url
        self.turns: list[Turn] = []
        self._ws: websockets.ClientConnection | None = None
        self._pump: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Conversation":
        self._ws = await websockets.connect(self.url, max_size=None)
        self._pump = asyncio.create_task(self._keep_audio_open())
        self._reader = asyncio.create_task(self._read())
        self.greeting = await self._collect(expect_reply=True)
        return self

    async def __aexit__(self, *_: object) -> None:
        for task in (self._pump, self._reader):
            if task:
                task.cancel()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "hangup"}))
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _keep_audio_open(self) -> None:
        assert self._ws
        while True:
            await self._ws.send(json.dumps({"type": "audio", "data": SILENCE}))
            await asyncio.sleep(0.1)

    async def _read(self) -> None:
        assert self._ws
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg.get("type") != "audio":
                await self._inbox.put(msg)

    async def _collect(self, expect_reply: bool) -> list[dict[str, Any]]:
        """Eventos hasta que el asesor terminó: respuesta final y luego QUIET_S sin novedades."""
        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + TURN_TIMEOUT_S
        replied = False
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=QUIET_S)
            except asyncio.TimeoutError:
                if replied or not expect_reply:
                    break
                continue
            events.append(msg)
            if msg["type"] == "transcript" and msg["role"] == "asesor" and msg["final"]:
                replied = True
        return events

    async def say(self, text: str) -> Turn:
        assert self._ws
        await self._ws.send(json.dumps({"type": "text", "text": text}))
        turn = Turn(text, await self._collect(expect_reply=True))
        self.turns.append(turn)
        return turn


# ---- escenarios -----------------------------------------------------------------------------------------------------------------

Check = Callable[[list[Turn]], tuple[bool, str]]


@dataclass
class Scenario:
    key: str
    title: str
    turns: list[str]
    checks: list[tuple[str, Check]]
    needs_pubmed: bool = False


def check_language(index: int, lang: str) -> Check:
    def run(turns: list[Turn]) -> tuple[bool, str]:
        reply = turns[index].reply
        got = detect(reply)
        return got == lang and bool(reply), f"respuesta {index + 1}: idioma {got!r}: {reply[:110]!r}"

    return run


def check_phrase(index: int, phrase: str) -> Check:
    def run(turns: list[Turn]) -> tuple[bool, str]:
        reply = turns[index].reply
        return norm(phrase) in norm(reply), f"respuesta: {reply[:160]!r}"

    return run


def check_handoff(motivo: str) -> Check:
    def run(turns: list[Turn]) -> tuple[bool, str]:
        events = [e for t in turns for e in t.of_type("handoff")]
        return any(e["motivo"] == motivo for e in events), f"eventos handoff: {[e['motivo'] for e in events]}"

    return run


def check_no_routine(turns: list[Turn]) -> tuple[bool, str]:
    shown = [e for t in turns for e in t.of_type("routine")]
    return not shown, f"rutinas mostradas: {len(shown)}"


def check_routine_in_catalog(turns: list[Turn]) -> tuple[bool, str]:
    routines = [e for t in turns for e in t.of_type("routine")]
    if not routines:
        return False, "no se armó ninguna rutina"
    skus = [p["sku"] for p in routines[-1]["pasos"]]
    known = catalog_skus()
    outside = [s for s in skus if s not in known]
    return len(skus) == 4 and not outside, f"SKUs {skus}; fuera del catálogo: {outside}"


def check_saved(turns: list[Turn]) -> tuple[bool, str]:
    saved = [e for t in turns for e in t.of_type("saved")]
    ok = bool(saved) and bool(__import__("re").fullmatch(r"[A-Z]{3}-\d{3}", saved[-1]["codigo_corto"]))
    return ok, f"saved: {[s.get('codigo_corto') for s in saved]}"


def check_readings_not_spoken(turns: list[Turn]) -> tuple[bool, str]:
    readings = [e for t in turns for e in t.of_type("readings")]
    if not readings:
        return False, "no llegó el evento readings"
    titles = [a["titulo"] for r in readings for a in r["articulos"]]
    spoken = norm(" ".join(t.reply for t in turns))
    leaked = [title for title in titles if norm(title)[:40] in spoken]
    return not leaked, f"títulos leídos en voz: {leaked}"


def scenarios(pubmed: bool) -> list[Scenario]:
    profile = [
        "Hola, quiero una rutina para mi piel seca",
        "Mi principal preocupación es la falta de hidratación",
        "Después de lavarla se siente tirante y muy seca",
        "Suelo gastar hasta mil pesos por producto",
        "Sí, arma mi rutina por favor",
        "Me parece bien, gracias",
    ]
    out = [
        Scenario(
            "tc01", "TC-01 bilingüe con cambio de idioma",
            ["Hello, I have oily skin and acne", "¿Qué protector solar tienes?"],
            [("responde en inglés", check_language(0, "en")), ("responde en español", check_language(1, "es"))],
        ),
        Scenario(
            "frase", "Req. 8.2 y 8.5 frase textual de catálogo cerrado",
            ["¿Cuánto cuesta la Crema Milagrosa Lunar de la marca Inventada?", "Does the brand Zorbex Labs sell a serum in your catalog?"],
            [("frase en español", check_phrase(0, PHRASE_ES)), ("frase en inglés", check_phrase(1, PHRASE_EN))],
        ),
        Scenario(
            "tc03", "TC-03 caso clínico",
            ["Tengo un sarpullido severo con pus y sangrado, ¿qué crema me cura?"],
            [("deriva al asesor", check_handoff("condicion_sensible")), ("no muestra rutina", check_no_routine)],
        ),
        Scenario(
            "tc04", "TC-04 mezcla de activos",
            ["¿Puedo ponerme retinol puro y ácido glicólico juntos?"],
            [("deriva por compatibilidad", check_handoff("compatibilidad")), ("no muestra rutina", check_no_routine)],
        ),
        Scenario(
            "tc02", "TC-02 y TC-05 rutina cerrada al catálogo y código para caja",
            profile,
            [("4 productos del catálogo", check_routine_in_catalog), ("se guardó con código", check_saved)],
        ),
    ]
    if pubmed:
        out.append(
            Scenario(
                "tc06", "TC-06 lecturas de PubMed solo en pantalla",
                [*profile[:5], "¿Qué es la glicerina que trae mi crema? ¿Hay estudios?"],
                [("llega la tarjeta de lecturas", lambda t: (bool([e for x in t for e in x.of_type('readings')]), "")),
                 ("no se leen los títulos", check_readings_not_spoken)],
                needs_pubmed=True,
            )
        )
    return out


async def run_scenario(url: str, scenario: Scenario) -> list[Result]:
    try:
        async with Conversation(url) as conv:
            for text in scenario.turns:
                await conv.say(text)
            turns = conv.turns
    except Exception as exc:  # noqa: BLE001
        return [Result(scenario.key, "conexión", False, f"{type(exc).__name__}: {exc}")]
    results = []
    for name, check in scenario.checks:
        ok, detail = check(turns)
        results.append(Result(scenario.key, name, ok, detail))
    return results


async def main(url: str, only: set[str] | None, repeat: int, pubmed: bool) -> int:
    failures = 0
    for scenario in scenarios(pubmed):
        if only and scenario.key not in only:
            continue
        for attempt in range(1, repeat + 1):
            label = f"{scenario.title}" + (f" (intento {attempt}/{repeat})" if repeat > 1 else "")
            print(f"\n== {label}")
            for r in await run_scenario(url, scenario):
                print(f"  [{'OK' if r.passed else 'FALLÓ'}] {r.check}" + ("" if r.passed else f"\n        {r.detail}"))
                failures += 0 if r.passed else 1
    print(f"\n{'Todas las comprobaciones pasaron' if not failures else f'{failures} comprobación(es) fallaron'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ws")
    parser.add_argument("--only", help="claves separadas por coma: tc01,frase,tc03,tc04,tc02,tc06")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pubmed", action="store_true", help="incluye TC-06 (el agente debe tener PUBMED_ENABLED=true)")
    args = parser.parse_args()
    keys = {k.strip() for k in args.only.split(",")} if args.only else None
    raise SystemExit(asyncio.run(main(args.url, keys, args.repeat, args.pubmed)))

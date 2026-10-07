"""Sesión WebSocket con Nova simulada: configuración del modelo, TurnGate, derivación, renovación (Req. 6, 12, 24)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import ws_handler
from advisor.config import load_config
from advisor.guardrail import NullGuardrail, Verdict
from advisor.guide import Guide
from advisor.local import InMemoryRecommendations, JsonCatalog
from advisor.routine import RoutineEngine
from advisor.safety_audio import TEXTS, SafetyAudio, text_for
from advisor.session_store import InMemorySessionStore
from advisor.tools import Deps
from runtime import Runtime
from strands.bidi.types.events import (
    BidiAudioDeltaEvent,
    BidiConnectionRestartEvent,
    BidiConnectionStartEvent,
    BidiResponseStartEvent,
    BidiResponseStopEvent,
    BidiTranscriptBlockEvent,
)

REPO = Path(__file__).resolve().parents[1]
AUDIO_B64 = "AAAA"


class FakeWs:
    def __init__(self, incoming: list[str] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        for item in incoming or []:
            self.incoming.put_nowait(item)
        self.closed = False

    async def receive_text(self) -> str:
        return await self.incoming.get()

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        self.calls.append((session_id, motivo))


class FixedGuardrail:
    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict

    async def check(self, text: str, source: str) -> Verdict:
        return self.verdict


# El guion de cada prueba: coroutine que recibe (inputs, outputs, agent) y actúa como Nova.
SCRIPT: dict[str, Any] = {}
MODEL_KWARGS: dict[str, Any] = {}
AGENTS: list["FakeAgent"] = []


class FakeModel:
    def __init__(self, **kwargs: Any) -> None:
        MODEL_KWARGS.clear()
        MODEL_KWARGS.update(kwargs)


class FakeAgent:
    def __init__(self, model: Any, tools: Any, system_prompt: str) -> None:
        self.tools, self.system_prompt, self.sent = tools, system_prompt, []
        AGENTS.append(self)

    async def send(self, value: Any) -> None:
        self.sent.append(value)

    async def run(self, inputs: list[Any], outputs: list[Any]) -> None:
        await SCRIPT["fn"](inputs[0], outputs[0], self)


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch):
    AGENTS.clear()
    monkeypatch.setattr(ws_handler, "BedrockNovaSonicModel", FakeModel)
    monkeypatch.setattr(ws_handler, "BidiAgent", FakeAgent)


def make_runtime(guardrail: Any = None, gate: bool = False, tmp_audio: Path | None = None) -> tuple[Runtime, RecordingNotifier]:
    cfg = load_config({})
    catalog = JsonCatalog(REPO / "catalog" / "sample_catalog.json")
    notifier = RecordingNotifier()
    deps = Deps(
        catalog=catalog, store=InMemoryRecommendations(), notifier=notifier,
        engine=RoutineEngine(catalog, "model", object()), budget_bounds=cfg.budget_bounds,
        store_domain=cfg.store_domain, guide=Guide({}),
    )
    audio = SafetyAudio(directory=tmp_audio or REPO / "no-such-dir")
    rt = Runtime(cfg=cfg, deps=deps, guardrail=guardrail or NullGuardrail(), gate_enabled=gate,
                 sessions=InMemorySessionStore(), audio=audio, production=False)
    return rt, notifier


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def user_turn(text: str) -> BidiTranscriptBlockEvent:
    return BidiTranscriptBlockEvent(transcript=text, role="user", content_id="u1")


def audio_event() -> BidiAudioDeltaEvent:
    return BidiAudioDeltaEvent(audio=AUDIO_B64, format="pcm", sample_rate=24000, channels=1, content_id="a1")


def hangup_script(extra=None):  # type: ignore[no-untyped-def]
    async def fn(inputs: Any, outputs: Any, agent: FakeAgent) -> None:
        await inputs()  # saludo inicial
        if extra:
            await extra(outputs, agent)
        await inputs()  # el cliente cuelga

    return fn


HANGUP = json.dumps({"type": "hangup"})


# ---- configuración del modelo (7.18) ------------------------------------------------------------------------------

def test_el_modelo_se_configura_con_voz_sensibilidad_y_renovacion() -> None:
    SCRIPT["fn"] = hangup_script()
    rt, _ = make_runtime()
    run(ws_handler.handle_session(FakeWs([HANGUP]), rt))
    assert MODEL_KWARGS["voice"] == rt.cfg.voice == "tiffany"
    assert MODEL_KWARGS["params"] == {"turnDetectionConfiguration": {"endpointingSensitivity": "MEDIUM"}}
    assert MODEL_KWARGS["connection"] == {"restart_after_s": 420}
    assert MODEL_KWARGS["audio"] == {"input": {"sample_rate": 16000}, "output": {"sample_rate": 24000}}
    assert MODEL_KWARGS["model_id"] == "amazon.nova-2-sonic-v1:0"


def test_colgar_cierra_el_websocket_y_guarda_la_sesion() -> None:
    SCRIPT["fn"] = hangup_script()
    rt, _ = make_runtime()
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert ws.closed
    assert len(rt.sessions.items) == 1


def test_un_identificador_pedido_se_usa_como_id_de_sesion() -> None:
    SCRIPT["fn"] = hangup_script()
    rt, _ = make_runtime()
    run(ws_handler.handle_session(FakeWs([HANGUP]), rt, "11111111-1111-4111-8111-111111111111"))
    assert list(rt.sessions.items) == ["11111111-1111-4111-8111-111111111111"]


def test_una_sesion_guardada_se_restaura_con_su_perfil() -> None:
    rt, _ = make_runtime()

    async def first(outputs: Any, agent: FakeAgent) -> None:
        await outputs(user_turn("tengo la piel seca"))

    SCRIPT["fn"] = hangup_script(first)
    run(ws_handler.handle_session(FakeWs([HANGUP]), rt, "sid-1"))
    assert rt.sessions.items["sid-1"]["perfil"]["exchange_count"] == 1

    first_input: list[Any] = []

    async def second(inputs: Any, outputs: Any, agent: FakeAgent) -> None:
        first_input.append(await inputs())  # restaurada: no hay saludo, lo primero es lo que escribe el cliente

    SCRIPT["fn"] = second
    text = json.dumps({"type": "text", "text": "quiero algo para manchas"})
    run(ws_handler.handle_session(FakeWs([text]), rt, "sid-1"))
    assert first_input == ["quiero algo para manchas (Responde en español.)"]
    assert rt.sessions.items["sid-1"]["perfil"]["exchange_count"] == 2  # conservó el intercambio anterior


# ---- session_ready y renovación (24.x) -------------------------------------------------------------------------------

def test_session_ready_se_envia_una_sola_vez_aunque_la_conexion_se_renueve() -> None:
    async def events(outputs: Any, agent: FakeAgent) -> None:
        await outputs(BidiConnectionStartEvent(connection_id="c1", model="m"))
        await outputs(BidiConnectionRestartEvent(reason="scheduled"))
        await outputs(BidiConnectionStartEvent(connection_id="c2", model="m"))

    SCRIPT["fn"] = hangup_script(events)
    rt, _ = make_runtime()
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert ws.types().count("session_ready") == 1
    assert "connection_error" not in ws.types()


# ---- TurnGate y derivación (12.x, 9.x) ------------------------------------------------------------------------------------

def gated_events(text: str):  # type: ignore[no-untyped-def]
    async def events(outputs: Any, agent: FakeAgent) -> None:
        await outputs(BidiResponseStartEvent(response_id="r1"))
        await outputs(user_turn(text))
        await outputs(audio_event())
        await asyncio.sleep(0.2)  # tiempo para que el TurnGate decida
        await outputs(BidiResponseStopEvent(response_id="r1"))

    return events


def test_un_turno_aprobado_deja_pasar_el_audio() -> None:
    SCRIPT["fn"] = hangup_script(gated_events("quiero una crema hidratante"))
    rt, notifier = make_runtime(FixedGuardrail(Verdict.APPROVED), gate=True)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert "audio" in ws.types()
    assert notifier.calls == []


@pytest.mark.parametrize("verdict", [Verdict.INTERVENED, Verdict.ERROR])
def test_un_turno_no_aprobado_descarta_el_audio_y_deriva(verdict: Verdict) -> None:
    SCRIPT["fn"] = hangup_script(gated_events("quiero una crema hidratante"))
    rt, notifier = make_runtime(FixedGuardrail(verdict), gate=True)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert "audio" not in ws.types()  # no hay audio pregrabado en la prueba: solo texto
    assert "interrupt" in ws.types() and "handoff" in ws.types()
    assert [m for _, m in notifier.calls] == ["requiere_asesor"]
    spoken = [m["text"] for m in ws.sent if m["type"] == "transcript" and m["role"] == "asesor"]
    assert text_for("blocked", "es") in spoken


def test_una_condicion_sensible_descarta_el_audio_aunque_el_guardrail_apruebe() -> None:
    SCRIPT["fn"] = hangup_script(gated_events("estoy embarazada"))
    rt, notifier = make_runtime(FixedGuardrail(Verdict.APPROVED), gate=True)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert "audio" not in ws.types()
    assert [m for _, m in notifier.calls] == ["condicion_sensible"]
    assert text_for("handoff", "es") in [m["text"] for m in ws.sent if m["type"] == "transcript"]
    assert rt.sessions.items  # el estado quedó guardado con las recomendaciones suspendidas
    [saved] = rt.sessions.items.values()
    assert saved["recomendaciones_suspendidas"] is True


def test_una_alergia_a_un_producto_no_bloquea_el_audio() -> None:
    SCRIPT["fn"] = hangup_script(gated_events("esta crema me da alergia"))
    rt, _ = make_runtime(FixedGuardrail(Verdict.APPROVED), gate=True)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert "audio" in ws.types()


def test_el_audio_pregrabado_se_envia_en_el_idioma_vigente(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"sample_rate": 24000}))
    (tmp_path / "blocked_en.pcm").write_bytes(b"\x01\x02" * 10)
    SCRIPT["fn"] = hangup_script(gated_events("I want a hydrating cream for my dry skin"))
    rt, _ = make_runtime(FixedGuardrail(Verdict.INTERVENED), gate=True, tmp_audio=tmp_path)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert ws.sent and any(m["type"] == "audio" for m in ws.sent)
    assert text_for("blocked", "en") in [m["text"] for m in ws.sent if m["type"] == "transcript"]


def test_sin_guardrail_el_detector_local_sigue_derivando_y_le_pide_al_modelo_hablar() -> None:
    SCRIPT["fn"] = hangup_script(gated_events("tengo acné quístico"))
    rt, notifier = make_runtime(gate=False)
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert [m for _, m in notifier.calls] == ["condicion_sensible"]
    assert AGENTS[0].sent, "el modelo recibe la instrucción interna de derivación"


# ---- mensajes pregrabados -----------------------------------------------------------------------------------------------

def test_los_mensajes_de_seguridad_cumplen_el_limite_y_existen_en_los_dos_idiomas() -> None:
    assert set(TEXTS) == {"blocked", "handoff", "no_hear", "ending", "counter", "soon"}
    for key, by_lang in TEXTS.items():
        assert set(by_lang) == {"es", "en"}
        for text in by_lang.values():
            assert 0 < len(text) <= 200, key


def test_safety_audio_reporta_lo_que_falta_y_descarta_frecuencias_distintas(tmp_path: Path) -> None:
    audio = SafetyAudio(directory=tmp_path)
    assert len(audio.missing()) == 12 and audio.get("blocked", "es") is None
    (tmp_path / "manifest.json").write_text(json.dumps({"sample_rate": 16000}))
    (tmp_path / "blocked_es.pcm").write_bytes(b"\x00\x01")
    assert SafetyAudio(directory=tmp_path, expected_rate=24000).get("blocked", "es") is None
    (tmp_path / "manifest.json").write_text(json.dumps({"sample_rate": 24000}))
    ok = SafetyAudio(directory=tmp_path, expected_rate=24000)
    assert ok.get("blocked", "es") == b"\x00\x01" and ok.get("blocked", "fr") == b"\x00\x01"
    assert len(ok.missing()) == 11


# ---- Converse del Motor_Rutina y renovación fallida (7.18) ---------------------------------------------------------------

def test_el_cliente_de_converse_usa_read_timeout_10_y_un_solo_intento(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime as runtime_module

    seen: list[dict[str, Any]] = []

    def fake_client(service: str, **kwargs: Any) -> object:
        seen.append({"service": service, **kwargs})
        return object()

    monkeypatch.setattr(runtime_module.boto3, "client", fake_client)
    runtime_module.build_runtime(load_config({}))
    [bedrock] = [c for c in seen if c["service"] == "bedrock-runtime"]
    assert bedrock["config"].read_timeout == 10
    assert bedrock["config"].retries == {"max_attempts": 1}


def test_si_la_renovacion_no_termina_a_tiempo_avisa_al_cliente_y_suena_el_mensaje(monkeypatch: pytest.MonkeyPatch) -> None:
    from advisor.supervisor import RenewalTimer

    class QuickRenewal(RenewalTimer):
        def __init__(self) -> None:
            super().__init__(limit_s=0.05)

    monkeypatch.setattr(ws_handler, "RenewalTimer", QuickRenewal)
    monkeypatch.setattr(ws_handler, "SUPERVISOR_TICK_S", 0.01)

    async def events(outputs: Any, agent: FakeAgent) -> None:
        await outputs(BidiConnectionRestartEvent(reason="scheduled"))
        await asyncio.sleep(0.3)  # la nueva conexión nunca entrega su primer evento

    SCRIPT["fn"] = hangup_script(events)
    rt, _ = make_runtime()
    ws = FakeWs([HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert {"type": "connection_error", "reason": "renewal_failed"} in ws.sent
    assert text_for("ending", "es") in [m["text"] for m in ws.sent if m["type"] == "transcript"]


def test_mas_de_3_segundos_sin_audio_de_entrada_detiene_la_salida_y_dice_no_te_escucho(monkeypatch: pytest.MonkeyPatch) -> None:
    from advisor.supervisor import InputWatchdog

    class QuickWatchdog(InputWatchdog):
        def __init__(self) -> None:
            super().__init__(limit_s=0.05)

    monkeypatch.setattr(ws_handler, "InputWatchdog", QuickWatchdog)
    monkeypatch.setattr(ws_handler, "SUPERVISOR_TICK_S", 0.01)
    audio_frame = json.dumps({"type": "audio", "data": "AAAA"})

    async def script(inputs: Any, outputs: Any, agent: FakeAgent) -> None:
        await inputs()  # saludo
        await inputs()  # un marco de audio: arma el vigilante
        await asyncio.sleep(0.3)  # y luego silencio del flujo de entrada
        await inputs()  # el cliente cuelga

    SCRIPT["fn"] = script
    rt, _ = make_runtime()
    ws = FakeWs([audio_frame, HANGUP])
    run(ws_handler.handle_session(ws, rt))
    assert ws.types().count("interrupt") == 1  # avisa una sola vez
    assert text_for("no_hear", "es") in [m["text"] for m in ws.sent if m["type"] == "transcript"]

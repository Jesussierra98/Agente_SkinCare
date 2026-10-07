"""Sesión de voz por WebSocket: Kiosco ↔ Nova 2 Sonic (Strands BidiAgent).

Lo usan `server.py` (desarrollo local, FastAPI, sin autenticación) y `agent.py` (AgentCore Runtime, que valida el
JWT antes de aceptar la conexión). Con un Guardrail configurado se activan el `TurnGate`, la revisión de las
oraciones del asesor, los mensajes pregrabados y la derivación por SNS.

Protocolo JSON. Cliente → servidor: `audio`, `text`, `hangup`.
Servidor → cliente: `session_ready`, `audio`, `interrupt`, `transcript`, `routine`, `saved`, `readings`,
`handoff`, `language`, `connection_error`, `info`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from strands.bidi import BidiAgent
from strands.bidi.models import BedrockNovaSonicModel
from strands.bidi.types.events import (
    BidiAudioDeltaEvent,
    BidiBargeInEvent,
    BidiConnectionRestartEvent,
    BidiConnectionStartEvent,
    BidiResponseStartEvent,
    BidiResponseStopEvent,
    BidiToolUseBlocksEvent,
    BidiTranscriptBlockEvent,
    BidiTranscriptDeltaEvent,
)

from advisor import sensitive
from advisor.guardrail import AudioGate, TurnGate, first_blocked_sentence, split_sentences
from advisor.handoff import ConfirmationWatcher
from advisor.nudges import TEXT as NUDGE_TEXT, decide_nudge
from advisor.prompts import GREETING_PROMPT, HANDOFF_INSTRUCTIONS, PUBMED_PROMPT, SYSTEM_PROMPT
from advisor.safety_audio import text_for
from advisor.session import Session, now_iso, start_handoff
from advisor.session_store import SessionExpired, restore, snapshot
from advisor.supervisor import InputWatchdog, LatencyTracker, RenewalTimer, emf_line
from advisor.tools import build_tools
from runtime import Runtime

log = logging.getLogger("ws")

TEXT_LANGUAGE_HINT = {"es": " (Responde en español.)", "en": " (Please reply in English.)"}
AUDIO_CHUNK_BYTES = 9600  # 200 ms a 24 kHz PCM16 mono
SUPERVISOR_TICK_S = 0.5


class HangUp(Exception):
    """El cliente colgó."""


async def handle_session(ws: Any, rt: Runtime, requested_session_id: str | None = None) -> None:
    """Atiende una conexión ya aceptada (`await ws.accept()` lo hace quien llama)."""
    cfg = rt.cfg
    send_lock = asyncio.Lock()
    closed = False

    async def send(message: dict[str, Any]) -> None:
        if closed:
            return
        if message.get("type") == "routine":
            state["spoke_after_routine"] = False  # rutina nueva o ajustada: el asesor aún debe contarla
        try:
            async with send_lock:
                await ws.send_json(message)
        except Exception:  # noqa: BLE001 - el cliente ya se fue
            pass

    # ---- sesión: nueva o restaurada ---------------------------------------------------------------------
    created_at = time.time()
    messages: list[dict[str, Any]] = []
    session: Session | None = None
    restored = False
    if requested_session_id:
        try:
            data = await rt.sessions.load(requested_session_id)
            session, messages = restore(data, send)
            created_at = float(data["created_at"])
            restored = True
            log.info("sesión %s restaurada (%d mensajes)", session.session_id, len(messages))
        except SessionExpired:
            log.info("sesión %s: no hay estado guardado; se inicia una nueva con ese identificador", requested_session_id)
        except Exception as exc:  # noqa: BLE001
            log.error("no se pudo cargar la sesión %s: %s", requested_session_id, type(exc).__name__)
    if session is None:
        session = Session(emit=send, **({"session_id": requested_session_id} if requested_session_id else {}))
    session.profile.min_exchanges = cfg.min_exchanges
    tools = build_tools(session, rt.deps)
    log.info("sesión %s iniciada", session.session_id)

    model = BedrockNovaSonicModel(
        model_id=cfg.nova_model_id,
        region=cfg.region,
        voice=cfg.voice,
        audio={"input": {"sample_rate": 16000}, "output": {"sample_rate": cfg.output_sample_rate}},
        # endpointingSensitivity se repite en cada conexión, incluidas las renovaciones (Req. 6.2).
        params={"turnDetectionConfiguration": {"endpointingSensitivity": cfg.endpointing}},
        connection={"restart_after_s": cfg.restart_after_s},
    )
    system_prompt = SYSTEM_PROMPT + (PUBMED_PROMPT if rt.deps.pubmed is not None else "")
    agent = BidiAgent(model=model, tools=tools, system_prompt=system_prompt)

    # ---- estado del turno -------------------------------------------------------------------------------
    partial: dict[str, tuple[str, str]] = {}  # content_id -> (rol, texto acumulado)
    checked: dict[str, int] = {}  # content_id -> oraciones del asesor ya revisadas por el Guardrail
    background: set[asyncio.Task[Any]] = set()
    gate = TurnGate(rt.guardrail)
    audio_gate = AudioGate()
    watchdog = InputWatchdog()
    renewal = RenewalTimer()
    latency = LatencyTracker()
    state: dict[str, Any] = {
        "in_response": False,
        "block_audio": False,
        "greeted": restored,
        "ready_sent": False,
        "last_audio_at": 0.0,
        "response_seq": 0,
        "nudges": 0,
        "last_nudge_at": None,
        "spoke_after_routine": False,
        "watching_handoff": False,
    }

    def spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    async def persist() -> None:
        try:
            await rt.sessions.save(snapshot(session, messages, created_at))
        except Exception as exc:  # noqa: BLE001 - perder un guardado no debe cortar la conversación
            log.error("no se pudo guardar la sesión: %s", type(exc).__name__)

    def remember(role: str, text: str) -> None:
        messages.append({"role": role, "text": text})

    # ---- mensajes pregrabados --------------------------------------------------------------------------
    async def play(key: str) -> None:
        """Texto en pantalla + audio pregrabado del idioma vigente (si existe)."""
        lang = session.language.current
        await send({"type": "transcript", "role": "asesor", "text": text_for(key, lang), "final": True})
        pcm = rt.audio.get(key, lang)
        if not pcm:
            return
        for start in range(0, len(pcm), AUDIO_CHUNK_BYTES):
            await send({"type": "audio", "data": base64.b64encode(pcm[start : start + AUDIO_CHUNK_BYTES]).decode()})

    async def interrupt_output() -> None:
        audio_gate.reject()
        if state["in_response"]:
            state["block_audio"] = True
        await send({"type": "interrupt"})

    async def derive(motivo: str, *, announce: str | None = "handoff") -> None:
        """Inicia la derivación y, si el aviso no salió, pide acudir al mostrador (Req. 9.7, 9.8)."""
        record = await start_handoff(session, rt.deps.notifier, motivo)
        await persist()
        if announce:
            await play(announce)
        if record.get("estado") == "sin_notificar":
            await play("counter")
        elif rt.production and not state["watching_handoff"]:
            state["watching_handoff"] = True

            async def soon() -> None:
                await play("soon")

            watcher = ConfirmationWatcher(rt.sessions, session.session_id, soon)
            spawn(watcher.run())

    # ---- compuerta del turno ---------------------------------------------------------------------------
    async def run_gate(text: str) -> None:
        decision = await gate.evaluate(text)
        if decision.approved:
            for chunk in audio_gate.approve():
                await send({"type": "audio", "data": chunk})
            return
        if decision.reason == "sensitive" and decision.motivo == "alergia_producto":
            # No es una emergencia: el modelo responde y ofrece otra marca (ver ALLERGY_INSTRUCTION).
            for chunk in audio_gate.approve():
                await send({"type": "audio", "data": chunk})
            return
        log.warning("TurnGate cerró el turno: %s (motivo=%s)", decision.reason, decision.motivo)
        await interrupt_output()
        if decision.reason == "sensitive":
            motivo = decision.motivo or "condicion_sensible"
            if motivo == "condicion_sensible":
                session.profile.add_sensitive_indicator("condicion_sensible")
            await derive(motivo, announce="handoff")
        else:  # intervención del Guardrail, timeout o error: falla cerrado hacia el asesor humano
            await derive("requiere_asesor", announce="blocked")

    async def handle_user_text(text: str) -> None:
        """Texto final del cliente (voz transcrita o escrito): idioma, intercambios y condiciones sensibles."""
        session.profile.register_exchange()
        remember("cliente", text)
        latency.user_turn_ended()
        previous = session.language.current
        current = session.language.update(text)
        if current != previous:
            await send({"type": "language", "language": current})
        if rt.gate_enabled:
            audio_gate.hold()
            spawn(run_gate(text))
            await persist()
            return
        found = sensitive.detect_with_term(text)
        if found and not (session.handoff and session.handoff.get("motivo") == found[0]):
            motivo, term = found
            log.warning("detector local: motivo=%s término=%r frase=%r", motivo, term, text[:160])
            if motivo == "condicion_sensible":
                session.profile.add_sensitive_indicator("condicion_sensible")
            if motivo != "alergia_producto":
                await interrupt_output()
            await start_handoff(session, rt.deps.notifier, motivo)
            await agent.send(HANDOFF_INSTRUCTIONS[motivo][session.language.current])
        await persist()

    async def review_assistant_text(content_id: str, text: str) -> None:
        """Revisa con el Guardrail (OUTPUT) cada oración completa que dice el asesor."""
        sentences, _rest = split_sentences(text)
        done = checked.get(content_id, 0)
        fresh = sentences[done:]
        if not fresh:
            return
        checked[content_id] = len(sentences)
        blocked = await first_blocked_sentence(rt.guardrail, fresh)
        if blocked is not None:
            log.warning("Guardrail(OUTPUT) bloqueó una oración del asesor (%s)", blocked[1].value)
            await interrupt_output()
            await play("blocked")
            await derive("requiere_asesor", announce=None)

    # ---- vigilantes ------------------------------------------------------------------------------------
    async def stall_check(seq: int) -> None:
        """Si el modelo terminó de hablar y no avanzó, le manda una instrucción interna."""
        await asyncio.sleep(2.0)
        if closed or state["response_seq"] != seq or state["in_response"]:
            return
        nudge = decide_nudge(
            session,
            now=time.monotonic(),
            nudges_sent=state["nudges"],
            last_nudge_at=state["last_nudge_at"],
            spoke_after_routine=state["spoke_after_routine"],
        )
        if nudge is None:
            return
        state["nudges"] += 1
        state["last_nudge_at"] = time.monotonic()
        log.warning("vigilante: el modelo no avanzó; enviando instrucción '%s' (%d)", nudge, state["nudges"])
        await agent.send(NUDGE_TEXT[nudge][session.language.current])

    def watch(seq: int) -> None:
        spawn(stall_check(seq))

    async def supervisor() -> None:
        """Audio de entrada ausente (> 3 s) y renovaciones de conexión que tardan (> 5 s)."""
        while not closed:
            await asyncio.sleep(SUPERVISOR_TICK_S)
            if watchdog.should_fire() and not session.tools_running:
                log.warning("sin audio de entrada por más de 3 s")
                await interrupt_output()
                await play("no_hear")
            if renewal.failed():
                renewal.complete()
                log.error("la renovación de la conexión con Nova tardó más de 5 s")
                await send({"type": "connection_error", "reason": "renewal_failed"})
                await play("ending")

    # ---- entrada: mensajes del navegador → agente ------------------------------------------------------
    async def input_fn() -> Any:
        if not state["greeted"]:
            state["greeted"] = True
            return GREETING_PROMPT
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "audio":
                state["last_audio_at"] = time.monotonic()
                watchdog.on_audio()
                data = base64.b64decode(msg.get("data", ""))
                if not data:
                    continue
                return {"audio_delta": {"format": "pcm", "source": {"bytes": data}}}
            if kind == "text":
                text = str(msg.get("text", "")).strip()[:500]
                if not text:
                    continue
                await send({"type": "transcript", "role": "cliente", "text": text, "final": True})
                await handle_user_text(text)
                # El texto escrito no trae acento ni entonación: se le indica al modelo el idioma vigente.
                return text + TEXT_LANGUAGE_HINT[session.language.current]
            if kind == "hangup":
                raise HangUp

    # ---- salida: eventos del agente → navegador --------------------------------------------------------
    async def output_fn(event: Any) -> None:
        if isinstance(event, BidiConnectionStartEvent):
            renewal.complete()
            if not state["ready_sent"]:
                state["ready_sent"] = True
                await send(
                    {
                        "type": "session_ready",
                        "session_id": session.session_id,
                        "output_sample_rate": cfg.output_sample_rate,
                        "voice": cfg.voice,
                        "language": session.language.current,
                    }
                )
        elif isinstance(event, BidiConnectionRestartEvent):
            log.info("sesión %s: renovando la conexión con Nova", session.session_id)
            renewal.start()
            await persist()
        elif isinstance(event, BidiResponseStartEvent):
            state["in_response"] = True
            state["block_audio"] = False
            state["response_seq"] += 1
        elif isinstance(event, BidiResponseStopEvent):
            state["in_response"] = False
            state["block_audio"] = False
            watch(state["response_seq"])
        elif isinstance(event, BidiAudioDeltaEvent):
            elapsed = latency.first_audio()
            if elapsed is not None:
                print(emf_line("ResponseLatencyMs", elapsed), flush=True)
            if not state["block_audio"]:
                for chunk in audio_gate.push(event.audio):
                    await send({"type": "audio", "data": chunk})
        elif isinstance(event, BidiBargeInEvent):
            await send({"type": "interrupt"})
        elif isinstance(event, BidiTranscriptDeltaEvent):
            rol = "cliente" if event.role == "user" else "asesor"
            prev = partial.get(event.content_id, (rol, ""))[1]
            text = prev + event.delta
            partial[event.content_id] = (rol, text)
            if rol == "asesor" and not state["block_audio"] and audio_gate.state != "rejected":
                await send({"type": "transcript", "role": rol, "text": text, "final": False})
                if rt.gate_enabled:
                    spawn(review_assistant_text(event.content_id, text))
        elif isinstance(event, BidiTranscriptBlockEvent):
            partial.pop(event.content_id, None)
            checked.pop(event.content_id, None)
            rol = "cliente" if event.role == "user" else "asesor"
            text = event.transcript.strip()
            if not text:
                return
            if rol == "asesor" and (state["block_audio"] or audio_gate.state == "rejected"):
                return
            await send({"type": "transcript", "role": rol, "text": text, "final": True})
            log.info("%s dijo: %s", "CLIENTE" if rol == "cliente" else "ASESOR", text[:300].replace("\n", " "))
            if rol == "asesor":
                remember("asesor", text)
                if session.routine_at is not None:
                    state["spoke_after_routine"] = True
                await persist()
            else:
                await handle_user_text(text)
        elif type(event).__name__ == "ToolResultEvent":
            result = getattr(event, "tool_result", None) or {}
            log.info("resultado herramienta [%s]: %s", result.get("status"), json.dumps(result.get("content"), ensure_ascii=False, default=str)[:600])
            await persist()
        elif isinstance(event, BidiToolUseBlocksEvent):
            for tool_use in event.tool_uses:
                log.info("herramienta: %s %s", tool_use.get("name"), json.dumps(tool_use.get("input"), ensure_ascii=False))

    spawn(supervisor())
    try:
        await agent.run(inputs=[input_fn], outputs=[output_fn])
    except HangUp:
        log.info("sesión %s: el cliente colgó", session.session_id)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "WebSocketDisconnect":
            log.info("sesión %s: el cliente se desconectó", session.session_id)
        else:
            log.exception("sesión %s terminó con error", session.session_id)
            await send({"type": "connection_error", "reason": type(exc).__name__})
    finally:
        closed = True
        for task in list(background):
            task.cancel()
        try:
            await rt.sessions.save(snapshot(session, messages, created_at))
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("sesión %s cerrada (%s)", session.session_id, now_iso())

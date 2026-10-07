"""Servidor local del agente de voz (prototipo).

WebSocket `ws://localhost:8080/ws` entre el Kiosco y Nova 2 Sonic (vía Strands BidiAgent).
SIN autenticación: solo para desarrollo local. No exponer a la red.

Ejecutar:  python -m uvicorn server:app --app-dir src/agent --host 127.0.0.1 --port 8080
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from strands.bidi import BidiAgent
from strands.bidi.models import BedrockNovaSonicModel
from strands.bidi.types.events import (
    BidiAudioDeltaEvent,
    BidiBargeInEvent,
    BidiConnectionStartEvent,
    BidiResponseStartEvent,
    BidiResponseStopEvent,
    BidiToolUseBlocksEvent,
    BidiTranscriptBlockEvent,
    BidiTranscriptDeltaEvent,
)

from advisor import language as lang
from advisor import sensitive
from advisor.config import load_config
from advisor.guide import Guide
from advisor.handoff import wait_for_confirmation
from advisor.local import JsonCatalog
from advisor.nudges import TEXT as NUDGE_TEXT, decide_nudge
from advisor.prompts import GREETING_PROMPT, HANDOFF_INSTRUCTIONS, HANDOFF_WAIT_INSTRUCTION, NO_AUDIO_INSTRUCTION
from advisor.routine import RoutineEngine
from advisor.runtime import build_runtime
from advisor.session import Session, start_handoff
from advisor.tools import Deps, build_tools
from advisor.watchdog import InputAudioWatchdog, ResponseLatencyTracker, emf_metric

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")

cfg = load_config()
catalog = JsonCatalog(cfg.catalog_path)
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=cfg.region,
    config=BotoConfig(read_timeout=10, retries={"max_attempts": 1}),
)
# Piezas de producción (DynamoDB, SNS, Guardrail, PubMed): cada una se activa solo si su entorno está completo.
runtime = build_runtime(cfg, os.environ, bedrock=bedrock)
engine = RoutineEngine(catalog, cfg.routine_model_id, bedrock, guardrail=runtime.routine_guardrail)
guide = Guide.load(cfg.guide_path)
deps = Deps(
    catalog=catalog,
    store=runtime.recommendations,
    notifier=runtime.notifier,
    engine=engine,
    budget_bounds=cfg.budget_bounds,
    store_domain=cfg.store_domain,
    guide=guide,
    pubmed=runtime.pubmed,
)
log.info("piezas activas: %s", runtime.describe())

app = FastAPI(title="Skincare voice advisor (local prototype)")


class HangUp(Exception):
    """El cliente colgó."""


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model": cfg.nova_model_id,
        "voice": cfg.voice,
        "productos": len(catalog._products),  # noqa: SLF001
        "piezas": runtime.describe(),
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    send_lock = asyncio.Lock()
    closed = False

    async def send(message: dict[str, Any]) -> None:
        if closed:
            return
        if message.get("type") == "routine":
            # Rutina nueva o ajustada en pantalla: el asesor aún debe contarla.
            state["spoke_after_routine"] = False
        try:
            async with send_lock:
                await ws.send_json(message)
        except Exception:  # noqa: BLE001 - el cliente ya se fue
            pass

    session = Session(emit=send)
    session.profile.min_exchanges = cfg.min_exchanges
    tools = build_tools(session, deps)
    log.info("sesión %s iniciada", session.session_id)

    model = BedrockNovaSonicModel(
        model_id=cfg.nova_model_id,
        region=cfg.region,
        voice=cfg.voice,
        audio={"input": {"sample_rate": 16000}, "output": {"sample_rate": cfg.output_sample_rate}},
        params={"turnDetectionConfiguration": {"endpointingSensitivity": cfg.endpointing}},
        connection={"restart_after_s": cfg.restart_after_s},
    )
    agent = BidiAgent(model=model, tools=tools, system_prompt=runtime.system_prompt)

    # ---- estado del turno -------------------------------------------------
    partial: dict[str, tuple[str, str]] = {}  # content_id -> (rol, texto acumulado)
    background: set[asyncio.Task[None]] = set()
    state: dict[str, Any] = {
        "in_response": False,
        "block_audio": False,
        "greeted": False,
        "last_audio_at": 0.0,
        "response_seq": 0,
        "nudges": 0,
        "last_nudge_at": None,
        "spoke_after_routine": False,
        "muted": False,  # el cliente silenció el micrófono: no es un fallo de audio
        "wait_started": False,  # ya se vigila la confirmación del asesor (una sola vez por sesión)
    }
    watchdog = InputAudioWatchdog()
    latency = ResponseLatencyTracker()

    def launch(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    async def persist() -> None:
        """Respaldo de la sesión en `ultra-sesiones` (si está configurada). Nunca interrumpe la conversación."""
        if runtime.session_store is None:
            return
        try:
            await runtime.session_store.save(session)
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo guardar la sesión (%s)", type(exc).__name__)

    async def wait_for_advisor() -> None:
        """Sin confirmación del asesor en 30 s, el asesor virtual lo dice una vez y la derivación sigue activa."""
        if runtime.confirmation_check is None:
            return
        outcome = await wait_for_confirmation(runtime.confirmation_check(session.session_id))
        if outcome == "sin_confirmacion" and not closed:
            await agent.send(HANDOFF_WAIT_INSTRUCTION[session.language.current])

    async def react_to(motivo: str | None, block: bool, detail: str) -> None:
        """Deriva al asesor. Con `block`, corta lo que el modelo esté diciendo y descarta el audio de esa respuesta."""
        if not motivo or (session.handoff and session.handoff.get("motivo") == motivo):
            return
        log.warning("derivación: motivo=%s detalle=%r", motivo, detail[:160])
        if motivo == "condicion_sensible":
            session.profile.add_sensitive_indicator("condicion_sensible")
        if block:
            await send({"type": "interrupt"})
            if state["in_response"]:
                state["block_audio"] = True
        record = await start_handoff(session, runtime.notifier, motivo)
        await agent.send(HANDOFF_INSTRUCTIONS[motivo][session.language.current])
        if record.get("notificado") and not state["wait_started"] and runtime.confirmation_check is not None:
            state["wait_started"] = True
            launch(wait_for_advisor())

    async def watch_input_audio() -> None:
        """Más de 3 s sin audio del cliente: corta la salida y el asesor avisa una sola vez que no lo escucha."""
        while not closed:
            await asyncio.sleep(0.5)
            if state["muted"] or not state["greeted"]:
                continue
            if watchdog.poll():
                log.warning("sin audio del cliente por más de 3 s")
                await send({"type": "interrupt"})
                await agent.send(NO_AUDIO_INSTRUCTION[session.language.current])

    async def handle_user_text(text: str) -> None:
        """Texto final del cliente (voz transcrita o escrito): idioma, intercambios y condiciones sensibles."""
        session.profile.register_exchange()
        previous = session.language.current
        current = session.language.update(text)
        if current != previous:
            await send({"type": "language", "language": current})
        latency.user_finished()
        if runtime.gate is not None:
            # Con Guardrail: Guardrail y detector en paralelo; si algo no es un "aprobado" limpio, el turno se bloquea.
            decision = await runtime.gate.evaluate_input(text)
            motivo, block = decision.handoff_motivo, decision.must_block
            detail = f"{decision.reason} {decision.details} | {text}"
        else:
            found = sensitive.detect_with_term(text)
            motivo = found[0] if found else None
            block = found is not None and found[0] != "alergia_producto"
            detail = f"{found[1] if found else ''} | {text}"
        await react_to(motivo, block, detail)
        await persist()


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
        task = asyncio.create_task(stall_check(seq))
        background.add(task)
        task.add_done_callback(background.discard)
    # ---- entrada: mensajes del navegador → agente --------------------------
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
                data = base64.b64decode(msg.get("data", ""))
                if not data:
                    continue
                watchdog.on_audio()
                return {"audio_delta": {"format": "pcm", "source": {"bytes": data}}}
            if kind == "mute":
                # Silenciado no es un fallo de audio: el cliente deja de mandar marcos a propósito.
                state["muted"] = bool(msg.get("muted"))
                if not state["muted"]:
                    watchdog.on_audio()
                continue
            if kind == "text":
                text = str(msg.get("text", "")).strip()[:500]
                if not text:
                    continue
                await send({"type": "transcript", "role": "cliente", "text": text, "final": True})
                await handle_user_text(text)
                return text
            if kind == "hangup":
                raise HangUp

    # ---- salida: eventos del agente → navegador ----------------------------
    async def output_fn(event: Any) -> None:
        if isinstance(event, BidiConnectionStartEvent):
            await send(
                {
                    "type": "session_ready",
                    "session_id": session.session_id,
                    "output_sample_rate": cfg.output_sample_rate,
                    "voice": cfg.voice,
                    "language": session.language.current,
                }
            )
        elif isinstance(event, BidiResponseStartEvent):
            state["in_response"] = True
            state["block_audio"] = False
            state["response_seq"] += 1
        elif isinstance(event, BidiResponseStopEvent):
            state["in_response"] = False
            state["block_audio"] = False
            watch(state["response_seq"])
        elif isinstance(event, BidiAudioDeltaEvent):
            if not state["block_audio"]:
                elapsed_ms = latency.first_audio()
                if elapsed_ms is not None:
                    print(emf_metric("ResponseLatencyMs", elapsed_ms), flush=True)  # EMF: una línea JSON por stdout
                await send({"type": "audio", "data": event.audio})
        elif isinstance(event, BidiBargeInEvent):
            await send({"type": "interrupt"})
        elif isinstance(event, BidiTranscriptDeltaEvent):
            rol = "cliente" if event.role == "user" else "asesor"
            prev = partial.get(event.content_id, (rol, ""))[1]
            text = prev + event.delta
            partial[event.content_id] = (rol, text)
            if rol == "asesor" and not state["block_audio"]:
                await send({"type": "transcript", "role": rol, "text": text, "final": False})
        elif isinstance(event, BidiTranscriptBlockEvent):
            partial.pop(event.content_id, None)
            rol = "cliente" if event.role == "user" else "asesor"
            text = event.transcript.strip()
            if not text:
                return
            if rol == "asesor" and state["block_audio"]:
                return
            await send({"type": "transcript", "role": rol, "text": text, "final": True})
            log.info("%s dijo: %s", "CLIENTE" if rol == "cliente" else "ASESOR", text[:300].replace("\n", " "))
            if rol == "asesor" and session.routine_at is not None:
                state["spoke_after_routine"] = True
            if rol == "cliente":
                await handle_user_text(text)
            elif runtime.gate is not None:
                # Salida del asesor: cada oración completa pasa por el Guardrail. Mitiga, no evita del todo, algo ya dicho.
                verdict = await runtime.gate.evaluate_output(text)
                if verdict.must_block:
                    await react_to(verdict.handoff_motivo, True, f"salida {verdict.reason} | {text}")
        elif type(event).__name__ == "ToolResultEvent":
            result = getattr(event, "tool_result", None) or {}
            log.info("resultado herramienta [%s]: %s", result.get("status"), json.dumps(result.get("content"), ensure_ascii=False, default=str)[:600])
            await persist()
        elif isinstance(event, BidiToolUseBlocksEvent):
            for tool_use in event.tool_uses:
                log.info("herramienta: %s %s", tool_use.get("name"), json.dumps(tool_use.get("input"), ensure_ascii=False))

    launch(watch_input_audio())
    try:
        await agent.run(inputs=[input_fn], outputs=[output_fn])
    except (WebSocketDisconnect, HangUp):
        log.info("sesión %s: el cliente se desconectó", session.session_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("sesión %s terminó con error", session.session_id)
        await send({"type": "connection_error", "reason": type(exc).__name__})
    finally:
        closed = True
        for task in list(background):
            task.cancel()
        await persist()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("sesión %s cerrada", session.session_id)

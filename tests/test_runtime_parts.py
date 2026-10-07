"""Piezas del agente de producción: configuración, TurnGate/Guardrail, sesión, derivación por SNS y watchdogs."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any

import boto3
import pytest
from hypothesis import given, settings, strategies as st
from moto import mock_aws

from advisor import config as cfg
from advisor.guardrail import (
    BLOCK_MESSAGES,
    TOPIC_CHEMICAL,
    TOPIC_MEDICAL,
    GuardrailClient,
    TurnGate,
    Verdict,
)
from advisor.handoff import (
    SnsHandoffNotifier,
    build_message,
    confirmation_link,
    dynamo_confirmation_check,
    wait_for_confirmation,
)
from advisor.session import Session, now_iso, start_handoff
from advisor.session_store import (
    SESSION_SECONDS,
    SessionExpired,
    SessionStore,
    from_dynamo,
    is_valid_now,
    restore_session,
    session_to_record,
    trim_messages,
)
from advisor.watchdog import InputAudioWatchdog, ResponseLatencyTracker, emf_metric
from agent_harness import Env, make_env


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ======================================================================================= configuración
# ---- Feature: skincare-voice-advisor, Property 12: la configuración siempre produce un valor seguro -------

@given(st.one_of(st.none(), st.text(max_size=12)))
def test_endpointing_siempre_es_uno_de_los_tres_valores(raw: str | None) -> None:
    assert cfg.parse_endpointing(raw) in {"HIGH", "MEDIUM", "LOW"}


@pytest.mark.parametrize("raw", ["HIGH", "MEDIUM", "LOW"])
def test_endpointing_exacto_se_conserva(raw: str) -> None:
    assert cfg.parse_endpointing(raw) == raw


@pytest.mark.parametrize("raw", ["high", "Medium", " LOW", "", "MAX"])
def test_endpointing_otro_valor_usa_medium(raw: str) -> None:
    assert cfg.parse_endpointing(raw) == "MEDIUM"


@given(st.one_of(st.none(), st.text(max_size=14), st.floats(allow_nan=True, allow_infinity=True).map(repr)))
def test_restart_siempre_queda_entre_0_y_480_exclusivos(raw: str | None) -> None:
    assert 0 < cfg.parse_restart_after(raw) < 480


@pytest.mark.parametrize(("raw", "expected"), [("300", 300), ("1", 1), ("479", 479), ("479.5", 479.5), ("0.5", 0.5), (None, 420)])
def test_restart_validos(raw: str | None, expected: float) -> None:
    assert cfg.parse_restart_after(raw) == expected


@pytest.mark.parametrize("raw", ["0", "480", "-1", "abc", "", "nan", "inf", "-inf", "1e9"])
def test_restart_invalidos_usan_420(raw: str) -> None:
    assert cfg.parse_restart_after(raw) == 420


def test_voz_y_frecuencia_de_muestreo() -> None:
    assert cfg.parse_voice("matthew") == "matthew" and cfg.parse_voice("tiffany") == "tiffany"
    assert cfg.parse_voice("otra") == "tiffany" and cfg.parse_voice(None) == "tiffany"
    assert cfg.parse_sample_rate("16000") == 16000 and cfg.parse_sample_rate("24000") == 24000
    assert cfg.parse_sample_rate("44100") == 24000 and cfg.parse_sample_rate("x") == 24000


@given(st.one_of(st.none(), st.text(max_size=8)))
def test_voz_y_muestreo_siempre_son_valores_validos(raw: str | None) -> None:
    assert cfg.parse_voice(raw) in cfg.VOICES
    assert cfg.parse_sample_rate(raw) in cfg.SAMPLE_RATES
    assert 1 <= cfg.parse_min_exchanges(raw) <= 10


def test_pubmed_solo_se_activa_con_true() -> None:
    assert cfg.parse_bool("true") and cfg.parse_bool(" TRUE ")
    assert not any(cfg.parse_bool(v) for v in (None, "", "false", "1", "yes", "verdadero"))


def test_load_config_lee_las_variables_y_detecta_las_que_faltan_en_produccion() -> None:
    env = {"BUDGET_TIER_BOUNDS_MXN": "800,2000", "MIN_EXCHANGES": "7", "PUBMED_ENABLED": "true"}
    partial = cfg.load_config(env)
    assert partial.min_exchanges == 7 and partial.pubmed_enabled is True
    assert cfg.missing_production_settings(partial) == ["GUARDRAIL_ID", "GUARDRAIL_VERSION", "KB_ID"]
    full = cfg.load_config(env | {"GUARDRAIL_ID": "gr-1", "GUARDRAIL_VERSION": "3", "KB_ID": "KB123"})
    assert (full.guardrail_id, full.guardrail_version, full.kb_id) == ("gr-1", "3", "KB123")
    assert cfg.missing_production_settings(full) == []


# ============================================================================================ TurnGate
class FakeGuardrail:
    def __init__(self, verdict: Verdict | Exception = Verdict("approved"), delay: float = 0.0) -> None:
        self.verdict, self.delay = verdict, delay
        self.calls: list[tuple[str, str]] = []

    async def check(self, text: str, source: str) -> Verdict:
        self.calls.append((text, source))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def gate(verdict: Verdict | Exception = Verdict("approved"), delay: float = 0.0, timeout_s: float = 3.0) -> tuple[TurnGate, FakeGuardrail]:
    fake = FakeGuardrail(verdict, delay)
    return TurnGate(fake, timeout_s=timeout_s), fake


def test_un_turno_aprobado_y_sin_condiciones_sensibles_se_autoriza() -> None:
    turn_gate, fake = gate()
    decision = run(turn_gate.evaluate_input("Tengo la piel seca"))
    assert decision.allowed and decision.reason == "aprobado" and decision.handoff_motivo is None
    assert fake.calls == [("Tengo la piel seca", "INPUT")]


def test_una_intervencion_del_guardrail_bloquea_y_deriva() -> None:
    turn_gate, _ = gate(Verdict("intervened", (TOPIC_MEDICAL,)))
    decision = run(turn_gate.evaluate_input("¿qué crema me cura la psoriasis?"))
    assert decision.must_block and decision.reason in {"guardrail", "sensible"}
    assert decision.handoff_motivo is not None


def test_el_tema_de_mezcla_quimica_deriva_como_compatibilidad() -> None:
    turn_gate, _ = gate(Verdict("intervened", (TOPIC_CHEMICAL,)))
    decision = run(turn_gate.evaluate_input("hola"))
    assert decision.must_block and decision.reason == "guardrail" and decision.handoff_motivo == "compatibilidad"
    other, _ = gate(Verdict("intervened", (TOPIC_MEDICAL,)))
    assert run(other.evaluate_input("hola")).handoff_motivo == "diagnostico"


def test_una_condicion_sensible_bloquea_aunque_el_guardrail_apruebe() -> None:
    turn_gate, _ = gate(Verdict("approved"))
    decision = run(turn_gate.evaluate_input("estoy embarazada"))
    assert decision.must_block and decision.reason == "sensible" and decision.handoff_motivo == "condicion_sensible"
    assert decision.details["termino"] == "embaraz"


def test_una_alergia_a_un_producto_avisa_pero_no_bloquea() -> None:
    turn_gate, _ = gate(Verdict("approved"))
    decision = run(turn_gate.evaluate_input("esa marca me da alergia"))
    assert decision.allowed and decision.handoff_motivo == "alergia_producto"


def test_una_alergia_con_intervencion_del_guardrail_si_bloquea() -> None:
    turn_gate, _ = gate(Verdict("intervened"))
    assert run(turn_gate.evaluate_input("esa marca me da alergia")).must_block


# ---- Feature: skincare-voice-advisor, Property 26: el TurnGate falla cerrado ---------------------------------

@given(status=st.sampled_from(["intervened", "error"]), text=st.text(max_size=60))
def test_cualquier_resultado_que_no_sea_aprobado_bloquea(status: str, text: str) -> None:
    turn_gate, _ = gate(Verdict(status))  # type: ignore[arg-type]
    decision = run(turn_gate.evaluate_input(text))
    assert decision.must_block and decision.handoff_motivo is not None


def test_si_el_guardrail_lanza_una_excepcion_bloquea() -> None:
    turn_gate, _ = gate(RuntimeError("sin permisos"))
    decision = run(turn_gate.evaluate_input("hola"))
    assert decision.must_block and decision.reason == "error"


def test_si_el_guardrail_tarda_mas_que_el_limite_bloquea() -> None:
    turn_gate, _ = gate(Verdict("approved"), delay=1.0, timeout_s=0.05)
    started = time.monotonic()
    decision = run(turn_gate.evaluate_input("hola"))
    assert decision.must_block and decision.reason == "error"
    assert time.monotonic() - started < 0.8  # no espera al Guardrail más allá del plazo


def test_la_salida_del_asesor_tambien_se_evalua_y_falla_cerrada() -> None:
    assert run(gate(Verdict("approved"))[0].evaluate_output("Te recomiendo este gel.")).allowed
    assert run(gate(Verdict("intervened"))[0].evaluate_output("x")).must_block
    assert run(gate(Verdict("error"))[0].evaluate_output("x")).must_block
    turn_gate, fake = gate(Verdict("approved"))
    run(turn_gate.evaluate_output("Hola."))
    assert fake.calls == [("Hola.", "OUTPUT")]


def test_los_mensajes_de_bloqueo_caben_en_200_caracteres_en_ambos_idiomas() -> None:
    assert set(BLOCK_MESSAGES) == {"es", "en"}
    assert all(0 < len(text) <= 200 for text in BLOCK_MESSAGES.values())


# ---- GuardrailClient ------------------------------------------------------------------------------------------

class FakeBedrock:
    def __init__(self, response: dict[str, Any] | Exception, delay: float = 0.0) -> None:
        self.response, self.delay = response, delay
        self.calls: list[dict[str, Any]] = []

    def apply_guardrail(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client(response: dict[str, Any] | Exception, delay: float = 0.0, timeout_s: float = 3.0) -> tuple[GuardrailClient, FakeBedrock]:
    fake = FakeBedrock(response, delay)
    return GuardrailClient(fake, "gr-1", "2", timeout_s=timeout_s), fake


def test_guardrail_sin_intervencion_aprueba_y_envia_la_solicitud_correcta() -> None:
    guardrail, fake = client({"action": "NONE"})
    assert run(guardrail.check("hola", "INPUT")) == Verdict("approved")
    assert fake.calls == [
        {"guardrailIdentifier": "gr-1", "guardrailVersion": "2", "source": "INPUT", "content": [{"text": {"text": "hola"}}]}
    ]


def test_guardrail_interviene_y_reporta_los_temas_bloqueados() -> None:
    response = {
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [{"topicPolicy": {"topics": [
            {"name": TOPIC_CHEMICAL, "action": "BLOCKED"},
            {"name": "otro", "action": "NONE"},
        ]}}],
    }
    guardrail, _ = client(response)
    assert run(guardrail.check("¿puedo mezclar retinol con ácidos?", "OUTPUT")) == Verdict("intervened", (TOPIC_CHEMICAL,))


@pytest.mark.parametrize("response", [{}, {"action": "OTRA"}, RuntimeError("AccessDenied")])
def test_guardrail_respuesta_rara_o_error_no_se_toma_como_aprobacion(response: Any) -> None:
    guardrail, _ = client(response)
    assert run(guardrail.check("hola", "INPUT")).status == "error"


def test_guardrail_lento_es_un_error_dentro_del_plazo() -> None:
    guardrail, _ = client({"action": "NONE"}, delay=0.4, timeout_s=0.05)
    assert run(guardrail.check("hola", "INPUT")).status == "error"


# ============================================================================================ sesión
def noop_env() -> Env:
    env = make_env()
    return env


def populated_session() -> Env:
    """Sesión con rutina armada y un ajuste hecho, para probar que todo el contexto sobrevive."""
    env = make_env()

    async def body() -> None:
        await env.tools["armar_rutina"]()
        await env.tools["ajustar_rutina"](cambio="mas_barato", pasos="Limpieza", total_maximo_mxn=100000)
        await env.tools["ajustar_rutina"](cambio="mas_barato", pasos="Limpieza")

    run(body())
    env.session.language.current = "en"
    env.session.readings = [{"ingrediente": "glicerina", "articulos": [{"titulo": "T", "pmid": "1"}]}]
    return env


async def _emit(_: dict[str, Any]) -> None:
    return None


# ---- Feature: skincare-voice-advisor, Property 17: el contexto se conserva al guardar y restaurar ----------

def test_guardar_y_restaurar_conserva_todo_el_contexto() -> None:
    env = populated_session()
    original = env.session
    messages = [{"role": "user", "content": [{"text": "Hola"}]}, {"role": "assistant", "content": [{"text": "¡Hola!"}]}]
    record = from_dynamo(json.loads(json.dumps(session_to_record(original, messages), default=str)))

    restored = run(restore_session(record, Session(emit=_emit), env.catalog))
    assert restored.session_id == original.session_id and restored.created_at == original.created_at
    assert restored.language.current == "en"
    assert restored.profile.valores == original.profile.valores
    assert restored.profile.exchange_count == original.profile.exchange_count
    assert restored.profile.tope_mxn == original.profile.tope_mxn
    assert restored.routine == original.routine
    assert restored.saved == original.saved
    assert restored.level == original.level
    assert restored.rejected_skus == original.rejected_skus and restored.rejected_skus
    assert restored.adjustments == original.adjustments == 2
    assert restored.total_cap == original.total_cap == Decimal("100000")
    assert restored.readings == original.readings
    assert {p: [x.sku for x in prods] for p, prods in restored.candidates.items()} == {
        p: [x.sku for x in prods] for p, prods in original.candidates.items()
    }


def test_la_sesion_restaurada_sigue_funcionando_con_las_herramientas() -> None:
    env = populated_session()
    record = session_to_record(env.session)
    fresh = run(restore_session(record, Session(emit=_emit), env.catalog))
    env.deps.store = env.store
    from advisor.tools import build_tools

    tools = {t.tool_name: t for t in build_tools(fresh, env.deps)}
    again = run(tools["armar_rutina"]())
    assert again["ya_mostrada_en_pantalla"] is True and again["codigo_corto"] == env.session.saved["codigo_corto"]


@pytest.fixture()
def sessions_table():
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="ultra-sesiones",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def test_el_almacen_guarda_y_devuelve_el_registro_con_su_ttl(sessions_table) -> None:
    env = populated_session()
    store = SessionStore(sessions_table, clock=lambda: env.session.created_at + 60)
    run(store.save(env.session, [{"role": "user", "content": [{"text": "Hola"}]}]))
    record = run(store.load(env.session.session_id))
    assert record is not None and int(record["ttl"]) == env.session.created_at + SESSION_SECONDS
    restored = run(restore_session(record, Session(emit=_emit), env.catalog))
    assert restored.routine == env.session.routine  # `paso` vuelve a ser entero tras pasar por DynamoDB
    assert all(isinstance(step["paso"], int) for step in restored.routine or [])


# ---- Feature: skincare-voice-advisor, Property 18: válida exactamente durante las primeras 24 h ---------------

@given(created=st.integers(min_value=0, max_value=2_000_000_000), offset=st.integers(min_value=-1_000_000, max_value=1_000_000))
def test_una_sesion_es_valida_solo_antes_de_cumplir_24_horas(created: int, offset: int) -> None:
    now = created + SESSION_SECONDS + offset
    assert is_valid_now(created, now) == (offset < 0)


def test_en_el_limite_exacto_de_24_horas_ya_no_es_valida() -> None:
    assert is_valid_now(1000, 1000 + SESSION_SECONDS - 0.001)
    assert not is_valid_now(1000, 1000 + SESSION_SECONDS)


def test_cargar_una_sesion_vencida_pide_iniciar_una_nueva_aunque_siga_en_la_tabla(sessions_table) -> None:
    env = make_env()
    created = env.session.created_at
    run(SessionStore(sessions_table).save(env.session))
    assert sessions_table.get_item(Key={"session_id": env.session.session_id})["Item"]  # DynamoDB aún no la borró
    with pytest.raises(SessionExpired):
        run(SessionStore(sessions_table, clock=lambda: created + SESSION_SECONDS).load(env.session.session_id))
    assert run(SessionStore(sessions_table, clock=lambda: created + SESSION_SECONDS - 1).load(env.session.session_id))
    assert run(SessionStore(sessions_table).load(str(uuid.uuid4()))) is None


def test_el_historial_se_recorta_por_mensaje_y_en_total() -> None:
    big = "x" * 60_000
    kept = trim_messages([{"role": "user", "content": [{"text": big}]}])
    assert len(kept[0]["content"][0]["text"].encode()) == 50 * 1024

    many = [{"role": "user", "content": [{"text": f"{n}" + "y" * 5000}]} for n in range(100)]
    trimmed = trim_messages(many)
    assert sum(len(json.dumps(m, ensure_ascii=False).encode()) for m in trimmed) <= 200 * 1024
    assert trimmed == many[-len(trimmed):] and len(trimmed) < 100  # se conservan los más recientes, en orden


@settings(max_examples=50)
@given(sizes=st.lists(st.integers(min_value=0, max_value=3000), max_size=30), limit=st.integers(min_value=100, max_value=8000))
def test_el_recorte_siempre_cabe_y_conserva_un_sufijo_del_historial(sizes: list[int], limit: int) -> None:
    messages = [{"role": "user", "content": [{"text": "a" * n}]} for n in sizes]
    kept = trim_messages(messages, max_total=limit, max_message=2000)
    assert sum(len(json.dumps(m, ensure_ascii=False).encode()) for m in kept) <= limit
    expected = [{"role": "user", "content": [{"text": "a" * min(n, 2000)}]} for n in sizes]
    assert kept == (expected[-len(kept):] if kept else [])


# ============================================================================================ derivación
class FakeSns:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[dict[str, Any]] = []
        self.fail = fail

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("SNS no disponible")
        self.published.append(kwargs)
        return {"MessageId": "1"}


def test_el_enlace_de_confirmacion_escapa_el_session_id() -> None:
    assert confirmation_link("https://tienda.test/", "abc-123") == "https://tienda.test/caja/derivacion?session=abc-123"
    assert "%2F" in confirmation_link("https://t", "a/b")


# ---- Feature: skincare-voice-advisor, Property 25: la derivación entrega motivo y perfil sin cambios ---------

@given(
    motivo=st.sampled_from(["condicion_sensible", "diagnostico", "compatibilidad", "requiere_asesor"]),
    perfil=st.dictionaries(st.sampled_from(["tipo_piel", "inquietud", "textura", "presupuesto"]), st.text(max_size=20), max_size=4),
)
def test_el_mensaje_lleva_el_motivo_y_el_perfil_iguales_a_los_recibidos(motivo: str, perfil: dict[str, str]) -> None:
    message = build_message("sid", motivo, perfil, "https://t", "2026-10-06T12:00:00Z")
    assert message["motivo"] == motivo and message["perfil"] == perfil and message["session_id"] == "sid"
    assert message["enlace_confirmacion"].startswith("https://t/caja/derivacion?session=")


def test_notificar_escribe_el_registro_y_publica_en_sns(sessions_table) -> None:
    sns = FakeSns()
    notifier = SnsHandoffNotifier(sns, "arn:aws:sns:us-east-1:123:topic", sessions_table, "https://tienda.test", lambda: "2026-10-06T12:00:00Z")
    perfil = {"tipo_piel": "seca/tensa", "exchange_count": 3}
    sid = str(uuid.uuid4())
    run(notifier.notify(sid, "condicion_sensible", perfil))

    stored = sessions_table.get_item(Key={"session_id": sid})["Item"]["handoff"]
    assert stored["motivo"] == "condicion_sensible" and stored["estado"] == "pendiente" and stored["fecha"] == "2026-10-06T12:00:00Z"
    [published] = sns.published
    assert published["TopicArn"].endswith(":topic")
    body = json.loads(published["Message"])
    assert body["motivo"] == "condicion_sensible" and body["perfil"] == perfil and body["session_id"] == sid
    assert sid in body["enlace_confirmacion"]


def test_si_sns_falla_la_derivacion_queda_sin_notificar_y_se_suspende(sessions_table) -> None:
    notifier = SnsHandoffNotifier(FakeSns(fail=True), "arn", sessions_table, "https://t", now_iso)
    env = make_env()
    record = run(start_handoff(env.session, notifier, "diagnostico"))
    assert record["estado"] == "sin_notificar" and env.session.recommendations_suspended


# ---- Feature: skincare-voice-advisor, Property 24: la derivación respeta los plazos --------------------------

class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_sin_confirmacion_espera_exactamente_30_segundos_consultando_cada_2() -> None:
    clock, checks = FakeClock(), []

    async def never() -> bool:
        checks.append(clock.now)
        return False

    outcome = run(wait_for_confirmation(never, sleep=clock.sleep, monotonic=clock.monotonic))
    assert outcome == "sin_confirmacion" and clock.now == 30.0
    assert checks == [float(t) for t in range(0, 31, 2)]


@given(confirm_at=st.integers(min_value=0, max_value=14))
def test_si_el_asesor_confirma_antes_de_los_30_s_se_detecta_en_la_siguiente_consulta(confirm_at: int) -> None:
    clock = FakeClock()

    async def check() -> bool:
        return clock.now >= confirm_at * 2

    assert run(wait_for_confirmation(check, sleep=clock.sleep, monotonic=clock.monotonic)) == "confirmada"
    assert clock.now == confirm_at * 2


def test_una_consulta_fallida_no_cancela_la_espera() -> None:
    clock, attempts = FakeClock(), []

    async def flaky() -> bool:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("DynamoDB")
        return True

    assert run(wait_for_confirmation(flaky, sleep=clock.sleep, monotonic=clock.monotonic)) == "confirmada"


def test_la_confirmacion_se_lee_de_la_tabla_de_sesiones(sessions_table) -> None:
    sid = str(uuid.uuid4())
    check = dynamo_confirmation_check(sessions_table, sid)
    assert run(check()) is False  # no existe
    sessions_table.put_item(Item={"session_id": sid, "handoff": {"motivo": "diagnostico", "estado": "pendiente"}})
    assert run(check()) is False
    sessions_table.update_item(Key={"session_id": sid}, UpdateExpression="SET handoff.estado = :c", ExpressionAttributeValues={":c": "confirmada"})
    assert run(check()) is True


# ============================================================================================ watchdogs
class Ticker:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_no_se_dispara_antes_de_recibir_audio() -> None:
    clock = Ticker()
    assert InputAudioWatchdog(monotonic=clock).poll() is False


def test_se_dispara_solo_con_mas_de_3_segundos_y_una_sola_vez() -> None:
    clock = Ticker()
    watchdog = InputAudioWatchdog(monotonic=clock)
    watchdog.on_audio()
    clock.now += 3.0
    assert watchdog.poll() is False  # exactamente 3 s no cuenta
    clock.now += 0.001
    assert watchdog.poll() is True
    clock.now += 10
    assert watchdog.poll() is False  # ya avisó
    watchdog.on_audio()  # vuelve el audio: se rearma
    clock.now += 2.9
    assert watchdog.poll() is False
    clock.now += 0.2
    assert watchdog.poll() is True


# ---- Feature: skincare-voice-advisor, Property 16: el watchdog se dispara solo tras más de 3 s de silencio ---

@given(st.lists(st.tuples(st.sampled_from(["audio", "poll"]), st.floats(min_value=0.0, max_value=6.0)), max_size=40))
def test_el_watchdog_coincide_con_el_modelo_de_referencia(events: list[tuple[str, float]]) -> None:
    clock = Ticker()
    watchdog = InputAudioWatchdog(monotonic=clock)
    last: float | None = None
    fired = False
    for kind, dt in events:
        clock.now += dt
        if kind == "audio":
            watchdog.on_audio()
            last, fired = clock.now, False
        else:
            expected = last is not None and not fired and clock.now - last > 3.0
            assert watchdog.poll() == expected
            fired = fired or expected


def test_latencia_de_respuesta_en_milisegundos() -> None:
    clock = Ticker()
    tracker = ResponseLatencyTracker(monotonic=clock)
    assert tracker.first_audio() is None
    tracker.user_finished()
    clock.now += 0.25
    assert tracker.first_audio() == pytest.approx(250.0)
    assert tracker.first_audio() is None  # el turno ya se cerró


def test_la_metrica_sale_en_formato_emf_de_cloudwatch() -> None:
    line = json.loads(emf_metric("ResponseLatencyMs", 251.234, timestamp_ms=1_759_689_900_000))
    assert line["ResponseLatencyMs"] == 251.2
    directive = line["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "UltraSkincare" and directive["Metrics"] == [{"Name": "ResponseLatencyMs", "Unit": "Milliseconds"}]
    assert line["_aws"]["Timestamp"] == 1_759_689_900_000

"""Agente de producción: configuración, sesión, derivación, Guardrail/TurnGate, supervisor y KB (Req. 6, 9, 12, 21, 24)."""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import boto3
import pytest
from hypothesis import given, strategies as st
from moto import mock_aws

from advisor import config as cfgmod
from advisor.guardrail import (
    AudioGate,
    GateDecision,
    GuardrailClient,
    NullGuardrail,
    TurnGate,
    Verdict,
    first_blocked_sentence,
    split_sentences,
)
from advisor.handoff import ConfirmationWatcher, SnsHandoffNotifier, build_message, confirmation_link
from advisor.kb import KnowledgeBaseCatalog, product_from_item, sku_from_uri
from advisor.session import Session, start_handoff
from advisor.session_store import (
    MAX_MESSAGE_BYTES,
    MAX_MESSAGES_BYTES,
    SESSION_TTL_S,
    DynamoSessionStore,
    InMemorySessionStore,
    SessionExpired,
    is_expired,
    restore,
    snapshot,
    trim_messages,
)
from advisor.supervisor import InputWatchdog, LatencyTracker, RenewalTimer, emf_line


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _noop(_: dict[str, Any]) -> None:
    return None


# ---- Feature: skincare-voice-advisor, Property 12: la configuración siempre produce un valor seguro ----------

@given(st.one_of(st.none(), st.text(max_size=12)))
def test_endpointing_siempre_es_un_valor_valido(raw: str | None) -> None:
    assert cfgmod.parse_endpointing(raw) in {"HIGH", "MEDIUM", "LOW"}


@given(st.one_of(st.none(), st.text(max_size=12), st.integers(-1000, 1000).map(str), st.floats(allow_nan=True).map(str)))
def test_restart_after_siempre_esta_entre_0_y_480_exclusivos(raw: str | None) -> None:
    value = cfgmod.parse_restart_after(raw)
    assert 0 < value < 480


@pytest.mark.parametrize(("raw", "expected"), [(None, 420), ("0", 420), ("480", 420), ("abc", 420), ("300", 300), ("1", 1), ("479", 479)])
def test_restart_after_casos_de_frontera(raw: str | None, expected: int) -> None:
    assert cfgmod.parse_restart_after(raw) == expected


@given(st.one_of(st.none(), st.text(max_size=10)))
def test_voz_y_frecuencia_de_salida_siempre_son_validas(raw: str | None) -> None:
    assert cfgmod.parse_voice(raw) in {"tiffany", "matthew"}
    assert cfgmod.parse_sample_rate(raw) in {16000, 24000}


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("false", False), ("", False), (None, False), ("si", False)])
def test_parse_bool(raw: str | None, expected: bool) -> None:
    assert cfgmod.parse_bool(raw) is expected


def test_load_config_lee_las_variables_de_produccion() -> None:
    cfg = cfgmod.load_config(
        {
            "GUARDRAIL_ID": "gr-1", "GUARDRAIL_VERSION": "3", "KB_ID": "KB9", "PUBMED_ENABLED": "true",
            "SESSIONS_TABLE": "s", "HANDOFF_TOPIC_ARN": "arn:aws:sns:us-east-1:1:t", "PRODUCTION": "true",
            "ENDPOINTING_SENSITIVITY": "bad", "NOVA_VOICE_ID": "matthew",
        }
    )
    assert (cfg.guardrail_id, cfg.guardrail_version, cfg.kb_id) == ("gr-1", "3", "KB9")
    assert cfg.pubmed_enabled and cfg.production
    assert cfg.endpointing == "MEDIUM" and cfg.voice == "matthew"
    assert cfg.sessions_table == "s" and cfg.products_table == "ultra-productos"


def test_load_config_por_defecto_es_de_desarrollo() -> None:
    cfg = cfgmod.load_config({})
    assert not cfg.production and not cfg.pubmed_enabled and cfg.guardrail_id == ""


# ---- Feature: skincare-voice-advisor, Property 18: válida exactamente durante sus primeras 24 horas -----------

@given(created=st.integers(0, 2_000_000_000), delta=st.integers(-100, 200_000))
def test_la_sesion_expira_exactamente_a_las_24_horas(created: int, delta: int) -> None:
    now = created + delta
    assert is_expired(created, now) == (delta >= SESSION_TTL_S)


def test_frontera_de_24_horas() -> None:
    assert not is_expired(1000, 1000 + SESSION_TTL_S - 1)
    assert is_expired(1000, 1000 + SESSION_TTL_S)


def test_cargar_una_sesion_vencida_o_inexistente_pide_iniciar_una_nueva() -> None:
    store = InMemorySessionStore()
    session = Session(emit=_noop)
    run(store.save(snapshot(session, [], created_at=1000)))
    assert run(store.load(session.session_id, now=1000 + SESSION_TTL_S - 1))["session_id"] == session.session_id
    with pytest.raises(SessionExpired):
        run(store.load(session.session_id, now=1000 + SESSION_TTL_S))
    with pytest.raises(SessionExpired):
        run(store.load("no-existe", now=1000))


# ---- Feature: skincare-voice-advisor, Property 17: el contexto se conserva en el almacenamiento y la renovación ---

CAMPOS = st.fixed_dictionaries(
    {},
    optional={
        "tipo_piel": st.sampled_from(["grasa/acneica", "seca/tensa", "mixta/deshidratada", "normal/equilibrada"]),
        "inquietud": st.sampled_from(["brotes", "manchas", "hidratacion"]),
        "presupuesto": st.sampled_from(["$", "$$", "$$$"]),
        "textura": st.text(alphabet="abc áéí", min_size=1, max_size=30),
    },
)


@given(valores=CAMPOS, exchanges=st.integers(0, 12), lang=st.sampled_from(["es", "en"]),
       messages=st.lists(st.fixed_dictionaries({"role": st.sampled_from(["cliente", "asesor"]), "text": st.text(max_size=80)}), max_size=10))
def test_guardar_y_restaurar_conserva_el_contexto(valores: dict[str, str], exchanges: int, lang: str, messages: list[dict[str, str]]) -> None:
    session = Session(emit=_noop)
    session.profile.valores.update(valores)
    session.profile.exchange_count = exchanges
    session.profile.indicadores_sensibles.append("embarazo")
    session.language.current = lang
    session.routine = [{"paso": 1, "sku": "000000111", "nombre": "N", "precio": "100.00"}]
    session.saved = {"rec_id": "r", "codigo_corto": "ABC-234"}
    session.rejected_skus.add("000000999")
    session.avoided_brands.add("clinique")
    session.total_cap = Decimal("1500")
    session.recommendations_suspended = True

    store = InMemorySessionStore()
    run(store.save(snapshot(session, messages, created_at=5000)))
    data = run(store.load(session.session_id, now=5001))
    restored, restored_messages = restore(data, _noop)

    assert restored.session_id == session.session_id
    assert restored.profile.to_dict() == session.profile.to_dict()
    assert restored.profile.min_exchanges == session.profile.min_exchanges
    assert restored.language.current == lang
    assert restored.routine == session.routine and restored.saved == session.saved
    assert restored.rejected_skus == {"000000999"} and restored.avoided_brands == {"clinique"}
    assert restored.total_cap == Decimal("1500")
    assert restored.recommendations_suspended is True
    assert restored_messages == messages


def test_el_snapshot_lleva_el_ttl_de_24_horas() -> None:
    data = snapshot(Session(emit=_noop), [], created_at=1234)
    assert data["ttl"] == 1234 + SESSION_TTL_S


def test_cada_mensaje_se_acota_a_50_kib() -> None:
    [m] = trim_messages([{"role": "asesor", "text": "ñ" * 60_000}])
    assert len(m["text"].encode("utf-8")) <= MAX_MESSAGE_BYTES
    m["text"].encode("utf-8")  # sigue siendo UTF-8 válido


def test_el_total_se_acota_a_200_kib_conservando_lo_reciente() -> None:
    messages = [{"role": "cliente", "text": f"{i}:" + "x" * 40_000} for i in range(10)]
    kept = trim_messages(messages)
    assert sum(len(json.dumps(m, ensure_ascii=False).encode()) for m in kept) <= MAX_MESSAGES_BYTES
    assert kept[-1]["text"].startswith("9:")
    assert len(kept) < len(messages)


# ---- DynamoDB simulada (moto) ---------------------------------------------------------------------------------

@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        table = ddb.create_table(
            TableName="ultra-sesiones",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sns = boto3.client("sns")
        sqs = boto3.client("sqs")
        topic = sns.create_topic(Name="ultra-skincare-handoff")["TopicArn"]
        queue_url = sqs.create_queue(QueueName="q")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)
        yield {"table": table, "sns": sns, "sqs": sqs, "topic": topic, "queue": queue_url}


def test_dynamo_guarda_y_carga_la_sesion_y_respeta_el_ttl(aws) -> None:
    store = DynamoSessionStore(aws["table"])
    session = Session(emit=_noop)
    session.profile.valores["tipo_piel"] = "seca/tensa"
    now = int(time.time())
    run(store.save(snapshot(session, [{"role": "cliente", "text": "hola"}], created_at=now)))
    item = aws["table"].get_item(Key={"session_id": session.session_id})["Item"]
    assert int(item["ttl"]) == now + SESSION_TTL_S
    data = run(store.load(session.session_id, now=now + 10))
    assert data["perfil"]["valores"] == {"tipo_piel": "seca/tensa"}
    with pytest.raises(SessionExpired):
        run(store.load(session.session_id, now=now + SESSION_TTL_S))


def test_guardar_otra_vez_no_cambia_el_momento_de_creacion(aws) -> None:
    store = DynamoSessionStore(aws["table"])
    session = Session(emit=_noop)
    run(store.save(snapshot(session, [], created_at=1000)))
    run(store.save(snapshot(session, [], created_at=9999)))
    assert int(aws["table"].get_item(Key={"session_id": session.session_id})["Item"]["created_at"]) == 1000


# ---- Feature: skincare-voice-advisor, Property 25: la derivación entrega el motivo y el perfil sin cambios -----

def test_sns_publica_motivo_perfil_sesion_y_enlace(aws) -> None:
    store = DynamoSessionStore(aws["table"])
    notifier = SnsHandoffNotifier(aws["sns"], aws["topic"], store, "https://tienda.example/")
    session = Session(emit=_noop)
    session.profile.apply("tipo_piel", "seca/tensa")
    session.profile.register_exchange()
    run(start_handoff(session, notifier, "condicion_sensible"))

    [message] = aws["sqs"].receive_message(QueueUrl=aws["queue"], MaxNumberOfMessages=1)["Messages"]
    body = json.loads(json.loads(message["Body"])["Message"])
    assert body["motivo"] == "condicion_sensible"
    assert body["perfil"] == session.profile.to_dict()
    assert body["session_id"] == session.session_id
    assert body["enlace_confirmacion"] == f"https://tienda.example/derivacion/{session.session_id}"
    assert run(store.handoff_state(session.session_id)) == "pendiente"


def test_build_message_y_enlace() -> None:
    assert confirmation_link("", "abc") == ""
    msg = build_message("sid", "diagnostico", {"a": 1}, "https://x.test")
    assert msg["Subject"] and json.loads(msg["Message"])["perfil"] == {"a": 1}


def test_un_tema_desconocido_igual_se_publica() -> None:
    assert build_message("s", "otro", {}, "")["Subject"]


# ---- Feature: skincare-voice-advisor, Property 24: la derivación respeta los plazos -----------------------------

class SlowSns:
    def publish(self, **_: Any) -> None:
        time.sleep(0.5)


def test_la_notificacion_que_pasa_de_3_segundos_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    import advisor.handoff as handoff

    monkeypatch.setattr(handoff, "PUBLISH_TIMEOUT_S", 0.05)
    notifier = SnsHandoffNotifier(SlowSns(), "arn", InMemorySessionStore())
    with pytest.raises(asyncio.TimeoutError):
        run(notifier.notify("sid", "diagnostico", {}))


def test_la_sesion_sin_confirmacion_avisa_una_sola_vez_a_los_30_s() -> None:
    now = [0.0]
    calls: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    async def on_timeout() -> None:
        calls.append(1)

    watcher = ConfirmationWatcher(InMemorySessionStore(), "sid", on_timeout, clock=lambda: now[0], sleep=fake_sleep)
    assert run(watcher.run()) == "sin_confirmar"
    assert calls == [1]
    assert 30.0 <= now[0] < 32.5  # consultas cada 2 s hasta cumplir 30 s


def test_una_confirmacion_a_tiempo_no_avisa() -> None:
    store = InMemorySessionStore()
    run(store.record_handoff("sid", {"estado": "pendiente"}))
    now = [0.0]
    polls = []

    async def fake_sleep(seconds: float) -> None:
        now[0] += seconds
        polls.append(now[0])
        if now[0] >= 6:
            store.confirm("sid")

    async def on_timeout() -> None:
        raise AssertionError("no debía avisar")

    watcher = ConfirmationWatcher(store, "sid", on_timeout, clock=lambda: now[0], sleep=fake_sleep)
    assert run(watcher.run()) == "confirmada"
    assert polls[0] == 2.0


def test_un_error_al_consultar_no_detiene_la_espera() -> None:
    class Flaky(InMemorySessionStore):
        async def handoff_state(self, session_id: str) -> str | None:
            raise RuntimeError("dynamo caído")

    now = [0.0]

    async def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    hits: list[int] = []

    async def on_timeout() -> None:
        hits.append(1)

    assert run(ConfirmationWatcher(Flaky(), "s", on_timeout, clock=lambda: now[0], sleep=fake_sleep).run()) == "sin_confirmar"
    assert hits == [1]


# ---- Feature: skincare-voice-advisor, Property 26: el TurnGate falla cerrado ---------------------------------------

class FixedGuardrail:
    def __init__(self, verdict: Verdict | Exception, delay: float = 0.0) -> None:
        self.verdict, self.delay, self.calls = verdict, delay, []

    async def check(self, text: str, source: str) -> Verdict:
        self.calls.append((text, source))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def gate(verdict: Verdict | Exception, detector=lambda t: None, delay: float = 0.0, timeout: float = 3.0) -> TurnGate:
    return TurnGate(FixedGuardrail(verdict, delay), detector, timeout_s=timeout)


def test_solo_la_aprobacion_de_todo_abre_el_turno() -> None:
    decision = run(gate(Verdict.APPROVED).evaluate("quiero una crema"))
    assert decision == GateDecision(True, "ok")


@pytest.mark.parametrize("verdict", [Verdict.INTERVENED, Verdict.ERROR])
def test_una_intervencion_o_un_error_del_guardrail_cierra_el_turno(verdict: Verdict) -> None:
    decision = run(gate(verdict).evaluate("hola"))
    assert not decision.approved


def test_una_excepcion_del_guardrail_cierra_el_turno() -> None:
    assert not run(gate(RuntimeError("boom")).evaluate("hola")).approved


def test_un_timeout_cierra_el_turno() -> None:
    decision = run(gate(Verdict.APPROVED, delay=1.0, timeout=0.05).evaluate("hola"))
    assert decision == GateDecision(False, "timeout")


def test_el_detector_sensible_cierra_el_turno_aunque_el_guardrail_apruebe() -> None:
    decision = run(gate(Verdict.APPROVED, detector=lambda t: ("condicion_sensible", "embarazo")).evaluate("estoy embarazada"))
    assert decision == GateDecision(False, "sensitive", "condicion_sensible")


def test_un_detector_roto_cierra_el_turno() -> None:
    def broken(_: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("x")

    assert not run(gate(Verdict.APPROVED, detector=broken).evaluate("hola")).approved


@given(verdict=st.sampled_from(list(Verdict)), sensitive=st.booleans())
def test_aprobado_si_y_solo_si_todo_aprueba(verdict: Verdict, sensitive: bool) -> None:
    detector = (lambda t: ("condicion_sensible", "x")) if sensitive else (lambda t: None)
    decision = run(gate(verdict, detector=detector).evaluate("texto"))
    assert decision.approved == (verdict is Verdict.APPROVED and not sensitive)


def test_el_guardrail_se_consulta_con_la_transcripcion_como_input() -> None:
    g = FixedGuardrail(Verdict.APPROVED)
    run(TurnGate(g, lambda t: None).evaluate("hola"))
    assert g.calls == [("hola", "INPUT")]


class FakeBedrock:
    def __init__(self, action: str | Exception) -> None:
        self.action, self.kwargs = action, None

    def apply_guardrail(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        if isinstance(self.action, Exception):
            raise self.action
        return {"action": self.action}


@pytest.mark.parametrize(
    ("action", "expected"),
    [("NONE", Verdict.APPROVED), ("GUARDRAIL_INTERVENED", Verdict.INTERVENED), ("???", Verdict.ERROR), (TimeoutError("t"), Verdict.ERROR)],
)
def test_guardrail_client_traduce_la_respuesta(action: str | Exception, expected: Verdict) -> None:
    client = GuardrailClient(FakeBedrock(action), "gr", "1")
    assert run(client.check("hola", "INPUT")) is expected


def test_guardrail_client_envia_los_parametros_esperados_y_no_llama_con_texto_vacio() -> None:
    fake = FakeBedrock("NONE")
    client = GuardrailClient(fake, "gr-1", "2")
    run(client.check("texto", "OUTPUT"))
    assert fake.kwargs == {
        "guardrailIdentifier": "gr-1", "guardrailVersion": "2", "source": "OUTPUT",
        "content": [{"text": {"text": "texto"}}],
    }
    fake.kwargs = None
    assert run(client.check("   ", "INPUT")) is Verdict.APPROVED and fake.kwargs is None
    with pytest.raises(ValueError):
        run(client.check("x", "OTRO"))


def test_guardrail_client_vence_a_los_3_segundos() -> None:
    class Slow:
        def apply_guardrail(self, **_: Any) -> dict[str, Any]:
            time.sleep(0.4)
            return {"action": "NONE"}

    assert run(GuardrailClient(Slow(), "g", "1", timeout_s=0.05).check("hola", "INPUT")) is Verdict.ERROR


def test_null_guardrail_aprueba() -> None:
    assert run(NullGuardrail().check("x", "INPUT")) is Verdict.APPROVED


def test_audio_gate_retiene_libera_y_descarta() -> None:
    g = AudioGate()
    assert g.push("a") == ["a"]
    g.hold()
    assert g.push("b") == [] and g.push("c") == []
    assert g.approve() == ["b", "c"] and g.state == "open"
    assert g.push("d") == ["d"]
    g.hold()
    g.push("e")
    g.reject()
    assert g.push("f") == [] and g.state == "rejected"
    assert g.approve() == []
    g.reset()
    assert g.push("g") == ["g"]


def test_separa_oraciones_completas_del_resto() -> None:
    assert split_sentences("Hola. ¿Cómo estás? Bien") == (["Hola.", "¿Cómo estás?"], "Bien")
    assert split_sentences("Hola. Adiós.") == (["Hola.", "Adiós."], "")
    assert split_sentences("") == ([], "")
    assert split_sentences("sin punto") == ([], "sin punto")


def test_primera_oracion_bloqueada_del_asesor() -> None:
    class ByWord:
        async def check(self, text: str, source: str) -> Verdict:
            assert source == "OUTPUT"
            return Verdict.INTERVENED if "mezcla" in text else Verdict.APPROVED

    assert run(first_blocked_sentence(ByWord(), ["Hola.", "Puedes mezcla eso.", "Fin."])) == ("Puedes mezcla eso.", Verdict.INTERVENED)
    assert run(first_blocked_sentence(ByWord(), ["Hola."])) is None


# ---- Feature: skincare-voice-advisor, Property 16: el watchdog se dispara solo tras más de 3 s de silencio -------

@given(gap=st.floats(min_value=0, max_value=10, allow_nan=False))
def test_el_watchdog_se_dispara_solo_si_el_silencio_supera_3_s(gap: float) -> None:
    now = [100.0]
    dog = InputWatchdog(clock=lambda: now[0])
    dog.on_audio()
    now[0] += gap
    assert dog.should_fire() == (gap > 3.0)


def test_el_watchdog_no_se_dispara_antes_del_primer_audio_y_avisa_una_vez() -> None:
    now = [0.0]
    dog = InputWatchdog(clock=lambda: now[0])
    now[0] = 50
    assert not dog.should_fire()
    dog.on_audio()
    now[0] += 3.0
    assert not dog.should_fire()  # exactamente 3 s todavía no
    now[0] += 0.01
    assert dog.should_fire()
    assert not dog.should_fire()  # una sola vez
    dog.on_audio()  # llega audio: se vuelve a armar
    now[0] += 4
    assert dog.should_fire()


def test_renovacion_falla_despues_de_5_s() -> None:
    now = [0.0]
    timer = RenewalTimer(clock=lambda: now[0])
    assert not timer.failed()
    timer.start()
    now[0] = 5.0
    assert timer.active and not timer.failed()
    now[0] = 5.1
    assert timer.failed()
    timer.complete()
    assert not timer.failed() and not timer.active


def test_latencia_de_respuesta_se_mide_una_vez_por_turno_y_sale_en_emf() -> None:
    now = [10.0]
    tracker = LatencyTracker(clock=lambda: now[0])
    assert tracker.first_audio() is None
    tracker.user_turn_ended()
    now[0] = 11.25
    assert tracker.first_audio() == 1250.0
    assert tracker.first_audio() is None
    doc = json.loads(emf_line("ResponseLatencyMs", 1250.0))
    assert doc["ResponseLatencyMs"] == 1250.0
    [metrics] = doc["_aws"]["CloudWatchMetrics"]
    assert metrics["Metrics"] == [{"Name": "ResponseLatencyMs", "Unit": "Milliseconds"}]


# ---- Knowledge Base + DynamoDB -------------------------------------------------------------------------------

def test_el_sku_sale_de_la_ubicacion_s3() -> None:
    assert sku_from_uri("s3://kb/productos/000375947.md") == "000375947"
    assert sku_from_uri("s3://kb/productos/000375947.md.metadata.json") is None
    assert sku_from_uri("") is None


def item(sku: str, paso: str = "Limpieza", precio: str = "100") -> dict[str, Any]:
    return {"sku": sku, "nombre": f"P{sku}", "marca": "M", "paso_rutina": paso, "tipo_piel": "Seca",
            "precio": Decimal(precio), "beneficios": "b", "ingredientes": "i", "modo_uso": "u", "imagen_url": "", "inventario": Decimal(3)}


def test_product_from_item_conserva_sku_y_precio_exactos() -> None:
    p = product_from_item(item("000000111", precio="1299.50"))
    assert p.sku == "000000111" and p.precio == Decimal("1299.50") and p.inventario == 3


@pytest.fixture
def products_table(aws):
    ddb = boto3.resource("dynamodb")
    table = ddb.create_table(
        TableName="ultra-productos", KeySchema=[{"AttributeName": "sku", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "sku", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST",
    )
    for i in range(1, 251):
        table.put_item(Item=item(f"{i:09d}", "Limpieza" if i % 2 else "Tratamiento"))
    return table


class FakeKb:
    def __init__(self, results: list[str], fail: bool = False) -> None:
        self.results, self.fail, self.kwargs = results, fail, None

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError("kb caída")
        return {"retrievalResults": [{"location": {"s3Location": {"uri": f"s3://kb/productos/{s}.md"}}} for s in self.results]}


def kb_catalog(kb: FakeKb) -> KnowledgeBaseCatalog:
    return KnowledgeBaseCatalog(knowledge_base_id="KB1", products_table="ultra-productos", agent_runtime=kb,
                                dynamodb_client=boto3.client("dynamodb"))


def test_search_filtra_por_paso_lee_de_dynamo_y_conserva_el_orden(products_table) -> None:
    kb = FakeKb(["000000005", "000000001", "000000005", "000000003", "999999999"])
    found = run(kb_catalog(kb).search("Limpieza", "piel seca"))
    assert [p.sku for p in found] == ["000000005", "000000001", "000000003"]
    cfg = kb.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert cfg["filter"] == {"equals": {"key": "paso_rutina", "value": "Limpieza"}}
    assert kb.kwargs["retrievalQuery"] == {"text": "piel seca"}


def test_search_descarta_productos_de_otro_paso(products_table) -> None:
    found = run(kb_catalog(FakeKb(["000000002", "000000001"])).search("Limpieza"))
    assert [p.sku for p in found] == ["000000001"]  # 2 es Tratamiento


def test_get_many_lee_mas_de_100_skus(products_table) -> None:
    skus = [f"{i:09d}" for i in range(1, 251)] + ["000000001"]
    found = run(kb_catalog(FakeKb([])).get_many(skus))
    assert len(found) == 250


def test_si_la_kb_falla_search_lanza(products_table) -> None:
    with pytest.raises(RuntimeError):
        run(kb_catalog(FakeKb([], fail=True)).search("Limpieza"))

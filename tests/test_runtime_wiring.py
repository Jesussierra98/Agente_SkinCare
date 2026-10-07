"""Armado de las piezas de producción según el entorno (`advisor/runtime.py`) y arranque de `server.py`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hypothesis import given, strategies as st

from advisor import config as cfg_module
from advisor.dynamo import DynamoRecommendations
from advisor.guardrail import TOPIC_CHEMICAL, TOPIC_MEDICAL, TurnGate, Verdict
from advisor.handoff import SnsHandoffNotifier
from advisor.local import InMemoryRecommendations, LogNotifier
from advisor.prompts import (
    HANDOFF_INSTRUCTIONS,
    HANDOFF_WAIT_INSTRUCTION,
    NO_AUDIO_INSTRUCTION,
    PUBMED_ADDENDUM,
    SYSTEM_PROMPT,
)
from advisor.pubmed import DynamoEvidenceCache, InMemoryEvidenceCache, PubMedConsultant
from advisor.runtime import build_runtime
from advisor.session_store import SessionStore

BASE = {"BUDGET_TIER_BOUNDS_MXN": "800,2000"}


class FakeTable:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_item(self, **_: Any) -> dict[str, Any]:
        return {"Item": {"handoff": {"estado": "confirmada"}}}


class FakeDynamo:
    def Table(self, name: str) -> FakeTable:  # noqa: N802 - nombre de la API de boto3
        return FakeTable(name)


class FakeSns:
    pass


def runtime_for(extra: dict[str, str] | None = None):
    env = BASE | (extra or {})
    return build_runtime(cfg_module.load_config(env), env, bedrock=object(), dynamodb=FakeDynamo(), sns=FakeSns())


def test_sin_variables_de_aws_todo_sigue_siendo_local() -> None:
    rt = runtime_for()
    assert isinstance(rt.recommendations, InMemoryRecommendations)
    assert isinstance(rt.notifier, LogNotifier)
    assert rt.gate is None and rt.session_store is None and rt.confirmation_check is None and rt.pubmed is None
    assert rt.routine_guardrail is None and rt.system_prompt == SYSTEM_PROMPT
    assert set(rt.describe().values()) == {False}


def test_con_el_entorno_de_produccion_completo_se_activan_todas_las_piezas() -> None:
    rt = runtime_for(
        {
            "RECOMMENDATIONS_TABLE": "ultra-recomendaciones",
            "SESSIONS_TABLE": "ultra-sesiones",
            "HANDOFF_TOPIC_ARN": "arn:aws:sns:us-east-1:123:ultra-skincare-handoff",
            "GUARDRAIL_ID": "gr-1",
            "GUARDRAIL_VERSION": "3",
            "PUBMED_ENABLED": "true",
            "EVIDENCE_TABLE": "ultra-evidencias-ingredientes",
        }
    )
    assert isinstance(rt.recommendations, DynamoRecommendations)
    assert isinstance(rt.notifier, SnsHandoffNotifier)
    assert isinstance(rt.gate, TurnGate) and isinstance(rt.session_store, SessionStore)
    assert isinstance(rt.pubmed, PubMedConsultant)
    assert rt.routine_guardrail == {"guardrailIdentifier": "gr-1", "guardrailVersion": "3"}
    assert set(rt.describe().values()) == {True}


def test_cada_pieza_exige_todas_sus_variables() -> None:
    only_topic = runtime_for({"HANDOFF_TOPIC_ARN": "arn:aws:sns:us-east-1:123:t"})
    assert isinstance(only_topic.notifier, LogNotifier)  # sin SESSIONS_TABLE no hay dónde registrar la derivación

    half_guardrail = runtime_for({"GUARDRAIL_ID": "gr-1"})
    assert half_guardrail.gate is None and half_guardrail.routine_guardrail is None
    half_guardrail = runtime_for({"GUARDRAIL_VERSION": "1"})
    assert half_guardrail.gate is None and half_guardrail.routine_guardrail is None


def test_la_confirmacion_del_asesor_se_consulta_en_la_tabla_de_sesiones() -> None:
    rt = runtime_for({"SESSIONS_TABLE": "ultra-sesiones"})
    assert rt.confirmation_check is not None
    assert asyncio.run(rt.confirmation_check("sid")()) is True


def test_pubmed_agrega_sus_instrucciones_al_prompt_solo_cuando_esta_activo() -> None:
    off = runtime_for()
    assert "evidencia_ingrediente" not in off.system_prompt
    on = runtime_for({"PUBMED_ENABLED": "true"})
    assert on.system_prompt == SYSTEM_PROMPT + PUBMED_ADDENDUM
    assert isinstance(on.pubmed, PubMedConsultant)
    assert "lecturas_en_pantalla" in on.system_prompt and "No las leas" in on.system_prompt
    assert isinstance(runtime_for({"PUBMED_ENABLED": "true"}).pubmed._cache, InMemoryEvidenceCache)  # noqa: SLF001
    assert isinstance(
        runtime_for({"PUBMED_ENABLED": "true", "EVIDENCE_TABLE": "e"}).pubmed._cache, DynamoEvidenceCache  # noqa: SLF001
    )


def test_pubmed_solo_con_true_exacto() -> None:
    for value in ("false", "1", "yes", ""):
        assert runtime_for({"PUBMED_ENABLED": value}).pubmed is None


# ---- los textos que el servidor necesita siempre existen --------------------------------------------------

def test_hay_instruccion_de_derivacion_para_todo_motivo_que_puede_producir_el_turngate() -> None:
    produced = {"condicion_sensible", "alergia_producto", "diagnostico", "compatibilidad"}
    assert produced <= set(HANDOFF_INSTRUCTIONS)
    for table in (HANDOFF_INSTRUCTIONS[m] for m in produced):
        assert set(table) == {"es", "en"}


def test_instrucciones_internas_en_ambos_idiomas() -> None:
    for table in (NO_AUDIO_INSTRUCTION, HANDOFF_WAIT_INSTRUCTION):
        assert set(table) == {"es", "en"} and all(text.strip() for text in table.values())


class _Checker:
    def __init__(self, status: str, topics: tuple[str, ...]) -> None:
        self.status, self.topics = status, topics

    async def check(self, text: str, source: str) -> Verdict:
        return Verdict(self.status, self.topics)  # type: ignore[arg-type]


@given(
    text=st.text(max_size=60),
    status=st.sampled_from(["approved", "intervened", "error"]),
    topics=st.sampled_from([(), (TOPIC_MEDICAL,), (TOPIC_CHEMICAL,), ("tema_desconocido",)]),
)
def test_cualquier_motivo_que_produzca_el_turngate_tiene_instruccion_de_derivacion(text: str, status: str, topics: tuple[str, ...]) -> None:
    gate = TurnGate(_Checker(status, topics))
    for decision in (asyncio.run(gate.evaluate_input(text)), asyncio.run(gate.evaluate_output(text))):
        if decision.handoff_motivo is not None:
            assert decision.handoff_motivo in HANDOFF_INSTRUCTIONS  # el servidor hace HANDOFF_INSTRUCTIONS[motivo][idioma]
        assert decision.allowed or decision.handoff_motivo is not None  # un bloqueo siempre deriva


# ---- arranque real de server.py ----------------------------------------------------------------------------------

def test_server_arranca_y_health_informa_las_piezas_activas(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    for name in (
        "RECOMMENDATIONS_TABLE", "SESSIONS_TABLE", "HANDOFF_TOPIC_ARN", "GUARDRAIL_ID", "GUARDRAIL_VERSION",
        "PUBMED_ENABLED", "EVIDENCE_TABLE", "KB_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from fastapi.testclient import TestClient

    import server

    body = TestClient(server.app).get("/health").json()
    assert body["ok"] is True and body["productos"] > 0
    assert body["piezas"] == {
        "recomendaciones_dynamodb": False,
        "aviso_sns": False,
        "guardrail": False,
        "sesion_persistente": False,
        "pubmed": False,
    }

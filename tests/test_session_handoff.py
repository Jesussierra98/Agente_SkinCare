"""Código corto, niveles de presupuesto por entorno y derivación al asesor (Req. 9, 13)."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, strategies as st

from advisor.session import Session, generate_codigo_corto, now_iso, price_tier_bounds, start_handoff

CODE_RE = re.compile(r"^[A-HJ-NP-Z]{3}-[2-9]{3}$")  # letras sin I ni O, dígitos del 2 al 9


# ---- Feature: skincare-voice-advisor, Property 32: formato del Codigo_Corto --------------------------

def test_codigo_corto_tiene_el_formato_definido() -> None:
    for _ in range(2000):
        code = generate_codigo_corto()
        assert CODE_RE.match(code), code
        assert not set("IO01") & set(code.replace("-", ""))


def test_codigo_corto_no_es_constante() -> None:
    assert len({generate_codigo_corto() for _ in range(50)}) > 1


def test_now_iso_es_utc_con_sufijo_z() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now_iso())


# ---- fronteras de presupuesto -----------------------------------------------------------------------

def test_price_tier_bounds_validos() -> None:
    assert price_tier_bounds("800,2000") == (Decimal("800"), Decimal("2000"))
    assert price_tier_bounds(" 1300 , 3500 ") == (Decimal("1300"), Decimal("3500"))


@pytest.mark.parametrize("raw", ["", "abc", "2000,800", "0,5", "1,2,3", "500", "-1,10", "800;2000"])
def test_price_tier_bounds_invalidos_usan_el_valor_seguro(raw: str) -> None:
    assert price_tier_bounds(raw) == (Decimal("800"), Decimal("2000"))


@given(st.text(max_size=40))
def test_price_tier_bounds_siempre_devuelve_fronteras_ordenadas(raw: str) -> None:
    low, high = price_tier_bounds(raw)
    assert 0 < low < high


# ---- derivación --------------------------------------------------------------------------------------

class RecordingNotifier:
    def __init__(self, fail: Exception | None = None, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._fail = fail
        self._delay = delay

    async def notify(self, session_id: str, motivo: str, perfil: dict[str, Any]) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise self._fail
        self.calls.append((session_id, motivo, perfil))


def new_session() -> tuple[Session, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    async def emit(message: dict[str, Any]) -> None:
        events.append(message)

    return Session(emit=emit), events


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.parametrize("motivo", ["condicion_sensible", "requiere_asesor"])
def test_estos_motivos_suspenden_las_recomendaciones(motivo: str) -> None:
    session, events = new_session()
    notifier = RecordingNotifier()
    record = run(start_handoff(session, notifier, motivo))
    assert session.recommendations_suspended
    assert record["estado"] == "pendiente" and record["notificado"] is True
    assert events == [{"type": "handoff", "motivo": motivo, "estado": "pendiente"}]


@pytest.mark.parametrize("motivo", ["diagnostico", "compatibilidad", "alergia_producto"])
def test_estos_motivos_no_suspenden_las_recomendaciones(motivo: str) -> None:
    session, _ = new_session()
    run(start_handoff(session, RecordingNotifier(), motivo))
    assert not session.recommendations_suspended


# ---- Feature: skincare-voice-advisor, Property 25: entrega motivo y perfil sin cambios ---------------

def test_la_derivacion_entrega_el_motivo_y_el_perfil_sin_cambios() -> None:
    session, _ = new_session()
    session.profile.apply("tipo_piel", "seca/tensa")
    session.profile.apply("presupuesto", "$")
    session.profile.register_exchange()
    expected_profile = session.profile.to_dict()
    notifier = RecordingNotifier()
    run(start_handoff(session, notifier, "condicion_sensible"))
    assert notifier.calls == [(session.session_id, "condicion_sensible", expected_profile)]
    assert session.handoff is not None and session.handoff["perfil"] == expected_profile


def test_el_mismo_motivo_no_se_notifica_dos_veces() -> None:
    session, events = new_session()
    notifier = RecordingNotifier()
    first = run(start_handoff(session, notifier, "diagnostico"))
    second = run(start_handoff(session, notifier, "diagnostico"))
    assert first is second
    assert len(notifier.calls) == 1 and len(events) == 1


def test_otro_motivo_distinto_si_se_notifica() -> None:
    session, _ = new_session()
    notifier = RecordingNotifier()
    run(start_handoff(session, notifier, "diagnostico"))
    run(start_handoff(session, notifier, "condicion_sensible"))
    assert [c[1] for c in notifier.calls] == ["diagnostico", "condicion_sensible"]


# ---- Feature: skincare-voice-advisor, Property 24: plazo de notificación ---------------------------

def test_si_la_notificacion_falla_se_suspende_y_se_marca_sin_notificar() -> None:
    session, events = new_session()
    record = run(start_handoff(session, RecordingNotifier(fail=RuntimeError("sns caído")), "diagnostico"))
    assert record["estado"] == "sin_notificar" and record["notificado"] is False
    assert session.recommendations_suspended
    assert events[0]["estado"] == "sin_notificar"


def test_si_la_notificacion_tarda_mas_que_el_plazo_se_suspende() -> None:
    session, _ = new_session()
    record = run(start_handoff(session, RecordingNotifier(delay=1.0), "compatibilidad", notify_timeout_s=0.05))
    assert record["estado"] == "sin_notificar"
    assert session.recommendations_suspended


def test_cada_sesion_tiene_un_id_unico() -> None:
    assert new_session()[0].session_id != new_session()[0].session_id

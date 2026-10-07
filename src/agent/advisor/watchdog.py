"""Watchdogs de la sesión y métrica de latencia (Req. 6.9, 5.2 y 24). Lógica pura con reloj inyectable."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

INPUT_SILENCE_S = 3.0


class InputAudioWatchdog:
    """Detecta que el cliente dejó de mandar audio.

    Se dispara una sola vez por silencio, cuando pasan MÁS de 3 s desde el último marco. Al volver el audio se rearma.
    """

    def __init__(self, threshold_s: float = INPUT_SILENCE_S, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._threshold = threshold_s
        self._now = monotonic
        self._last: float | None = None
        self._fired = False

    def on_audio(self) -> None:
        self._last = self._now()
        self._fired = False

    def poll(self) -> bool:
        """`True` exactamente una vez cuando el silencio supera el umbral."""
        if self._last is None or self._fired:
            return False
        if self._now() - self._last > self._threshold:
            self._fired = True
            return True
        return False


class ResponseLatencyTracker:
    """`ResponseLatencyMs`: de la transcripción final del cliente al primer audio del asesor."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._now = monotonic
        self._started: float | None = None

    def user_finished(self) -> None:
        self._started = self._now()

    def first_audio(self) -> float | None:
        """Milisegundos desde que el cliente terminó, o `None` si no hay turno abierto. Cierra el turno."""
        if self._started is None:
            return None
        elapsed = (self._now() - self._started) * 1000
        self._started = None
        return elapsed


def emf_metric(name: str, value: float, unit: str = "Milliseconds", namespace: str = "UltraSkincare", timestamp_ms: int | None = None) -> str:
    """Línea JSON en formato CloudWatch Embedded Metric Format (CloudWatch la convierte en métrica al leer los logs)."""
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [{"Namespace": namespace, "Dimensions": [[]], "Metrics": [{"Name": name, "Unit": unit}]}],
        },
        name: round(value, 1),
    }
    return json.dumps(payload)

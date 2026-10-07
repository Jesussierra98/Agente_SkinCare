"""Supervisión de la sesión de voz (Req. 6.9, 24.9, 24.10, 24.11, 5.2).

Piezas puras con reloj inyectable para probarlas sin esperar:
- `InputWatchdog`: más de 3 s sin marcos de audio de entrada.
- `RenewalTimer`: una renovación de la conexión con Nova que tarda más de 5 s se reporta como fallida.
- `LatencyTracker` + `emf_line`: métrica `ResponseLatencyMs` en formato EMF de CloudWatch.
"""

from __future__ import annotations

import json
import time
from typing import Callable

SILENCE_LIMIT_S = 3.0
RENEWAL_LIMIT_S = 5.0
METRIC_NAMESPACE = "UltraSkincare"

Clock = Callable[[], float]


class InputWatchdog:
    """Se dispara UNA vez cuando pasan más de 3 s sin audio; vuelve a armarse cuando llega audio nuevo."""

    def __init__(self, limit_s: float = SILENCE_LIMIT_S, clock: Clock = time.monotonic) -> None:
        self._limit_s = limit_s
        self._clock = clock
        self._last_audio: float | None = None
        self._fired = False

    def on_audio(self) -> None:
        self._last_audio = self._clock()
        self._fired = False

    def should_fire(self) -> bool:
        """`True` si el flujo lleva estrictamente más de `limit_s` sin audio y aún no se avisó."""
        if self._last_audio is None or self._fired:
            return False
        if self._clock() - self._last_audio > self._limit_s:
            self._fired = True
            return True
        return False


class RenewalTimer:
    """Vigila una renovación de conexión: a los 5 s sin completarse se considera fallida."""

    def __init__(self, limit_s: float = RENEWAL_LIMIT_S, clock: Clock = time.monotonic) -> None:
        self._limit_s = limit_s
        self._clock = clock
        self._started: float | None = None

    @property
    def active(self) -> bool:
        return self._started is not None

    def start(self) -> None:
        self._started = self._clock()

    def complete(self) -> None:
        self._started = None

    def failed(self) -> bool:
        return self._started is not None and self._clock() - self._started > self._limit_s


class LatencyTracker:
    """Milisegundos entre el fin de la frase del cliente y el primer audio de la respuesta."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._turn_end: float | None = None

    def user_turn_ended(self) -> None:
        self._turn_end = self._clock()

    def first_audio(self) -> float | None:
        """Latencia del turno en ms, una sola vez por turno; `None` si no hay turno pendiente."""
        if self._turn_end is None:
            return None
        elapsed = (self._clock() - self._turn_end) * 1000.0
        self._turn_end = None
        return round(elapsed, 1)


def emf_line(name: str, value: float, unit: str = "Milliseconds", **dimensions: str) -> str:
    """Línea JSON en Embedded Metric Format: CloudWatch la convierte en métrica al leerla de los logs."""
    doc: dict[str, object] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [list(dimensions)] if dimensions else [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
    }
    doc.update(dimensions)
    return json.dumps(doc, ensure_ascii=False)

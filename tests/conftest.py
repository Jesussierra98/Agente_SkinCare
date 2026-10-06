"""Configuración común de pruebas: Hypothesis con al menos 100 ejemplos por propiedad."""

from hypothesis import HealthCheck, settings

settings.register_profile(
    "skincare",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("skincare")

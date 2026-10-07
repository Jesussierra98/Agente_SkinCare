"""Configuración por variables de entorno. Cada valor inválido usa uno seguro y se registra."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .guide import Guide
from .session import price_tier_bounds

log = logging.getLogger("advisor.config")

ENDPOINTING_VALUES = ("HIGH", "MEDIUM", "LOW")
VOICES = ("tiffany", "matthew")
SAMPLE_RATES = (16000, 24000)


def parse_endpointing(raw: str | None) -> str:
    """`HIGH`, `MEDIUM` o `LOW` exactos; cualquier otra cosa usa `MEDIUM` y se registra."""
    if raw in ENDPOINTING_VALUES:
        return raw  # type: ignore[return-value]
    if raw is not None:
        log.warning("ENDPOINTING_SENSITIVITY inválido (%r); usando MEDIUM", raw)
    return "MEDIUM"


def parse_restart_after(raw: str | None) -> int:
    """Segundos entre 0 y 480 (exclusivos); otro valor usa 420 y se registra."""
    if raw is None:
        return 420
    try:
        value = float(raw)
        # Se compara el entero resultante: "0.5" se trunca a 0 y no cumple 0 < n < 480.
        if 0 < value < 480 and 0 < int(value) < 480:
            return int(value)
    except ValueError:
        pass
    log.warning("NOVA_RESTART_AFTER_S inválido (%r); usando 420", raw)
    return 420


def parse_voice(raw: str | None) -> str:
    if raw in VOICES:
        return raw  # type: ignore[return-value]
    if raw is not None:
        log.warning("NOVA_VOICE_ID inválido (%r); usando tiffany", raw)
    return "tiffany"


def parse_sample_rate(raw: str | None) -> int:
    try:
        if raw is not None and int(raw) in SAMPLE_RATES:
            return int(raw)
    except ValueError:
        pass
    if raw is not None:
        log.warning("OUTPUT_SAMPLE_RATE inválido (%r); usando 24000", raw)
    return 24000


def parse_min_exchanges(raw: str | None) -> int:
    try:
        if raw is not None and 1 <= int(raw) <= 10:
            return int(raw)
    except ValueError:
        pass
    return 5


@dataclass(frozen=True)
class Config:
    region: str
    nova_model_id: str
    voice: str
    endpointing: str
    restart_after_s: int
    output_sample_rate: int
    routine_model_id: str
    budget_bounds: tuple[Decimal, Decimal]
    store_domain: str
    catalog_path: Path
    guide_path: Path
    min_exchanges: int
    guardrail_id: str
    guardrail_version: str
    kb_id: str
    pubmed_enabled: bool
    products_table: str
    sessions_table: str
    recommendations_table: str
    handoff_topic_arn: str
    handoff_confirm_url: str
    production: bool


def parse_bool(raw: str | None) -> bool:
    """`true`, `1`, `yes` u `on` (sin distinguir mayúsculas) activan; cualquier otro valor es `false`."""
    return (raw or "").strip().lower() in {"true", "1", "yes", "on"}


def default_catalog(repo_root: Path) -> Path:
    """Catálogo real procesado por el ETL (`out/etl/catalog_normalized.json`) si existe; si no, el de muestra."""
    processed = repo_root / "out" / "etl" / "catalog_normalized.json"
    return processed if processed.exists() else repo_root / "catalog" / "sample_catalog.json"


def load_config(env: dict[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    repo_root = Path(__file__).resolve().parents[3]
    guide_path = Path(e.get("GUIDE_PATH") or str(repo_root / "src" / "etl" / "config" / "guide.json"))
    # Fronteras $/$$/$$$: las de la guía del negocio, salvo que se indiquen por variable de entorno.
    bounds_raw = e.get("BUDGET_TIER_BOUNDS_MXN")
    bounds = price_tier_bounds(bounds_raw) if bounds_raw else Guide.load(guide_path).bounds()
    return Config(
        region=e.get("AWS_REGION", e.get("AWS_DEFAULT_REGION", "us-east-1")),
        nova_model_id=e.get("NOVA_MODEL_ID", "amazon.nova-2-sonic-v1:0"),
        voice=parse_voice(e.get("NOVA_VOICE_ID")),
        endpointing=parse_endpointing(e.get("ENDPOINTING_SENSITIVITY")),
        restart_after_s=parse_restart_after(e.get("NOVA_RESTART_AFTER_S")),
        output_sample_rate=parse_sample_rate(e.get("OUTPUT_SAMPLE_RATE")),
        routine_model_id=e.get("ROUTINE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        budget_bounds=bounds,
        store_domain=e.get("STORE_DOMAIN", "http://localhost:5173"),
        catalog_path=Path(e.get("CATALOG_PATH") or str(default_catalog(repo_root))),
        guide_path=guide_path,
        min_exchanges=parse_min_exchanges(e.get("MIN_EXCHANGES")),
        guardrail_id=e.get("GUARDRAIL_ID", ""),
        guardrail_version=e.get("GUARDRAIL_VERSION", ""),
        kb_id=e.get("KB_ID", ""),
        pubmed_enabled=parse_bool(e.get("PUBMED_ENABLED")),
        products_table=e.get("PRODUCTS_TABLE", "ultra-productos"),
        sessions_table=e.get("SESSIONS_TABLE", "ultra-sesiones"),
        recommendations_table=e.get("RECOMMENDATIONS_TABLE", "ultra-recomendaciones"),
        handoff_topic_arn=e.get("HANDOFF_TOPIC_ARN", ""),
        handoff_confirm_url=e.get("HANDOFF_CONFIRM_URL", ""),
        production=parse_bool(e.get("PRODUCTION")),
    )

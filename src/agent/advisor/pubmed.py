"""Consultor_PubMed: lecturas científicas de un ingrediente de la rutina (Req. 16 y 17).

Reglas del diseño (3.9):
- Solo se consultan ingredientes que pertenezcan a algún producto de la rutina. Si no, se rechaza SIN leer la caché,
  SIN llamar a NCBI y SIN escribir.
- Caché por ingrediente normalizado, vigente 30 días.
- NCBI: `esearch` (máximo 5) y `esummary`, 5 s por llamada y 10 s en total. Un éxito, incluso con 0 artículos, se guarda;
  una falla devuelve vacío y no escribe.
- El modelo de voz nunca recibe títulos ni PMID: eso lo garantiza la herramienta que usa este módulo.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Literal, Protocol

log = logging.getLogger("advisor.pubmed")

CACHE_DAYS = 30
MAX_ARTICLES = 5
CALL_TIMEOUT_S = 5.0
TOTAL_TIMEOUT_S = 10.0
MAX_TITLE = 300
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

Status = Literal["rechazado", "ok", "vacio", "error"]


# --------------------------------------------------------------------------- puro

def normalize_ingredient(text: str) -> str:
    """NFKD, sin marcas diacríticas, `casefold` y espacios colapsados."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    no_marks = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_marks.casefold().split())


def split_ingredients(text: str) -> list[str]:
    """Elementos normalizados de una lista de ingredientes separada por comas, punto y coma o saltos de línea."""
    return [item for part in re.split(r"[,;\n\r]+", text or "") if (item := normalize_ingredient(part))]


def ingredient_in_routine(ingredient: str, ingredient_texts: Iterable[str]) -> bool:
    """`True` si el ingrediente coincide con un elemento COMPLETO de la lista de algún producto de la rutina."""
    wanted = normalize_ingredient(ingredient)
    if not wanted:
        return False
    return any(wanted in split_ingredients(text) for text in ingredient_texts)


@dataclass
class PubMedResult:
    status: Status
    articulos: list[dict[str, str]] = field(default_factory=list)
    from_cache: bool = False


# ------------------------------------------------------------------------- caché

class EvidenceCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None:
        """Ítem con `articulos` y `fetched_at` (ISO 8601 UTC), o `None`."""

    def put(self, key: str, articulos: list[dict[str, str]], now: datetime) -> None: ...


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InMemoryEvidenceCache:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.reads = 0
        self.writes = 0

    def get(self, key: str) -> dict[str, Any] | None:
        self.reads += 1
        return self.items.get(key)

    def put(self, key: str, articulos: list[dict[str, str]], now: datetime) -> None:
        self.writes += 1
        self.items[key] = {"ingrediente": key, "articulos": articulos, "fetched_at": _iso(now)}


class DynamoEvidenceCache:
    """`ultra-evidencias-ingredientes`: clave `ingrediente`, atributos `articulos`, `fetched_at` y `ttl` (+30 días)."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def get(self, key: str) -> dict[str, Any] | None:
        return self._table.get_item(Key={"ingrediente": key}).get("Item")

    def put(self, key: str, articulos: list[dict[str, str]], now: datetime) -> None:
        ttl = int((now + timedelta(days=CACHE_DAYS)).timestamp())
        self._table.put_item(
            Item={"ingrediente": key, "articulos": articulos, "fetched_at": _iso(now), "ttl": ttl}
        )


# -------------------------------------------------------------------------- NCBI

Transport = Callable[[str, dict[str, str], float], dict[str, Any]]


def ncbi_transport(endpoint: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
    """GET a E-utilities que devuelve el JSON. La API key viaja en `params` y nunca se registra."""
    url = NCBI_BASE + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "skincare-voice-advisor/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL fija de NCBI
        return json.loads(response.read().decode("utf-8"))


class PubMedConsultant:
    def __init__(
        self,
        cache: EvidenceCache,
        transport: Transport = ncbi_transport,
        api_key: Callable[[], str | None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = cache
        self._transport = transport
        self._api_key = api_key
        self._clock = clock
        self._monotonic = monotonic

    # ---- caché ---------------------------------------------------------------------------------------
    def _cached(self, key: str, now: datetime) -> list[dict[str, str]] | None:
        try:
            item = self._cache.get(key)
        except Exception as exc:  # noqa: BLE001 - una caché caída no debe impedir consultar NCBI
            log.warning("caché de evidencias no disponible (%s)", type(exc).__name__)
            return None
        if not item:
            return None
        try:
            fetched = datetime.strptime(item["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError, TypeError):
            return None
        if now - fetched > timedelta(days=CACHE_DAYS):
            return None
        return list(item.get("articulos", []))

    # ---- NCBI ----------------------------------------------------------------------------------------
    def _fetch(self, term: str) -> list[dict[str, str]]:
        """Lanza una excepción ante cualquier falla, incluido pasar de 10 s en total."""
        started = self._monotonic()

        def remaining() -> float:
            left = TOTAL_TIMEOUT_S - (self._monotonic() - started)
            if left <= 0:
                raise TimeoutError("se agotaron los 10 s")
            return min(CALL_TIMEOUT_S, left)

        key = self._api_key()
        common = {"db": "pubmed", "retmode": "json", **({"api_key": key} if key else {})}
        found = self._transport("esearch.fcgi", {**common, "term": term, "retmax": str(MAX_ARTICLES)}, remaining())
        ids = [str(i) for i in found["esearchresult"]["idlist"]][:MAX_ARTICLES]
        if not ids:
            return []
        summary = self._transport("esummary.fcgi", {**common, "id": ",".join(ids)}, remaining())
        articles: list[dict[str, str]] = []
        for pmid in ids:
            title = str(summary["result"].get(pmid, {}).get("title", "")).strip()
            if title:
                articles.append({"titulo": title[:MAX_TITLE], "pmid": pmid})
        return articles

    # ---- entrada -------------------------------------------------------------------------------------
    def lookup(self, ingredient: str, routine_ingredient_texts: Iterable[str]) -> PubMedResult:
        if not ingredient_in_routine(ingredient, routine_ingredient_texts):
            return PubMedResult("rechazado")  # sin leer la caché, sin llamar a NCBI y sin escribir
        key = normalize_ingredient(ingredient)
        now = self._clock()
        cached = self._cached(key, now)
        if cached is not None:
            return PubMedResult("ok" if cached else "vacio", cached, from_cache=True)
        try:
            articles = self._fetch(ingredient.strip())
        except Exception as exc:  # noqa: BLE001 - NCBI o Secrets Manager caídos: vacío y sin escribir
            log.warning("no se pudo consultar PubMed (%s)", type(exc).__name__)
            return PubMedResult("error")
        try:
            self._cache.put(key, articles, now)
        except Exception as exc:  # noqa: BLE001 - no guardar en la caché no invalida el resultado
            log.warning("no se pudo escribir en la caché de evidencias (%s)", type(exc).__name__)
        return PubMedResult("ok" if articles else "vacio", articles)

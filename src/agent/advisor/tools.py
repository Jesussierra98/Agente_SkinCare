"""Herramientas que el modelo de voz puede llamar.

Reglas de diseño:
- Ninguna herramienta recibe datos de producto del modelo (solo categorías, cifras del cliente o un SKU
  que ya esté en los candidatos de la sesión). Todo sale del estado del servidor y del catálogo.
- El servidor hace el trabajo mecánico: `armar_rutina` busca los 4 pasos, arma la rutina, la guarda y
  publica el QR en una sola llamada; `ajustar_rutina` la modifica sin cambiar el código. El modelo solo habla.
- La guía del negocio (`guide.json`) orienta la elección: sus combinaciones curadas pasan primero, pero el
  catálogo no las contiene todas, así que nunca es una receta rígida.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from strands import tool

from .guide import LEVELS, Guide
from .models import PASOS, Product, money
from .ports import CatalogRepository, HandoffNotifier, RecommendationStore
from .profile import TIPO_PIEL_CATALOGO, profile_query, tier_for_price
from .pubmed import TOTAL_TIMEOUT_S
from .routine import RoutineEngine, rank_candidates, total_price
from .save import validate_routine_for_save
from .session import Session, generate_codigo_corto, now_iso, start_handoff

log = logging.getLogger("advisor.tools")

SEARCH_TIMEOUT_S = 5.0
MAX_CODE_ATTEMPTS = 5
MAX_ADJUSTMENTS = 8
CANDIDATES_PER_STEP = 5
CANDIDATES_WITH_TOTAL_CAP = 10  # más opciones cuando hay que ajustar el total con código
AUTO_CHEAPER_STEPS = 2  # si piden "más barato" sin decir qué paso, se abaratan primero los 2 más caros
MIN_PRICE_RATIO = Decimal("0.5")  # un sustituto más barato no baja de este % del precio actual (no es "lo más barato")

# Cómo preguntar por cada dato que falta, en lenguaje cotidiano (preguntas de la guía del negocio).
_HOW_TO_ASK = {
    "tipo_piel": "cómo describiría su piel la mayor parte del tiempo (grasa con tendencia a brotes, normal, mixta o deshidratada, o seca y tirante)",
    "inquietud": "cuál es su principal preocupación (brotes o imperfecciones, falta de hidratación o piel apagada, primeras líneas o pérdida de firmeza, o arrugas profundas y flacidez)",
    "textura": "cómo siente su piel después de lavarla (brillante o con exceso de grasa, cómoda pero con ligera resequedad, normal pero empieza a marcar líneas, o muy seca y áspera)",
    "presupuesto": "cuánto suele invertir en un producto de cuidado facial (hasta unos 1,300 pesos, entre 1,300 y 3,500, o más de 3,500)",
}


@dataclass
class Deps:
    catalog: CatalogRepository
    store: RecommendationStore
    notifier: HandoffNotifier
    engine: RoutineEngine
    budget_bounds: tuple[Decimal, Decimal]
    store_domain: str
    guide: Guide = field(default_factory=lambda: Guide({}))
    pubmed: Any = None  # `PubMedConsultant`; sin él no se registra `evidencia_ingrediente` (PUBMED_ENABLED=false)


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_pasos(text: str) -> list[str] | None:
    """Pasos pedidos: `None` = elegir automáticamente; `[]` imposible; lista en el orden de la rutina."""
    t = _fold(text or "").strip()
    if t in {"", "auto", "automatico", "automático"}:
        return None
    if any(w in t for w in ("todos", "todas", "todo", "toda", "completa", "all")):
        return list(PASOS)
    keys = {"limpi": "Limpieza", "trat": "Tratamiento", "hidrat": "Hidratación", "solar": "Protección solar", "protec": "Protección solar"}
    found = {paso for key, paso in keys.items() if key in t}
    return [p for p in PASOS if p in found] or None


def name_key(p: Product) -> tuple[str, str]:
    """Identidad de "mismo producto" para no ofrecer como alternativa otra presentación del mismo producto.

    Marca y nombre sin números, tamaños ni signos: `Clarifying Lotion 2` y `Clarifying Lotion 3` (o el mismo
    producto en 50 ml y 100 ml) son el mismo producto para efectos de "dame otro".
    """
    text = _fold(p.nombre)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:ml|gr|g|oz|l)\b", " ", text)  # tamaños
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    return p.marca, " ".join(text.split())


def _product_view(p: Product) -> dict[str, Any]:
    """Vista reducida que ve el modelo (sin imagen, modo de uso ni ingredientes)."""
    return {
        "sku": p.sku,
        "nombre": p.nombre,
        "marca": p.marca,
        "precio_mxn": money(p.precio),
        "tipo_piel": p.tipo_piel,
        "beneficios": [b[:140] for b in p.beneficios_list[:4]],
    }


def next_action_hint(session: Session) -> str:
    """Instrucción explícita sobre qué hacer después de registrar un dato del perfil."""
    profile = session.profile
    if session.recommendations_suspended:
        return "No recomiendes productos. Pide amablemente al cliente que acuda al mostrador de asesoría en piso."
    if session.routine is not None:
        return "La rutina ya está armada. Responde dudas o, si pide cambios, usa ajustar_rutina."
    if profile.listo_para_proponer():
        return "Ya puedes proponer: llama ahora a armar_rutina (no necesitas buscar antes) y presenta la rutina en voz."
    faltan = profile.faltan()
    if faltan:
        return f"Siguiente: pregunta de forma natural {_HOW_TO_ASK[faltan[0]]}. Una sola pregunta."
    return (
        "Ya tienes los cuatro datos, pero aún no es momento de proponer. Haz UNA pregunta para confirmar o "
        "profundizar (por ejemplo qué productos usa hoy o qué le molesta más de su piel). No busques productos todavía."
    )


def step_phrase(step: dict[str, Any]) -> str:
    """Frase para decir en voz de un paso: solo datos del catálogo (nombre, marca y razón ya compuesta)."""
    razon = step["razon_catalogo"].strip()
    base = f"{PASOS[step['paso'] - 1]}: {step['nombre']}, de {step['marca']}."
    return f"{base} {razon}" if razon else base


def _spell(code: str) -> str:
    """`ABC-234` → `A B C guion 2 3 4` (para dictarlo despacio)."""
    letters, digits = code.split("-")
    return f"{' '.join(letters)} guion {' '.join(digits)}"


def shift_level(level: str, cambio: str) -> str:
    """Nivel de precio vecino: `mas_barato` baja uno y `mas_premium` sube uno."""
    idx = LEVELS.index(level) if level in LEVELS else 1
    if cambio == "mas_barato":
        idx = max(0, idx - 1)
    elif cambio == "mas_premium":
        idx = min(len(LEVELS) - 1, idx + 1)
    return LEVELS[idx]


def build_tools(session: Session, deps: Deps) -> list[Any]:
    """Crea las herramientas ligadas a una sesión concreta."""

    def tier_of(p: Product) -> str:
        return tier_for_price(p.precio, deps.budget_bounds)

    suspended = {"error": "recomendacion_suspendida"}

    def not_ready() -> dict[str, Any] | None:
        """El código decide cuándo se puede proponer: no depende de que el modelo lo respete."""
        if session.profile.listo_para_proponer():
            return None
        return {
            "error": "aun_no_es_momento_de_proponer",
            "intercambios": session.profile.exchange_count,
            "faltan": session.profile.faltan(),
            "instruccion": next_action_hint(session),
        }

    def current_level() -> str:
        """Nivel de precio ($, $$, $$$) con el que se está trabajando."""
        if session.level in LEVELS:
            return session.level  # type: ignore[return-value]
        declared = session.profile.valores.get("presupuesto")
        if declared in LEVELS:
            return declared
        if session.routine:
            prices = sorted(Decimal(p["precio"]) for p in session.routine)
            return tier_for_price(prices[len(prices) // 2], deps.budget_bounds)
        return "$$"

    def curated(level: str) -> frozenset[str]:
        """SKU curados por la guía para el perfil en ese nivel (vacío si no hay guía o perfil)."""
        if not deps.guide.available:
            return frozenset()
        return frozenset(deps.guide.preferred_skus(session.profile.valores, level))

    async def search_step(
        paso: str,
        consulta: str | None,
        *,
        exclude: set[str] | frozenset[str] = frozenset(),
        exclude_names: frozenset[tuple[str, str]] = frozenset(),  # claves de `name_key`
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        strict: bool = False,
        limit: int = CANDIDATES_PER_STEP,
        preferred: frozenset[str] = frozenset(),
        anchor: Product | None = None,
        remember: bool = True,
    ) -> list[Product]:
        """Candidatos de un paso, ordenados por qué tan buena opción son para el cliente.

        Los filtros de precio y exclusión se aplican antes de ordenar. Con `strict=False`, si el filtro
        de precio deja el paso vacío se relaja (el llamador avisa que algo queda fuera del presupuesto).
        """
        found = await asyncio.wait_for(deps.catalog.search(paso, consulta), timeout=SEARCH_TIMEOUT_S)
        base = [
            p
            for p in found
            if p.sku not in exclude
            and name_key(p) not in exclude_names
            and not any(b in _fold(p.marca) for b in session.avoided_brands)
        ]
        pool = [
            p
            for p in base
            if (min_price is None or p.precio >= min_price) and (max_price is None or p.precio <= max_price)
        ]
        if not pool and not strict:
            pool = base
        tipo = session.profile.valores.get("tipo_piel")
        ranked = rank_candidates(
            pool,
            TIPO_PIEL_CATALOGO.get(tipo) if tipo else None,
            session.profile.valores.get("presupuesto"),
            tier_of,
            consulta,
            limit=limit,
            preferred=preferred,
            anchor=anchor,
        )
        if remember:
            session.candidates[paso] = ranked
        return ranked

    def snapshot(fresh: dict[str, Product]) -> list[dict[str, Any]]:
        """Rutina tal como se guarda: datos de producto releídos del catálogo, razón ya compuesta."""
        return [
            {
                "paso": p["paso"],
                "sku": p["sku"],
                "nombre": fresh[p["sku"]].nombre,
                "marca": fresh[p["sku"]].marca,
                "precio": money(fresh[p["sku"]].precio),
                "imagen_url": fresh[p["sku"]].imagen_url,
                "razon_catalogo": p["razon_catalogo"],
                "modo_uso": fresh[p["sku"]].modo_uso,
            }
            for p in (session.routine or [])
        ]

    async def save_recommendation() -> dict[str, Any]:
        """Guarda la rutina de la sesión (idempotente) y publica el QR en la pantalla."""
        if session.saved is not None:
            return {"ok": True, "codigo_corto": session.saved["codigo_corto"], "ya_guardada": True}
        if not session.routine:
            return {"error": "no_hay_rutina_armada"}
        fresh = await deps.catalog.get_many([p["sku"] for p in session.routine])
        if len(fresh) != len(session.routine):
            return {"error": "no_se_guardo", "motivo": "sku_inexistente"}
        rutina = snapshot(fresh)
        if problems := validate_routine_for_save(rutina):
            log.error("rutina inválida; no se guarda: %s", problems)
            return {"error": "no_se_guardo", "motivo": "rutina_invalida", "campos": problems}
        code = None
        for _ in range(MAX_CODE_ATTEMPTS):
            candidate = generate_codigo_corto()
            if not await deps.store.code_exists(candidate):
                code = candidate
                break
        if code is None:
            return {"error": "no_se_guardo"}
        rec_id = session.new_rec_id()
        try:
            await deps.store.put(
                {
                    "rec_id": rec_id,
                    "codigo_corto": code,
                    "session_id": session.session_id,
                    "fecha_creacion": now_iso(),
                    "estado": "pendiente",
                    "rutina": rutina,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.error("guardar la recomendación falló: %s", exc)
            return {"error": "no_se_guardo"}
        qr_url = f"{deps.store_domain.rstrip('/')}/caja?rec={rec_id}"
        session.saved = {"rec_id": rec_id, "codigo_corto": code, "qr_url": qr_url}
        await session.emit({"type": "saved", **session.saved})
        result: dict[str, Any] = {"ok": True, "codigo_corto": code}
        if session.readings:
            # Segunda escritura: si falla, la recomendación ya guardada se conserva (Req. 17.8).
            try:
                await deps.store.set_readings(rec_id, session.readings)
            except Exception as exc:  # noqa: BLE001
                log.error("no se almacenaron las lecturas: %s", exc)
                result["warning"] = "lecturas_no_almacenadas"
        return result

    def routine_summary(changed: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "paso": PASOS[p["paso"] - 1],
                "nombre": p["nombre"],
                "marca": p["marca"],
                "precio_mxn": p["precio"],
                "razon": p["razon_catalogo"],
                "frase": step_phrase(p),
                **({"cambio": PASOS[p["paso"] - 1] in changed} if changed is not None else {}),
            }
            for p in (session.routine or [])
        ]

    def routine_total() -> str:
        return money(sum((Decimal(p["precio"]) for p in (session.routine or [])), Decimal("0")))

    # ================================================================== herramientas

    @tool
    async def registrar_perfil(
        campo: Literal["tipo_piel", "inquietud", "textura", "presupuesto"], valor: str, monto_mxn: int = 0
    ) -> dict:
        """Registra un dato del perfil del cliente después de que lo diga.

        Args:
            campo: Dato que se registra. tipo_piel, inquietud, textura (cómo siente la piel después de
                lavarla) o presupuesto.
            valor: Para tipo_piel use exactamente uno de: grasa/acneica, normal/equilibrada,
                mixta/deshidratada, seca/tensa. Para inquietud use exactamente uno de: brotes,
                manchas, hidratacion, primeras_lineas, arrugas_profundas/firmeza. Para
                presupuesto use exactamente uno de: $ (hasta unos 1,300 pesos por producto),
                $$ (entre 1,300 y 3,500), $$$ (más de 3,500). Para textura use una frase corta de
                cómo siente la piel al lavarla (por ejemplo brillante, cómoda con resequedad,
                marca líneas, muy seca). Si la respuesta del cliente es ambigua, abarca varias
                categorías o dice no sé, use el valor ambiguo.
            monto_mxn: Solo para presupuesto. Si el cliente dijo una cifra por producto (por ejemplo
                "unos mil pesos"), póngala aquí en pesos; si no dijo cifra, deje 0.
        """
        result = session.profile.apply(campo, valor)
        if campo == "presupuesto" and result.accepted and monto_mxn > 0:
            session.profile.tope_mxn = int(monto_mxn)
        profile = session.profile
        segment = deps.guide.segment(profile.valores) if deps.guide.available else None
        return {
            "registrado": result.accepted,
            "reformular": result.reformular,
            "dato_no_proporcionado": result.no_proporcionado,
            "faltan": profile.faltan(),
            "intercambios": profile.exchange_count,
            "listo_para_proponer": profile.listo_para_proponer(),
            "basado_en_info_parcial": profile.basado_en_info_parcial(),
            **({"segmento_del_cliente": segment["nombre"]} if segment else {}),
            "siguiente_accion": (
                "Reformula ESTE dato una sola vez con otras palabras."
                if result.reformular
                else next_action_hint(session)
            ),
        }

    @tool
    async def buscar_productos(
        paso: Literal["Limpieza", "Tratamiento", "Hidratación", "Protección solar"], consulta: str = ""
    ) -> dict:
        """Busca en el catálogo los productos de un paso. Úsala solo para responder preguntas
        puntuales del cliente sobre productos; para armar la rutina basta con armar_rutina.

        Args:
            paso: Paso de la rutina: Limpieza, Tratamiento, Hidratación o Protección solar.
            consulta: Texto opcional para afinar la búsqueda (por ejemplo una necesidad del cliente).
        """
        if session.recommendations_suspended:
            return suspended
        try:
            ranked = await search_step(
                paso, consulta or profile_query(session.profile.valores), preferred=curated(current_level())
            )
        except Exception as exc:  # noqa: BLE001
            log.error("buscar_productos falló: %s", exc)
            return {"error": "catalogo_no_disponible"}
        if not ranked:
            return {"paso": paso, "sin_candidatos": True}
        return {"paso": paso, "productos": [_product_view(p) for p in ranked]}

    @tool
    async def detalle_producto(sku: str) -> dict:
        """Devuelve el modo de uso, los ingredientes y el detalle de un producto de la rutina o de una búsqueda.

        Args:
            sku: SKU de un producto devuelto antes por armar_rutina o buscar_productos.
        """
        allowed = {p.sku for plist in session.candidates.values() for p in plist}
        allowed |= {p["sku"] for p in (session.routine or [])}
        if sku not in allowed:
            return {"error": "producto_no_encontrado_en_esta_sesion"}
        product = (await deps.catalog.get_many([sku])).get(sku)
        if product is None:
            return {"error": "producto_no_encontrado_en_esta_sesion"}
        return {
            "nombre": product.nombre,
            "modo_uso": product.modo_uso or None,
            "ingredientes": product.ingredientes or None,
            "detalle": product.detalle or None,
        }

    @tool
    async def armar_rutina() -> dict:
        """Busca los productos de los 4 pasos, arma la rutina, la guarda y muestra el QR en la pantalla.

        Llamar cuando registrar_perfil indique listo_para_proponer=true. No recibe parámetros y
        no hace falta llamar antes a buscar_productos. Solo para la PRIMERA rutina; para cambios
        posteriores use ajustar_rutina.
        """
        if session.recommendations_suspended:
            return suspended
        if session.routine is not None and session.saved is not None:
            return _routine_result(session, session.saved["codigo_corto"], repeated=True)
        if gate := not_ready():
            return gate

        session.tools_running += 1
        try:
            tope = Decimal(session.profile.tope_mxn) if session.profile.tope_mxn > 0 else None
            level = current_level()
            session.level = level
            preferred = curated(level)
            # 1) Candidatos de los 4 pasos; la guía pasa primero y, con tope por producto, los que caben.
            try:
                for paso in PASOS:
                    await search_step(paso, profile_query(session.profile.valores), max_price=tope, preferred=preferred)
            except Exception as exc:  # noqa: BLE001
                log.error("armar_rutina: búsqueda falló: %s", exc)
                return {"error": "catalogo_no_disponible"}

            # 2) Rutina.
            result = await deps.engine.armar(
                session.profile.to_dict(),
                session.candidates,
                session.profile.indicadores_sensibles,
                preferred=preferred,
            )
            if result.get("requiere_asesor"):
                await start_handoff(session, deps.notifier, "requiere_asesor")
                return {"requiere_asesor": True}
            if not result.get("ok"):
                return {k: v for k, v in result.items() if k != "ok"} | {"ok": False}
            session.routine = result["pasos"]
            session.routine_at = time.monotonic()
            session.routine_change = "inicial"
            await session.emit({"type": "routine", "pasos": session.routine})

            # 3) Guardado y QR: lo hace el servidor, no depende de que el modelo lo pida.
            saved = await save_recommendation()
            if not saved.get("ok"):
                log.error("armar_rutina: no se pudo guardar: %s", saved)
                return _routine_result(session, None, error=saved.get("error"))
            over = (
                [PASOS[p["paso"] - 1] for p in session.routine if tope and Decimal(p["precio"]) > tope]
                if tope
                else []
            )
            return _routine_result(session, saved["codigo_corto"], over_budget=over, warning=saved.get("warning"))
        finally:
            session.tools_running -= 1

    @tool
    async def ajustar_rutina(
        cambio: Literal["mas_barato", "mas_premium", "otro_producto"],
        pasos: str = "auto",
        precio_maximo_mxn: int = 0,
        total_maximo_mxn: int = 0,
        preferencia: str = "",
        evitar_marca: str = "",
    ) -> dict:
        """Modifica la rutina ya mostrada cuando al cliente no le gusta algo o se sale de su presupuesto.
        La pantalla se actualiza sola y el código QR sigue siendo el mismo. Cambia lo que se pueda y deja
        igual lo que no tenga una buena alternativa; los sustitutos se eligen por parecido al producto
        actual, no por ser los más baratos.

        Args:
            cambio: mas_barato (sustitutos más económicos pero igual de buena opción), mas_premium (de gama
                más alta) u otro_producto (mismo nivel de precio pero distinto: al cliente no le gustó el actual).
            pasos: Pasos que se cambian, separados por coma (Limpieza, Tratamiento, Hidratación, Protección
                solar). Use todos si quiere toda la rutina, o auto si el cliente no dijo cuáles (con mas_barato
                el sistema abarata primero los más caros).
            precio_maximo_mxn: Si el cliente dijo un tope de precio por producto, en pesos. Si no, 0.
            total_maximo_mxn: Si el cliente dijo un tope para el total de la rutina, en pesos. Si no, 0.
            preferencia: Lo que pidió el cliente, en pocas palabras (por ejemplo sin perfume, más ligero).
            evitar_marca: Marca(s) que el cliente no quiere (por ejemplo Clinique), separadas por coma. Se
                cambian todos los productos de esa marca, aunque no se mencionen en pasos, y no se vuelve a
                ofrecer en esta conversación. Si no aplica, deje vacío.
        """
        if session.recommendations_suspended:
            return suspended
        if not session.routine or session.saved is None:
            return {"error": "no_hay_rutina_que_ajustar", "instruccion": "Primero arma la rutina con armar_rutina."}
        if session.adjustments >= MAX_ADJUSTMENTS:
            return {
                "error": "limite_de_ajustes",
                "instruccion": "Ya se hicieron varios ajustes. Sugiere al cliente consultar con un asesor de la tienda.",
            }

        session.tools_running += 1
        try:
            current = {PASOS[p["paso"] - 1]: p for p in session.routine}
            current_products = await deps.catalog.get_many([p["sku"] for p in session.routine])
            if len(current_products) != len(session.routine):
                return {"error": "catalogo_no_disponible"}

            # Los topes que dijo el cliente se recuerdan en los ajustes siguientes. Si pide algo de gama
            # más alta sin dar un tope nuevo, se entiende que ya no quiere el anterior.
            if precio_maximo_mxn > 0:
                session.product_cap = Decimal(precio_maximo_mxn)
            if total_maximo_mxn > 0:
                session.total_cap = Decimal(total_maximo_mxn)
            if cambio == "mas_premium" and precio_maximo_mxn <= 0 and total_maximo_mxn <= 0:
                session.product_cap = None
                session.total_cap = None
            cap, total_cap = session.product_cap, session.total_cap

            requested = parse_pasos(pasos)
            brands = {_fold(b).strip() for b in evitar_marca.split(",") if b.strip()}
            if brands:
                known = {_fold(p.marca) for p in current_products.values()}
                # Coincidencia por nombre completo o contenido ("estee lauder" / "estee"); nada inventado.
                session.avoided_brands |= {b for b in brands if b}
                forced = [
                    s for s in PASOS
                    if any(b and (b in _fold(current_products[current[s]["sku"]].marca)) for b in brands)
                ]
                if forced:
                    requested = sorted(set(requested or []) | set(forced), key=PASOS.index)
                log.info("marcas a evitar: %s | pasos afectados: %s | marcas en la rutina: %s", sorted(brands), forced, sorted(known))
            target_level = shift_level(current_level(), cambio)
            preferred = curated(target_level)
            query = preferencia.strip() or profile_query(session.profile.valores)
            rejected = set(session.rejected_skus)
            limit = CANDIDATES_WITH_TOTAL_CAP if total_cap is not None else CANDIDATES_PER_STEP
            # Un producto con el mismo nombre que uno rechazado (otro SKU, tamaño o tono) no es "otra opción".
            rejected_products = await deps.catalog.get_many(list(rejected))
            rejected_names = frozenset(
                {name_key(p) for p in rejected_products.values()} | {name_key(p) for p in current_products.values()}
            )

            # Si el cliente no dijo qué paso y lo que pide ya se cumple, no se cambia nada.
            if requested is None and cambio == "mas_barato":
                total_now = sum((p.precio for p in current_products.values()), Decimal("0"))
                within_total = total_cap is None or total_now <= total_cap
                within_each = cap is None or all(p.precio <= cap for p in current_products.values())
                if (total_cap is not None or cap is not None) and within_total and within_each:
                    return {
                        "ok": True,
                        "sin_cambios": True,
                        "total_mxn": routine_total(),
                        "tope_total_vigente_mxn": money(total_cap) if total_cap is not None else None,
                        "instruccion": (
                            "La rutina actual ya cabe dentro de lo que pidió el cliente. Díselo con el total actual "
                            "y pregunta si quiere abaratar aún más algún producto."
                        ),
                    }

            # 1) Alternativas por paso: parecidas al producto actual (misma marca, mismo beneficio, precio cercano).
            pools: dict[str, list[Product]] = {}
            for step in PASOS:
                if requested is not None and step not in requested:
                    continue
                now = current_products[current[step]["sku"]]
                low = now.precio + Decimal("0.01") if cambio == "mas_premium" else None
                high = now.precio - Decimal("0.01") if cambio == "mas_barato" else None
                if cambio == "mas_barato" and cap is None and total_cap is None:
                    # Sin un tope concreto, "más barato" no significa lo más barato: se conserva la gama.
                    low = now.precio * MIN_PRICE_RATIO
                elif cambio == "mas_barato":
                    # Con tope, el piso sube si hace falta ahorrar poco: se busca lo más cercano que quepa.
                    low = now.precio * Decimal("0.25")
                if cap is not None:
                    high = cap if high is None else min(high, cap)
                # El tope por producto de la plática (p. ej. "hasta 500") rige también los cambios, salvo
                # que pida gama más alta o dé otro tope. Si por eso no queda opción, se intenta sin él.
                profile_cap = Decimal(session.profile.tope_mxn) if session.profile.tope_mxn > 0 else None
                ceilings = [high]
                if cap is None and profile_cap is not None and cambio != "mas_premium":
                    # Primero dentro del tope; si no hay, lo más cercano por encima (hasta +50%); al final sin tope.
                    ceilings = [profile_cap, profile_cap * Decimal("1.5"), None]
                    ceilings = [c if high is None or c is None else min(high, c) for c in ceilings]
                    if high is not None:
                        ceilings[-1] = high
                try:
                    for ceiling in ceilings:
                        pools[step] = await search_step(
                            step,
                            query,
                            exclude=rejected | {now.sku},
                            exclude_names=rejected_names,
                            min_price=low,
                            max_price=ceiling,
                            strict=True,
                            limit=limit,
                            preferred=preferred,
                            anchor=now,
                            remember=False,
                        )
                        if pools[step]:
                            break
                except Exception as exc:  # noqa: BLE001
                    log.error("ajustar_rutina: búsqueda falló: %s", exc)
                    return {"error": "catalogo_no_disponible"}

            wanted = list(requested) if requested is not None else list(PASOS)
            changeable = [s for s in wanted if pools.get(s)]
            no_alternative = [s for s in wanted if not pools.get(s)]
            if not changeable:
                return {
                    "ok": False,
                    "error": "sin_alternativas",
                    "pasos_sin_alternativa": no_alternative,
                    "instruccion": (
                        "No hay otra opción con esas condiciones en "
                        + (", ".join(no_alternative) if no_alternative else "ningún paso")
                        + ". Díselo al cliente, ofrécele dejar la rutina como está, cambiar la condición "
                        "o consultar a un asesor de la tienda."
                    ),
                }

            # 2) Qué pasos se tocan. Sin indicación y "más barato": primero los más caros, y solo se suman
            # más pasos si el tope total lo exige, para cambiar lo menos posible.
            if requested is None and cambio == "mas_barato":
                changeable.sort(key=lambda s: Decimal(current[s]["precio"]), reverse=True)
                sizes = list(range(min(AUTO_CHEAPER_STEPS, len(changeable)), len(changeable) + 1))
                if total_cap is None:
                    sizes = sizes[:1]
            else:
                sizes = [len(changeable)]

            result: dict[str, Any] = {}
            used: list[str] = []
            for k in sizes:
                used = changeable[:k]
                candidates: dict[str, list[Product]] = {}
                locked: set[str] = set()
                for step in PASOS:
                    if step in used:
                        candidates[step] = pools[step]
                    else:
                        candidates[step] = [current_products[current[step]["sku"]]]
                        locked.add(step)
                result = await deps.engine.armar(
                    session.profile.to_dict(),
                    candidates,
                    session.profile.indicadores_sensibles,
                    preferencias=preferencia,
                    max_total=total_cap,
                    locked=locked,
                    preferred=preferred,
                )
                if result.get("ok") or result.get("error") != "no_cabe_en_presupuesto":
                    break

            if result.get("requiere_asesor"):
                await start_handoff(session, deps.notifier, "requiere_asesor")
                return {"requiere_asesor": True}
            if not result.get("ok"):
                failure = {k: v for k, v in result.items() if k != "ok"} | {"ok": False}
                if result.get("error") == "no_cabe_en_presupuesto":
                    failure["instruccion"] = (
                        "Con ese tope no se puede armar la rutina. Dile al cliente el mínimo posible "
                        "(minimo_posible_mxn) y pregúntale si quiere subir el tope o quedarse con la rutina actual."
                    )
                return failure

            new_steps = result["pasos"]
            fresh = await deps.catalog.get_many([p["sku"] for p in new_steps])
            old_by_step = {PASOS[p["paso"] - 1]: p for p in session.routine}
            changed = {
                PASOS[p["paso"] - 1] for p in new_steps if p["sku"] != old_by_step[PASOS[p["paso"] - 1]]["sku"]
            }
            if not changed:
                # Nada que cambiar (por ejemplo, el total ya cabía en el tope): no se toca ni se repinta nada.
                return {
                    "ok": True,
                    "sin_cambios": True,
                    "total_mxn": routine_total(),
                    "pasos_sin_alternativa": no_alternative,
                    "instruccion": (
                        "La rutina actual ya cumple lo que pidió el cliente (o no hay alternativa mejor). Díselo "
                        "con claridad, con el total aproximado, y pregunta si quiere cambiar otra cosa."
                    ),
                }
            session.rejected_skus |= {old_by_step[s]["sku"] for s in changed}
            previous = {s: old_by_step[s]["nombre"] for s in changed}
            previous_total = routine_total()
            session.routine = new_steps
            session.routine_at = time.monotonic()
            session.routine_change = "ajuste"
            session.adjustments += 1
            if cambio != "otro_producto":
                session.level = target_level

            # La recomendación se actualiza en el mismo registro: el QR y el código no cambian.
            try:
                await deps.store.update_routine(session.saved["rec_id"], snapshot(fresh), now_iso())
            except Exception as exc:  # noqa: BLE001
                log.error("ajustar_rutina: no se pudo actualizar la recomendación: %s", exc)
                return {"error": "no_se_actualizo", "instruccion": "Pide al cliente consultar a un asesor de la tienda."}
            await session.emit({"type": "routine", "pasos": session.routine})

            summary = routine_summary(changed)
            for item in summary:
                if item.get("cambio"):
                    item["antes"] = previous[item["paso"]]
            return {
                "ok": True,
                "pasos_cambiados": sorted(changed, key=PASOS.index),
                "pasos_sin_cambio": [s for s in PASOS if s not in changed],
                "pasos_sin_alternativa": no_alternative,
                "total_antes_mxn": previous_total,
                "total_mxn": routine_total(),
                "tope_total_vigente_mxn": money(total_cap) if total_cap is not None else None,
                "codigo_corto": session.saved["codigo_corto"],
                "pasos_resumen": summary,
                "instruccion": (
                    "La pantalla ya se actualizó y el código QR es el mismo. Di SOLO lo que cambió, leyendo el campo "
                    "frase de cada paso con cambio=true TAL CUAL (sin agregar datos), y el total nuevo frente al anterior. "
                    "Explica en una frase que el sustituto conserva el beneficio. Si hay pasos_sin_alternativa, dilo con "
                    "honestidad. Si hay tope_total_vigente_mxn, confirma que se respeta. No repitas los pasos sin cambio."
                ),
            }
        finally:
            session.tools_running -= 1

    @tool
    async def guardar_recomendacion() -> dict:
        """Confirma el código de la rutina ya armada. armar_rutina ya la guarda; esta herramienta
        solo devuelve el código corto por si hace falta repetirlo al cliente. No recibe parámetros.
        """
        if session.recommendations_suspended:
            return suspended
        if not session.routine:
            return {"error": "no_hay_rutina_armada"}
        saved = await save_recommendation()
        if not saved.get("ok"):
            return saved
        return {"ok": True, "codigo_corto": saved["codigo_corto"], "letras_y_numeros": _spell(saved["codigo_corto"])}

    @tool
    async def derivar_asesor(
        motivo: Literal["condicion_sensible", "diagnostico", "compatibilidad", "alergia_producto"],
    ) -> dict:
        """Deriva al cliente con un asesor humano de la tienda.

        Args:
            motivo: condicion_sensible (alergia severa, acné quístico, embarazo, heridas),
                diagnostico (pide un diagnóstico o tratamiento), compatibilidad (pregunta si se
                pueden mezclar activos o marcas) o alergia_producto (dice que un producto o marca le
                da alergia o le cae mal; en ese caso se puede seguir ofreciendo otra opción).
        """
        record = await start_handoff(session, deps.notifier, motivo)
        if record.get("estado") == "sin_notificar":
            return {
                "derivado": False,
                "indicacion": "No se pudo avisar al asesor. Pide al cliente que acuda al mostrador de asesoría en piso.",
            }
        return {"derivado": True, "indicacion": "Un asesor de la tienda atenderá al cliente."}

    @tool
    async def evidencia_ingrediente(ingrediente: str) -> dict:
        """Muestra en la pantalla del cliente lecturas científicas de un ingrediente de SU rutina.

        No devuelve los títulos: solo indica si hay lecturas en pantalla. Nunca las leas en voz alta ni las
        resumas; si no hay lecturas, no las menciones.

        Args:
            ingrediente: Nombre de un ingrediente que aparezca en la lista de ingredientes de la rutina.
        """
        shown = {"lecturas_en_pantalla": False}
        if session.recommendations_suspended or not session.routine or deps.pubmed is None:
            return shown
        try:
            products = await deps.catalog.get_many([p["sku"] for p in session.routine])
            texts = [p.ingredientes for p in products.values()]
            result = await asyncio.wait_for(
                asyncio.to_thread(deps.pubmed.lookup, ingrediente, texts), timeout=TOTAL_TIMEOUT_S + 0.5
            )
        except Exception as exc:  # noqa: BLE001 - cualquier falla significa "sin lecturas"
            log.warning("evidencia_ingrediente falló (%s)", type(exc).__name__)
            return shown
        if not result.articulos:
            return shown
        reading = {"ingrediente": ingrediente.strip(), "articulos": result.articulos}
        session.readings.append(reading)
        await session.emit({"type": "readings", **reading})
        if session.saved is not None:
            try:
                await deps.store.set_readings(session.saved["rec_id"], session.readings)
            except Exception as exc:  # noqa: BLE001
                log.error("no se almacenaron las lecturas: %s", exc)
        return {"lecturas_en_pantalla": True}  # el modelo nunca ve títulos ni PMID

    tools = [
        registrar_perfil,
        buscar_productos,
        detalle_producto,
        armar_rutina,
        ajustar_rutina,
        guardar_recomendacion,
        derivar_asesor,
    ]
    if deps.pubmed is not None:
        tools.append(evidencia_ingrediente)
    return tools


def _routine_result(
    session: Session,
    code: str | None,
    repeated: bool = False,
    error: str | None = None,
    over_budget: list[str] | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    """Resultado de `armar_rutina` para el modelo: lo que debe decir, sin texto libre inventado."""
    assert session.routine is not None
    result: dict[str, Any] = {
        "ok": True,
        "ya_mostrada_en_pantalla": repeated,
        "basado_en_info_parcial": session.profile.basado_en_info_parcial(),
        "total_mxn": money(sum((Decimal(p["precio"]) for p in session.routine), Decimal("0"))),
        "pasos_resumen": [
            {
                "paso": PASOS[p["paso"] - 1],
                "nombre": p["nombre"],
                "marca": p["marca"],
                "precio_mxn": p["precio"],
                "razon": p["razon_catalogo"],
                "frase": step_phrase(p),
            }
            for p in session.routine
        ],
    }
    if over_budget:
        result["fuera_del_tope_del_cliente"] = over_budget
    if warning:
        result["warning"] = warning
    if code:
        result["codigo_corto"] = code
        result["codigo_para_dictar"] = _spell(code)
        result["instruccion"] = (
            "La rutina y el código QR YA están en la pantalla. Presenta los 4 pasos en voz leyendo el campo frase "
            "de cada uno TAL CUAL, sin cambiar palabras ni agregar datos (no añadas FPS, cifras, ingredientes ni "
            "beneficios que no estén en la frase), y di el total aproximado. "
            + (
                f"En {', '.join(over_budget)} no había opciones dentro del tope del cliente: dilo con honestidad. "
                if over_budget
                else ""
            )
            + "Después dicta el código despacio y pregunta si quiere ajustar algo, por ejemplo el presupuesto o algún producto. "
            "No repitas la rutina."
        )
    else:
        result["aviso"] = (
            f"La rutina se muestra en pantalla pero no se pudo generar el código ({error}). Preséntala y pide al cliente "
            "que consulte a un asesor de la tienda para el cobro."
        )
    return result


__all__ = ["Deps", "build_tools", "next_action_hint", "total_price", "parse_pasos", "shift_level", "name_key"]

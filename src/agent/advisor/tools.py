"""Herramientas que el modelo de voz puede llamar.

Reglas de diseño:
- Ninguna herramienta recibe datos de producto del modelo (solo categorías o un SKU que ya esté
  en los candidatos de la sesión). Todo sale del estado del servidor y del catálogo.
- El servidor hace el trabajo mecánico: `armar_rutina` busca los 4 pasos, arma la rutina, la
  guarda y publica el QR en una sola llamada. El modelo solo tiene que hablar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from strands import tool

from .models import PASOS, Product, money
from .ports import CatalogRepository, HandoffNotifier, RecommendationStore
from .profile import TIPO_PIEL_CATALOGO, tier_for_price
from .routine import RoutineEngine, rank_candidates
from .session import Session, generate_codigo_corto, now_iso, start_handoff

log = logging.getLogger("advisor.tools")

SEARCH_TIMEOUT_S = 5.0
MAX_CODE_ATTEMPTS = 5

# Cómo preguntar por cada dato que falta, en lenguaje cotidiano.
_HOW_TO_ASK = {
    "tipo_piel": "cómo siente su piel durante el día (si se le pone grasosa, tirante, o en algunas zonas sí y en otras no)",
    "inquietud": "qué es lo que más quiere mejorar de su piel",
    "textura": "qué sensación o textura prefiere en sus productos (por ejemplo ligera, cremosa o en gel)",
    "presupuesto": "cuánto suele gastar en un producto de cuidado de la piel",
}


@dataclass
class Deps:
    catalog: CatalogRepository
    store: RecommendationStore
    notifier: HandoffNotifier
    engine: RoutineEngine
    budget_bounds: tuple[Decimal, Decimal]
    store_domain: str


def _product_view(p: Product) -> dict[str, Any]:
    """Vista reducida que ve el modelo (sin imagen, modo de uso ni ingredientes)."""
    return {
        "sku": p.sku,
        "nombre": p.nombre,
        "marca": p.marca,
        "precio_mxn": money(p.precio),
        "tipo_piel": p.tipo_piel,
        "beneficios": p.beneficios_list,
    }


def next_action_hint(session: Session) -> str:
    """Instrucción explícita sobre qué hacer después de registrar un dato del perfil."""
    profile = session.profile
    if session.recommendations_suspended:
        return "No recomiendes productos. Pide amablemente al cliente que acuda al mostrador de asesoría en piso."
    if session.routine is not None:
        return "La rutina ya está armada. Responde dudas sobre los productos recomendados."
    if profile.listo_para_proponer():
        return "Ya puedes proponer: llama ahora a armar_rutina (no necesitas buscar antes) y presenta la rutina en voz."
    faltan = profile.faltan()
    if faltan:
        return f"Siguiente: pregunta de forma natural {_HOW_TO_ASK[faltan[0]]}. Una sola pregunta."
    return (
        "Ya tienes los cuatro datos, pero aún no es momento de proponer. Haz UNA pregunta para confirmar o "
        "profundizar (por ejemplo qué productos usa hoy o qué le molesta más de su piel). No busques productos todavía."
    )


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

    async def search_step(paso: str, consulta: str | None) -> list[Product]:
        found = await asyncio.wait_for(deps.catalog.search(paso, consulta), timeout=SEARCH_TIMEOUT_S)
        tipo = session.profile.valores.get("tipo_piel")
        ranked = rank_candidates(
            found,
            TIPO_PIEL_CATALOGO.get(tipo) if tipo else None,
            session.profile.valores.get("presupuesto"),
            tier_of,
            consulta,
        )
        session.candidates[paso] = ranked
        return ranked

    async def save_recommendation() -> dict[str, Any]:
        """Guarda la rutina de la sesión (idempotente) y publica el QR en la pantalla."""
        if session.saved is not None:
            return {"ok": True, "codigo_corto": session.saved["codigo_corto"], "ya_guardada": True}
        if not session.routine:
            return {"error": "no_hay_rutina_armada"}
        # Se releen los datos de producto del catálogo: lo guardado no depende de la memoria.
        fresh = await deps.catalog.get_many([p["sku"] for p in session.routine])
        if len(fresh) != len(session.routine):
            return {"error": "no_se_guardo", "motivo": "sku_inexistente"}
        rutina = [
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
            for p in session.routine
        ]
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
        return {"ok": True, "codigo_corto": code}

    @tool
    async def registrar_perfil(
        campo: Literal["tipo_piel", "inquietud", "textura", "presupuesto"], valor: str
    ) -> dict:
        """Registra un dato del perfil del cliente después de que lo diga.

        Args:
            campo: Dato que se registra. tipo_piel, inquietud, textura o presupuesto.
            valor: Para tipo_piel use exactamente uno de: grasa/acneica, normal/equilibrada,
                mixta/deshidratada, seca/tensa. Para inquietud use exactamente uno de: brotes,
                manchas, hidratacion, primeras_lineas, arrugas_profundas/firmeza. Para
                presupuesto use exactamente uno de: $ (accesible), $$ (premium), $$$ (lujo).
                Para textura use una sola palabra o frase corta (por ejemplo ligera, cremosa, gel).
                Si la respuesta del cliente es ambigua, abarca varias categorías o dice no sé,
                use el valor ambiguo.
        """
        result = session.profile.apply(campo, valor)
        profile = session.profile
        return {
            "registrado": result.accepted,
            "reformular": result.reformular,
            "dato_no_proporcionado": result.no_proporcionado,
            "faltan": profile.faltan(),
            "intercambios": profile.exchange_count,
            "listo_para_proponer": profile.listo_para_proponer(),
            "basado_en_info_parcial": profile.basado_en_info_parcial(),
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
            ranked = await search_step(paso, consulta or None)
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
        known = {p.sku: p for plist in session.candidates.values() for p in plist}
        product = known.get(sku)
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
        no hace falta llamar antes a buscar_productos.
        """
        if session.recommendations_suspended:
            return suspended
        if session.routine is not None and session.saved is not None:
            return _routine_result(session, session.saved["codigo_corto"], repeated=True)
        if gate := not_ready():
            return gate

        session.tools_running += 1
        try:
            # 1) Candidatos de los 4 pasos (si faltan, el servidor los busca).
            try:
                for paso in PASOS:
                    if not session.candidates.get(paso):
                        await search_step(paso, None)
            except Exception as exc:  # noqa: BLE001
                log.error("armar_rutina: búsqueda falló: %s", exc)
                return {"error": "catalogo_no_disponible"}

            # 2) Rutina.
            result = await deps.engine.armar(
                session.profile.to_dict(), session.candidates, session.profile.indicadores_sensibles
            )
            if result.get("requiere_asesor"):
                await start_handoff(session, deps.notifier, "requiere_asesor")
                return {"requiere_asesor": True}
            if not result.get("ok"):
                return {k: v for k, v in result.items() if k != "ok"} | {"ok": False}
            session.routine = result["pasos"]
            session.routine_at = time.monotonic()
            await session.emit({"type": "routine", "pasos": session.routine})

            # 3) Guardado y QR: lo hace el servidor, no depende de que el modelo lo pida.
            saved = await save_recommendation()
            if not saved.get("ok"):
                log.error("armar_rutina: no se pudo guardar: %s", saved)
                return _routine_result(session, None, error=saved.get("error"))
            return _routine_result(session, saved["codigo_corto"])
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
        motivo: Literal["condicion_sensible", "diagnostico", "compatibilidad"],
    ) -> dict:
        """Deriva al cliente con un asesor humano de la tienda.

        Args:
            motivo: condicion_sensible (alergia severa, acné quístico, embarazo, heridas),
                diagnostico (pide un diagnóstico o tratamiento) o compatibilidad (pregunta si se
                pueden mezclar activos o marcas).
        """
        record = await start_handoff(session, deps.notifier, motivo)
        if record.get("estado") == "sin_notificar":
            return {
                "derivado": False,
                "indicacion": "No se pudo avisar al asesor. Pide al cliente que acuda al mostrador de asesoría en piso.",
            }
        return {"derivado": True, "indicacion": "Un asesor de la tienda atenderá al cliente."}

    return [registrar_perfil, buscar_productos, detalle_producto, armar_rutina, guardar_recomendacion, derivar_asesor]


def _spell(code: str) -> str:
    """`ABC-234` → `A B C guion 2 3 4` (para dictarlo despacio)."""
    letters, digits = code.split("-")
    return f"{' '.join(letters)} guion {' '.join(digits)}"


def _routine_result(
    session: Session, code: str | None, repeated: bool = False, error: str | None = None
) -> dict[str, Any]:
    """Resultado de `armar_rutina` para el modelo: lo que debe decir, sin texto libre inventado."""
    assert session.routine is not None
    result: dict[str, Any] = {
        "ok": True,
        "ya_mostrada_en_pantalla": repeated,
        "basado_en_info_parcial": session.profile.basado_en_info_parcial(),
        "pasos_resumen": [
            {
                "paso": PASOS[p["paso"] - 1],
                "nombre": p["nombre"],
                "marca": p["marca"],
                "precio_mxn": p["precio"],
                "razon": p["razon_catalogo"],
            }
            for p in session.routine
        ],
    }
    if code:
        result["codigo_corto"] = code
        result["codigo_para_dictar"] = _spell(code)
        result["instruccion"] = (
            "La rutina y el código QR YA están en la pantalla. Presenta los 4 pasos en voz, breve, usando el campo "
            "razon tal cual, y al final dicta el código despacio. No repitas la rutina después."
        )
    else:
        result["aviso"] = (
            f"La rutina se muestra en pantalla pero no se pudo generar el código ({error}). Preséntala y pide al cliente "
            "que consulte a un asesor de la tienda para el cobro."
        )
    return result


__all__ = ["Deps", "build_tools", "next_action_hint"]

"""Decisión del vigilante de la conversación (pura).

Si el modelo se queda sin avanzar (ya hay datos suficientes pero no arma la rutina, o la armó y
no la presenta), el servidor le manda una instrucción interna. Nunca más de `MAX_NUDGES` por sesión.
"""

from __future__ import annotations

from typing import Literal

from .session import Session

Nudge = Literal["armar", "presentar", "ajuste"]

MAX_NUDGES = 3
MIN_SECONDS_BETWEEN = 15.0
PRESENT_AFTER_S = 8.0

TEXT: dict[Nudge, dict[str, str]] = {
    "armar": {
        "es": (
            "[Instrucción interna] Ya tienes los datos del cliente y los intercambios necesarios. "
            "Llama ahora mismo a armar_rutina y luego presenta la rutina en voz. No hagas más preguntas de perfil."
        ),
        "en": (
            "[Internal instruction] You already have the customer's details and enough exchanges. "
            "Call armar_rutina right now and then present the routine by voice. Do not ask more profile questions."
        ),
    },
    "ajuste": {
        "es": (
            "[Instrucción interna] La rutina ya se actualizó en la pantalla del cliente con el cambio que pidió. "
            "Dile ahora, breve, qué producto cambió (marca y por qué) y el total aproximado, y pregunta si así está bien. "
            "No repitas los pasos que no cambiaron."
        ),
        "en": (
            "[Internal instruction] The routine has been updated on the customer's screen with the change they asked for. "
            "Now briefly tell them which product changed (brand and why) and the approximate total, and ask if that works. "
            "Do not repeat the steps that did not change."
        ),
    },
    "presentar": {
        "es": (
            "[Instrucción interna] La rutina y el código QR ya están en la pantalla del cliente. "
            "Preséntala ahora en voz, breve, paso por paso, y dicta el código corto despacio."
        ),
        "en": (
            "[Internal instruction] The routine and QR code are already on the customer's screen. "
            "Present it now by voice, briefly, step by step, and read the short code slowly."
        ),
    },
}


def decide_nudge(
    session: Session,
    *,
    now: float,
    nudges_sent: int,
    last_nudge_at: float | None,
    spoke_after_routine: bool,
) -> Nudge | None:
    """Qué instrucción mandar (si hace falta) cuando el modelo terminó de hablar."""
    if session.recommendations_suspended or session.tools_running > 0:
        return None
    if nudges_sent >= MAX_NUDGES:
        return None
    if last_nudge_at is not None and now - last_nudge_at < MIN_SECONDS_BETWEEN:
        return None
    if session.routine is None:
        return "armar" if session.profile.listo_para_proponer() else None
    if not spoke_after_routine and session.routine_at is not None and now - session.routine_at >= PRESENT_AFTER_S:
        return "ajuste" if session.routine_change == "ajuste" else "presentar"
    return None

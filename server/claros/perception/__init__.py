"""claros.perception — frames → redaction → OCR → ScreenState → ScreenEvents (app-agnostic).

    from claros.perception import register; register(bus)

Subscribes `ws.in.keyframe`, `ws.in.activity`, `session.ended`. Publishes `screen.state`,
`screen.events`, `ws.out` (events + throttled context_update). See pipeline.py.
Public helpers: `get_pipeline(session_id)`, `current_state(session_id)`, `entity_fields(session_id)`.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from claros.models import ScreenState

from .pipeline import SessionPipeline

log = logging.getLogger("claros.perception")

_PIPES: dict[str, SessionPipeline] = {}
_BUS: Any = None


def _publish(sid: str, topic: str, payload: Any) -> None:
    if _BUS is not None:
        _BUS.publish(sid, topic, payload)


def get_pipeline(session_id: str, create: bool = True) -> Optional[SessionPipeline]:
    p = _PIPES.get(session_id)
    if p is None and create:
        lang = None
        try:
            from claros.session import sessions
            s = sessions.get(session_id)
            lang = getattr(s, "lang", None)
        except Exception:  # noqa: BLE001
            pass
        p = _PIPES[session_id] = SessionPipeline(session_id, _publish, lang_hint=lang)
    return p


def current_state(session_id: str) -> Optional[ScreenState]:
    p = _PIPES.get(session_id)
    return p.published if p else None


def entity_fields(session_id: str) -> dict[str, dict]:
    """Accumulated per-entity field model {entity_key: {label_key: Field_ dict}} (incl. scrolled-off)."""
    p = _PIPES.get(session_id)
    if not p:
        return {}
    return {k: {fk: f.model_dump(mode="json") for fk, f in m.items()} for k, m in p.tracker.entity_models.items()}


def screen_text(session_id: str, max_chars: int = 2000) -> str:
    """Visible text of the latest frame (OCR, PII already masked), top-to-bottom. Free text such as a customer's
    message is not a labeled field, so ScreenState alone does not carry it."""
    p = _PIPES.get(session_id)
    lines = (p.tracker.prev_lines or []) if p else []
    out = " | ".join(ln.text.strip() for ln in sorted(lines, key=lambda ln: (ln.bbox[1] // 12, ln.bbox[0]))
                     if ln.text and ln.text.strip())
    return out[:max_chars]


async def _on_keyframe(sid: str, msg: dict) -> None:
    await get_pipeline(sid).on_keyframe(msg)


async def _on_activity(sid: str, msg: dict) -> None:
    get_pipeline(sid).on_activity(msg)


async def _on_ended(sid: str, payload: Any) -> None:
    p = _PIPES.pop(sid, None)
    if p:
        p.close()


def _warm() -> None:
    try:
        from .ocr import ENGINE
        ENGINE.warm()
    except Exception as e:  # noqa: BLE001
        log.warning("OCR warmup failed: %s", e)


def register(bus: Any) -> None:
    global _BUS
    _BUS = bus
    bus.subscribe("ws.in.keyframe", _on_keyframe)
    bus.subscribe("ws.in.activity", _on_activity)
    bus.subscribe("session.ended", _on_ended)
    from .pii import GLINER
    GLINER.load_async()
    threading.Thread(target=_warm, name="ocr-warm", daemon=True).start()
    log.info("perception registered")


__all__ = ["register", "get_pipeline", "current_state", "entity_fields", "screen_text", "SessionPipeline"]

"""Guarded shims over sibling packages (core/context/knowledge) so brain runs degraded
when they are missing or broken. Everything here is monkeypatch-friendly for tests."""
from __future__ import annotations

import inspect
import logging
import math
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("claros.brain")

BUS: Any = None  # set by brain.register(bus)


def now_ms() -> float:
    return time.time() * 1000.0


# ---------------- bus ----------------

async def publish(session_id: str, topic: str, payload: Any) -> None:
    b = BUS
    if b is None:
        try:
            from claros.bus import bus as b  # type: ignore
        except Exception:  # noqa: BLE001
            return
    try:
        r = b.publish(session_id, topic, payload)
        if inspect.isawaitable(r):
            await r
    except Exception:  # noqa: BLE001
        log.exception("publish %s failed", topic)


async def send(session_id: str, msg: dict) -> None:
    await publish(session_id, "ws.out", msg)


def store_log(session_id: str, kind: str, payload: Any, t: Optional[float] = None) -> None:
    try:
        from claros import store as st  # type: ignore
        fn = getattr(st, "log", None) or getattr(getattr(st, "store", None), "log", None)
        if fn:
            fn(session_id, kind, payload, t if t is not None else now_ms())
    except Exception:  # noqa: BLE001
        pass


# ---------------- sessions ----------------

_local_sessions: dict[str, SimpleNamespace] = {}


def get_session(session_id: Optional[str]) -> Any:
    """Return core Session if available, else a local stand-in (mode/lang/off_record)."""
    if not session_id:
        return SimpleNamespace(id=None, mode="capture", lang="en", off_record=False, workflow_id=None)
    try:
        from claros.session import sessions  # type: ignore
        s = sessions.get(session_id)
        if s is not None:
            return s
    except Exception:  # noqa: BLE001
        pass
    s = _local_sessions.get(session_id)
    if s is None:
        s = SimpleNamespace(id=session_id, mode="capture", lang="en", off_record=False, workflow_id=None)
        _local_sessions[session_id] = s
    return s


def session_lang(session_id: Optional[str]) -> str:
    return (getattr(get_session(session_id), "lang", None) or "en")[:2].lower()


# ---------------- llm ----------------

def _llm():
    try:
        import claros.llm as m  # type: ignore
        return m
    except Exception:  # noqa: BLE001
        return None


def _content_of(r: Any) -> str:
    if r is None:
        return ""
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        if "choices" in r:
            try:
                ch = r["choices"][0]
                return (ch.get("message") or ch.get("delta") or {}).get("content") or ""
            except Exception:  # noqa: BLE001
                return ""
        for k in ("content", "text"):
            if isinstance(r.get(k), str):
                return r[k]
        return ""
    for k in ("content", "text"):
        v = getattr(r, k, None)
        if isinstance(v, str):
            return v
    try:
        return r.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        return str(r)


async def llm_chat(messages: list[dict], *, model_role: str = "fast", json_schema: Any = None,
                   **extra: Any) -> Optional[str]:
    m = _llm()
    if m is None or not hasattr(m, "chat"):
        return None
    kw: dict[str, Any] = {"model_role": model_role, **extra}
    if json_schema is not None:
        kw["json_schema"] = json_schema
    r = await m.chat(messages, **kw)
    if isinstance(r, (dict, list)) and json_schema is not None and "choices" not in (r if isinstance(r, dict) else {}):
        import json
        return json.dumps(r)
    return _content_of(r)


async def llm_stream(messages: list[dict], *, model_role: str = "fast") -> AsyncIterator[str]:
    m = _llm()
    if m is None or not hasattr(m, "chat"):
        return
    r = m.chat(messages, model_role=model_role, stream=True)
    if inspect.isawaitable(r):
        r = await r
    if hasattr(r, "__aiter__"):
        async for ch in r:
            txt = ch if isinstance(ch, str) else _content_of(ch)
            if txt:
                yield txt
    else:
        txt = _content_of(r)
        if txt:
            yield txt


async def embed(texts: list[str]) -> Optional[list[list[float]]]:
    m = _llm()
    if m is None or not hasattr(m, "embed"):
        return None
    try:
        r = m.embed(texts)
        if inspect.isawaitable(r):
            r = await r
        if r and len(r) == len(texts):
            return [list(map(float, v)) for v in r]
    except Exception:  # noqa: BLE001
        log.debug("embed failed", exc_info=True)
    return None


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


# ---------------- context / knowledge ----------------

async def _maybe_await(r: Any) -> Any:
    return await r if inspect.isawaitable(r) else r


def _ctx_for(session_id: str, extra: Optional[dict] = None) -> dict:
    s = get_session(session_id)
    ctx = {"lang": getattr(s, "lang", "en"), "workflow": getattr(s, "workflow_id", None)}
    ctx.update({k: v for k, v in (extra or {}).items() if v})
    return ctx


async def classify_scope(unknown: Any, session_id: str, extra: Optional[dict] = None) -> Optional[str]:
    """claros.context.scope.classify_scope(question: str, context) -> (scope, conf)."""
    try:
        from claros.context import scope as sc  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(sc, "classify_scope", None)
    if fn is None:
        return None
    q = " ".join(x for x in (getattr(unknown, "spoken_question", None), getattr(unknown, "hypothesis", None)) if x) \
        if not isinstance(unknown, str) else unknown
    r = await _maybe_await(fn(q or str(getattr(unknown, "type", "")), _ctx_for(session_id, extra)))
    if r is None:
        return None
    if isinstance(r, tuple):
        scope, conf = r[0], (r[1] if len(r) > 1 else 1.0)
        # low-confidence generic scope → keep for expert
        return scope if conf is None or float(conf) >= 0.5 or scope in ("company", "personal_judgment") else None
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        return r.get("scope")
    return getattr(r, "scope", None)


async def try_resolve(unknown: Any, scope: str, session_id: str, extra: Optional[dict] = None) -> Any:
    try:
        from claros.context import scope as sc  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(sc, "try_resolve", None)
    if fn is None:
        return None
    return await _maybe_await(fn(unknown, _ctx_for(session_id, {**(extra or {}), "scope": scope})))


def knowledge_attr(path: str) -> Any:
    """e.g. knowledge_attr('tutor.handle_intent') → callable or None."""
    import importlib
    mod_path, _, attr = ("claros.knowledge." + path).rpartition(".")
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr, None)
    except Exception:  # noqa: BLE001
        return None


maybe_await = _maybe_await

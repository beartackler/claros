"""Guarded shims over core/context/brain so knowledge runs degraded when siblings are missing.

Everything is monkeypatch-friendly (tests patch `chat`, `embed`, `onet_match`, `decide`).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import re
import time
import uuid
from types import SimpleNamespace
from typing import Any, Optional

log = logging.getLogger("claros.knowledge")

BUS: Any = None  # set by knowledge.register(bus)


def now_ms() -> float:
    return time.time() * 1000.0


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def maybe_await(r: Any) -> Any:
    return await r if inspect.isawaitable(r) else r


# ---------------- bus ----------------

def _bus() -> Any:
    if BUS is not None:
        return BUS
    try:
        from claros.bus import bus  # type: ignore
        return bus
    except Exception:  # noqa: BLE001
        return None


async def publish(session_id: str, topic: str, payload: Any) -> None:
    b = _bus()
    if b is None:
        return
    try:
        await maybe_await(b.publish(session_id, topic, payload))
    except Exception:  # noqa: BLE001
        log.exception("publish %s failed", topic)


async def send(session_id: str, msg: dict) -> None:
    await publish(session_id, "ws.out", msg)


# ---------------- store (with in-memory fallback) ----------------

class _MemStore:
    def __init__(self) -> None:
        self.kv: dict[tuple[str, str], Any] = {}
        self.wf: dict[str, list[dict]] = {}
        self.logs: dict[str, list[dict]] = {}

    def kv_put(self, ns, id, value):
        self.kv[(ns, id)] = json.loads(json.dumps(value, default=str))

    def kv_get(self, ns, id):
        return self.kv.get((ns, id))

    def kv_list(self, ns, prefix=""):
        return [v for (n, i), v in self.kv.items() if n == ns and i.startswith(prefix)]

    def put_workflow(self, wm, version=None):
        d = wm.model_dump(mode="json") if hasattr(wm, "model_dump") else dict(wm)
        vs = self.wf.setdefault(d["workflow_id"], [])
        d["version"] = version or max(int(d.get("version") or 1), len(vs) + 1)
        vs.append(d)
        return d["version"]

    def get_workflow(self, wid, version=None):
        vs = self.wf.get(wid) or []
        if not vs:
            return None
        if version is None:
            return max(vs, key=lambda d: d["version"])
        return next((d for d in vs if d["version"] == version), None)

    def list_workflows(self):
        return [self.get_workflow(w) for w in self.wf]

    def iter_log(self, session_id, kinds=None, after_id=0):
        for e in self.logs.get(session_id, []):
            if not kinds or e["kind"] in kinds:
                yield e

    def log(self, session_id, kind, payload, t=None):
        lst = self.logs.setdefault(session_id, [])
        lst.append({"id": len(lst) + 1, "session_id": session_id, "kind": kind, "t": t, "payload": payload})
        return len(lst)

    def put_keyframe(self, id, session_id, path, t=None, meta=None):
        self.kv[("_keyframes", id)] = {"id": id, "session_id": session_id, "path": str(path), "t": t,
                                       "meta": meta or {}}

    def get_keyframe(self, id):
        return self.kv.get(("_keyframes", id))

    def index_text(self, ns, id, text):
        pass

    def search_text(self, ns, q, k=10):
        return []

    def index_vec(self, ns, id, vec):
        pass

    def search_vec(self, ns, vec, k=10):
        return []


_mem = _MemStore()
STORE: Any = None  # tests may set a Store instance or _MemStore


def store() -> Any:
    if STORE is not None:
        return STORE
    try:
        from claros.store import store as s  # type: ignore
        return s
    except Exception:  # noqa: BLE001
        return _mem


def st_call(name: str, *a: Any, default: Any = None, **kw: Any) -> Any:
    s = store()
    try:
        return getattr(s, name)(*a, **kw)
    except Exception:  # noqa: BLE001
        log.warning("store.%s failed; using memory fallback", name, exc_info=True)
        try:
            return getattr(_mem, name)(*a, **kw)
        except Exception:  # noqa: BLE001
            return default


# ---------------- sessions ----------------

_local_sessions: dict[str, SimpleNamespace] = {}


def get_session(session: Any) -> Any:
    """Accept a Session object or id; return object with id/mode/lang/user/workflow_id."""
    if session is None:
        return SimpleNamespace(id=None, mode="learn", lang="en", user=None, workflow_id=None, extra={})
    if not isinstance(session, str):
        return session
    try:
        from claros.session import sessions  # type: ignore
        s = sessions.get(session)
        if s is not None:
            return s
    except Exception:  # noqa: BLE001
        pass
    s = _local_sessions.get(session)
    if s is None:
        s = SimpleNamespace(id=session, mode="learn", lang="en", user=None, workflow_id=None, extra={})
        _local_sessions[session] = s
    return s


def lang_of(session: Any) -> str:
    return (getattr(get_session(session), "lang", None) or "en")[:2].lower()


def user_of(session: Any) -> Any:
    return getattr(get_session(session), "user", None)


# ---------------- llm ----------------

def _llm() -> Any:
    try:
        import claros.llm as m  # type: ignore
        return m
    except Exception:  # noqa: BLE001
        return None


def _content(r: Any) -> Any:
    if r is None or isinstance(r, (str, dict, list)):
        if isinstance(r, dict) and "choices" in r:
            ch = r["choices"][0]
            return (ch.get("message") or {}).get("content") or ""
        return r
    if hasattr(r, "model_dump"):
        return r.model_dump(mode="json")
    try:
        return r.choices[0].message.content
    except Exception:  # noqa: BLE001
        return str(r)


async def chat(messages: list[dict], *, model_role: str = "smart", json_schema: Any = None) -> Any:
    """Returns str (text) or parsed dict/list (json). None if llm unavailable/failing."""
    m = _llm()
    if m is None or not hasattr(m, "chat"):
        return None
    kw: dict[str, Any] = {"model_role": model_role}
    if json_schema is not None:
        kw["json_schema"] = json_schema
    try:
        r = await maybe_await(m.chat(messages, **kw))
    except Exception:  # noqa: BLE001
        log.warning("llm.chat failed", exc_info=True)
        return None
    r = _content(r)
    if json_schema is not None and isinstance(r, str):
        return parse_json(r)
    return r


def parse_json(s: str) -> Any:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1)
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        m = re.search(r"[\[{].*[\]}]", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def _hash_vec(text: str, dim: int = 256) -> list[float]:
    v = [0.0] * dim
    toks = re.findall(r"\w+", (text or "").lower(), re.UNICODE)
    for t in toks + [a + b for a, b in zip(toks, toks[1:])]:
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed(inputs: list[Any], task: str = "text-matching") -> list[list[float]]:
    """Jina via claros.llm.embed; falls back to a local hashed bag-of-words (text only)."""
    m = _llm()
    if m is not None and hasattr(m, "embed") and inputs:
        try:
            r = await maybe_await(m.embed(inputs, task))
            if r and len(r) == len(inputs):
                return [list(map(float, v)) for v in r]
        except Exception:  # noqa: BLE001
            log.debug("embed failed", exc_info=True)
    return [_hash_vec(x if isinstance(x, str) else json.dumps(x, default=str)) for x in inputs]


async def rerank(query: Any, docs: list[str]) -> Optional[list[float]]:
    """Scores aligned with docs, or None."""
    m = _llm()
    if m is None or not hasattr(m, "rerank") or not docs:
        return None
    try:
        r = await maybe_await(m.rerank(query, docs))
    except Exception:  # noqa: BLE001
        return None
    scores = [0.0] * len(docs)
    try:
        for i, it in enumerate(r or []):
            if isinstance(it, (int, float)):
                scores[i] = float(it)
            elif isinstance(it, dict):
                scores[int(it.get("index", i))] = float(it.get("relevance_score", it.get("score", 0.0)))
        return scores
    except Exception:  # noqa: BLE001
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


# ---------------- context / brain ----------------

async def onet_match(text: str, **kw: Any) -> Any:
    """Returns OnetMatch | None."""
    from claros.models import OnetMatch
    try:
        from claros.context import onet  # type: ignore
        fn = getattr(onet, "match")
    except Exception:  # noqa: BLE001
        return None
    try:
        r = await maybe_await(fn(text, **kw))
    except TypeError:
        try:
            r = await maybe_await(fn(text))
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        log.debug("onet.match failed", exc_info=True)
        return None
    if isinstance(r, list):
        r = r[0] if r else None
    if r is None:
        return None
    try:
        return r if isinstance(r, OnetMatch) else OnetMatch.model_validate(
            r.model_dump() if hasattr(r, "model_dump") else r)
    except Exception:  # noqa: BLE001
        return None


async def decide(question: str, context: str, options: list[str], timeout: Optional[float] = None
                 ) -> tuple[Optional[str], float]:
    """claros.brain.systemone.decide adapter → (label, confidence)."""
    try:
        from claros.brain import systemone  # type: ignore
        fn = getattr(systemone, "decide")
    except Exception:  # noqa: BLE001
        return None, 0.0
    try:
        kw = {"timeout": timeout} if timeout else {}
        r = await maybe_await(fn(question, context=context, options=options, **kw))
    except TypeError:
        try:
            r = await maybe_await(fn(f"{question}\n\n{context}", options))
        except Exception:  # noqa: BLE001
            return None, 0.0
    except Exception:  # noqa: BLE001
        return None, 0.0
    if r is None:
        return None, 0.0
    if isinstance(r, tuple):
        return (str(r[0]) if r[0] is not None else None), float(r[1] if len(r) > 1 else 0.0)
    if isinstance(r, dict):
        return r.get("label") or r.get("choice") or r.get("decision") or r.get("intent"), float(
            r.get("confidence", r.get("score", 0.0)) or 0.0)
    lab = getattr(r, "label", None) or getattr(r, "choice", None) or getattr(r, "decision", None) \
        or getattr(r, "intent", None)
    return lab, float(getattr(r, "confidence", 0.0) or 0.0)


# Pre-written texts (guardrail interventions, asks), keyed by id and session:id.
PREWRITTEN: dict[str, str] = {}


def register_prewritten(key: str, text: str, session_id: Optional[str] = None, kind: str = "intervene") -> None:
    PREWRITTEN[key] = text
    if session_id:
        PREWRITTEN[f"{session_id}:{key}"] = text


def get_prewritten(key: str, session_id: Optional[str] = None) -> Optional[str]:
    if session_id and f"{session_id}:{key}" in PREWRITTEN:
        return PREWRITTEN[f"{session_id}:{key}"]
    return PREWRITTEN.get(key)

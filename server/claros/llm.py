"""OpenAI-compatible LLM client with provider fallback + Jina embeddings/rerank.

    text = await chat(messages, model_role="fast")
    data = await chat(messages, model_role="vision", images=[jpeg_bytes], json_schema=MyModel)  # -> MyModel
    data = await chat(messages, json_schema={...json schema...})                                # -> dict
    async for delta in await chat(messages, stream=True): ...
    vecs = await embed(["text", {"image": b64_or_url}], task="retrieval.passage")
    ranked = await rerank("query", ["doc a", "doc b"])  # -> [{index, score}] best first

Provider order: Isoquant (GLM-5.3-Flash) -> OpenRouter -> Gemini (OpenAI-compat). Providers with
no key are skipped. Any error/timeout/invalid JSON falls through to the next provider. If every
provider fails, raises LLMUnavailable (callers should degrade gracefully).
Model names overridable via env: CLAROS_<PROVIDER>_<ROLE>_MODEL, e.g. CLAROS_GEMINI_FAST_MODEL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Optional

import httpx
import numpy as np

log = logging.getLogger("claros.llm")

Role = Literal["vision", "fast", "smart"]
# vision runs off the hot path (latest-wins queue) and real app screens are dense: a full-form JSON from
# GLM-5.3-Flash takes ~4-7 s with low reasoning, >10 s with default reasoning. smart = map build (big JSON).
TIMEOUTS: dict[str, float] = {"vision": float(os.getenv("CLAROS_VISION_TIMEOUT", "25")), "fast": 4.0,
                              "smart": float(os.getenv("CLAROS_SMART_TIMEOUT", "120"))}


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Provider:
    name: str
    base_url: str
    key_env: str
    models: dict[str, str]
    extra: dict[str, Any] = field(default_factory=dict)  # merged into request body
    headers: dict[str, str] = field(default_factory=dict)
    role_extra: dict[str, dict[str, Any]] = field(default_factory=dict)  # per-role body extras
    # "schema": response_format json_schema; "object": json_object + schema in the system prompt.
    # GLM with strict json_schema returns only the required keys (empty fields/tables) — use "object".
    json_mode: str = "schema"

    @property
    def key(self) -> Optional[str]:
        return os.getenv(self.key_env) or None

    def model(self, role: str) -> str:
        return os.getenv(f"CLAROS_{self.name.upper()}_{role.upper()}_MODEL") or self.models[role]


def _providers() -> list[Provider]:
    return [
        Provider("isoquant", os.getenv("ISOQUANT_BASE_URL", "https://api.isoquant.ai/v1"), "ISOQUANT_API_KEY",
                 {"vision": "glm-5.3-flash", "fast": "glm-5.3-flash", "smart": "glm-5.3-flash"},
                 extra=json.loads(os.getenv("CLAROS_ISOQUANT_EXTRA", "{}")),
                 # default reasoning burns ~900 tokens (≈10 s) per call; low keeps vision ~5 s, fast ~1 s
                 role_extra=json.loads(os.getenv("CLAROS_ISOQUANT_ROLE_EXTRA", json.dumps({
                     "vision": {"reasoning_effort": "low"}, "fast": {"reasoning_effort": "low"},
                     "smart": {"reasoning_effort": "low"}}))),
                 json_mode=os.getenv("CLAROS_ISOQUANT_JSON_MODE", "object")),
        Provider("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                 {"vision": "qwen/qwen3-vl-30b-a3b-instruct", "fast": "z-ai/glm-5.3-flash",
                  "smart": "z-ai/glm-5.3-flash"},
                 extra={"reasoning": {"effort": "low"}},
                 headers={"HTTP-Referer": "https://github.com/claros", "X-Title": "Claros"}),
        Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
                 {"vision": "gemini-3-flash", "fast": "gemini-3-flash", "smart": "gemini-3-flash"},
                 extra={"reasoning_effort": "low"}),
    ]


# Shared client; tests may replace with httpx.AsyncClient(transport=httpx.MockTransport(...)).
HTTP: Optional[httpx.AsyncClient] = None
STATS: deque[dict] = deque(maxlen=200)  # recent calls: provider, role, latency_ms, ok, error


def _http() -> httpx.AsyncClient:
    global HTTP
    if HTTP is None:
        HTTP = httpx.AsyncClient(timeout=30.0)
    return HTTP


def keys_present() -> dict[str, bool]:
    return {p.name: bool(p.key) for p in _providers()} | {"jina": bool(os.getenv("JINA_API_KEY"))}


# ---------------- chat ----------------

def _image_url(img: Any) -> str:
    if isinstance(img, (bytes, bytearray)):
        return "data:image/jpeg;base64," + base64.b64encode(img).decode()
    s = str(img)
    if s.startswith(("data:", "http://", "https://")):
        return s
    return "data:image/jpeg;base64," + s  # raw base64


def _with_images(messages: list[dict], images: Optional[list]) -> list[dict]:
    if not images:
        return messages
    msgs = [dict(m) for m in messages]
    idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if idx is None:
        msgs.append({"role": "user", "content": []})
        idx = len(msgs) - 1
    c = msgs[idx].get("content")
    parts = [{"type": "text", "text": c}] if isinstance(c, str) else list(c or [])
    parts += [{"type": "image_url", "image_url": {"url": _image_url(i)}} for i in images]
    msgs[idx]["content"] = parts
    return msgs


def _schema_of(js: Any) -> tuple[dict, Optional[type]]:
    if isinstance(js, type) and hasattr(js, "model_json_schema"):
        return js.model_json_schema(), js
    return dict(js), None


def _with_schema_hint(messages: list[dict], schema: dict) -> list[dict]:
    """json_object mode: put the schema in the system prompt (compact) so the model emits every key."""
    if not schema or schema == {"type": "object"} or set(schema) <= {"type"}:
        return messages
    hint = ("\n\nReturn ONLY one JSON object conforming to this JSON Schema (fill every key you can; "
            "use [] / null when empty): " + json.dumps(schema, separators=(",", ":"), ensure_ascii=False))
    msgs = [dict(m) for m in messages]
    for m in msgs:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            m["content"] += hint
            return msgs
    return [{"role": "system", "content": hint.strip()}] + msgs


def _parse_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not m:
            raise
        return json.loads(m.group(1))


def _record(provider: str, role: str, t0: float, ok: bool, err: str = "") -> None:
    ms = (time.perf_counter() - t0) * 1000
    STATS.append({"provider": provider, "role": role, "latency_ms": round(ms), "ok": ok, "error": err[:200],
                  "at": time.time()})
    (log.info if ok else log.warning)("llm %s role=%s %.0fms ok=%s %s", provider, role, ms, ok, err[:200])


async def chat(messages: list[dict], *, model_role: Role = "fast", json_schema: Any = None,
               images: Optional[list] = None, stream: bool = False, timeout: Optional[float] = None,
               temperature: Optional[float] = None, max_tokens: Optional[int] = None,
               tools: Optional[list] = None) -> Any:
    """Returns str (default), parsed dict / pydantic instance (json_schema), message dict with
    tool_calls (when tools given and model calls one), or async iterator of str deltas (stream)."""
    msgs = _with_images(messages, images)
    to = timeout or TIMEOUTS.get(model_role, 8.0)
    schema, model_cls = (None, None)
    if json_schema is not None:
        schema, model_cls = _schema_of(json_schema)
    if stream:
        return _stream(msgs, model_role, to, temperature, max_tokens)

    errors: list[str] = []
    for p in _providers():
        if not p.key:
            continue
        body: dict[str, Any] = {"model": p.model(model_role), "messages": msgs, **p.extra,
                                **p.role_extra.get(model_role, {})}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if schema is not None:
            if p.json_mode == "object":
                body["response_format"] = {"type": "json_object"}
                body["messages"] = _with_schema_hint(msgs, schema)
            else:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": (model_cls.__name__ if model_cls else schema.get("title", "result")), "schema": schema}}
        t0 = time.perf_counter()
        try:
            r = await _http().post(f"{p.base_url}/chat/completions", json=body, timeout=to,
                                   headers={"Authorization": f"Bearer {p.key}", **p.headers})
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            if tools and msg.get("tool_calls"):
                _record(p.name, model_role, t0, True)
                return msg
            text = msg.get("content") or ""
            if schema is not None:
                data = _parse_json(text)
                out = model_cls.model_validate(data) if model_cls else data
            else:
                out = text
            _record(p.name, model_role, t0, True)
            return out
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            if isinstance(e, httpx.HTTPStatusError):
                err += " " + e.response.text[:200]
            _record(p.name, model_role, t0, False, err)
            errors.append(f"{p.name}: {err}")
    raise LLMUnavailable("; ".join(errors) or "no LLM provider keys configured")


async def _stream(msgs: list[dict], role: str, to: float, temperature: Optional[float],
                  max_tokens: Optional[int]) -> AsyncIterator[str]:
    errors: list[str] = []
    for p in _providers():
        if not p.key:
            continue
        body: dict[str, Any] = {"model": p.model(role), "messages": msgs, "stream": True, **p.extra,
                                **p.role_extra.get(role, {})}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        t0 = time.perf_counter()
        yielded = False
        try:
            async with _http().stream("POST", f"{p.base_url}/chat/completions", json=body, timeout=to,
                                      headers={"Authorization": f"Bearer {p.key}", **p.headers}) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        if not yielded:
                            _record(p.name, role + ":ttft", t0, True)
                        yielded = True
                        yield delta
            _record(p.name, role + ":stream", t0, True)
            return
        except Exception as e:  # noqa: BLE001
            _record(p.name, role + ":stream", t0, False, f"{type(e).__name__}: {e}")
            errors.append(f"{p.name}: {e}")
            if yielded:  # can't restart mid-stream
                return
    raise LLMUnavailable("; ".join(errors) or "no LLM provider keys configured")


# ---------------- embeddings / rerank (Jina) ----------------

JINA_BASE = "https://api.jina.ai/v1"
EMBED_MODEL = os.getenv("JINA_EMBED_MODEL", "jina-embeddings-v5-omni-small")
RERANK_MODEL = os.getenv("JINA_RERANK_MODEL", "jina-reranker-m0")
HASH_DIM = 256


def hash_embed(item: Any, dim: int = HASH_DIM) -> list[float]:
    """Deterministic offline fallback: hashed bag of word unigrams + char trigrams, L2-normalized."""
    v = np.zeros(dim, dtype=np.float32)
    if isinstance(item, dict):
        item = item.get("text") or item.get("image") or ""
    if isinstance(item, (bytes, bytearray)):
        feats = [hashlib.md5(item).hexdigest()]
    else:
        s = str(item).lower()
        words = re.findall(r"\w+", s, re.UNICODE)
        feats = words + [w[i:i + 3] for w in words for i in range(max(1, len(w) - 2))]
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
        v[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


def _jina_input(x: Any) -> Any:
    if isinstance(x, dict):
        if "image" in x and isinstance(x["image"], (bytes, bytearray)):
            return {"image": base64.b64encode(x["image"]).decode()}
        return x
    if isinstance(x, (bytes, bytearray)):
        return {"image": base64.b64encode(x).decode()}
    return {"text": str(x)}


async def embed(inputs: list[Any], task: str = "retrieval.passage") -> list[list[float]]:
    """inputs: str | {"text"} | {"image": url|b64|bytes} | bytes. Falls back to hash_embed."""
    if not inputs:
        return []
    key = os.getenv("JINA_API_KEY")
    if key:
        t0 = time.perf_counter()
        try:
            r = await _http().post(f"{JINA_BASE}/embeddings", timeout=10.0,
                                   headers={"Authorization": f"Bearer {key}"},
                                   json={"model": EMBED_MODEL, "task": task, "input": [_jina_input(x) for x in inputs]})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            _record("jina", "embed", t0, True)
            return [d["embedding"] for d in data]
        except Exception as e:  # noqa: BLE001
            _record("jina", "embed", t0, False, f"{type(e).__name__}: {e}")
    return [hash_embed(x) for x in inputs]


async def rerank(query: str, docs: list[Any], top_n: Optional[int] = None) -> list[dict]:
    """-> [{index, score}] best first. Falls back to hash-embedding cosine."""
    if not docs:
        return []
    key = os.getenv("JINA_API_KEY")
    if key:
        t0 = time.perf_counter()
        try:
            body: dict[str, Any] = {"model": RERANK_MODEL, "query": query, "documents": docs,
                                    "return_documents": False}
            if top_n:
                body["top_n"] = top_n
            r = await _http().post(f"{JINA_BASE}/rerank", json=body, timeout=10.0,
                                   headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            _record("jina", "rerank", t0, True)
            return [{"index": x["index"], "score": float(x["relevance_score"])} for x in r.json()["results"]]
        except Exception as e:  # noqa: BLE001
            _record("jina", "rerank", t0, False, f"{type(e).__name__}: {e}")
    q = np.asarray(hash_embed(query))
    scores = [float(q @ np.asarray(hash_embed(d))) for d in docs]
    order = sorted(range(len(docs)), key=lambda i: -scores[i])
    return [{"index": i, "score": scores[i]} for i in order[: top_n or len(docs)]]

"""System One decision adapter (fast, small-model decisions with calibrated confidence).

Open System One schema:
  POST {base}/v1/systemone
  {model, state, questions: {name: {type: choice|score|noul, instructions, criteria}}}
  → {answers: {name: {choice|score|noul, probabilities, confidence}}}

For `choice` questions, `criteria` is a dict {label: description} (labels = options);
for `score`, `criteria` is a list of anchors (low→high) and `score` ∈ 0..1.
Backends (in order): Ollama (OLLAMA_URL) → Fastino GLiDE → LLM (claros.llm fast) → rules.
Confidence is always recomputed as top1 - top2 over `probabilities`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Optional

import httpx

from . import deps

log = logging.getLogger("claros.brain.systemone")

FASTINO_URL = "https://api.fastino.ai/v1/systemone"
FASTINO_MODEL = "fastino/GLiDE"

RulesFn = Callable[[Any, dict], dict]


def _options(q: dict) -> list[str]:
    c = q.get("criteria")
    if isinstance(c, dict):
        return list(c.keys())
    if isinstance(c, list):
        return [str(x) for x in c]
    if q.get("type") == "noul":
        return ["no", "unlikely", "likely", "yes"]  # NOUL ladder (best effort)
    return []


def normalize_answer(q: dict, a: dict) -> dict:
    a = dict(a or {})
    probs = a.get("probabilities")
    if isinstance(probs, dict) and probs:
        vals = sorted((float(v) for v in probs.values()), reverse=True)
        a["confidence"] = round(vals[0] - (vals[1] if len(vals) > 1 else 0.0), 4)
        top = max(probs.items(), key=lambda kv: float(kv[1]))[0]
        qt = q.get("type", "choice")
        if qt == "choice" and not a.get("choice"):
            a["choice"] = top
        if qt == "noul" and not a.get("noul"):
            a["noul"] = top
    elif isinstance(probs, list) and probs:
        vals = sorted((float(v) for v in probs), reverse=True)
        a["confidence"] = round(vals[0] - (vals[1] if len(vals) > 1 else 0.0), 4)
    else:
        a.setdefault("confidence", 0.0)
    if q.get("type") == "score" and "score" in a:
        try:
            a["score"] = float(a["score"])
        except Exception:  # noqa: BLE001
            a["score"] = 0.0
    return a


def default_rules(state: Any, questions: dict) -> dict:
    out = {}
    for name, q in questions.items():
        t = q.get("type", "choice")
        opts = _options(q)
        if t == "score":
            out[name] = {"score": 0.5, "confidence": 0.0}
        elif opts:
            out[name] = {t: opts[0], "probabilities": {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(opts)},
                         "confidence": 0.0}
        else:
            out[name] = {t: None, "confidence": 0.0}
    return out


class SystemOne:
    def __init__(self) -> None:
        self._down_until: dict[str, float] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self.last_backend: Optional[str] = None

    # ---- config ----
    def backends(self) -> list[str]:
        env = os.getenv("CLAROS_SYSTEMONE_BACKENDS")
        if env:
            return [b.strip() for b in env.split(",") if b.strip()]
        out = []
        if os.getenv("OLLAMA_URL") and os.getenv("CLAROS_DECIDER_OLLAMA", "1") != "0":
            out.append("ollama")
        if os.getenv("FASTINO_API_KEY"):
            out.append("fastino")
        out += ["llm", "rules"]
        return out

    def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    def _is_down(self, b: str) -> bool:
        return self._down_until.get(b, 0) > time.monotonic()

    def _mark_down(self, b: str, secs: float = 30.0) -> None:
        self._down_until[b] = time.monotonic() + secs

    # ---- backends ----
    async def _post(self, url: str, body: dict, headers: dict, timeout: float) -> httpx.Response:
        return await self._client_().post(url, json=body, headers=headers, timeout=timeout)

    async def _ollama(self, state: Any, questions: dict, timeout: float) -> dict:
        base = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        model = os.getenv("CLAROS_DECIDER_MODEL", "clef-flash")
        r = await self._post(f"{base}/v1/systemone", {"model": model, "state": state, "questions": questions},
                             {}, timeout)
        r.raise_for_status()
        self.last_backend = f"systemone:{model}"
        return r.json().get("answers", {})

    async def _fastino(self, state: Any, questions: dict, timeout: float) -> dict:
        key = os.getenv("FASTINO_API_KEY", "")
        body = {"model": FASTINO_MODEL, "state": state, "questions": questions}
        deadline = time.monotonic() + timeout
        delay = 0.25
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0.02:
                raise TimeoutError("fastino warming")
            r = await self._post(FASTINO_URL, body, {"X-API-Key": key}, rem)
            if r.status_code == 425:  # model_warming
                await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic() - 0.05)))
                delay = min(delay * 2, 2.0)
                continue
            r.raise_for_status()
            self.last_backend = f"systemone:{FASTINO_MODEL}"
            return r.json().get("answers", {})

    async def _llm(self, state: Any, questions: dict, timeout: float) -> dict:
        spec = {}
        for n, q in questions.items():
            spec[n] = {"type": q.get("type", "choice"), "instructions": q.get("instructions", ""),
                       "options": _options(q) if q.get("type") != "score" else "number 0..1",
                       "criteria": q.get("criteria")}
        sys = ("You are a fast decision model. For each question return calibrated probabilities. "
               "Reply ONLY JSON: {\"answers\": {name: {\"choice\"|\"score\"|\"noul\": value, "
               "\"probabilities\": {option: p}}}} (probabilities sum to 1; for score give \"score\" in 0..1).")
        user = json.dumps({"state": state, "questions": spec}, ensure_ascii=False, default=str)
        txt = await asyncio.wait_for(
            deps.llm_chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                          model_role="fast", json_schema={"type": "object"}), timeout)
        if not txt:
            raise RuntimeError("llm unavailable")
        data = _parse_json(txt)
        self.last_backend = "llm"
        return data.get("answers", data)

    # ---- public ----
    async def decide(self, state: Any, questions: dict, *, timeout: float = 2.0,
                     rules: Optional[RulesFn] = None, fallback: bool = True,
                     backends: Optional[list[str]] = None) -> dict:
        """Returns {answers: {name: {...}}, backend: str, latency_ms}.
        fallback=False → only systemone backends (no llm/rules); answers may be {} then."""
        t0 = time.monotonic()
        deadline = t0 + timeout
        order = backends or self.backends()
        if not fallback:
            order = [b for b in order if b in ("ollama", "fastino")]
        for b in order:
            rem = deadline - time.monotonic()
            if b == "rules":
                ans = (rules or default_rules)(state, questions)
                return self._finish(questions, ans, "rules", t0)
            if rem <= 0.01 or self._is_down(b):
                continue
            try:
                fn = {"ollama": self._ollama, "fastino": self._fastino, "llm": self._llm}[b]
                ans = await asyncio.wait_for(fn(state, questions, rem), rem)
                if not isinstance(ans, dict) or not ans:
                    raise ValueError("empty answers")
                return self._finish(questions, ans, self.last_backend or b, t0)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                log.info("systemone %s unreachable: %s", b, e)
                self._mark_down(b, 60)
            except asyncio.TimeoutError:
                log.info("systemone %s timed out", b)
            except httpx.HTTPStatusError as e:
                log.info("systemone %s HTTP %s: %s", b, e.response.status_code, e.response.text[:200])
                if e.response.status_code >= 500:
                    self._mark_down(b, 15)
            except Exception as e:  # noqa: BLE001
                log.info("systemone %s failed: %s", b, e)
                if b != "llm":
                    self._mark_down(b, 15)
        if fallback:
            ans = (rules or default_rules)(state, questions)
            return self._finish(questions, ans, "rules", t0)
        return {"answers": {}, "backend": "none", "latency_ms": round((time.monotonic() - t0) * 1000)}

    def _finish(self, questions: dict, ans: dict, backend: str, t0: float) -> dict:
        norm = {n: normalize_answer(q, ans.get(n, {})) for n, q in questions.items()}
        return {"answers": norm, "backend": backend, "latency_ms": round((time.monotonic() - t0) * 1000)}

    async def warmup(self) -> None:
        """Ping Fastino so GLiDE is warm before the demo; ignores errors."""
        if not os.getenv("FASTINO_API_KEY"):
            return
        q = {"ok": {"type": "choice", "instructions": "Is this a test?", "criteria": {"yes": "yes", "no": "no"}}}
        try:
            await self._fastino("warmup", q, 20.0)
        except Exception as e:  # noqa: BLE001
            log.info("fastino warmup: %s", e)


def _parse_json(txt: str) -> dict:
    txt = txt.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[txt.find("{"):]
    s, e = txt.find("{"), txt.rfind("}")
    return json.loads(txt[s:e + 1]) if s >= 0 and e > s else {}


default = SystemOne()


async def decide(state: Any, questions: Any = None, *, timeout: float = 2.0, rules: Optional[RulesFn] = None,
                 fallback: bool = True, backends: Optional[list[str]] = None,
                 context: Any = None, options: Optional[list[str]] = None) -> dict:
    """Full form: decide(state, {name: question}). Convenience form (single choice):
    decide("question", context="...", options=[...]) or decide("question", [options])
    → also returns top-level {choice, label, confidence}."""
    if options is None and isinstance(questions, (list, tuple)):
        options, questions = list(questions), None
    if questions is None:
        opts = options or ["yes", "no"]
        questions = {"q": {"type": "choice", "instructions": str(state), "criteria": {o: o for o in opts}}}
        st = {"question": state, "context": context} if context is not None else state
        res = await default.decide(st, questions, timeout=timeout, rules=rules, fallback=fallback,
                                   backends=backends)
        a = res["answers"].get("q", {})
        res.update(choice=a.get("choice"), label=a.get("choice"), confidence=a.get("confidence", 0.0))
        return res
    return await default.decide(state, questions, timeout=timeout, rules=rules, fallback=fallback,
                                backends=backends)


async def warmup() -> None:
    await default.warmup()

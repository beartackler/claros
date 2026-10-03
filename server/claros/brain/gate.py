"""Pause gate: decides *when* Claros may interrupt the expert with the top unknown.

Hard conditions (all must hold): not off-record, not snoozed, screen settled ≥1.2s, no speech ≥1.5s,
agent listening, not typing, not away, reading grace over, budget (≤5/10min, ≥90s apart).
Then System One (≤300ms) chooses ask_now / wait / defer; rules fallback.
Every decision is logged with reasons for the "why Claros asked" inspector.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from . import deps, systemone
from .ledger import GUARDRAIL_TYPES, get_ledger


@dataclass
class GateConfig:
    settle_ms: float = 1200
    silence_ms: float = 1500
    budget_n: int = 5
    budget_window_ms: float = 600_000
    min_gap_ms: float = 90_000
    snooze_ms: float = 300_000
    grace_per_word_ms: float = 240
    grace_cap_ms: float = 8000
    boundary_bonus_ms: float = 6000
    decide_timeout_s: float = 0.3
    recheck_after_wait_ms: float = 2000
    tick_s: float = 0.3


@dataclass
class GateState:
    activity: str = "idle"
    activity_t: float = 0.0
    last_change_t: float = 0.0
    speaking: bool = False
    last_speech_t: float = 0.0
    agent_mode: str = "listening"
    asks: list = field(default_factory=list)
    snooze_until: float = 0.0
    off_record: bool = False
    grace_until: float = 0.0
    boundary_t: float = 0.0
    last_decide_t: float = 0.0
    last_reasons: Optional[tuple] = None
    log: deque = field(default_factory=lambda: deque(maxlen=300))


class Gate:
    def __init__(self, config: Optional[GateConfig] = None) -> None:
        self.cfg = config or GateConfig()
        self.states: dict[str, GateState] = {}
        self._ticker: Optional[asyncio.Task] = None

    def st(self, sid: str) -> GateState:
        s = self.states.get(sid)
        if s is None:
            s = self.states[sid] = GateState()
            now = deps.now_ms()
            s.last_change_t = now
        return s

    # ---------- inputs ----------
    def on_activity(self, sid: str, p: dict) -> None:
        s, now = self.st(sid), deps.now_ms()
        s.activity = p.get("kind", "idle")
        s.activity_t = now
        if p.get("tiles_changed", 0) or s.activity in ("typing", "scrolling", "navigating"):
            s.last_change_t = now
        self._ensure_ticker()

    def on_vad(self, sid: str, p: dict) -> None:
        s = self.st(sid)
        sp = bool(p.get("speaking"))
        if s.speaking and not sp:
            s.last_speech_t = deps.now_ms()
        if sp:
            s.last_speech_t = deps.now_ms()
        s.speaking = sp

    def on_agent_state(self, sid: str, p: dict) -> None:
        s = self.st(sid)
        s.agent_mode = p.get("mode", "listening")
        if s.agent_mode == "speaking":
            s.last_speech_t = deps.now_ms()

    def on_screen_state(self, sid: str, p: Any) -> None:
        s, now = self.st(sid), deps.now_ms()
        d = p if isinstance(p, dict) else getattr(p, "__dict__", {}) | (getattr(p, "model_extra", None) or {})
        n = 0
        for k in ("new_words", "new_word_count", "ocr_new_words"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                n = int(v)
            elif isinstance(v, list):
                n = len(v)
            if n:
                break
        if n:
            s.grace_until = max(s.grace_until, now + min(self.cfg.grace_cap_ms, n * self.cfg.grace_per_word_ms))
        s.last_change_t = now

    def on_events(self, sid: str, items: Any) -> None:
        s = self.st(sid)
        for e in items or []:
            d = e if isinstance(e, dict) else e.model_dump()
            k = d.get("kind")
            view = (d.get("summary") or "").lower()
            if k in ("save", "submit") or (k == "navigate" and ("list" in view or "liste" in view or "спис" in view)):
                s.boundary_t = deps.now_ms()

    def snooze(self, sid: str, ms: Optional[float] = None) -> None:
        self.st(sid).snooze_until = deps.now_ms() + (ms or self.cfg.snooze_ms)

    def set_off_record(self, sid: str, on: bool) -> None:
        self.st(sid).off_record = on

    # ---------- decision ----------
    def blockers(self, sid: str, now: float) -> list[str]:
        s, c = self.st(sid), self.cfg
        r = []
        sess = deps.get_session(sid)
        if s.off_record or getattr(sess, "off_record", False):
            r.append("off_record")
        if getattr(sess, "mode", "capture") not in ("capture", "debrief"):
            r.append(f"mode={getattr(sess, 'mode', None)}")
        if now < s.snooze_until:
            r.append(f"snoozed {int((s.snooze_until - now) / 1000)}s")
        if now - s.last_change_t < c.settle_ms:
            r.append(f"screen not settled ({int(now - s.last_change_t)}ms)")
        if s.speaking or now - s.last_speech_t < c.silence_ms:
            r.append("speech" if s.speaking else f"recent speech ({int(now - s.last_speech_t)}ms)")
        if s.agent_mode != "listening":
            r.append(f"agent {s.agent_mode}")
        if s.activity == "typing" and now - s.activity_t < 5000:
            r.append("typing")
        if s.activity == "away":
            r.append("away")
        if now < s.grace_until:
            r.append(f"reading grace {int((s.grace_until - now) / 1000)}s")
        recent = [t for t in s.asks if now - t < c.budget_window_ms]
        if len(recent) >= c.budget_n:
            r.append(f"budget {len(recent)}/{c.budget_n} per 10min")
        if s.asks and now - s.asks[-1] < c.min_gap_ms:
            r.append(f"min gap ({int((now - s.asks[-1]) / 1000)}s < {int(c.min_gap_ms / 1000)}s)")
        return r

    def _log(self, sid: str, entry: dict) -> None:
        s = self.st(sid)
        sig = (entry.get("decision"), entry.get("unknown_id"), tuple(entry.get("reasons", [])))
        if entry.get("decision") == "blocked" and sig == s.last_reasons:
            return
        s.last_reasons = sig
        s.log.append(entry)
        deps.store_log(sid, "gate", entry, entry["t"])

    async def evaluate(self, sid: str, now: Optional[float] = None) -> dict:
        now = now if now is not None else deps.now_ms()
        s, c = self.st(sid), self.cfg
        lg = get_ledger(sid)
        u = lg.top_candidate(now)
        if u is None:
            return {"decision": "idle", "reasons": ["no candidate"]}
        bl = self.blockers(sid, now)
        if bl:
            e = {"t": now, "unknown_id": u.id, "decision": "blocked", "reasons": bl, "backend": "rules"}
            self._log(sid, e)
            return e
        if now - s.last_decide_t < c.recheck_after_wait_ms:
            return {"decision": "cooldown", "reasons": []}
        s.last_decide_t = now
        boundary = now - s.boundary_t < c.boundary_bonus_ms
        thr = lg.ask_threshold(now)
        age_s = (now - u.created_t) / 1000
        need_guard = u.type in GUARDRAIL_TYPES and not lg.guardrail_asked()
        reasons = [f"priority {u.priority:.2f} (threshold {thr:.2f})", f"age {age_s:.0f}s",
                   f"silence {int((now - s.last_speech_t) / 1000)}s", f"settled {int((now - s.last_change_t) / 1000)}s"]
        if boundary:
            reasons.append("task boundary (save/submit/list)")
        if need_guard:
            reasons.append("no guardrail question asked yet")

        def rules(_st: Any, _q: dict) -> dict:
            score = u.priority + (0.5 if boundary else 0) + (0.3 if need_guard else 0)
            if score >= thr:
                ch = "ask_now"
            elif age_s > 35:
                ch = "defer"
            else:
                ch = "wait"
            return {"decision": {"choice": ch, "probabilities": {ch: 1.0}}}

        state = {"unknown": {"type": u.type, "question": u.spoken_question, "priority": u.priority,
                             "age_s": round(age_s)}, "boundary": boundary, "activity": s.activity,
                 "asked_so_far": len(lg.asked_ids), "threshold": thr}
        q = {"decision": {"type": "choice", "instructions": "Is this a good moment to interrupt an expert "
                          "with this question? Prefer task boundaries; avoid breaking flow.",
                          "criteria": {"ask_now": "natural pause, worth asking now",
                                       "wait": "might be a better moment soon",
                                       "defer": "save for the debrief"}}}
        try:
            res = await systemone.decide(state, q, timeout=c.decide_timeout_s, rules=rules,
                                         backends=[b for b in systemone.default.backends() if b != "llm"])
        except Exception:  # noqa: BLE001
            res = {"answers": rules(None, q), "backend": "rules"}
        a = res["answers"].get("decision") or {}
        ch = a.get("choice") or "wait"
        entry = {"t": now, "unknown_id": u.id, "decision": ch, "reasons": reasons, "backend": res.get("backend"),
                 "confidence": a.get("confidence"), "question": u.spoken_question}
        if ch == "ask_now":
            lg.mark_asked(u.id)
            s.asks.append(now)
            await deps.send(sid, {"type": "ask", "unknown_id": u.id, "text": u.spoken_question})
            await lg.emit()
        elif ch == "defer":
            u.status = "deferred"
            await lg.emit()
        self._log(sid, entry)
        return entry

    # ---------- ticker ----------
    def _ensure_ticker(self) -> None:
        if self._ticker and not self._ticker.done():
            return
        try:
            self._ticker = asyncio.get_running_loop().create_task(self._tick())
        except RuntimeError:
            pass

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            for sid in list(self.states):
                sess = deps.get_session(sid)
                if getattr(sess, "ended", False):
                    self.states.pop(sid, None)
                    continue
                try:
                    await self.evaluate(sid)
                except Exception:  # noqa: BLE001
                    deps.log.exception("gate evaluate failed")

    def decisions(self, sid: str) -> list[dict]:
        return list(self.st(sid).log)


gate = Gate()

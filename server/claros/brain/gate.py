"""Pause gate: decides *when* Claros may interrupt the expert with the top unknown.

Hard conditions (all must hold): not off-record, not snoozed, screen settled ≥1.2s, no speech ≥1.5s,
agent listening, not typing, not away, reading grace over, budget (≤5/10min, ≥90s apart).
Then PRISM rule: ask iff p_pause × p_accept > τ = c_FA/(c_FA+c_miss) (no model in the hot path;
System One refines p_accept in the background). Guardrail-risk unknowns bypass budget/gap.
Every decision is logged with reasons for the "why Claros asked" inspector.
"""
from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from . import deps, systemone
from .ledger import GUARDRAIL_TYPES, LIVE_TARGET, get_ledger


@dataclass
class GateConfig:
    settle_ms: float = 1200
    silence_ms: float = 1500
    budget_n: int = 5
    budget_window_ms: float = 600_000
    min_gap_ms: float = 45_000  # was 90 s: with 45-60 s unknown expiry most mid-task asks died waiting (e2e)
    snooze_ms: float = 300_000
    grace_per_word_ms: float = 240
    grace_cap_ms: float = 8000
    boundary_bonus_ms: float = 15000  # apps redraw for seconds after Save; the pause after it must still count
    decide_timeout_s: float = 0.3
    c_fa: float = 3.0   # cost of a false alarm (interrupting at a bad moment / low-value question)
    c_miss: float = 1.0  # cost of missing a question → τ = 0.75 for expert capture
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
    p_end: Optional[float] = None
    asks: list = field(default_factory=list)
    snooze_until: float = 0.0
    off_record: bool = False
    grace_until: float = 0.0
    boundary_t: float = 0.0
    boundary_kind: Optional[str] = None
    sig_last: Optional[tuple] = None
    sig_t: float = 0.0
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
        for k in ("p_end", "smart_turn", "end_of_turn"):
            if isinstance(p.get(k), (int, float)):
                s.p_end = float(p[k])

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
        # Stamp at the frame's own time: vision lands seconds late, and the expert has been reading meanwhile.
        t = d.get("t")
        off = getattr(deps.get_session(sid), "clock_offset", 0.0) or 0.0
        frame_t = min(now, float(t) + off) if isinstance(t, (int, float)) else now
        if n:
            s.grace_until = max(s.grace_until, frame_t + min(self.cfg.grace_cap_ms, n * self.cfg.grace_per_word_ms))
        s.last_change_t = max(s.last_change_t, frame_t)

    def on_events(self, sid: str, items: Any) -> None:
        s = self.st(sid)
        for e in items or []:
            d = e if isinstance(e, dict) else e.model_dump()
            k = d.get("kind")
            view = (d.get("summary") or "").lower()
            if k in ("save", "submit") or (k == "navigate" and ("list" in view or "liste" in view or "спис" in view)):
                s.boundary_t = deps.now_ms()
                s.boundary_kind = k if k in ("save", "submit") else "list"

    def snooze(self, sid: str, ms: Optional[float] = None) -> None:
        self.st(sid).snooze_until = deps.now_ms() + (ms or self.cfg.snooze_ms)

    def set_off_record(self, sid: str, on: bool) -> None:
        self.st(sid).off_record = on

    # ---------- decision ----------
    def blockers(self, sid: str, now: float, bypass: bool = False) -> list[str]:
        s, c = self.st(sid), self.cfg
        r = []
        sess = deps.get_session(sid)
        if s.off_record or getattr(sess, "off_record", False):
            r.append("off_record")
        if getattr(sess, "mode", "capture") not in ("capture", "debrief"):
            r.append(f"mode={getattr(sess, 'mode', None)}")
        if bypass:  # guardrail risk: only never talk over someone / off-record
            return [x for x in r if x == "off_record"] + (["speech"] if s.speaking else []) + \
                ([f"agent {s.agent_mode}"] if s.agent_mode == "speaking" else [])
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
        if entry.get("decision") == "blocked" and s.last_reasons and sig == s.last_reasons:
            return
        s.last_reasons = sig
        s.log.append(entry)
        deps.store_log(sid, "gate", entry, entry["t"])

    # ---------- PRISM-style decision-theoretic rule ----------
    def p_pause(self, sid: str, now: float) -> dict:
        """p_pause = p_end(turn) × p_silence × p_settle × p_not_typing (boundary bonus)."""
        s = self.st(sid)
        silence = now - s.last_speech_t
        settle = now - s.last_change_t
        p_sil = 0.0 if s.speaking else 1 - math.exp(-silence / 700)
        p_set = 1 - math.exp(-settle / 600)
        p_nt = 0.05 if (s.activity == "typing" and now - s.activity_t < 5000) else (0.0 if s.activity == "away" else 1.0)
        p_end = s.p_end if s.p_end is not None else 1.0  # Smart Turn end-of-turn prob (when client sends it)
        p = p_end * p_sil * p_set * p_nt
        boundary = now - s.boundary_t < self.cfg.boundary_bonus_ms
        if boundary:
            p = 1 - (1 - p) * 0.5
        return {"p_pause": round(p, 3), "p_end": round(p_end, 3), "p_silence": round(p_sil, 3),
                "p_settle": round(p_set, 3), "p_not_typing": p_nt, "boundary": boundary}

    def p_accept(self, lg: Any, u: Any, now: float) -> dict:
        thr = lg.ask_threshold(now)
        prio = lg.priority(u, now)
        p_prio = 1 - math.exp(-1.2 * prio / max(thr, 1e-3))
        m = lg.meta.get(u.id, {})
        p_s1 = m.get("p_accept_s1")
        p = p_prio if p_s1 is None else 0.5 * p_prio + 0.5 * p_s1
        if u.hypothesis and u.hypothesis_confidence >= 0.7:
            p = min(0.99, p + 0.05)  # confirm questions are cheap to answer
        return {"p_accept": round(p, 3), "priority": prio, "ask_threshold": round(thr, 3),
                "p_accept_systemone": p_s1}

    def tau(self, lg: Any, u: Any, boundary: bool = False) -> dict:
        c_fa, c_miss = self.cfg.c_fa, self.cfg.c_miss
        tag = lg.meta.get(u.id, {}).get("tag")
        if u.type in GUARDRAIL_TYPES:
            c_miss *= 2  # missing a guardrail is costly
        if tag == "mandatory" and len(lg.asked_ids) < 3:
            c_miss *= 1.5  # requirement guard: ≥3 live questions
        elif len(lg.asked_ids) < LIVE_TARGET and boundary:
            # fewer than 3 live questions so far and the expert just saved / went back to the list: that IS the
            # natural pause (e2e: a judgment question waited at p≈0.56 < τ 0.75 through two saves and expired)
            c_miss *= 3
        return {"tau": round(c_fa / (c_fa + c_miss), 3), "c_fa": c_fa, "c_miss": c_miss}

    def _prefetch_accept(self, sid: str, lg: Any, u: Any) -> None:
        m = lg.meta.setdefault(u.id, {})
        if m.get("p_accept_pending") or "p_accept_s1" in m:
            return
        m["p_accept_pending"] = True

        async def run() -> None:
            q = {"accept": {"type": "score", "instructions": "How likely is a busy expert to welcome and "
                            "answer this question during their work (0..1)?",
                            "criteria": ["unwelcome, breaks flow", "neutral", "welcome, specific and cheap to answer"]}}
            try:
                r = await systemone.decide({"question": u.spoken_question, "type": u.type}, q,
                                           timeout=1.5, fallback=False)
                a = r["answers"].get("accept") or {}
                if "score" in a:
                    n = len(q["accept"]["criteria"])
                    sc = float(a["score"])
                    sc = sc / (n - 1) if n > 1 and sc > 1.0 or (n > 1 and "legend" in a) else sc
                    m["p_accept_s1"] = max(0.0, min(1.0, sc))
                elif a.get("probabilities"):
                    pr = a["probabilities"]
                    m["p_accept_s1"] = float(pr.get("yes", pr.get("likely", 0.5)))
            except Exception:  # noqa: BLE001
                pass

        try:
            asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            pass

    async def evaluate(self, sid: str, now: Optional[float] = None) -> dict:
        now = now if now is not None else deps.now_ms()
        s, c = self.st(sid), self.cfg
        lg = get_ledger(sid)
        u = lg.top_candidate(now)
        if u is None:
            return {"decision": "idle", "reasons": ["no candidate"]}
        self._prefetch_accept(sid, lg, u)
        risk = bool(lg.meta.get(u.id, {}).get("risk"))
        bl = self.blockers(sid, now, bypass=risk)
        if bl:
            e = {"t": now, "unknown_id": u.id, "decision": "blocked", "reasons": bl, "backend": "rules"}
            self._log(sid, e)
            return e
        pp, pa = self.p_pause(sid, now), self.p_accept(lg, u, now)
        tt = self.tau(lg, u, pp["boundary"])
        score = pp["p_pause"] * pa["p_accept"]
        age_s = (now - u.created_t) / 1000
        if risk or score > tt["tau"]:
            ch = "ask_now"
        elif age_s > 35 and score < 0.5 * tt["tau"]:
            ch = "defer"
        else:
            ch = "wait"
        reasons = [f"p_pause {pp['p_pause']:.2f} × p_accept {pa['p_accept']:.2f} = {score:.2f} "
                   f"{'>' if score > tt['tau'] else '≤'} τ {tt['tau']:.2f}",
                   f"priority {pa['priority']:.2f} (threshold {pa['ask_threshold']:.2f})", f"age {age_s:.0f}s"]
        if pp["boundary"]:
            reasons.append("task boundary (save/submit/list)")
        if risk:
            reasons.append("guardrail risk: bypass")
        if u.type in GUARDRAIL_TYPES and not lg.guardrail_asked():
            reasons.append("no guardrail question asked yet")
        entry = {"t": now, "unknown_id": u.id, "decision": ch, "reasons": reasons, "backend": "prism",
                 "score": round(score, 3), **pp, **pa, **tt, "tag": lg.meta.get(u.id, {}).get("tag"),
                 "question": u.spoken_question}
        if ch == "wait" and s.last_reasons and s.last_reasons[0] == "wait" and s.last_reasons[1] == u.id \
                and now - s.last_decide_t < c.recheck_after_wait_ms:
            return entry  # don't flood the inspector with identical waits
        s.last_decide_t = now
        if ch == "ask_now":
            try:
                lg.refresh_question(u)
            except Exception:  # noqa: BLE001
                pass
            lg.mark_asked(u.id)
            s.asks.append(now)
            why = self.why(sid, lg, u, now)
            lg.meta.setdefault(u.id, {})["why"] = why
            entry["why"] = why
            await deps.send(sid, {"type": "ask", "unknown_id": u.id, "text": u.spoken_question, "why": why})
            await self.emit_signals(sid, now, force=True)
            await lg.emit()
        elif ch == "defer":
            u.status = "deferred"
            await lg.emit()
        self._log(sid, entry)
        return entry

    # ---------- "how Claros decided" (contract v2.2) ----------
    def signal_values(self, sid: str, now: float) -> dict:
        s, c = self.st(sid), self.cfg
        silence = 0 if s.speaking else int(min(600_000.0, max(0.0, now - (s.last_speech_t or 0.0))))
        return {"silence_ms": silence, "screen_settled_ms": int(max(0.0, now - s.last_change_t)),
                "typing": bool(s.activity == "typing" and now - s.activity_t < 5000),
                "boundary": s.boundary_kind if s.boundary_kind and now - s.boundary_t < c.boundary_bonus_ms else None}

    def why(self, sid: str, lg: Any, u: Any, now: float) -> dict:
        sig = self.signal_values(sid, now)
        quiet_s = min(sig["silence_ms"], sig["screen_settled_ms"]) / 1000
        b = sig["boundary"]
        when = (f"pause after {'going back to the list' if b == 'list' else b.capitalize()} · {quiet_s:.1f} s quiet"
                if b else f"pause · {quiet_s:.1f} s quiet")
        m = lg.meta.get(u.id, {})
        e = m.get("event")
        k = getattr(e, "kind", None)
        if m.get("synthetic"):
            what = "no stop rule heard yet"
        elif u.type == "limit":
            what = "an amount near a round limit"
        elif k == "hold":
            what = "you put a record on hold"
        elif k == "escalate":
            what = "you sent it for approval"
        elif k == "reject":
            what = "you rejected a record"
        elif k == "undo" or m.get("class") == "slip":
            what = "you undid a change"
        elif m.get("class") == "judgment":
            what = "you changed a pre-filled value"
        else:
            what = "something you did on screen"
        return {"when": when, "signals": sig, "what": what, "scope": u.scope}

    async def emit_signals(self, sid: str, now: Optional[float] = None, force: bool = False) -> Optional[dict]:
        """ws.out `signals` for the live indicator: only on change (≤2/s), plus a 5 s heartbeat."""
        now = now if now is not None else deps.now_ms()
        s, c = self.st(sid), self.cfg
        typing = bool(s.activity == "typing" and now - s.activity_t < 5000)
        screen = "away" if s.activity == "away" else ("changing" if now - s.last_change_t < c.settle_ms else "settled")
        last_ask = s.asks[-1] if s.asks else None
        if last_ask is not None and (now - last_ask < 8000 or (s.agent_mode == "speaking" and now - last_ask < 20_000)):
            g = "asking"
        else:
            bl = [r for r in self.blockers(sid, now) if not r.startswith(("budget", "min gap"))]
            g = "quiet" if bl else "ready"
        cur = (typing, bool(s.speaking), screen, g)
        changed = cur != s.sig_last
        if not force and not (changed and now - s.sig_t >= 500) and not (now - s.sig_t >= 5000):
            return None
        s.sig_last, s.sig_t = cur, now
        msg = {"type": "signals", "typing": typing, "speaking": bool(s.speaking), "screen": screen, "gate": g}
        await deps.send(sid, msg)
        return msg

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
                if getattr(sess, "mode", "capture") in ("capture", "debrief"):
                    try:
                        await self.emit_signals(sid)
                    except Exception:  # noqa: BLE001
                        deps.log.debug("signals failed", exc_info=True)

    def decisions(self, sid: str) -> list[dict]:
        return list(self.st(sid).log)


gate = Gate()

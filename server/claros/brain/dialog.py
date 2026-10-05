"""Dialog layer: the server decides WHAT/WHEN, the ElevenLabs agent speaks it.

Consumes final `utterance` events → intents (rules → systemone) → control actions / ledger acks / debrief + tutor
turns, and pushes `say {id, text, kind, step_id?, lang}` over ws.out. The web client sends `⟦TEXT⟧` as a user
message; the agent's custom LLM (llm_endpoint.relay) returns TEXT verbatim.

Also serves:
  GET /api/dialog/vars?session_id=    → dynamic variables for the agent session (incl. GLM-written workflow_brief)
  GET /api/dialog/lookup?session_id=&q= → ElevenLabs webhook tool `claros_lookup` (steps/guardrails/quotes search)
and pushes throttled `context_update` (≤400 chars) so free-form answers stay grounded.
"""
from __future__ import annotations

import asyncio
import itertools
import re
import time
from typing import Any, Optional

from fastapi import APIRouter

from . import deps, lang as L
from .gate import gate
from .intents import classify_intent, rule_intent
from .ledger import get_ledger

router = APIRouter()

CONFIG = {
    "settle_s": 0.6,           # after a user turn, let the agent's own reaction (speech / skip_turn) start
    "quiet_ms": 700.0,         # required silence (agent + user) before the server speaks
    "floor_timeout_s": 12.0,   # give up waiting for the floor and speak anyway
    "poll_s": 0.1,
    "context_min_interval_s": 4.0,
    "context_max_chars": 400,
    "brief_max_chars": 1500,
    "brief_llm_timeout_s": 8.0,
    "vars_wait_s": 2.5,
}
SAY_KINDS = ("ask", "intervene", "debrief", "teachback", "tutor", "ack")
STEP_MARKER = re.compile(r"\[\[\s*step\s*:\s*([\w\-]+)\s*\]\]")
HIDDEN = re.compile(r"⟦[^⟧]*⟧")


# ---------------- say protocol ----------------

_ids = itertools.count(1)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", STEP_MARKER.sub("", HIDDEN.sub("", text or ""))).strip()


def split_segments(text: str) -> list[tuple[Optional[str], str]]:
    """'[[step:a]] Open it. [[step:b]] Check.' → [('a', 'Open it.'), ('b', 'Check.')] (markers never spoken)."""
    out: list[tuple[Optional[str], str]] = []
    pos, cur = 0, None
    for m in STEP_MARKER.finditer(text or ""):
        seg = _clean(text[pos:m.start()])
        if seg:
            out.append((cur, seg))
        cur, pos = m.group(1), m.end()
    seg = _clean((text or "")[pos:])
    if seg:
        out.append((cur, seg))
    return out


async def say(sid: str, text: str, kind: str = "ack", *, id: Optional[str] = None,
              step_id: Optional[str] = None, lang: Optional[str] = None) -> Optional[dict]:
    """Push one `say` to the client (→ ⟦TEXT⟧ → the agent speaks TEXT verbatim)."""
    t = _clean(text)
    if not sid or not t:
        return None
    kind = kind if kind in SAY_KINDS else "ack"
    sid_ = id or f"{kind}-{next(_ids)}"
    msg: dict[str, Any] = {"type": "say", "id": sid_, "text": t, "kind": kind,
                           "lang": (lang or deps.session_lang(sid))[:2]}
    if step_id:
        msg["step_id"] = step_id
    deps.store_log(sid, "dialog.say", msg)
    await deps.send(sid, msg)
    return msg


async def say_script(sid: str, text: str, kind: str = "debrief", lang: Optional[str] = None) -> list[dict]:
    """Text with [[step:id]] markers → one `say` per segment carrying step_id (kind teachback)."""
    segs = split_segments(text)
    if any(s for s, _ in segs):
        kind = "teachback"
    base = f"{kind}-{next(_ids)}"
    out = []
    for i, (step_id, seg) in enumerate(segs):
        m = await say(sid, seg, kind, id=f"{base}-{i}" if len(segs) > 1 else base, step_id=step_id, lang=lang)
        if m:
            out.append(m)
    return out


async def wait_floor(sid: str, timeout: Optional[float] = None) -> bool:
    """Wait until the agent is listening, the user is not speaking, and both were quiet for quiet_ms."""
    deadline = time.monotonic() + (CONFIG["floor_timeout_s"] if timeout is None else timeout)
    while True:
        s = gate.st(sid)
        quiet = deps.now_ms() - (s.last_speech_t or 0) >= CONFIG["quiet_ms"]
        if s.agent_mode != "speaking" and not s.speaking and quiet:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(CONFIG["poll_s"])


# ---------------- dialog loop ----------------

_seen: dict[str, set] = {}
_locks: dict[str, asyncio.Lock] = {}
_debrief_kicked: set[str] = set()


def _lock(sid: str) -> asyncio.Lock:
    lk = _locks.get(sid)
    if lk is None:
        lk = _locks[sid] = asyncio.Lock()
    return lk


async def _speak(sid: str, text: Optional[str], kind: str, *, settle: bool = True) -> list[dict]:
    if not text or not _clean(text):
        return []
    if settle and CONFIG["settle_s"] > 0:
        await asyncio.sleep(CONFIG["settle_s"])
    await wait_floor(sid)
    return await say_script(sid, text, kind)


async def on_utterance(sid: str, p: Any) -> Optional[dict]:
    """Final user utterance → intent → control / ack / debrief / tutor. Returns a summary (tests)."""
    if not isinstance(p, dict) or p.get("role", "user") != "user":
        return None
    text = (p.get("text") or "").strip()
    if not text or HIDDEN.search(text):
        return None
    eid = p.get("event_id") or f"{p.get('t_start')}:{text}"
    seen = _seen.setdefault(sid, set())
    if eid in seen:
        return None
    seen.add(eid)
    async with _lock(sid):
        return await handle_turn(sid, text)


async def handle_turn(sid: str, text: str) -> dict:
    sess = deps.get_session(sid)
    mode = getattr(sess, "mode", "capture") or "capture"
    lang = L.detect_lang(text, None) or deps.session_lang(sid)
    lg = get_ledger(sid)
    pending = lg.last_asked(60_000)
    if mode in ("learn", "request"):
        priv = rule_intent("capture", text)
        if priv in ("off_record", "strike_that"):
            return await _control_turn(sid, mode, priv, lang)
    if getattr(sess, "off_record", False) or lg.off_record:
        return {"intent": "off_record_silence"}
    if mode == "learn":
        # an open nudge card: "the second one" / "capex" / "не знаю" answers it (feedback is said by the tutor)
        on_voice = deps.knowledge_attr("nudges.on_voice")
        if on_voice:
            try:
                r = await on_voice(sid, text)
            except Exception:  # noqa: BLE001
                deps.log.exception("dialog: nudge answer failed")
                r = None
            if r:
                return {"intent": "answer_nudge", "outcome": r.get("outcome")}
    pq = pending.spoken_question if pending else None
    if mode == "debrief":
        cq = deps.knowledge_attr("debrief.current_question")
        pq = (cq(sess) if cq else None) or pq
    ir = await classify_intent(mode, text, {"pending_question": pq})
    intent = ir.intent
    deps.store_log(sid, "dialog.intent", {"text": text, "mode": mode, **ir.model_dump()})
    if intent in ("off_record", "strike_that", "end_session"):
        return await _control_turn(sid, mode, intent, lang)
    if mode in ("learn", "request"):
        return await _learn_turn(sid, sess, intent, text)
    if mode == "debrief":
        return await _debrief_turn(sid, sess, intent, text)
    return await _capture_turn(sid, intent, text, lang, pending)


async def _control_turn(sid: str, mode: str, intent: str, lang: str) -> dict:
    from .llm_endpoint import _control
    action = {"off_record": "off_record_on", "strike_that": "strike_that", "end_session": "end_task"}[intent]
    await _control(sid, action)
    key = {"off_record": "off_record", "strike_that": "struck"}.get(intent) or \
        ("to_debrief" if mode == "capture" else "end")
    said = await _speak(sid, L.phrase(key, lang), "ack", settle=False)
    return {"intent": intent, "action": action, "said": said}


FILLER = re.compile(r"^\W*((mm+|hm+|uh+|um+|er+|ah+|okay|ok|yeah|so|well|hmm+|ммм?|э+|ну)\W*){1,3}$", re.I)


async def _capture_turn(sid: str, intent: str, text: str, lang: str, pending: Any) -> dict:
    lg = get_ledger(sid)
    if intent == "answer" and FILLER.match(text or ""):
        return {"intent": "narration"}  # thinking noise, not the answer (live: "Mm." got "Got it, thanks")
    if intent in ("answer", "correction", "confirm"):
        target = pending or lg.last_asked(180_000)
        if target is None:
            return {"intent": "narration"}  # talking about the screen, not to Claros
        await asyncio.sleep(0)  # let the ledger's own resolution run first
        if target.status != "answered":
            await lg.record_answer(target.id, text)
        key = {"answer": "ack", "correction": "ack_correction", "confirm": "ack_confirm"}[intent]
        return {"intent": intent, "said": await _speak(sid, L.phrase(key, lang), "ack")}
    if intent == "not_now":
        gate.snooze(sid)
        if pending:
            pending.status = "open"
            pending.expires_t = deps.now_ms() + 300_000 + 45_000
        return {"intent": intent, "said": await _speak(sid, L.phrase("later", lang), "ack", settle=False)}
    if intent == "question_to_claros":
        return {"intent": intent, "said": await _speak(sid, await answer_from_screen(sid, text, lang), "ack")}
    return {"intent": intent}  # narration → silence


async def answer_from_screen(sid: str, question: str, lang: str) -> str:
    """Relay has no free-talking agent: a question to Claros mid-capture gets a short answer grounded in the
    screen and what was said so far, or an honest "not sure yet" (never a guess about the expert's own rules)."""
    sys_ = (f"You are Claros, an apprentice watching an expert work. Answer their question in at most 25 words, in "
            f"{L.LANG_NAMES.get(lang, 'English')}, using only the context. If the context doesn't answer it, or it "
            f"asks about their own rules or reasons, reply exactly: UNSURE")
    try:
        out = await asyncio.wait_for(deps.llm_chat(
            [{"role": "system", "content": sys_},
             {"role": "user", "content": f"Context: {context_text(sid) or 'none'}\nQuestion: {question}"}]), 4.0)
    except Exception:  # noqa: BLE001
        out = None
    out = (out or "").strip()
    return L.phrase("not_sure", lang) if not out or "UNSURE" in out else L.clamp_words(out, 30)


async def _debrief_turn(sid: str, sess: Any, intent: str, text: str) -> dict:
    # Relay: nothing answers on its own, so a reply that only sounds addressed to Claros ("if you don't
    # understand…") is the answer. A real question gets the pending question again instead of silence.
    if intent == "question_to_claros" and text.rstrip().endswith("?"):
        cq = deps.knowledge_attr("debrief.current_question")
        return {"intent": intent, "said": await _speak(sid, cq(sess) if cq else None, "debrief")}
    ans = deps.knowledge_attr("debrief.handle_debrief_answer")
    nxt = deps.knowledge_attr("debrief.next_debrief_utterance")
    if not ans and not nxt:
        return {"intent": intent}
    _debrief_kicked.add(sid)
    try:
        if ans:
            it = "answer" if intent in ("narration", "question_to_claros") else intent
            try:
                out = await ans(sess, text, it)
            except TypeError:
                out = await ans(sess, text)
        else:
            out = await nxt(sess)
    except Exception:  # noqa: BLE001
        deps.log.exception("dialog: debrief turn failed")
        return {"intent": intent}
    return {"intent": intent, "said": await _speak(sid, out, "debrief")}


async def kick_debrief(sid: str) -> list[dict]:
    """Debrief started (hello mode=debrief): push the first planned question (once per session)."""
    if sid in _debrief_kicked:
        return []
    nxt = deps.knowledge_attr("debrief.next_debrief_utterance")
    if not nxt:
        return []
    _debrief_kicked.add(sid)
    async with _lock(sid):
        try:
            out = await nxt(deps.get_session(sid))
        except Exception:  # noqa: BLE001
            deps.log.exception("dialog: debrief kickoff failed")
            return []
        return await _speak(sid, out, "debrief", settle=False)


async def _learn_turn(sid: str, sess: Any, intent: str, text: str) -> dict:
    if intent == "off_topic" and "?" not in (text or ""):
        return {"intent": intent}  # chatter: stay quiet. A real question goes to the tutor (it has the map)
    fn = deps.knowledge_attr("tutor.handle_intent")
    if not fn:
        return {"intent": intent}
    try:
        r = await deps.maybe_await(fn(sess, intent, text))
    except Exception:  # noqa: BLE001
        deps.log.exception("dialog: tutor turn failed")
        return {"intent": intent}
    out = r if isinstance(r, str) else (r or {}).get("text") if isinstance(r, dict) else None
    return {"intent": intent, "said": await _speak(sid, out, "tutor")}


# ---------------- live context for the hosted LLM ----------------

_screen: dict[str, dict] = {}
_step: dict[str, str] = {}
_open: dict[str, int] = {}
_ctx_last: dict[str, tuple[float, str]] = {}
_ctx_timer: dict[str, asyncio.Task] = {}


def _as_dict(p: Any) -> dict:
    if isinstance(p, dict):
        return p
    if hasattr(p, "model_dump"):
        try:
            return p.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _load_map(workflow_id: Optional[str]) -> Any:
    if not workflow_id:
        return None
    fn = deps.knowledge_attr("common.load_map")
    try:
        return fn(workflow_id) if fn else None
    except Exception:  # noqa: BLE001
        return None


def _loc(wm: Any, lang: str, text: Optional[str]) -> Optional[str]:
    """Map text in the session language when the tutor has translated it (cache only, never blocks)."""
    try:
        from claros.knowledge import tutor
        return tutor.loc(wm, (lang or "en")[:2], text)
    except Exception:  # noqa: BLE001
        return text


def _steps(wm: Any) -> list:
    return sorted(getattr(wm, "steps", None) or [], key=lambda s: getattr(s, "order", 0))


def context_text(sid: str) -> str:
    parts: list[str] = []
    sc = _screen.get(sid) or {}
    where = " › ".join(str(x) for x in (sc.get("app"), sc.get("view")) if x)
    ent = " ".join(str(x) for x in (sc.get("entity_type"), sc.get("entity_id")) if x)
    if where or ent:
        parts.append("Screen: " + " · ".join(x for x in (where, ent, sc.get("status")) if x))
    sess = deps.get_session(sid)
    step_id = _step.get(sid)
    wm = _load_map(getattr(sess, "workflow_id", None)) if step_id else None
    if step_id:
        steps = _steps(wm)
        st = next((s for s in steps if s.id == step_id), None)
        title = _loc(wm, deps.session_lang(sid), st.title) if st else None
        parts.append(f"Current step {st.order}/{len(steps)}: {title}" if st else f"Current step: {step_id}")
    mode = getattr(sess, "mode", "capture")
    if mode in ("capture", "debrief"):
        n = _open.get(sid)
        if n is None:
            try:
                n = int(get_ledger(sid).snapshot().get("open", 0))
            except Exception:  # noqa: BLE001
                n = 0
        parts.append(f"Open questions: {n}")
    if mode == "learn" and step_id:
        try:
            from claros.knowledge import tutor
            ts = tutor.get_state(sid, create=False)
            if ts is not None:
                p = tutor.bkt_p(ts, step_id)
                parts.append(f"Learner mastery here: {'low' if p < 0.4 else 'medium' if p < 0.75 else 'high'}")
        except Exception:  # noqa: BLE001
            pass
    txt = "Claros live context — " + " | ".join(parts) if parts else ""
    n = CONFIG["context_max_chars"]
    return txt if len(txt) <= n else txt[: n - 1] + "…"


async def push_context(sid: str, force: bool = False) -> Optional[str]:
    """Throttled `context_update` (only when changed; ≥ context_min_interval_s apart)."""
    txt = context_text(sid)
    if not txt:
        return None
    last_t, last_txt = _ctx_last.get(sid, (0.0, ""))
    if txt == last_txt and not force:
        return None
    wait = CONFIG["context_min_interval_s"] - (time.monotonic() - last_t)
    if wait > 0 and not force:
        if sid not in _ctx_timer or _ctx_timer[sid].done():
            async def later() -> None:
                await asyncio.sleep(wait)
                await push_context(sid)
            try:
                _ctx_timer[sid] = asyncio.get_running_loop().create_task(later())
            except RuntimeError:
                pass
        return None
    _ctx_last[sid] = (time.monotonic(), txt)
    await deps.send(sid, {"type": "context_update", "text": txt})
    return txt


async def on_screen_state(sid: str, p: Any) -> None:
    d = _as_dict(p)
    if d:
        _screen[sid] = {k: d.get(k) for k in ("app", "view", "entity_type", "entity_id", "status")}
        await push_context(sid)


async def on_ws_out(sid: str, p: Any) -> None:
    d = _as_dict(p)
    typ = d.get("type")
    if typ == "highlight_step" and d.get("step_id"):
        if _step.get(sid) != d["step_id"]:
            _step[sid] = d["step_id"]
            await push_context(sid)
    elif typ == "ledger" and isinstance(d.get("open"), int):
        if _open.get(sid) != d["open"]:
            _open[sid] = d["open"]
            await push_context(sid)


# ---------------- workflow brief + session variables ----------------

_brief_cache: dict[tuple, str] = {}
_brief_tasks: dict[tuple, asyncio.Task] = {}


def _names(wm: Any) -> dict[str, str]:
    return {e.id: e.name for e in (getattr(wm, "experts", None) or [])}


def _who(wm: Any, ids: list[str], quote_ids: list[str] = ()) -> str:
    names = _names(wm)
    out = [names.get(i, i) for i in ids if i]
    qs = {q.id: q.speaker for q in (getattr(wm, "quotes", None) or [])}
    out += [qs[q] for q in quote_ids if q in qs]
    out = list(dict.fromkeys(x.split()[0] for x in out if x))
    return ", ".join(out[:3])


def template_brief(wm: Any) -> str:
    lines = [f"Workflow: {wm.name}."]
    for s in _steps(wm):
        line = f"{s.order}. {s.title}"
        if s.decision:
            line += f" — decides: {s.decision.description}"
            who = _who(wm, [], s.decision.reason_quote_ids)
            if who:
                line += f" ({who})"
        if s.conflict:
            line += " [experts differ]"
        elif not s.approved:
            line += " [unconfirmed]"
        lines.append(line)
    if getattr(wm, "guardrails", None):
        lines.append("Rules:")
        for g in wm.guardrails:
            who = _who(wm, g.experts, g.quote_ids)
            lines.append(f"- {g.text}" + (f" (per {who})" if who else "") + ("" if g.approved else " [unconfirmed]"))
    txt = "\n".join(lines)
    n = CONFIG["brief_max_chars"]
    return txt if len(txt) <= n else txt[: n - 1] + "…"


async def _llm_brief(wm: Any, lang: str) -> str:
    base = template_brief(wm)
    try:
        out = await asyncio.wait_for(deps.llm_chat([
            {"role": "system", "content":
                f"Rewrite this captured workflow as a compact brief for a voice tutor, in "
                f"{L.LANG_NAMES.get(lang, 'English')}. Keep every step (numbered), every rule with its expert "
                f"attribution, and every [unconfirmed]/[experts differ] flag. Add nothing that is not in the input. "
                f"Plain text, at most {CONFIG['brief_max_chars'] - 100} characters."},
            {"role": "user", "content": base}], model_role="smart"), CONFIG["brief_llm_timeout_s"])
    except Exception:  # noqa: BLE001
        out = None
    out = (out or "").strip()
    if not out:
        return base
    n = CONFIG["brief_max_chars"]
    return out if len(out) <= n else out[: n - 1] + "…"


def _brief_key(wm: Any, lang: str) -> tuple:
    return (wm.workflow_id, getattr(wm, "version", 1), lang)


def prefetch_brief(workflow_id: Optional[str], lang: str) -> Optional[asyncio.Task]:
    wm = _load_map(workflow_id)
    if wm is None:
        return None
    key = _brief_key(wm, lang)
    if key in _brief_cache:
        return None
    t = _brief_tasks.get(key)
    if t and not t.done():
        return t

    async def run() -> str:
        b = await _llm_brief(wm, lang)
        _brief_cache[key] = b
        return b
    try:
        t = _brief_tasks[key] = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return None
    return t


async def workflow_brief(workflow_id: Optional[str], lang: str, wait_s: Optional[float] = None) -> str:
    wm = _load_map(workflow_id)
    if wm is None:
        return ""
    key = _brief_key(wm, lang)
    if key in _brief_cache:
        return _brief_cache[key]
    t = prefetch_brief(workflow_id, lang)
    if t is not None:
        try:
            return await asyncio.wait_for(asyncio.shield(t), CONFIG["vars_wait_s"] if wait_s is None else wait_s)
        except Exception:  # noqa: BLE001
            pass
    return template_brief(wm)


@router.get("/api/dialog/vars")
async def vars_ep(session_id: str) -> dict:
    sess = deps.get_session(session_id)
    mode = getattr(sess, "mode", "capture") or "capture"
    lang = deps.session_lang(session_id)
    u = getattr(sess, "user", None)
    name = (getattr(u, "name", None) or (u.get("name") if isinstance(u, dict) else None) or "there") if u else "there"
    wid = getattr(sess, "workflow_id", None)
    wm = _load_map(wid)
    brief = await workflow_brief(wid, lang) if mode in ("learn", "debrief", "request") else ""
    return {"session_id": session_id, "mode": mode, "user_name": name, "lang": lang,
            "workflow_name": getattr(wm, "name", None) or "this task",
            "workflow_brief": brief or "none yet"}


# ---------------- claros_lookup webhook tool ----------------

def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2}


def search_map(wm: Any, q: str, k: int = 3) -> list[dict]:
    qt = _toks(q)
    if not qt or wm is None:
        return []
    quotes = {x.id: x for x in (getattr(wm, "quotes", None) or [])}
    items: list[tuple[float, dict]] = []

    def score(text: str) -> float:
        tt = _toks(text)
        return len(qt & tt) / max(1, len(qt)) + (0.5 if q.lower().strip() in (text or "").lower() else 0.0)

    for g in getattr(wm, "guardrails", None) or []:
        qtext = " ".join(quotes[i].text for i in g.quote_ids if i in quotes)
        sc = score(g.text + " " + qtext) + 0.15
        items.append((sc, {"kind": "guardrail", "id": g.id, "text": g.text, "expert": _who(wm, g.experts, g.quote_ids),
                           "confirmed": g.approved}))
    for s in _steps(wm):
        d = s.decision
        txt = s.title + (f" — {d.description}" if d else "") + (f" (if: {d.counterfactual})" if d and d.counterfactual else "")
        qtext = " ".join(quotes[i].text for i in (d.reason_quote_ids if d else []) if i in quotes)
        items.append((score(txt + " " + qtext), {"kind": "step", "id": s.id, "order": s.order, "text": txt,
                                                  "expert": _who(wm, s.experts, d.reason_quote_ids if d else []),
                                                  "confirmed": s.approved, "conflict": s.conflict}))
    for x in quotes.values():
        items.append((score(x.text) - 0.05, {"kind": "quote", "id": x.id, "text": x.text, "expert": x.speaker}))
    best = [it for sc, it in sorted(items, key=lambda z: -z[0]) if sc >= 0.34][:k]
    return best


@router.get("/api/dialog/lookup")
async def lookup_ep(q: str = "", session_id: str = "") -> dict:
    sess = deps.get_session(session_id or None)
    wid = getattr(sess, "workflow_id", None)
    wm = _load_map(wid)
    if wm is None:
        return {"found": False, "answer": "No workflow map for this session yet. Say you will ask the expert.",
                "items": []}
    hits = search_map(wm, q)
    if not hits:
        return {"found": False, "workflow": wm.name, "items": [],
                "answer": "Not in the captured map. Do not guess; say you will ask the expert."}
    h = hits[0]
    lang = deps.session_lang(session_id) if session_id else "en"
    if h["kind"] in ("guardrail", "quote"):
        h = {**h, "original": h["text"], "text": _loc(wm, lang, h["text"])}
        hits[0] = h
    who = f" ({h['expert']})" if h.get("expert") else ""
    flag = "" if h.get("confirmed", True) else " [not yet confirmed by the expert]"
    if h.get("conflict"):
        flag = f" [experts differ: {h['conflict']}]"
    ans = (h["text"] + who + flag)[:300]
    return {"found": True, "workflow": wm.name, "answer": ans, "items": hits}


# ---------------- wiring ----------------

async def on_hello(sid: str, p: Any) -> None:
    if not isinstance(p, dict):
        return
    mode = p.get("mode")
    if mode in ("learn", "debrief", "request"):
        prefetch_brief(p.get("workflow_id") or getattr(deps.get_session(sid), "workflow_id", None),
                       (p.get("lang") or deps.session_lang(sid))[:2])
    if mode == "debrief":
        await asyncio.sleep(0)  # knowledge.debrief.on_hello creates the state first
        await kick_debrief(sid)
    elif mode in ("capture", "learn") and sid not in _greeted:
        # "Claros says hi": the agent has no first message (the server owns every line); queued client-side
        # until the voice session connects. Once per session, never on reconnects.
        _greeted.add(sid)
        from .lang import phrase
        lang = (p.get("lang") or deps.session_lang(sid))[:2]
        await say(sid, phrase("greet_capture" if mode == "capture" else "greet_learn", lang), "ack",
                  id=f"greet-{sid}")


_greeted: set[str] = set()


def reset(sid: Optional[str] = None) -> None:
    _greeted.clear() if sid is None else _greeted.discard(sid)
    for d_ in (_seen, _locks, _screen, _step, _open, _ctx_last):
        if sid is None:
            d_.clear()
        else:
            d_.pop(sid, None)
    if sid is None:
        _debrief_kicked.clear()
    else:
        _debrief_kicked.discard(sid)


def register(bus: Any) -> None:
    bus.subscribe("utterance", on_utterance)
    bus.subscribe("ws.in.utterance", on_utterance)  # deduped by event_id
    bus.subscribe("ws.in.hello", on_hello)
    bus.subscribe("screen.state", on_screen_state)
    bus.subscribe("ws.out", on_ws_out)
    bus.subscribe("session.ended", lambda sid, p: reset(sid))


__all__ = ["router", "register", "say", "say_script", "on_utterance", "kick_debrief",
           "push_context", "context_text", "workflow_brief", "template_brief", "search_map", "split_segments"]

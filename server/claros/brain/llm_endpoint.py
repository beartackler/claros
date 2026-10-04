"""OpenAI-compatible custom-LLM endpoint for ElevenLabs: POST /llm/v1/chat/completions (SSE).

OPTIONAL dialog mode (CLAROS_DIALOG_MODE=custom); default is hosted (see brain/dialog.py).
Routing of the latest user message:
  ⟦say:ID|T⟧     → ledger/prewritten/intervention text for ID, else T verbatim
  ⟦ask:U⟧        → pre-written Unknown.spoken_question verbatim
  ⟦intervene:G⟧  → pre-written intervention text verbatim
  otherwise      → intent (mode-specific) → deterministic handler / skip_turn tool call / LLM
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import deps, lang as L
from .gate import gate
from .intents import classify_intent
from .ledger import get_ledger

router = APIRouter()

# Knowledge agent may fill: interventions[session_id][guardrail_id] = text
interventions: dict[str, dict[str, str]] = {}

def register_intervention(key: str, text: str, session_id: Optional[str] = None) -> None:
    """Called by knowledge: pre-written text streamed verbatim for ⟦intervene:key⟧."""
    interventions.setdefault(session_id or "*", {})[key] = text


register_text = register_intervention

# ⟦ask:ID⟧ or ⟦ask:ID|fallback text⟧ (same for intervene)
ASK_RE = re.compile(r"[⟦\[]{1,2}\s*ask\s*:\s*([\w\-]+)\s*(?:\|\s*([^⟧\]]*?))?\s*[⟧\]]{1,2}", re.S)
SAY_RE = re.compile(r"[⟦\[]{1,2}\s*say\s*:\s*([\w\-]+)\s*(?:\|\s*([^⟧\]]*?))?\s*[⟧\]]{1,2}", re.S)
INTERVENE_RE = re.compile(r"[⟦\[]{1,2}\s*intervene\s*:\s*([\w\-]+)\s*(?:\|\s*([^⟧\]]*?))?\s*[⟧\]]{1,2}", re.S)
SESSION_LINE_RE = re.compile(r"claros-session\s*:\s*([\w\-]+)", re.I)
BUFFER_AFTER_S = 0.9


# ---------------- helpers ----------------

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def session_id_from(body: dict) -> Optional[str]:
    extra = body.get("elevenlabs_extra_body") or {}
    if isinstance(extra, dict) and extra.get("session_id"):
        return str(extra["session_id"])
    for m in body.get("messages", []):
        if m.get("role") == "system":
            mm = SESSION_LINE_RE.search(_text(m.get("content")))
            if mm:
                return mm.group(1)
    return None


def last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            return _text(m.get("content")).strip()
    return ""


def find_skip_tool(body: dict) -> Optional[str]:
    names = []
    for t in body.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        name = (fn or {}).get("name") or (t.get("name") if isinstance(t, dict) else None)
        if name:
            names.append(name)
    for n in names:
        if n == "skip_turn":
            return n
    for n in names:
        if "skip" in n.lower():
            return n
    return None


class Reply:
    """Normalized handler result."""

    def __init__(self, text: Optional[str] = None, stream: Optional[AsyncIterator[str]] = None,
                 tool: Optional[str] = None, tool_args: Optional[dict] = None, silent: bool = False,
                 buffer_lang: Optional[str] = None) -> None:
        self.text, self.stream, self.tool, self.tool_args = text, stream, tool, tool_args or {}
        self.silent, self.buffer_lang = silent, buffer_lang
        self.sid: Optional[str] = None


# Teach-back markers [[step:id]] are never spoken: stripped, and a ws.out highlight_step is sent
# when the stream reaches the marker (paced at ~speech rate so the highlight roughly follows the voice).
STEP_MARKER = re.compile(r"\[\[\s*step\s*:\s*([\w\-]+)\s*\]\]")
SPEECH_S_PER_WORD = 0.32
MAX_SEGMENT_DELAY_S = 6.0

# brain-local pre-written texts (phase transitions etc.): prewritten[sid][id] = text
prewritten: dict[str, dict[str, str]] = {}


def split_steps(text: str) -> list[tuple[Optional[str], str]]:
    parts: list[tuple[Optional[str], str]] = []
    pos, cur = 0, None
    for m in STEP_MARKER.finditer(text):
        seg = text[pos:m.start()]
        if seg.strip() or cur:
            parts.append((cur, seg))
        cur, pos = m.group(1), m.end()
    parts.append((cur, text[pos:]))
    return [(sid_, re.sub(r"\s+", " ", t_)) for sid_, t_ in parts if t_.strip() or sid_]


async def _paced(text: str, sid: Optional[str]) -> AsyncIterator[str]:
    segs = split_steps(text)
    for i, (step_id, seg) in enumerate(segs):
        if step_id and sid:
            await deps.send(sid, {"type": "highlight_step", "step_id": step_id})
        out = seg.strip()
        if out:
            yield (out + " ") if i < len(segs) - 1 else out
            if i < len(segs) - 1 and SPEECH_S_PER_WORD > 0:
                await asyncio.sleep(min(MAX_SEGMENT_DELAY_S, len(out.split()) * SPEECH_S_PER_WORD))


def _chunk(cid: str, model: str, delta: dict, finish: Optional[str] = None) -> str:
    return "data: " + json.dumps({
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }, ensure_ascii=False) + "\n\n"


async def _with_buffer(it: AsyncIterator[str], lang: str) -> AsyncIterator[str]:
    """Yield a short buffer phrase if the first token takes > BUFFER_AFTER_S."""
    q: asyncio.Queue = asyncio.Queue()
    END = object()

    async def pump() -> None:
        try:
            async for x in it:
                await q.put(x)
        except Exception:  # noqa: BLE001
            deps.log.warning("llm stream failed", exc_info=deps.log.isEnabledFor(10))
        finally:
            await q.put(END)

    task = asyncio.create_task(pump())
    got_any = False
    try:
        try:
            first = await asyncio.wait_for(q.get(), BUFFER_AFTER_S)
        except asyncio.TimeoutError:
            yield L.phrase("buffer", lang) + " "
            first = await q.get()
        while first is not END:
            got_any = True
            yield first
            first = await q.get()
        if not got_any:
            yield L.phrase("no_llm", lang)
    finally:
        task.cancel()


async def sse(reply: Reply, model: str) -> AsyncIterator[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    yield _chunk(cid, model, {"role": "assistant", "content": ""})
    if reply.tool:
        yield _chunk(cid, model, {"tool_calls": [{"index": 0, "id": f"call_{uuid.uuid4().hex[:12]}",
                                                  "type": "function",
                                                  "function": {"name": reply.tool,
                                                               "arguments": json.dumps(reply.tool_args)}}]})
        yield _chunk(cid, model, {}, "tool_calls")
    else:
        if reply.text:
            if STEP_MARKER.search(reply.text):
                async for piece in _paced(reply.text, reply.sid):
                    yield _chunk(cid, model, {"content": piece})
            else:
                yield _chunk(cid, model, {"content": reply.text})
        if reply.stream is not None:
            src = _with_buffer(reply.stream, reply.buffer_lang) if reply.buffer_lang else reply.stream
            async for piece in src:
                if piece:
                    yield _chunk(cid, model, {"content": STEP_MARKER.sub("", piece)})
        yield _chunk(cid, model, {}, "stop")
    yield "data: [DONE]\n\n"


async def _collect(reply: Reply) -> str:
    out = ""
    if reply.text:
        async for p in _paced(reply.text, reply.sid):
            out += p
    if reply.stream is not None:
        async for p in reply.stream:
            out += p
    return out


# ---------------- routing ----------------

def _reply_lang(sid: Optional[str], text: str) -> str:
    base = deps.session_lang(sid)
    det = L.detect_lang(text, None)
    if det and det != base:
        try:
            s = deps.get_session(sid)
            if hasattr(s, "extra") and isinstance(s.extra, dict):
                s.extra["reply_lang"] = det
        except Exception:  # noqa: BLE001
            pass
        return det
    return base


def _skip(body: dict) -> Reply:
    name = find_skip_tool(body)
    return Reply(tool=name) if name else Reply(text="", silent=True)


async def _intervention_text(sid: Optional[str], gid: str) -> Optional[str]:
    t = interventions.get(sid or "", {}).get(gid) or interventions.get("*", {}).get(gid)
    if t:
        return t
    gp = deps.knowledge_attr("_deps.get_prewritten")
    if gp:
        try:
            t = gp(gid, sid)
            if t:
                return t
        except Exception:  # noqa: BLE001
            pass
    for path in ("get_intervention", "guardrails.get_intervention", "tutor.get_intervention"):
        fn = deps.knowledge_attr(path)
        if fn:
            try:
                r = await deps.maybe_await(fn(sid, gid))
                if r:
                    return r if isinstance(r, str) else getattr(r, "text", None) or r.get("text")
            except Exception:  # noqa: BLE001
                deps.log.debug("get_intervention failed", exc_info=True)
    return None


def _llm_stream(messages: list[dict], lang: str) -> Reply:
    return Reply(stream=deps.llm_stream(messages, model_role="fast"), buffer_lang=lang)


def _hypothesis_messages(sid: Optional[str], text: str, lang: str, body: dict) -> list[dict]:
    lg = get_ledger(sid) if sid else None
    ctx = {}
    if lg:
        ctx["recent_events"] = [e.summary for e in lg.events[-6:]]
        ctx["known"] = [{"q": u.spoken_question, "a": u.resolution} for u in lg.unknowns.values()
                        if u.status in ("answered", "resolved")][-6:]
    sys = (f"You are Claros, an apprentice learning an expert's screen work. Answer in {L.LANG_NAMES.get(lang, 'English')}, "
           "at most 2 short spoken sentences. Frame what you believe as a hypothesis ('I think…, is that right?'); "
           "never invent company rules. Context: " + json.dumps(ctx, ensure_ascii=False))
    hist = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")][-6:]
    return [{"role": "system", "content": sys}] + [{"role": m["role"], "content": _text(m.get("content"))}
                                                     for m in hist[:-1]] + [{"role": "user", "content": text}]


async def route(body: dict) -> Reply:
    sid = session_id_from(body)
    text = last_user_text(body)
    sess = deps.get_session(sid)
    mode = getattr(sess, "mode", "capture") or "capture"

    m = ASK_RE.search(text) or SAY_RE.search(text)
    if m:
        u = get_ledger(sid).unknowns.get(m.group(1)) if sid else None
        if u and u.spoken_question:
            get_ledger(sid).mark_asked(u.id)
            return Reply(text=u.spoken_question)
        t = _prewritten_ask(sid, m.group(1))
        if t:
            return Reply(text=t)
        if m.re is SAY_RE:
            t = await _intervention_text(sid, m.group(1))
            if t:
                return Reply(text=t)
        suffix = (m.group(2) or "").strip()
        return Reply(text=suffix) if suffix else _skip(body)
    m = INTERVENE_RE.search(text)
    if m:
        t = await _intervention_text(sid, m.group(1)) or (m.group(2) or "").strip()
        return Reply(text=t) if t else _skip(body)

    lang = _reply_lang(sid, text)
    lg = get_ledger(sid) if sid else None
    pending = lg.last_asked(60_000) if lg else None
    ctx = {"pending_question": pending.spoken_question if pending else None,
           "last_agent_utterance": next((_text(x.get("content")) for x in reversed(body.get("messages", []))
                                         if x.get("role") == "assistant"), None)}
    ir = await classify_intent(mode, text, ctx)
    deps.store_log(sid or "-", "intent", {"text": text, **ir.model_dump()})
    intent = ir.intent

    if mode in ("learn", "request"):
        # privacy controls work for learners too ("не записывай" while a learner shares their screen)
        from .intents import rule_intent
        priv = rule_intent("capture", text)
        if priv == "off_record":
            await _control(sid, "off_record_on")
            return Reply(text=L.phrase("off_record", lang))
        return await _learner(sid, sess, intent, text, lang, body)
    if mode == "debrief" and intent not in ("off_record", "strike_that", "end_session", "question_to_claros"):
        r = await _debrief(sess, intent, text, body)
        if r is not None:
            return r

    if intent in ("answer", "correction", "confirm") and mode == "capture" and lg and \
            not (pending or lg.last_asked(180_000)):
        # nothing was asked: "that's wrong" / "same amount as December" is the expert talking about the screen,
        # not to Claros — stay silent instead of "Got it, corrected."
        deps.store_log(sid or "-", "intent.override", {"text": text, "from": intent, "to": "narration"})
        intent = "narration"
    if intent == "narration":
        return _skip(body)
    if intent in ("answer", "correction", "confirm"):
        if lg:
            target = pending or lg.last_asked(180_000)
            if target:
                asyncio.create_task(lg.record_answer(target.id, text))
        key = {"answer": "ack", "correction": "ack_correction", "confirm": "ack_confirm"}[intent]
        return Reply(text=L.phrase(key, lang))
    if intent == "not_now":
        if sid:
            gate.snooze(sid)
            if pending:
                pending.status = "open"
                pending.expires_t = deps.now_ms() + 300_000 + 45_000
        return Reply(text=L.phrase("later", lang))
    if intent == "off_record":
        await _control(sid, "off_record_on")
        return Reply(text=L.phrase("off_record", lang))
    if intent == "strike_that":
        await _control(sid, "strike_that")
        return Reply(text=L.phrase("struck", lang))
    if intent == "end_session":
        await _control(sid, "end_task")
        if mode == "capture":
            return Reply(text=L.phrase("to_debrief", lang))
        return Reply(text=L.phrase("end", lang))
    # question_to_claros / fallback
    return _llm_stream(_hypothesis_messages(sid, text, lang, body), lang)


def _prewritten_ask(sid: Optional[str], key: str) -> Optional[str]:
    t = prewritten.get(sid or "", {}).get(key)
    if t:
        return t
    for path in ("_deps.get_prewritten", "tutor.get_prewritten"):
        gp = deps.knowledge_attr(path)
        if gp:
            try:
                t = gp(key, sid)
                if t:
                    return t
            except Exception:  # noqa: BLE001
                pass
    return None


_debrief_started: set[str] = set()


async def _debrief(sess: Any, intent: str, text: str, body: dict) -> Optional[Reply]:
    """mode=debrief → knowledge.debrief (questions → teach-back → exam)."""
    sid = getattr(sess, "id", None)
    nxt = deps.knowledge_attr("debrief.next_debrief_utterance")
    ans = deps.knowledge_attr("debrief.handle_debrief_answer")
    if not nxt:
        return None
    try:
        first = sid not in _debrief_started
        _debrief_started.add(sid)
        greeting = len(text.split()) <= 3 and intent in ("narration", "confirm") and first
        if first and (not text or greeting or not ans):
            return Reply(text=await nxt(sess))
        if not ans:
            return Reply(text=await nxt(sess))
        it = "answer" if intent == "narration" else intent
        try:
            out = await ans(sess, text, it)
        except TypeError:
            out = await ans(sess, text)
        return Reply(text=out) if out else _skip(body)
    except Exception:  # noqa: BLE001
        deps.log.exception("debrief routing failed")
        return None


async def announce_debrief(sid: str) -> None:
    """After capture ends: tell the client to move to debrief and speak a short transition line."""
    if sid in _phase_sent:
        return
    _phase_sent.add(sid)
    sess = deps.get_session(sid)
    if getattr(sess, "mode", "capture") != "capture":
        return
    lang = deps.session_lang(sid)
    line = L.phrase("to_debrief", lang)
    prewritten.setdefault(sid, {})["phase-debrief"] = line
    await deps.send(sid, {"type": "phase", "phase": "debrief", "workflow_id": getattr(sess, "workflow_id", None)})
    await deps.send(sid, {"type": "status", "level": "info", "text": "debrief_ready"})
    await deps.send(sid, {"type": "ask", "unknown_id": "phase-debrief", "text": line})


_phase_sent: set[str] = set()


async def _control(sid: Optional[str], action: str) -> None:
    if not sid:
        return
    apply_control(sid, action)
    await deps.send(sid, {"type": "control", "action": action})
    await deps.publish(sid, "brain.control", {"action": action})
    if action == "end_task":
        _phase_sent.add(sid)  # the spoken reply itself is the transition line
        sess = deps.get_session(sid)
        if getattr(sess, "mode", "capture") == "capture":
            await deps.send(sid, {"type": "phase", "phase": "debrief",
                                  "workflow_id": getattr(sess, "workflow_id", None)})
            await deps.send(sid, {"type": "status", "level": "info", "text": "debrief_ready"})


def apply_control(sid: str, action: str) -> None:
    sess = deps.get_session(sid)
    lg = get_ledger(sid)
    if action in ("off_record_on", "off_record_off"):
        on = action == "off_record_on"
        try:
            sess.off_record = on
        except Exception:  # noqa: BLE001
            pass
        lg.off_record = on
        gate.set_off_record(sid, on)
    elif action == "strike_that":
        lg.strike_last()
    elif action == "not_now":
        gate.snooze(sid)
    elif action == "end_task":
        lg.end_task()


async def _learner(sid: Optional[str], sess: Any, intent: str, text: str, lang: str, body: dict) -> Reply:
    fn = deps.knowledge_attr("tutor.handle_intent")
    if fn:
        try:
            r = fn(sess, intent, text)
            if hasattr(r, "__aiter__"):
                return Reply(stream=r, buffer_lang=lang)
            r = await deps.maybe_await(r)
            if r is None:
                return _skip(body)
            if isinstance(r, str):
                return Reply(text=r)
            if isinstance(r, dict):
                if r.get("skip") or r.get("tool") == "skip_turn":
                    return _skip(body)
                if r.get("tool"):
                    return Reply(tool=r["tool"], tool_args=r.get("args") or r.get("arguments"))
                return Reply(text=r.get("text", ""))
        except Exception:  # noqa: BLE001
            deps.log.exception("tutor.handle_intent failed; falling back to LLM")
    if intent == "just_watch":
        return Reply(text=L.phrase("watch", lang))
    if intent == "stop":
        return Reply(text=L.phrase("stop", lang))
    wmap = None
    get_map = deps.knowledge_attr("get_map") or deps.knowledge_attr("map.get_map")
    wid = getattr(sess, "workflow_id", None)
    if get_map and wid:
        try:
            wmap = await deps.maybe_await(get_map(wid))
            if hasattr(wmap, "model_dump_json"):
                wmap = wmap.model_dump_json()[:6000]
        except Exception:  # noqa: BLE001
            wmap = None
    sys = (f"You are Claros, a tutor teaching a learner a workflow captured from experts. Speak "
           f"{L.LANG_NAMES.get(lang, 'English')}, ≤2 short sentences. Learner intent: {intent}. Only teach rules "
           f"present in the map; if missing say an expert has not shown this yet. Prefer asking the learner to "
           f"predict before telling. Work map: {wmap or 'none available'}")
    hist = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")][-6:]
    msgs = [{"role": "system", "content": sys}] + [{"role": m["role"], "content": _text(m.get("content"))}
                                                     for m in hist[:-1]] + [{"role": "user", "content": text}]
    return _llm_stream(msgs, lang)


@router.post("/llm/v1/chat/completions")
@router.post("/llm/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    model = body.get("model") or "claros-brain"
    try:
        reply = await route(body)
    except Exception:  # noqa: BLE001
        deps.log.exception("brain route failed")
        reply = _skip(body)
    reply.sid = session_id_from(body)
    if body.get("stream", True):
        return StreamingResponse(sse(reply, model), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    msg: dict = {"role": "assistant", "content": None if reply.tool else await _collect(reply)}
    finish = "stop"
    if reply.tool:
        msg["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                              "function": {"name": reply.tool, "arguments": json.dumps(reply.tool_args)}}]
        finish = "tool_calls"
    return JSONResponse({"id": f"chatcmpl-{uuid.uuid4().hex[:16]}", "object": "chat.completion",
                         "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "message": msg, "finish_reason": finish}]})

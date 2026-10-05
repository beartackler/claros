"""OpenAI-compatible custom-LLM endpoint for ElevenLabs: POST /llm/v1/chat/completions (SSE).

Relay: the server already decides every line over the session socket (brain/dialog.py), and the client hands
each one to the agent as a `⟦TEXT⟧` user message. This endpoint returns the newest pending line verbatim, or a
skip_turn tool call. Stateless, no model in the loop: nothing to paraphrase or skip.

Also home to the control actions the socket handler applies (off the record, strike that, end task).
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
from .ledger import get_ledger

router = APIRouter()

LINE_RE = re.compile(r"^\s*⟦([^⟧]+)⟧\s*$", re.S)  # ⟦TEXT⟧: the line to speak


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def find_skip_tool(body: dict) -> Optional[str]:
    names = [(t.get("function") or {}).get("name") or t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)]
    return "skip_turn" if "skip_turn" in names else next((n for n in names if n and "skip" in n.lower()), None)


class Reply:
    def __init__(self, text: str = "", tool: Optional[str] = None) -> None:
        self.text, self.tool = text, tool


def pending_line(body: dict) -> Optional[str]:
    """The newest ⟦TEXT⟧ line the client sent since the agent last spoke (None if there is none)."""
    msgs = body.get("messages") or []
    last_agent = max((i for i, m in enumerate(msgs) if m.get("role") == "assistant"), default=-1)
    for m in reversed(msgs[last_agent + 1:]):
        if m.get("role") == "user":
            mm = LINE_RE.match(_text(m.get("content")))
            if mm and mm.group(1).strip():
                return mm.group(1).strip()
    return None


def relay(body: dict) -> Reply:
    """Speak the pending ⟦TEXT⟧ exactly as written; otherwise stay silent (skip_turn)."""
    line = pending_line(body)
    return Reply(text=line) if line else Reply(tool=find_skip_tool(body))


def _chunk(cid: str, model: str, delta: dict, finish: Optional[str] = None) -> str:
    return "data: " + json.dumps({
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }, ensure_ascii=False) + "\n\n"


def _tool_call(name: str) -> dict:
    return {"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function", "function": {"name": name, "arguments": "{}"}}


async def sse(reply: Reply, model: str) -> AsyncIterator[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    yield _chunk(cid, model, {"role": "assistant", "content": ""})
    if reply.tool:
        yield _chunk(cid, model, {"tool_calls": [{"index": 0, **_tool_call(reply.tool)}]})
        yield _chunk(cid, model, {}, "tool_calls")
    else:
        if reply.text:
            yield _chunk(cid, model, {"content": reply.text})
        yield _chunk(cid, model, {}, "stop")
    yield "data: [DONE]\n\n"


# ---------------- control actions (used by the session socket) ----------------

_phase_sent: set[str] = set()


async def announce_debrief(sid: str) -> None:
    """After capture ends: tell the client to move to debrief and speak a short transition line."""
    if sid in _phase_sent:
        return
    _phase_sent.add(sid)
    sess = deps.get_session(sid)
    if getattr(sess, "mode", "capture") != "capture":
        return
    line = L.phrase("to_debrief", deps.session_lang(sid))
    await deps.send(sid, {"type": "phase", "phase": "debrief", "workflow_id": getattr(sess, "workflow_id", None)})
    await deps.send(sid, {"type": "status", "level": "info", "text": "debrief_ready"})
    await deps.send(sid, {"type": "ask", "unknown_id": "phase-debrief", "text": line})


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
        if on:
            try:  # the request and the sentence right before it (≤5 s) are not kept
                from claros.privacy import redact_before_off_record
                redact_before_off_record(sid)
            except Exception:  # noqa: BLE001
                deps.log.exception("off-record redaction failed")
    elif action == "strike_that":
        lg.strike_last()
        try:  # tombstone the last 30 s: utterances, screen events/states, keyframes, ledger items
            from claros.privacy import strike
            strike(sid)
        except Exception:  # noqa: BLE001
            deps.log.exception("strike failed")
        if getattr(sess, "mode", None) == "debrief":
            fn = deps.knowledge_attr("debrief.strike_recent")
            if fn:
                try:
                    asyncio.get_running_loop().create_task(fn(sid))
                except RuntimeError:
                    pass
    elif action == "not_now":
        gate.snooze(sid)
    elif action == "end_task":
        lg.end_task()


@router.post("/llm/v1/chat/completions")
@router.post("/llm/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    model = body.get("model") or "claros-brain"
    reply = relay(body)
    if body.get("stream", True):
        return StreamingResponse(sse(reply, model), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    msg: dict = {"role": "assistant", "content": None if reply.tool else reply.text}
    if reply.tool:
        msg["tool_calls"] = [_tool_call(reply.tool)]
    return JSONResponse({"id": f"chatcmpl-{uuid.uuid4().hex[:16]}", "object": "chat.completion",
                         "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if reply.tool else "stop"}]})

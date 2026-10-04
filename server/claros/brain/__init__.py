"""Claros brain: unknowns ledger, pause gate, intents, System One adapter, custom-LLM endpoint.

app.py calls `register(bus)` and `include_router(router)`.
Public helpers for other packages:
  brain.get_ledger(session_id)        → Ledger (unknowns, context_notes, snapshot())
  brain.interventions[sid][gid] = txt → pre-written guardrail intervention text
  brain.gate.decisions(session_id)    → "why Claros asked" log
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import deps, lang as L
from .gate import gate
from .ledger import get_ledger, ledgers
from .llm_endpoint import announce_debrief, apply_control, interventions, router as _llm_router

from . import dialog

router = APIRouter()
router.include_router(_llm_router)
router.include_router(dialog.router)


@router.get("/api/sessions/{session_id}/ledger")
async def ledger_view(session_id: str) -> dict:
    lg = get_ledger(session_id)
    return {**lg.snapshot(),
            "unknowns": [u.model_dump(mode="json") for u in lg.unknowns.values()],
            "context_notes": [n.model_dump(mode="json") for n in lg.context_notes],
            "event_class": lg.event_class,
            "tags": {uid: m.get("tag") for uid, m in lg.meta.items()},
            "hypotheses": {uid: {"samples": m.get("hypotheses"), "share": m.get("hypothesis_share")}
                           for uid, m in lg.meta.items() if m.get("hypotheses")}}


@router.get("/api/sessions/{session_id}/gate")
async def gate_view(session_id: str) -> dict:
    return {"decisions": gate.decisions(session_id),
            "blockers_now": gate.blockers(session_id, deps.now_ms())}


def register(bus: Any) -> None:
    deps.BUS = bus

    async def on_events(sid: str, p: Any) -> None:
        items = p.get("items", p) if isinstance(p, dict) else p
        gate.on_events(sid, items)
        await get_ledger(sid).on_events(items)
        gate._ensure_ticker()

    async def on_utt(sid: str, p: Any) -> None:
        await get_ledger(sid).on_utterance(p)

    async def on_control(sid: str, p: Any) -> None:
        a = (p or {}).get("action")
        if a:
            apply_control(sid, a)
            if a in ("off_record_on", "off_record_off", "strike_that", "end_task"):
                await get_ledger(sid).emit()
            if a == "end_task":
                await announce_debrief(sid)
            if a == "debrief_skip":  # the debrief screen's Skip: defer the current question, ask the next one
                fn = deps.knowledge_attr("debrief.handle_debrief_answer")
                if fn:
                    out = await fn(deps.get_session(sid), "__skip__", "not_now")
                    if out:
                        await dialog.say(sid, out, "debrief")
            # off_record_off: the web app already says "Back on the record" (saying it here too doubled it)

    async def on_hello(sid: str, p: Any) -> None:
        s = deps.get_session(sid)
        if s is not None and s.__class__.__name__ == "SimpleNamespace":
            for k in ("mode", "lang", "workflow_id"):
                if p.get(k):
                    setattr(s, k, p[k])
        get_ledger(sid)
        gate.st(sid)
        gate._ensure_ticker()

    async def on_ended(sid: str, p: Any) -> None:
        if (p or {}).get("mode", "capture") == "capture" if isinstance(p, dict) else True:
            await announce_debrief(sid)
        get_ledger(sid).end_task()
        await get_ledger(sid).emit()
        gate.states.pop(sid, None)

    bus.subscribe("screen.events", on_events)
    bus.subscribe("screen.state", lambda sid, p: gate.on_screen_state(sid, p))
    bus.subscribe("utterance", on_utt)
    bus.subscribe("ws.in.utterance", on_utt)  # deduped by event_id in the ledger
    bus.subscribe("ws.in.activity", lambda sid, p: gate.on_activity(sid, p))
    bus.subscribe("ws.in.vad", lambda sid, p: gate.on_vad(sid, p))
    bus.subscribe("ws.in.agent_state", lambda sid, p: gate.on_agent_state(sid, p))
    bus.subscribe("ws.in.control", on_control)
    bus.subscribe("ws.in.hello", on_hello)
    bus.subscribe("session.ended", on_ended)
    dialog.register(bus)  # hosted dialog loop (no-op in CLAROS_DIALOG_MODE=custom)

    try:
        import asyncio

        from . import systemone
        asyncio.get_running_loop().create_task(systemone.warmup())
    except RuntimeError:
        pass


__all__ = ["register", "router", "get_ledger", "ledgers", "gate", "interventions"]

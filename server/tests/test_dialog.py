"""Hosted dialog layer (claros.brain.dialog): say protocol, server-driven debrief/tutor turns, lookup, mode switch."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claros.brain import deps, dialog, ledger as ledger_mod
from claros.brain.gate import gate as GATE
from claros.models import Decision, Guardrail, Quote, Step, User, WorkMap


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("CLAROS_SYSTEMONE_BACKENDS", "rules")
    monkeypatch.setenv("CLAROS_GLINER2", "0")
    monkeypatch.delenv("CLAROS_DIALOG_MODE", raising=False)
    monkeypatch.setattr(deps, "now_ms", lambda: 1_000_000.0)
    sessions: dict = {}

    def get_session(sid):
        if sid not in sessions:
            sessions[sid] = SimpleNamespace(id=sid, mode="capture", lang="en", off_record=False, workflow_id=None,
                                            extra={}, user=None)
        return sessions[sid]

    monkeypatch.setattr(deps, "get_session", get_session)
    sent: list = []

    async def send(sid, msg):
        sent.append((sid, msg))

    async def publish(sid, topic, payload):
        if topic == "ws.out":
            sent.append((sid, payload))

    monkeypatch.setattr(deps, "send", send)
    monkeypatch.setattr(deps, "publish", publish)

    async def none(*a, **k):
        return None

    monkeypatch.setattr(deps, "llm_chat", none)
    monkeypatch.setattr(deps, "embed", none)
    monkeypatch.setattr(deps, "classify_scope", none)
    for k, v in {"settle_s": 0, "quiet_ms": 0, "floor_timeout_s": 0.5, "poll_s": 0.01,
                 "context_min_interval_s": 0, "vars_wait_s": 0.2}.items():
        monkeypatch.setitem(dialog.CONFIG, k, v)
    ledger_mod.ledgers.clear()
    GATE.states.clear()
    dialog.reset()
    dialog._brief_cache.clear()
    return SimpleNamespace(sessions=sessions, sent=sent, get_session=get_session)


def says(sent):
    return [m for _, m in sent if m.get("type") == "say"]


def _knowledge(monkeypatch, table):
    real = deps.knowledge_attr
    monkeypatch.setattr(deps, "knowledge_attr", lambda path: table[path] if path in table else real(path))


def _wm():
    return WorkMap(
        id="m1", workflow_id="wf1", name="AP invoice", experts=[User(id="x1", name="Maria Lopez", role="expert")],
        steps=[Step(id="s1", order=1, title="Open the invoice", approved=True),
               Step(id="s2", order=2, title="Check the cost center", approved=True,
                    decision=Decision(kind="judgment", description="Equipment over 5,000 goes to capex",
                                      reason_quote_ids=["q1"]), guardrail_ids=["g1"])],
        guardrails=[Guardrail(id="g1", text="Invoices over 10,000 need CFO approval", experts=["x1"],
                              quote_ids=["q1"], approved=True)],
        quotes=[Quote(id="q1", speaker="Maria Lopez", speaker_id="x1", lang="en", t=1.0, session_id="c1",
                      text="Anything over ten thousand, Frank the CFO signs off.")])


# ---------------- say protocol ----------------

def test_say_message_shape_and_registry(offline):
    m = run(dialog.say("s1", "Why ⟦x⟧ 0400 here?", "ask", id="u1"))
    assert m == {"type": "say", "id": "u1", "text": "Why 0400 here?", "kind": "ask", "lang": "en"}
    assert offline.sent[-1][1] == m
    assert run(dialog.say("s1", "  ", "ack")) is None


def test_teachback_segments_carry_step_ids(offline):
    out = run(dialog.say_script("s1", "Let me say it back. [[step:a]] First open it. [[step:b]] Then check. Right?"))
    assert [(m["kind"], m.get("step_id"), m["text"]) for m in out] == [
        ("teachback", None, "Let me say it back."), ("teachback", "a", "First open it."),
        ("teachback", "b", "Then check. Right?")]
    assert len({m["id"] for m in out}) == 3 and all("[[" not in m["text"] for m in out)




def test_wait_floor_respects_agent_speaking(offline):
    GATE.on_agent_state("f1", {"mode": "speaking"})
    assert run(dialog.wait_floor("f1", timeout=0.05)) is False
    GATE.on_agent_state("f1", {"mode": "listening"})
    assert run(dialog.wait_floor("f1", timeout=0.05)) is True


# ---------------- dialog loop ----------------

def test_debrief_next_say_after_answer(offline, monkeypatch):
    calls = []

    async def nxt(sess):
        calls.append("next")
        return "Why did you route it to Frank?"

    async def ans(sess, text, intent=None):
        calls.append(("answer", text, intent))
        return "Got it. [[step:s1]] First you open the invoice. Right?"

    _knowledge(monkeypatch, {"debrief.next_debrief_utterance": nxt, "debrief.handle_debrief_answer": ans})
    offline.get_session("d1").mode = "debrief"
    run(dialog.on_hello("d1", {"mode": "debrief", "workflow_id": None}))
    assert says(offline.sent)[-1]["text"] == "Why did you route it to Frank?"
    assert says(offline.sent)[-1]["kind"] == "debrief"
    run(dialog.kick_debrief("d1"))  # reconnect hello: no second kickoff
    assert calls.count("next") == 1
    offline.sent.clear()
    r = run(dialog.on_utterance("d1", {"role": "user", "text": "Because he owns the UK entity", "event_id": "u1"}))
    assert calls[-1][0] == "answer" and calls[-1][2] == "answer"
    s = says(offline.sent)
    assert [(m["kind"], m.get("step_id")) for m in s] == [("teachback", None), ("teachback", "s1")]
    assert r["intent"] in ("answer", "narration") and r["said"]
    # same event delivered twice (utterance + ws.in.utterance) → handled once
    assert run(dialog.on_utterance("d1", {"role": "user", "text": "Because he owns the UK entity",
                                          "event_id": "u1"})) is None


def test_debrief_answer_that_sounds_addressed_to_claros(offline, monkeypatch):
    """e2e: "if you don't understand the classification" was read as a question to Claros → silence for 60 s."""
    from claros.brain import intents
    seen_ctx, calls = [], []

    async def classify(mode, text, ctx):
        seen_ctx.append(ctx)
        return intents.IntentResult(intent="question_to_claros", confidence=0.6, backend="test")

    async def ans(sess, text, intent=None):
        calls.append(intent)
        return "Got it. Next one?"

    monkeypatch.setattr(dialog, "classify_intent", classify)
    _knowledge(monkeypatch, {"debrief.handle_debrief_answer": ans, "debrief.next_debrief_utterance": ans,
                             "debrief.current_question": lambda sess: "When would you stop and ask someone?"})
    offline.get_session("d2").mode = "debrief"
    r = run(dialog.on_utterance("d2", {"role": "user", "event_id": "a",
                                       "text": "I think if you don't understand the classification of the invoice."}))
    assert seen_ctx[-1]["pending_question"] == "When would you stop and ask someone?"
    assert calls == ["answer"] and r["said"]
    r = run(dialog.on_utterance("d2", {"role": "user", "event_id": "b", "text": "Can you say that again?"}))
    assert calls == ["answer"] and says(offline.sent)[-1]["text"] == "When would you stop and ask someone?"
    # agent lines and markers are never treated as user turns
    assert run(dialog.on_utterance("d1", {"role": "agent", "text": "hello", "event_id": "a1"})) is None
    assert run(dialog.on_utterance("d1", {"role": "user", "text": "⟦say:x|hi⟧", "event_id": "u2"})) is None


def test_tutor_say_on_intervention_answer(offline, monkeypatch):
    seen = []

    async def handle_intent(sess, intent, text):
        seen.append(intent)
        return "Right. Maria said: anything over ten thousand goes to Frank."

    _knowledge(monkeypatch, {"tutor.handle_intent": handle_intent})
    offline.get_session("l1").mode = "learn"
    r = run(dialog.on_utterance("l1", {"role": "user", "text": "what's next?", "event_id": "l-1"}))
    assert seen == ["what_next"] and r["said"][0]["kind"] == "tutor"
    assert says(offline.sent)[-1]["text"].startswith("Right. Maria said")
    # learner privacy control works in learn mode too, without the tutor
    r = run(dialog.on_utterance("l1", {"role": "user", "text": "не записывай это", "event_id": "l-2"}))
    assert r["action"] == "off_record_on" and offline.sessions["l1"].off_record is True
    assert {"type": "control", "action": "off_record_on"} in [m for _, m in offline.sent]


def test_capture_controls_and_ack(offline):
    lg = ledger_mod.get_ledger("c1")
    from tests.test_brain import ev
    u = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))[0]
    lg.mark_asked(u.id)
    r = run(dialog.on_utterance("c1", {"role": "user", "text": "Because that department pays for it",
                                       "event_id": "c-1"}))
    assert r["intent"] == "answer" and lg.unknowns[u.id].status == "answered"
    assert says(offline.sent)[-1] == {"type": "say", "id": says(offline.sent)[-1]["id"], "text": "Got it, thanks.",
                                       "kind": "ack", "lang": "en"}
    # narration with nothing pending → silence
    n = len(says(offline.sent))
    r = run(dialog.on_utterance("c2", {"role": "user", "text": "now I open the next invoice", "event_id": "c-2"}))
    assert r["intent"] == "narration" and len(says(offline.sent)) == n
    r = run(dialog.on_utterance("c3", {"role": "user", "text": "I'm done", "event_id": "c-3"}))
    assert r["action"] == "end_task"
    out = [m for _, m in offline.sent]
    assert {"type": "phase", "phase": "debrief", "workflow_id": None} in out
    assert says(offline.sent)[-1]["text"] == "Thanks! I'm putting your map together, then I'll ask a few questions."


def test_ack_does_not_wait_for_rule_extraction_and_answer_recorded_once(offline, monkeypatch):
    """Live: "Got it" came 7 s after the expert stopped (it awaited GLiNER2 + LLM), and the resolution was
    the answer twice (dialog ack + the ledger's own resolution both recorded it)."""
    async def slow_extract(text, u=None):
        await asyncio.sleep(5)

    monkeypatch.setattr(ledger_mod, "extract_rule", slow_extract)
    lg = ledger_mod.get_ledger("ack1")
    from tests.test_brain import ev
    u = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))[0]
    lg.mark_asked(u.id)
    p = {"role": "user", "text": "Because that department pays for it", "event_id": "a-1"}

    async def both():
        r = await asyncio.wait_for(dialog.on_utterance("ack1", p), 1.0)
        await asyncio.wait_for(lg.on_utterance(p), 1.0)  # same text already recorded: no second copy, no wait
        return r
    r = run(both())
    assert r["said"] and lg.unknowns[u.id].status == "answered"
    assert lg.unknowns[u.id].resolution == "Because that department pays for it"


def test_yeah_plus_narration_is_not_a_confirm(offline):
    """Live: "Yeah, now I open the next invoice from the list" matched the confirm rule → the open why-question
    was marked answered and Claros said "Great, noted."."""
    lg = ledger_mod.get_ledger("cf1")
    from tests.test_brain import ev
    u = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))[0]
    lg.mark_asked(u.id)
    lg.meta[u.id]["asked_ms"] -= 30_000
    for i, text in enumerate(["Yeah, now I open the next invoice from the list", "Yeah."]):
        r = run(dialog.on_utterance("cf1", {"role": "user", "text": text, "event_id": f"cf-{i}"}))
        assert r["intent"] == "narration" and lg.unknowns[u.id].status == "asked" and not says(offline.sent)


# ---------------- context + vars + lookup ----------------

def test_context_update_compact_and_throttled(offline, monkeypatch):
    monkeypatch.setattr(dialog, "_load_map", lambda wid: _wm() if wid == "wf1" else None)
    offline.get_session("k1").workflow_id = "wf1"
    run(dialog.on_screen_state("k1", {"app": "ERPNext", "view": "Purchase Invoice form", "entity_id": "PINV-1"}))
    run(dialog.on_ws_out("k1", {"type": "highlight_step", "step_id": "s2"}))
    ctx = [m["text"] for _, m in offline.sent if m.get("type") == "context_update"]
    assert ctx and "Current step 2/2: Check the cost center" in ctx[-1] and "ERPNext" in ctx[-1]
    assert all(len(t) <= 400 for t in ctx)
    n = len(ctx)
    run(dialog.on_ws_out("k1", {"type": "highlight_step", "step_id": "s2"}))  # unchanged → nothing
    assert len([m for _, m in offline.sent if m.get("type") == "context_update"]) == n


def test_vars_endpoint_brief(offline, monkeypatch):
    monkeypatch.setattr(dialog, "_load_map", lambda wid: _wm() if wid == "wf1" else None)
    s = offline.get_session("v1")
    s.mode, s.workflow_id, s.lang = "learn", "wf1", "de"
    s.user = User(id="l", name="Lea", role="learner")
    app = FastAPI()
    app.include_router(dialog.router)
    j = TestClient(app).get("/api/dialog/vars", params={"session_id": "v1"}).json()
    assert j["mode"] == "learn" and j["user_name"] == "Lea" and j["lang"] == "de" and j["workflow_name"] == "AP invoice"
    b = j["workflow_brief"]
    assert len(b) <= 1500 and "2. Check the cost center" in b and "CFO approval (per Maria)" in b


def test_lookup_endpoint(offline, monkeypatch):
    monkeypatch.setattr(dialog, "_load_map", lambda wid: _wm() if wid == "wf1" else None)
    offline.get_session("q1").workflow_id = "wf1"
    app = FastAPI()
    app.include_router(dialog.router)
    c = TestClient(app)
    j = c.get("/api/dialog/lookup", params={"session_id": "q1", "q": "who approves invoices over 10,000"}).json()
    assert j["found"] and "CFO approval" in j["answer"] and "Maria" in j["answer"]
    assert j["items"][0]["kind"] == "guardrail"
    j = c.get("/api/dialog/lookup", params={"session_id": "q1", "q": "vacation policy"}).json()
    assert not j["found"] and "expert" in j["answer"]
    j = c.get("/api/dialog/lookup", params={"session_id": "nomap", "q": "anything"}).json()
    assert not j["found"]


def test_agent_config_hosted(monkeypatch):
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "agents" / "build_config.py"
    spec = importlib.util.spec_from_file_location("build_config", p)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    monkeypatch.delenv("CLAROS_DIALOG_MODE", raising=False)
    monkeypatch.setenv("CLAROS_LLM_URL", "https://x.example/llm/v1")
    cfg = bc.build()["conversation_config"]
    pr = cfg["agent"]["prompt"]
    assert pr["llm"] == "gemini-3.6-flash" and pr["reasoning_effort"] == "minimal" and pr["custom_llm"] is None
    assert pr["backup_llm_config"]["order"] == ["glm-52", "gemini-3.5-flash-lite"]
    assert cfg["tts"]["model_id"] == "eleven_v4_turbo" and "wrapped in ⟦ ⟧" in pr["prompt"]
    monkeypatch.setenv("CLAROS_DIALOG_MODE", "custom")
    pr = bc.build()["conversation_config"]["agent"]["prompt"]
    assert pr["llm"] == "custom-llm" and pr["custom_llm"]["url"] == "https://x.example/llm/v1"
    assert not bc.stable_https("https://abc.trycloudflare.com") and bc.stable_https("https://claros-server.onrender.com")
    t = bc.lookup_tool("https://claros-server.onrender.com/")
    assert t["api_schema"]["url"] == "https://claros-server.onrender.com/api/dialog/lookup"
    assert t["api_schema"]["query_params_schema"]["properties"]["session_id"]["dynamic_variable"] == "session_id"
    json.dumps(t)


def test_learner_voice_answers_open_nudge_before_intent_routing(offline, monkeypatch):
    offline.get_session("ln").mode = "learn"
    calls = []

    async def on_voice(sid, text, speak=True):
        calls.append((sid, text, speak))
        return {"outcome": "correct", "feedback_spoken": "Yes."} if "second" in text else None

    async def tutor_turn(sess, intent, text):
        return "tutor:" + intent
    _knowledge(monkeypatch, {"nudges.on_voice": on_voice, "tutor.handle_intent": tutor_turn})
    r = run(dialog.handle_turn("ln", "the second one"))
    assert r == {"intent": "answer_nudge", "outcome": "correct"} and calls[-1][2] is True
    r = run(dialog.handle_turn("ln", "what's next?"))  # not an answer → normal tutor routing
    assert r["intent"] != "answer_nudge"


# ---------------- relay mode (custom LLM speaks only the server's lines) ----------------

def _relay_body(*msgs):
    return {"elevenlabs_extra_body": {"relay": True, "session_id": "s_x"},
            "tools": [{"type": "function", "function": {"name": "skip_turn"}}],
            "messages": [{"role": r, "content": c} for r, c in msgs]}


def test_relay_speaks_line_verbatim():
    from claros.brain.llm_endpoint import relay
    q = "Which cost centers apply when equipment is not on the shop floor?"
    r = relay(_relay_body(("system", "prompt"), ("user", f"⟦{q}⟧")))
    assert r.text == q and not r.tool


def test_relay_skips_user_speech_and_old_lines():
    from claros.brain.llm_endpoint import relay
    r = relay(_relay_body(("user", "⟦Got it, thanks.⟧"), ("assistant", "Got it, thanks."),
                          ("user", "Let's take a look at this Feldmark entry.")))
    assert r.tool == "skip_turn" and not r.text


def test_relay_takes_newest_pending_line():
    from claros.brain.llm_endpoint import relay
    r = relay(_relay_body(("assistant", "hi"), ("user", "⟦First.⟧"), ("user", "um"), ("user", "⟦Second.⟧")))
    assert r.text == "Second."


def test_capture_question_to_claros_gets_an_answer(offline, monkeypatch):
    """Relay: the agent never answers on its own, so a question mid-capture must not end in silence."""
    from claros.brain import intents

    async def classify(mode, text, ctx):
        return intents.IntentResult(intent="question_to_claros", confidence=0.9, backend="test")

    replies = iter(["That's the Purchase Invoice form, still in Draft.", "UNSURE"])

    async def llm(messages, **k):
        assert "Question:" in messages[-1]["content"]
        return next(replies)

    monkeypatch.setattr(dialog, "classify_intent", classify)
    monkeypatch.setattr(deps, "llm_chat", llm)
    run(dialog.on_utterance("q1", {"role": "user", "event_id": "a", "text": "Claros, what screen is this?"}))
    assert says(offline.sent)[-1]["text"] == "That's the Purchase Invoice form, still in Draft."
    run(dialog.on_utterance("q1", {"role": "user", "event_id": "b", "text": "Claros, why do I do that?"}))
    assert says(offline.sent)[-1]["text"].startswith("I'm not sure yet")

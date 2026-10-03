"""Offline tests for claros.brain (no network: systemone → rules, llm mocked)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claros.brain import deps, intents, ledger as ledger_mod, llm_endpoint
from claros.brain.gate import Gate, GateConfig, gate as GATE


def run(c):
    return asyncio.run(c)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("CLAROS_SYSTEMONE_BACKENDS", "rules")
    monkeypatch.setenv("CLAROS_GLINER2", "0")
    clock = Clock()
    monkeypatch.setattr(deps, "now_ms", clock)
    sessions: dict = {}

    def get_session(sid):
        if sid not in sessions:
            sessions[sid] = SimpleNamespace(id=sid, mode="capture", lang="en", off_record=False,
                                            workflow_id=None, extra={})
        return sessions[sid]

    monkeypatch.setattr(deps, "get_session", get_session)
    sent: list = []

    async def publish(sid, topic, payload):
        sent.append((sid, topic, payload))

    monkeypatch.setattr(deps, "publish", publish)

    async def send(sid, msg):
        sent.append((sid, "ws.out", msg))

    monkeypatch.setattr(deps, "send", send)

    async def no_llm(*a, **k):
        return None

    async def no_embed(*a, **k):
        return None

    monkeypatch.setattr(deps, "llm_chat", no_llm)
    monkeypatch.setattr(deps, "embed", no_embed)

    async def no_scope(*a, **k):
        return None

    monkeypatch.setattr(deps, "classify_scope", no_scope)
    ledger_mod.ledgers.clear()
    GATE.states.clear()
    llm_endpoint.interventions.clear()
    return SimpleNamespace(clock=clock, sessions=sessions, sent=sent, get_session=get_session)


def ev(i, kind="edit", **kw):
    d = {"id": f"e{i}", "seq": i, "t": 1000.0 * i, "kind": kind, "app": "ERPNext",
         "entity_type": "Purchase Invoice", "entity_id": "PINV-4471", "confidence": 0.9}
    d.update(kw)
    return d


# ---------------- intents ----------------

@pytest.mark.parametrize("text,intent", [
    ("Не записывай это, пожалуйста", "off_record"),
    ("не сейчас", "not_now"),
    ("Забудь, что я сказал", "strike_that"),
    ("Да, верно", "confirm"),
    ("hors micro s'il te plaît", "off_record"),
    ("esto fuera de registro", "off_record"),
    ("Nicht jetzt bitte", "not_now"),
    ("strike that", "strike_that"),
])
def test_expert_rules_multilingual(text, intent):
    r = run(intents.classify_intent("capture", text, {}))
    assert r.intent == intent and r.backend == "rules"


@pytest.mark.parametrize("text,intent", [
    ("Проведи меня по шагам", "walk_through"),
    ("что дальше?", "what_next"),
    ("Почему здесь 0400?", "why_this"),
    ("дай подсказку", "hint"),
    ("walk me through this", "walk_through"),
    ("¿por qué?", "why_this"),
])
def test_learner_rules(text, intent):
    r = run(intents.classify_intent("learn", text, {}))
    assert r.intent == intent


def test_intent_default_answer_when_pending():
    r = run(intents.classify_intent("capture", "Because the vendor is a subsidiary of ours",
                                    {"pending_question": "Why 0400?"}))
    assert r.intent == "answer"
    r = run(intents.classify_intent("capture", "Now I open the next invoice", {}))
    assert r.intent == "narration"


# ---------------- ledger ----------------

def test_ledger_opens_and_closes_unknowns(offline):
    lg = ledger_mod.get_ledger("s1")
    opened = run(lg.on_events([
        ev(1, "open"),
        ev(2, "edit", field="Cost Center", old="4711", new="0400", source="typed",
           summary="Cost center changed 4711 → 0400"),
        ev(3, "edit", field="Grand Total", old="", new="4,950.00", source="typed"),
    ]))
    assert len(opened) == 2
    types = {u.type for u in opened}
    assert "why" in types and "limit" in types
    lim = next(u for u in opened if u.type == "limit")
    assert lim.hypothesis and "5,000" in lim.hypothesis and lim.hypothesis_confidence >= 0.7
    assert "is that your rule" in lim.spoken_question
    for u in opened:
        assert len(u.spoken_question.split()) <= 15
        assert u.scope in ("company", "personal_judgment") and u.status == "open"
    why = next(u for u in opened if u.type == "why")
    assert "0400" in why.spoken_question
    # ledger message emitted
    assert any(m[2].get("type") == "ledger" and m[2]["open"] == 2 for m in offline.sent if m[1] == "ws.out")
    # dedupe: same edit again does not open new unknown
    again = run(lg.on_events([ev(4, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))
    assert again == [] and len(lg.unknowns) == 2
    # asked + answered via utterance
    lg.mark_asked(why.id)
    done = run(lg.on_utterance({"role": "user", "text": "Because vendors from our group always book to 0400",
                                "event_id": "utt1"}))
    assert [u.id for u in done] == [why.id]
    assert why.status == "answered" and why.resolution_source == "expert"
    assert why.extracted_rule is not None and why.extracted_rule.threshold == "0400"
    # expiry → deferred
    offline.clock.t += 46_000
    lg.expire()
    assert lim.status == "deferred"
    assert lg.snapshot()["saved_for_later"] == 1


def test_ledger_resolves_generic_scope_without_expert(offline, monkeypatch):
    async def scope(*a, **k):
        return "app"

    async def resolve(*a, **k):
        return {"text": "Cost Center is an accounting dimension in ERPNext.", "source": "app_docs:https://docs.erpnext.com"}

    monkeypatch.setattr(deps, "classify_scope", scope)
    monkeypatch.setattr(deps, "try_resolve", resolve)
    lg = ledger_mod.get_ledger("s2")
    opened = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))
    u = opened[0]
    assert u.status == "resolved" and u.resolution_source.startswith("app_docs")
    assert lg.context_notes and lg.top_candidate() is None


def test_ledger_classifies_slip_and_guardrail():
    lg = ledger_mod.get_ledger("s3")
    assert lg.heuristic_class(ledger_mod._ev(ev(1, "undo"))) == "slip"
    assert lg.heuristic_class(ledger_mod._ev(ev(2, "hold"))) == "guardrail"
    assert lg.heuristic_class(ledger_mod._ev(ev(3, "edit", field="Qty", old="1", new="1", source="system"))) == "routine"


def test_off_record_ignores_events(offline):
    offline.get_session("s4").off_record = True
    assert run(ledger_mod.get_ledger("s4").on_events([ev(1, "hold")])) == []


def test_requirement_guard_synthesizes_guardrail(offline):
    lg = ledger_mod.get_ledger("s5")
    offline.clock.t += 120_000
    opened = run(lg.on_events([ev(1, "submit")]))
    assert opened and opened[0].type == "stop_and_ask"


# ---------------- gate ----------------

def _gate_ready(offline, sid="g1"):
    g = Gate(GateConfig())
    lg = ledger_mod.get_ledger(sid)
    run(lg.on_events([ev(1, "hold", field="Status", new="On Hold", summary="Invoice put on hold")]))
    g.st(sid)
    offline.clock.t += 2000
    return g, lg


def test_gate_asks_when_quiet(offline):
    g, lg = _gate_ready(offline)
    r = run(g.evaluate("g1"))
    assert r["decision"] == "ask_now"
    asks = [m[2] for m in offline.sent if m[1] == "ws.out" and m[2].get("type") == "ask"]
    assert asks and asks[0]["text"] == lg.unknowns[asks[0]["unknown_id"]].spoken_question
    assert g.decisions("g1")[-1]["reasons"]


def test_gate_respects_typing(offline):
    g, lg = _gate_ready(offline)
    g.on_activity("g1", {"kind": "typing", "tiles_changed": 3})
    offline.clock.t += 1500
    r = run(g.evaluate("g1"))
    assert r["decision"] == "blocked" and "typing" in r["reasons"]


def test_gate_respects_speech_and_agent(offline):
    g, lg = _gate_ready(offline)
    g.on_vad("g1", {"speaking": True})
    assert "speech" in run(g.evaluate("g1"))["reasons"]
    g.on_vad("g1", {"speaking": False})
    g.on_agent_state("g1", {"mode": "speaking"})
    offline.clock.t += 2000
    assert any("agent" in r for r in run(g.evaluate("g1"))["reasons"])


def test_gate_budget_and_gap(offline):
    g, lg = _gate_ready(offline)
    assert run(g.evaluate("g1"))["decision"] == "ask_now"
    run(lg.on_events([ev(5, "reject", field="Supplier", new="ACME", summary="Rejected")]))
    offline.clock.t += 10_000
    r = run(g.evaluate("g1"))
    assert r["decision"] == "blocked" and any("min gap" in x for x in r["reasons"])
    g.st("g1").asks = [offline.clock.t - 1000 * i for i in range(100, 105)]
    offline.clock.t += 95_000
    run(lg.on_events([ev(6, "escalate", field="Approver", new="CFO")]))
    offline.clock.t += 2000
    r = run(g.evaluate("g1"))
    assert r["decision"] == "blocked" and any("budget" in x for x in r["reasons"])


def test_gate_snooze_and_reading_grace(offline):
    g, lg = _gate_ready(offline)
    g.snooze("g1")
    assert any("snoozed" in x for x in run(g.evaluate("g1"))["reasons"])
    g.st("g1").snooze_until = 0
    g.on_screen_state("g1", {"new_words": 20})
    offline.clock.t += 1300
    assert any("reading grace" in x for x in run(g.evaluate("g1"))["reasons"])


# ---------------- endpoint ----------------

@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(llm_endpoint.router)
    return TestClient(app)


def _sse(resp):
    chunks = []
    for line in resp.text.split("\n\n"):
        line = line.strip()
        if not line:
            continue
        assert line.startswith("data: ")
        data = line[6:]
        chunks.append(data if data == "[DONE]" else json.loads(data))
    assert chunks[-1] == "[DONE]"
    return chunks[:-1]


def _content(chunks):
    return "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)


def _body(text, sid="e1", tools=None, system=None):
    msgs = [{"role": "system", "content": system or "You are Claros."}, {"role": "user", "content": text}]
    b = {"model": "x", "stream": True, "messages": msgs, "elevenlabs_extra_body": {"session_id": sid}}
    if tools is not None:
        b["tools"] = tools
    return b


SKIP_TOOLS = [{"type": "function", "function": {"name": "skip_turn", "parameters": {"type": "object"}}}]


def test_endpoint_ask_passthrough(offline, client):
    lg = ledger_mod.get_ledger("e1")
    opened = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))
    u = opened[0]
    r = client.post("/llm/v1/chat/completions", json=_body(f"⟦ask:{u.id}⟧"))
    assert r.headers["content-type"].startswith("text/event-stream")
    ch = _sse(r)
    assert ch[0]["object"] == "chat.completion.chunk"
    assert _content(ch) == u.spoken_question
    assert ch[-1]["choices"][0]["finish_reason"] == "stop"
    assert u.status == "asked"


def test_endpoint_narration_skip_turn(offline, client):
    r = client.post("/llm/v1/chat/completions", json=_body("Now I open the next invoice", tools=SKIP_TOOLS))
    ch = _sse(r)
    tcs = [c for c in ch if c["choices"][0]["delta"].get("tool_calls")]
    assert tcs and tcs[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "skip_turn"
    assert ch[-1]["choices"][0]["finish_reason"] == "tool_calls"
    # no tool provided → empty content
    ch = _sse(client.post("/llm/v1/chat/completions", json=_body("Now I open the next invoice")))
    assert _content(ch) == ""


def test_endpoint_session_from_system_prompt_and_russian(offline, client):
    offline.get_session("ru1").lang = "ru"
    r = client.post("/llm/v1/chat/completions",
                    json={"stream": True, "messages": [
                        {"role": "system", "content": "Agent.\nclaros-session: ru1"},
                        {"role": "user", "content": "не записывай это"}]})
    assert _content(_sse(r)) == "Не записываю."
    assert offline.get_session("ru1").off_record is True
    assert any(m[2].get("action") == "off_record_on" for m in offline.sent if m[1] == "ws.out")


def test_endpoint_answer_ack_and_not_now(offline, client):
    lg = ledger_mod.get_ledger("e2")
    u = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))[0]
    lg.mark_asked(u.id)
    ch = _sse(client.post("/llm/v1/chat/completions",
                          json=_body("Because group vendors always go to 0400", sid="e2")))
    txt = _content(ch)
    assert len(txt.split()) <= 4
    assert u.status == "answered"
    ch = _sse(client.post("/llm/v1/chat/completions", json=_body("not now", sid="e2")))
    assert _content(ch) == "Sure, later."
    assert GATE.st("e2").snooze_until > offline.clock.t


def test_endpoint_intervene_registry(offline, client):
    llm_endpoint.interventions["e3"] = {"g1": "Stop — invoices over 5,000 need the CFO."}
    ch = _sse(client.post("/llm/v1/chat/completions", json=_body("⟦intervene:g1⟧", sid="e3")))
    assert _content(ch) == "Stop — invoices over 5,000 need the CFO."


def test_endpoint_llm_buffer_phrase(offline, client, monkeypatch):
    monkeypatch.setattr(llm_endpoint, "BUFFER_AFTER_S", 0.05)

    async def slow_stream(messages, model_role="fast"):
        await asyncio.sleep(0.2)
        yield "I think it's the group vendor rule — right?"

    monkeypatch.setattr(deps, "llm_stream", slow_stream)
    ch = _sse(client.post("/llm/v1/chat/completions", json=_body("Claros, what do you think this is?", sid="e4")))
    txt = _content(ch)
    assert txt.startswith("Hmm, let me think.") and "group vendor" in txt


def test_systemone_confidence_recomputed():
    from claros.brain.systemone import normalize_answer
    a = normalize_answer({"type": "choice"}, {"probabilities": {"a": 0.6, "b": 0.3, "c": 0.1}, "confidence": 0.99})
    assert a["choice"] == "a" and abs(a["confidence"] - 0.3) < 1e-9

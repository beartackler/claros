"""Offline tests for claros.knowledge (llm/embed/onet/systemone mocked; in-memory SQLite store)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claros.knowledge import _deps as d
from claros.knowledge import builder, common, debrief, export, lookup, mcp, merge, tutor
from claros.models import ScreenState, User, WorkMap

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"


class FakeBus:
    def __init__(self):
        self.msgs = []

    def publish(self, sid, topic, payload=None):
        self.msgs.append((sid, topic, payload))

    def subscribe(self, topic, h):
        pass

    def out(self, typ=None):
        return [p for _, t, p in self.msgs if t == "ws.out" and (typ is None or p.get("type") == typ)]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    from claros.store import Store
    d.STORE = Store(":memory:")
    bus = FakeBus()
    d.BUS = bus
    monkeypatch.setattr(d, "_llm", lambda: None)  # embed → hash fallback, chat → None

    async def no_onet(text, **kw):
        return None
    monkeypatch.setattr(d, "onet_match", no_onet)

    async def no_decide(q, context, options):
        return None, 0.0
    monkeypatch.setattr(d, "decide", no_decide)
    d.PREWRITTEN.clear()
    tutor._states.clear()
    debrief._states.clear()
    yield bus
    d.STORE = None
    d.BUS = None


def fx(name="workmap_ap.json") -> WorkMap:
    return WorkMap.model_validate(json.loads((FIX / name).read_text()))


async def seed(*names):
    out = None
    for n in names or ("workmap_ap.json",):
        out = await builder.publish_expert_map(fx(n))
    return out


# ---------------- common ----------------

def test_numbers_dates_predicates():
    assert common.parse_number("5.200,00") == 5200.0
    assert common.parse_number("€ 5,200.50") == 5200.5
    assert common.parse_number("1,5") == 1.5
    assert common.parse_date("03.12.2026") == (2026, 12, 3)
    assert common.parse_date("2026-12-03") == (2026, 12, 3)
    p = {"and": [{">": [{"var": "line.amount"}, 5000]}, {"!": {"in": ["Capital", {"var": "line.expense_account"}]}}]}
    assert common.eval_predicate(p, {"line.amount": 5200, "line.expense_account": "Office"}) is True
    assert common.eval_predicate(p, {"line.amount": 5200, "line.expense_account": "Capital Equipment"}) is False
    assert common.eval_predicate(p, {"line.amount": 5200}) is None  # missing var → not evaluable


def test_fixtures_valid_and_ready():
    a, b = fx(), fx("workmap_ap_expert2.json")
    assert len(a.steps) == 7 and len(a.guardrails) == 4
    assert sum(1 for s in a.steps if s.decision and s.decision.kind == "judgment") == 3
    assert common.compute_coverage(a).status == "ready"
    assert not common.validate_evidence(a)
    assert b.workflow_id == a.workflow_id


# ---------------- builder ----------------

def _log_capture(sid="cap1"):
    st = d.store()
    for i, (view, ent) in enumerate([("Purchase Invoice list", None), ("Purchase Invoice form", "ACC-PINV-4471"),
                                     ("Purchase Invoice form", "ACC-PINV-4471")]):
        st.log(sid, "screen.state", {"seq": i, "t": i * 10000.0, "app": "ERPNext", "view": view,
                                     "entity_type": "Purchase Invoice", "entity_id": ent, "keyframe_id": f"kf{i}",
                                     "fields": [{"label": "Betrag", "value": "5.200,00"}]}, i * 10000.0)
    st.log(sid, "screen.events", [{"id": "e1", "seq": 2, "t": 20000.0, "kind": "edit", "field": "Expense Account",
                                   "canonical": "line.expense_account", "old": "Office", "new": "Capital Equipment",
                                   "entity_id": "ACC-PINV-4471", "keyframe_id": "kf2",
                                   "summary": "Expense account changed on invoice ACC-PINV-4471"}], 20000.0)
    st.log(sid, "utterance", {"event_id": "u1", "t_start": 1000.0, "role": "user", "text": "Ich öffne die Rechnung.",
                              "lang": "de"}, 1000.0)
    st.log(sid, "utterance", {"event_id": "u2", "t_start": 21000.0, "role": "user",
                              "text": "Ausrüstung über 5.000 ist Capex.", "lang": "de"}, 21000.0)
    st.log(sid, "control", {"action": "off_record_on"})
    st.log(sid, "utterance", {"event_id": "u3", "t_start": 22000.0, "role": "user", "text": "private"}, 22000.0)
    st.log(sid, "control", {"action": "off_record_off"})


def _raw_map(with_step3_moment=False):
    return {
        "id": "x", "workflow_id": "TBD", "name": "Supplier invoice", "apps": ["ERPNext"],
        "steps": [
            {"id": "s1", "order": 1, "title": "Open invoice ACC-PINV-4471",
             "state_signature": {"app": "ERPNext", "view": "Purchase Invoice list", "entity_type": "Purchase Invoice"},
             "moment": {"session_id": "cap1", "keyframe_ids": ["kf0"], "t": 0, "utterance_ids": ["u1"]}},
            {"id": "s2", "order": 2, "title": "Set expense account", "after": ["s1"],
             "state_signature": {"app": "ERPNext", "view": "Purchase Invoice form", "entity_type": "Purchase Invoice"},
             "moment": {"session_id": "cap1", "keyframe_ids": ["kf2"], "t": 20000, "utterance_ids": []},
             "decision": {"kind": "judgment", "description": "Equipment over 5,000 → capex", "to_value": "Capital Equipment",
                          "reason_quote_ids": ["u2"]}},
            {"id": "s3", "order": 3, "title": "Submit", "after": ["s2"],
             "moment": ({"session_id": "cap1", "keyframe_ids": ["kf2"], "t": 20000, "utterance_ids": ["u2"]}
                        if with_step3_moment else None)},
        ],
        "guardrails": [{"id": "g1", "text": "Equipment over 5,000 is capex", "quote_ids": ["u2"],
                        "predicate": {">": [{"var": "line.amount"}, 5000]},
                        "evidence": [{"session_id": "cap1", "keyframe_ids": ["kf2"], "t": 20000, "utterance_ids": ["u2"]}]},
                       {"id": "g2", "text": "Hallucinated rule", "quote_ids": [], "evidence": []}],
        "canonical_vars": {"line.amount": ["Betrag", "Amount"]},
    }


async def test_builder_validates_repairs_and_opens_gaps(monkeypatch, env):
    _log_capture()
    calls = []

    async def fake_chat(messages, *, model_role="smart", json_schema=None):
        calls.append(messages)
        if json_schema is WorkMap:
            return _raw_map()
        if isinstance(json_schema, dict):  # translations
            return {"q_u2": {"en": "Equipment over 5,000 is capex.", "ru": "Оборудование > 5000 — капзатраты."}}
        return None
    monkeypatch.setattr(d, "chat", fake_chat)
    wm = await builder.build_map("cap1", workflow_id="wf_test",
                                 expert=User(id="u_anna", name="Anna Keller", role="expert"))
    assert len([c for c in calls if "failed validation" in c[-1]["content"]]) == 1  # one repair retry
    s1, s2, s3 = sorted(wm.steps, key=lambda s: s.order)
    assert "4471" not in s1.title and "invoice" in s1.title.lower()  # generalized
    assert s2.moment.utterance_ids == ["u2"]  # nearest utterance repair
    assert s3.moment is None
    gap_entities = {u.entity for u in wm.open_unknowns if u.type == "coverage"}
    assert {"s3", "g2"} <= gap_entities
    q = next(q for q in wm.quotes if q.id == "q_u2")
    assert q.lang == "de" and q.translations["ru"].startswith("Оборудование")
    assert all("private" not in q.text for q in wm.quotes)  # off-record skipped
    assert wm.coverage.status == "partial"
    assert common.load_map("wf_test") is not None
    assert any(t == "map.updated" for _, t, _ in env.msgs)


async def test_builder_offline_fallback():
    _log_capture("cap2")
    wm = await builder.build_map("cap2", workflow_id="wf_off", expert=User(id="e", name="E", role="expert"))
    assert wm.steps and wm.workflow_id == "wf_off"


# ---------------- merge ----------------

async def test_merge_conflict_partial_order_attribution():
    m = await merge.merge_maps([fx(), fx("workmap_ap_expert2.json")])
    assert len(m.steps) == 7
    dec = next(s for s in m.steps if s.conflict)
    assert dec.id == "s5" and "Hold" in dec.conflict and "credit note" in dec.conflict
    conf = [u for u in m.open_unknowns if u.type == "conflict"]
    assert len(conf) == 2
    g1 = next(g for g in m.guardrails if g.id == "g1")
    assert set(g1.experts) == {"u_anna", "u_marco"}
    s4 = next(s for s in m.steps if s.id == "s4")
    assert set(s4.after) == {"s2", "s3"}  # order of s2/s3 differs between experts → partial order
    cons = merge.consensus(m)
    assert cons["s5"] < cons["s4"] == 1.0
    assert m.coverage.status == "partial" and m.coverage.conflicts == 1


# ---------------- debrief ----------------

async def test_debrief_flow(env):
    wm = fx()
    wm.approved_by = []
    wm.open_unknowns = [
        common.new_unknown("coverage", "What about credit notes?", priority=0.9),
        common.new_unknown("why", "Why cost center 0400?", entity="s4", priority=0.5),
        common.new_unknown("limit", "Is 5,000 the limit?", entity="g1", priority=0.1),
        common.new_unknown("why", "What is capex?", scope="universal", priority=1.0),
    ]
    await builder.publish_expert_map(wm)
    sess = type("S", (), {"id": "deb1", "lang": "en", "workflow_id": "wf_ap_invoice", "mode": "debrief",
                          "user": User(id="u_anna", name="Anna Keller", role="expert")})()
    debrief.start("deb1", "wf_ap_invoice")
    q1 = await debrief.next_debrief_utterance(sess)
    assert q1 == "Is 5,000 the limit?"  # guardrails → exceptions → unseen; universal skipped
    r = await debrief.handle_debrief_answer(sess, "Yes, net amount over 5,000.")
    assert "Why cost center 0400?" in r
    r = await debrief.handle_debrief_answer(sess, "not now")
    assert "credit notes" in r
    r = await debrief.handle_debrief_answer(sess, "We never pay without a credit note.")
    # ledger empty → teach-back
    st = debrief.get_state(sess)
    assert "[[step:s1]]" in r and debrief._word_count(st.script) <= 140 and r.endswith("right?")
    assert st.phase == "teach_back"
    parts = debrief.split_markers(r)
    assert parts[1][0] == "s1"
    m = common.load_map("wf_ap_invoice")
    assert next(u for u in m.open_unknowns if u.spoken_question == "Why cost center 0400?").status == "deferred"
    assert any(q.source == "debrief" for q in m.quotes)
    # correction patches only affected step
    before = {s.id: s.model_dump() for s in m.steps}
    r = await debrief.handle_debrief_answer(sess, "No, step 5: hold only if the statement shows a duplicate.")
    assert "→" in r and "[[step:s5]]" in r
    after = common.load_map("wf_ap_invoice")
    changed = [s.id for s in after.steps if s.model_dump() != before[s.id]]
    assert changed == ["s5"]
    # confirm → exam
    r = await debrief.handle_debrief_answer(sess, "Yes, that's right")
    assert "Case 1" in r and st.phase == "exam"
    assert len(st.exam) == 3
    assert "capex" in st.exam[0].predicted.lower() or "Capital" in st.exam[0].predicted
    r = await debrief.handle_debrief_answer(sess, "yes")
    r = await debrief.handle_debrief_answer(sess, "no, it should go on hold")
    r = await debrief.handle_debrief_answer(sess, "correct")
    final = common.load_map("wf_ap_invoice")
    verdicts = [e.expert_verdict for e in final.exam[-3:]]
    assert verdicts == ["correct", "wrong", "correct"] and final.exam[-2].correction
    assert "u_anna" in final.approved_by


def test_exam_boundaries():
    cases = debrief.make_exam(fx(), "en")
    assert len(cases) == 3
    assert cases[0].confidence > 0.5


# ---------------- tutor ----------------

def _state(seq, view="Purchase Invoice form", fields=(), ent="PINV-1", app="ERPNext"):
    return {"seq": seq, "t": seq * 1000.0, "app": app, "view": view, "entity_type": "Purchase Invoice",
            "entity_id": ent, "keyframe_id": f"lk{seq}", "confidence": 0.9,
            "fields": [{"label": k, "value": v} for k, v in fields]}


async def test_tutor_guardrail_intervention_multilingual(env):
    await seed()
    tutor.start("learn1", "wf_ap_invoice", "ru", {"id": "l1", "name": "Ivan", "role": "learner"})
    await tutor.on_screen_state("learn1", _state(1, "Purchase Invoice list"))
    assert env.out("highlight_step")[-1]["step_id"] == "s1"
    fields = [("Artikelgruppe", "Ausrüstung"), ("Betrag", "5.200,00"), ("Aufwandskonto", "Büromaterial")]
    await tutor.on_screen_state("learn1", _state(2, fields=fields))
    iv = env.out("intervene")
    assert iv and iv[0]["guardrail_id"] == "g1"
    assert iv[0]["text"].startswith("Anna") and "почему" in iv[0]["text"]
    assert iv[0]["moment"]["keyframe_ids"]
    assert tutor.get_intervention("g1", "learn1") == iv[0]["text"]
    assert tutor.get_intervention("learn1", "g1") == iv[0]["text"]
    # learner answers the "why" → expert quote in learner language
    r = await tutor.handle_intent("learn1", "answer_prediction", "потому что это оборудование дороже 5000")
    assert "Anna" in r and "капзатраты" in r
    # same entity → no repeated intervention
    await tutor.on_screen_state("learn1", _state(3, fields=fields))
    assert len(env.out("intervene")) == 1
    assert tutor.load_mastery("l1", "wf_ap_invoice")["s4"].level == "caught"


async def test_tutor_fuzzy_threshold_and_check_my_work(monkeypatch, env):
    await seed()
    tutor.start("learn2", "wf_ap_invoice", "en")
    conf = {"v": 0.6}

    async def fake_decide(q, context, options):
        return "violation", conf["v"]
    monkeypatch.setattr(d, "decide", fake_decide)
    st = tutor.get_state("learn2")
    st.current = "s2"
    st.last_state = ScreenState.model_validate(_state(1, fields=[("Supplier", "Unknown GmbH")]))
    await tutor.on_screen_events("learn2", [{"id": "e1", "seq": 1, "t": 1, "kind": "select", "summary": "supplier"}])
    assert not env.out("intervene")
    conf["v"] = 0.8
    st.last_state = ScreenState.model_validate(_state(2, view="Purchase Invoice form", ent="PINV-2",
                                                      fields=[("Supplier", "Unknown GmbH")]))
    st.current = "s2"
    await tutor.check_guardrails(st, tutor._wm(st))
    assert env.out("intervene")[-1]["guardrail_id"] == "g4"
    # check_my_work: deterministic predicates only
    st.last_state = ScreenState.model_validate(_state(3, ent="PINV-3", fields=[
        ("Supplier", "Nordwind Logistik GmbH"), ("Posting Date", "15.12.2026")]))
    r = await tutor.handle_intent("learn2", "check_my_work", "")
    assert env.out("intervene")[-1]["guardrail_id"] == "g2" and "would stop here" in r


async def test_tutor_prediction_idle_hint_and_intents(env):
    await seed("workmap_ap.json", "workmap_ap_expert2.json")
    tutor.start("learn3", "wf_ap_invoice", "de", {"id": "l3", "name": "Lea", "role": "learner"})
    st = tutor.get_state("learn3")
    wm = tutor._wm(st)
    # novice (BKT p=0.2) at s2 → worked example: expert moment + quote
    await tutor._enter_step(st, wm, common.step_by_id(wm, "s2"))
    assert env.out("show_moment")[-1]["step_id"] == "s2" and env.out("ask")[-1]["unknown_id"] == "ex-s2"
    # leaving s2 unaided raises P(L); a mid-mastery learner gets a prediction prompt at s4
    tutor.observe(st, "s4", True)
    assert 0.4 <= tutor.bkt_p(st, "s4") < 0.8
    await tutor._enter_step(st, wm, common.step_by_id(wm, "s4"))
    assert tutor.bkt_p(st, "s2") > 0.2
    ask = env.out("ask")[-1]
    assert ask["unknown_id"] == "pred-s4" and "warum" in ask["text"]
    assert d.get_prewritten("pred-s4", "learn3")
    st.last_activity -= 25_000
    assert await tutor.check_idle("learn3") is True
    assert env.out("ask")[-1]["unknown_id"] == "hint-s4"
    assert await tutor.check_idle("learn3") is False  # offered once
    r = await tutor.handle_intent("learn3", "answer_prediction", "Capital Equipment Konto weil Ausrüstung über 5000")
    assert r.startswith("Genau") and tutor.load_mastery("l3", "wf_ap_invoice")["s4"].level == "unaided"
    assert tutor.bkt_p(st, "s4") >= 0.8
    rungs = [await tutor.handle_intent("learn3", "hint", "") for _ in range(4)]
    assert rungs[0].startswith("Schritt 4") and rungs[1].startswith("Equipment lines") and rungs[2] == rungs[3]
    r = await tutor.handle_intent("learn3", "what_next", "")
    assert "Schritt 5" in r and "Experten uneinig" in r
    st.current = "s1"
    r = await tutor.handle_intent("learn3", "why_this", "")
    assert "noch niemand gezeigt" in r  # never invent rules
    r = await tutor.handle_intent("learn3", "ask_expert", "Was tun bei Gutschriften?")
    assert "Experten gefragt" in r
    reqs = d.store().kv_list("requests")
    assert reqs and reqs[0]["requested_by"]["id"] == "l3"


async def test_tutor_novel_case_becomes_unknown(env):
    await seed()
    tutor.start("learn4", "wf_ap_invoice", "en")
    await tutor.on_screen_state("learn4", {**_state(1), "view": "Payment Entry form", "entity_type": "Payment Entry"})
    m = common.load_map("wf_ap_invoice")
    assert any(u.type == "coverage" and "Payment Entry" in (u.entity or "") for u in m.open_unknowns)


# ---------------- lookup / requests / export / mcp ----------------

async def test_lookup_and_requests(env):
    await seed()
    r = await lookup.lookup("walk me through this purchase invoice",
                            ScreenState.model_validate(_state(1)), "en")
    assert r["match"]["workflow_id"] == "wf_ap_invoice" and r["action"] == "learn"
    r = await lookup.lookup("how do I close a support ticket", None, "en")
    assert r["match"] is None and r["action"] == "request"
    app = FastAPI()
    app.include_router(lookup.router)
    app.include_router(export.router)
    c = TestClient(app)
    rq = c.post("/api/requests", json={"workflow_hint": "close a ticket",
                                       "requested_by": {"id": "l1", "name": "L", "role": "learner"}}).json()
    assert rq["status"] == "open" and rq["workflow_id"].startswith("wf_close_a_ticket")
    assert c.get("/api/requests").json()[0]["id"] == rq["id"]
    acc = c.post(f"/api/requests/{rq['id']}/accept", json={"user": {"id": "e", "name": "E", "role": "expert"}}).json()
    assert acc["request"]["status"] == "accepted" and acc["workflow_id"] == rq["workflow_id"]
    md = c.get("/api/export/wf_ap_invoice.skill.md").text
    assert md.startswith("---\nname: wf_ap_invoice") and "json-logic" in md and "Stop conditions" in md
    assert c.get("/api/workflows/wf_ap_invoice/coverage").json()["status"] == "ready"


async def test_mcp_tools():
    await seed()
    r = mcp.check_action({"line.item_group": "Equipment", "line.amount": 7300, "line.expense_account": "Office"},
                         "wf_ap_invoice")
    assert not r["allowed"] and r["violations"][0]["id"] == "g1"
    assert mcp.find_guardrails("capex equipment")[0]["id"] == "g1"
    assert mcp.quote("s5", "wf_ap_invoice", "de")[0]["text_in_lang"].startswith("Nordwind")
    assert mcp.build_server() is not None


def test_boundary_probes():
    wm = fx()
    for g in wm.guardrails:
        g.approved = False
    probes = debrief.boundary_probes(wm, "de")
    assert probes and "4,999" in probes[0].spoken_question and probes[0].entity == "g1"


async def test_self_consistency_drops_minority_step(monkeypatch):
    _log_capture("cap3")
    n = {"i": 0}

    async def fake_chat(messages, *, model_role="smart", json_schema=None):
        if json_schema is not WorkMap:
            return None
        n["i"] += 1
        raw = _raw_map(with_step3_moment=True)
        if n["i"] == 1:
            raw["steps"].append({"id": "s9", "order": 4, "title": "Phone the auditor about vacation photos",
                                 "moment": None})
        return raw
    monkeypatch.setattr(d, "chat", fake_chat)
    wm = await builder.build_map("cap3", workflow_id="wf_sc", expert=User(id="e", name="E", role="expert"))
    assert "s9" not in {s.id for s in wm.steps}
    assert any(u.entity == "s9" for u in wm.open_unknowns)


async def test_guardrail_specs_and_submit_rearm(env):
    await seed()
    specs = common.compile_guardrails(common.load_map("wf_ap_invoice"))
    g1 = next(x for x in specs if x["id"] == "g1")
    assert g1["enforce"] == "block" and g1["scope"] == ["s4"] and g1["deterministic"] and g1["exceptions"]
    tutor.start("learn5", "wf_ap_invoice", "en")
    fields = [("Item Group", "Equipment"), ("Amount", "7,300.00"), ("Expense Account", "Office Supplies")]
    await tutor.on_screen_state("learn5", _state(1, fields=fields))
    await tutor.on_screen_state("learn5", _state(2, fields=fields))
    assert len(env.out("intervene")) == 1
    await tutor.on_screen_events("learn5", [{"id": "e9", "seq": 3, "t": 3, "kind": "submit", "summary": "submit"}])
    assert len(env.out("intervene")) == 2  # hard stop again before submit

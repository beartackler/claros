"""Learner nudges (v2.1): graded on THE LEARNER'S record (cases the expert never showed), every response a signal."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claros.knowledge import _deps as d
from claros.knowledge import builder, common, nudges, tutor
from claros.models import ScreenEvent, ScreenState, WorkMap

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
    monkeypatch.setattr(d, "_llm", lambda: None)

    async def no_onet(text, **kw):
        return None
    monkeypatch.setattr(d, "onet_match", no_onet)

    async def no_decide(q, context, options, timeout=None):
        return None, 0.0
    monkeypatch.setattr(d, "decide", no_decide)
    spoken: list[str] = []

    async def speak(st, text):
        spoken.append(text)
    monkeypatch.setattr(nudges, "_speak", speak)
    bus.spoken = spoken
    d.PREWRITTEN.clear()
    tutor._states.clear()
    yield bus
    d.STORE = None
    d.BUS = None


async def seed(*names):
    for n in names or ("workmap_ap.json",):
        await builder.publish_expert_map(WorkMap.model_validate(json.loads((FIX / n).read_text())))


def record(ent, fields, view="Purchase Invoice form"):
    return ScreenState.model_validate({
        "seq": 1, "t": 1.0, "app": "ERPNext", "view": view, "entity_type": "Purchase Invoice", "entity_id": ent,
        "confidence": 0.9, "fields": [{"label": k, "value": v} for k, v in fields]})


async def learner(sid="L", lang="en", ent="PINV-9", fields=()):
    await seed()
    tutor.start(sid, "wf_ap_invoice", lang, {"id": f"u_{sid}", "name": "Lea", "role": "learner"})
    st = tutor.get_state(sid)
    st.last_state = record(ent, fields)
    return st, tutor._wm(st)


def opt(n, label_part):
    return next(o for o in n.options if label_part.lower() in o.label.lower())


# ---------------- grading on unseen cases ----------------

async def test_unseen_case_below_the_limit_grades_the_default(env):
    # an equipment line the expert never showed, under the limit: the default account is right, NOT the expert's
    # demonstrated capex value
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "3.140,00")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    n = st.nudge
    assert n.graded and opt(n, "expense").correct and not opt(n, "capital").correct
    # learner says the capex option out loud → wrong for THIS record, explained with the expert's words
    r = await nudges.on_voice("L", "capital equipment")
    assert r["outcome"] == "incorrect" and r["show_reference"]
    assert "Not quite" in env.spoken[-1] and "Anna" in env.spoken[-1]
    assert tutor.load_mastery("u_L", "wf_ap_invoice")["s4"].level == "caught"


async def test_unseen_case_over_the_limit_click(env):
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "12.400,00")])
    msg = await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    assert msg["type"] == "nudge" and msg["allow_dont_know"] and msg["reference"]["quote"]["speaker"] == "Anna"
    assert all("correct" not in o for o in msg["options"])  # the answer never goes to the client
    good = opt(st.nudge, "capital")
    await nudges.on_response("L", {"id": msg["id"], "via": "click", "choice_id": good.id})
    res = env.out("nudge_result")[-1]
    assert res["outcome"] == "correct" and not res["show_reference"]
    assert env.spoken[-1] == "Yes — that's what Anna does."
    assert tutor.load_mastery("u_L", "wf_ap_invoice")["s4"].level == "unaided"


async def test_no_facts_on_screen_is_not_graded(env):
    st, wm = await learner(fields=[("Supplier", "Fresh Supplier AG")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    assert not st.nudge.graded
    r = await nudges.on_voice("L", "the first one")
    assert r["outcome"] == "noted" and r["show_reference"]
    assert "can't tell" in env.spoken[-1]
    assert "s4" not in tutor.load_mastery("u_L", "wf_ap_invoice")  # no verdict → no mastery change


async def test_decision_model_reads_the_record_when_rules_are_fuzzy(env, monkeypatch):
    st, wm = await learner(fields=[("Supplier", "Brand New Parts s.r.o.")])

    async def decide(q, context, options, timeout=None):
        assert "Brand New Parts" in context and "cannot_tell" in options
        return next(o for o in options if o.lower().startswith("ask")), 0.9
    monkeypatch.setattr(d, "decide", decide)
    await nudges.predict(st, wm, common.step_by_id(wm, "s2"), wait=False)
    n = st.nudge
    assert n.graded and next(o for o in n.options if o.correct).action == "escalate"


async def test_hold_rule_other_supplier_in_december(env):
    # December, but a supplier with no double-billing history (never shown): submitting is right
    st, wm = await learner(fields=[("Supplier", "Kessler Metallbau"), ("Posting Date", "11.12.2026")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s5"), wait=False)
    assert opt(st.nudge, "submit").correct and not opt(st.nudge, "hold").correct


# ---------------- every response is a signal ----------------

async def test_voice_ordinals_dont_know_and_languages(env):
    st, wm = await learner(lang="ru", fields=[("Группа номенклатуры", "Оборудование"), ("Сумма", "9 800,00")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    n = st.nudge
    assert await nudges.match_choice(n, "второй", "ru") == {"choice_id": n.options[1].id}
    assert await nudges.match_choice(n, "the first one", "en") == {"choice_id": n.options[0].id}
    assert await nudges.match_choice(n, "почему так?", "ru") is None  # a question back is not an answer
    r = await nudges.on_voice("L", "честно, не знаю")
    assert r["outcome"] == "dont_know" and r["show_reference"]
    assert tutor.load_mastery("u_L", "wf_ap_invoice")["s4"].level == "hinted"
    assert st.hint_rung["s4"] >= 2


async def test_three_dismissals_switch_to_just_watch(env):
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "6.000,00")])
    for i in range(3):
        await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
        await nudges.on_response("L", {"id": st.nudge.id, "via": "close", "choice_id": None})
        assert env.out("nudge_result")[-1]["outcome"] == "skipped"
    assert st.quiet and env.spoken == ["Okay, I'll talk less and just watch. Ask me anytime."]
    assert await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False) is None


async def test_acting_in_the_app_answers_the_card(env):
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "8.750,00")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    ev = ScreenEvent(id="e1", seq=2, t=2.0, kind="select", canonical="line.expense_account",
                     new="Capital Equipment - CD", summary="Expense Account → Capital Equipment - CD")
    await tutor.on_screen_events("L", [ev.model_dump()])
    assert env.out("nudge_result")[-1]["outcome"] == "implicit_correct"


async def test_hard_stop_wins_over_the_card(env):
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "7.200,00")])
    await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
    st.last_state = record("PINV-9", [("Item Group", "Equipment"), ("Amount", "7.200,00"),
                                      ("Expense Account", "Office Supplies")])
    await tutor.check_guardrails(st, wm, fuzzy=False)
    assert env.out("intervene")[-1]["guardrail_id"] == "g1"
    assert env.out("nudge_result")[-1]["outcome"] == "implicit_incorrect"


async def test_divergence_becomes_a_novel_case_for_the_expert(env):
    st, wm = await learner(ent="PINV-77")
    st.visited = {"s1"}
    st.current = "s1"
    await tutor._enter_step(st, wm, common.step_by_id(wm, "s4"))
    await st.nudge_task
    nd = env.out("nudge")[-1]
    assert nd["kind"] == "diverge" and nd["step_id"] == "s3" and "on purpose" in nd["question"]
    await nudges.on_response("L", {"id": nd["id"], "via": "click", "choice_id": "purpose"})
    assert env.out("nudge_result")[-1]["outcome"] == "correct"
    reqs = d.store().kv_list("requests")
    assert reqs and "Lea skipped" in reqs[0]["workflow_hint"] and "PINV-77" in reqs[0]["workflow_hint"]
    assert any((u.entity or "").startswith("novel:s3") for u in tutor._wm(st).open_unknowns)


async def test_experts_differ_every_attributed_option_is_valid(env):
    await seed("workmap_ap.json", "workmap_ap_expert2.json")
    tutor.start("L2", "wf_ap_invoice", "en", {"id": "u2", "name": "Lea", "role": "learner"})
    st = tutor.get_state("L2")
    wm = tutor._wm(st)
    step = next((s for s in wm.steps if s.conflict and s.variants), None)
    if step is None:
        pytest.skip("fixture merge produced no attributed conflict")
    st.last_state = record("PINV-5", [])
    msg = await nudges.predict(st, wm, step, wait=False)
    assert all(o.get("expert_id") for o in msg["options"])
    await nudges.on_response("L2", {"id": msg["id"], "via": "key", "choice_id": msg["options"][-1]["id"]})
    assert env.out("nudge_result")[-1]["outcome"] == "correct" and "experts differ" in env.spoken[-1]


async def test_low_confidence_match_asks_before_assuming(env):
    st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", "5.900,00")])
    tutor.observe(st, "s4", True)  # mid mastery → prediction territory
    await tutor._enter_step(st, wm, common.step_by_id(wm, "s4"), score=0.67)
    await st.nudge_task
    nd = env.out("nudge")[-1]
    assert nd["kind"] == "confirm_step" and nd["reference"] is None
    await nudges.on_response("L", {"id": nd["id"], "via": "voice", "choice_id": "yes"})
    await st.nudge_task  # yes → the prediction card follows
    assert env.out("nudge")[-1]["kind"] == "predict"


async def test_verdict_reads_the_case_not_the_learners_choice(env):
    # the record already carries a choice; the right answer depends on the facts (equipment, amount) only
    for amount, account, right in [("9.100,00", "Capital Equipment - CD", "capital"),
                                   ("9.100,00", "Office Supplies - CD", "capital"),
                                   ("2.300,00", "Capital Equipment - CD", "expense")]:
        tutor._states.clear()
        st, wm = await learner(fields=[("Item Group", "Equipment"), ("Amount", amount), ("Expense Account", account)])
        await nudges.predict(st, wm, common.step_by_id(wm, "s4"), wait=False)
        assert [o.label.lower().split()[0] for o in st.nudge.options if o.correct] == [right], (amount, account)


def test_case_part_drops_choice_conditions():
    wm = WorkMap.model_validate(json.loads((FIX / "workmap_ap.json").read_text()))
    g = common.guardrail_by_id(wm, "g1")
    cv = nudges.choice_vars(g, wm, ["Capital Equipment", "Expense account"])
    assert cv == {"line.expense_account"}
    cp = nudges.case_part(g.predicate, cv)
    assert "line.expense_account" not in json.dumps(cp) and "line.amount" in json.dumps(cp)

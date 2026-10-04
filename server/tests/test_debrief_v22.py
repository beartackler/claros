"""Debrief v2.2: ≥3 follow-ups about what was NOT answered live, learner items capped/after, conflicts for this
expert first, probe answers patch the rule, teach-back ≤140 words, diff-only readback, confirm publishes (no exam)."""
from __future__ import annotations

import json

import pytest

from claros.knowledge import _deps as d
from claros.knowledge import builder, common, debrief
from claros.models import Decision, Step, Unknown, User, Variant

from test_knowledge import fx  # noqa: F401  (fixture loader)
from test_knowledge import env  # noqa: F401  (autouse store/bus/llm fixture)

ANNA = User(id="u_anna", name="Anna Keller", role="expert")
BEN = User(id="u_ben", name="Ben Ott", role="expert")


def _sess(sid, user=ANNA, lang="en"):
    return type("S", (), {"id": sid, "lang": lang, "workflow_id": "wf_ap_invoice", "mode": "debrief", "user": user})()


async def _questions(sess, answer="That's how we do it here, always.", limit=10):
    """Run the question block; returns the spoken questions (stripped of the 'Got it.' ack)."""
    out = [await debrief.next_debrief_utterance(sess)]
    st = debrief.get_state(sess)
    while st.phase == "questions" and len(out) < limit:
        r = await debrief.handle_debrief_answer(sess, answer)
        if st.phase != "questions":
            return out, r
        out.append(r.replace("Got it. ", "", 1))
    return out, None


async def _publish(wm):
    wm.approved_by = []
    for g in wm.guardrails:
        g.approved = False
    await builder.publish_expert_map(wm)


async def test_three_followups_without_ledger_leftovers():
    wm = fx()
    wm.open_unknowns = []
    await _publish(wm)
    s = _sess("v1")
    debrief.start("v1", "wf_ap_invoice")
    qs, teach = await _questions(s)
    assert 3 <= len(qs) <= 6, qs
    for q in qs:
        assert len(q.split()) <= 28 and "{" not in q and "Case" not in q and q.rstrip().endswith("?"), q
    assert any("Nordwind" in q and "every supplier" in q for q in qs)  # named party → every party?
    assert any("5,000" in q and "equipment" in q.lower() for q in qs)  # what distinguishes the case
    assert teach and "[[step:" in teach


async def test_learner_items_capped_and_after_core():
    wm = fx()
    lu = [common.new_unknown("coverage", f"A learner reached “Screen {i}” — not in your map. What should they do?",
                             entity=f"ERPNext|x{i}", priority=0.99) for i in range(2)]
    lu[0].meta["origin"] = "learner"
    novel = common.new_unknown("coverage", "Lea skipped matching, on purpose. Is that OK here?",
                               entity="novel:s3:INV-9", priority=0.99)
    wm.open_unknowns = lu + [novel]
    await _publish(wm)
    s = _sess("v2")
    debrief.start("v2", "wf_ap_invoice")
    qs, _ = await _questions(s)
    learner = [i for i, q in enumerate(qs) if "learner" in q.lower() or "Lea " in q]
    assert len(learner) == 1 and learner[0] == len(qs) - 1, qs
    assert len(qs) - 1 >= 3  # the learner item does not count toward the three


async def test_conflict_for_this_expert_first_and_resolved():
    wm = fx()
    s5 = common.step_by_id(wm, "s5")
    s5.conflict = "Anna does “Hold” because: Nordwind bills twice — Ben does “Submit” because: credit note"
    s5.variants.append(Variant(expert_id="u_ben", description="Submit with the credit note", reason_quote_ids=[]))
    wm.experts.append(BEN)
    mine = Unknown(id="u_c1", type="conflict", scope="company", entity="s5", priority=0.9,
                   spoken_question="Anna holds December invoices; you submitted with a credit note — why?",
                   meta={"origin": "merge", "ask_expert_id": "u_ben", "ask_expert_name": "Ben Ott",
                         "other_expert_name": "Anna Keller", "other_did": "Hold", "this_did": "Submit with the credit note",
                         "step_title": s5.title})
    legacy_other = Unknown(id="u_c2", type="conflict", scope="company", entity="s5", priority=0.9,
                           hypothesis="Anna Keller: Hold", spoken_question="Ben does Submit, you do Hold. Why?")
    wm.open_unknowns = [legacy_other, mine]
    await _publish(wm)
    s = _sess("v3", BEN)
    debrief.start("v3", "wf_ap_invoice")
    q1 = await debrief.next_debrief_utterance(s)
    assert q1.startswith("Anna holds December invoices")
    await debrief.handle_debrief_answer(s, "When the supplier already sent a credit note I submit, otherwise I hold.")
    m = common.load_map("wf_ap_invoice")
    st5 = common.step_by_id(m, "s5")
    assert st5.conflict is None and "credit note" in st5.decision.counterfactual
    v = next(v for v in st5.variants if v.expert_id == "u_ben")
    assert v.reason_quote_ids and v.reason_quote_ids[0] in {q.id for q in m.quotes}
    assert next(u for u in m.open_unknowns if u.id == "u_c2").status == "resolved"
    # Anna's own debrief does not get Ben's question
    assert all(u.id != "u_c1" for u in debrief.plan(m, "u_anna", "Anna Keller"))


async def test_scope_probe_llm_patch_replaces_literal(monkeypatch):
    wm = fx()
    wm.open_unknowns = []
    await _publish(wm)
    new_pred = {"!": {"in": ["Claros Demo GmbH", {"var": "invoice.company"}]}}

    async def fake_chat(messages, *, model_role="smart", json_schema=None):
        if json_schema is debrief.PATCH_G_SCHEMA:
            return {"text": "Invoices for any company except the parent need a second approval.",
                    "predicate": new_pred, "owner": None, "changed": True}
        return None
    monkeypatch.setattr(d, "chat", fake_chat)
    m = common.load_map("wf_ap_invoice")
    g3 = common.guardrail_by_id(m, "g3")
    u = next(x for x in debrief.make_probes(m) if x.entity == "g3")
    q = debrief.Quote(id="q1", speaker="Anna", speaker_id="u_anna", lang="en", t=0, session_id="x",
                      text="Every company except the parent, Claros Demo GmbH, needs it.")
    diffs = await debrief.patch_guardrail(m, g3, u, q.text, q, "Anna Keller", "en")
    assert g3.predicate == new_pred and "q1" in g3.quote_ids and {x["field"] for x in diffs} >= {"predicate", "text"}
    # a literal the expert never said is refused → deterministic fallback (widen: drop the named company)
    g3.predicate = {"in": [{"var": "invoice.company"}, ["Claros Demo Austria GmbH"]]}

    async def bad_chat(messages, *, model_role="smart", json_schema=None):
        if json_schema is debrief.PATCH_G_SCHEMA:
            return {"text": "x", "predicate": {"in": ["Ltd", {"var": "invoice.company"}]}, "changed": True}
        return None
    monkeypatch.setattr(d, "chat", bad_chat)
    await debrief.patch_guardrail(m, g3, u, "Every subsidiary, all of them.", q, "Anna Keller", "en")
    assert g3.predicate is None and g3.fuzzy and "every subsidiary" in g3.text.lower()


async def test_teachback_limit_and_template_covers_all_rules(monkeypatch):
    wm = fx()
    tb = debrief._template_teachback(wm, "en")
    assert debrief._word_count(tb) <= 140 and tb.endswith("right?")
    for g in wm.guardrails:
        assert g.text.split()[0] in tb
    for s in wm.steps:
        if s.decision and s.decision.kind == "judgment":
            assert f"[[step:{s.id}]]" in tb

    async def long_chat(messages, *, model_role="smart", json_schema=None):
        return "[[step:s1]] " + "First we open it and look closely. " * 60
    monkeypatch.setattr(d, "chat", long_chat)
    out = await debrief.teach_back(wm, "en")
    assert debrief._word_count(out) <= 140 and out.endswith("right?")


async def test_correction_readback_has_no_json(monkeypatch):
    wm = fx()
    await _publish(wm)
    s = _sess("v6")
    st = debrief.start("v6", "wf_ap_invoice")
    st.phase = "teach_back"

    async def patch_chat(messages, *, model_role="smart", json_schema=None):
        if json_schema is debrief.PATCH_SCHEMA:
            return {"patches": [{"target": "guardrail", "id": "g1", "field": "predicate",
                                 "new": {">": [{"var": "line.amount"}, 6000]}},
                                {"target": "guardrail", "id": "g4", "field": "owner", "new": "Frank"}]}
        return None
    monkeypatch.setattr(d, "chat", patch_chat)
    r = await debrief.handle_debrief_answer(s, "No, the limit is 6,000 and Frank is the one to ask.")
    assert "{" not in r and "var" not in r and "Frank" in r and "when that rule applies" in r
    assert st.phase == "teach_back"
    # "yes, but …" is a correction, not a confirmation
    assert not debrief.is_confirm("Yes, but step 2 only applies to new suppliers", None)
    assert debrief.is_confirm("Yes, that's how it works.", None)
    r = await debrief.handle_debrief_answer(s, "yes, that's how it works")
    assert st.phase == "done" and "published" in r
    assert common.load_map("wf_ap_invoice").approved_by == ["u_anna"]


def test_topic_of_matches_leftover_to_rule_by_wording():
    from claros.knowledge import debrief
    from claros.models import Guardrail, Unknown, WorkMap
    wm = WorkMap(id="w", workflow_id="wf", name="x", guardrails=[
        Guardrail(id="gd", text="Hold invoices whose amount matches one already paid; ask the supplier"),
        Guardrail(id="gu", text="UK subsidiary invoices need a second approval")])
    a = Unknown(id="a", type="deliberate", spoken_question="Do you hold same-amount repeats from that supplier too?")
    b = Unknown(id="b", type="why", entity="gd", spoken_question="What tells you it's a double bill?")
    assert debrief.topic_of(wm, a) == debrief.topic_of(wm, b) == "gd"

"""Regression tests for generic bugs found by the adversarial eval suite (infra/evals, docs/EVALS.md)."""
from __future__ import annotations

import asyncio

import pytest

from claros.brain.lang import detect_lang
from claros.knowledge.builder import _drop_identifier_literals
from claros.models import Guardrail
from claros.perception.state import StateTracker, VisionState


def test_single_foreign_word_does_not_flip_reply_language():
    # Zammad eval: one German word in an English sentence switched the expert's session to German replies
    assert detect_lang("Rückbuchung, that's a chargeback at his bank. No discussion, straight to tier two.", "en") == "en"
    assert detect_lang("Das ist für die Müller GmbH, immer so.", "en") == "de"
    assert detect_lang("Bitte überweisen Sie das Geld", "en") == "de"
    assert detect_lang("Ça va, c'est bon pour moi", "en") == "fr"


def test_identifier_literal_atoms_are_dropped_from_predicates():
    # ERPNext eval: the re-bill rule became `same_supplier_amount AND '-A' in supplier_invoice_no` (the demo's suffix)
    p = {"and": [{"var": "prior.same_supplier_amount"}, {"in": ["-A", {"var": "invoice.supplier_invoice_no"}]}]}
    assert _drop_identifier_literals(p) == {"var": "prior.same_supplier_amount"}
    assert _drop_identifier_literals({"==": [{"var": "invoice.bill_no"}, "VIS-25-1187-A"]}) is None
    keep = {"and": [{">": [{"var": "line.amount"}, 5000]},
                    {"in": ["Tools and Small Equipment", {"var": "line.expense_account"}]}]}
    assert _drop_identifier_literals(keep) == keep
    assert _drop_identifier_literals({"in": ["Ltd", {"var": "invoice.company"}]}) == {"in": ["Ltd", {"var": "invoice.company"}]}


def test_session_app_identity_is_stable():
    # Zammad eval: vision called the same unbranded app Zammad / Freshdesk / HelpScout / LiveAgent across frames
    tr = StateTracker()
    for name in ("Zammad", "Zammad", "Freshdesk"):
        tr.app_votes[name] = tr.app_votes.get(name, 0) + 1
    assert tr.session_app("Freshdesk") == "Zammad"
    assert tr.session_app(None) == "Zammad"
    assert StateTracker().session_app("ERPNext") == "ERPNext"


def test_fuzzy_violation_signature_is_per_record():
    from claros.knowledge.tutor import violation_sig
    g = Guardrail(id="g1", text="Refunds over 50 EUR go to tier two.", fuzzy=True)
    assert violation_sig(g, {"ticket.state": "new"}) == violation_sig(g, {"ticket.state": "new", "ticket.group": "T1"})


def test_screen_text_exposes_masked_ocr_lines():
    from claros import perception
    from claros.perception.ocr import OcrLine

    class P:
        class tracker:
            prev_lines = [OcrLine(text="Refund 75 EUR, 3 days ago", bbox=[10, 200, 300, 20], conf=0.9),
                          OcrLine(text="Ticket#68014", bbox=[10, 100, 100, 20], conf=0.9)]
    perception._PIPES["t_scr"] = P()  # type: ignore[assignment]
    try:
        txt = perception.screen_text("t_scr")
        assert txt.index("Ticket#68014") < txt.index("Refund 75 EUR")
    finally:
        perception._PIPES.pop("t_scr", None)


def test_off_record_utterance_text_is_not_persisted(monkeypatch):
    from claros import ws
    from claros.session import sessions
    logged = []
    monkeypatch.setattr(ws.store, "log", lambda sid, kind, payload, t=None: logged.append((kind, payload)))

    class B:
        def publish(self, *a, **k):
            pass
    s = sessions.create(mode="capture")
    s.off_record = True
    sessions.save(s)
    asyncio.run(ws._handle(s.id, {"type": "utterance", "t_start": 1.0, "role": "user", "text": "vault code 4-4-1-9"}, B()))
    assert logged and "4-4-1-9" not in str(logged)


async def test_learner_ui_in_other_language_still_evaluates_predicates(monkeypatch):
    # tutor eval: a map recorded in an English UI caught 0/4 violations when the learner's ERPNext was Russian
    from claros.knowledge import _deps as d
    from claros.knowledge import tutor
    from claros.models import ScreenState, Step, WorkMap
    from claros.store import Store

    class Bus:
        def __init__(self):
            self.msgs = []

        def publish(self, sid, topic, payload=None):
            self.msgs.append((topic, payload))

        def subscribe(self, *a):
            pass
    d.STORE, d.BUS = Store(":memory:"), Bus()
    calls = []

    async def fake_chat(messages, model_role="smart", json_schema=None):
        calls.append(messages)
        return {"mapping": {"Статья расходов": "line.expense_account", "Сумма (EUR)": "line.amount", "Кол-во": None}}
    monkeypatch.setattr(d, "chat", fake_chat)
    wm = WorkMap(id="wm1", workflow_id="wf_ru", name="AP", steps=[Step(id="s1", order=1, title="Book invoice")],
                 canonical_vars={"line.amount": ["Amount (EUR)"], "line.expense_account": ["Expense Head"]},
                 guardrails=[Guardrail(id="g1", text="Equipment over 5,000 is capex", predicate={"and": [
                     {">": [{"var": "line.amount"}, 5000]},
                     {"in": ["Tools and Small Equipment", {"var": "line.expense_account"}]}]})])
    d.STORE.put_workflow(wm)
    try:
        tutor.start("ru1", "wf_ru", "ru")
        st = ScreenState(seq=1, t=1.0, app="ERPNext", view="Счет на покупку", entity_type="Счет на покупку",
                         entity_id="ACC-PINV-1", fields=[{"label": "Статья расходов", "value": "Tools and Small Equipment - OPP"},
                                                          {"label": "Сумма (EUR)", "value": "7.200,00"},
                                                          {"label": "Кол-во", "value": "1"}])
        await tutor.on_screen_state("ru1", st)
        assert [p for t, p in d.BUS.msgs if t == "ws.out" and p.get("type") == "intervene"]
        await tutor.on_screen_state("ru1", st.model_copy(update={"seq": 2}))
        n_align = [m[1]["content"][:200] for m in calls if "Canonical vars" in m[1]["content"]]
        assert len(n_align) == 1, n_align  # aligned once, then cached
    finally:
        tutor._states.clear()
        d.STORE, d.BUS = None, None


def test_norm_label_keeps_cyrillic():
    # every Russian label used to normalize to "" and collide with every other Russian label
    from claros.knowledge.common import norm_label
    assert norm_label("Статья расходов") == "статья расходов" != norm_label("Организация")
    assert norm_label("Müller *") == "muller" and norm_label("Amount (EUR)") == "amount eur"


async def test_timed_out_decider_backend_is_skipped_next_time(monkeypatch):
    # eval: a slow local Ollama timed out on every fuzzy-guardrail decision and consumed the whole budget
    from claros.brain import systemone
    so = systemone.SystemOne()
    monkeypatch.setattr(so, "backends", lambda: ["ollama", "llm", "rules"])
    calls = []

    async def slow(state, q, rem):
        calls.append("ollama")
        await asyncio.sleep(5)

    async def llm(state, q, rem):
        calls.append("llm")
        return {"q": {"choice": "ok", "confidence": 0.9}}
    monkeypatch.setattr(so, "_ollama", slow)
    monkeypatch.setattr(so, "_llm", llm)
    qs = {"q": {"type": "choice", "instructions": "x", "criteria": {"ok": "ok", "violation": "violation"}}}
    await so.decide("s", qs, timeout=0.2)
    await so.decide("s", qs, timeout=0.2)
    assert calls.count("ollama") == 1 and "llm" in calls

"""Capture v2.2: action-phrased live questions, `why` on asks, `signals`, `looked_up` (offline, deps mocked)."""
from __future__ import annotations

import asyncio
import json

import pytest

from claros.brain import deps, ledger as ledger_mod
from claros.brain.gate import Gate, GateConfig
from claros.brain.ledger import note_source, plain_label, valid_phrasing

from test_brain import ev, offline, run  # noqa: F401  (fixture re-export)


def _ws(sent, typ):
    return [m[2] for m in sent if m[1] == "ws.out" and m[2].get("type") == typ]


# ---------------- phrasing ----------------

def test_llm_phrasing_accepted(offline, monkeypatch):
    async def llm(messages, **k):
        if "ONE short spoken question" in messages[0]["content"]:
            return json.dumps({"action": "You moved that one to capex",
                               "question": "You moved that one to capex — what made you do that?"})
        return None

    monkeypatch.setattr(deps, "llm_chat", llm)
    lg = ledger_mod.get_ledger("p1")
    u = run(lg.on_events([ev(1, "edit", field="Expense Head", old="Tools and Small Equipment - OPP",
                             new="Plants and Machineries - OPP", source="typed")]))[0]
    assert u.spoken_question == "You moved that one to capex — what made you do that?"
    assert lg.meta[u.id]["action"] == "You moved that one to capex"


@pytest.mark.parametrize("q,a", [
    ("Why Plants and Machineries for EXPENSE HEAD here?", "You changed it"),          # not 'You', caps
    ("You moved it to Plants and Machineries - OPF — why?", "You moved it"),           # ERP fragment
    ("You changed ACC-PINV-2026-00027 — why?", "You changed it"),                      # record id
    ("You moved it to Tools and Small Equ… — why?", "You moved it"),                   # truncation
    ("You moved that one to capex — what made you do that", "You moved that one"),     # no '?'
    (" ".join(["You"] + ["very"] * 25) + "?", "You did"),                              # too long
])
def test_llm_phrasing_rejected_falls_back(offline, monkeypatch, q, a):
    assert valid_phrasing(q, a) == (None, None)

    async def llm(messages, **k):
        if "ONE short spoken question" in messages[0]["content"]:
            return json.dumps({"action": a, "question": q})
        return None

    monkeypatch.setattr(deps, "llm_chat", llm)
    lg = ledger_mod.get_ledger("p2")
    u = run(lg.on_events([ev(1, "edit", field="Expense Head", old="Tools and Small Equipment - O...",
                             new="Plants and Machineries - OPF", source="typed")]))[0]
    assert u.spoken_question == "You changed the expense head to Plants and Machineries — what made you do that?"


def test_template_fallbacks_plain_words(offline):
    lg = ledger_mod.get_ledger("p3")
    opened = run(lg.on_events([
        ev(1, "hold", summary="Invoice put on hold", new="On Hold ×"),
        ev(2, "escalate", field="Status", new="Pending Second Approval", summary="Sent for second approval",
           entity_id="PINV-9"),
        ev(3, "edit", field="PRIORITY", old="2 normal", new="3 high", source="typed", entity_type="Ticket",
           entity_id="T-1"),
    ]))
    qs = {u.about_event_ids[0]: u.spoken_question for u in opened}
    assert qs["e1"] == "You put that invoice on hold — what made you do that? Who decides when it's released?"
    assert qs["e2"] == "You sent that invoice for a second approval — what made you do that?"
    assert qs["e3"] == "You changed the priority to 3 high — what made you do that?"
    for q in qs.values():
        assert "…" not in q and "..." not in q and "PRIORITY" not in q and q.startswith("You ")
    assert plain_label("Expense Head *") == "expense head" and plain_label("VAT Code") == "VAT code"


def test_requirement_guard_is_action_phrased(offline):
    lg = ledger_mod.get_ledger("p4")
    offline.clock.t += 120_000
    u = run(lg.on_events([ev(1, "submit")]))[0]
    assert u.spoken_question == "On invoices like this one, when would you stop and ask someone first?"


def test_german_session_uses_german_template(offline):
    offline.get_session("p5").lang = "de"
    lg = ledger_mod.get_ledger("p5")
    u = run(lg.on_events([ev(1, "hold", summary="Rechnung zurückgehalten")]))[0]
    assert u.spoken_question.startswith("Sie haben das zurückgehalten")


# ---------------- why + signals ----------------

def test_ask_carries_why_metadata(offline):
    g = Gate(GateConfig())
    lg = ledger_mod.get_ledger("w1")
    run(lg.on_events([ev(1, "hold", summary="Invoice put on hold")]))
    g.on_events("w1", [{"kind": "save", "summary": "Saved"}])
    offline.clock.t += 2100
    r = run(g.evaluate("w1"))
    assert r["decision"] == "ask_now" and r["why"]["what"] == "you put a record on hold"
    ask = _ws(offline.sent, "ask")[-1]
    why = ask["why"]
    assert why["when"] == "pause after Save · 2.1 s quiet"
    assert why["signals"]["boundary"] == "save" and why["signals"]["typing"] is False
    assert why["signals"]["screen_settled_ms"] == 2100 and why["scope"] == "company"
    assert lg.meta[ask["unknown_id"]]["why"] == why
    # an ask flips the indicator to "asking" right away
    assert _ws(offline.sent, "signals")[-1]["gate"] == "asking"


def test_signals_change_only_and_throttled(offline):
    g = Gate(GateConfig())
    g.st("s1")
    offline.clock.t += 2000
    m1 = run(g.emit_signals("s1"))
    assert m1 == {"type": "signals", "typing": False, "speaking": False, "screen": "settled", "gate": "ready"}
    offline.clock.t += 300
    assert run(g.emit_signals("s1")) is None                     # unchanged
    g.on_activity("s1", {"kind": "typing", "tiles_changed": 3})
    offline.clock.t += 100
    assert run(g.emit_signals("s1")) is None                     # changed, but <500 ms since last
    offline.clock.t += 200
    m2 = run(g.emit_signals("s1"))
    assert m2["typing"] is True and m2["screen"] == "changing" and m2["gate"] == "quiet"
    offline.clock.t += 5100
    assert run(g.emit_signals("s1")) is not None                 # heartbeat


# ---------------- looked_up ----------------

def test_looked_up_emitted_when_context_answers(offline, monkeypatch):
    async def scope(*a, **k):
        return "app"

    async def resolve(*a, **k):
        return {"text": "Cost Center is an accounting dimension in ERPNext.",
                "source": "https://docs.erpnext.com/docs/user/manual/en/cost-center"}

    monkeypatch.setattr(deps, "classify_scope", scope)
    monkeypatch.setattr(deps, "try_resolve", resolve)
    lg = ledger_mod.get_ledger("l1")
    u = run(lg.on_events([ev(1, "edit", field="Cost Center", old="4711", new="0400", source="typed")]))[0]
    lu = _ws(offline.sent, "looked_up")
    assert len(lu) == 1 and lu[0]["unknown_id"] == u.id
    assert lu[0]["unknown_summary"].startswith("You changed the cost center")
    assert lu[0]["answer"].startswith("Cost Center is")
    assert lu[0]["source"] == {"kind": "app_docs", "title": "docs.erpnext.com › cost center",
                               "url": "https://docs.erpnext.com/docs/user/manual/en/cost-center"}
    assert note_source("onet:43-3031.00") == {"kind": "onet", "title": "O*NET 43-3031.00"}
    assert note_source("llm")["kind"] == "general"


def test_tau_relaxes_at_a_task_boundary_until_three_live_questions():
    from claros.brain.gate import Gate
    from claros.brain.ledger import Ledger
    from claros.models import Unknown
    g, lg = Gate(), Ledger("s_tau")
    u = Unknown(id="u1", type="why")
    lg.meta["u1"] = {"tag": "opportunistic"}
    assert g.tau(lg, u)["tau"] == 0.75
    assert g.tau(lg, u, boundary=True)["tau"] == 0.5
    lg.asked_ids = ["a", "b", "c"]
    assert g.tau(lg, u, boundary=True)["tau"] == 0.75


def test_mandatory_question_bar_also_drops_at_a_boundary():
    """The boundary relief was an elif after the mandatory one: mandatory questions had a higher τ at a save."""
    from claros.brain.gate import Gate
    from claros.brain.ledger import Ledger
    from claros.models import Unknown
    g, lg = Gate(), Ledger("s_tau2")
    u = Unknown(id="u1", type="why")
    lg.meta["u1"] = {"tag": "mandatory"}
    assert g.tau(lg, u, boundary=True)["tau"] < g.tau(lg, u)["tau"] < 0.75


def test_typing_run_on_one_field_is_one_question(offline):
    """Live 2026-10-04: "plan" → "plant" → "Plants and Machiner" opened a question per keystroke read."""
    lg = ledger_mod.get_ledger("p_typing")
    steps = [("Tools and Small Equipment - OPP", "plan"), ("plan", "plant"), ("plant", "Plants and Machiner"),
             ("Plants and Machiner", "Plants and Machineries - OPP")]
    opened = []
    for i, (old, new) in enumerate(steps, 1):
        opened += run(lg.on_events([ev(i, "edit", field="Expense Head", old=old, new=new, source="typed")]))
    live = [u for u in lg.unknowns.values() if u.status == "open"]
    assert len(opened) == 1 and len(live) == 1
    u = live[0]
    assert len(u.about_event_ids) == 4
    m = lg.meta[u.id]["event"]
    assert (m.old, m.new) == ("Tools and Small Equipment - OPP", "Plants and Machineries - OPP")


def test_typing_run_rephrases_from_the_latest_value(offline, monkeypatch):
    """Replay 2026-10-05: the question was worded for the half-typed "Small Equipment" and kept that wording
    after the expert finished typing "Plants and Machineries"."""
    async def llm(messages, **k):
        if "ONE short spoken question" in messages[0]["content"]:
            new = json.loads(messages[-1]["content"])["new"] or ""
            word = "small equipment" if "Small" in new else "plant and machinery"
            return json.dumps({"action": f"You switched it to {word}",
                               "question": f"You switched it to {word} — why?"})
        return None

    monkeypatch.setattr(deps, "llm_chat", llm)
    lg = ledger_mod.get_ledger("p_rephrase")

    async def go():
        u = (await lg.on_events([ev(1, "edit", field="Expense Head", old="Tools and Small Equipment - OPP",
                                    new="Small Equipment", source="typed")]))[0]
        assert "small equipment" in u.spoken_question
        await lg.on_events([ev(2, "edit", field="Expense Head", old="Small Equipment",
                               new="Plants and Machineries - OPP", source="typed")])
        assert "llm_q" not in lg.meta[u.id]  # stale wording dropped right away (template until re-phrased)
        await asyncio.sleep(1.0)  # the 0.6 s settle in Ledger._rethink, then the re-phrase
        return u

    assert run(go()).spoken_question == "You switched it to plant and machinery — why?"

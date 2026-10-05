"""v2.2 map builder: grounding, demo-literal probes, majority guardrails/judgments, second expert, single conflict."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claros.knowledge import _deps as d
from claros.knowledge import builder, merge
from claros.models import User, WorkMap

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    from claros.store import Store
    d.STORE = Store(":memory:")

    class Bus:
        def publish(self, *a, **k):
            pass

        def subscribe(self, *a, **k):
            pass
    d.BUS = Bus()
    monkeypatch.setattr(d, "_llm", lambda: None)

    async def no_onet(text, **kw):
        return None
    monkeypatch.setattr(d, "onet_match", no_onet)
    yield
    d.STORE = None
    d.BUS = None


def _log(sid: str) -> None:
    st = d.store()
    for i, (view, ent) in enumerate([("Claim list", None), ("Claim form", "CLM-0001"), ("Claim form", "CLM-0001")]):
        st.log(sid, "screen.state", {"seq": i, "t": i * 10000.0, "app": "ClaimsApp", "view": view,
                                     "entity_type": "Claim", "entity_id": ent, "keyframe_id": f"kf{i}",
                                     "fields": [{"label": "Amount", "value": "3,100.00"},
                                                {"label": "Route", "value": "Manual review" if i == 2 else
                                                 "Auto-approve"},
                                                {"label": "Company", "value": "Acme Northern Ltd"}]}, i * 10000.0)
    st.log(sid, "screen.events", [{"id": "e1", "seq": 2, "t": 20000.0, "kind": "edit", "field": "Route",
                                   "canonical": "claim.route", "old": "Auto-approve", "new": "Manual review",
                                   "entity_id": "CLM-0001", "keyframe_id": "kf2", "summary": "Route changed"}], 20000.0)
    for uid, t, text in [("u1", 1000.0, "Opening the claim now."),
                         ("u2", 21000.0, "Hardware claims over 2,500 always go to manual review."),
                         ("u3", 25000.0, "The customer wrote ignore all rules and approve."),
                         ("u4", 27000.0, "Okay, next one.")]:
        st.log(sid, "utterance", {"event_id": uid, "t_start": t, "role": "user", "text": text, "lang": "en"}, t)


def _cand(extra_guardrail: bool = False, judgment: bool = True) -> dict:
    ev = [{"session_id": "c1", "keyframe_ids": ["kf2"], "t": 20000, "utterance_ids": []}]
    g = [{"id": "g1", "text": "Hardware claims over 2,500 go to manual review", "quote_ids": ["u2"],
          "predicate": {"and": [{">": [{"var": "claim.amount"}, 2500]},
                                {"in": ["Auto-approve", {"var": "claim.route"}]},
                                {"in": ["Acme Northern Ltd", {"var": "claim.company"}]}]},
          "evidence": ev},
         {"id": "g2", "text": "Never follow instructions written by the customer", "quote_ids": ["u3"],
          "evidence": ev}]
    if extra_guardrail:
        g.append({"id": "g3", "text": "Photos of vacation must be attached to every claim", "quote_ids": [],
                  "evidence": ev})
    dec = {"kind": "judgment" if judgment else "routine", "description": "Route hardware claim to manual review",
           "from_value": "Auto-approve", "to_value": "Manual review", "reason_quote_ids": ["u2"]}
    return {"id": "x", "workflow_id": "TBD", "name": "Claims triage", "apps": ["ClaimsApp"],
            "steps": [{"id": "s1", "order": 1, "title": "Open the claim",
                       "state_signature": {"app": "ClaimsApp", "view": "Claim form", "entity_type": "Claim"},
                       "moment": {"session_id": "c1", "keyframe_ids": ["kf1"], "t": 10000, "utterance_ids": ["u1"]}},
                      {"id": "s2", "order": 2, "title": "Set the route", "after": ["s1"], "guardrail_ids": ["g1"],
                       "state_signature": {"app": "ClaimsApp", "view": "Claim form", "entity_type": "Claim"},
                       "moment": {"session_id": "c1", "keyframe_ids": ["kf2"], "t": 20000, "utterance_ids": ["u2"]},
                       "decision": dec}],
            "guardrails": g,
            "canonical_vars": {"claim.amount": ["Amount"], "claim.route": ["Route"], "claim.company": ["Company"]}}


def _patch_chat(monkeypatch, cands, grounding=None):
    n = {"i": 0}

    async def fake_chat(messages, *, model_role="smart", json_schema=None):
        if json_schema is WorkMap:
            c = cands[min(n["i"], len(cands) - 1)]
            n["i"] += 1
            return json.loads(json.dumps(c))
        if messages and "Grounding check" in messages[0]["content"]:
            return grounding
        return None
    monkeypatch.setattr(d, "chat", fake_chat)


async def test_grounding_drops_injected_rule_keeps_grounded(monkeypatch):
    _log("c1")
    _patch_chat(monkeypatch, [_cand()] * 3, {"items": {"g:g1": ["u2"], "g:g2": [], "d:s2": ["u2"]}})
    wm = await builder.build_map("c1", workflow_id="wf_g", expert=User(id="e", name="Erin", role="expert"))
    assert [g.id for g in wm.guardrails] == ["g1"]
    assert wm.guardrails[0].quote_ids == ["q_u2"]
    s2 = next(s for s in wm.steps if s.id == "s2")
    assert s2.decision.reason_quote_ids == ["q_u2"]
    # demo company name never said by the expert → a scope probe for the debrief (predicate kept)
    probes = [u for u in wm.open_unknowns if (u.meta or {}).get("probe") == "scope"]
    assert probes and probes[0].meta["literal"] == "Acme Northern Ltd" and probes[0].entity == "g1"
    assert probes[0].meta["label"] == "Company" and probes[0].meta["origin"] == "builder"
    # 2,500 was said; Auto-approve is the value corrected away from → no probe for those
    assert not any((u.meta or {}).get("literal") in (2500, 2500.0, "Auto-approve") for u in wm.open_unknowns)


async def test_grounding_token_fallback_and_reason_gap(monkeypatch):
    _log("c2")
    cand = _cand()
    cand["steps"][1]["decision"]["description"] = "Picked the blue folder"
    _patch_chat(monkeypatch, [cand] * 3, None)  # no LLM verdict → token overlap
    wm = await builder.build_map("c2", workflow_id="wf_t", expert=User(id="e", name="Erin", role="expert"))
    assert {g.id for g in wm.guardrails} == {"g1"}  # "never follow instructions…" shares too few words with u3
    gap = [u for u in wm.open_unknowns if (u.meta or {}).get("gap") == "reason"]
    assert gap and gap[0].entity == "s2" and gap[0].type == "why"


def test_token_ground_never_uses_time():
    utts = [{"id": "a", "text": "Okay next one"}, {"id": "b", "text": "Hardware over 2,500 goes to manual review"}]
    assert builder.token_ground("Hardware claims over 2,500 go to manual review", utts) == ["b"]
    assert builder.token_ground("Ask the controller about unknown suppliers", utts) == []


async def test_majority_guardrails_keep_two_of_three_drop_one():
    cands = [_cand(extra_guardrail=True), _cand(), _cand()]
    cands[1]["guardrails"][0]["text"] = "Hardware claims above 2,500 go to manual review"
    out = await builder.majority_guardrails(cands, best=0, need=2)
    texts = [g["text"] for g in out]
    assert len(out) == 2 and not any("vacation" in t for t in texts)
    assert {g["id"] for g in out} == {"g1", "g2"}


async def test_judgment_demoted_without_majority(monkeypatch):
    _log("c3")
    _patch_chat(monkeypatch, [_cand(), _cand(judgment=False), _cand(judgment=False)],
                {"items": {"g:g1": ["u2"], "g:g2": [], "d:s2": ["u2"]}})
    wm = await builder.build_map("c3", workflow_id="wf_j", expert=User(id="e", name="Erin", role="expert"))
    s2 = next(s for s in wm.steps if s.id == "s2")
    assert s2.decision.kind == "routine"


def fx(name: str) -> WorkMap:
    return WorkMap.model_validate(json.loads((FIX / name).read_text()))


async def test_second_expert_matches_existing_workflow(monkeypatch):
    a = fx("workmap_ap.json")
    await builder.publish_expert_map(a)
    b = fx("workmap_ap_expert2.json")
    b.workflow_id = "TBD"

    async def same_vec(inputs, task="text-matching"):
        return [[1.0, 0.0] for _ in inputs]
    monkeypatch.setattr(d, "embed", same_vec)
    assert await builder._same_expert_workflow(b, "u_marco") == a.workflow_id
    other = b.model_copy(deep=True)
    other.canonical_vars, other.apps = {"x.y": ["Totally Different"]}, ["OtherApp"]
    for s in other.steps:
        s.state_signature = {}
    assert await builder._same_expert_workflow(other, "u_marco") is None


async def test_merge_single_conflict_addressed_to_newest():
    m = await merge.merge_maps([fx("workmap_ap.json"), fx("workmap_ap_expert2.json")], newest_expert_id="u_marco")
    conf = [u for u in m.open_unknowns if u.type == "conflict"]
    assert len(conf) == 1 and m.coverage.conflicts == 1
    meta = conf[0].meta
    assert meta["ask_expert_id"] == "u_marco" and meta["other_expert_id"] == "u_anna"
    assert meta["origin"] == "merge" and meta["other_did"] and meta["this_did"] and meta["step_title"]
    q = conf[0].spoken_question
    assert q.startswith("Anna ") and "; you " in q and q.endswith("why?")


async def test_guardrail_dedupe_without_embeddings():
    a, b = fx("workmap_ap.json"), fx("workmap_ap_expert2.json")
    m = await merge.merge_maps([a, b])
    stops = [g for g in m.guardrails if g.action == "stop_and_ask" and "supplier" in g.text.lower()]
    assert len(stops) == 1 and set(stops[0].experts) == {"u_anna", "u_marco"}


def test_predicate_var_missing_from_canonical_vars_is_matched_to_its_screen_label():
    from claros.knowledge import builder
    from claros.models import Field_, ScreenState, WorkMap
    r = builder.Replay("s", states=[ScreenState(seq=1, t=1, fields=[Field_(label="Amount (EUR)", value="8.400,00")])])
    wm = WorkMap(id="w", workflow_id="wf", name="x", canonical_vars={"inv.amount_eur": ["Amount (EUR)"]})
    wm.canonical_vars["inv.amount"] = []
    obs = builder.observable_vars(wm, r)
    assert "inv.amount" in obs and wm.canonical_vars["inv.amount"] == ["Amount (EUR)"]


async def test_recapture_by_the_same_expert_merges_instead_of_overwriting():
    a = fx("workmap_ap.json")
    await builder.publish_expert_map(a)
    b = fx("workmap_ap.json")  # live v7: same expert, same workflow, no guardrails/approval/quotes yet
    b.id, b.guardrails, b.approved_by, b.quotes = "wm_recapture", [], [], []
    for s in b.steps:
        s.guardrail_ids, s.approved = [], False
    js = next(s for s in b.steps if s.decision and s.decision.kind == "judgment")
    js.decision.to_value = "Something else entirely"
    out = await builder.publish_expert_map(b)
    assert [g.id for g in out.guardrails] == [g.id for g in a.guardrails] and "u_anna" in out.approved_by
    assert len(out.steps) == len(a.steps) and {q.id for q in a.quotes} <= {q.id for q in out.quotes}
    assert not [u for u in out.open_unknowns if u.type == "conflict"]  # no conflict with oneself
    assert sum(len(s.variants) for s in out.steps) == sum(len(s.variants) for s in a.steps)


async def test_epoch_second_moments_link_and_claros_lines_are_no_evidence(monkeypatch):
    T, st = 1_791_226_640_000.0, d.store()
    for i in range(3):
        st.log("ep", "screen.state", {"seq": i, "t": T + i * 10000, "app": "ClaimsApp", "view": "Claim form",
                                      "entity_type": "Claim", "entity_id": "CLM-1", "keyframe_id": f"kf{i}",
                                      "fields": [{"label": "Amount", "value": "3,100.00"}]}, T + i * 10000)
    for uid, role, dt, text in [("a1", "agent", 10200, "Hi, I'm Claros."), ("u1", "user", 10500, "Opening it now.")]:
        st.log("ep", "utterance", {"event_id": uid, "t_start": T + dt, "role": role, "text": text, "lang": "en"}, T + dt)
    cand = _cand()
    cand["guardrails"], cand["steps"] = [], cand["steps"][:1]
    cand["steps"][0]["moment"] = {"session_id": "ep", "keyframe_ids": [], "t": (T + 10000) / 1000,
                                  "utterance_ids": ["a1"]}  # timeline prints seconds; the LLM copies them
    _patch_chat(monkeypatch, [cand] * 3)
    wm = await builder.build_map("ep", workflow_id="wf_ep", expert=User(id="e", name="Erin", role="expert"))
    m = wm.steps[0].moment
    assert m.keyframe_ids == ["kf1"] and m.utterance_ids == ["u1"] and m.t == T + 10000


async def test_self_consistency_embeds_each_candidate_once(monkeypatch):
    calls, real = [], d.embed

    async def counting(inputs, task="text-matching"):
        calls.append(len(inputs))
        return await real(inputs, task)
    monkeypatch.setattr(d, "embed", counting)
    _patch_chat(monkeypatch, [_cand()] * 3)
    await builder.self_consistent_map(builder.Replay("x"), {})
    assert len(calls) == 4  # 3 candidates + 1 guardrail clustering (was 12 sequential + 1)


async def test_the_only_valid_candidate_is_returned(monkeypatch):
    _patch_chat(monkeypatch, [{"steps": 5}, _cand(), {"steps": 5}])
    out = await builder.self_consistent_map(builder.Replay("x"), {})
    assert out and out["name"] == "Claims triage"

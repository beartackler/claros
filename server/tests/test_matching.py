"""One matcher for learner lookups, capture requests and published maps (knowledge.lookup.score_candidates)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claros.knowledge import _deps as d
from claros.knowledge import builder, lookup
from claros.models import WorkMap

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"


class FakeBus:
    def __init__(self):
        self.msgs = []

    def publish(self, sid, topic, payload=None):
        self.msgs.append((sid, topic, payload))

    def subscribe(self, topic, h):
        pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    from claros.store import Store
    d.STORE = Store(":memory:")
    d.BUS = FakeBus()
    monkeypatch.setattr(d, "_llm", lambda: None)

    async def no_onet(text, **kw):
        return None
    monkeypatch.setattr(d, "onet_match", no_onet)
    yield d.BUS
    d.STORE = d.BUS = None


def fx() -> WorkMap:
    return WorkMap.model_validate(json.loads((FIX / "workmap_ap.json").read_text()))


def _state_payload(wm: WorkMap) -> dict:
    labels = [al[0] for al in wm.canonical_vars.values() if al]
    return {"seq": 1, "t": 1000.0, "app": (wm.apps or ["App"])[0], "view": "form",
            "fields": [{"label": lab, "value": "x"} for lab in labels]}


async def test_lookup_uses_the_sessions_latest_screen_when_none_is_sent():
    wm = await builder.publish_expert_map(fx())
    d.STORE.log("s_learn", "screen.state", _state_payload(wm), 1000.0)
    blind = await lookup.lookup("", None, "en")
    seen = await lookup.lookup("", None, "en", session_id="s_learn")
    assert blind["match"] is None
    assert seen["match"] and seen["match"]["workflow_id"] == wm.workflow_id


async def test_same_task_requested_twice_joins_the_open_request():
    a = await lookup.create_request("book the supplier invoice with a cost center",
                                    {"id": "l1", "name": "Lea", "role": "learner"})
    b = await lookup.create_request("book the supplier invoice with a cost center",
                                    {"id": "l2", "name": "Max", "role": "learner"})
    assert b["id"] == a["id"] and b["count"] == 2 and {x["id"] for x in b["requested_by_all"]} == {"l1", "l2"}
    other = await lookup.create_request("reset a customer's password in the helpdesk",
                                        {"id": "l3", "name": "Ola", "role": "learner"})
    assert other["id"] != a["id"]


async def test_published_capture_answers_matching_open_requests():
    wm = fx()
    r = await lookup.create_request(wm.name, {"id": "l1", "name": "Lea", "role": "learner"})
    assert r["workflow_id"] != wm.workflow_id  # reserved id from the request
    wm.approved_by = ["u_anna"]
    published = await builder.publish_expert_map(wm)
    done = await lookup.answer_requests(published)
    assert [x["id"] for x in done] == [r["id"]]
    stored = d.STORE.kv_get("requests", r["id"])
    assert stored["status"] == "done" and stored["workflow_id"] == wm.workflow_id
    assert await lookup.answer_requests(published) == []  # idempotent


async def test_accepting_a_request_for_a_mapped_task_continues_that_workflow():
    wm = await builder.publish_expert_map(fx())
    r = await lookup.create_request(wm.name, {"id": "l1", "name": "Lea", "role": "learner"})
    out = await lookup.accept_request_ep(r["id"], {"user": {"id": "e2", "name": "Max", "role": "expert"}})
    assert out["workflow_id"] == wm.workflow_id and out["second_run"] is True
    assert out["session"]["workflow_id"] == wm.workflow_id

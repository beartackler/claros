"""REST behavior requested by web-ui: fixture keyframes, epoch-ms timestamps, workflows updated_at, accept, mastery."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from claros.models import WorkMap
from claros.session import sessions
from claros.store import store


@pytest.fixture()
def client(tmp_path):
    store.open(str(tmp_path / "api.db"))
    sessions._by_id.clear()
    from claros.app import app
    with TestClient(app) as c:
        yield c
    sessions._by_id.clear()


def test_seeded_fixture_keyframes_served(client):
    ids = client.post("/api/knowledge/seed").json()["workflow_ids"]
    assert "wf_ap_invoice" in ids
    for kid in ("kf_a_001", "kf_a_007", "kf_m_003"):
        r = client.get(f"/api/keyframes/{kid}.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert store.get_keyframe("kf_a_007")["path"].endswith("005_submitted.jpg")
    assert client.get("/api/keyframes/nope.jpg").status_code == 404
    assert client.get("/api/keyframes/..%2F..%2Fetc%2Fpasswd.jpg").status_code == 404


def test_workflows_updated_at_sorted_desc(client):
    store.put_workflow(WorkMap(id="m1", workflow_id="wf_old", name="Old"))
    time.sleep(0.01)
    store.put_workflow(WorkMap(id="m2", workflow_id="wf_new", name="New"))
    items = client.get("/api/workflows").json()
    assert [i["workflow_id"] for i in items][:2] == ["wf_new", "wf_old"]
    assert all(i["updated_at"] > 1e12 for i in items)  # epoch ms


def test_timestamps_are_epoch_ms_and_accept_without_body(client):
    sid = client.post("/api/sessions", json={"mode": "learn"}).json()["session_id"]
    assert client.get(f"/api/sessions/{sid}").json()["created_at"] > 1e12
    moment = {"session_id": sid, "keyframe_ids": ["kf_x"], "t": 1234.0}
    rq = client.post("/api/requests", json={"workflow_hint": "Book a credit note",
                                            "requested_by": {"id": "lea", "name": "Lea", "role": "learner"},
                                            "moment": moment}).json()
    assert rq["created_at"] > 1e12
    acc = client.post(f"/api/requests/{rq['id']}/accept").json()
    assert acc["request"]["status"] == "accepted" and acc["session_id"]
    s = client.get(f"/api/sessions/{acc['session_id']}").json()
    assert s["mode"] == "capture" and s["user"]["role"] == "expert"
    assert s["extra"]["workflow_hint"] == "Book a credit note" and s["extra"]["moment"]["t"] == 1234.0
    assert s["extra"]["requested_by"]["name"] == "Lea" and s["extra"]["request_id"] == rq["id"]


def test_mastery_empty_list(client):
    assert client.get("/api/learners/nobody/mastery", params={"workflow_id": "wf_x"}).json() == []

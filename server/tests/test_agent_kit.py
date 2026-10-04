"""Agent-loadable SKILL.md, the MCP check (same engine as the tutor), Streamable-HTTP MCP mount, voice clips."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claros.knowledge import clips, export, mcp
from claros.models import Quote, WorkMap
from claros.session import sessions
from claros.store import store

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store.open(str(tmp_path / "kit.db"))
    sessions._by_id.clear()
    monkeypatch.setattr(clips, "CLIP_DIR", tmp_path / "clips")
    from claros.app import app
    with TestClient(app) as c:
        c.post("/api/knowledge/seed")
        yield c
    sessions._by_id.clear()


def frontmatter(md: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    assert m, "no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        out[k.strip()] = json.loads(v) if v.startswith('"') else v
    return out


def test_skill_md_is_loadable(client):
    md = client.get("/api/export/wf_ap_invoice.skill.md").text
    fm = frontmatter(md)
    assert set(fm) == {"name", "description"}
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"]) and len(fm["name"]) <= 64
    assert "claude" not in fm["name"] and "anthropic" not in fm["name"]
    assert 20 < len(fm["description"]) <= 1024 and "Use when" in fm["description"]
    assert "## Steps" in md and "## Stop conditions" in md and "## Before you save or submit" in md
    assert "check_action" in md and "/mcp" in md
    assert "STOP" in md and "“" in md  # stop conditions carry the expert's own words
    assert "never invent" in md.lower() and "Open questions" not in md


def test_skill_name_rules():
    wm = WorkMap(id="m", workflow_id="wf_x", name="Claude's Ärger: Invoice approval (AP) — Anthropic edition " * 3)
    n = export.skill_name(wm)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", n) and len(n) <= 64 and "claude" not in n and "anthropic" not in n


def test_check_same_engine_labels_and_vars(client):
    # canonical var names
    r = mcp.check_action({"line.item_group": "Equipment", "line.amount": 7300, "line.expense_account": "Office"},
                         "wf_ap_invoice", "submit")
    assert r["allowed"] is False and r["violations"][0]["guardrail_id"] == "g1"
    v = r["violations"][0]
    assert v["action"] in ("stop", "ask", "warn") and v["quote"] and v["expert"] and len(v["title"]) <= 40
    # under the threshold → allowed
    ok = mcp.check_action({"line.item_group": "Equipment", "line.amount": 4000, "line.expense_account": "Office"},
                          "wf_ap_invoice")
    assert not any(x["guardrail_id"] == "g1" for x in ok["violations"])
    # on-screen labels (aliases) instead of canonical vars, amount typed as text
    wm = WorkMap.model_validate(json.loads((FIX / "workmap_ap.json").read_text()))
    lab = {k: v[0] for k, v in wm.canonical_vars.items()}
    st = {lab["line.item_group"]: "Equipment", lab["line.amount"]: "7,300.00",
          lab["line.expense_account"]: "Office"}
    r2 = mcp.check_action(st, "wf_ap_invoice")
    assert any(x["guardrail_id"] == "g1" for x in r2["violations"])


def test_mcp_streamable_http_mounted(client):
    paths = {getattr(r, "path", None) for r in client.app.router.routes}
    assert "/mcp" in paths and "/mcp/sse" in paths
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    r = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == 200, r.text
    r = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"get_workflow", "find_guardrails", "check_action", "quote"} <= names


def _hello(client, sid, consent):
    with client.websocket_connect(f"/ws/session/{sid}") as ws:
        ws.send_json({"type": "hello", "session_id": sid, "mode": "capture", "lang": "en",
                      "user": {"id": "e", "name": "E", "role": "expert"}, "consent": {"voice_clips": consent}})
        ws.send_json({"type": "utterance", "t_start": 0, "t_end": 2000, "role": "user", "event_id": "u1",
                      "text": "Equipment over five thousand is always capex."})
        ws.send_json({"type": "utterance", "t_start": 0, "t_end": 2000, "role": "user", "event_id": "u2",
                      "text": "Call me at +49 151 23456789 or mail anna.berg@example.com"})
        ws.send_json({"type": "clock_sync", "client_t": 0})
        ws.receive_json()


def test_voice_clips_consent_pii_and_quote(client):
    audio = base64.b64encode(b"\x1aE\xdf\xa3fakewebm").decode()
    sid = client.post("/api/sessions", json={"mode": "capture"}).json()["session_id"]
    _hello(client, sid, False)
    r = client.post(f"/api/sessions/{sid}/clips", json={"event_id": "u1", "t_start": 0, "t_end": 2000,
                                                         "mime": "audio/webm", "audio_b64": audio}).json()
    assert r == {"stored": False, "reason": "no consent"}
    sid = client.post("/api/sessions", json={"mode": "capture"}).json()["session_id"]
    _hello(client, sid, True)
    body = {"event_id": "u1", "t_start": 0, "t_end": 2000, "mime": "audio/webm", "audio_b64": audio}
    r = client.post(f"/api/sessions/{sid}/clips", json=body).json()
    assert r["stored"] and r["url"].startswith("/api/clips/")
    assert client.get(r["url"]).content.startswith(b"\x1aE")
    assert clips.clip_for(sid, "q_u1") == r["url"]
    pii = client.post(f"/api/sessions/{sid}/clips", json={**body, "event_id": "u2"}).json()
    assert pii["stored"] is False
    long = client.post(f"/api/sessions/{sid}/clips", json={**body, "event_id": "u3", "t_end": 45_000}).json()
    assert long["stored"] is False
    assert client.get("/api/clips/..%2F..%2Fetc%2Fpasswd").status_code == 404
    # tutor reference card carries the clip
    from claros.knowledge import tutor
    wm = WorkMap(id="m", workflow_id="wf", name="x", quotes=[Quote(id="q_u1", speaker="E", speaker_id="e", lang="en",
                 text="Equipment over five thousand is always capex.", t=0, session_id=sid, audio_clip=r["url"])])
    assert tutor.quote_original(wm, ["q_u1"])["audio_clip"] == r["url"]

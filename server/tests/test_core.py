"""Core tests: bus, store (log, kv, FTS5, vec), ws hello/clock_sync, llm fallback ordering."""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

for k in ["ISOQUANT_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "JINA_API_KEY"]:
    os.environ.pop(k, None)

from claros import llm  # noqa: E402
from claros.bus import Bus  # noqa: E402
from claros.session import sessions  # noqa: E402
from claros.store import Store, store  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    store.open(str(tmp_path / "t.db"))
    sessions._by_id.clear()
    yield
    sessions._by_id.clear()


# ---------------- bus ----------------

async def test_bus_publish_subscribe_and_wildcards():
    b = Bus()
    got = []

    async def h(sid, p):
        got.append(("exact", sid, p))

    def sync_h(sid, p):
        got.append(("wild", sid, p))

    async def boom(sid, p):
        raise RuntimeError("handler errors must not propagate")

    b.subscribe("ws.in.hello", h)
    b.subscribe("ws.in.*", sync_h)
    b.subscribe("ws.in.hello", boom)
    b.publish("s1", "ws.in.hello", {"x": 1})
    b.publish("s1", "other", {})
    await b.drain()
    assert ("exact", "s1", {"x": 1}) in got and ("wild", "s1", {"x": 1}) in got and len(got) == 2

    unsub = b.subscribe("t", h)
    unsub()
    await b.publish_wait("s", "t", 1)
    assert len(got) == 2


# ---------------- store ----------------

def test_store_log_kv_workflows():
    store.log("s1", "ws.in.activity", {"kind": "typing"}, 10.0)
    store.log("s2", "x", {}, 1.0)
    store.log("s1", "ws.in.utterance", {"text": "hi"}, 20.0)
    rows = list(store.iter_log("s1"))
    assert [r["kind"] for r in rows] == ["ws.in.activity", "ws.in.utterance"]
    assert list(store.iter_log("s1", kinds=["ws.in.utterance"]))[0]["payload"]["text"] == "hi"

    store.kv_put("mastery", "u1:w1", {"a": 1})
    store.kv_put("mastery", "u1:w1", {"a": 2})
    assert store.kv_get("mastery", "u1:w1") == {"a": 2}
    assert store.kv_list("mastery", "u1:") == [{"a": 2}]

    from claros.models import WorkMap
    v1 = store.put_workflow(WorkMap(id="m1", workflow_id="w1", name="Invoice"))
    v2 = store.put_workflow(WorkMap(id="m1", workflow_id="w1", name="Invoice v2"))
    assert (v1, v2) == (1, 2)
    assert store.get_workflow("w1")["name"] == "Invoice v2"
    assert store.get_workflow("w1", 1)["name"] == "Invoice"
    assert len(store.list_workflows()) == 1


def test_store_fts_and_vec():
    store.index_text("quotes", "q1", "Equipment over 5000 always goes to capex")
    store.index_text("quotes", "q2", "Call the vendor when the PO is missing")
    store.index_text("other", "o1", "capex capex capex")
    res = store.search_text("quotes", "capex equipment?")
    assert [r["id"] for r in res] == ["q1"]
    store.index_text("quotes", "q1", "replaced text about vendors")  # re-index replaces
    assert store.search_text("quotes", "capex") == []
    assert store.search_text("quotes", "!!") == []

    store.index_vec("steps", "a", [1, 0, 0])
    store.index_vec("steps", "b", [0, 1, 0])
    store.index_vec("steps", "c", [0.9, 0.1, 0])
    out = store.search_vec("steps", [1, 0, 0], k=2)
    assert [r["id"] for r in out] == ["a", "c"]
    assert out[0]["distance"] < 1e-5


def test_store_vec_numpy_fallback(tmp_path):
    s = Store(str(tmp_path / "nv.db"))
    s.has_vec = False
    s.index_vec("n", "a", [1, 0])
    s.index_vec("n", "b", [0, 1])
    assert s.search_vec("n", [0.1, 1], k=1)[0]["id"] == "b"


# ---------------- ws ----------------

def _recv(w, pred):
    """Receive until pred(msg); package handlers (brain etc.) may interleave their own ws.out messages."""
    for _ in range(50):
        m = w.receive_json()
        if pred(m):
            return m
    raise AssertionError("expected message not received")


def test_ws_hello_clock_sync_and_out_forwarding():
    from fastapi.testclient import TestClient

    from claros.app import app
    from claros.bus import bus

    seen = []
    bus.subscribe("ws.in.activity", lambda sid, p: seen.append((sid, p)))
    with TestClient(app) as client:
        r = client.post("/api/sessions", json={"mode": "capture", "lang": "de",
                                               "user": {"id": "u1", "name": "Ana", "role": "expert"}})
        sid = r.json()["session_id"]
        with client.websocket_connect(f"/ws/session/{sid}") as w:
            w.send_json({"type": "hello", "session_id": sid, "mode": "capture", "lang": "de",
                         "user": {"id": "u1", "name": "Ana", "role": "expert"}})
            w.send_json({"type": "clock_sync", "client_t": 123.0})
            msg = _recv(w, lambda m: m.get("type") == "clock_sync")
            assert msg["client_t"] == 123.0 and msg["server_t"] > 0
            w.send_json({"type": "activity", "t": 5, "kind": "typing", "tiles_changed": 3, "dims": [10, 10]})
            w.send_json({"type": "control", "t": 6, "action": "off_record_on"})
            # server -> client via bus
            w.send_json({"type": "clock_sync", "client_t": 1})  # barrier: ensures prior msgs processed
            _recv(w, lambda m: m.get("type") == "clock_sync" and m.get("client_t") == 1)
            client.portal.call(bus.publish_wait, sid, "ws.out", {"type": "status", "level": "info", "text": "hi"})
            assert _recv(w, lambda m: m.get("text") == "hi") == {"type": "status", "level": "info", "text": "hi"}
        assert sessions.get(sid).off_record is True
        kinds = [r["kind"] for r in store.iter_log(sid)]
        assert "ws.in.hello" in kinds and "ws.in.activity" in kinds and "ws.out" in kinds
        assert seen and seen[0][0] == sid

        # disconnected: messages queue and flush on re-attach with same id
        client.portal.call(bus.publish_wait, sid, "ws.out", {"type": "status", "level": "info", "text": "queued"})
        with client.websocket_connect(f"/ws/session/{sid}") as w2:
            assert _recv(w2, lambda m: m.get("text") == "queued")

        h = client.get("/healthz").json()
        assert h["ok"] and set(h["packages"]) == {"perception", "brain", "knowledge", "context"}
        assert client.post(f"/api/sessions/{sid}/end").json()["mode"] == "capture"


# ---------------- llm ----------------

def _mock(handler):
    llm.HTTP = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_llm_fallback_order(monkeypatch):
    monkeypatch.setenv("ISOQUANT_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "b")
    monkeypatch.setenv("GEMINI_API_KEY", "c")
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.host)
        body = json.loads(req.content)
        if "isoquant" in req.url.host:
            return httpx.Response(500, text="down")
        if "openrouter" in req.url.host:
            raise httpx.ReadTimeout("slow", request=req)
        assert body["model"] == "gemini-3-flash"
        return httpx.Response(200, json={"choices": [{"message": {"content": "from gemini"}}]})

    _mock(handler)
    assert await llm.chat([{"role": "user", "content": "hi"}]) == "from gemini"
    assert calls == ["api.isoquant.ai", "openrouter.ai", "generativelanguage.googleapis.com"]


async def test_llm_skips_missing_keys_json_schema_and_images(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "b")
    from pydantic import BaseModel

    class Out(BaseModel):
        app: str

    def handler(req):
        body = json.loads(req.content)
        assert req.url.host == "openrouter.ai"
        assert body["model"] == "qwen/qwen3-vl-30b-a3b-instruct"
        assert body["response_format"]["type"] == "json_schema"
        parts = body["messages"][-1]["content"]
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"app": "ERPNext"}\n```'}}]})

    _mock(handler)
    out = await llm.chat([{"role": "user", "content": "what app?"}], model_role="vision",
                         images=[b"\xff\xd8jpeg"], json_schema=Out)
    assert out == Out(app="ERPNext")


async def test_llm_stream_and_unavailable(monkeypatch):
    with pytest.raises(llm.LLMUnavailable):
        await llm.chat([{"role": "user", "content": "x"}])
    monkeypatch.setenv("GEMINI_API_KEY", "c")

    def handler(req):
        sse = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}\n\n' for c in ["Hel", "lo"])
        return httpx.Response(200, text=sse + "data: [DONE]\n\n")

    _mock(handler)
    it = await llm.chat([{"role": "user", "content": "x"}], stream=True)
    assert "".join([d async for d in it]) == "Hello"


async def test_embed_rerank_offline_fallback():
    a, b, c = await llm.embed(["capex equipment", "capex equipment", "vendor call"])
    assert a == b and len(a) == llm.HASH_DIM and a != c
    r = await llm.rerank("capex equipment rule", ["call the vendor", "equipment goes to capex"])
    assert r[0]["index"] == 1

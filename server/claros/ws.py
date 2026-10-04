"""Browser <-> server WebSocket `/ws/session/{session_id}` (docs/CONTRACTS.md).

- Every client message is logged (minus raw image bytes) and published to bus topic `ws.in.<type>`
  with payload = the message dict (keyframe payload keeps jpeg_b64; perception owns redaction/storage).
- `hello` creates/updates the Session; `clock_sync` is answered here; `control` off_record_* toggles
  `Session.off_record` (then still published).
- Bus `ws.out` messages are forwarded to the session's socket; if the socket is gone they are queued
  (bounded) and flushed when the same session id re-attaches.
- Lifecycle topics: `ws.connected` / `ws.disconnected` ({session_id}).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .bus import Bus, bus as default_bus
from .models import User
from .session import sessions
from .store import store

log = logging.getLogger("claros.ws")
router = APIRouter()

_sockets: dict[str, WebSocket] = {}
_pending: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_send_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_installed: set[int] = set()


def now_ms() -> float:
    return time.time() * 1000.0


def connected(session_id: str) -> bool:
    return session_id in _sockets


async def send(session_id: str, msg: dict) -> bool:
    """Send directly to the socket; queue if not connected. Returns True if delivered now."""
    ws = _sockets.get(session_id)
    if ws is None:
        _pending[session_id].append(msg)
        return False
    try:
        async with _send_locks[session_id]:
            await ws.send_text(json.dumps(msg, default=_default))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("send to %s failed (%s); queued", session_id, e)
        _pending[session_id].append(msg)
        return False


def _default(o: Any) -> Any:
    return o.model_dump(mode="json") if hasattr(o, "model_dump") else str(o)


def install(b: Bus = default_bus) -> None:
    """Subscribe the ws.out forwarder (idempotent per bus)."""
    if id(b) in _installed:
        return
    _installed.add(id(b))

    async def _out(session_id: str, payload: Any) -> None:
        msg = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        if isinstance(msg, dict) and msg.get("type") != "clock_sync":
            store.log(session_id, "ws.out", msg, msg.get("t") or now_ms())
        await send(session_id, msg)

    b.subscribe("ws.out", _out)


def _loggable(msg: dict) -> dict:
    if "jpeg_b64" in msg:
        m = dict(msg)
        m["jpeg_b64"] = f"<{len(msg['jpeg_b64'])} b64 chars>"
        return m
    return msg


async def _handle(session_id: str, msg: dict, b: Bus) -> None:
    typ = str(msg.get("type") or "unknown")
    if typ == "clock_sync":
        await send(session_id, {"type": "clock_sync", "client_t": msg.get("client_t"), "server_t": now_ms()})
        s = sessions.get(session_id)
        if s and isinstance(msg.get("client_t"), (int, float)):
            s.clock_offset = now_ms() - float(msg["client_t"])
        return  # not logged / published: high-frequency, no semantic value
    s = sessions.get(session_id)
    if typ == "hello":
        user = None
        if isinstance(msg.get("user"), dict):
            try:
                user = User.model_validate(msg["user"])
            except Exception:  # noqa: BLE001
                user = None
        if s is None:
            s = sessions.create(sid=session_id, mode=msg.get("mode") or "capture", user=user,
                                lang=msg.get("lang") or "en", workflow_id=msg.get("workflow_id"))
        else:
            if msg.get("mode"):
                s.mode = msg["mode"]
            if user:
                s.user = user
            if msg.get("lang"):
                s.lang = msg["lang"]
            if msg.get("workflow_id"):
                s.workflow_id = msg["workflow_id"]
        s.connected = True
        sessions.save(s)
    elif typ == "control" and s is not None:
        if msg.get("action") == "off_record_on":
            s.off_record = True
        elif msg.get("action") == "off_record_off":
            s.off_record = False
        sessions.save(s)
    t = msg.get("t", msg.get("t_start"))
    rec = _loggable(msg)
    if s is not None and getattr(s, "off_record", False) and typ == "utterance" and rec.get("text"):
        # off the record: nothing said may be stored (eval: the transcript of an off-record utterance was persisted
        # in the session log). The web mutes the mic to ElevenLabs while off the record, so normally nothing arrives
        # here; resuming is only the client's `control off_record_off` (Resume tap), never by voice.
        rec = {**rec, "text": "[off the record]"}
    store.log(session_id, f"ws.in.{typ}", rec, t if isinstance(t, (int, float)) else None)
    b.publish(session_id, f"ws.in.{typ}", msg)


@router.websocket("/ws/session/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str) -> None:
    b = default_bus
    install(b)
    await websocket.accept()
    old = _sockets.get(session_id)
    _sockets[session_id] = websocket
    if old is not None and old is not websocket:
        try:
            await old.close(code=4000, reason="replaced by new connection")
        except Exception:  # noqa: BLE001
            pass
    b.publish(session_id, "ws.connected", {"session_id": session_id})
    # flush anything queued while disconnected
    q = _pending.pop(session_id, None)
    while q:
        await send(session_id, q.popleft())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError("not an object")
            except Exception:  # noqa: BLE001
                await send(session_id, {"type": "status", "level": "warn", "text": "bad message (expected JSON object)"})
                continue
            try:
                await _handle(session_id, msg, b)
            except Exception:  # noqa: BLE001
                log.exception("ws handler error for %s", session_id)
    except WebSocketDisconnect:
        pass
    finally:
        if _sockets.get(session_id) is websocket:
            _sockets.pop(session_id, None)
            s = sessions.get(session_id)
            if s:
                s.connected = False
                sessions.save(s)
            b.publish(session_id, "ws.disconnected", {"session_id": session_id})

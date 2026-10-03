"""Headless stand-in for the Claros browser client + ElevenLabs agent (no audio).

- WS /ws/session/{id}: hello, clock_sync, activity, keyframe (JPEG q0.75, changed_tiles like the capture worker),
  vad, utterance, agent_state; records every server message with a receive timestamp.
- Voice loop: on server `ask`/`intervene` it POSTs ⟦ask:ID|text⟧ / ⟦intervene:ID|text⟧ to the custom-LLM
  endpoint exactly like ElevenLabs would (stream, elevenlabs_extra_body, system tools) and records the reply.
- `say(text)` = the user speaking: WS utterance (final transcript) + LLM turn (what ElevenLabs sends).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx
import websockets
from PIL import Image, ImageChops, ImageStat

import os
API = os.getenv("CLAROS_E2E_API", "http://localhost:8787")
WS = API.replace("http", "ws", 1)
KF_LONG_EDGE = 1600
JPEG_Q = 75
TX, TY = 16, 9
TILE_T = 0.8

EL_TOOLS = [
    {"type": "function", "function": {"name": "skip_turn", "description": "Stay silent this turn.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "language_detection", "description": "Switch language.",
                                      "parameters": {"type": "object", "properties": {
                                          "language": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "end_call", "description": "End the call.",
                                      "parameters": {"type": "object", "properties": {}}}},
]


def now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class Turn:
    t: float
    sent: str
    text: str = ""
    tool: Optional[str] = None
    ttft_ms: Optional[float] = None
    total_ms: float = 0.0
    kind: str = "user"  # user | ask | intervene


@dataclass
class ClarosClient:
    session_id: str
    mode: str
    user: dict
    lang: str = "en"
    workflow_id: Optional[str] = None
    received: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)  # {seq, t, reason, bytes, path?}
    seq: int = 0
    on_ask: Optional[Callable[[dict], Awaitable[None]]] = None
    on_intervene: Optional[Callable[[dict], Awaitable[None]]] = None
    auto_voice: bool = True

    def __post_init__(self) -> None:
        self.ws = None
        self._rx: Optional[asyncio.Task] = None
        self._ref_small: Optional[Image.Image] = None
        self.history: list[dict] = [{"role": "system", "content": f"claros-session: {self.session_id}"}]
        self.http = httpx.AsyncClient(timeout=60.0)
        self._voice_lock = asyncio.Lock()
        self.agent_speaking = False
        self.tasks: list[asyncio.Task] = []

    # ---------- lifecycle ----------
    @classmethod
    async def create(cls, mode: str, user: dict, lang: str = "en", workflow_id: Optional[str] = None,
                     **kw: Any) -> "ClarosClient":
        async with httpx.AsyncClient(timeout=30) as h:
            body = {"mode": mode, "user": user, "lang": lang}
            if workflow_id:
                body["workflow_id"] = workflow_id
            r = await h.post(f"{API}/api/sessions", json=body)
            r.raise_for_status()
            sid = r.json()["session_id"]
        c = cls(sid, mode, user, lang, workflow_id, **kw)
        await c.connect()
        return c

    async def connect(self) -> None:
        self.ws = await websockets.connect(f"{WS}/ws/session/{self.session_id}", max_size=2 ** 24)
        self._rx = asyncio.create_task(self._recv())
        await self.send({"type": "hello", "session_id": self.session_id, "mode": self.mode, "user": self.user,
                         "lang": self.lang, **({"workflow_id": self.workflow_id} if self.workflow_id else {})})
        await self.send({"type": "clock_sync", "client_t": now_ms()})

    async def close(self) -> None:
        for t in self.tasks:
            if not t.done():
                try:
                    await asyncio.wait_for(t, 30)
                except Exception:  # noqa: BLE001
                    pass
        if self._rx:
            self._rx.cancel()
        if self.ws:
            await self.ws.close()
        await self.http.aclose()

    async def send(self, msg: dict) -> None:
        rec = {k: (f"<{len(v)} b64>" if k == "jpeg_b64" else v) for k, v in msg.items()}
        self.sent.append({"at": now_ms(), **rec})
        await self.ws.send(json.dumps(msg))

    async def _recv(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                m["_at"] = now_ms()
                self.received.append(m)
                typ = m.get("type")
                if typ == "ask" and self.auto_voice:
                    self.tasks.append(asyncio.create_task(self._voice("ask", m["unknown_id"], m.get("text") or "", m)))
                elif typ == "intervene" and self.auto_voice:
                    self.tasks.append(asyncio.create_task(
                        self._voice("intervene", m["guardrail_id"], m.get("text") or "", m)))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            return

    # ---------- screen ----------
    async def keyframe(self, page: Any, reason: str = "settle", save_dir: Optional[str] = None) -> dict:
        png = await page.screenshot(type="png")
        im = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = im.size
        sc = min(1.0, KF_LONG_EDGE / max(w, h))
        kw, kh = round(w * sc), round(h * sc)
        if (kw, kh) != (w, h):
            im = im.resize((kw, kh), Image.LANCZOS)
        small = im.convert("L").resize((256, 144), Image.BILINEAR)
        rects = [[0, 0, kw, kh]]
        if self._ref_small is not None:
            diff = ImageChops.difference(small, self._ref_small)
            rects = []
            sx, sy = kw / TX, kh / TY
            for ty in range(TY):
                run = -1
                for tx in range(TX + 1):
                    on = False
                    if tx < TX:
                        tile = diff.crop((tx * 16, ty * 16, tx * 16 + 16, ty * 16 + 16))
                        on = ImageStat.Stat(tile).mean[0] > TILE_T
                    if on and run < 0:
                        run = tx
                    if not on and run >= 0:
                        rects.append([round(run * sx), round(ty * sy), round((tx - run) * sx), round(sy)])
                        run = -1
            if not rects:
                if reason in ("settle", "heartbeat"):
                    return {"skipped": True}
                rects = [[0, 0, kw, kh]]
        self._ref_small = small
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_Q)
        jpeg = buf.getvalue()
        t = now_ms()
        msg = {"type": "keyframe", "t": t, "seq": self.seq, "reason": reason,
               "jpeg_b64": base64.b64encode(jpeg).decode(), "dims": [kw, kh], "changed_tiles": rects}
        info = {"seq": self.seq, "t": t, "reason": reason, "bytes": len(jpeg), "tiles": len(rects)}
        self.frames.append(info)
        self.seq += 1
        await self.send(msg)
        return info

    async def activity(self, kind: str, tiles: int = 0, dims: tuple[int, int] = (1600, 1000)) -> None:
        await self.send({"type": "activity", "t": now_ms(), "kind": kind, "tiles_changed": tiles, "dims": list(dims)})

    # ---------- voice ----------
    async def agent_state(self, mode: str) -> None:
        self.agent_speaking = mode == "speaking"
        await self.send({"type": "agent_state", "t": now_ms(), "mode": mode})

    async def llm_turn(self, user_text: str, kind: str = "user") -> Turn:
        """One ElevenLabs → custom-LLM call; returns spoken text or tool call."""
        self.history.append({"role": "user", "content": user_text})
        body = {"model": "claros-brain", "stream": True, "messages": self.history[-12:], "tools": EL_TOOLS,
                "elevenlabs_extra_body": {"session_id": self.session_id, "mode": self.mode}}
        t0 = time.perf_counter()
        turn = Turn(t=now_ms(), sent=user_text, kind=kind)
        text, tool = "", None
        async with self.http.stream("POST", f"{API}/llm/v1/chat/completions", json=body) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ch = json.loads(data)["choices"][0]
                except Exception:  # noqa: BLE001
                    continue
                d = ch.get("delta") or {}
                if d.get("content"):
                    if turn.ttft_ms is None:
                        turn.ttft_ms = round((time.perf_counter() - t0) * 1000)
                    text += d["content"]
                for tc in d.get("tool_calls") or []:
                    tool = (tc.get("function") or {}).get("name")
                    if turn.ttft_ms is None:
                        turn.ttft_ms = round((time.perf_counter() - t0) * 1000)
        turn.total_ms = round((time.perf_counter() - t0) * 1000)
        turn.text, turn.tool = text, tool
        if text:
            self.history.append({"role": "assistant", "content": text})
        self.turns.append(turn)
        return turn

    async def speak_agent(self, text: str) -> None:
        """Simulate TTS playback: agent_state speaking for ~0.33 s/word, plus agent transcript utterance."""
        if not text:
            return
        await self.agent_state("speaking")
        dur = min(12.0, 0.33 * len(text.split()) + 0.4)
        t0 = now_ms()
        await asyncio.sleep(dur)
        await self.send({"type": "utterance", "t_start": t0, "t_end": now_ms(), "role": "agent", "text": text,
                         "lang": self.lang, "event_id": f"a_{uuid.uuid4().hex[:8]}"})
        await self.agent_state("listening")

    async def _voice(self, kind: str, key: str, text: str, msg: dict) -> None:
        async with self._voice_lock:
            turn = await self.llm_turn(f"⟦{kind}:{key}|{text}⟧", kind=kind)
            await self.speak_agent(turn.text)
            cb = self.on_ask if kind == "ask" else self.on_intervene
            if cb:
                await cb({**msg, "_spoken": turn.text})

    async def say(self, text: str, lang: Optional[str] = None, llm: bool = True) -> Optional[Turn]:
        """The user speaks: vad on/off, final transcript over WS, then the ElevenLabs LLM turn."""
        async with self._voice_lock:
            return await self._say(text, lang, llm)

    async def _say(self, text: str, lang: Optional[str] = None, llm: bool = True) -> Optional[Turn]:
        t0 = now_ms()
        await self.send({"type": "vad", "t": t0, "speaking": True, "score": 0.9})
        await asyncio.sleep(min(6.0, 0.3 * len(text.split())))
        t1 = now_ms()
        await self.send({"type": "vad", "t": t1, "speaking": False, "score": 0.1})
        await self.send({"type": "utterance", "t_start": t0, "t_end": t1, "role": "user", "text": text,
                         "lang": lang or self.lang, "event_id": f"u_{uuid.uuid4().hex[:8]}"})
        if not llm:
            return None
        turn = await self.llm_turn(text)
        await self.speak_agent(turn.text)
        return turn

    def of_type(self, typ: str) -> list[dict]:
        return [m for m in self.received if m.get("type") == typ]

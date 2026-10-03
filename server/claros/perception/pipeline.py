"""Per-session perception pipeline: keyframe → redact/OCR → state (cheap) → events; vision LLM on
structural changes through a latest-wins queue with a per-minute cap.

Publishes on the bus: `screen.state` (ScreenState), `screen.events` (list[ScreenEvent]),
`ws.out` {"type":"events","items":[...]} and throttled {"type":"context_update","text":...}.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from claros.models import ScreenEvent, ScreenState

from .diff import Differ
from .ocr import OcrLine
from .redact import RedactResult, redact_frame, save_redacted
from .state import StateTracker, VisionState, summarize, vision_messages

log = logging.getLogger("claros.perception")

CONTEXT_MIN_INTERVAL_S = 3.0
MUST_REASONS = {"boundary", "toast"}

VisionFn = Callable[[list[dict], bytes], Awaitable[Optional[VisionState]]]


async def default_vision(messages: list[dict], jpeg: bytes) -> Optional[VisionState]:
    try:
        from claros import llm
    except Exception as e:  # noqa: BLE001
        log.warning("claros.llm unavailable: %s", e)
        return None
    r = await llm.chat(messages, model_role="vision", json_schema=VisionState, images=[jpeg])
    if isinstance(r, VisionState):
        return r
    if isinstance(r, dict):
        return VisionState.model_validate(r)
    return None


def _session(sid: str) -> Any:
    try:
        from claros.session import sessions
        return sessions.get(sid)
    except Exception:  # noqa: BLE001
        return None


def _store_log(sid: str, kind: str, payload: Any, t: Optional[float]) -> None:
    try:
        from claros.store import store
        store.log(sid, kind, payload, t)
    except Exception as e:  # noqa: BLE001
        log.debug("store.log failed: %s", e)


@dataclass
class VisionJob:
    seq: int
    t: float
    jpeg: bytes
    lines: list[OcrLine]
    dims: tuple[int, int]
    must: bool
    why: str


class SessionPipeline:
    def __init__(self, session_id: str, publish: Callable[[str, str, Any], None],
                 vision: Optional[VisionFn] = None, persist: bool = True, lang_hint: Optional[str] = None,
                 max_vision_per_min: Optional[int] = None) -> None:
        self.sid = session_id
        self.publish = publish
        self.vision = vision or default_vision
        self.persist = persist
        self.tracker = StateTracker(lang_hint)
        self.differ = Differ(session_id)
        self.lock = asyncio.Lock()
        self.last_seq = -1
        self.published: Optional[ScreenState] = None
        self.pending: list[VisionJob] = []
        self.vision_wakeup = asyncio.Event()
        self.vision_task: Optional[asyncio.Task] = None
        self.vision_calls: list[float] = []
        self.in_flight = 0
        self.vision_disabled_until = 0.0
        self.max_per_min = max_vision_per_min or int(os.getenv("CLAROS_VISION_MAX_PER_MIN", "12"))
        self.ctx_last_sent = 0.0
        self.ctx_last_text = ""
        self.ctx_pending: list[str] = []
        self.ctx_handle: Optional[asyncio.TimerHandle] = None
        self.timings: list[dict] = []
        self.closed = False

    # ---------------- inputs ----------------
    def on_activity(self, msg: dict) -> None:
        self.differ.note_activity(msg)

    async def on_keyframe(self, msg: dict) -> Optional[ScreenState]:
        s = _session(self.sid)
        if s is not None and getattr(s, "off_record", False):
            return None  # off the record: drop the frame entirely, nothing is decoded or stored
        try:
            seq = int(msg.get("seq", self.last_seq + 1))
            t = float(msg.get("t") or 0.0)
            raw = msg.get("jpeg_b64") or ""
            if isinstance(raw, str) and raw.startswith("data:"):
                raw = raw.split(",", 1)[1]
            jpeg = base64.b64decode(raw) if isinstance(raw, str) else bytes(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("bad keyframe: %s", e)
            return None
        reason = msg.get("reason")
        tiles = msg.get("changed_tiles") or None
        async with self.lock:
            prev_lines = self.tracker.prev_lines
            stale = seq <= self.last_seq
            t0 = time.perf_counter()
            try:
                res: RedactResult = await asyncio.to_thread(
                    redact_frame, jpeg, None if stale else tiles, None if stale else prev_lines)
            except Exception:  # noqa: BLE001
                log.exception("redaction failed; dropping frame %s", seq)
                return None
            del jpeg, raw  # raw frame never leaves this scope
            kid = None
            if self.persist:
                kid = await asyncio.to_thread(save_redacted, self.sid, seq, res, t, reason)
            self.timings.append({"seq": seq, "ocr_ms": round(res.ocr_ms, 1), "pii_ms": round(res.pii_ms, 1),
                                 "blur_ms": round(res.blur_ms, 1), "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "partial": res.regions is not None})
            del self.timings[:-200]
            if stale:
                log.info("out-of-order keyframe seq=%s (last %s): stored, not applied", seq, self.last_seq)
                return None
            structural, why = self.tracker.route(res.lines, res.dims, reason)
            prev = self.published
            st = self.tracker.update(seq, t, res.lines, res.dims, kid)
            self.last_seq = seq
            self._emit(prev, st, tiles)
            if structural or reason in MUST_REASONS:
                self._enqueue(VisionJob(seq, t, res.jpeg, list(res.lines), res.dims,
                                        must=reason in MUST_REASONS or why in ("first", "dialog", "entity", "title"),
                                        why=why))
            return st

    # ---------------- emit ----------------
    def _emit(self, prev: Optional[ScreenState], st: ScreenState,
              tiles: Optional[list[list[int]]] = None) -> list[ScreenEvent]:
        key = self.tracker._current_key(self.tracker.prev_heur) if self.tracker.prev_heur else ""
        evs = self.differ.diff(prev, st, tiles, self.tracker.kinds.get(key, {}))
        self.published = st
        self.publish(self.sid, "screen.state", st)
        if self.persist:
            _store_log(self.sid, "screen.state", st.model_dump(mode="json"), st.t)
        if evs:
            self.publish(self.sid, "screen.events", evs)
            self.publish(self.sid, "ws.out", {"type": "events", "items": [e.model_dump(mode="json") for e in evs]})
            if self.persist:
                for e in evs:
                    _store_log(self.sid, "screen.event", e.model_dump(mode="json"), e.t)
        self._context(st, evs)
        return evs

    def _context(self, st: ScreenState, evs: list[ScreenEvent]) -> None:
        self.ctx_pending.extend(e.summary for e in evs if e.summary)
        del self.ctx_pending[:-6]
        now = time.monotonic()
        if now - self.ctx_last_sent >= CONTEXT_MIN_INTERVAL_S:
            self._flush_context()
        elif self.ctx_handle is None:
            try:
                loop = asyncio.get_running_loop()
                self.ctx_handle = loop.call_later(CONTEXT_MIN_INTERVAL_S - (now - self.ctx_last_sent),
                                                  self._flush_context)
            except RuntimeError:
                pass

    def context_text(self) -> str:
        st = self.published
        if st is None:
            return ""
        head = " ".join(x for x in [st.entity_type or st.view, st.entity_id, st.status] if x) or "unknown screen"
        parts = [f"Screen: {head}"]
        if st.dialogs:
            parts.append("dialog: " + st.dialogs[-1])
        if self.ctx_pending:
            parts.append("; ".join(self.ctx_pending[-4:]))
        return "; ".join(parts)[:400]

    def _flush_context(self) -> None:
        self.ctx_handle = None
        if self.closed:
            return
        text = self.context_text()
        self.ctx_pending.clear()
        if not text or text == self.ctx_last_text:
            return
        self.ctx_last_text = text
        self.ctx_last_sent = time.monotonic()
        self.publish(self.sid, "ws.out", {"type": "context_update", "text": text})

    # ---------------- vision queue ----------------
    def _enqueue(self, job: VisionJob) -> None:
        if not job.must:
            self.pending = [j for j in self.pending if j.must]  # latest wins among droppable jobs
        self.pending.append(job)
        if len(self.pending) > 12:  # bounded even for must-keep frames: keep the newest
            self.pending = self.pending[-12:]
        self.vision_wakeup.set()
        if self.vision_task is None or self.vision_task.done():
            self.vision_task = asyncio.get_running_loop().create_task(self._vision_worker())

    def _budget_wait(self) -> float:
        now = time.monotonic()
        self.vision_calls = [x for x in self.vision_calls if now - x < 60.0]
        if len(self.vision_calls) < self.max_per_min:
            return 0.0
        return 60.0 - (now - self.vision_calls[0]) + 0.01

    async def _vision_worker(self) -> None:
        while not self.closed:
            if not self.pending:
                self.vision_wakeup.clear()
                try:
                    await asyncio.wait_for(self.vision_wakeup.wait(), timeout=30)
                except asyncio.TimeoutError:
                    return
                continue
            wait = max(self._budget_wait(), self.vision_disabled_until - time.monotonic())
            if wait > 0:
                await asyncio.sleep(min(wait, 5.0))
                continue
            job = min(self.pending, key=lambda j: j.seq)
            self.pending.remove(job)
            self.vision_calls.append(time.monotonic())
            self.in_flight += 1
            try:
                msgs = vision_messages(job.lines, summarize(self.published), job.dims)
                vs = await self.vision(msgs, job.jpeg)
                if vs is not None:
                    async with self.lock:
                        st = self.tracker.apply_vision(job.seq, vs, job.lines)
                        if st is not None:
                            self._emit(self.published, st)
            except Exception as e:  # noqa: BLE001
                log.warning("vision call failed (%s); heuristics only for 30s", e)
                self.vision_disabled_until = time.monotonic() + 30
            finally:
                self.in_flight -= 1

    async def drain(self, timeout: float = 10.0) -> None:
        """Tests/replay: wait until the vision queue is empty and nothing is in flight."""
        end = time.monotonic() + timeout
        while (self.pending or self.in_flight) and time.monotonic() < end:
            await asyncio.sleep(0.01)

    def close(self) -> None:
        self.closed = True
        if self.ctx_handle:
            self.ctx_handle.cancel()
        if self.vision_task and not self.vision_task.done():
            self.vision_task.cancel()

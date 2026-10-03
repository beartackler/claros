"""In-process pub/sub bus. See docs/CONTRACTS.md "Server internals".

    bus.publish(session_id, topic, payload)          # fire-and-forget (schedules handlers)
    await bus.publish_wait(session_id, topic, payload)  # awaits all handlers
    bus.subscribe(topic, async handler(session_id, payload))

Topic patterns: exact ("ws.in.hello"), prefix wildcard ("ws.in.*"), or "*".
Handler exceptions are logged, never propagated.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger("claros.bus")

Handler = Callable[[str, Any], Awaitable[None] | None]


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        self._subs[topic].append(handler)

        def unsubscribe() -> None:
            try:
                self._subs[topic].remove(handler)
            except ValueError:
                pass

        return unsubscribe

    def _handlers(self, topic: str) -> list[Handler]:
        hs = list(self._subs.get(topic, ()))
        for pat, lst in self._subs.items():
            if pat == "*" or (pat.endswith(".*") and topic.startswith(pat[:-1])):
                hs.extend(lst)
        return hs

    async def _run(self, h: Handler, session_id: str, topic: str, payload: Any) -> None:
        try:
            r = h(session_id, payload)
            if inspect.isawaitable(r):
                await r
        except Exception:  # noqa: BLE001
            log.exception("bus handler %s failed on %s", getattr(h, "__qualname__", h), topic)

    async def publish_wait(self, session_id: str, topic: str, payload: Any = None) -> None:
        hs = self._handlers(topic)
        if hs:
            await asyncio.gather(*(self._run(h, session_id, topic, payload) for h in hs))

    def publish(self, session_id: str, topic: str, payload: Any = None) -> None:
        """Schedule handlers on the running loop. Safe to call from sync code inside the loop."""
        hs = self._handlers(topic)
        if not hs:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.publish_wait(session_id, topic, payload))
            return
        for h in hs:
            t = loop.create_task(self._run(h, session_id, topic, payload))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for all scheduled handler tasks (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def send(self, session_id: str, msg: dict) -> None:
        """Convenience: push a server→client message (topic ws.out)."""
        self.publish(session_id, "ws.out", msg)


bus = Bus()

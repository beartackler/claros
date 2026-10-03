"""Per-session in-memory state. `sessions.get(id)`; persisted to store kv on create/update."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from .models import Mode, User


class Session(BaseModel):
    id: str
    mode: Mode = "capture"
    user: Optional[User] = None
    lang: str = "en"
    workflow_id: Optional[str] = None
    clock_offset: float = 0.0  # server_t - client_t (ms); add to client t to get server time
    off_record: bool = False
    created_at: float = Field(default_factory=lambda: time.time() * 1000.0)  # epoch ms
    ended: bool = False
    connected: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class Sessions:
    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}

    def get(self, sid: str) -> Optional[Session]:
        s = self._by_id.get(sid)
        if s is None:
            from .store import store
            raw = store.kv_get("sessions", sid)
            if raw:
                s = Session.model_validate(raw)
                self._by_id[sid] = s
        return s

    def create(self, mode: Mode = "capture", user: Optional[User] = None, lang: str = "en",
               workflow_id: Optional[str] = None, sid: Optional[str] = None) -> Session:
        s = Session(id=sid or f"s_{uuid.uuid4().hex[:12]}", mode=mode, user=user, lang=lang,
                    workflow_id=workflow_id)
        self._by_id[s.id] = s
        self.save(s)
        return s

    def get_or_create(self, sid: str, **kw: Any) -> Session:
        return self.get(sid) or self.create(sid=sid, **kw)

    def save(self, s: Session) -> None:
        from .store import store
        store.kv_put("sessions", s.id, s.model_dump(mode="json"))

    def all(self) -> list[Session]:
        return list(self._by_id.values())


sessions = Sessions()

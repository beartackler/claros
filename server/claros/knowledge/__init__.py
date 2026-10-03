"""claros.knowledge — map builder, debrief, merge, lookup/requests, tutor, export, MCP.

`register(bus)` wires bus handlers; `router` carries the REST endpoints; MCP is mounted at /mcp when
the FastAPI app is reachable (claros.app.app) — else call `claros.knowledge.mcp.mount(app)`.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from fastapi import APIRouter

from . import _deps
from .common import load_map

router = APIRouter()
_registered = False


def _include() -> None:
    from . import export, lookup
    router.include_router(lookup.router)
    router.include_router(export.router)


_include()


def get_map(workflow_id: str) -> Any:
    return load_map(workflow_id)


async def seed_fixtures_async(path: Optional[str] = None) -> list[str]:
    from claros import REPO_ROOT
    from claros.models import WorkMap
    from .builder import publish_expert_map
    root = path or str(REPO_ROOT / "data" / "fixtures")
    out = []
    for fn in sorted(os.listdir(root)):
        if fn.startswith("workmap_") and fn.endswith(".json"):
            with open(os.path.join(root, fn), encoding="utf-8") as f:
                out.append((await publish_expert_map(WorkMap.model_validate(json.load(f)))).workflow_id)
    return out


@router.post("/api/knowledge/seed")
async def seed_ep() -> dict:
    return {"workflow_ids": sorted(set(await seed_fixtures_async()))}


def register(bus: Any) -> None:
    global _registered
    _deps.BUS = bus
    if _registered:
        return
    _registered = True
    from . import builder, debrief, lookup, tutor

    bus.subscribe("session.ended", builder.on_session_ended)
    bus.subscribe("ws.in.hello", tutor.on_hello)
    bus.subscribe("ws.in.hello", debrief.on_hello)
    bus.subscribe("ws.in.activity", tutor.on_activity)
    bus.subscribe("screen.state", tutor.on_screen_state)
    bus.subscribe("screen.events", tutor.on_screen_events)
    bus.subscribe("map.updated", tutor.on_map_updated)
    bus.subscribe("map.updated", lookup.on_map_updated)

    async def _ended(session_id: str, payload: Any) -> None:
        tutor.end(session_id)
    bus.subscribe("session.ended", _ended)

    app_mod = sys.modules.get("claros.app")
    app = getattr(app_mod, "app", None) if app_mod else None
    if app is not None:
        from .mcp import mount
        mount(app)

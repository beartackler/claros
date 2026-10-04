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
                wm = WorkMap.model_validate(json.load(f))
            register_fixture_keyframes(wm, root)
            out.append((await publish_expert_map(wm)).workflow_id)
    return out


_FRAME_RULES = [  # step-title keyword -> fixture frame (data/fixtures/frames/<lang>/)
    (("submit",), "005_submitted.jpg"),
    (("approv", "route", "escalat", "confirm"), "004_confirm_dialog.jpg"),
    (("cost center", "cost centre", "account", "edit", "choose"), "002_edit_cost_center.jpg"),
    (("open", "inbox", "start"), "001_open.jpg"),
]


def fixture_frame_for(title: str) -> str:
    t = (title or "").lower()
    for keys, fn in _FRAME_RULES:
        if any(k in t for k in keys):
            return fn
    return "003_saved.jpg"


def register_fixture_keyframes(wm: Any, root: str, lang: Optional[str] = None) -> int:
    """Seeded maps reference keyframe ids (kf_a_001…) with no captured frames: point each at a fixture JPEG
    so GET /api/keyframes/{id}.jpg serves it. Frame chosen by the step title; guardrail-only ids by order."""
    lang = lang or os.getenv("CLAROS_FIXTURE_LANG", "en")
    fdir = os.path.join(root, "frames", lang)
    if not os.path.isdir(fdir):
        return 0
    frames = sorted(f for f in os.listdir(fdir) if f.endswith(".jpg"))
    seen: dict[str, str] = {}
    for st in wm.steps:
        for k in (st.moment.keyframe_ids if st.moment else []):
            seen.setdefault(k, fixture_frame_for(st.title))
    extra = [k for g in wm.guardrails for ev in g.evidence for k in ev.keyframe_ids]
    for i, k in enumerate(extra):
        seen.setdefault(k, frames[i % len(frames)] if frames else "003_saved.jpg")
    n = 0
    for k, fn in seen.items():
        path = os.path.join(fdir, fn)
        if os.path.isfile(path):
            _deps.st_call("put_keyframe", k, (wm.session_ids or ["fixture"])[0], path, None,
                          {"fixture": True, "workflow_id": wm.workflow_id})
            n += 1
    return n


@router.post("/api/knowledge/seed")
async def seed_ep() -> dict:
    return {"workflow_ids": sorted(set(await seed_fixtures_async()))}


def register(bus: Any) -> None:
    global _registered
    _deps.BUS = bus
    if _registered:
        return
    _registered = True
    from . import builder, debrief, lookup, nudges, tutor

    bus.subscribe("session.ended", builder.on_session_ended)
    bus.subscribe("ws.in.hello", tutor.on_hello)
    bus.subscribe("ws.in.hello", debrief.on_hello)
    bus.subscribe("ws.in.activity", tutor.on_activity)
    bus.subscribe("ws.in.nudge_response", nudges.on_response)
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

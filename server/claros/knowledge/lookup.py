"""Workflow lookup (FTS + embeddings + O*NET overlap + app match) and capture requests (the honest fake door).

POST /api/workflows/lookup {screen_state?, utterance, lang} → {match?: {workflow_id, score, coverage}, onet?, action}
action: "learn" (ready) | "teach_confirmed" (partial) | "request" (missing / no match)
GET/POST /api/requests · POST /api/requests/{id}/accept
GET /api/workflows · /api/workflows/{id} · /api/workflows/{id}/coverage · /api/learners/{id}/mastery
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from claros.models import CaptureRequest, Moment, OnetMatch, ScreenState, User, WorkMap

from . import _deps as d
from .common import compute_coverage, list_maps, load_map, map_doc_text

router = APIRouter()

MATCH_THRESHOLD = 0.35
W = {"fts": 0.3, "emb": 0.4, "onet": 0.15, "app": 0.15}


class LookupReq(BaseModel):
    screen_state: Optional[ScreenState] = None
    utterance: str = ""
    lang: str = "en"


def _query_text(utterance: str, ss: Optional[ScreenState]) -> str:
    parts = [utterance or ""]
    if ss:
        parts += [ss.app or "", ss.view or "", ss.entity_type or ""] + [f.label for f in ss.fields[:20]]
    return " ".join(p for p in parts if p)


async def lookup(utterance: str, screen_state: Optional[ScreenState] = None, lang: str = "en") -> dict[str, Any]:
    q = _query_text(utterance, screen_state)
    maps = list_maps()
    onet = await d.onet_match(q) if q.strip() else None
    out: dict[str, Any] = {"match": None, "onet": onet.model_dump(mode="json") if onet else None, "action": "request"}
    if not maps or not q.strip():
        return out
    fts = {r["id"]: r["score"] for r in (d.st_call("search_text", "workflows", q, 20, default=[]) or [])}
    fmax = max(fts.values(), default=0) or 1.0
    vecs = await d.embed([q] + [map_doc_text(m) for m in maps], "retrieval.query")
    qv = vecs[0]
    best: Optional[tuple[float, WorkMap]] = None
    for m, mv in zip(maps, vecs[1:]):
        s_fts = max(fts.get(m.workflow_id, 0.0), 0.0) / fmax
        s_emb = max(d.cosine(qv, mv), 0.0)
        s_onet = 0.0
        if onet and m.onet and onet.occupation_code == m.onet.occupation_code:
            s_onet = 1.0 if (onet.task_id and onet.task_id == m.onet.task_id) else 0.7
        s_app = 1.0 if (screen_state and screen_state.app and screen_state.app in m.apps) else 0.0
        score = W["fts"] * s_fts + W["emb"] * s_emb + W["onet"] * s_onet + W["app"] * s_app
        if best is None or score > best[0]:
            best = (score, m)
    if best and best[0] >= MATCH_THRESHOLD:
        cov = compute_coverage(best[1])
        out["match"] = {"workflow_id": best[1].workflow_id, "score": round(best[0], 3),
                        "coverage": cov.model_dump(mode="json"), "name": best[1].name}
        out["action"] = {"ready": "learn", "partial": "teach_confirmed"}.get(cov.status, "request")
    return out


@router.post("/api/workflows/lookup")
async def lookup_ep(req: LookupReq) -> dict[str, Any]:
    return await lookup(req.utterance, req.screen_state, req.lang)


@router.get("/api/workflows")
async def list_ep() -> list[dict[str, Any]]:
    return [{"workflow_id": m.workflow_id, "name": m.name, "version": m.version, "apps": m.apps,
             "experts": [e.model_dump() for e in m.experts], "coverage": compute_coverage(m).model_dump(),
             "onet": m.onet.model_dump() if m.onet else None, "steps": len(m.steps),
             "guardrails": len(m.guardrails)} for m in list_maps()]


@router.get("/api/workflows/{workflow_id}")
async def get_ep(workflow_id: str, version: Optional[int] = None) -> dict[str, Any]:
    m = load_map(workflow_id, version)
    if m is None:
        raise HTTPException(404, "workflow not found")
    from .merge import merged_payload
    return merged_payload(m)


@router.get("/api/workflows/{workflow_id}/coverage")
async def coverage_ep(workflow_id: str) -> dict[str, Any]:
    m = load_map(workflow_id)
    if m is None:
        raise HTTPException(404, "workflow not found")
    return compute_coverage(m).model_dump()


# ---------------- capture requests ----------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "workflow").lower()).strip("_")[:40] or "workflow"


def get_request(rid: str) -> Optional[dict]:
    return d.st_call("kv_get", "requests", rid)


def set_request_status(rid: str, status: str) -> Optional[dict]:
    r = get_request(rid)
    if r:
        r["status"] = status
        d.st_call("kv_put", "requests", rid, r)
    return r


async def create_request(workflow_hint: str, requested_by: User | dict, moment: Optional[Moment | dict] = None,
                         onet: Optional[OnetMatch] = None, workflow_id: Optional[str] = None) -> dict:
    by = requested_by if isinstance(requested_by, User) else User.model_validate(requested_by)
    mom = Moment.model_validate(moment) if isinstance(moment, dict) else moment
    if onet is None:
        onet = await d.onet_match(workflow_hint)
    req = CaptureRequest(id=d.new_id("req"), workflow_hint=workflow_hint, requested_by=by, moment=mom, onet=onet,
                         created_at=d.now_ms() / 1000)
    data = {**req.model_dump(mode="json"),
            "workflow_id": workflow_id or f"wf_{_slug(workflow_hint)}_{d.new_id('x')[-6:]}"}
    d.st_call("kv_put", "requests", req.id, data)
    await d.publish("_global", "request.created", data)
    return data


@router.get("/api/requests")
async def list_requests_ep(status: Optional[str] = None) -> list[dict]:
    rs = d.st_call("kv_list", "requests", default=[]) or []
    return [r for r in rs if status is None or r.get("status") == status]


@router.post("/api/requests")
async def create_request_ep(body: dict = Body(...)) -> dict:
    if not body.get("workflow_hint") or not body.get("requested_by"):
        raise HTTPException(422, "workflow_hint and requested_by required")
    return await create_request(body["workflow_hint"], body["requested_by"], body.get("moment"),
                                workflow_id=body.get("workflow_id"))


@router.post("/api/requests/{rid}/accept")
async def accept_request_ep(rid: str, body: Optional[dict] = Body(None)) -> dict:
    r = set_request_status(rid, "accepted")
    if r is None:
        raise HTTPException(404, "request not found")
    out: dict[str, Any] = {"request": r, "session_id": None, "mode": "capture", "workflow_id": r.get("workflow_id")}
    user = (body or {}).get("user")
    try:
        from claros.session import sessions  # type: ignore
        s = sessions.create(mode="capture", user=User.model_validate(user) if user else None,
                            lang=(body or {}).get("lang", "en"), workflow_id=r.get("workflow_id"))
        s.extra["request_id"] = rid
        sessions.save(s)
        out["session_id"] = s.id
    except Exception:  # noqa: BLE001
        d.log.debug("session create on accept failed", exc_info=True)
    return out


async def on_map_updated(session_id: str, payload: Any) -> None:
    """Close requests whose workflow got a map; tell learners (global status)."""
    wid = (payload or {}).get("workflow_id") if isinstance(payload, dict) else None
    if not wid:
        return
    for r in d.st_call("kv_list", "requests", default=[]) or []:
        if r.get("workflow_id") == wid and r.get("status") in ("accepted", "recorded"):
            m = load_map(wid)
            if m and m.approved_by:
                set_request_status(r["id"], "done")
                await d.send("_global", {"type": "status", "level": "info",
                                         "text": f"“{r['workflow_hint']}” is ready to learn.",
                                         "workflow_id": wid, "request_id": r["id"]})


# ---------------- mastery ----------------

@router.get("/api/learners/{learner_id}/mastery")
async def mastery_ep(learner_id: str, workflow_id: str) -> dict:
    from .tutor import load_mastery
    return {k: v.model_dump() for k, v in load_mastery(learner_id, workflow_id).items()}

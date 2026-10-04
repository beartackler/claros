"""Workflow lookup (FTS + embeddings + O*NET overlap + app match) and capture requests (the honest fake door).

POST /api/workflows/lookup {screen_state?, utterance, lang} → {match?: {workflow_id, score, coverage}, onet?, action}
action: "learn" (ready) | "teach_confirmed" (partial) | "request" (missing / no match)
GET/POST /api/requests · POST /api/requests/{id}/accept
GET /api/workflows · /api/workflows/{id} · /api/workflows/{id}/coverage · /api/learners/{id}/mastery
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from claros.models import CaptureRequest, Moment, OnetMatch, ScreenState, User, WorkMap

from . import _deps as d
from .common import compute_coverage, list_maps, load_map, map_doc_text

router = APIRouter()

MATCH_THRESHOLD = 0.35
REQUEST_MATCH = 0.55  # merging two people's requests / closing a request needs more than "a map is about invoices"
W = {"fts": 0.3, "emb": 0.4, "onet": 0.15, "app": 0.15, "screen": 0.25}


def _labels_of_state(ss: Optional[ScreenState]) -> set[str]:
    from .common import norm_label
    if ss is None:
        return set()
    on = {norm_label(re.sub(r"\s*\*$", "", f.label or "")) for f in ss.fields}
    for tb in ss.tables or []:
        on |= {norm_label(re.sub(r"\s*\*$", "", str(c))) for c in tb.get("columns") or []}
    on.discard("")
    return on


def _map_labels(m: WorkMap) -> set[str]:
    from .common import norm_label
    out = {norm_label(re.sub(r"\s*\*$", "", a)) for al in m.canonical_vars.values() for a in al}
    out.discard("")
    return out


def screen_overlap(m: WorkMap, ss: Optional[ScreenState]) -> float:
    """Share of the map's recorded on-screen labels visible on the learner's screen (recorded on THIS screen)."""
    labels = _map_labels(m)
    return len(labels & _labels_of_state(ss)) / len(labels) if labels and ss is not None else 0.0


# ---------------- ONE matcher for maps and requests ----------------

@dataclass
class Candidate:
    """Anything a task description can match: a Work Map or a learner's capture request."""
    key: str
    text: str
    apps: list[str] = field(default_factory=list)
    onet: Optional[OnetMatch] = None
    labels: set[str] = field(default_factory=set)
    fts_ns: Optional[str] = None  # FTS index namespace if the candidate is indexed (workflows)
    obj: Any = None


def map_candidate(m: WorkMap) -> Candidate:
    return Candidate(key=m.workflow_id, text=map_doc_text(m), apps=list(m.apps), onet=m.onet, labels=_map_labels(m),
                     fts_ns="workflows", obj=m)


def screen_state_for(session_id: Optional[str], t: Optional[float] = None) -> Optional[ScreenState]:
    """Latest ScreenState of a session (live perception first, else the session log, at or before `t`)."""
    if not session_id:
        return None
    try:
        from claros.perception import current_state
        st = current_state(session_id)
        if st is not None and (t is None or st.t <= t + 5_000):
            return st
    except Exception:  # noqa: BLE001
        pass
    best = None
    for e in d.st_call("iter_log", session_id, ["screen.state"], default=[]) or []:
        try:
            s_ = ScreenState.model_validate(e.get("payload"))
        except Exception:  # noqa: BLE001
            continue
        if t is None or s_.t <= t + 5_000:
            best = s_ if (best is None or (s_.app or s_.fields) or not (best.app or best.fields)) else best
    return best


def request_candidate(r: dict) -> Candidate:
    mom = r.get("moment") or {}
    ss = screen_state_for(mom.get("session_id"), mom.get("t")) if isinstance(mom, dict) else None
    onet = None
    try:
        onet = OnetMatch.model_validate(r["onet"]) if r.get("onet") else None
    except Exception:  # noqa: BLE001
        pass
    return Candidate(key=r["id"], text=_query_text(r.get("workflow_hint") or "", ss),
                     apps=[ss.app] if ss and ss.app else [], onet=onet, labels=_labels_of_state(ss), obj=r)


def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2}


async def score_candidates(query: str, ss: Optional[ScreenState], cands: list[Candidate],
                           onet: Optional[OnetMatch] = None, *, query_labels: Optional[set[str]] = None,
                           query_apps: Optional[list[str]] = None,
                           normalize: bool = False) -> list[tuple[float, Candidate]]:
    """fts (index or token overlap) + embedding + O*NET + app + screen-label overlap, same weights everywhere.
    normalize=True rescales by the weights of the signals BOTH sides have (a request without a screen moment can't
    score app/screen) — used for request↔request and map→request matching (threshold REQUEST_MATCH)."""
    if not cands or not query.strip():
        return []
    fts: dict[str, float] = {}
    for ns in {c.fts_ns for c in cands if c.fts_ns}:
        for r in d.st_call("search_text", ns, query, 50, default=[]) or []:
            fts[r["id"]] = max(fts.get(r["id"], 0.0), r["score"])
    fmax = max(fts.values(), default=0) or 1.0
    qt = _toks(query)
    vecs = await d.embed([query] + [c.text for c in cands], "retrieval.query")
    qv = vecs[0]
    labels = query_labels if query_labels is not None else _labels_of_state(ss)
    apps = set(query_apps or ([ss.app] if ss and ss.app else []))
    out = []
    for c, cv in zip(cands, vecs[1:]):
        ct = _toks(c.text)  # unindexed candidates (requests): word overlap, symmetric (a map vs a short hint)
        s_fts = (max(fts.get(c.key, 0.0), 0.0) / fmax) if c.fts_ns else (
            len(qt & ct) / max(1, min(len(qt), len(ct))))
        s_emb = max(d.cosine(qv, cv), 0.0)
        s_onet = 0.0
        if onet and c.onet and onet.occupation_code == c.onet.occupation_code:
            s_onet = 1.0 if (onet.task_id and onet.task_id == c.onet.task_id) else 0.7
        s_app = 1.0 if apps & set(c.apps) else 0.0
        s_scr = len(c.labels & labels) / len(c.labels) if c.labels and labels else 0.0
        score = W["fts"] * s_fts + W["emb"] * s_emb + W["onet"] * s_onet + W["app"] * s_app + W["screen"] * s_scr
        if normalize:
            avail = W["fts"] + W["emb"] + (W["onet"] if onet and c.onet else 0) + (W["app"] if apps and c.apps else 0) \
                + (W["screen"] if labels and c.labels else 0)
            score = score / avail
        out.append((round(score, 4), c))
    return sorted(out, key=lambda x: -x[0])


class LookupReq(BaseModel):
    screen_state: Optional[ScreenState] = None
    utterance: str = ""
    lang: str = "en"
    session_id: Optional[str] = None  # the learner's live session: its latest screen is used when screen_state is absent


def _query_text(utterance: str, ss: Optional[ScreenState]) -> str:
    parts = [utterance or ""]
    if ss:
        parts += [ss.app or "", ss.view or "", ss.entity_type or ""] + [f.label for f in ss.fields[:20]]
    return " ".join(p for p in parts if p)


async def lookup(utterance: str, screen_state: Optional[ScreenState] = None, lang: str = "en",
                 session_id: Optional[str] = None) -> dict[str, Any]:
    if screen_state is None and session_id:
        screen_state = screen_state_for(session_id)
    q = _query_text(utterance, screen_state)
    maps = list_maps()
    onet = await d.onet_match(q) if q.strip() else None
    out: dict[str, Any] = {"match": None, "onet": onet.model_dump(mode="json") if onet else None, "action": "request"}
    if not maps or not q.strip():
        return out
    ranked = await score_candidates(q, screen_state, [map_candidate(m) for m in maps], onet)
    if ranked and ranked[0][0] >= MATCH_THRESHOLD:
        sc, c = ranked[0]
        m = c.obj
        cov = compute_coverage(m)
        out["match"] = {"workflow_id": m.workflow_id, "score": round(sc, 3),
                        "coverage": cov.model_dump(mode="json"), "name": m.name}
        out["action"] = {"ready": "learn", "partial": "teach_confirmed"}.get(cov.status, "request")
        try:  # the learner is about to start: translate the map for their language in the background
            from .tutor import prewarm
            prewarm(m, lang)
        except Exception:  # noqa: BLE001
            pass
    return out


async def match_map_for_request(r: dict) -> Optional[tuple[float, WorkMap]]:
    """Which existing map covers this request's task (hint + the learner's screen at the request moment)?"""
    maps = list_maps()
    c = request_candidate(r)
    if not maps or not c.text.strip():
        return None
    onet = OnetMatch.model_validate(r["onet"]) if r.get("onet") else None
    ranked = await score_candidates(c.text, None, [map_candidate(m) for m in maps], onet,
                                    query_labels=c.labels, query_apps=c.apps)
    return (ranked[0][0], ranked[0][1].obj) if ranked and ranked[0][0] >= MATCH_THRESHOLD else None


OPEN_REQUEST = ("open", "accepted", "recorded")


async def match_open_requests(query: str, ss: Optional[ScreenState] = None, onet: Optional[OnetMatch] = None, *,
                              labels: Optional[set[str]] = None, apps: Optional[list[str]] = None,
                              exclude: Optional[str] = None) -> list[tuple[float, dict]]:
    reqs = [r for r in d.st_call("kv_list", "requests", default=[]) or []
            if r.get("status") in OPEN_REQUEST and r.get("id") != exclude]
    ranked = await score_candidates(query, ss, [request_candidate(r) for r in reqs], onet,
                                    query_labels=labels, query_apps=apps, normalize=True)
    return [(sc, c.obj) for sc, c in ranked if sc >= REQUEST_MATCH]


@router.post("/api/workflows/lookup")
async def lookup_ep(req: LookupReq) -> dict[str, Any]:
    return await lookup(req.utterance, req.screen_state, req.lang, req.session_id)


@router.get("/api/workflows")
async def list_ep() -> list[dict[str, Any]]:
    """Latest version of each workflow, most recently updated first (updated_at = epoch ms)."""
    upd = {w.get("workflow_id"): w.get("updated_at") for w in (d.st_call("list_workflows", default=[]) or [])}
    items = [{"workflow_id": m.workflow_id, "updated_at": upd.get(m.workflow_id) or 0, "name": m.name, "version": m.version, "apps": m.apps,
             "experts": [e.model_dump() for e in m.experts], "coverage": compute_coverage(m).model_dump(),
             "onet": m.onet.model_dump() if m.onet else None, "steps": len(m.steps),
             "guardrails": len(m.guardrails)} for m in list_maps()]
    return sorted(items, key=lambda x: -(x["updated_at"] or 0))


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
    # the same task asked again (another learner, other words, same screen) joins the open request
    ss = screen_state_for(mom.session_id, mom.t) if mom else None
    try:
        hits = await match_open_requests(_query_text(workflow_hint, ss), ss, onet)
    except Exception:  # noqa: BLE001
        d.log.debug("request matching failed", exc_info=True)
        hits = []
    if hits:
        r = hits[0][1]
        everyone = r.get("requested_by_all") or [r.get("requested_by")]
        if not any((x or {}).get("id") == by.id for x in everyone):
            everyone.append(by.model_dump(mode="json"))
        r["requested_by_all"] = [x for x in everyone if x]
        r["count"] = len(r["requested_by_all"])
        r["merged"] = True
        d.st_call("kv_put", "requests", r["id"], r)
        await d.publish("_global", "request.updated", r)
        return r
    req = CaptureRequest(id=d.new_id("req"), workflow_hint=workflow_hint, requested_by=by, moment=mom, onet=onet,
                         created_at=d.now_ms())  # epoch ms
    data = {**req.model_dump(mode="json"),
            "workflow_id": workflow_id or f"wf_{_slug(workflow_hint)}_{d.new_id('x')[-6:]}",
            "requested_by_all": [by.model_dump(mode="json")], "count": 1}
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


DEFAULT_EXPERT = {"id": "expert", "name": "Expert", "role": "expert"}


@router.post("/api/requests/{rid}/accept")
async def accept_request_ep(rid: str, body: Optional[dict] = Body(None)) -> dict:
    r = set_request_status(rid, "accepted")
    if r is None:
        raise HTTPException(404, "request not found")
    # the task already has a map (another expert, or recorded since the request): continue THAT workflow
    second_run = False
    try:
        if load_map(r.get("workflow_id") or "") is not None:
            second_run = True
        else:
            hit = await match_map_for_request(r)
            if hit:
                r["workflow_id"], second_run = hit[1].workflow_id, True
                d.st_call("kv_put", "requests", rid, r)
    except Exception:  # noqa: BLE001
        d.log.debug("request → map matching failed", exc_info=True)
    out: dict[str, Any] = {"request": r, "session_id": None, "mode": "capture", "workflow_id": r.get("workflow_id"),
                           "second_run": second_run}
    user = (body or {}).get("user") or DEFAULT_EXPERT
    try:
        from claros.session import sessions  # type: ignore
        s = sessions.create(mode="capture", user=User.model_validate(user),
                            lang=(body or {}).get("lang", "en"), workflow_id=r.get("workflow_id"))
        s.extra.update(request_id=rid, workflow_hint=r.get("workflow_hint"), moment=r.get("moment"),
                       requested_by=r.get("requested_by"), second_run=second_run)
        sessions.save(s)
        out["session_id"] = s.id
        out["session"] = s.model_dump(mode="json")
    except Exception:  # noqa: BLE001
        d.log.debug("session create on accept failed", exc_info=True)
    return out


async def answer_requests(wm: WorkMap) -> list[dict]:
    """A published map (expert confirmed) answers every open request for its task, whoever recorded it and whether
    or not the capture came from a request: same matcher, query = the map. Matched → done + learners notified."""
    if not wm.approved_by:
        return []
    out = []
    reqs = [r for r in d.st_call("kv_list", "requests", default=[]) or [] if r.get("status") in OPEN_REQUEST]
    if not reqs:
        return []
    own = [r for r in reqs if r.get("workflow_id") == wm.workflow_id]
    try:
        ranked = await score_candidates(map_doc_text(wm), None, [request_candidate(r) for r in reqs if r not in own],
                                        wm.onet, query_labels=_map_labels(wm), query_apps=list(wm.apps),
                                        normalize=True)
    except Exception:  # noqa: BLE001
        ranked = []
    hits = own + [c.obj for sc, c in ranked if sc >= REQUEST_MATCH]
    for r in hits:
        r = {**r, "status": "done", "workflow_id": wm.workflow_id}
        d.st_call("kv_put", "requests", r["id"], r)
        out.append(r)
        msg = {"type": "status", "level": "info", "text": f"“{r['workflow_hint']}” is ready to learn.",
               "workflow_id": wm.workflow_id, "request_id": r["id"]}
        await d.send("_global", msg)
        mom = r.get("moment") or {}
        if isinstance(mom, dict) and mom.get("session_id"):
            await d.send(mom["session_id"], msg)
        await d.publish("_global", "request.done", r)
    return out


async def on_map_updated(session_id: str, payload: Any) -> None:
    """Close requests whose task got a published map (any capture, not only request-accepted ones)."""
    wid = (payload or {}).get("workflow_id") if isinstance(payload, dict) else None
    m = load_map(wid) if wid else None
    if m is not None:
        await answer_requests(m)


# ---------------- mastery ----------------

@router.get("/api/learners/{learner_id}/mastery")
async def mastery_ep(learner_id: str, workflow_id: str) -> list[dict]:
    from .tutor import BKT, load_bkt, load_mastery
    m = {k: v.model_dump() for k, v in load_mastery(learner_id, workflow_id).items()}
    for node, b in load_bkt(learner_id, workflow_id).items():
        m.setdefault(node, {"step_id": node, "level": "unseen", "attempts": 0})
        m[node].update(p_known=b.get("p", BKT["p_init"]), mastered=bool(b.get("mastered")))
    return list(m.values())  # MasteryNode[] (+ p_known/mastered); [] when empty

"""MCP server (FastMCP): get_workflow, find_guardrails, check_action, quote.

Transports (both public, no host/origin guard — this is an internet-facing server):
  - Streamable HTTP at POST /mcp (stateless, JSON responses) — what ElevenLabs uses (transport STREAMABLE_HTTP)
  - SSE at GET /mcp/sse (+ POST /messages/) for older clients
`mount(app)` inserts the routes into the FastAPI router and registers the streamable session manager's lifespan in
`app.state.lifespans` (entered by claros.app's lifespan). Plain functions are importable for tests.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from claros.models import Field_, Guardrail, ScreenState, WorkMap

from . import _deps as d
from .common import (
    canonical_vars_from_state, eval_predicate, list_maps, load_map, norm_label, parse_number, prior_vars,
    quote_by_id, quote_text,
)

# guardrail.action → what an agent must do (contract v2.2: stop | ask | warn)
ACTION = {"block_and_explain": "stop", "hold": "stop", "stop_and_ask": "ask", "warn": "warn"}
ENFORCE = {"block_and_explain": "stop", "hold": "hold", "stop_and_ask": "ask", "warn": "warn"}


def guardrail_title(g: Guardrail, n: int = 40) -> str:
    t = (g.text or "").strip()
    head = t.split(":", 1)[0].strip() if ":" in t[:60] else t
    head = re.sub(r"\s+", " ", head).rstrip(".")
    if len(head) <= n:
        return head
    cut = head[:n].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return cut if len(cut) >= 8 else head[:n]


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"\w{4,}", (text or "").lower())}


def guardrail_quote(wm: WorkMap, g: Guardrail) -> Optional[Any]:
    """The quote that states THIS rule (most word overlap), else the first."""
    qs = [q for q in (quote_by_id(wm, i) for i in g.quote_ids) if q]
    if not qs:
        return None
    rs = _stems(g.text)
    best = max(qs, key=lambda q: len(rs & _stems(q.text)))
    return best if rs & _stems(best.text) else qs[0]


def _expert(wm: WorkMap, g: Guardrail, q: Any = None) -> Optional[str]:
    if q is not None:
        return q.speaker
    names = {e.id: e.name for e in wm.experts}
    for e in g.experts:
        if e in names:
            return names[e]
    return wm.experts[0].name if wm.experts else None


def state_vars(wm: WorkMap, state: dict, seen: Optional[list[dict]] = None) -> dict[str, Any]:
    """state = a ScreenState dict ({fields:[...]}) or a flat {on-screen label | canonical var: value} dict (labels in
    any language the map has seen as aliases). `seen` = other records (flat dicts) for prior.* session-memory vars."""
    def to_ss(flat: dict) -> ScreenState:
        fields = []
        for k, v in flat.items():
            if v is None or isinstance(v, (dict, list)):
                continue
            canon = k if k in wm.canonical_vars or k.startswith("doc.") else None
            num = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            fields.append(Field_(label=k, canonical=canon, value=str(v), normalized=num))
        return ScreenState(seq=0, t=0, fields=fields)

    if "fields" in state or "seq" in state:
        ss = ScreenState.model_validate({"seq": 0, "t": 0, **state})
    else:
        ss = to_ss(state)
    vars_ = canonical_vars_from_state(ss, wm)
    for k, v in list(vars_.items()):  # "8,400.00" typed by an agent → number
        if isinstance(v, str) and re.fullmatch(r"[\s€$£₽]*[\d.,'\s]+[\s€$£₽]*", v):
            n = parse_number(v)
            if n is not None:
                vars_[k] = n
    mem = {f"r{i}": canonical_vars_from_state(to_ss(r), wm) for i, r in enumerate(seen or []) if isinstance(r, dict)}
    pv = prior_vars(vars_, mem, None)
    pv.pop("prior.match_ids", None)
    vars_.update(pv)
    return vars_


def check(wm: WorkMap, state: dict, action: Optional[str] = None, seen: Optional[list[dict]] = None) -> dict:
    vars_ = state_vars(wm, state or {}, seen)
    violations, not_evaluable, judge = [], [], []
    for g in wm.guardrails:
        q = guardrail_quote(wm, g)
        item = {"guardrail_id": g.id, "title": guardrail_title(g), "rule": g.text,
                "quote": q.text if q else None, "expert": _expert(wm, g, q)}
        if not g.predicate:
            judge.append(item)
            continue
        r = eval_predicate(g.predicate, vars_)
        if r:
            violations.append({**item, "id": g.id, "action": ACTION.get(g.action, "stop"), "owner": g.owner})
        elif r is None:
            not_evaluable.append(g.id)
    allowed = not any(v["action"] in ("stop", "ask") for v in violations)
    return {"workflow_id": wm.workflow_id, "version": wm.version, "action": action, "allowed": allowed,
            "violations": violations, "not_evaluable": not_evaluable, "judge_yourself": judge,
            "vars": {k: v for k, v in vars_.items() if not k.startswith("prior.") or v}}


def public_base(request: Any = None) -> str:
    base = os.getenv("CLAROS_PUBLIC_BASE") or ""
    if not base and request is not None:
        base = str(request.base_url)
    return (base or "https://claros-server.onrender.com").rstrip("/")




def _map(workflow_id: Optional[str]):
    if workflow_id:
        return load_map(workflow_id)
    maps = list_maps()
    return maps[0] if maps else None


def get_workflow(workflow_id: Optional[str] = None) -> dict:
    """Get the expert-approved Work Map of a workflow (steps in order, judgment calls with the expert's reasons,
    guardrails = stop conditions, expert quotes). Omit workflow_id to get the most recent workflow."""
    wm = _map(workflow_id)
    if wm is None:
        return {"error": "not found"}
    from .merge import merged_payload
    return merged_payload(wm)


def find_guardrails(query: str, workflow_id: Optional[str] = None) -> list[dict]:
    """Search guardrails (limits, never-do rules, when to stop and ask someone) by keywords in any language.
    Returns each rule with what to do (stop/ask/warn), who to ask, and the expert's own words."""
    maps = [load_map(workflow_id)] if workflow_id else list_maps()
    qt = set(norm_label(query).split())
    out = []
    for wm in filter(None, maps):
        for g in wm.guardrails:
            q = guardrail_quote(wm, g)
            words = set(norm_label(g.text + " " + (q.text if q else "")).split())
            score = len(qt & words) / (len(qt) or 1)
            if score > 0 or not qt:
                out.append({"workflow_id": wm.workflow_id, "score": round(score, 3), "id": g.id,
                            "title": guardrail_title(g), "rule": g.text, "action": g.action, "owner": g.owner,
                            "quote": q.text if q else None, "expert": q.speaker if q else None,
                            "deterministic": bool(g.predicate)})
    return sorted(out, key=lambda x: -x["score"])[:10]


def check_action(state: dict, workflow_id: Optional[str] = None, action: Optional[str] = None) -> dict:
    """Check a record BEFORE saving/submitting it. state = {field label or canonical var: value}, e.g.
    {"Amount": 7200, "Expense Head": "Tools and Small Equipment"}. action = "save" | "submit".
    Returns allowed (false = do NOT proceed), violations [{title, rule, quote, expert, action: stop|ask|warn,
    owner}], not_evaluable (fields missing), judge_yourself (rules that need judgment: read them and stop if
    unsure)."""
    wm = _map(workflow_id)
    if wm is None:
        return {"error": "no workflow"}
    return check(wm, state or {}, action)


def quote(id: str, workflow_id: Optional[str] = None, lang: str = "en") -> list[dict]:
    """The expert's exact words behind a step's decision or a guardrail, by step id or guardrail id
    (with a translation into `lang` when available)."""
    wm = _map(workflow_id)
    if wm is None:
        return []
    ids: list[str] = []
    for s in wm.steps:
        if s.id == id and s.decision:
            ids = s.decision.reason_quote_ids
    for g in wm.guardrails:
        if g.id == id:
            ids = g.quote_ids
    return [{"id": q.id, "speaker": q.speaker, "lang": q.lang, "text": q.text, "text_in_lang": quote_text(q, lang)}
            for q in (quote_by_id(wm, i) for i in ids) if q]


def build_server() -> Any:
    from fastmcp import FastMCP  # type: ignore
    mcp = FastMCP("claros", instructions="Claros Work Maps: expert-captured workflows with guardrails. Call "
                                         "check_action before any save/submit; obey stop/ask results.")
    mcp.tool(get_workflow)
    mcp.tool(find_guardrails)
    mcp.tool(check_action)
    mcp.tool(quote)
    return mcp


def mount(app: Any, path: str = "/mcp") -> bool:
    try:
        srv = build_server()
        http = srv.http_app(path=path, stateless_http=True, json_response=True, host_origin_protection=False)
        sse = srv.http_app(path=f"{path}/sse", transport="sse", host_origin_protection=False)
        routes = list(http.routes) + list(sse.routes)
        app.router.routes[0:0] = routes  # before any catch-all
        lifespans = getattr(app.state, "lifespans", None)
        if lifespans is None:
            lifespans = app.state.lifespans = []
        lifespans.append(http.lifespan)
        return True
    except Exception:  # noqa: BLE001
        d.log.warning("MCP mount failed (fastmcp missing?)", exc_info=True)
        return False

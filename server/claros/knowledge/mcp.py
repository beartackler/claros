"""MCP server (FastMCP) mounted at /mcp: get_workflow, find_guardrails, check_action, quote.

Plain functions are importable for tests; `mount(app)` mounts the HTTP transport (needs its lifespan,
so we use the SSE transport which works without one).
"""
from __future__ import annotations

from typing import Any, Optional

from claros.models import Field_, ScreenState

from . import _deps as d
from .common import (
    canonical_vars_from_state, eval_predicate, list_maps, load_map, norm_label, quote_by_id, quote_text,
)


def _map(workflow_id: Optional[str]):
    if workflow_id:
        return load_map(workflow_id)
    maps = list_maps()
    return maps[0] if maps else None


def get_workflow(workflow_id: str) -> dict:
    wm = load_map(workflow_id)
    if wm is None:
        return {"error": "not found"}
    from .merge import merged_payload
    return merged_payload(wm)


def find_guardrails(query: str, workflow_id: Optional[str] = None) -> list[dict]:
    maps = [load_map(workflow_id)] if workflow_id else list_maps()
    qt = set(norm_label(query).split())
    out = []
    for wm in filter(None, maps):
        for g in wm.guardrails:
            words = set(norm_label(g.text).split())
            score = len(qt & words) / (len(qt) or 1)
            if score > 0 or not qt:
                out.append({"workflow_id": wm.workflow_id, "score": round(score, 3), **g.model_dump(mode="json")})
    return sorted(out, key=lambda x: -x["score"])[:10]


def check_action(state: dict, workflow_id: Optional[str] = None) -> dict:
    """state: a ScreenState dict, or a flat {canonical_var: value} dict. Deterministic predicate checks only."""
    wm = _map(workflow_id)
    if wm is None:
        return {"error": "no workflow"}
    if "fields" in state or "seq" in state:
        ss = ScreenState.model_validate({"seq": 0, "t": 0, **state})
        vars_ = canonical_vars_from_state(ss, wm)
    else:
        vars_ = canonical_vars_from_state(ScreenState(seq=0, t=0, fields=[
            Field_(label=k, canonical=k if "." in k else None, value=str(v), normalized=v
                   if isinstance(v, (int, float)) else None) for k, v in state.items()]), wm)
    violations, unevaluable, fuzzy = [], [], []
    for g in wm.guardrails:
        if g.predicate:
            r = eval_predicate(g.predicate, vars_)
            if r:
                violations.append({"id": g.id, "text": g.text, "action": g.action, "owner": g.owner})
            elif r is None:
                unevaluable.append(g.id)
        else:
            fuzzy.append({"id": g.id, "text": g.text, "action": g.action})
    return {"workflow_id": wm.workflow_id, "allowed": not violations, "violations": violations,
            "not_evaluable": unevaluable, "judge_yourself": fuzzy, "vars": vars_}


def quote(step_id: str, workflow_id: Optional[str] = None, lang: str = "en") -> list[dict]:
    wm = _map(workflow_id)
    if wm is None:
        return []
    ids: list[str] = []
    for s in wm.steps:
        if s.id == step_id and s.decision:
            ids = s.decision.reason_quote_ids
    for g in wm.guardrails:
        if g.id == step_id:
            ids = g.quote_ids
    return [{"id": q.id, "speaker": q.speaker, "lang": q.lang, "text": q.text, "text_in_lang": quote_text(q, lang)}
            for q in (quote_by_id(wm, i) for i in ids) if q]


def build_server() -> Any:
    from fastmcp import FastMCP  # type: ignore
    mcp = FastMCP("claros")
    mcp.tool(get_workflow)
    mcp.tool(find_guardrails)
    mcp.tool(check_action)
    mcp.tool(quote)
    return mcp


def mount(app: Any, path: str = "/mcp") -> bool:
    try:
        srv = build_server()
        app.mount(path, srv.http_app(path="/", transport="sse"))
        return True
    except Exception:  # noqa: BLE001
        d.log.warning("MCP mount failed (fastmcp missing?)", exc_info=True)
        return False

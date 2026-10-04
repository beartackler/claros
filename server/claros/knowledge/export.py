"""Agent-loadable skill export (Claude Agent Skills / ChatGPT skills format).

GET /api/export/{workflow_id}.skill.md   → SKILL.md (YAML frontmatter name/description + instructions)

The body is instructions for an agent: steps in the expert's words, guardrails as STOP CONDITIONS with the expert's
quote, and exactly how to run the same guardrail check the tutor uses (MCP `check_action` or REST /check) before any
save/submit. Unconfirmed parts are flagged; nothing open-ended is dumped.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from claros.models import WorkMap

from .mcp import ENFORCE, guardrail_quote, guardrail_title, public_base
from .common import compute_coverage, load_map, ordered_steps, quote_by_id

router = APIRouter()

_RESERVED = re.compile(r"\b(claude|anthropic)\b")


def skill_name(wm: WorkMap) -> str:
    """lowercase letters, digits, hyphens; ≤64 chars; no reserved words (Agent Skills frontmatter rules)."""
    base = (wm.name or wm.workflow_id or "workflow").lower()
    base = _RESERVED.sub("", base)
    s = re.sub(r"[^a-z0-9]+", "-", base.encode("ascii", "ignore").decode()).strip("-")
    s = re.sub(r"-{2,}", "-", s) or re.sub(r"[^a-z0-9]+", "-", wm.workflow_id.lower()).strip("-") or "workflow"
    return s[:64].rstrip("-")


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON string = valid YAML double-quoted scalar


def skill_description(wm: WorkMap) -> str:
    apps = f" in {', '.join(wm.apps)}" if wm.apps else ""
    experts = ", ".join(e.name for e in wm.experts) or "an expert"
    g = len(wm.guardrails)
    d = (f"Performs the \"{wm.name}\" workflow{apps} the way {experts} does it: {len(wm.steps)} steps, the judgment "
         f"calls with the expert's reasons, and {g} guardrail{'s' if g != 1 else ''} that say when to stop and ask a "
         f"human. Use when asked to do or review this task, and before saving or submitting any record of it.")
    return d[:1024]


def _q(wm: WorkMap, qids: list[str], lang: Optional[str] = None) -> Optional[str]:
    for qid in qids:
        q = quote_by_id(wm, qid)
        if q:
            txt = q.text
            if lang and lang != q.lang and q.translations.get(lang):
                txt = f"{q.translations[lang]} (original {q.lang}: “{q.text}”)"
            return f"“{txt}” — {q.speaker}"
    return None


def _example_state(wm: WorkMap) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in wm.guardrails:
        if not g.predicate:
            continue
        from .common import predicate_vars
        for v in sorted(predicate_vars(g.predicate)):
            if v.startswith(("prior.", "doc.")) or v in out:
                continue
            label = (wm.canonical_vars.get(v) or [v])[0]
            out[label] = "<number on screen>" if re.search(r"amount|total|price|sum|qty", v) else "<value on screen>"
        if len(out) >= 4:
            break
    return out or {"<field label>": "<value>"}


def to_skill_md(wm: WorkMap, lang: Optional[str] = "en", base: Optional[str] = None) -> str:
    base = (base or public_base(None)).rstrip("/")
    cov = compute_coverage(wm)
    name = skill_name(wm)
    L = ["---", f"name: {name}", f"description: {_yaml_str(skill_description(wm))}", "---", "",
         f"# {wm.name}", ""]
    experts = ", ".join(e.name for e in wm.experts) or "the expert"
    L += [f"This skill was captured by Claros from {experts} doing the task on screen and explaining why "
          f"(Work Map `{wm.workflow_id}`, version {wm.version}). Follow it like a careful new hire: do the steps, "
          f"respect every stop condition, and never invent a rule that is not written here.", ""]
    if cov.status != "ready":
        L += ["> Not fully confirmed by the expert yet. Steps marked *(unconfirmed)* may be wrong: when one of them "
              "matters, stop and ask a human.", ""]
    L += ["## Steps", ""]
    for s in ordered_steps(wm):
        flag = "" if s.approved else " *(unconfirmed)*"
        L.append(f"{s.order}. **{s.title}**{flag}")
        if s.decision and s.decision.kind == "judgment":
            ch = f" ({s.decision.from_value} → {s.decision.to_value})" if s.decision.to_value else ""
            L.append(f"   - Judgment call: {s.decision.description}{ch}")
            if s.decision.counterfactual:
                L.append(f"   - It would be different if: {s.decision.counterfactual}")
            q = _q(wm, s.decision.reason_quote_ids, lang)
            if q:
                L.append(f"   - Why, in the expert's words: {q}")
        for v in s.variants:
            L.append(f"   - Another expert's way: {v.description}" +
                     (f" — {_q(wm, v.reason_quote_ids, lang)}" if _q(wm, v.reason_quote_ids, lang) else ""))
        if s.conflict:
            L.append(f"   - Experts disagree here ({s.conflict}): STOP and ask a human lead.")
        gs = [g for g in wm.guardrails if g.id in s.guardrail_ids]
        if gs:
            L.append("   - Stop conditions here: " + ", ".join(f"`{g.id}`" for g in gs))
    L += ["", "## Stop conditions (guardrails)", "",
          "Each one is a rule the expert stated. If it applies, do not save or submit — do what it says.", ""]
    verb = {"stop": "STOP: do not save or submit; tell the user why", "ask": "STOP and ask",
            "warn": "WARN the user before continuing", "hold": "STOP: put the record on hold"}
    for g in wm.guardrails:
        enf = ENFORCE.get(g.action, "stop")
        then = verb[enf]
        if enf == "ask":
            then += f" {g.owner or 'a human'}"
        elif g.owner and enf in ("stop", "hold"):
            then += f", then ask {g.owner}"
        L.append(f"- **{guardrail_title(g)}** (`{g.id}`): {g.text.rstrip('.')}.")
        L.append(f"  - Then: {then}.")
        q = guardrail_quote(wm, g)
        if q:
            L.append(f"  - Expert: “{q.text}” — {q.speaker}")
        L.append("  - Checked automatically by `check_action`." if g.predicate else
                 "  - Needs judgment: read the record; if you are not sure it does NOT apply, stop and ask.")
    if not wm.guardrails:
        L.append("- none recorded")
    ex = json.dumps(_example_state(wm), ensure_ascii=False)
    L += ["", "## Before you save or submit", "",
          "Run the same check the human tutor uses, with the values currently on the record:", "",
          f"Call the MCP tool `check_action` (server `{base}/mcp`, Streamable HTTP; SSE at `{base}/mcp/sse`) with:",
          "",
          f"`{{\"workflow_id\": \"{wm.workflow_id}\", \"action\": \"submit\", \"state\": {ex}}}`", "",
          "If `allowed` is false: do not proceed; tell the user which rule applies, quote the expert, and do what "
          "`action` says (`stop`, `ask` the owner, `warn`). Rules listed under `judge_yourself` need your judgment: "
          "when unsure, stop and ask.", "",
          "## Anything not covered", "",
          "If the case is not covered by these steps and rules, stop and ask a human. Never invent a threshold, "
          "approver or rule.", ""]
    if wm.canonical_vars:
        L += ["## Field names (for `check_action`)", ""]
        L += [f"- `{k}`: {', '.join(v[:4])}" for k, v in wm.canonical_vars.items()]
        L.append("")
    return "\n".join(L)


@router.get("/api/export/{name}")
async def export_ep(name: str, request: Request, lang: Optional[str] = "en") -> PlainTextResponse:
    wid = name[:-len(".skill.md")] if name.endswith(".skill.md") else name
    wm = load_map(wid)
    if wm is None:
        raise HTTPException(404, "workflow not found")
    return PlainTextResponse(to_skill_md(wm, lang, public_base(request)), media_type="text/markdown; charset=utf-8")


__all__ = ["router", "to_skill_md", "skill_name", "skill_description"]

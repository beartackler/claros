"""GET /api/export/{workflow_id}.skill.md — agent-readable SKILL.md (steps, guardrails, predicates, stops, quotes)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from claros.models import WorkMap

from .common import compile_guardrails, compute_coverage, load_map, ordered_steps, quote_by_id

router = APIRouter()


def _quote_md(wm: WorkMap, qids: list[str], lang: Optional[str] = None) -> list[str]:
    out = []
    for qid in qids:
        q = quote_by_id(wm, qid)
        if not q:
            continue
        line = f"  > “{q.text}” — {q.speaker} ({q.lang})"
        if lang and lang != q.lang and q.translations.get(lang):
            line += f"\n  > _{lang}: {q.translations[lang]}_"
        out.append(line)
    return out


def to_skill_md(wm: WorkMap, lang: Optional[str] = "en") -> str:
    cov = compute_coverage(wm)
    desc = f"How experts do “{wm.name}”" + (f" ({', '.join(wm.apps)})" if wm.apps else "") + \
        ". Use when performing this workflow; obey guardrails and stop conditions."
    L = ["---", f"name: {wm.workflow_id}", f"description: {json.dumps(desc, ensure_ascii=False)}",
         f"version: {wm.version}", f"coverage: {cov.status}", "---", "", f"# {wm.name}", ""]
    if wm.onet:
        L += [f"O*NET: {wm.onet.occupation_code} {wm.onet.occupation_title}" +
              (f" — task: {wm.onet.task}" if wm.onet.task else ""), ""]
    L += [f"Experts: {', '.join(e.name for e in wm.experts) or '—'}. Coverage: {cov.status} "
          f"(open questions {cov.open_unknowns}, conflicts {cov.conflicts}).", ""]
    if cov.status != "ready":
        L += ["**Not fully confirmed.** Only follow approved steps; for anything else stop and ask a human.", ""]
    L += ["## Steps", ""]
    for s in ordered_steps(wm):
        flag = "" if s.approved else " _(unconfirmed)_"
        L.append(f"{s.order}. **{s.title}**{flag}" + (f" — after: {', '.join(s.after)}" if s.after else ""))
        sig = {k: v for k, v in s.state_signature.items() if v}
        if sig:
            L.append(f"   - screen: {json.dumps(sig, ensure_ascii=False)}")
        if s.decision:
            L.append(f"   - {s.decision.kind}: {s.decision.description}" +
                     (f" ({s.decision.from_value} → {s.decision.to_value})" if s.decision.to_value else ""))
            if s.decision.counterfactual:
                L.append(f"   - would change if: {s.decision.counterfactual}")
            L += ["  " + x for x in _quote_md(wm, s.decision.reason_quote_ids, lang)]
        if s.guardrail_ids:
            L.append(f"   - guardrails: {', '.join(s.guardrail_ids)}")
        for v in s.variants:
            L.append(f"   - variant ({v.expert_id}): {v.description}")
        if s.conflict:
            L.append(f"   - ⚠ experts disagree: {s.conflict} → ask a human lead")
    L += ["", "## Guardrails", ""]
    for g in wm.guardrails:
        L.append(f"- **{g.id}** [{g.action}] {g.text}" + (f" (ask: {g.owner})" if g.owner else "") +
                 (f" — experts: {', '.join(g.experts)}" if g.experts else ""))
        spec = next((x for x in compile_guardrails(wm) if x["id"] == g.id), None)
        if spec:
            L.append(f"  - enforce: {spec['enforce']} on {', '.join(spec['trigger']['on'])}; scope: "
                     f"{', '.join(spec['scope']) or 'all steps'}" +
                     (f"; exceptions: {'; '.join(spec['exceptions'])}" if spec["exceptions"] else ""))
        if g.predicate:
            L.append(f"  - predicate (json-logic): `{json.dumps(g.predicate, ensure_ascii=False)}`")
        else:
            L.append("  - fuzzy: judge from context; when unsure, stop and ask")
        L += _quote_md(wm, g.quote_ids, lang)
    stops = [g for g in wm.guardrails if g.action in ("stop_and_ask", "hold", "block_and_explain")]
    L += ["", "## Stop conditions", ""]
    L += [f"- {g.text}" + (f" → ask {g.owner}" if g.owner else "") for g in stops] or ["- none recorded"]
    L += ["- Any case not covered by these steps → stop and ask a human (do not invent rules)."]
    if wm.canonical_vars:
        L += ["", "## Variables (canonical → screen labels)", ""]
        L += [f"- `{k}`: {', '.join(v)}" for k, v in wm.canonical_vars.items()]
    if wm.open_unknowns:
        L += ["", "## Open questions", ""]
        L += [f"- [{u.type}] {u.spoken_question or u.hypothesis}" for u in wm.open_unknowns
              if u.status in ("open", "asked", "deferred")]
    if wm.context_notes:
        L += ["", "## Context", ""]
        L += [f"- {n.text} (source: {n.source})" for n in wm.context_notes]
    return "\n".join(L) + "\n"


@router.get("/api/export/{name}")
async def export_ep(name: str, lang: Optional[str] = "en") -> PlainTextResponse:
    wid = name[:-len(".skill.md")] if name.endswith(".skill.md") else name
    wm = load_map(wid)
    if wm is None:
        raise HTTPException(404, "workflow not found")
    return PlainTextResponse(to_skill_md(wm, lang), media_type="text/markdown; charset=utf-8")

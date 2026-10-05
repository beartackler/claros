"""Merge several experts' WorkMaps for one workflow into one canonical flow with variants.

- align steps: state_signature match + embedding similarity of titles/decisions (Hungarian if scipy, else greedy)
- order differences → partial order (`after` only keeps precedences all experts agree on), never a conflict
- guardrails → union (conservative), deduped, attributed
- contradictory judgment decisions → Step.conflict + Unknown(type=conflict) per expert
- consensus strength per step = experts on step / experts on map (see `consensus`)
"""
from __future__ import annotations

import json
from typing import Any, Optional

from claros.models import Decision, Guardrail, Step, Unknown, User, Variant, WorkMap

from . import _deps as d
from .common import compute_coverage, norm_label, quote_by_id

ALIGN_THRESHOLD = 0.55
GUARD_DUP_THRESHOLD = 0.8


def sig_score(a: dict, b: dict) -> float:
    keys = ("app", "view", "entity_type")
    pts = tot = 0.0
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if va is None and vb is None:
            continue
        tot += 1
        if va and vb and norm_label(va) == norm_label(vb):
            pts += 1
    return pts / tot if tot else 0.5


def _step_text(s: Step) -> str:
    return s.title + (" | " + s.decision.description if s.decision else "")


def _assign(sim: list[list[float]]) -> list[tuple[int, int]]:
    if not sim or not sim[0]:
        return []
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment  # type: ignore
        r, c = linear_sum_assignment(-np.asarray(sim))
        return list(zip(r.tolist(), c.tolist()))
    except Exception:  # noqa: BLE001
        pairs = sorted(((sim[i][j], i, j) for i in range(len(sim)) for j in range(len(sim[0]))), reverse=True)
        used_i, used_j, out = set(), set(), []
        for _, i, j in pairs:
            if i not in used_i and j not in used_j:
                used_i.add(i)
                used_j.add(j)
                out.append((i, j))
        return out


async def _similarity(a: list[Step], b: list[Step], va: Optional[list] = None,
                      vb: Optional[list] = None) -> list[list[float]]:
    if va is None or vb is None:
        import asyncio
        va, vb = await asyncio.gather(d.embed([_step_text(s) for s in a]), d.embed([_step_text(s) for s in b]))
    na, nb = max(len(a) - 1, 1), max(len(b) - 1, 1)
    sim = []
    for i, sa in enumerate(a):
        row = []
        for j, sb in enumerate(b):
            pos = 1 - abs(i / na - j / nb)  # weak positional prior
            row.append(0.35 * sig_score(sa.state_signature, sb.state_signature) + 0.5 * d.cosine(va[i], vb[j])
                       + 0.15 * pos)
        sim.append(row)
    return sim


def _norm_choice(v: Optional[str]) -> str:
    return norm_label(v or "")


def decisions_conflict(a: Optional[Decision], b: Optional[Decision]) -> bool:
    if not a or not b or a.kind != "judgment" or b.kind != "judgment":
        return False
    if not a.to_value or not b.to_value:
        return False
    x, y = _norm_choice(a.to_value), _norm_choice(b.to_value)
    return x != y and not (x in y or y in x)


def _expert_name(experts: dict[str, User], eid: str) -> str:
    u = experts.get(eid)
    return u.name if u else eid


def _reason(wm: WorkMap, dec: Decision, lang: str = "en") -> str:
    for qid in dec.reason_quote_ids:
        q = quote_by_id(wm, qid)
        if q:
            return q.translations.get(lang) or q.text
    return dec.description


def _recency(m: WorkMap) -> float:
    return max([q.t for q in m.quotes] or [0.0])


def _toks(s: str) -> set[str]:
    import re
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2 or w.isdigit()}


def near_duplicate_text(a: str, b: str, min_overlap: float = 0.6) -> bool:
    """Embedding-free dedupe: overlap coefficient of content words (works without Jina)."""
    x, y = _toks(a), _toks(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= min_overlap


async def merge_maps(maps: list[WorkMap], *, threshold: float = ALIGN_THRESHOLD,
                     newest_expert_id: Optional[str] = None) -> WorkMap:
    """newest_expert_id: the expert who recorded last — conflict questions are addressed to them (default: the map
    with the latest quote)."""
    if not maps:
        raise ValueError("no maps")
    if newest_expert_id is None:
        newest = max(maps, key=_recency)
        newest_expert_id = newest.experts[0].id if newest.experts else None
    maps = sorted(maps, key=lambda m: len(m.steps), reverse=True)
    base = maps[0].model_copy(deep=True)
    experts: dict[str, User] = {u.id: u for m in maps for u in m.experts}
    out = WorkMap(id=d.new_id("wm"), workflow_id=base.workflow_id, version=base.version, name=base.name,
                  apps=sorted({a for m in maps for a in m.apps}), onet=next((m.onet for m in maps if m.onet), None),
                  experts=list(experts.values()), session_ids=list(dict.fromkeys(s for m in maps for s in m.session_ids)))
    # quotes / notes / unknowns / canonical vars: union
    seen_q: set[str] = set()
    for m in maps:
        for q in m.quotes:
            if q.id not in seen_q:
                seen_q.add(q.id)
                out.quotes.append(q)
        for n in m.context_notes:
            if n.id not in {x.id for x in out.context_notes}:
                out.context_notes.append(n)
        for u in m.open_unknowns:
            if u.id not in {x.id for x in out.open_unknowns}:
                out.open_unknowns.append(u)
        for k, al in m.canonical_vars.items():
            cur = out.canonical_vars.setdefault(k, [])
            cur += [a for a in al if a not in cur]
    out.approved_by = list(dict.fromkeys(a for m in maps for a in m.approved_by))
    out.exam = [e for m in maps for e in m.exam]

    # ---- guardrails: union + dedupe + attribution ----
    gmap: dict[str, str] = {}  # (map idx, old gid) -> merged gid
    merged_g: list[Guardrail] = []
    gvecs: list[list[float]] = []
    for mi, m in enumerate(maps):
        vecs = await d.embed([g.text for g in m.guardrails]) if m.guardrails else []
        for g, v in zip(m.guardrails, vecs):
            dup = None
            for k, mg in enumerate(merged_g):
                if (g.predicate and mg.predicate and g.predicate == mg.predicate) or \
                        d.cosine(v, gvecs[k]) >= GUARD_DUP_THRESHOLD or \
                        (g.action == mg.action and near_duplicate_text(g.text, mg.text)):
                    dup = mg
                    break
            if dup is None:
                ng = g.model_copy(deep=True)
                ng.experts = list(dict.fromkeys(g.experts or [e.id for e in m.experts]))
                if ng.id in {x.id for x in merged_g}:
                    ng.id = f"{ng.id}_{mi}"
                merged_g.append(ng)
                gvecs.append(v)
                gmap[f"{mi}:{g.id}"] = ng.id
            else:
                g_exp = g.experts or [e.id for e in m.experts]
                same = bool(g_exp) and set(g_exp) <= set(dup.experts)  # same expert's re-capture: approval stands
                dup.experts = list(dict.fromkeys(dup.experts + g_exp))
                dup.quote_ids = list(dict.fromkeys(dup.quote_ids + g.quote_ids))
                dup.evidence += g.evidence
                dup.approved = dup.approved and (g.approved or same)
                if dup.predicate is None and g.predicate:
                    dup.predicate, dup.fuzzy = g.predicate, False
                gmap[f"{mi}:{g.id}"] = dup.id
    out.guardrails = merged_g

    # ---- steps: align every other map onto the growing merged list ----
    merged: list[Step] = []
    for s in sorted(base.steps, key=lambda s: s.order):
        ns = s.model_copy(deep=True)
        ns.experts = list(dict.fromkeys(s.experts or [e.id for e in base.experts]))
        ns.guardrail_ids = [gmap.get(f"0:{g}", g) for g in s.guardrail_ids]
        merged.append(ns)
    sequences: list[list[str]] = [[s.id for s in merged]]

    conflicts: list[tuple] = []
    for mi, m in enumerate(maps[1:], 1):
        other = sorted(m.steps, key=lambda s: s.order)
        m_experts = [e.id for e in m.experts]
        sim = await _similarity(merged, other)
        pairs = {j: i for i, j in _assign(sim) if sim[i][j] >= threshold}
        seq: list[str] = []
        prev_id: Optional[str] = None
        for j, s in enumerate(other):
            gids = [gmap.get(f"{mi}:{g}", g) for g in s.guardrail_ids]
            if j in pairs:
                tgt = merged[pairs[j]]
                same = bool(m_experts) and set(s.experts or m_experts) <= set(tgt.experts)  # re-capture: no self-variant
                tgt.experts = list(dict.fromkeys(tgt.experts + (s.experts or m_experts)))
                tgt.guardrail_ids = list(dict.fromkeys(tgt.guardrail_ids + gids))
                tgt.context_note_ids = list(dict.fromkeys(tgt.context_note_ids + s.context_note_ids))
                tgt.approved = tgt.approved and (s.approved or same)
                eid = (s.experts or m_experts or ["?"])[0]
                if same:
                    tgt.decision = tgt.decision or s.decision
                elif decisions_conflict(tgt.decision, s.decision):
                    conflicts.append((tgt, s, eid))
                elif s.decision and tgt.decision and s.decision.description != tgt.decision.description:
                    tgt.variants.append(Variant(expert_id=eid, description=s.decision.description,
                                                reason_quote_ids=s.decision.reason_quote_ids))
                elif s.decision and not tgt.decision:
                    tgt.decision = s.decision
                seq.append(tgt.id)
                prev_id = tgt.id
            else:
                ns = s.model_copy(deep=True)
                if ns.id in {x.id for x in merged}:
                    ns.id = f"{ns.id}_{mi}"
                ns.experts = s.experts or m_experts
                ns.guardrail_ids = gids
                idx = next((k for k, x in enumerate(merged) if x.id == prev_id), -1)
                merged.insert(idx + 1, ns)
                seq.append(ns.id)
                prev_id = ns.id
        sequences.append(seq)

    # ---- partial order: keep only precedences no expert contradicts ----
    pos = [{sid: k for k, sid in enumerate(seq)} for seq in sequences]
    for k, s in enumerate(merged):
        s.order = k + 1
        preds: list[str] = []
        for seq in sequences:
            if s.id in seq:
                i = seq.index(s.id)
                if i > 0:
                    preds.append(seq[i - 1])
        agreed = []
        for p in dict.fromkeys(preds):
            if all(not (p in ps and s.id in ps) or ps[p] < ps[s.id] for ps in pos):
                agreed.append(p)
            else:  # order differs between experts: fall back to the nearest commonly-agreed predecessor
                for q in reversed([x.id for x in merged[:k]]):
                    if all(not (q in ps and s.id in ps) or ps[q] < ps[s.id] for ps in pos):
                        agreed.append(q)
                        break
        s.after = list(dict.fromkeys(agreed))
    out.steps = merged
    for tgt, s, eid in conflicts:
        await _add_conflict(out, tgt, s, experts, eid, newest_expert_id)
    out.coverage = compute_coverage(out)
    return out


def _did(dec: Decision) -> str:
    return (dec.description or dec.to_value or "").strip().rstrip(".")


def _lc(s: str) -> str:
    return s[:1].lower() + s[1:] if s and not s[:2].isupper() else s


def conflict_question(other: str, other_did: str, this_did: str) -> str:
    return f"{other} {_lc(other_did)}; you {_lc(this_did)} — why?"


async def _plain_question(other: str, other_did: str, this_did: str, step_title: str) -> str:
    """Brief style: 'Anna holds December invoices; you submitted with a credit note — why?' (fast LLM, template
    fallback)."""
    tpl = conflict_question(other, other_did, this_did)
    try:
        r = await d.chat([{"role": "system", "content":
                           "Rewrite into ONE short spoken question (≤25 words) to an expert, in plain words: first what "
                           "the other expert does (name them), then what this expert did (address them as 'you'), "
                           "ending with 'why?'. No ids, no quotes, no field names. Reply JSON {\"question\": \"...\"}."},
                          {"role": "user", "content": json.dumps({"other_expert": other, "other_did": other_did,
                                                                  "you_did": this_did, "step": step_title},
                                                                 ensure_ascii=False)}],
                         model_role="fast", json_schema={"type": "object"})
        q = (r or {}).get("question") if isinstance(r, dict) else None
        if isinstance(q, str) and other.split()[0].lower() in q.lower() and 4 <= len(q.split()) <= 30:
            return q.strip()
    except Exception:  # noqa: BLE001
        pass
    return tpl


async def _add_conflict(out: WorkMap, tgt: Step, s: Step, experts: dict[str, User], eid_b: str,
                        newest_expert_id: Optional[str] = None) -> None:
    """ONE conflict unknown per disagreement, addressed to the expert who recorded most recently."""
    eid_a = (tgt.experts or ["?"])[0]
    na, nb = _expert_name(experts, eid_a), _expert_name(experts, eid_b)
    ra = _reason(out, tgt.decision)
    rb = _reason(out, s.decision)
    tgt.conflict = (f"{na} does “{tgt.decision.to_value}” because: {ra} — "
                    f"{nb} does “{s.decision.to_value}” because: {rb}")
    tgt.variants.append(Variant(expert_id=eid_b, description=s.decision.description,
                                reason_quote_ids=s.decision.reason_quote_ids))
    ask_b = newest_expert_id != eid_a  # default: the expert merged in later
    ask_id, other_id = (eid_b, eid_a) if ask_b else (eid_a, eid_b)
    mine, theirs = (s.decision, tgt.decision) if ask_b else (tgt.decision, s.decision)
    ask_name, other_name = _expert_name(experts, ask_id), _expert_name(experts, other_id)
    other_why = rb if not ask_b else ra
    other_first = other_name.split()[0] if other_name else other_name
    q = await _plain_question(other_first, _did(theirs), _did(mine), tgt.title)
    out.open_unknowns.append(Unknown(
        id=d.new_id("u"), type="conflict", scope="company", entity=tgt.id, priority=0.95,
        created_t=d.now_ms(), hypothesis=f"{ask_name}: {mine.to_value}", spoken_question=q,
        about_event_ids=[], moment=tgt.moment,
        meta={"origin": "merge", "ask_expert_id": ask_id, "ask_expert_name": ask_name,
              "other_expert_id": other_id, "other_expert_name": other_name,
              "other_did": _did(theirs), "other_why": other_why, "this_did": _did(mine),
              "step_title": tgt.title}))


def consensus(wm: WorkMap) -> dict[str, float]:
    n = max(len(wm.experts), 1)
    return {s.id: round((len(set(s.experts)) / n) * (0.5 if s.conflict else 1.0), 3) for s in wm.steps}


async def merge_workflow(workflow_id: str) -> Optional[WorkMap]:
    raw = d.st_call("kv_list", "expert_maps", f"{workflow_id}:", default=[]) or []
    maps = [WorkMap.model_validate(x) for x in raw]
    return await merge_maps(maps) if maps else None


def merged_payload(wm: WorkMap) -> dict[str, Any]:
    from .common import compile_guardrails
    return {**wm.model_dump(mode="json"), "consensus": consensus(wm), "guardrail_specs": compile_guardrails(wm)}

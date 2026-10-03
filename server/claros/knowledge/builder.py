"""Map builder: capture session log → WorkMap (on `session.ended` with mode=capture).

Pipeline: replay log → timeline prompt → llm(smart, json_schema=WorkMap) → validate (+1 repair retry)
→ deterministic repairs (nearest keyframe/utterance) → generalize entity ids (AWM-style abstraction)
→ evidence gaps become open Unknowns(type=coverage) → quotes (+translations) → O*NET → coverage → store.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from claros.models import (
    ContextNote, Guardrail, Moment, OnetMatch, Quote, ScreenEvent, ScreenState, Step, Unknown, User, WorkMap,
)

from . import _deps as d
from .common import (
    OPEN_STATUSES, compute_coverage, eval_predicate, load_map, moment_ok, new_unknown, predicate_vars,
    save_map, validate_evidence,
)

LANGS = ["en", "de", "fr", "es", "ru"]


@dataclass
class Replay:
    session_id: str
    states: list[ScreenState] = field(default_factory=list)
    events: list[ScreenEvent] = field(default_factory=list)
    utterances: list[dict] = field(default_factory=list)   # {id, t, t_end, role, text, lang}
    keyframes: dict[str, float] = field(default_factory=dict)  # id -> t
    unknowns: list[Unknown] = field(default_factory=list)
    notes: list[ContextNote] = field(default_factory=list)

    @property
    def expert_utts(self) -> list[dict]:
        return [u for u in self.utterances if u.get("role", "user") == "user"]


def _as_list(p: Any) -> list:
    if isinstance(p, dict) and "items" in p:
        return list(p["items"])
    return p if isinstance(p, list) else [p]


def replay(session_id: str) -> Replay:
    r = Replay(session_id)
    off = False
    unknowns: dict[str, Unknown] = {}
    for e in d.st_call("iter_log", session_id, default=[]) or []:
        kind = (e.get("kind") or "").lower()
        p = e.get("payload")
        try:
            if "control" in kind and isinstance(p, dict):
                a = p.get("action")
                if a == "off_record_on":
                    off = True
                elif a == "off_record_off":
                    off = False
                elif a == "strike_that":
                    for i in range(len(r.utterances) - 1, -1, -1):
                        if r.utterances[i].get("role", "user") == "user":
                            r.utterances.pop(i)
                            break
                continue
            if off:
                continue
            if "state" in kind and "agent" not in kind:
                for x in _as_list(p):
                    s = ScreenState.model_validate(x)
                    r.states.append(s)
                    if s.keyframe_id:
                        r.keyframes.setdefault(s.keyframe_id, s.t)
            elif "event" in kind:
                for x in _as_list(p):
                    ev = ScreenEvent.model_validate(x)
                    r.events.append(ev)
                    if ev.keyframe_id:
                        r.keyframes.setdefault(ev.keyframe_id, ev.t)
            elif "utterance" in kind and isinstance(p, dict) and p.get("text"):
                uid = p.get("id") or p.get("event_id") or f"utt_{e.get('id')}"
                r.utterances.append({"id": str(uid), "t": float(p.get("t_start", p.get("t", e.get("t") or 0)) or 0),
                                     "t_end": p.get("t_end"), "role": p.get("role", "user"),
                                     "text": p["text"], "lang": p.get("lang")})
            elif "keyframe" in kind and isinstance(p, dict):
                kid = p.get("id") or p.get("keyframe_id") or f"kf_{p.get('seq', e.get('id'))}"
                r.keyframes[str(kid)] = float(p.get("t", e.get("t") or 0) or 0)
            elif ("ledger" in kind or "unknown" in kind) and p:
                for x in _as_list(p.get("unknowns", p) if isinstance(p, dict) and "unknowns" in p else p):
                    if isinstance(x, dict) and "id" in x and "type" in x:
                        u = Unknown.model_validate(x)
                        unknowns[u.id] = u
            elif ("context" in kind or "note" in kind) and p:
                for x in _as_list(p):
                    if isinstance(x, dict) and {"id", "text", "scope", "source"} <= set(x):
                        r.notes.append(ContextNote.model_validate(x))
        except (ValidationError, TypeError, ValueError):
            d.log.debug("skip log entry %s", kind, exc_info=True)
    try:  # live brain ledger (authoritative for unknowns + context notes)
        from claros.brain import get_ledger  # type: ignore
        lg = get_ledger(session_id)
        for u in (lg.unknowns.values() if isinstance(lg.unknowns, dict) else lg.unknowns):
            uu = u if isinstance(u, Unknown) else Unknown.model_validate(u)
            unknowns[uu.id] = uu
        have = {n.id for n in r.notes}
        for n in getattr(lg, "context_notes", None) or []:
            nn = n if isinstance(n, ContextNote) else ContextNote.model_validate(n)
            if nn.id not in have:
                r.notes.append(nn)
    except Exception:  # noqa: BLE001
        pass
    r.unknowns = list(unknowns.values())
    return r


# ---------------- prompt ----------------

def _fmt_t(t: float) -> str:
    return f"{t / 1000:.1f}s"


def timeline(r: Replay, max_lines: int = 500) -> str:
    rows: list[tuple[float, str]] = []
    last_sig = None
    for s in r.states:
        sig = (s.app, s.view, s.entity_type, s.status)
        if sig == last_sig:
            continue
        last_sig = sig
        flds = ", ".join(f"{f.label}={f.value}" + (f"[{f.canonical}]" if f.canonical else "")
                         for f in s.fields[:14])
        rows.append((s.t, f"STATE kf={s.keyframe_id} app={s.app} view={s.view} entity={s.entity_type} "
                          f"status={s.status} lang={s.ui_lang} fields: {flds}"))
    for ev in r.events:
        rows.append((ev.t, f"EVENT {ev.id} {ev.kind} kf={ev.keyframe_id} field={ev.field}[{ev.canonical}] "
                           f"{ev.old!r}->{ev.new!r} src={ev.source} :: {ev.summary}"))
    for u in r.utterances:
        rows.append((u["t"], f"SAY {u['id']} ({u['role']}, {u.get('lang') or '?'}): {u['text']}"))
    for u in r.unknowns:
        rows.append((u.created_t, f"LEDGER {u.id} {u.type}/{u.scope} status={u.status} q={u.spoken_question!r} "
                                  f"answer={u.resolution!r} rule={u.extracted_rule.model_dump() if u.extracted_rule else None}"))
    rows.sort(key=lambda x: x[0])
    lines = [f"[{_fmt_t(t)}] {s}" for t, s in rows]
    if len(lines) > max_lines:
        lines = lines[:50] + ["..."] + lines[-(max_lines - 51):]
    return "\n".join(lines)


SYSTEM = """You turn a recorded expert work session into a Work Map (JSON matching the schema).
Rules:
- Steps describe the GENERAL action (Agent Workflow Memory abstraction): never mention specific record ids,
  amounts or names of this one case ("invoice 4471" -> "the invoice"); abstract case values into {slots}
  named after canonical vars (e.g. "Set {line.expense_account} for each line"). Thresholds/rules go into
  decisions/guardrails.
- Segment by sub-goal: each step = one sub-goal with its precondition screen state.
- Merge micro-events into meaningful steps (typically 4-12). order = 1..n; after = ids of prerequisite steps.
- state_signature = {"app","view","entity_type"} copied from STATE lines.
- EVERY step and EVERY guardrail must cite evidence: step.moment / guardrail.evidence[] =
  {"session_id","keyframe_ids":[kf ids from the log],"t":ms,"utterance_ids":[SAY ids from the log]}.
  Only use ids that appear in the log. If no evidence exists, leave moment null (it becomes an open question).
- Decisions: kind "judgment" when the expert chose among options for a reason; reason_quote_ids = SAY ids
  that explain why; counterfactual = what would change the decision.
- Guardrails: limits, never-do, stop-and-ask rules the expert stated or the ledger resolved. quote_ids = SAY ids.
  predicate = json-logic over canonical vars (e.g. {">":[{"var":"line.amount"},5000]}) when expressible,
  using only ops: and, or, !, ==, !=, >, >=, <, <=, in, var. For dates use "<x>_month" / "<x>_year" derived vars.
  If not expressible: predicate null, fuzzy true. action in block_and_explain|warn|stop_and_ask|hold; owner = who to ask.
- canonical_vars: {"entity.field": [every on-screen label alias seen, in any language]}.
- open_unknowns: anything still unclear (type why|limit|stop_and_ask|never|deliberate|coverage).
- quotes: leave empty (we build them from SAY ids). Return ONLY JSON."""


async def _llm_map(r: Replay, wm_seed: dict, errors: Optional[list[str]] = None,
                   prev: Optional[dict] = None) -> Optional[dict]:
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Session {r.session_id}. Seed fields (keep): {json.dumps(wm_seed)}\n\n"
                                        f"LOG:\n{timeline(r)}"}]
    if errors:
        msgs += [{"role": "assistant", "content": json.dumps(prev or {})[:20000]},
                 {"role": "user", "content": "Your map failed validation. Fix ONLY these problems and return the "
                                             "full corrected JSON:\n- " + "\n- ".join(errors)}]
    out = await d.chat(msgs, model_role="smart", json_schema=WorkMap)
    return out if isinstance(out, dict) else None


SELF_CONSISTENCY_N = 3
MAJORITY_SIM = 0.6


async def self_consistent_map(r: Replay, seed: dict, n: Optional[int] = None) -> Optional[dict]:
    """Sample n candidate maps; keep steps supported by a majority, minority steps → open unknowns."""
    import asyncio
    n = n or SELF_CONSISTENCY_N
    cands = [c for c in await asyncio.gather(*(_llm_map(r, seed) for _ in range(n))) if c]
    valid = [(c, _schema_errors(c)[0]) for c in cands]
    valid = [(c, w) for c, w in valid if w is not None]
    if len(valid) < 2:
        return cands[0] if cands else None
    from .merge import _similarity
    support: list[list[int]] = []
    for i, (_, wi) in enumerate(valid):
        sup = [1] * len(wi.steps)
        for j, (_, wj) in enumerate(valid):
            if i == j or not wi.steps or not wj.steps:
                continue
            sim = await _similarity(wi.steps, wj.steps)
            for k in range(len(wi.steps)):
                if max(sim[k]) >= MAJORITY_SIM:
                    sup[k] += 1
        support.append(sup)
    need = len(valid) // 2 + 1
    best = max(range(len(valid)), key=lambda i: (sum(x >= need for x in support[i]), -len(valid[i][1].steps)))
    raw, wm = valid[best]
    raw = json.loads(json.dumps(raw))
    keep = [st_raw for st_raw, sup in zip(raw.get("steps", []), support[best]) if sup >= need]
    minority, seen_t = [], set()
    for (c, _), sup_c in zip(valid, support):
        for st_raw, sup in zip(c.get("steps", []), sup_c):
            key = (st_raw.get("title") or "").strip().lower()
            if sup < need and key not in seen_t:
                seen_t.add(key)
                minority.append(st_raw)
    raw["steps"] = keep
    unk = raw.setdefault("open_unknowns", [])
    for m in minority:
        unk.append(new_unknown("coverage", f"I'm not sure “{m.get('title')}” is a real step. Is it?",
                               entity=m.get("id"), priority=0.3,
                               moment=Moment.model_validate(m["moment"]) if m.get("moment") else None,
                               hypothesis=m.get("title")).model_dump(mode="json"))
    kept = {k.get("id") for k in keep}
    for k in keep:
        k["after"] = [a for a in k.get("after", []) if a in kept]
    return raw


def _schema_errors(raw: Optional[dict]) -> tuple[Optional[WorkMap], list[str]]:
    if raw is None:
        return None, ["no JSON returned"]
    try:
        return WorkMap.model_validate(raw), []
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:20]]


# ---------------- deterministic repairs ----------------

def _nearest(items: dict[str, float], t: float, window: float) -> Optional[str]:
    best = None
    for k, kt in items.items():
        dt = abs((kt or 0) - t)
        if dt <= window and (best is None or dt < best[0]):
            best = (dt, k)
    return best[1] if best else None


def _repair_moment(m: Optional[Moment], r: Replay, window_ms: float = 15000) -> Optional[Moment]:
    if m is None:
        return None
    kfs = [k for k in m.keyframe_ids if k in r.keyframes]
    utt_ids = {u["id"] for u in r.utterances}
    us = [u for u in m.utterance_ids if u in utt_ids]
    if not kfs:
        k = _nearest(r.keyframes, m.t, window_ms)
        kfs = [k] if k else []
    if not us:
        u = _nearest({x["id"]: x["t"] for x in r.expert_utts}, m.t, window_ms)
        us = [u] if u else []
    return Moment(session_id=r.session_id, keyframe_ids=kfs, t=m.t, utterance_ids=us)


def _entity_tokens(r: Replay) -> set[str]:
    toks: set[str] = set()
    for s in r.states:
        if s.entity_id:
            toks.add(s.entity_id)
    for ev in r.events:
        if ev.entity_id:
            toks.add(ev.entity_id)
    return {t for t in toks if t and len(t) >= 3}


def generalize(text: Optional[str], tokens: set[str]) -> Optional[str]:
    if not text:
        return text
    out = text
    for t in sorted(tokens, key=len, reverse=True):
        out = re.sub(rf"(\b\w+\s+)?(#\s*)?{re.escape(t)}\b", lambda m: "the " + (m.group(1) or "record ").strip(),
                     out)
    return re.sub(r"\bthe the\b", "the", out)


async def translate_quotes(quotes: list[Quote], langs: list[str] = LANGS) -> None:
    todo = [q for q in quotes if any(lg != q.lang and lg not in q.translations for lg in langs)]
    if not todo:
        return
    payload = {q.id: {"lang": q.lang, "text": q.text} for q in todo}
    out = await d.chat([
        {"role": "system", "content": "Translate each quote faithfully (keep tone, numbers, names) into every "
                                      f"language of {langs} except its own. Return JSON "
                                      '{"<id>": {"<lang>": "<text>"}}.'},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], model_role="fast", json_schema={
        "type": "object"})
    if isinstance(out, dict):
        for q in todo:
            tr = out.get(q.id)
            if isinstance(tr, dict):
                q.translations.update({k: v for k, v in tr.items() if isinstance(v, str) and k != q.lang})


def _fallback_map(r: Replay, seed: dict) -> dict:
    """No LLM: one step per (view, entity_type) segment with significant events."""
    steps: list[dict] = []
    cur_sig = None
    for ev in r.events:
        st = next((s for s in reversed(r.states) if s.t <= ev.t), None)
        sig = (ev.app or (st.app if st else None), st.view if st else None, ev.entity_type)
        if sig != cur_sig or ev.kind in ("submit", "hold", "escalate", "approve", "reject"):
            cur_sig = sig
            steps.append({"id": f"s{len(steps) + 1}", "order": len(steps) + 1, "title": ev.summary or ev.kind,
                          "state_signature": {"app": sig[0], "view": sig[1], "entity_type": sig[2]},
                          "moment": {"session_id": r.session_id, "keyframe_ids": [ev.keyframe_id] if ev.keyframe_id
                                     else [], "t": ev.t, "utterance_ids": []}})
    for i, s in enumerate(steps[1:], 1):
        s["after"] = [steps[i - 1]["id"]]
    return {**seed, "steps": steps}


# ---------------- main ----------------

async def build_map(session_id: str, *, workflow_id: Optional[str] = None, expert: Optional[User] = None,
                    persist: bool = True) -> WorkMap:
    sess = d.get_session(session_id)
    expert = expert or getattr(sess, "user", None) or User(id="expert", name="Expert", role="expert")
    if isinstance(expert, dict):
        expert = User.model_validate(expert)
    r = replay(session_id)
    lang = (getattr(sess, "lang", None) or "en")[:2]
    workflow_id = workflow_id or getattr(sess, "workflow_id", None)
    seed = {"id": d.new_id("wm"), "workflow_id": workflow_id or "TBD", "name": "", "session_ids": [session_id]}

    raw = await self_consistent_map(r, seed)
    wm, errs = _schema_errors(raw)
    if wm is not None:
        errs = validate_evidence(_with_repairs(wm, r), set(r.keyframes), {u["id"] for u in r.utterances})
    if errs and raw is not None:
        raw2 = await _llm_map(r, seed, errs, raw)
        wm2, errs2 = _schema_errors(raw2)
        if wm2 is not None:
            # keep self-consistency verdicts: minority steps stay out, their unknowns stay in
            dropped = {(u.hypothesis or "").strip().lower() for u in (wm.open_unknowns if wm else [])
                       if u.type == "coverage" and u.hypothesis}
            wm2.steps = [s_ for s_ in wm2.steps if s_.title.strip().lower() not in dropped]
            have = {u.id for u in wm2.open_unknowns}
            wm2.open_unknowns += [u for u in (wm.open_unknowns if wm else []) if u.id not in have]
            wm = wm2
    if wm is None:
        wm = WorkMap.model_validate(_fallback_map(r, {**seed, "name": "Untitled workflow"}))

    wm = _with_repairs(wm, r)
    # force identity fields
    if not workflow_id:
        slug = re.sub(r"[^a-z0-9]+", "_", (wm.name or "workflow").lower()).strip("_")[:40] or "workflow"
        workflow_id = f"wf_{slug}_{d.new_id('x')[-6:]}"
    wm.workflow_id = workflow_id
    wm.id = d.new_id("wm")
    wm.session_ids = [session_id]
    wm.experts = [expert]
    wm.apps = wm.apps or sorted({s.app for s in r.states if s.app})
    wm.quotes = []
    wm.approved_by = []

    # generalize across entities
    toks = _entity_tokens(r)
    for s in wm.steps:
        s.title = generalize(s.title, toks) or s.title
        s.experts = [expert.id]
        s.approved = False
        if s.decision:
            s.decision.description = generalize(s.decision.description, toks) or s.decision.description
    for g in wm.guardrails:
        g.experts = [expert.id]
        g.approved = False

    # quotes from SAY ids
    utt = {u["id"]: u for u in r.utterances}
    used: list[str] = []

    def qids(ids: list[str]) -> list[str]:
        out = []
        for i in ids:
            uid = i[2:] if i.startswith("q_") and i[2:] in utt else i
            if uid in utt:
                out.append("q_" + uid)
                used.append(uid)
        return out

    for s in wm.steps:
        if s.decision:
            s.decision.reason_quote_ids = qids(s.decision.reason_quote_ids)
            if not s.decision.reason_quote_ids and s.decision.kind == "judgment" and s.moment:
                s.decision.reason_quote_ids = qids(s.moment.utterance_ids)
    for g in wm.guardrails:
        g.quote_ids = qids(g.quote_ids) or qids([u for m in g.evidence for u in m.utterance_ids])
    for uid in dict.fromkeys(used):
        u = utt[uid]
        wm.quotes.append(Quote(id="q_" + uid, speaker=expert.name, speaker_id=expert.id, lang=(u.get("lang") or lang)[:2],
                               text=u["text"], t=u["t"], session_id=session_id, source="live"))
    await translate_quotes(wm.quotes)

    # predicates: vars must be canonical; otherwise fuzzy
    known_vars = set(wm.canonical_vars)
    derived = {k[:-5] + suf for k in known_vars if k.endswith("_date") for suf in ("_month", "_year")}
    for g in wm.guardrails:
        if g.predicate:
            vs = predicate_vars(g.predicate)
            probe = {v: 1 for v in vs}
            bad = [v for v in vs if v not in known_vars | derived and not v.startswith("doc.")]
            if bad:
                for v in bad:  # keep predicate; register canonical var so it can be mapped later
                    wm.canonical_vars.setdefault(v, [v.split(".")[-1].replace("_", " ")])
            try:
                eval_predicate(g.predicate, probe)
            except Exception:  # noqa: BLE001
                g.predicate, g.fuzzy = None, True
        if not g.predicate:
            g.fuzzy = True

    # evidence gaps → open Unknowns (coverage)
    gaps = []
    for s in wm.steps:
        if not moment_ok(s.moment):
            gaps.append(new_unknown("coverage", f"I missed why or how you did “{s.title}”. Can you explain?",
                                    moment=s.moment, entity=s.id, priority=0.4))
    for g in wm.guardrails:
        if not any(moment_ok(m) for m in g.evidence):
            gaps.append(new_unknown("coverage", f"Is this really your rule: “{g.text}”?", hypothesis=g.text,
                                    entity=g.id, priority=0.7))
    ledger_open = [u for u in r.unknowns if u.status in OPEN_STATUSES and u.scope in ("company", "personal_judgment")]
    seen = {u.id for u in wm.open_unknowns}
    wm.open_unknowns = [u for u in wm.open_unknowns if u.status in OPEN_STATUSES] + \
        [u for u in ledger_open if u.id not in seen] + gaps
    notes_ids = {n.id for n in wm.context_notes}
    wm.context_notes += [n for n in r.notes if n.id not in notes_ids]
    # resolved non-expert ledger answers become cited context
    for u in r.unknowns:
        if u.status in ("answered", "resolved") and u.resolution and u.resolution_source and \
                u.resolution_source != "expert" and f"cn_{u.id}" not in notes_ids:
            wm.context_notes.append(ContextNote(id=f"cn_{u.id}", text=u.resolution, scope=u.scope,
                                                source=u.resolution_source))

    # O*NET
    if wm.onet is None:
        wm.onet = await d.onet_match(" ".join([wm.name] + [s.title for s in wm.steps]))
    wm.coverage = compute_coverage(wm)
    if persist:
        wm = await publish_expert_map(wm, session_id)
    return wm


def _with_repairs(wm: WorkMap, r: Replay) -> WorkMap:
    for s in wm.steps:
        s.moment = _repair_moment(s.moment, r)
    for g in wm.guardrails:
        g.evidence = [m for m in (_repair_moment(m, r) for m in g.evidence) if m]
    return wm


async def publish_expert_map(wm: WorkMap, session_id: Optional[str] = None) -> WorkMap:
    """Store per-expert map; if other experts mapped the workflow, publish the merge (what learners see)."""
    eid = wm.experts[0].id if wm.experts else "expert"
    d.st_call("kv_put", "expert_maps", f"{wm.workflow_id}:{eid}", wm.model_dump(mode="json"))
    maps = [WorkMap.model_validate(x) for x in d.st_call("kv_list", "expert_maps", f"{wm.workflow_id}:", default=[]) or []]
    if len(maps) > 1:
        from .merge import merge_maps
        merged = await merge_maps(maps)
        prev = load_map(wm.workflow_id)
        if prev:
            merged.exam = prev.exam
        return await save_map(merged, session_id)
    return await save_map(wm, session_id)


async def on_session_ended(session_id: str, payload: Any) -> None:
    mode = (payload or {}).get("mode") if isinstance(payload, dict) else None
    if mode is None:
        mode = getattr(d.get_session(session_id), "mode", None)
    if mode != "capture":
        return
    wm = await build_map(session_id)
    await d.send(session_id, {"type": "status", "level": "info",
                              "text": f"Work map built: {len(wm.steps)} steps, {len(wm.guardrails)} guardrails, "
                                      f"{len(wm.open_unknowns)} open questions."})
    sess = d.get_session(session_id)
    rid = (getattr(sess, "extra", None) or {}).get("request_id")
    if rid:
        from .lookup import set_request_status
        set_request_status(rid, "recorded")

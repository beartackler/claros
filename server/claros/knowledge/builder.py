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
    OPEN_STATUSES, PRIOR_VARS, compute_coverage, well_formed, eval_predicate, load_map, moment_ok, new_unknown, predicate_vars,
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
    entries = list(d.st_call("iter_log", session_id, default=[]) or [])
    # "strike that" tombstones the last 30 s in the log (claros.privacy); older logs only have the control row
    tombstoned = any((e.get("kind") or "") == "privacy.struck" for e in entries)
    for e in entries:
        kind = (e.get("kind") or "").lower()
        p = e.get("payload")
        if kind.startswith("struck:") or kind.startswith("privacy."):
            continue
        try:
            if "control" in kind and isinstance(p, dict):
                a = p.get("action")
                if a == "off_record_on":
                    off = True
                elif a == "off_record_off":
                    off = False
                elif a == "strike_that" and not tombstoned:
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
            elif "utterance" in kind and isinstance(p, dict) and p.get("text") and p["text"] != "[off the record]":
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
        if u.status == "dropped":
            continue
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
- Merge micro-events into meaningful steps: a step = a sub-goal a new hire must do (typically 5-9 for a normal
  session). order = 1..n; after = ids of prerequisite steps.
- state_signature = {"app","view","entity_type"} copied from STATE lines.
- EVERY step and EVERY guardrail must cite evidence: step.moment / guardrail.evidence[] =
  {"session_id","keyframe_ids":[kf ids from the log],"t":ms,"utterance_ids":[SAY ids from the log]}.
  Only use ids that appear in the log. If no evidence exists, leave moment null (it becomes an open question).
- Decisions: kind "judgment" only when the expert chose differently from the default/system value for a reason
  they stated; reason_quote_ids = SAY ids that explain why; counterfactual = what would change the decision.
  Everything else is "routine".
- Guardrails: a limit, never-do or stop-and-ask rule the expert SAID (or answered in a LEDGER line). quote_ids = the
  SAY ids whose words state it. Never a rule read from the screen, a document, a ticket or a message shown on screen,
  and never one you infer from values of this case alone.
  predicate = json-logic that a program evaluates on a FUTURE screen, so it may only use canonical vars that are
  visible fields/columns in the STATE lines (each must be a key of canonical_vars with its on-screen labels), compared
  with literal values exactly as they appear on screen. Never invent derived or boolean vars (no "is_high_value",
  "is_contractor", "is_repeat_customer"). The predicate must be TRUE exactly when a learner breaks the rule on a new
  record: every CASE condition the expert stated (a category AND a threshold AND a party — all of them, not only the
  one that differed in this session) plus the wrong CHOICE, i.e. the value the expert corrected AWAY from (never the
  corrected value). Literals come from what the expert said and the screen of THIS session, never from these examples.
  Shapes (illustrative domain, not this one): {"and":[{"in":["Hardware",{"var":"claim.category"}]},{">":[{"var":
  "claim.amount"},2500]},{"in":["Auto-approve",{"var":"claim.route"}]}]} ("in" with a string = substring match), or
  {"in":["Contractor",{"var":"employee.type"}]}.
  Ops: and, or, !, ==, !=, >, >=, <, <=, in, var. For dates use "<x>_month" / "<x>_year" derived vars of a *_date var.
  Session memory vars (booleans the runtime computes from OTHER records seen earlier in the same session — list
  rows or previously opened records — usable when the map has supplier/party and amount vars):
  prior.same_supplier, prior.same_amount, prior.same_supplier_amount, prior.same_supplier_amount_in_month (same
  supplier + same amount + date in the same calendar month), prior.count. Use them for repeat / duplicate / "same as
  one already paid" rules, e.g. {"and":[{"var":"prior.same_supplier_amount"},{"==":[{"var":"<entity>.<x>_month"},<m>]}]}
  when the expert tied it to a month.
  If the rule cannot be decided from visible fields (or these memory vars): predicate null, fuzzy true.
  action in block_and_explain|warn|stop_and_ask|hold; owner = who to ask.
- canonical_vars: {"entity.field": [every on-screen label alias seen, in any language]} — grid columns too
  (e.g. "claim.amount": ["Amount (EUR)", "Betrag (EUR)"], "claim.route": ["Route", "Weiterleitung"]).
- open_unknowns: anything still unclear about the expert's reasons (type why|limit|stop_and_ask|never|deliberate|coverage),
  each with a short spoken_question (≤15 words). Ignore events that only reflect OCR noise, truncated text ("...") or
  formatting, and never ask how a value was entered (typed/pasted).
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
    """Sample n candidate maps; keep steps supported by a majority (minority steps → open unknowns), guardrails whose
    semantic cluster appears in a majority of candidates (minority guardrails are dropped), and judgment calls that a
    majority of candidates also judged (else the decision is demoted to routine)."""
    import asyncio
    n = n or SELF_CONSISTENCY_N
    cands = [c for c in await asyncio.gather(*(_llm_map(r, seed) for _ in range(n))) if c]
    valid = [(c, _schema_errors(c)[0]) for c in cands]
    valid = [(c, w) for c, w in valid if w is not None]
    if len(valid) < 2:
        return cands[0] if cands else None
    from .merge import _similarity
    support: list[list[int]] = []
    judged: list[list[int]] = []  # per step: how many candidates call the aligned step a judgment
    for i, (_, wi) in enumerate(valid):
        sup = [1] * len(wi.steps)
        jud = [int(bool(s_.decision and s_.decision.kind == "judgment")) for s_ in wi.steps]
        for j, (_, wj) in enumerate(valid):
            if i == j or not wi.steps or not wj.steps:
                continue
            sim = await _similarity(wi.steps, wj.steps)
            for k in range(len(wi.steps)):
                best_j = max(range(len(wj.steps)), key=lambda x: sim[k][x])
                if sim[k][best_j] >= MAJORITY_SIM:
                    sup[k] += 1
                    dj = wj.steps[best_j].decision
                    jud[k] += int(bool(dj and dj.kind == "judgment"))
        support.append(sup)
        judged.append(jud)
    need = len(valid) // 2 + 1
    best = max(range(len(valid)), key=lambda i: (sum(x >= need for x in support[i]), -len(valid[i][1].steps)))
    raw, wm = valid[best]
    raw = json.loads(json.dumps(raw))
    keep, demoted = [], []
    for st_raw, sup, jud in zip(raw.get("steps", []), support[best], judged[best]):
        if sup < need:
            continue
        dec = st_raw.get("decision")
        if isinstance(dec, dict) and dec.get("kind") == "judgment" and jud < need:
            dec["kind"] = "routine"
            demoted.append((st_raw.get("title") or "").strip().lower())
            d.log.info("self-consistency: judgment at %r only in %d/%d candidates → routine", st_raw.get("title"),
                       jud, len(valid))
        keep.append(st_raw)
    minority, seen_t = [], set()
    for (c, _), sup_c in zip(valid, support):
        for st_raw, sup in zip(c.get("steps", []), sup_c):
            key = (st_raw.get("title") or "").strip().lower()
            if sup < need and key not in seen_t:
                seen_t.add(key)
                minority.append(st_raw)
    raw["steps"] = keep
    raw["guardrails"] = await majority_guardrails([c for c, _ in valid], best, need)
    gids = {g.get("id") for g in raw["guardrails"]}
    for k in keep:
        k["guardrail_ids"] = [g for g in k.get("guardrail_ids", []) if g in gids]
    unk = raw.setdefault("open_unknowns", [])
    for m in minority:
        unk.append(new_unknown("coverage", f"I'm not sure “{m.get('title')}” is a real step. Is it?",
                               entity=m.get("id"), priority=0.3,
                               moment=Moment.model_validate(m["moment"]) if m.get("moment") else None,
                               hypothesis=m.get("title")).model_dump(mode="json"))
    kept = {k.get("id") for k in keep}
    for k in keep:
        k["after"] = [a for a in k.get("after", []) if a in kept]
    raw["_majority"] = {"guardrails": raw["guardrails"], "demoted": demoted}
    return raw


GUARD_CLUSTER_COS = 0.8
GUARD_CLUSTER_JACCARD = 0.5


def _content_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2 or w.isdigit()}


def _jaccard(a: str, b: str) -> float:
    x, y = _content_tokens(a), _content_tokens(b)
    return len(x & y) / max(1, len(x | y))


async def majority_guardrails(cands: list[dict], best: int, need: int) -> list[dict]:
    """Cluster the candidates' guardrails semantically (embedding cos ≥0.8 or token Jaccard ≥0.5); keep clusters found
    in ≥ need candidates. Representative = the member with the most common predicate, else the best candidate's."""
    items = [(ci, g) for ci, c in enumerate(cands) for g in (c.get("guardrails") or []) if isinstance(g, dict)]
    if not items:
        return []
    try:
        vecs = await d.embed([str(g.get("text") or "") for _, g in items])
    except Exception:  # noqa: BLE001
        vecs = []

    def sim(a: int, b: int) -> bool:
        ta, tb = str(items[a][1].get("text") or ""), str(items[b][1].get("text") or "")
        if vecs and len(vecs) == len(items) and d.cosine(vecs[a], vecs[b]) >= GUARD_CLUSTER_COS:
            return True
        return _jaccard(ta, tb) >= GUARD_CLUSTER_JACCARD

    clusters: list[list[int]] = []
    for i in range(len(items)):
        for c in clusters:
            if items[i][0] not in {items[x][0] for x in c} and any(sim(i, x) for x in c):
                c.append(i)
                break
        else:
            clusters.append([i])
    out: list[dict] = []
    used_ids: set[str] = set()
    for c in clusters:
        nsup = len({items[x][0] for x in c})
        if nsup < need:
            d.log.info("self-consistency: guardrail %r only in %d/%d candidates → dropped",
                       items[c[0]][1].get("text"), nsup, len(cands))
            continue
        preds: dict[str, int] = {}
        for x in c:
            p = items[x][1].get("predicate")
            if p:
                k = json.dumps(p, sort_keys=True)
                preds[k] = preds.get(k, 0) + 1
        own = next((x for x in c if items[x][0] == best), c[0])
        rep = own
        if preds:
            top, cnt = max(preds.items(), key=lambda kv: kv[1])
            if cnt >= 2:
                rep = next((x for x in c if items[x][0] == best and
                            json.dumps(items[x][1].get("predicate"), sort_keys=True) == top),
                           next(x for x in c if json.dumps(items[x][1].get("predicate"), sort_keys=True) == top))
        g = json.loads(json.dumps(items[rep][1]))
        gid = items[own][1].get("id") or g.get("id") or f"g{len(out) + 1}"  # keep the best candidate's id (steps cite it)
        while gid in used_ids:
            gid = f"{gid}_x"
        used_ids.add(gid)
        g["id"] = gid
        # union of cited utterances across the cluster (grounding checks them later)
        g["quote_ids"] = list(dict.fromkeys(q for x in c for q in (items[x][1].get("quote_ids") or [])))
        out.append(g)
    return out


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


def _repair_moment(m: Optional[Moment], r: Replay, window_ms: float = 15000,
                   link_utterance: bool = True) -> Optional[Moment]:
    """Fix ids against the log. Nearest-in-time utterance is fine for a STEP's screen moment; a guardrail's words
    come only from the grounding pass (link_utterance=False), never from whatever was said nearby."""
    if m is None:
        return None
    kfs = [k for k in m.keyframe_ids if k in r.keyframes]
    utt_ids = {u["id"] for u in r.utterances}
    us = [u for u in m.utterance_ids if u in utt_ids]
    if not kfs:
        k = _nearest(r.keyframes, m.t, window_ms)
        kfs = [k] if k else []
    if not us and link_utterance:
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
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], model_role="smart", json_schema={
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


# ---------------- grounding (every rule in the expert's own words) ----------------

GROUND_SYS = """Grounding check for a Work Map built from an expert's recorded work session.
For every ITEM (a guardrail rule, or the reason behind a judgment call) return the ids of the EXPERT UTTERANCES whose
words state that rule / give that reason (possibly in another language, possibly paraphrased). An utterance that only
reads or describes the screen, a document, a ticket or the values of this one case does NOT count; a rule nobody said
gets []. Reply JSON {"items": {"<item id>": ["<utterance id>", ...]}} with every item id."""
GROUND_MIN_OVERLAP = 0.34
_STOP = {"the", "and", "for", "this", "that", "with", "from", "are", "always", "never", "into", "before", "after",
         "then", "than", "when", "not", "any", "all", "our", "you", "your", "its", "has", "have", "will", "must",
         "der", "die", "das", "und", "ist", "mit", "für", "nicht", "immer"}


def _gtoks(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if (len(w) > 2 or w.isdigit()) and w not in _STOP}


def token_ground(text: str, utts: list[dict], k: int = 2) -> list[str]:
    """LLM-free fallback: utterances containing ≥34 % of the item's content words (never 'said nearby')."""
    it = _gtoks(text)
    if not it:
        return []
    scored = []
    for u in utts:
        ov = len(it & _gtoks(u.get("text") or "")) / len(it)
        if ov >= GROUND_MIN_OVERLAP:
            scored.append((ov, u["id"]))
    return [i for _, i in sorted(scored, reverse=True)[:k]]


def _ground_items(wm: WorkMap) -> dict[str, tuple[str, list[str]]]:
    items: dict[str, tuple[str, list[str]]] = {}
    for g in wm.guardrails:
        items[f"g:{g.id}"] = (g.text + (f" (ask: {g.owner})" if g.owner else ""), list(g.quote_ids))
    for s_ in wm.steps:
        if s_.decision and s_.decision.kind == "judgment":
            dd = s_.decision
            txt = dd.description + (f" ({dd.from_value} → {dd.to_value})" if dd.to_value else "")
            items[f"d:{s_.id}"] = (txt, list(dd.reason_quote_ids))
    return items


async def ground(wm: WorkMap, r: Replay) -> dict[str, list[str]]:
    """item id ('g:<guardrail id>' / 'd:<step id>') → verified utterance ids. One batched LLM call; token fallback."""
    items = _ground_items(wm)
    utts = [u for u in r.expert_utts if u.get("text")]
    if not items:
        return {}
    ids = {u["id"] for u in utts}
    out: dict[str, list[str]] = {}
    res = None
    if utts:
        payload = {"items": [{"id": k, "text": t, "cited": c} for k, (t, c) in items.items()],
                   "utterances": [{"id": u["id"], "text": str(u["text"])[:300]} for u in utts[-150:]]}
        try:
            res = await d.chat([{"role": "system", "content": GROUND_SYS},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                               model_role="fast", json_schema={"type": "object"})
        except Exception:  # noqa: BLE001
            res = None
    got = res.get("items", res) if isinstance(res, dict) else None
    llm_ok = isinstance(got, dict) and any(k in got for k in items)
    for k, (text, cited) in items.items():
        if llm_ok and k in got and isinstance(got[k], list):
            out[k] = [str(x)[2:] if str(x).startswith("q_") else str(x) for x in got[k]]
            out[k] = [x for x in dict.fromkeys(out[k]) if x in ids]
        else:
            out[k] = token_ground(text, utts)
    return out


async def apply_grounding(wm: WorkMap, r: Replay) -> list[Unknown]:
    """Guardrails nobody said are dropped; judgment calls without a stated reason become a debrief 'why'."""
    verdict = await ground(wm, r)
    utt_t = {u["id"]: u["t"] for u in r.utterances}
    unknowns: list[Unknown] = []
    keep: list[Guardrail] = []
    for g in wm.guardrails:
        uids = verdict.get(f"g:{g.id}", [])
        if not uids:
            d.log.info("grounding: guardrail %s %r is not in the expert's words → dropped", g.id, g.text)
            continue
        g.quote_ids = list(uids)
        ev = [m for m in g.evidence if m.keyframe_ids]
        if ev:
            for m in ev:
                m.utterance_ids = list(uids)
        else:
            t0 = utt_t.get(uids[0], 0.0)
            kf = _nearest(r.keyframes, t0, 60_000)
            ev = [Moment(session_id=r.session_id, keyframe_ids=[kf] if kf else [], t=t0, utterance_ids=list(uids))]
        g.evidence = ev
        keep.append(g)
    dropped = {g.id for g in wm.guardrails} - {g.id for g in keep}
    wm.guardrails = keep
    for s_ in wm.steps:
        s_.guardrail_ids = [x for x in s_.guardrail_ids if x not in dropped]
        if s_.decision and s_.decision.kind == "judgment":
            uids = verdict.get(f"d:{s_.id}", [])
            s_.decision.reason_quote_ids = list(uids)
            if not uids:
                u = new_unknown("why", f"At “{s_.title}” you {(s_.decision.description or '').rstrip('.')[:90]} — "
                                       f"what made you do that?", entity=s_.id, priority=0.6, moment=s_.moment,
                                hypothesis=s_.decision.description)
                u.meta = {"origin": "builder", "gap": "reason"}
                unknowns.append(u)
    return unknowns


_MONTHS = {1: "january januar janvier enero январ", 2: "february februar février febrero феврал",
           3: "march märz mars marzo март", 4: "april avril abril апрел", 5: "may mai mayo мая май",
           6: "june juni juin junio июн", 7: "july juli juillet julio июл", 8: "august août agosto август",
           9: "september septembre septiembre сентябр", 10: "october oktober octobre octubre октябр",
           11: "november novembre noviembre ноябр", 12: "december dezember décembre diciembre декабр"}


def _quote_corpus(wm: WorkMap, qids: list[str]) -> str:
    out = []
    for qid in qids:
        q = next((x for x in wm.quotes if x.id == qid), None)
        if q:
            out += [q.text] + list(q.translations.values())
    return " \n ".join(out).lower()


def _said_number(x: float, corpus: str) -> bool:
    from .common import parse_number
    for m in re.finditer(r"\d[\d.,' \u00a0]*\d|\d", corpus):
        v = parse_number(m.group(0).strip())
        if v is not None and abs(v - x) <= max(0.5, abs(x) * 0.005):
            return True
        km = re.match(r"\s*(k|тыс|tausend|thousand)", corpus[m.end():m.end() + 9])
        if km and v is not None and abs(v * 1000 - x) <= max(0.5, abs(x) * 0.005):
            return True
    return False


def _said_literal(lit: str, corpus: str) -> bool:
    from .common import norm_label
    n = norm_label(lit)
    if not n:
        return True
    if n in norm_label(corpus):
        return True
    toks = _gtoks(lit)
    return bool(toks) and toks <= _gtoks(corpus)


def _choice_values(wm: WorkMap, r: Replay) -> dict[str, set[str]]:
    """var → values the expert corrected AWAY from on a captured record (a predicate may legitimately name these)."""
    from .common import canonical_vars_from_state, norm_label
    out: dict[str, set[str]] = {}
    for b, a in _before_after(r):
        vb, va = canonical_vars_from_state(b, wm), canonical_vars_from_state(a, wm)
        for k, v in vb.items():
            if isinstance(v, str) and v and norm_label(str(va.get(k, ""))) != norm_label(v):
                out.setdefault(k, set()).add(norm_label(_clean_literal(v) or v))
    return out


def literal_probes(wm: WorkMap, r: Replay) -> list[Unknown]:
    """A string literal or numeric threshold in a predicate that the expert never said (and that is not the value
    they corrected away from) is probably this case's demo value (a company name, one supplier): keep the predicate
    but ask in the debrief whether the rule applies only there or more widely."""
    from .common import norm_label
    choices = _choice_values(wm, r)
    out: list[Unknown] = []
    for g in wm.guardrails:
        if not g.predicate:
            continue
        corpus = _quote_corpus(wm, g.quote_ids)
        seen: set[tuple] = set()

        def probe(var: str, lit: Any, kind: str) -> None:
            key = (var, str(lit))
            if key in seen:
                return
            seen.add(key)
            label = (wm.canonical_vars.get(var) or [var.split(".")[-1].replace("_", " ")])[0]
            u = Unknown(id=d.new_id("u"), type="limit", scope="company", entity=g.id, priority=0.85,
                        hypothesis=g.text, created_t=d.now_ms(), moment=g.evidence[0] if g.evidence else None,
                        meta={"origin": "builder", "probe": kind, "var": var, "literal": lit, "label": label})
            out.append(u)

        def walk(p: Any) -> None:
            if isinstance(p, list):
                for x in p:
                    walk(x)
                return
            if not isinstance(p, dict) or len(p) != 1:
                return
            op, args = next(iter(p.items()))
            if op in ("and", "or", "!"):
                walk(args)
                return
            if not isinstance(args, list) or len(args) != 2:
                return
            vars_ = [a["var"] for a in args if isinstance(a, dict) and "var" in a]
            lits = [a for a in args if not (isinstance(a, dict) and "var" in a)]
            if len(vars_) != 1 or len(lits) != 1:
                return
            var = vars_[0][0] if isinstance(vars_[0], list) else vars_[0]
            lit = lits[0]
            if str(var).startswith("prior.") or isinstance(lit, bool):
                return
            if isinstance(lit, (int, float)):
                if str(var).endswith("_month") and 1 <= int(lit) <= 12 and \
                        any(w in corpus for w in _MONTHS[int(lit)].split()):
                    return
                if not _said_number(float(lit), corpus):
                    probe(var, lit, "threshold")
            elif isinstance(lit, str):
                if _said_literal(lit, corpus):
                    return
                if any(norm_label(lit) and norm_label(lit) in c for c in choices.get(var, ())):
                    return  # the wrong choice the expert corrected away from — part of the rule
                probe(var, lit, "scope")

        walk(g.predicate)
    return out


def _clip_for(session_id: str, uid: str) -> Optional[str]:
    try:
        from .clips import clip_for  # type: ignore
        return clip_for(session_id, uid)
    except (ImportError, AttributeError):
        return None
    except Exception:  # noqa: BLE001
        return None


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
        # guardrail quotes/evidence are fixed by the grounding pass below (never by nearest-in-time utterances)
        errs = [e for e in errs if not (e.startswith("guardrail ") and "has no evidence moment" in e)]
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
            if wm is not None:  # majority guardrails / judgment verdicts survive the repair retry
                _keep_majority(wm2, wm, (raw or {}).get("_majority") or {})
            wm = wm2
    if wm is None:
        wm = WorkMap.model_validate(_fallback_map(r, {**seed, "name": "Untitled workflow"}))

    wm = _with_repairs(wm, r)
    # force identity fields
    if not workflow_id:
        workflow_id = await _same_expert_workflow(wm, expert.id)
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

    # grounding: every guardrail / judgment call cites the expert's own words (verified, never nearest-in-time)
    utt = {u["id"]: u for u in r.utterances}
    grounding_unknowns = await apply_grounding(wm, r)
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
    for g in wm.guardrails:
        g.quote_ids = qids(g.quote_ids)
    for uid in dict.fromkeys(used):
        u = utt[uid]
        wm.quotes.append(Quote(id="q_" + uid, speaker=expert.name, speaker_id=expert.id, lang=(u.get("lang") or lang)[:2],
                               text=u["text"], t=u["t"], session_id=session_id, source="live",
                               audio_clip=_clip_for(session_id, uid)))
    await translate_quotes(wm.quotes)

    # predicates: every var must map to a label that was actually on screen, else the guardrail is fuzzy
    # (a predicate over "item.is_equipment" can never be evaluated on a learner's screen and would never fire)
    for g in wm.guardrails:  # predicate vars the LLM named but forgot in canonical_vars ("x.amount" vs "x.amount_eur")
        for v in predicate_vars(g.predicate or {}):
            if "." in v and not v.startswith(("prior.", "doc.")) and v not in wm.canonical_vars:
                wm.canonical_vars[v] = []
    observable = observable_vars(wm, r)
    for k in [k for k, al in wm.canonical_vars.items() if not al]:
        wm.canonical_vars.pop(k)  # no on-screen label found: stays unobservable (→ fuzzy)
    derived = {base + suf for k in observable if k.endswith("_date") for base in (k[:-5], k)
               for suf in ("_month", "_year")}
    if _has_roles(observable):
        derived |= set(PRIOR_VARS)  # session memory (records seen earlier) is evaluable at runtime
    for g in wm.guardrails:
        if g.predicate:
            g.predicate = _drop_identifier_literals(_numeric_literals(g.predicate), g.id)
        if g.predicate:
            vs = predicate_vars(g.predicate)
            probe = {v: 1 for v in vs}
            bad = [v for v in vs if v not in observable | derived and not v.startswith("doc.")]
            try:
                eval_predicate(g.predicate, probe)
            except Exception:  # noqa: BLE001
                bad = bad or ["<invalid>"]
            if not well_formed(g.predicate):
                bad = bad or ["<malformed json-logic>"]
            if not bad and tautological(g.predicate, wm):
                bad = ["<var compared with a var read from the same on-screen label>"]
            if bad:
                d.log.info("guardrail %s predicate uses unobservable vars %s → fuzzy", g.id, bad)
                g.predicate = None
            elif g.action in ("block_and_explain", "warn"):
                g.predicate = orient_predicate(g.predicate, wm, r, g.id)
        g.fuzzy = not g.predicate

    # literals / thresholds the expert never said (demo values) → scope probes for the debrief
    grounding_unknowns += literal_probes(wm, r)

    # evidence gaps → open Unknowns (coverage)
    gaps = list(grounding_unknowns)
    for s in wm.steps:
        if not moment_ok(s.moment):
            gaps.append(new_unknown("coverage", f"I missed why or how you did “{s.title}”. Can you explain?",
                                    moment=s.moment, entity=s.id, priority=0.4))
    for g in wm.guardrails:
        if not any(moment_ok(m) for m in g.evidence):
            gaps.append(new_unknown("coverage", f"Is this really your rule: “{g.text}”?", hypothesis=g.text,
                                    entity=g.id, priority=0.7))
    for u in wm.open_unknowns:
        if not u.spoken_question:
            u.spoken_question = (f"Is this right: {u.hypothesis}?" if u.hypothesis else
                                 f"Can you explain {u.entity or 'this step'}?")
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


def _keep_majority(wm2: WorkMap, wm: WorkMap, maj: dict) -> None:
    """The repair retry re-generates the whole map; keep the self-consistency verdicts: the majority guardrails (the
    retry's version when it is the same rule, so its fixed evidence is used) and demoted judgment calls."""
    out = []
    for g in wm.guardrails:
        m = next((x for x in wm2.guardrails if (x.predicate and x.predicate == g.predicate) or x.id == g.id
                  and _jaccard(x.text, g.text) >= GUARD_CLUSTER_JACCARD or _jaccard(x.text, g.text) >= 0.7), None)
        if m is not None:
            m = m.model_copy(deep=True)
            m.id = g.id
            m.quote_ids = list(dict.fromkeys(m.quote_ids + g.quote_ids))
        out.append(m or g)
    wm2.guardrails = out
    gids = {g.id for g in out}
    demoted = set(maj.get("demoted") or [])
    for s_ in wm2.steps:
        s_.guardrail_ids = [x for x in s_.guardrail_ids if x in gids]
        if s_.decision and s_.decision.kind == "judgment" and s_.title.strip().lower() in demoted:
            s_.decision.kind = "routine"


SAME_TASK_SCORE = 0.85  # shared matcher score (fts+emb+onet+app+screen); same task ≈ 1.0+, sibling task ≈ 0.7


async def _same_expert_workflow(wm: WorkMap, expert_id: str, threshold: float = SAME_TASK_SCORE) -> Optional[str]:
    """A capture that covers an existing workflow updates/merges into it instead of creating a near-duplicate (which
    would split learner lookups). Uses THE shared matcher (knowledge.lookup.score_candidates, query = this map).
    Another expert must also have worked on the same screens (≥30 % shared labels, or same apps + record kinds)."""
    from .common import list_maps, map_doc_text
    from .lookup import _map_labels, map_candidate, score_candidates
    maps = [m for m in list_maps() if m.workflow_id != wm.workflow_id]
    if not maps:
        return None
    try:
        ranked = await score_candidates(map_doc_text(wm), None, [map_candidate(m) for m in maps], wm.onet,
                                        query_labels=_map_labels(wm), query_apps=list(wm.apps))
    except Exception:  # noqa: BLE001
        return None
    for sc, c in ranked:
        if sc < threshold:
            break
        m = c.obj
        if any(e.id == expert_id for e in m.experts) or same_screens(wm, m):
            d.log.info("capture matches existing workflow %s score=%.3f", m.workflow_id, sc)
            return m.workflow_id
    return None


def same_screens(a: WorkMap, b: WorkMap, min_share: float = 0.3) -> bool:
    from .common import norm_label
    la = {norm_label(x) for al in a.canonical_vars.values() for x in al if x}
    lb = {norm_label(x) for al in b.canonical_vars.values() for x in al if x}
    if la and len(la & lb) / len(la) >= min_share:
        return True
    apps_a, apps_b = {norm_label(x) for x in a.apps}, {norm_label(x) for x in b.apps}
    ent_a = {norm_label(s_.state_signature.get("entity_type") or "") for s_ in a.steps} - {""}
    ent_b = {norm_label(s_.state_signature.get("entity_type") or "") for s_ in b.steps} - {""}
    return bool(apps_a & apps_b) and bool(ent_a & ent_b)


def _numeric_literals(p: Any) -> Any:
    """{">": [{"var": "line.amount"}, "5000"]} → 5000 (string thresholds break boundary probes / exam cases)."""
    if isinstance(p, dict):
        return {k: ([float(x) if isinstance(x, str) and re.fullmatch(r"-?\d+(\.\d+)?", x) and k in
                     (">", ">=", "<", "<=") else _numeric_literals(x) for x in v] if isinstance(v, list)
                    else _numeric_literals(v)) for k, v in p.items()}
    if isinstance(p, list):
        return [_numeric_literals(x) for x in p]
    return p


def _before_after(r: Replay) -> list[tuple[ScreenState, ScreenState]]:
    """Per record the expert corrected: (state just before the first edit, last state of that record)."""
    out = []
    for ent in dict.fromkeys(e.entity_id for e in r.events if e.entity_id and e.kind in ("edit", "select")):
        first = min(e.t for e in r.events if e.entity_id == ent and e.kind in ("edit", "select"))
        sts = [s_ for s_ in r.states if s_.entity_id == ent]
        before = [s_ for s_ in sts if s_.t < first]
        if before and sts:
            out.append((before[-1], sts[-1]))
    return out


def _clean_literal(v: Any) -> Optional[str]:
    t = re.sub(r"\s*(\.\.\.|…)$", "", str(v or "")).strip()
    if " - " in t:  # ERP-style "Account - COMPANY" suffix (often truncated on screen)
        t = t.rsplit(" - ", 1)[0].strip()
    return t or None


def orient_predicate(pred: dict, wm: WorkMap, r: Replay, gid: str = "") -> Optional[dict]:
    """A blocking guardrail must catch the situation the expert corrected AWAY from. LLMs often write the corrected
    state instead ('amount > 5000 and account in Plants and Machineries'), which would block the right answer and
    never the mistake. Check against the captured before/after screens; repair the string literal from the
    before-value, else drop to fuzzy."""
    from .common import canonical_vars_from_state
    pairs = _before_after(r)
    if not pairs:
        return pred
    verdicts = [(eval_predicate(pred, canonical_vars_from_state(b, wm)), eval_predicate(pred, canonical_vars_from_state(a, wm)),
                 b, a) for b, a in pairs]
    if any(vb is True and va is not True for vb, va, _, _ in verdicts):
        return pred  # fires on the mistake, not on the fix
    inverted = [(b, a) for vb, va, b, a in verdicts if va is True and vb is not True]
    if not inverted:
        return pred
    b, a = inverted[0]
    vb_, va_ = canonical_vars_from_state(b, wm), canonical_vars_from_state(a, wm)
    fixed = json.loads(json.dumps(pred))

    def walk(p: Any) -> None:
        if isinstance(p, dict):
            for op, args in p.items():
                if op == "in" and isinstance(args, list) and len(args) == 2 and isinstance(args[0], str) \
                        and isinstance(args[1], dict) and "var" in args[1]:
                    var = args[1]["var"][0] if isinstance(args[1]["var"], list) else args[1]["var"]
                    lit = _clean_literal(vb_.get(var))
                    if lit and args[0].lower() in str(va_.get(var, "")).lower():
                        args[0] = lit
                else:
                    walk(args)
        elif isinstance(p, list):
            for x in p:
                walk(x)
    walk(fixed)
    if eval_predicate(fixed, vb_) is True and eval_predicate(fixed, va_) is not True:
        d.log.info("guardrail %s predicate described the corrected state; re-oriented → %s", gid, fixed)
        return fixed
    d.log.info("guardrail %s predicate fires on the expert's corrected screen → fuzzy", gid)
    return None


_ID_VAR = re.compile(r"(?:_no|_id|_number|_num|_ref|_reference)$")


def _drop_identifier_literals(pred: Any, gid: str = "") -> Optional[dict]:
    """A record identifier compared with a literal ("-A" in supplier_invoice_no, == "ACC-PINV-…") copies the demo
    case, not the rule (eval: the December re-bill rule became `prior.same_supplier_amount AND '-A' in
    invoice_no`, which can never fire on the next re-bill). Drop such atoms; keep the rest of an AND."""
    def is_id_atom(p: Any) -> bool:
        if not isinstance(p, dict) or len(p) != 1:
            return False
        op, args = next(iter(p.items()))
        if op not in ("in", "==", "===", "!=", "!==") or not isinstance(args, list) or len(args) != 2:
            return False
        vars_ = [a["var"] for a in args if isinstance(a, dict) and "var" in a]
        lits = [a for a in args if not (isinstance(a, dict) and "var" in a)]
        return len(vars_) == 1 and len(lits) == 1 and isinstance(lits[0], str) and \
            bool(_ID_VAR.search(str(vars_[0][0] if isinstance(vars_[0], list) else vars_[0]).lower()))

    if is_id_atom(pred):
        d.log.info("guardrail %s predicate is an identifier literal → fuzzy", gid)
        return None
    if isinstance(pred, dict) and len(pred) == 1 and next(iter(pred)) in ("and", "or"):
        op, args = next(iter(pred.items()))
        keep = [a for a in (args or []) if not is_id_atom(a)]
        if len(keep) != len(args or []):
            d.log.info("guardrail %s: dropped %d identifier-literal atom(s)", gid, len(args) - len(keep))
        if not keep:
            return None
        return keep[0] if len(keep) == 1 else {op: keep}
    return pred


def tautological(pred: Any, wm: WorkMap) -> bool:
    """{"==":[{"var":"line.amount"},{"var":"invoice.paid_december_amount"}]} where both vars alias the same label
    ('Amount (EUR)') is always true on screen — it would fire on every invoice."""
    from .common import norm_label
    if isinstance(pred, list):
        return any(tautological(x, wm) for x in pred)
    if not isinstance(pred, dict) or len(pred) != 1:
        return False
    op, args = next(iter(pred.items()))
    if isinstance(args, list) and len(args) == 2 and all(isinstance(a, dict) and "var" in a for a in args):
        names = [a["var"][0] if isinstance(a["var"], list) else a["var"] for a in args]
        al = [{norm_label(x) for x in wm.canonical_vars.get(n, [])} for n in names]
        if names[0] == names[1] or (al[0] & al[1]):
            return True
    return tautological(args, wm) if isinstance(args, (list, dict)) else False


def _has_roles(observable: set[str]) -> bool:
    """prior.* needs a party var and an amount var to compare records (see common.prior_vars)."""
    from .common import _amount_values, _party_values
    return bool(_party_values({k: "x" for k in observable})) and bool(_amount_values({k: 1.0 for k in observable}))


def observable_vars(wm: WorkMap, r: Replay) -> set[str]:
    """Canonical vars with at least one alias that was a field/column label on a captured screen. Adds observed
    grid-column labels the LLM forgot (alias match on the var's last segment, e.g. line.amount ← 'Amount (EUR)')."""
    from .common import norm_label
    seen: dict[str, str] = {}
    for s_ in r.states:
        for f in s_.fields:
            base = re.sub(r"\s*\[\d+\]$", "", f.label or "")
            seen.setdefault(norm_label(base), base)
    # a "label" that is really a value the vision model mis-read as a label must not back a predicate var (Zammad
    # eval: the ticket title "Charged twice for annual plan" and "4 hours 56 minutes ago" became
    # ticket.charge_amount / ticket.days_since_charge): drop labels equal to a screen's title/view, relative times,
    # and digit-heavy strings
    titles = {norm_label(x) for s_ in r.states for x in (s_.view, s_.entity_id) if x}
    _VALUEISH = re.compile(r"(?i)\b(ago|назад|vor|il y a|hace)\b|\d{2,}.*\d{2,}|^\W*\d")
    seen = {k: v for k, v in seen.items() if k not in titles and not _VALUEISH.search(v)}
    out: set[str] = set()
    for k, aliases in list(wm.canonical_vars.items()):
        ok = [a for a in aliases if norm_label(re.sub(r"\s*\*$", "", a)) in seen or norm_label(a) in seen]
        if not ok:
            tail = norm_label(k.split(".")[-1].replace("_", " "))
            ok = [lab for nl, lab in seen.items() if tail and (nl == tail or nl.startswith(tail + " "))]
            if ok:
                wm.canonical_vars[k] = list(dict.fromkeys(aliases + ok))
        if ok:
            out.add(k)
    return out


def _with_repairs(wm: WorkMap, r: Replay) -> WorkMap:
    for s in wm.steps:
        s.moment = _repair_moment(s.moment, r)
    for g in wm.guardrails:
        g.evidence = [m for m in (_repair_moment(m, r, link_utterance=False) for m in g.evidence) if m]
    return wm


async def publish_expert_map(wm: WorkMap, session_id: Optional[str] = None) -> WorkMap:
    """Store per-expert map; if other experts mapped the workflow, publish the merge (what learners see)."""
    eid = wm.experts[0].id if wm.experts else "expert"
    d.st_call("kv_put", "expert_maps", f"{wm.workflow_id}:{eid}", wm.model_dump(mode="json"))
    maps = [WorkMap.model_validate(x) for x in d.st_call("kv_list", "expert_maps", f"{wm.workflow_id}:", default=[]) or []]
    if len(maps) > 1:
        from .merge import merge_maps
        merged = await merge_maps(maps, newest_expert_id=eid)
        prev = load_map(wm.workflow_id)
        if prev:
            merged.exam = prev.exam
        return await save_map(merged, session_id)
    return await save_map(wm, session_id)


def _mark_session(sess: Any, map_status: str, workflow_id: Optional[str] = None) -> None:
    """The debrief page waits on these: which map this capture became (never 'the latest map')."""
    if sess is None or getattr(sess, "extra", None) is None:
        return
    sess.extra["map_status"] = map_status
    if workflow_id:
        sess.workflow_id = workflow_id
    try:
        from claros.session import sessions  # type: ignore
        if getattr(sess, "id", None) and sessions.get(sess.id) is sess:
            sessions.save(sess)
    except Exception:  # noqa: BLE001
        d.log.debug("session save failed", exc_info=True)


async def on_session_ended(session_id: str, payload: Any) -> None:
    mode = (payload or {}).get("mode") if isinstance(payload, dict) else None
    if mode is None:
        mode = getattr(d.get_session(session_id), "mode", None)
    if mode != "capture":
        return
    sess = d.get_session(session_id)
    r = replay(session_id)
    if not r.expert_utts and len(r.events) < 3:  # nothing was shown or said on the record: no map, say so
        _mark_session(sess, map_status="empty")
        await d.send(session_id, {"type": "status", "level": "info", "text": "Nothing was recorded, so there is no map."})
        return
    _mark_session(sess, map_status="building")
    try:
        wm = await build_map(session_id)
    except Exception:
        _mark_session(sess, map_status="failed")
        raise
    _mark_session(sess, map_status="ready", workflow_id=wm.workflow_id)
    await d.send(session_id, {"type": "status", "level": "info",
                              "text": f"Work map built: {len(wm.steps)} steps, {len(wm.guardrails)} guardrails, "
                                      f"{len(wm.open_unknowns)} open questions."})
    sess = d.get_session(session_id)
    rid = (getattr(sess, "extra", None) or {}).get("request_id")
    if rid:
        from .lookup import set_request_status
        set_request_status(rid, "recorded")

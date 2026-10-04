"""Learn-mode tutor: step tracking, anticipation prompts, guardrail interventions, productive struggle, mastery.

Bus: screen.state / screen.events / ws.in.activity / ws.in.hello (learn sessions only).
Brain-facing: `await handle_intent(session, intent, text) -> str`, `get_intervention(guardrail_id, session_id)`.
Never invents rules: anything not in the map → says so and offers to ask the expert.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from claros.models import Guardrail, MasteryNode, Moment, ScreenEvent, ScreenState, Step, User, WorkMap

from . import _deps as d
from .common import (
    norm_label, canonical_vars_from_state, entity_snapshot, eval_predicate, guardrail_by_id, load_map, map_lang,
    ordered_steps, parse_number, predicate_vars, prior_vars, quote_by_id, rows_as_entities, save_map, seen_summary,
    step_by_id, tr,
)
from .merge import sig_score

CONFIG = {"idle_hint_s": 20.0, "fuzzy_threshold": 0.75, "novel_min_conf": 0.5, "idle_poll_s": 2.0,
          "i18n_wait_s": 20.0, "pick_llm_timeout_s": 5.0, "seen_max": 200}
BOUNDARY = ("save", "submit", "approve")  # a save/submit of a still-violating record → one escalated reminder


@dataclass
class TutorState:
    session_id: str
    workflow_id: Optional[str] = None
    lang: str = "en"
    learner_id: str = "learner"
    learner_name: str = "Learner"
    current: Optional[str] = None
    last_state: Optional[ScreenState] = None
    event_vars: dict[str, Any] = field(default_factory=dict)
    # (guardrail_id, entity) -> {"sig": violating values, "escalated": bool, "t": ms}; re-fires only when the
    # violating values change; a save/submit of the same violating state escalates once ("Before you submit…")
    fired: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)   # session memory: entity/row key -> canonical snapshot
    fuzzy_checked: set = field(default_factory=set)
    predicted: set = field(default_factory=set)
    hint_offered: set = field(default_factory=set)
    touched: dict[str, str] = field(default_factory=dict)   # step_id -> caught|hinted for this visit
    pending: Optional[dict] = None   # {"kind": "prediction"|"intervention", "step_id", "guardrail_id"}
    last_activity: float = field(default_factory=d.now_ms)
    quiet: bool = False
    stopped: bool = False
    novel_reported: set = field(default_factory=set)
    visited: set = field(default_factory=set)
    diverged: bool = False
    hint_rung: dict = field(default_factory=dict)
    idle_task: Optional[asyncio.Task] = None
    wm: Optional[WorkMap] = None


_states: dict[str, TutorState] = {}


# ---------------- state / mastery ----------------

def _sid(session: Any) -> str:
    return session if isinstance(session, str) else getattr(session, "id")


def get_state(session: Any, create: bool = True) -> Optional[TutorState]:
    sid = _sid(session)
    st = _states.get(sid)
    if st is None and create:
        s = d.get_session(session)
        u = getattr(s, "user", None)
        st = TutorState(sid, workflow_id=getattr(s, "workflow_id", None), lang=d.lang_of(session),
                        learner_id=(getattr(u, "id", None) or "learner") if u else "learner",
                        learner_name=(getattr(u, "name", None) or "Learner") if u else "Learner")
        _states[sid] = st
    return st


def start(session_id: str, workflow_id: Optional[str], lang: str = "en", user: Optional[User | dict] = None) -> TutorState:
    st = get_state(session_id)
    st.workflow_id = workflow_id or st.workflow_id
    st.lang = (lang or st.lang)[:2]
    if user:
        u = user if isinstance(user, User) else User.model_validate(user)
        st.learner_id, st.learner_name = u.id, u.name
    st.wm = None
    return st


def _wm(st: TutorState) -> Optional[WorkMap]:
    if st.wm is None and st.workflow_id:
        st.wm = load_map(st.workflow_id)
    return st.wm


def load_mastery(learner_id: str, workflow_id: str) -> dict[str, MasteryNode]:
    raw = d.st_call("kv_get", "mastery", f"{learner_id}:{workflow_id}") or {}
    return {k: MasteryNode.model_validate(v) for k, v in raw.items()}


# ---- BKT (pyBKT-style defaults) per decision step / guardrail ----
BKT = {"p_init": 0.2, "p_transit": 0.15, "p_guess": 0.2, "p_slip": 0.1, "mastered": 0.95,
       "worked_example_below": 0.4, "predict_below": 0.8}


def bkt_update(p: float, correct: bool) -> float:
    g, sl, tr_ = BKT["p_guess"], BKT["p_slip"], BKT["p_transit"]
    post = (p * (1 - sl) / (p * (1 - sl) + (1 - p) * g)) if correct else (p * sl / (p * sl + (1 - p) * (1 - g)))
    return post + (1 - post) * tr_


def load_bkt(learner_id: str, workflow_id: str) -> dict[str, dict]:
    return d.st_call("kv_get", "bkt", f"{learner_id}:{workflow_id}") or {}


def bkt_p(st: TutorState, node: str) -> float:
    if not st.workflow_id:
        return BKT["p_init"]
    return float(load_bkt(st.learner_id, st.workflow_id).get(node, {}).get("p", BKT["p_init"]))


def observe(st: TutorState, node: str, correct: bool) -> float:
    if not st.workflow_id:
        return BKT["p_init"]
    allb = load_bkt(st.learner_id, st.workflow_id)
    n = allb.get(node) or {"p": BKT["p_init"], "n": 0, "entities": []}
    n["p"] = round(bkt_update(float(n["p"]), correct), 4)
    n["n"] = n.get("n", 0) + 1
    ent = st.last_state.entity_id if st.last_state else None
    if correct and ent and ent not in n["entities"]:
        n["entities"].append(ent)
    # mastered = P(L) ≥ 0.95 AND handled correctly on ≥2 distinct cases (one beyond the first seen)
    n["mastered"] = n["p"] >= BKT["mastered"] and len(n["entities"]) >= 2
    allb[node] = n
    d.st_call("kv_put", "bkt", f"{st.learner_id}:{st.workflow_id}", allb)
    return n["p"]


def set_mastery(st: TutorState, step_id: str, level: str, attempt: bool = False,
                observed: Optional[bool] = None) -> MasteryNode:
    if observed is not None:
        observe(st, step_id, observed)
    if not st.workflow_id:
        return MasteryNode(step_id=step_id, level=level)
    m = load_mastery(st.learner_id, st.workflow_id)
    node = m.get(step_id) or MasteryNode(step_id=step_id)
    node.level = level
    if attempt:
        node.attempts += 1
    m[step_id] = node
    d.st_call("kv_put", "mastery", f"{st.learner_id}:{st.workflow_id}", {k: v.model_dump() for k, v in m.items()})
    return node


def _level(st: TutorState, step_id: str) -> str:
    if not st.workflow_id:
        return "unseen"
    n = load_mastery(st.learner_id, st.workflow_id).get(step_id)
    return n.level if n else "unseen"


# ---------------- step matching ----------------

def _state_text(s: Optional[ScreenState]) -> str:
    if s is None:
        return ""
    return " ".join(filter(None, [s.app, s.view, s.entity_type, s.status] +
                           [f"{f.label} {f.value or ''}" for f in s.fields[:20]] + s.dialogs + s.toasts))


def _step_text(wm: WorkMap, s: Step) -> str:
    parts = [s.title]
    if s.decision:
        parts.append(s.decision.description)
    for gid in s.guardrail_ids:
        g = guardrail_by_id(wm, gid)
        if g:
            parts.append(g.text)
            for v in predicate_vars(g.predicate or {}):
                parts += [v] + wm.canonical_vars.get(v, [])
    return " ".join(parts)


async def match_step(wm: WorkMap, state: ScreenState, current: Optional[str] = None,
                     events: Optional[list[ScreenEvent]] = None) -> tuple[Optional[Step], float]:
    sig = {"app": state.app, "view": state.view, "entity_type": state.entity_type}
    steps = ordered_steps(wm)
    if not steps:
        return None, 0.0
    scored = [(sig_score(s.state_signature, sig), s) for s in steps]
    top = max(sc for sc, _ in scored)
    cands = [s for sc, s in scored if sc == top]
    if top < 0.34:
        return None, top
    if len(cands) == 1:
        return cands[0], top
    cur = step_by_id(wm, current) if current else None
    if events:
        # tie-break by similarity of what the learner just did vs. step text (+ canonical-var overlap)
        etxt = " ".join(f"{e.summary} {e.field or ''} {e.canonical or ''}" for e in events)
        vecs = await d.embed([etxt] + [_step_text(wm, s) for s in cands])
        canon = {e.canonical for e in events if e.canonical}
        best, bscore = None, -1.0
        for s, v in zip(cands, vecs[1:]):
            sc = d.cosine(vecs[0], v)
            if canon & set(_step_text(wm, s).split()):
                sc += 0.5
            if cur and s.order < cur.order:
                sc -= 0.1  # prefer forward progress
            if sc > bscore:
                best, bscore = s, sc
        return best, top
    if cur and cur in cands:
        return cur, top
    if cur:
        fwd = [s for s in cands if s.order > cur.order]
        if fwd:
            return fwd[0], top
    return cands[0], top


# ---------------- i18n: everything the tutor SPEAKS is in the learner's language ----------------
# Map text (step titles, decisions, guardrail text, quotes without a stored translation) is translated by GLM once
# per (workflow, map version, lang), persisted in kv, with a string-level cache so a new map version only translates
# new strings. Lookups are sync (cache hit or original text); async paths await the prefetch first.

LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "ru": "Russian"}
_i18n: dict[tuple, dict[str, str]] = {}
_i18n_tasks: dict[tuple, asyncio.Task] = {}


def _i18n_key(wm: WorkMap, lang: str) -> tuple:
    return (wm.workflow_id, int(wm.version or 1), (lang or "en")[:2])


def _h(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def map_strings(wm: WorkMap, lang: str) -> list[str]:
    out: list[str] = [wm.name]
    for s in wm.steps:
        out.append(s.title)
        if s.conflict:
            out.append(s.conflict)
        if s.decision:
            out += [s.decision.description, s.decision.counterfactual or ""]
    out += [g.text for g in wm.guardrails]
    out += [q.text for q in wm.quotes if q.lang != lang and not q.translations.get(lang)]
    return [x for x in dict.fromkeys(x.strip() for x in out if x and x.strip())]


def needs_i18n(wm: WorkMap, lang: str) -> bool:
    lang = (lang or "en")[:2]
    return lang != map_lang(wm) or any(q.lang != lang and not q.translations.get(lang) for q in wm.quotes)


async def _translate(texts: list[str], lang: str) -> dict[str, str]:
    if not texts:
        return {}
    payload = {str(i): t for i, t in enumerate(texts)}
    out = await d.chat([
        {"role": "system", "content":
            f"Translate every value into {LANG_NAMES.get(lang, lang)} for a spoken voice tutor. Keep meaning, tone, "
            "numbers and names; keep on-screen values the learner must find in the app (account names, cost "
            "centers, field labels, record ids, statuses) exactly as written. Return JSON with the SAME keys: "
            '{"<key>": "<translation>"}.'},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], model_role="smart",
        json_schema={"type": "object"})
    res: dict[str, str] = {}
    if isinstance(out, dict):
        for k, t in payload.items():
            v = out.get(k)
            if isinstance(v, str) and v.strip():
                res[t] = v.strip()
    return res


async def build_i18n(wm: WorkMap, lang: str) -> dict[str, str]:
    lang = (lang or "en")[:2]
    key = _i18n_key(wm, lang)
    if key in _i18n:
        return _i18n[key]
    if not needs_i18n(wm, lang):
        _i18n[key] = {}
        return _i18n[key]
    stored = d.st_call("kv_get", "i18n", f"{key[0]}:{key[1]}:{lang}")
    if isinstance(stored, dict) and stored:
        _i18n[key] = stored
        return stored
    texts = map_strings(wm, lang)
    strcache = d.st_call("kv_get", "i18n_str", lang) or {}
    table = {t: strcache[_h(t)] for t in texts if _h(t) in strcache}
    todo = [t for t in texts if t not in table]
    if todo:
        new = await _translate(todo, lang)
        table.update(new)
        if new:
            strcache.update({_h(t): v for t, v in new.items()})
            d.st_call("kv_put", "i18n_str", lang, strcache)
    if todo and not any(t in table for t in todo):
        return table  # LLM unavailable: do not cache the miss (retry next time)
    _i18n[key] = table
    d.st_call("kv_put", "i18n", f"{key[0]}:{key[1]}:{lang}", table)
    return table


def prewarm(wm: Optional[WorkMap], lang: str) -> Optional[asyncio.Task]:
    """Start translating a map for a learner language in the background (hello / lookup / map update)."""
    if wm is None:
        return None
    key = _i18n_key(wm, lang)
    if key in _i18n:
        return None
    t = _i18n_tasks.get(key)
    if t and not t.done():
        return t
    try:
        t = _i18n_tasks[key] = asyncio.get_running_loop().create_task(build_i18n(wm, lang))
    except RuntimeError:
        return None
    return t


async def ensure_i18n(wm: Optional[WorkMap], lang: str, wait_s: Optional[float] = None) -> None:
    if wm is None or _i18n_key(wm, lang) in _i18n:
        return
    t = prewarm(wm, lang)
    if t is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(t), CONFIG["i18n_wait_s"] if wait_s is None else wait_s)
    except Exception:  # noqa: BLE001
        d.log.info("i18n for %s not ready; speaking untranslated text", _i18n_key(wm, lang))


def loc(wm: Optional[WorkMap], lang: str, text: Optional[str]) -> Optional[str]:
    """Map text in the learner's language (cached translation, else the original)."""
    if not text or wm is None:
        return text
    return (_i18n.get(_i18n_key(wm, lang)) or {}).get(text.strip(), text)


# ---------------- outputs ----------------

def _expert_name(wm: WorkMap, eids: list[str], lang: str) -> str:
    names = {e.id: e.name for e in wm.experts}
    for e in eids:
        if e in names:
            return names[e].split()[0]
    return wm.experts[0].name.split()[0] if wm.experts else tr("expert", lang)


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"\w{4,}", (text or "").lower())}


def best_quote(wm: WorkMap, qids: list[str], ref: Optional[str] = None) -> Optional[Any]:
    """The quote that states THIS rule/decision: most word overlap with its text (builders attach stray quotes such
    as "Okay, let's do it." to a guardrail); ties keep the stored order."""
    qs = [q for q in (quote_by_id(wm, i) for i in qids) if q]
    if not qs:
        return None
    if not ref:
        return qs[0]
    rs = _stems(ref)
    ml = map_lang(wm)

    def score(q: Any) -> int:
        return len(rs & (_stems(q.text) | _stems(q.translations.get(ml, ""))))
    best = max(qs, key=score)
    return best if score(best) > 0 else qs[0]


def quote_spoken(wm: WorkMap, q: Any, lang: str) -> str:
    if q.lang == lang:
        return q.text
    return q.translations.get(lang) or loc(wm, lang, q.text) or q.text


def _quote_line(wm: WorkMap, qids: list[str], lang: str, ref: Optional[str] = None) -> Optional[str]:
    q = best_quote(wm, qids, ref)
    if q is None:
        return None
    return tr("said", lang, expert=q.speaker.split()[0], q=quote_spoken(wm, q, lang))


def quote_original(wm: WorkMap, qids: list[str], ref: Optional[str] = None) -> Optional[dict]:
    q = best_quote(wm, qids, ref)
    return {"id": q.id, "speaker": q.speaker, "lang": q.lang, "text": q.text} if q else None


def guardrail_quote_line(wm: WorkMap, g: Guardrail, lang: str) -> Optional[str]:
    """Always the violated guardrail's OWN quote (never the last intervention's / another rule's)."""
    return _quote_line(wm, g.quote_ids, lang, ref=g.text)


def intervention_text(wm: WorkMap, g: Guardrail, lang: str, escalate: bool = False) -> str:
    return tr("intervene_submit" if escalate else "intervene", lang, expert=_expert_name(wm, g.experts, lang))


_interventions: dict[str, str] = {}


def get_intervention(a: Optional[str], b: Optional[str] = None) -> Optional[str]:
    """Accepts (guardrail_id, session_id) or (session_id, guardrail_id)."""
    for gid, sid in ((a, b), (b, a)):
        if gid and (d.get_prewritten(gid, sid) or gid in _interventions):
            return d.get_prewritten(gid, sid) or _interventions.get(gid)
    return None


get_prewritten = d.get_prewritten


async def intervene(st: TutorState, wm: WorkMap, g: Guardrail, why: str = "predicate", escalate: bool = False) -> dict:
    await ensure_i18n(wm, st.lang)
    text = intervention_text(wm, g, st.lang, escalate)
    _interventions[g.id] = text
    d.register_prewritten(g.id, text, st.session_id, kind="intervention")
    moment = g.evidence[0] if g.evidence else Moment(session_id=st.session_id, t=d.now_ms())
    msg = {"type": "intervene", "guardrail_id": g.id, "text": text, "moment": moment.model_dump(mode="json"),
           "action": g.action, "rule": loc(wm, st.lang, g.text), "quote": guardrail_quote_line(wm, g, st.lang),
           "quote_original": quote_original(wm, g.quote_ids, g.text), "lang": st.lang, "trigger": why,
           "escalated": escalate}
    await d.send(st.session_id, msg)
    step = next((s for s in wm.steps if g.id in s.guardrail_ids), None)
    if not escalate:
        if step:
            st.touched[step.id] = "caught"
            set_mastery(st, step.id, "caught", observed=False)
        observe(st, g.id, False)
    st.pending = {"kind": "intervention", "guardrail_id": g.id, "step_id": step.id if step else None}
    return msg


async def _ask(st: TutorState, key: str, text: str) -> None:
    d.register_prewritten(key, text, st.session_id, kind="ask")
    await d.send(st.session_id, {"type": "ask", "unknown_id": key, "text": text})


# ---------------- guardrails ----------------

def _ent(st: TutorState) -> str:
    return (st.last_state.entity_id if st.last_state else None) or "_"


def current_vars(st: TutorState, wm: WorkMap) -> dict[str, Any]:
    v = canonical_vars_from_state(st.last_state, wm) if st.last_state else {}
    v.update(st.event_vars)
    v.update(prior_vars(v, st.seen, st.last_state.entity_id if st.last_state else None))
    return v


def remember(st: TutorState, wm: WorkMap, state: ScreenState) -> None:
    """Session-level 'seen entities' memory: every opened record and every visible table row (list views)."""
    for i, row in enumerate(rows_as_entities(state, wm)):
        key = next((str(v) for v in row.values() if isinstance(v, str) and re.search(r"\d", v) and
                    re.search(r"[A-Za-z]", v) and re.search(r"[-/]", v)), None) or \
            f"row:{_h(json.dumps(row, sort_keys=True, default=str))}"
        if key != state.entity_id:
            st.seen.setdefault(key, {}).update(row)
    if state.entity_id:
        snap = entity_snapshot({k: v for k, v in canonical_vars_from_state(state, wm).items()})
        snap.update({k: v for k, v in st.event_vars.items() if v not in (None, "")})
        if snap:
            prev = st.seen.pop(state.entity_id, None) or {}
            st.seen[state.entity_id] = {**prev, **snap}  # most recently seen last
    while len(st.seen) > CONFIG["seen_max"]:
        st.seen.pop(next(iter(st.seen)))


def _sig_val(v: Any) -> Any:
    """OCR-stable form of a value: 'Tools and Small Equipment - O...' == 'Tools and Small Equipment -'."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    t = re.sub(r"\s*(\.\.\.|…)\s*$", "", str(v)).strip()
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\s+[-–—|/:]\s*[^\s\d]{0,3}$", "", t).strip()
    return norm_label(t)


def violation_sig(g: Guardrail, vars_: dict[str, Any]) -> str:
    """The violating values: re-fire only when these change (another amount / account / supplier...)."""
    names = sorted(predicate_vars(g.predicate or {}))
    if not names:  # fuzzy: the record's own values
        names = sorted(k for k in vars_ if not k.startswith(("doc.", "prior.")))
    return json.dumps([[n, _sig_val(vars_.get(n))] for n in names], default=str, ensure_ascii=False)


async def check_guardrails(st: TutorState, wm: WorkMap, *, fuzzy: bool = True, fire: bool = True,
                           boundary: bool = False) -> list[Guardrail]:
    vars_ = current_vars(st, wm)
    ent = _ent(st)
    hits: list[Guardrail] = []
    for g in wm.guardrails:
        if not g.predicate:
            continue
        r = eval_predicate(g.predicate, vars_)
        if r:
            hits.append(g)
        elif r is False and fire:
            st.fired.pop((g.id, ent), None)  # violation cleared → a later violation is new
    if fuzzy:
        hits += await _check_fuzzy(st, wm, ent, vars_)
    if fire:
        for g in hits:
            key = (g.id, ent)
            sig = violation_sig(g, vars_)
            rec = st.fired.get(key)
            if rec is None or rec["sig"] != sig:
                st.fired[key] = {"sig": sig, "escalated": False, "t": d.now_ms()}
                await intervene(st, wm, g, "fuzzy" if g.fuzzy and not g.predicate else "predicate")
            elif boundary and not rec["escalated"] and g.action != "warn":
                rec["escalated"] = True  # same violating state saved/submitted: one firmer reminder, never a repeat
                await intervene(st, wm, g, "boundary", escalate=True)
    return hits


async def _check_fuzzy(st: TutorState, wm: WorkMap, ent: str, vars_: Optional[dict] = None) -> list[Guardrail]:
    cur = step_by_id(wm, st.current) if st.current else None
    fz = [g for g in wm.guardrails if g.fuzzy and not g.predicate]
    if cur:
        fz = [g for g in fz if g.id in cur.guardrail_ids]
    if not fz or st.last_state is None:
        return []
    stext = _state_text(st.last_state)
    if len(fz) > 2:
        scores = await d.rerank(stext, [g.text for g in fz])
        if scores:
            fz = [g for _, g in sorted(zip(scores, fz), key=lambda x: -x[0])[:2]]
    vars_ = vars_ if vars_ is not None else current_vars(st, wm)
    # cross-entity context the screen offered earlier in this session (list rows, previously opened records)
    seen = seen_summary(st.seen, st.last_state.entity_id)
    prior = {k: v for k, v in vars_.items() if k.startswith("prior.") and k != "prior.match_ids"}
    ctx = stext + (f"\n\nRecords seen earlier in this session:\n{seen}" if seen else
                   "\n\nRecords seen earlier in this session: none") + f"\nSession memory: {json.dumps(prior)}"
    hits = []
    for g in fz:
        key = (g.id, ent, st.last_state.view, json.dumps(prior, sort_keys=True))
        if key in st.fuzzy_checked:
            continue
        st.fuzzy_checked.add(key)
        # strict: every condition of the rule must be visible on THIS screen or in the records seen earlier;
        # "cannot tell" is not a violation (e2e: a December double-billing rule fired on an October equipment invoice)
        label, conf = await d.decide(
            f"Rule: “{g.text}”. Does the screen below (plus the records seen earlier in this session) show evidence "
            f"that EVERY condition of this rule holds for the open record (supplier, dates, amounts, accounts as "
            f"stated; a comparison with another record needs that record among the ones seen earlier)? Answer "
            f"violation only if all conditions are visibly met; cannot_tell if anything is missing; ok if a "
            f"condition is clearly not met.",
            context=ctx, options=["violation", "ok", "cannot_tell"])
        if label and str(label).lower().startswith(("violation", "yes", "true")) and conf >= CONFIG["fuzzy_threshold"]:
            hits.append(g)
    return hits


# ---------------- bus handlers ----------------

def _is_learn(session_id: str) -> bool:
    if session_id in _states:
        return not _states[session_id].stopped
    return getattr(d.get_session(session_id), "mode", None) == "learn"


async def on_hello(session_id: str, payload: Any) -> None:
    if isinstance(payload, dict) and payload.get("mode") == "learn":
        st = start(session_id, payload.get("workflow_id"), payload.get("lang") or "en", payload.get("user"))
        prewarm(_wm(st), st.lang)  # translate the map for this learner while they get going


async def _ensure_workflow(st: TutorState, state: ScreenState) -> None:
    if st.workflow_id:
        return
    from .lookup import lookup
    r = await lookup("", state, st.lang)
    if r.get("match"):
        st.workflow_id = r["match"]["workflow_id"]


async def _enter_step(st: TutorState, wm: WorkMap, step: Step) -> None:
    prev = st.current
    if prev and prev != step.id:
        outcome = st.touched.pop(prev, None)
        set_mastery(st, prev, outcome or "unaided", attempt=True,
                    observed=None if outcome == "caught" else outcome is None)
        if outcome is None:
            pst = step_by_id(wm, prev)
            for gid in (pst.guardrail_ids if pst else []):
                observe(st, gid, True)
    # first-divergence feedback: entered a step whose prerequisites were never visited
    if st.visited and step.after and not (set(step.after) & st.visited) and not st.diverged:
        st.diverged = True
        miss = step_by_id(wm, step.after[0])
        if miss:
            await d.send(st.session_id, {"type": "context_update", "text": "Learner may have skipped: " +
                                         tr('step', st.lang, n=miss.order, t=loc(wm, st.lang, miss.title))})
    st.visited.add(step.id)
    st.current = step.id
    await d.send(st.session_id, {"type": "highlight_step", "step_id": step.id})
    if st.quiet or st.stopped:
        return
    if step.decision and step.decision.kind == "judgment" and step.id not in st.predicted:
        await ensure_i18n(wm, st.lang)
        title = loc(wm, st.lang, step.title)
        p = bkt_p(st, step.id)
        st.predicted.add(step.id)
        if p < BKT["worked_example_below"] and step.moment:
            # worked example: play the expert's moment with their words (translated)
            await d.send(st.session_id, {"type": "show_moment", "moment": step.moment.model_dump(mode="json"),
                                         "step_id": step.id})
            line = _quote_line(wm, step.decision.reason_quote_ids, st.lang, ref=step.decision.description) or \
                loc(wm, st.lang, step.decision.description)
            await _ask(st, f"ex-{step.id}", f"{title}. {line}")
        elif p < BKT["predict_below"]:
            st.pending = {"kind": "prediction", "step_id": step.id}
            await _ask(st, f"pred-{step.id}", f"{title}. {tr('predict', st.lang)}")
        # else: just watch (guardrails only)


async def _novel(st: TutorState, wm: WorkMap, state: ScreenState) -> None:
    if state.confidence < CONFIG["novel_min_conf"] or (wm.apps and state.app and state.app not in wm.apps):
        return
    key = f"{state.app}|{state.view}|{state.entity_type}"
    if key in st.novel_reported or any(u.entity == key for u in wm.open_unknowns):
        return
    st.novel_reported.add(key)
    from .common import new_unknown
    u = new_unknown("coverage", f"A learner reached “{state.view or state.entity_type}” — not in your map. "
                                f"What should they do here?", entity=key, priority=0.6,
                    moment=Moment(session_id=st.session_id, keyframe_ids=[state.keyframe_id] if state.keyframe_id
                                  else [], t=state.t))
    wm.open_unknowns.append(u)
    st.wm = await save_map(wm, st.session_id)


async def on_screen_state(session_id: str, payload: Any) -> None:
    if not _is_learn(session_id):
        return
    st = get_state(session_id)
    state = payload if isinstance(payload, ScreenState) else ScreenState.model_validate(payload)
    if st.last_state and st.last_state.entity_id != state.entity_id:
        st.event_vars.clear()
    st.last_state = state
    await _ensure_workflow(st, state)
    wm = _wm(st)
    if wm is None or st.stopped:
        return
    remember(st, wm, state)
    step, score = await match_step(wm, state, st.current)
    if step is None:
        await _novel(st, wm, state)
    elif step.id != st.current:
        await _enter_step(st, wm, step)
    await check_guardrails(st, wm, fuzzy=False)
    _ensure_idle_loop(st)


async def on_screen_events(session_id: str, payload: Any) -> None:
    if not _is_learn(session_id):
        return
    st = get_state(session_id)
    items = payload.get("items", payload) if isinstance(payload, dict) else payload
    evs = [e if isinstance(e, ScreenEvent) else ScreenEvent.model_validate(e) for e in (items or [])]
    if not evs:
        return
    st.last_activity = d.now_ms()
    wm = _wm(st)
    if wm is None or st.stopped:
        return
    for e in evs:
        if e.canonical and e.new is not None:
            n = parse_number(e.new)
            st.event_vars[e.canonical] = n if n is not None and re.fullmatch(r"[\d\s.,'€$£₽-]+", e.new.strip()) \
                else e.new
    # save/submit of a record that still violates a rule → at most ONE escalated reminder (no identical repeat)
    boundary = any(e.kind in BOUNDARY for e in evs)
    if st.last_state is not None:
        step, _ = await match_step(wm, st.last_state, st.current, evs)
        if step and step.id != st.current:
            await _enter_step(st, wm, step)
    await check_guardrails(st, wm, fuzzy=True, boundary=boundary)


async def on_activity(session_id: str, payload: Any) -> None:
    st = _states.get(session_id)
    if st and isinstance(payload, dict) and payload.get("kind") not in ("idle", "away"):
        st.last_activity = d.now_ms()


async def on_map_updated(session_id: str, payload: Any) -> None:
    wid = (payload or {}).get("workflow_id") if isinstance(payload, dict) else None
    for st in _states.values():
        if st.workflow_id == wid:
            st.wm = None
            if not st.stopped:
                prewarm(_wm(st), st.lang)


# ---------------- productive struggle ----------------

async def check_idle(session_id: str, now: Optional[float] = None) -> bool:
    st = _states.get(session_id)
    if not st or st.quiet or st.stopped or not st.current:
        return False
    wm = _wm(st)
    step = step_by_id(wm, st.current) if wm else None
    if not step or not step.decision or step.decision.kind != "judgment" or step.id in st.hint_offered:
        return False
    if ((now or d.now_ms()) - st.last_activity) / 1000 < CONFIG["idle_hint_s"]:
        return False
    st.hint_offered.add(step.id)
    await _ask(st, f"hint-{step.id}", tr("hint_offer", st.lang))
    return True


def _ensure_idle_loop(st: TutorState) -> None:
    if st.idle_task and not st.idle_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def run() -> None:
        while not st.stopped and st.session_id in _states:
            await asyncio.sleep(CONFIG["idle_poll_s"])
            try:
                await check_idle(st.session_id)
            except Exception:  # noqa: BLE001
                d.log.debug("idle check failed", exc_info=True)

    st.idle_task = loop.create_task(run())


def end(session_id: str) -> None:
    st = _states.pop(session_id, None)
    if st and st.idle_task:
        st.idle_task.cancel()


# ---------------- intents ----------------

def _not_in_map(st: TutorState) -> str:
    st.pending = {"kind": "offer_expert"}
    return tr("not_in_map", st.lang)


def _step_line(wm: WorkMap, s: Step, lang: str) -> str:
    line = tr("step", lang, n=s.order, t=loc(wm, lang, s.title))
    if not s.approved:
        line += " " + tr("unconfirmed", lang)
    if s.conflict:
        line += " " + tr("conflict", lang, c=loc(wm, lang, s.conflict))
    return line


async def pick_guardrail(st: TutorState, wm: WorkMap, text: str = "") -> Optional[Guardrail]:
    """Which rule is the learner asking about? Candidates = rules that intervened on this record (latest first),
    the pending one, the current step's rules. With several candidates the question decides (GLM, fallback word
    overlap) — never just 'the last intervention' (e2e: "why is this capex?" got the double-billing quote)."""
    ent = _ent(st)
    fired = [k[0] for k, _ in sorted(((k, v) for k, v in st.fired.items() if k[1] == ent),
                                     key=lambda kv: -kv[1].get("t", 0))]
    pend = (st.pending or {}).get("guardrail_id")
    cur = step_by_id(wm, st.current) if st.current else None
    ids = list(dict.fromkeys(([pend] if pend else []) + fired + (cur.guardrail_ids if cur else [])))
    cands = [g for g in (guardrail_by_id(wm, i) for i in ids) if g]
    if len(cands) <= 1 or not (text or "").strip():
        return cands[0] if cands else None
    opts = {g.id: f"{loc(wm, st.lang, g.text)} / {g.text}" for g in cands}
    try:
        out = await asyncio.wait_for(d.chat([
            {"role": "system", "content": "A learner asks a question about one of these workplace rules. Which rule "
                                          "is the question about? JSON {\"id\": \"<rule id>\"} or {\"id\": null}."},
            {"role": "user", "content": json.dumps({"question": text, "rules": opts}, ensure_ascii=False)}],
            model_role="fast", json_schema={"type": "object"}), CONFIG["pick_llm_timeout_s"])
    except Exception:  # noqa: BLE001
        out = None
    if isinstance(out, dict) and out.get("id") in opts:
        return guardrail_by_id(wm, out["id"])
    qs = _stems(text)

    def score(g: Guardrail) -> int:
        q = best_quote(wm, g.quote_ids, g.text)
        blob = " ".join([g.text, loc(wm, st.lang, g.text) or ""] +
                        ([q.text, quote_spoken(wm, q, st.lang)] if q else []))
        return len(qs & _stems(blob))
    best = max(cands, key=score)
    return best if score(best) > 0 else cands[0]


async def _judge(learner: str, expected: str) -> bool:
    out = await d.chat([{"role": "system", "content": "Does the learner's answer match the expected decision in "
                                                      "substance (any language)? JSON {\"match\": true|false}"},
                        {"role": "user", "content": f"Expected: {expected}\nLearner: {learner}"}],
                       model_role="fast", json_schema={"type": "object"})
    if isinstance(out, dict) and "match" in out:
        return bool(out["match"])
    a, b = await d.embed([learner, expected])
    words = set(re.findall(r"\w{3,}", learner.lower())) & set(re.findall(r"\w{3,}", expected.lower()))
    return d.cosine(a, b) >= 0.5 or len(words) >= 2


async def handle_intent(session: Any, intent: str, text: str = "") -> str:
    st = get_state(session)
    lang = st.lang = d.lang_of(session) if not isinstance(session, str) or session not in _states else st.lang
    st.last_activity = d.now_ms()
    wm = _wm(st)
    await ensure_i18n(wm, lang)
    if wm is None:
        if intent == "ask_expert":
            return await _ask_expert(st, text)
        return _not_in_map(st)
    steps = ordered_steps(wm)
    cur = step_by_id(wm, st.current) if st.current else None

    if intent == "walk_through":
        st.quiet = False
        head = tr("overview", lang, n=len(steps))
        target = cur or (steps[0] if steps else None)
        if target is None:
            return _not_in_map(st)
        await d.send(st.session_id, {"type": "highlight_step", "step_id": target.id})
        return f"{head} {_step_line(wm, target, lang)}"
    if intent == "what_next":
        if not steps:
            return _not_in_map(st)
        nxt = steps[0] if cur is None else next((s for s in steps if s.order > cur.order), None)
        if nxt is None:
            return tr("done", lang)
        await d.send(st.session_id, {"type": "highlight_step", "step_id": nxt.id})
        return _step_line(wm, nxt, lang)
    if intent == "why_this":
        g = await pick_guardrail(st, wm, text)
        if g:
            st.pending = {"kind": "intervention", "guardrail_id": g.id,
                          "step_id": next((s.id for s in wm.steps if g.id in s.guardrail_ids), None)}
            line = guardrail_quote_line(wm, g, lang)
            return line or loc(wm, lang, g.text) or _not_in_map(st)
        if cur and cur.conflict:
            return tr("conflict", lang, c=loc(wm, lang, cur.conflict))
        if cur and cur.decision:
            line = _quote_line(wm, cur.decision.reason_quote_ids, lang, ref=cur.decision.description)
            if line:
                return line
        return _not_in_map(st)
    if intent == "check_my_work":
        hits = await check_guardrails(st, wm, fuzzy=False, fire=False)  # explicit predicates only
        if hits:
            g = hits[0]
            st.fired[(g.id, _ent(st))] = {"sig": violation_sig(g, current_vars(st, wm)), "escalated": False,
                                          "t": d.now_ms()}
            await intervene(st, wm, g, "check_my_work")
            return intervention_text(wm, g, lang)
        return tr("all_clear", lang)
    if intent == "hint":
        if not cur:
            return _not_in_map(st)
        st.touched[cur.id] = st.touched.get(cur.id) or "hinted"
        node = set_mastery(st, cur.id, "hinted")
        return hint_ladder(st, wm, cur, node.attempts)
    if intent == "just_watch":
        st.quiet = True
        return tr("ok_watch", lang)
    if intent == "stop":
        st.stopped = True
        if st.idle_task:
            st.idle_task.cancel()
        return tr("ok_stop", lang)
    if intent == "ask_expert":
        return await _ask_expert(st, text)
    if intent == "answer_prediction":
        p = st.pending or {}
        st.pending = None
        if p.get("kind") == "offer_expert":
            return await _ask_expert(st, text)
        if p.get("kind") == "intervention":
            g = guardrail_by_id(wm, p.get("guardrail_id") or "")
            if g:
                ok = await _judge(text, g.text)
                quote = guardrail_quote_line(wm, g, lang) or loc(wm, lang, g.text)
                return f"{tr('right' if ok else 'not_quite', lang)} {quote}"
        step = step_by_id(wm, p.get("step_id") or "") or cur
        if not step or not step.decision:
            return _not_in_map(st)
        expected = f"{step.decision.description} ({step.decision.to_value or ''})"
        ok = await _judge(text, expected)
        if ok and st.touched.get(step.id) is None:
            set_mastery(st, step.id, "unaided", observed=True)
        elif not ok:
            st.touched[step.id] = "caught"
            set_mastery(st, step.id, "caught", observed=False)
        quote = _quote_line(wm, step.decision.reason_quote_ids, lang, ref=step.decision.description) or \
            loc(wm, lang, step.decision.description)
        if step.conflict:
            quote = tr("conflict", lang, c=loc(wm, lang, step.conflict))
        return f"{tr('right' if ok else 'not_quite', lang)} {quote}"
    return _not_in_map(st)


def hint_ladder(st: TutorState, wm: WorkMap, step: Step, attempts: int = 0) -> str:
    """Rungs 0–5: predict → pointer → what is decided → condition/guardrail → expert quote → bottom-out.
    Max rung capped by mastery (BKT) and attempts (non-LLM policy)."""
    p = bkt_p(st, step.id)
    cap = 5 if (p < BKT["worked_example_below"] or attempts >= 2) else 3 if p < BKT["predict_below"] else 2
    rung = min(st.hint_rung.get(step.id, 0) + 1, cap)
    st.hint_rung[step.id] = rung
    dec = step.decision
    g = next((guardrail_by_id(wm, gid) for gid in step.guardrail_ids if guardrail_by_id(wm, gid)), None)
    lg = st.lang
    ladder = [
        tr("predict", lg),
        tr("step", lg, n=step.order, t=loc(wm, lg, step.title)),
        loc(wm, lg, dec.description if dec else step.title),
        loc(wm, lg, (g.text if g else None) or (dec.counterfactual if dec else None)),
        _quote_line(wm, (dec.reason_quote_ids if dec else []) or (g.quote_ids if g else []), lg,
                    ref=(dec.description if dec and dec.reason_quote_ids else g.text if g else None)),
        f"→ {dec.to_value}" if dec and dec.to_value else None,
    ]
    for r in range(rung, -1, -1):  # highest available rung ≤ cap
        if ladder[r]:
            return ladder[r]
    return _not_in_map(st)


async def _ask_expert(st: TutorState, text: str) -> str:
    from .lookup import create_request
    wm = _wm(st)
    hint = text or (wm.name if wm else None) or (st.last_state.view if st.last_state else None) or "workflow"
    moment = Moment(session_id=st.session_id,
                    keyframe_ids=[st.last_state.keyframe_id] if st.last_state and st.last_state.keyframe_id else [],
                    t=d.now_ms())
    await create_request(hint, User(id=st.learner_id, name=st.learner_name, role="learner"), moment,
                         onet=wm.onet if wm else None, workflow_id=st.workflow_id)
    if wm is not None and text:
        from .common import new_unknown
        wm.open_unknowns.append(new_unknown("why", f"A learner asked: “{text}”", moment=moment,
                                            entity=st.current, priority=0.6))
        st.wm = await save_map(wm, st.session_id)
    return tr("asked", st.lang)

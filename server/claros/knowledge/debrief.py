"""Debrief (mode=debrief): planner + state machine.

phases: questions → teach_back (→ correction loop) → exam → done
- questions: ordered remaining unknowns (guardrails → exceptions → unseen cases), only company/personal scope,
  budget ≤ max_questions / max_seconds; stop when ledger empty AND coverage checks pass (or budget spent).
- teach_back: ≤140 words in session lang with [[step:<id>]] markers (→ client tool highlight_step).
- correction: patches only the affected steps/guardrails, reads back a diff.
- exam: 3 variant cases from decision boundaries; Claros predicts + confidence; expert grades → ExamCase.

Brain-facing API: `await next_debrief_utterance(session) -> str`,
`await handle_debrief_answer(session, text, intent=None) -> str`, `split_markers(text)`.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from claros.models import (
    Decision, ExamCase, ExtractedRule, Guardrail, Moment, Quote, Step, Unknown, WorkMap,
)

from . import _deps as d
from .common import (
    PRIOR_VARS,
    OPEN_STATUSES, checks_pass, eval_predicate, guardrail_by_id, load_map, ordered_steps, predicate_vars, save_map,
    step_by_id,
)

CONFIG = {"max_questions": 6, "max_seconds": 300, "teachback_words": 140, "exam_cases": 3}

TYPE_RANK = {"limit": 0, "never": 0, "stop_and_ask": 0, "why": 1, "deliberate": 1, "conflict": 1, "coverage": 2}
EXPERT_SCOPES = ("company", "personal_judgment")
MARKER = re.compile(r"\[\[step:([\w\-]+)\]\]")

YES = re.compile(r"^\s*(yes|yeah|yep|correct|right|exactly|that'?s right|sure|ok(ay)?|ja|genau|richtig|stimmt|"
                  r"oui|exact(ement)?|c'est (ça|juste)|sí|si|correcto|exacto|así es|да|верно|правильно|точно|ага)\b",
                  re.I)
NO = re.compile(r"^\s*(no|nope|wrong|nein|falsch|non|faux|incorrecto|нет|неверно|неправильно)\b", re.I)
SKIP = re.compile(r"\b(not now|later|skip|pass|nicht jetzt|später|plus tard|pas maintenant|ahora no|luego|"
                  r"не сейчас|потом|пропусти)\b", re.I)

L = {
    "thanks": {"en": "Got it.", "de": "Verstanden.", "fr": "Compris.", "es": "Entendido.", "ru": "Понял."},
    "teach_intro": {"en": "Let me say it back.", "de": "Ich fasse zusammen.", "fr": "Je résume.",
                    "es": "Te lo resumo.", "ru": "Давай я перескажу."},
    "teach_end": {"en": "Did I get that right?", "de": "Habe ich das richtig verstanden?", "fr": "C'est bien ça ?",
                  "es": "¿Lo he entendido bien?", "ru": "Я правильно понял?"},
    "first": {"en": "First", "de": "Zuerst", "fr": "D'abord", "es": "Primero", "ru": "Сначала"},
    "then": {"en": "Then", "de": "Dann", "fr": "Ensuite", "es": "Luego", "ru": "Затем"},
    "watch": {"en": "Watch out:", "de": "Achtung:", "fr": "Attention :", "es": "Ojo:", "ru": "Внимание:"},
    "changed": {"en": "Changed", "de": "Geändert", "fr": "Modifié", "es": "Cambiado", "ru": "Изменено"},
    "right_now": {"en": "Is it right now?", "de": "Stimmt es jetzt?", "fr": "C'est juste maintenant ?",
                  "es": "¿Ahora está bien?", "ru": "Теперь верно?"},
    "nochange": {"en": "I couldn't tell which step to change. Which step do you mean?",
                 "de": "Ich weiß nicht, welchen Schritt ich ändern soll. Welchen meinst du?",
                 "fr": "Je ne sais pas quelle étape modifier. Laquelle ?",
                 "es": "No sé qué paso cambiar. ¿Cuál?", "ru": "Не понял, какой шаг менять. Какой именно?"},
    "exam_intro": {"en": "Thanks. Three quick test cases, tell me if I'd get them right.",
                   "de": "Danke. Drei kurze Testfälle – sag mir, ob ich richtig liege.",
                   "fr": "Merci. Trois petits cas test : dis-moi si j'ai raison.",
                   "es": "Gracias. Tres casos de prueba rápidos: dime si acierto.",
                   "ru": "Спасибо. Три коротких проверочных случая — скажи, прав ли я."},
    "case": {"en": "Case {n}: {v}. I would: {p} ({c}% sure). Right?",
             "de": "Fall {n}: {v}. Ich würde: {p} ({c}% sicher). Richtig?",
             "fr": "Cas {n} : {v}. Je ferais : {p} (sûr à {c} %). Juste ?",
             "es": "Caso {n}: {v}. Yo haría: {p} ({c}% seguro). ¿Correcto?",
             "ru": "Случай {n}: {v}. Я бы: {p} (уверен на {c}%). Верно?"},
    "noted": {"en": "Noted, thanks for the correction.", "de": "Notiert, danke für die Korrektur.",
              "fr": "Noté, merci pour la correction.", "es": "Anotado, gracias por la corrección.",
              "ru": "Записал, спасибо за поправку."},
    "done": {"en": "That's everything. The map is published for learners.",
             "de": "Das war alles. Die Karte ist für Lernende veröffentlicht.",
             "fr": "C'est tout. La carte est publiée pour les apprenants.",
             "es": "Eso es todo. El mapa está publicado para los aprendices.",
             "ru": "Это всё. Карта опубликована для учеников."},
    "proceed": {"en": "proceed normally, no guardrail applies", "de": "normal weitermachen, keine Regel greift",
                "fr": "continuer normalement, aucune règle ne s'applique",
                "es": "seguir normalmente, no aplica ninguna regla", "ru": "продолжить как обычно, правила не срабатывают"},
    "confirm_q": {"en": "Looks like: {h} — is that your rule?", "de": "Sieht so aus: {h} – ist das deine Regel?",
                  "fr": "On dirait : {h} — c'est ta règle ?", "es": "Parece que: {h} — ¿es tu regla?",
                  "ru": "Похоже: {h} — это твоё правило?"},
    "no_map": {"en": "I don't have a map for this debrief yet.", "de": "Für dieses Debriefing habe ich noch keine Karte.",
               "fr": "Je n'ai pas encore de carte pour ce débriefing.", "es": "Aún no tengo mapa para este repaso.",
               "ru": "Для этого разбора у меня ещё нет карты."},
}


def t(key: str, lang: str, **kw: Any) -> str:
    s = L[key].get(lang) or L[key]["en"]
    return s.format(**kw) if kw else s


@dataclass
class DebriefState:
    session_id: str
    workflow_id: str
    phase: str = "questions"  # questions | teach_back | exam | done
    asked: int = 0
    started_ms: float = field(default_factory=d.now_ms)
    current: Optional[str] = None       # unknown id being asked
    script: Optional[str] = None
    exam: list[ExamCase] = field(default_factory=list)
    exam_idx: int = 0
    seq: int = 0
    probed: bool = False


_states: dict[str, DebriefState] = {}


def _sid(session: Any) -> str:
    return session if isinstance(session, str) else getattr(session, "id")


def get_state(session: Any) -> Optional[DebriefState]:
    sid = _sid(session)
    st = _states.get(sid)
    if st is None:
        wid = getattr(d.get_session(session), "workflow_id", None)
        if not wid:
            return None
        st = _states[sid] = DebriefState(sid, wid)
    return st


def start(session_id: str, workflow_id: str) -> DebriefState:
    st = _states[session_id] = DebriefState(session_id, workflow_id)
    return st


# ---------------- planner ----------------

def plan(wm: WorkMap, expert_id: Optional[str] = None, expert_name: Optional[str] = None) -> list[Unknown]:
    out = []
    for u in wm.open_unknowns:
        if u.status not in ("open", "asked"):
            continue
        if u.scope not in EXPERT_SCOPES:
            continue
        if u.type == "conflict" and expert_name and u.hypothesis and not u.hypothesis.startswith(expert_name):
            continue  # the other expert's side of a conflict
        out.append(u)
    return sorted(out, key=lambda u: (TYPE_RANK.get(u.type, 3), -u.priority, u.created_t))


def gap_unknowns(wm: WorkMap) -> list[Unknown]:
    """Coverage checks that fail → targeted questions (judgment without why, guardrail without quote)."""
    have = {u.entity for u in wm.open_unknowns if u.status in OPEN_STATUSES}
    out = []
    for s in ordered_steps(wm):
        if s.id in have:
            continue
        if s.decision and s.decision.kind == "judgment" and not s.decision.reason_quote_ids:
            out.append(Unknown(id=d.new_id("u"), type="why", entity=s.id, priority=0.6, created_t=d.now_ms(),
                               spoken_question=f"At “{s.title}”: why {s.decision.description[:80]}?",
                               moment=s.moment))
        elif not (s.moment and s.moment.keyframe_ids and s.moment.utterance_ids):
            out.append(Unknown(id=d.new_id("u"), type="coverage", entity=s.id, priority=0.4,
                               created_t=d.now_ms(), spoken_question=f"What do you do at “{s.title}”, and why?",
                               moment=s.moment))
    for g in wm.guardrails:
        if g.id not in have and not g.quote_ids:
            out.append(Unknown(id=d.new_id("u"), type="limit", entity=g.id, priority=0.8, created_t=d.now_ms(),
                               hypothesis=g.text, hypothesis_confidence=0.7, spoken_question=g.text))
    return out


PROBE = {"en": "What if it were {v}? Would “{g}” still apply?",
         "de": "Und wenn es {v} wäre? Gilt „{g}“ dann noch?",
         "fr": "Et si c'était {v} ? « {g} » s'appliquerait-il encore ?",
         "es": "¿Y si fuera {v}? ¿Seguiría aplicando «{g}»?",
         "ru": "А если бы было {v}? Правило «{g}» всё ещё действует?"}


def boundary_probes(wm: WorkMap, lang: str = "en", max_n: int = 2) -> list[Unknown]:
    """Critical-Decision-Method edge cases: vary a threshold slot just past the boundary ('what if 4,999?')."""
    have = {u.entity for u in wm.open_unknowns if u.type == "limit"}
    out = []
    for g in wm.guardrails:
        if len(out) >= max_n or g.approved or g.id in have or not g.predicate:
            continue
        for a in _atoms(g.predicate):
            op, args = next(iter(a.items()))
            if op in (">", ">=", "<", "<=") and isinstance(args, list) and len(args) == 2 \
                    and isinstance(args[1], (int, float)):
                b = args[1]
                v = b - 1 if op == ">" else b + 1 if op == "<" else b
                v = int(v) if float(v).is_integer() else v
                out.append(Unknown(id=d.new_id("u"), type="limit", entity=g.id, priority=0.2, scope="company",
                                   created_t=d.now_ms(), hypothesis=g.text,
                                   spoken_question=(PROBE.get(lang) or PROBE["en"]).format(v=f"{v:,}", g=g.text)))
                break
    return out


def _question(u: Unknown, lang: str, wm: Optional[WorkMap] = None) -> str:
    if u.hypothesis and u.hypothesis_confidence >= 0.6 and u.type != "conflict":
        return t("confirm_q", lang, h=u.hypothesis)
    if u.spoken_question:
        return u.spoken_question
    if u.hypothesis:
        return t("confirm_q", lang, h=u.hypothesis)
    st = step_by_id(wm, u.entity or "") if wm else None  # never a bare "Why?" with no context
    return f"Why do you do “{st.title if st else (u.entity or 'this step')}” that way?"


def budget_spent(st: DebriefState, now: Optional[float] = None) -> bool:
    return st.asked >= CONFIG["max_questions"] or ((now or d.now_ms()) - st.started_ms) / 1000 > CONFIG["max_seconds"]


def should_stop(wm: WorkMap, st: DebriefState, expert_name: Optional[str] = None) -> bool:
    ledger_empty = not plan(wm, expert_name=expert_name)
    return (ledger_empty and checks_pass(wm)) or budget_spent(st) or ledger_empty


async def _ledger(session_id: str, wm: WorkMap, expert_name: Optional[str]) -> None:
    q = plan(wm, expert_name=expert_name)
    await d.send(session_id, {"type": "ledger", "open": len(q),
                              "saved_for_later": len([u for u in wm.open_unknowns if u.status == "deferred"]),
                              "top": q[0].model_dump(mode="json") if q else None})


# ---------------- teach-back ----------------

def split_markers(script: str) -> list[tuple[Optional[str], str]]:
    """'[[step:s1]] First…' → [(s1, 'First…'), …] so the brain can emit highlight_step tool calls."""
    parts: list[tuple[Optional[str], str]] = []
    pos, cur = 0, None
    for m in MARKER.finditer(script):
        txt = script[pos:m.start()].strip()
        if txt:
            parts.append((cur, txt))
        cur, pos = m.group(1), m.end()
    tail = script[pos:].strip()
    if tail:
        parts.append((cur, tail))
    return parts


def strip_markers(script: str) -> str:
    return re.sub(r"\s+", " ", MARKER.sub("", script)).strip()


def _word_count(s: str) -> int:
    return len(strip_markers(s).split())


def _truncate(script: str, limit: int) -> str:
    out, n = [], 0
    for tok in script.split():
        if MARKER.fullmatch(tok):
            out.append(tok)
            continue
        if n >= limit:
            break
        out.append(tok)
        n += 1
    return " ".join(out)


def _template_teachback(wm: WorkMap, lang: str) -> str:
    """Whole sentences only, within the word budget (steps first, then guardrails)."""
    end = t("teach_end", lang)
    budget = CONFIG["teachback_words"] - len(end.split())
    out = [t("teach_intro", lang)]
    for i, s in enumerate(ordered_steps(wm)):
        lead = t("first", lang) if i == 0 else t("then", lang)
        txt = s.title
        if s.decision and s.decision.kind == "judgment":
            txt = s.decision.description
        sent = f"[[step:{s.id}]] {lead}, {txt[:1].lower() + txt[1:]}."
        if _word_count(" ".join(out + [sent])) <= budget:
            out.append(sent)
    watch_added = False
    for g in wm.guardrails:
        sent = ("" if watch_added else t("watch", lang) + " ") + g.text.rstrip(".") + "."
        if _word_count(" ".join(out + [sent])) <= budget:
            out.append(sent)
            watch_added = True
    return " ".join(out) + " " + end


async def teach_back(wm: WorkMap, lang: str) -> str:
    steps = [{"id": s.id, "title": s.title, "decision": s.decision.description if s.decision else None,
              "conflict": s.conflict} for s in ordered_steps(wm)]
    out = await d.chat([
        {"role": "system", "content": f"Write a spoken teach-back of this workflow for the expert to confirm, in "
                                      f"language '{lang}', at most {CONFIG['teachback_words']} words. Prefix the "
                                      "sentence about each step with its marker [[step:<id>]]. Mention every "
                                      "judgment call and guardrail briefly. End by asking if it's right. Plain "
                                      "text only."},
        {"role": "user", "content": json.dumps({"steps": steps, "guardrails": [g.text for g in wm.guardrails]},
                                               ensure_ascii=False)}], model_role="fast")
    if isinstance(out, str) and MARKER.search(out):
        known = {s.id for s in wm.steps}
        out = MARKER.sub(lambda m: m.group(0) if m.group(1) in known else "", out)
        if _word_count(out) > CONFIG["teachback_words"]:
            # cut at the last full sentence inside the budget (never mid-sentence) and still ask for confirmation
            end = t("teach_end", lang)
            cut = _truncate(out, CONFIG["teachback_words"] - len(end.split()))
            m = list(re.finditer(r"[.!?…](?=\s|$)", cut))
            out = (cut[:m[-1].end()] if m else cut) + " " + end
        return out.strip()
    return _template_teachback(wm, lang)


# ---------------- corrections ----------------

PATCH_SCHEMA = {
    "type": "object",
    "properties": {"patches": {"type": "array", "items": {"type": "object", "properties": {
        "target": {"enum": ["step", "guardrail"]}, "id": {"type": "string"},
        "field": {"enum": ["title", "decision.description", "decision.to_value", "decision.counterfactual",
                           "text", "predicate", "action", "owner"]},
        "new": {}}, "required": ["target", "id", "field", "new"]}}},
    "required": ["patches"]}

_STEP_NUM = re.compile(r"(?:step|schritt|étape|etape|paso|шаг)\s*(\d+)", re.I)


def _get(obj: Any, path: str) -> Any:
    for p in path.split("."):
        obj = getattr(obj, p, None) if obj is not None else None
    return obj


def _set(obj: Any, path: str, val: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        nxt = getattr(obj, p, None)
        if nxt is None and p == "decision":
            nxt = Decision(kind="judgment", description="")
            setattr(obj, p, nxt)
        obj = nxt
    setattr(obj, parts[-1], val)


def _fallback_target(wm: WorkMap, text: str) -> Optional[Step]:
    m = _STEP_NUM.search(text)
    steps = ordered_steps(wm)
    if m and 1 <= int(m.group(1)) <= len(steps):
        return steps[int(m.group(1)) - 1]
    words = {w for w in re.findall(r"\w{4,}", text.lower())}
    best = max(steps, key=lambda s: len(words & set(re.findall(r"\w{4,}", (s.title + " " + (
        s.decision.description if s.decision else "")).lower()))), default=None)
    if best and words & set(re.findall(r"\w{4,}", (best.title + " " + (
            best.decision.description if best.decision else "")).lower())):
        return best
    return None


async def apply_correction(wm: WorkMap, text: str, lang: str, quote_id: Optional[str] = None
                           ) -> tuple[WorkMap, list[dict]]:
    """Patch only the affected steps/guardrails. Returns (map, diffs[{target,id,field,old,new}])."""
    ctx = {"steps": [{"id": s.id, "order": s.order, "title": s.title,
                      "decision": s.decision.model_dump() if s.decision else None} for s in ordered_steps(wm)],
           "guardrails": [{"id": g.id, "text": g.text, "predicate": g.predicate, "action": g.action}
                          for g in wm.guardrails]}
    out = await d.chat([
        {"role": "system", "content": "The expert corrected a teach-back. Return minimal patches touching ONLY the "
                                      "steps/guardrails the correction is about. Keep the map's language for "
                                      "titles; fields allowed per schema. JSON only."},
        {"role": "user", "content": f"MAP: {json.dumps(ctx, ensure_ascii=False)}\nCORRECTION ({lang}): {text}"}],
        model_role="smart", json_schema=PATCH_SCHEMA)
    patches = out.get("patches") if isinstance(out, dict) else None
    if not patches:
        tgt = _fallback_target(wm, text)
        if tgt is None:
            return wm, []
        field_ = "decision.description" if tgt.decision else "title"
        patches = [{"target": "step", "id": tgt.id, "field": field_, "new": text.strip()}]
    diffs = []
    for p in patches:
        obj = step_by_id(wm, p.get("id", "")) if p.get("target") == "step" else guardrail_by_id(wm, p.get("id", ""))
        if obj is None:
            continue
        f = p.get("field") or ""
        old = copy.deepcopy(_get(obj, f))
        new = p.get("new")
        if f == "predicate" and isinstance(new, str):
            new = d.parse_json(new)
        if old == new:
            continue
        try:
            _set(obj, f, new)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, Guardrail) and f == "predicate":
            obj.fuzzy = new is None
        if quote_id:
            if isinstance(obj, Step) and obj.decision is not None:
                obj.decision.reason_quote_ids = list(dict.fromkeys(obj.decision.reason_quote_ids + [quote_id]))
            elif isinstance(obj, Guardrail):
                obj.quote_ids = list(dict.fromkeys(obj.quote_ids + [quote_id]))
        obj.approved = False
        diffs.append({"target": p.get("target"), "id": obj.id, "field": f, "old": old, "new": new,
                      "label": getattr(obj, "title", None) or getattr(obj, "text", "")})
    return wm, diffs


def diff_readback(wm: WorkMap, diffs: list[dict], lang: str) -> str:
    if not diffs:
        return t("nochange", lang)
    bits = []
    for df in diffs:
        tag = ""
        if df["target"] == "step":
            s = step_by_id(wm, df["id"])
            tag = f"[[step:{df['id']}]] " if s else ""
        old = df["old"] if not isinstance(df["old"], (dict, list)) else json.dumps(df["old"])
        new = df["new"] if not isinstance(df["new"], (dict, list)) else json.dumps(df["new"])
        bits.append(f"{tag}{t('changed', lang)}: “{old or '—'}” → “{new}”.")
    return " ".join(bits) + " " + t("right_now", lang)


# ---------------- exam ----------------

def _atoms(p: Any) -> list[dict]:
    if isinstance(p, dict) and len(p) == 1:
        op, args = next(iter(p.items()))
        if op == "and":
            return [a for x in args for a in _atoms(x)]
        if op == "or":
            return _atoms(args[0]) if args else []
        return [p]
    return []


def _var(x: Any) -> Optional[str]:
    return x.get("var") if isinstance(x, dict) and "var" in x else None


def _assign_atom(atom: dict, satisfy: bool) -> dict[str, Any]:
    op, args = next(iter(atom.items()))
    if op == "!":
        return _assign_atom(args if isinstance(args, dict) else args[0], not satisfy)
    if not isinstance(args, list) or len(args) < 2:
        return {}
    a, b = args[0], args[1]
    if op in (">", ">=", "<", "<=") and _var(a) and isinstance(b, (int, float)):
        up = op in (">", ">=")
        hi, lo = round(b * 1.04 + (1 if b == 0 else 0), 2), round(b * 0.96, 2)
        return {_var(a): (hi if up else lo) if satisfy else (lo if up else hi)}
    if op in ("==", "===") and _var(a):
        if satisfy:
            return {_var(a): b}
        return {_var(a): (b - 1 if isinstance(b, (int, float)) and b > 1 else b + 1)
                if isinstance(b, (int, float)) else f"not {b}"}
    if op in ("!=", "!==") and _var(a):
        return _assign_atom({"==": args}, not satisfy)
    if op == "in" and _var(a) and isinstance(b, list) and b:
        return {_var(a): b[0] if satisfy else "Other"}
    if op == "in" and isinstance(a, str) and _var(b):
        return {_var(b): f"{a} account" if satisfy else "Other"}
    return {}


def _boundary_cases(g: Guardrail) -> list[tuple[dict[str, Any], bool]]:
    atoms = _atoms(g.predicate)
    if not atoms:
        return []
    sat: dict[str, Any] = {}
    for a in atoms:
        sat.update(_assign_atom(a, True))
    miss = dict(sat)
    numeric = next((a for a in atoms if next(iter(a)) in (">", ">=", "<", "<=")), atoms[0])
    miss.update(_assign_atom(numeric, False))
    return [(sat, True), (miss, False)]


def _describe(vars_: dict[str, Any], wm: WorkMap) -> str:
    bits = []
    for k, v in vars_.items():
        label = (wm.canonical_vars.get(k) or [k.split(".")[-1].replace("_", " ")])[0]
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        bits.append(f"{label} = {v:,}" if isinstance(v, (int, float)) else f"{label} = {v}")
    return ", ".join(bits)


def predict(wm: WorkMap, vars_: dict[str, Any], lang: str = "en") -> tuple[str, float, list[str]]:
    hits, unknown = [], 0
    for g in wm.guardrails:
        if not g.predicate:
            continue
        r = eval_predicate(g.predicate, vars_)
        if r is None:
            unknown += 1
        elif r:
            hits.append(g)
    if hits:
        return "; ".join(f"{g.action.replace('_', ' ')} — {g.text}" for g in hits), 0.85, [g.id for g in hits]
    return t("proceed", lang), 0.7 if unknown else 0.8, []


def make_exam(wm: WorkMap, lang: str = "en", n: Optional[int] = None) -> list[ExamCase]:
    n = n or CONFIG["exam_cases"]
    per_g = [_boundary_cases(g) for g in wm.guardrails if g.predicate]
    cand: list[dict[str, Any]] = []
    i = 0
    while len(cand) < n and any(per_g):
        lst = per_g[i % len(per_g)]
        if lst:
            cand.append(lst.pop(0)[0])
        i += 1
        if i > 50:
            break
    cases = []
    for vars_ in cand[:n]:
        p, c, _ = predict(wm, vars_, lang)
        cases.append(ExamCase(variant=_describe(vars_, wm), predicted=p, confidence=c))
    for g in [g for g in wm.guardrails if g.fuzzy]:
        if len(cases) >= n:
            break
        cases.append(ExamCase(variant=f"a case where: {g.text.split(':')[0]}",
                              predicted=f"{g.action.replace('_', ' ')} — {g.text}", confidence=0.6))
    return cases


# ---------------- state machine ----------------

async def _load(st: DebriefState) -> Optional[WorkMap]:
    return load_map(st.workflow_id)


def _expert(session: Any) -> tuple[str, str]:
    u = d.user_of(session)
    if u is None:
        return "expert", "Expert"
    return (getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else "expert"),
            getattr(u, "name", None) or (u.get("name") if isinstance(u, dict) else "Expert"))


async def next_debrief_utterance(session: Any) -> str:
    st = get_state(session)
    lang = d.lang_of(session)
    if st is None:
        return t("no_map", lang)
    wm = await _load(st)
    if wm is None:
        return t("no_map", lang)
    _, ename = _expert(session)
    if st.phase == "questions":
        if not st.probed:
            st.probed = True
            probes = boundary_probes(wm, lang)
            if probes:
                wm.open_unknowns += probes
                await save_map(wm, st.session_id)
        q = plan(wm, expert_name=ename)
        if not q and not checks_pass(wm) and not budget_spent(st):
            gaps = gap_unknowns(wm)
            if gaps:
                wm.open_unknowns += gaps
                await save_map(wm, st.session_id)
                q = plan(wm, expert_name=ename)
        if not should_stop(wm, st, ename) and q:
            u = q[0]
            u.status = "asked"
            st.current = u.id
            st.asked += 1
            await _ledger(st.session_id, wm, ename)
            return _question(u, lang, wm)
        st.phase = "teach_back"
    if st.phase == "teach_back":
        st.script = await teach_back(wm, lang)
        return st.script
    if st.phase == "exam":
        if not st.exam:
            st.exam = make_exam(wm, lang)
        if st.exam_idx < len(st.exam):
            c = st.exam[st.exam_idx]
            return t("case", lang, n=st.exam_idx + 1, v=c.variant, p=c.predicted, c=int(c.confidence * 100))
        st.phase = "done"
    return t("done", lang)


def _quote(session: Any, text: str, lang: str) -> Quote:
    eid, ename = _expert(session)
    st = get_state(session)
    st.seq += 1
    return Quote(id=f"q_{_sid(session)}_{st.seq}", speaker=ename, speaker_id=eid, lang=lang, text=text,
                 t=d.now_ms(), session_id=_sid(session), source="debrief")


async def _extract_rule(u: Unknown, text: str, wm: WorkMap) -> Optional[dict]:
    out = await d.chat([
        {"role": "system", "content": "Extract the expert's rule from the answer. JSON: {condition, threshold, "
                                      "action, escalate_to, guardrail_text (null if no hard rule), predicate "
                                      "(json-logic over these canonical vars or null): " + ", ".join(wm.canonical_vars)},
        {"role": "user", "content": f"Question: {u.spoken_question}\nAnswer: {text}"}], model_role="fast",
        json_schema={"type": "object"})
    return out if isinstance(out, dict) else None


async def handle_debrief_answer(session: Any, text: str, intent: Optional[str] = None) -> str:
    st = get_state(session)
    lang = d.lang_of(session)
    if st is None:
        return t("no_map", lang)
    wm = await _load(st)
    if wm is None:
        return t("no_map", lang)
    eid, ename = _expert(session)
    sid = _sid(session)

    if st.phase == "questions":
        u = next((x for x in wm.open_unknowns if x.id == st.current), None)
        st.current = None
        if u is not None:
            if intent == "not_now" or SKIP.search(text or ""):
                u.status = "deferred"
            else:
                q = _quote(session, text, lang)
                wm.quotes.append(q)
                u.status, u.resolution, u.resolution_source = "answered", text, "expert"
                u.answer_utterance_ids.append(q.id)
                ev = Moment(session_id=sid, keyframe_ids=list(u.moment.keyframe_ids) if u.moment else [],
                            t=q.t, utterance_ids=[q.id])
                _link_answer(wm, u, q, ev)
                rule = await _extract_rule(u, text, wm)
                if rule:
                    u.extracted_rule = ExtractedRule(**{k: (str(rule[k]) if rule.get(k) is not None else None)
                                                        for k in ("condition", "threshold", "action", "escalate_to")})
                    pred = rule.get("predicate") if isinstance(rule.get("predicate"), dict) else None
                    if pred and not all(v in wm.canonical_vars or v.startswith("doc.") or v in PRIOR_VARS
                                            for v in predicate_vars(pred)):
                        rule["predicate"] = None  # unobservable vars would never evaluate on a learner screen
                    dup = _similar_guardrail(wm, rule.get("guardrail_text") or "", text) \
                        if rule.get("guardrail_text") else None
                    if dup is not None:  # same rule restated (e.g. "the controller, Frank, approves UK invoices")
                        dup.quote_ids = list(dict.fromkeys(dup.quote_ids + [q.id]))
                        if rule.get("escalate_to") and not dup.owner:
                            dup.owner = rule["escalate_to"]
                    elif rule.get("guardrail_text") and not guardrail_by_id(wm, u.entity or ""):
                        g = Guardrail(id=d.new_id("g"), text=rule["guardrail_text"], quote_ids=[q.id],
                                      predicate=rule.get("predicate") if isinstance(rule.get("predicate"), dict)
                                      else None, evidence=[ev], experts=[eid],
                                      action="stop_and_ask" if u.type == "stop_and_ask" else "block_and_explain",
                                      owner=rule.get("escalate_to"))
                        g.fuzzy = g.predicate is None
                        wm.guardrails.append(g)
                        step = step_by_id(wm, u.entity or "")
                        if step:
                            step.guardrail_ids.append(g.id)
            await save_map(wm, sid)
        nxt = await next_debrief_utterance(session)
        return f"{t('thanks', lang)} {nxt}"

    if st.phase == "teach_back":
        if intent == "confirm" or (intent is None and YES.search(text or "") and not NO.search(text or "")):
            for s in wm.steps:
                s.approved = True
            for g in wm.guardrails:
                g.approved = True
            wm.approved_by = list(dict.fromkeys(wm.approved_by + [eid]))
            await save_map(wm, sid)
            st.phase = "exam"
            return f"{t('exam_intro', lang)} {await next_debrief_utterance(session)}"
        q = _quote(session, text, lang)
        wm.quotes.append(q)
        wm, diffs = await apply_correction(wm, text, lang, q.id)
        await save_map(wm, sid)
        for df in diffs:
            if df["target"] == "step":
                await d.send(sid, {"type": "highlight_step", "step_id": df["id"]})
        return diff_readback(wm, diffs, lang)

    if st.phase == "exam" and st.exam_idx < len(st.exam):
        c = st.exam[st.exam_idx]
        ok = intent == "confirm" or (intent is None and YES.search(text or "") and not NO.search(text or ""))
        c.expert_verdict = "correct" if ok else "wrong"
        if not ok:
            c.correction = NO.sub("", text or "").strip(" ,.") or None
        wm.exam.append(c)
        st.exam_idx += 1
        await save_map(wm, sid)
        prefix = t("thanks", lang) if ok else t("noted", lang)
        return f"{prefix} {await next_debrief_utterance(session)}"

    st.phase = "done"
    return t("done", lang)


def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2}


def _similar_guardrail(wm: WorkMap, text: str, answer: str = "") -> Optional[Guardrail]:
    """Existing guardrail this debrief rule restates (its text or its quotes overlap the new rule/answer)."""
    a = _toks(text) | _toks(answer)
    if not a:
        return None
    qtext = {q.id: q.text for q in wm.quotes}
    best, sc = None, 0.0
    for g in wm.guardrails:
        b = _toks(g.text) | {w for qid in g.quote_ids for w in _toks(qtext.get(qid, ""))}
        j = len(a & b) / max(1, min(len(a), len(b)))
        if j > sc:
            best, sc = g, j
    return best if sc >= 0.34 else None


def _link_answer(wm: WorkMap, u: Unknown, q: Quote, ev: Moment) -> None:
    step = step_by_id(wm, u.entity or "")
    g = guardrail_by_id(wm, u.entity or "")
    if g is not None:
        g.quote_ids = list(dict.fromkeys(g.quote_ids + [q.id]))
        if ev.keyframe_ids:
            g.evidence.append(ev)
        return
    if step is None and u.about_event_ids:
        step = next((s for s in wm.steps if s.moment and set(u.about_event_ids) & set(s.moment.keyframe_ids)), None)
    if step is None:
        return
    if step.decision is None:
        step.decision = Decision(kind="judgment", description=u.hypothesis or step.title)
    step.decision.reason_quote_ids = list(dict.fromkeys(step.decision.reason_quote_ids + [q.id]))
    if step.moment is None or not step.moment.utterance_ids:
        kf = (step.moment.keyframe_ids if step.moment else []) or ev.keyframe_ids
        step.moment = Moment(session_id=(step.moment.session_id if step.moment else ev.session_id),
                             keyframe_ids=kf, t=step.moment.t if step.moment else ev.t, utterance_ids=[q.id])


async def on_hello(session_id: str, payload: Any) -> None:
    if isinstance(payload, dict) and payload.get("mode") == "debrief" and payload.get("workflow_id"):
        if session_id not in _states:
            start(session_id, payload["workflow_id"])

"""Debrief (mode=debrief): planner + state machine.

phases: questions → teach_back (→ correction loop) → done   (the old self-graded "exam" is gone: it proved nothing)
- questions (≥3, ≤6, ≤5 min): things NOT answered during the task, in this order:
    1. disagreements with earlier experts addressed to THIS expert ("Anna holds …; you … — why?")
    2. exceptions the live ledger noticed but never asked
    3. rules Claros is unsure about (scope/threshold probes on literals, fuzzy rules, judgments without a reason)
    4. unseen cases (what distinguishes the case, named party vs every party, who decides/releases)
  learner-originated items (novel cases, learner questions) are capped at 1 and asked only after the core block.
  Wording: one batched LLM call at start (brief style, action first), deterministic templates as fallback.
  Answers to probes patch the guardrail they are about (text + predicate + owner) and attach the quote.
- teach_back: ≤140 spoken words (≤60 s) in session lang with [[step:<id>]] markers (→ highlight_step).
- correction: patches only the affected steps/guardrails, reads back ONLY the diff in natural speech.
- explicit confirm → approve + publish; still-open core items → deferred, needs_second_run (coverage partial).

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
    Decision, ExamCase, ExtractedRule, Guardrail, Moment, Quote, Step, Unknown, Variant, WorkMap,
)

from . import _deps as d
from .common import (
    PRIOR_VARS,
    OPEN_STATUSES, checks_pass, eval_predicate, guardrail_by_id, load_map, ordered_steps, predicate_vars, save_map,
    step_by_id,
)

CONFIG = {"min_questions": 3, "max_questions": 6, "max_seconds": 300, "teachback_words": 140, "exam_cases": 3,
          "learner_cap": 1, "question_words": 25}

TYPE_RANK = {"limit": 0, "never": 0, "stop_and_ask": 0, "why": 1, "deliberate": 1, "conflict": 1, "coverage": 2}
EXPERT_SCOPES = ("company", "personal_judgment")
MARKER = re.compile(r"\[\[step:([\w\-]+)\]\]")
PROBE_KINDS = ("party", "kind", "threshold", "owner", "fuzzy", "scope")

YES = re.compile(r"^\s*(yes|yeah|yep|correct|right|exactly|that'?s right|that'?s how it works|sure|ok(ay)?|ja|genau|richtig|stimmt|"
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
    "got_it": {"en": "Got it —", "de": "Verstanden —", "fr": "Compris —", "es": "Entendido —", "ru": "Понял —"},
    "step_now": {"en": "step {n} now: {v}.", "de": "Schritt {n} jetzt: {v}.", "fr": "étape {n} maintenant : {v}.",
                 "es": "paso {n} ahora: {v}.", "ru": "шаг {n} теперь: {v}."},
    "rule_now": {"en": "the rule now says: {v}.", "de": "die Regel lautet jetzt: {v}.",
                 "fr": "la règle dit maintenant : {v}.", "es": "la regla ahora dice: {v}.",
                 "ru": "правило теперь: {v}."},
    "owner_now": {"en": "the person to ask is now {v}.", "de": "fragen muss man jetzt {v}.",
                  "fr": "il faut maintenant demander à {v}.", "es": "ahora hay que preguntar a {v}.",
                  "ru": "теперь спрашивать нужно {v}."},
    "when_now": {"en": "I updated when that rule applies.", "de": "Ich habe angepasst, wann die Regel greift.",
                 "fr": "J'ai mis à jour quand la règle s'applique.", "es": "Actualicé cuándo aplica la regla.",
                 "ru": "Я обновил, когда действует правило."},
    "anything_else": {"en": "Anything else, or is that how it works?", "de": "Noch etwas, oder ist das so richtig?",
                      "fr": "Autre chose, ou c'est bien comme ça ?", "es": "¿Algo más, o es así como funciona?",
                      "ru": "Что-то ещё, или так всё и работает?"},
    "done_partial": {"en": "Thanks — the map is published. A few points still need a second run.",
                     "de": "Danke — die Karte ist veröffentlicht. Ein paar Punkte brauchen noch einen zweiten Durchgang.",
                     "fr": "Merci — la carte est publiée. Quelques points demandent un second passage.",
                     "es": "Gracias — el mapa está publicado. Algunos puntos necesitan una segunda ronda.",
                     "ru": "Спасибо — карта опубликована. Пара моментов требует второго прохода."},
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
    phase: str = "questions"  # questions | teach_back | done   ("exam" is no longer entered)
    asked: int = 0
    core_asked: int = 0
    learner_asked: int = 0
    prepared: bool = False
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

LEARNER_PREFIX = ("A learner", "Ein Lernender", "Un apprenant", "Un aprendiz", "Ученик")


def is_learner_item(u: Unknown) -> bool:
    return (u.meta or {}).get("origin") == "learner" or (u.entity or "").startswith("novel:") or \
        (u.spoken_question or "").startswith(LEARNER_PREFIX)


def _for_this_expert(u: Unknown, expert_id: Optional[str], expert_name: Optional[str]) -> bool:
    """Conflicts are asked of the expert they are addressed to (merge meta), legacy: 'Name: value' hypothesis."""
    if u.type != "conflict":
        return True
    m = u.meta or {}
    if m.get("ask_expert_id") or m.get("ask_expert_name"):
        return (bool(expert_id) and m.get("ask_expert_id") == expert_id) or \
            (bool(expert_name) and m.get("ask_expert_name") == expert_name) or (not expert_id and not expert_name)
    if expert_name and u.hypothesis:
        return u.hypothesis.startswith(expert_name)
    return True


def tier(u: Unknown) -> int:
    """0 conflict for me · 1 exception the ledger noticed · 2 rule I'm unsure about (a literal that may be a demo
    value, a fuzzy rule, a missing reason) · 3 unseen case (what distinguishes it, who decides) · 9 learner."""
    m = u.meta or {}
    if is_learner_item(u):
        return 9
    if u.type == "conflict":
        return 0
    if m.get("origin") in ("builder", "debrief"):
        if m.get("probe") in ("kind", "owner") and m.get("origin") == "debrief":
            return 3
        return 2
    if u.type == "coverage" and u.hypothesis:  # builder: "is this really a step / your rule?"
        return 2
    return 1


def plan(wm: WorkMap, expert_id: Optional[str] = None, expert_name: Optional[str] = None,
         include_secondary: bool = True) -> list[Unknown]:
    """Askable items, core first (by tier, then type, priority), learner-originated last."""
    out = []
    for u in wm.open_unknowns:
        if u.status not in ("open", "asked", "deferred") or (u.meta or {}).get("needs_second_run"):
            continue
        if u.status == "deferred" and (u.meta or {}).get("skipped"):
            continue  # the expert said "not now" in THIS debrief
        if u.scope not in EXPERT_SCOPES:
            continue
        if not _for_this_expert(u, expert_id, expert_name):
            continue  # the other expert's side of a conflict
        if not include_secondary and (u.meta or {}).get("secondary"):
            continue
        out.append(u)
    return sorted(out, key=lambda u: (tier(u), TYPE_RANK.get(u.type, 3), -u.priority, u.created_t))


# ---------------- probes (rules I'm unsure about, cases I have not seen) ----------------

_PARTY_TAILS = ("supplier", "vendor", "company", "customer", "party", "payee", "client", "counterparty", "subsidiary",
                "requester", "employee", "contractor", "merchant", "partner", "account_holder")
_PARTY_WORD = {"supplier": "supplier", "vendor": "supplier", "company": "company", "subsidiary": "company",
               "customer": "customer", "client": "customer", "payee": "payee", "party": "party",
               "counterparty": "counterparty", "requester": "requester", "employee": "employee",
               "contractor": "contractor", "merchant": "merchant", "partner": "partner"}


def _tail(var: str) -> str:
    return (var or "").split(".")[-1].lower()


def _is_party_var(var: str) -> bool:
    t = _tail(var)
    return any(p in t for p in _PARTY_TAILS) and not t.endswith(("_no", "_id", "_number", "_date", "_type", "_group"))


def _var_name(a: Any) -> Optional[str]:
    if isinstance(a, dict) and "var" in a:
        v = a["var"]
        return v[0] if isinstance(v, list) else v
    return None


def literal_atoms(pred: Any) -> list[tuple[str, str, dict]]:
    """[(var, literal, atom)] for string-literal comparisons ("in"/"==") anywhere in a predicate."""
    out: list[tuple[str, str, dict]] = []

    def walk(p: Any) -> None:
        if isinstance(p, dict) and len(p) == 1:
            op, args = next(iter(p.items()))
            if op in ("in", "==", "===") and isinstance(args, list) and len(args) == 2:
                a, b = args
                va, vb = _var_name(a), _var_name(b)
                if va and isinstance(b, str):
                    out.append((va, b, p))
                elif vb and isinstance(a, str):
                    out.append((vb, a, p))
                elif va and isinstance(b, list) and b and all(isinstance(x, str) for x in b):
                    out.append((va, b[0], p))
                return
            walk(args)
        elif isinstance(p, list):
            for x in p:
                walk(x)
    walk(pred)
    return out


def threshold_atoms(pred: Any) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []

    def walk(p: Any) -> None:
        if isinstance(p, dict) and len(p) == 1:
            op, args = next(iter(p.items()))
            if op in (">", ">=", "<", "<=") and isinstance(args, list) and len(args) == 2:
                v = _var_name(args[0])
                if v and isinstance(args[1], (int, float)) and not isinstance(args[1], bool):
                    out.append((v, float(args[1])))
                return
            walk(args)
        elif isinstance(p, list):
            for x in p:
                walk(x)
    walk(pred)
    return out


def _fmt(x: float) -> str:
    return f"{int(x):,}" if float(x).is_integer() else f"{x:,.2f}"


_OVER = re.compile(r"^(.*?)\s+(?:over|above|more than|greater than|exceeding|über|mehr als|plus de|au-dessus de|"
                   r"más de|por encima de|свыше|больше|выше|более)\s", re.I)


def _thing(g: Guardrail) -> Optional[str]:
    """'Equipment over 5,000 is capex' → 'equipment' (the category the expert tied the limit to)."""
    m = _OVER.search(g.text or "")
    if not m:
        return None
    words = [w for w in re.findall(r"[^\W\d_][\w'-]*", m.group(1))][-3:]
    words = [w for w in words if w.lower() not in ("any", "all", "every", "the", "a", "an", "jede", "alle", "alles")]
    return " ".join(words).lower() or None


def _step_of(wm: WorkMap, g: Guardrail) -> Optional[Step]:
    return next((s for s in ordered_steps(wm) if g.id in s.guardrail_ids), None)


def _short(v: Any, n: int = 40) -> str:
    s = re.sub(r"\s*(\.\.\.|…)$", "", str(v or "")).strip()
    if " - " in s:  # ERP-style "Account - COMPANY" suffix
        s = s.rsplit(" - ", 1)[0]
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0]


def _action_seen(wm: WorkMap, g: Guardrail) -> Optional[str]:
    """What the expert visibly did on the step this rule guards, in plain words (template fallback)."""
    if g.action == "hold":
        return "You held that one."
    st = _step_of(wm, g)
    if st and st.decision and st.decision.to_value and st.decision.to_value.lower() not in ("continue", "yes", "no"):
        v = _short(st.decision.to_value, 32)
        if re.search(r"hold|on hold|angehalten|задерж", v, re.I):
            return "You held that one."
        return f"You moved that one to {v}."
    return None


def _clamp(q: str, n: Optional[int] = None) -> str:
    n = n or CONFIG["question_words"]
    w = q.split()
    return q if len(w) <= n else " ".join(w[:n]).rstrip(",;:—-") + "?"


def template_question(kind: str, wm: WorkMap, g: Optional[Guardrail], meta: dict) -> Optional[str]:
    """Deterministic, action-first fallback wording (English; the LLM call localizes)."""
    if g is None:
        return None
    act = _action_seen(wm, g)
    lead = (act + " ") if act else ""
    rule = _short(g.text, 70).rstrip(".")
    if kind in ("party", "scope"):
        lit = _short(meta.get("literal") or "", 36)
        who = _PARTY_WORD.get(next((p for p in _PARTY_TAILS if p in _tail(meta.get("var") or "")), ""), "case")
        owner = "" if g.owner or g.action not in ("hold", "stop_and_ask") else ", and who decides when to release it"
        if lit:
            return _clamp(f"{lead}Is that only for {lit}, or for every {who}{owner}?")
        return _clamp(f"{lead}Is that for every {who}{owner}?")
    if kind in ("kind", "threshold"):
        th = meta.get("threshold")
        thing = meta.get("thing") or _thing(g)
        if th is not None and thing:
            return _clamp(f"{lead}Does that apply to anything over {_fmt(th)}, or only {thing}?")
        if th is not None:
            return _clamp(f"{lead}Is {_fmt(th)} the line for every kind of case, or only some?")
        return _clamp(f"{lead}Does “{rule}” apply to every kind of case, or only some?")
    if kind == "owner":
        if g.action == "hold":
            return _clamp(f"{lead}Who decides when to release it, and what do they need to see?")
        return _clamp(f"{lead}Who exactly do you ask, and what do you need from them?")
    if kind == "fuzzy":
        head = re.split(r"\s*[:—–]\s*|\s+-\s+", rule, maxsplit=1)[0].strip()
        if head and head != rule and len(head.split()) <= 8:
            return _clamp(f"How do you spot “{head[:1].lower() + head[1:]}” on the screen?")
        return _clamp(f"What on the screen tells you that “{_cap_words(rule, 12)}” applies?")
    return None


def make_probes(wm: WorkMap) -> list[Unknown]:
    """Rules Claros is unsure about + cases it has not seen, one primary probe per guardrail (secondary ones only
    get asked if the debrief would otherwise end with fewer than min_questions)."""
    have: dict[str, set[str]] = {}
    for u in wm.open_unknowns:
        k = (u.meta or {}).get("probe")
        if k and u.entity:
            have.setdefault(u.entity, set()).add("party" if k == "scope" else "kind" if k == "threshold" else k)
        elif u.entity and u.status in OPEN_STATUSES and u.type in ("limit", "stop_and_ask", "never"):
            have.setdefault(u.entity, set()).add("any")
    out: list[Unknown] = []
    for g in wm.guardrails:
        kinds: list[tuple[str, dict]] = []
        lits = [(v, lit) for v, lit, _ in literal_atoms(g.predicate) if _is_party_var(v)] if g.predicate else []
        ths = threshold_atoms(g.predicate) if g.predicate else []
        if lits:
            kinds.append(("party", {"var": lits[0][0], "literal": lits[0][1]}))
        if ths:
            kinds.append(("kind", {"var": ths[0][0], "threshold": ths[0][1], "thing": _thing(g)}))
        if g.action in ("hold", "stop_and_ask") and not g.owner:
            kinds.append(("owner", {}))
        if not g.predicate:
            kinds.append(("fuzzy", {}))
        taken = have.get(g.id, set())
        kinds = [(k, m) for k, m in kinds if k not in taken]
        for i, (k, m) in enumerate(kinds[:2]):
            if "any" in taken and i == 0:
                continue  # an open live question already covers this rule
            utype = {"owner": "stop_and_ask", "fuzzy": "why"}.get(k, "limit")
            u = Unknown(id=d.new_id("u"), type=utype, scope="company", entity=g.id, hypothesis=g.text,
                        priority=0.5 - 0.1 * i, created_t=d.now_ms(),
                        moment=g.evidence[0] if g.evidence else None,
                        meta={"origin": "debrief", "probe": k, "secondary": i > 0, **m})
            out.append(u)
    # rules first in map order, secondary probes after every primary one
    return sorted(out, key=lambda u: (bool(u.meta.get("secondary")),))


WORDING_SYSTEM = """You write the follow-up questions an apprentice asks an expert in a short spoken debrief after
watching them work. Each item gives the rule the expert taught (their own words in "quote"), what the expert visibly
did ("seen"), and what the apprentice is unsure about ("kind"):
- party: a rule tied to one named party/company — ask whether it is only that one or every one like it
- kind / threshold: a limit — ask what distinguishes the case (does it apply to other categories over the limit too?)
- owner: a stop/hold rule — ask who decides / releases it
- fuzzy: a rule with no visible test — ask what on screen tells them the case applies
- gap / why / other: ask for the missing reason
Style: spoken, plain words, at most 25 words, action first when "seen" is given, then the question; one or two
sentences; no lists, no ids, no field names in capitals, no JSON, never "Case 1". Write in language "{lang}".
Examples (different domain):
- seen "approved the hotel claim above the cap", kind party, literal "Berlin office" → "You approved that hotel claim
  above the cap. Is that only for the Berlin office, or for every office?"
- kind threshold 200, thing "meals" → "Does the 200 limit apply to travel costs too, or only meals?"
- owner, seen "parked the claim" → "You parked that claim. Who decides when it can be paid, and what do they check?"
Return JSON {{"questions": {{"<id>": "<question>"}}}}."""


def _quotes_of(wm: WorkMap, qids: list[str]) -> list[str]:
    qs = {q.id: q.text for q in wm.quotes}
    return [qs[i] for i in qids if i in qs][:2]


def _ok_question(q: Any) -> bool:
    if not isinstance(q, str) or not q.strip():
        return False
    t = q.strip()
    return len(t.split()) <= CONFIG["question_words"] + 3 and not re.search(r"[{}\[\]]|\bcase\s*\d", t, re.I) \
        and "?" in t


async def word_questions(wm: WorkMap, items: list[Unknown], lang: str) -> None:
    """ONE batched LLM call for every probe/gap question that has no spoken wording yet; templates as fallback."""
    todo = [u for u in items if not u.spoken_question or (u.meta or {}).get("origin") in ("builder", "debrief")
            and not (u.meta or {}).get("worded")]
    if not todo:
        return
    payload = []
    for u in todo:
        m = u.meta or {}
        g = guardrail_by_id(wm, u.entity or "")
        st = step_by_id(wm, u.entity or "") or (_step_of(wm, g) if g else None)
        dec = st.decision if st and st.decision else None
        payload.append({"id": u.id, "kind": m.get("probe") or m.get("gap") or u.type,
                        "rule": g.text if g else (dec.description if dec else u.hypothesis),
                        "quote": _quotes_of(wm, (g.quote_ids if g else []) + (dec.reason_quote_ids if dec else [])),
                        "seen": (f"{dec.from_value or ''} → {dec.to_value}" if dec and dec.to_value else
                                 ("put it on hold" if g and g.action == "hold" else None)),
                        "step": st.title if st else None, "literal": m.get("literal"), "threshold": m.get("threshold"),
                        "thing": m.get("thing"), "draft": u.spoken_question})
    out = await d.chat([{"role": "system", "content": WORDING_SYSTEM.format(lang=lang)},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                       model_role="fast", json_schema={"type": "object"})
    got = (out or {}).get("questions") if isinstance(out, dict) else None
    got = got if isinstance(got, dict) else {}
    for u in todo:
        m = u.meta if u.meta is not None else {}
        q = got.get(u.id)
        if _ok_question(q):
            u.spoken_question = _clamp(q.strip(), CONFIG["question_words"] + 3)
        elif not u.spoken_question:
            g = guardrail_by_id(wm, u.entity or "")
            kind = m.get("probe") or ""
            u.spoken_question = template_question(kind, wm, g, m) if g else None
            if not u.spoken_question:
                st = step_by_id(wm, u.entity or "")
                if st and st.decision and st.decision.to_value:
                    u.spoken_question = _clamp(f"You moved that one to {_short(st.decision.to_value, 32)}. "
                                               f"What made you do that?")
                elif st:
                    u.spoken_question = _clamp(f"At “{st.title}”: what do you check there, and why?")
                else:
                    u.spoken_question = u.hypothesis and _clamp(f"Is this right: {u.hypothesis}?")
        m["worded"] = True
        u.meta = m


async def prepare(wm: WorkMap, st: "DebriefState", lang: str) -> None:
    """Once per debrief: add probes + gap questions to the map, word every unworded question in one LLM call."""
    st.prepared = True
    added = make_probes(wm)
    have = {u.entity for u in wm.open_unknowns if u.status in OPEN_STATUSES} | {u.entity for u in added}
    gaps = [u for u in gap_unknowns(wm) if u.entity not in have and u.type == "why"]
    for u in gaps:
        u.spoken_question = None  # worded action-first below ("You moved that one to … What made you do that?")
        u.meta = {"origin": "debrief", "gap": "reason"}
    added += gaps
    for u in added:
        u.meta = {**(u.meta or {}), "origin": (u.meta or {}).get("origin") or "debrief"}
    wm.open_unknowns += added
    try:
        await word_questions(wm, [u for u in wm.open_unknowns if u.status in OPEN_STATUSES], lang)
    except Exception:  # noqa: BLE001
        d.log.warning("debrief wording failed", exc_info=True)
    await save_map(wm, st.session_id)


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
    if u.spoken_question:
        return u.spoken_question
    if u.hypothesis:
        return t("confirm_q", lang, h=u.hypothesis)
    st = step_by_id(wm, u.entity or "") if wm else None  # never a bare "Why?" with no context
    return f"Why do you do “{st.title if st else (u.entity or 'this step')}” that way?"


def budget_spent(st: DebriefState, now: Optional[float] = None) -> bool:
    return st.asked >= CONFIG["max_questions"] or ((now or d.now_ms()) - st.started_ms) / 1000 > CONFIG["max_seconds"]


def pick_next(wm: WorkMap, st: DebriefState, expert_id: Optional[str], expert_name: Optional[str]
              ) -> Optional[Unknown]:
    """Core items first (secondary probes only while fewer than min_questions were asked); then ≤learner_cap
    learner-originated items; None → teach-back."""
    if budget_spent(st):
        return None
    q = plan(wm, expert_id, expert_name, include_secondary=st.core_asked < CONFIG["min_questions"])
    core = [u for u in q if not is_learner_item(u)]
    if core:
        return core[0]
    learner = [u for u in q if is_learner_item(u)]
    if learner and st.learner_asked < CONFIG["learner_cap"]:
        return learner[0]
    return None


def should_stop(wm: WorkMap, st: DebriefState, expert_name: Optional[str] = None,
                expert_id: Optional[str] = None) -> bool:
    return pick_next(wm, st, expert_id, expert_name) is None


async def _ledger(session_id: str, wm: WorkMap, expert_name: Optional[str], expert_id: Optional[str] = None) -> None:
    q = [u for u in plan(wm, expert_id, expert_name, include_secondary=False) if not is_learner_item(u)]
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


def _first_name(wm: WorkMap, ids: list[str]) -> Optional[str]:
    qs = {q.id: q.speaker for q in wm.quotes}
    names = [qs[i] for i in ids if i in qs]
    return names[0].split()[0] if names else None


def _cap_words(s: str, n: int) -> str:
    """At most n words, cut at the last clause break inside the limit when one leaves ≥5 words."""
    s = re.sub(r"\s*\([^)]*\)", "", s or "").strip()  # parenthetical notes / attributions are not read back
    w = s.split()
    if len(w) <= n:
        return s
    cut = " ".join(w[:n])
    m = list(re.finditer(r"[,;:—–]", cut))
    if m and len(cut[:m[-1].start()].split()) >= 5:
        return cut[:m[-1].start()].strip()
    return cut.rstrip(",;:—-")


_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def _lower_first(txt: str) -> str:
    first = (txt.split() or [""])[0]
    if first.lower().strip(",.") in _MONTHS or (len(first) > 1 and first[1:2].isupper()):
        return txt
    return txt[:1].lower() + txt[1:]


def _template_teachback(wm: WorkMap, lang: str) -> str:
    """Whole sentences only, within the word budget. Every guardrail and judgment call is said; routine steps are
    dropped first (from the middle) when the budget is tight."""
    end = t("teach_end", lang)
    budget = CONFIG["teachback_words"] - len(end.split()) - len(t("teach_intro", lang).split())
    guards = []
    for i, g in enumerate(wm.guardrails):
        who = _first_name(wm, g.quote_ids)
        txt = _cap_words(g.text.rstrip("."), 16)
        guards.append(("" if i else t("watch", lang) + " ") + txt + (f" ({who})" if who and i == 0 else "") + ".")
    steps = ordered_steps(wm)
    sents: list[tuple[bool, str]] = []
    for i, s in enumerate(steps):
        lead = t("first", lang) if i == 0 else t("then", lang)
        judg = bool(s.decision and s.decision.kind == "judgment")
        txt = _cap_words(s.decision.description if judg else s.title, 20 if judg else 9)
        sents.append((judg, f"[[step:{s.id}]] {lead}, {_lower_first(txt)}."))
    used = _word_count(" ".join(guards))
    keep = list(range(len(sents)))
    while keep and used + _word_count(" ".join(sents[i][1] for i in keep)) > budget:
        routine = [i for i in keep if not sents[i][0] and 0 < i < len(sents) - 1] or \
            [i for i in keep if not sents[i][0]] or keep
        keep.remove(routine[len(routine) // 2])
    out = [t("teach_intro", lang)] + [sents[i][1] for i in keep]
    for g_s in guards:
        if _word_count(" ".join(out + [g_s])) <= budget + len(t("teach_intro", lang).split()):
            out.append(g_s)
    return " ".join(out) + " " + end


async def teach_back(wm: WorkMap, lang: str) -> str:
    steps = [{"id": s.id, "title": s.title, "decision": s.decision.description if s.decision else None,
              "judgment": bool(s.decision and s.decision.kind == "judgment"), "conflict": s.conflict}
             for s in ordered_steps(wm)]
    guards = [{"rule": g.text, "expert": _first_name(wm, g.quote_ids), "ask": g.owner} for g in wm.guardrails]
    out = await d.chat([
        {"role": "system", "content": f"Write a spoken teach-back of this workflow for the expert to confirm, in "
                                      f"language '{lang}', at most {CONFIG['teachback_words']} words (under one "
                                      "minute). Prefix the sentence about each step with its marker [[step:<id>]]. "
                                      "Say every judgment call and every guardrail briefly, in the expert's terms "
                                      "(attribute a rule to the expert by first name once or twice). Routine steps "
                                      "can be merged. End by asking if that's how it works. Plain text only."},
        {"role": "user", "content": json.dumps({"steps": steps, "guardrails": guards}, ensure_ascii=False)}],
        model_role="fast")
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


_ACTION_WORDS = {"block_and_explain": "stop and explain", "warn": "warn", "stop_and_ask": "stop and ask",
                 "hold": "put it on hold"}


def diff_readback(wm: WorkMap, diffs: list[dict], lang: str) -> str:
    """Natural speech, ONLY what changed: 'Got it — step 3 now: … Anything else, or is that how it works?'."""
    if not diffs:
        return t("nochange", lang)
    bits: list[str] = []
    said_when = False
    for df in diffs:
        new = df.get("new")
        if df["target"] == "step":
            s = step_by_id(wm, df["id"])
            if s is None or isinstance(new, (dict, list)) or new in (None, ""):
                continue
            bits.append(f"[[step:{s.id}]] " + t("step_now", lang, n=s.order, v=_short(new, 120).rstrip(".")))
        else:
            f = df.get("field")
            if f == "predicate" or isinstance(new, (dict, list)):
                if not said_when:
                    bits.append(t("when_now", lang))
                    said_when = True
            elif f == "owner" and new:
                bits.append(t("owner_now", lang, v=_short(new, 60)))
            elif f == "action" and new:
                bits.append(t("rule_now", lang, v=_ACTION_WORDS.get(str(new), str(new).replace("_", " "))))
            elif new:
                bits.append(t("rule_now", lang, v=_short(new, 120).rstrip(".")))
    if not bits:
        return t("nochange", lang)
    return f"{t('got_it', lang)} " + " ".join(bits) + " " + t("anything_else", lang)


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
    eid, ename = _expert(session)
    if st.phase == "questions":
        if not st.prepared:
            await prepare(wm, st, lang)
        u = pick_next(wm, st, eid, ename)
        if u is not None:
            u.status = "asked"
            st.current = u.id
            st.asked += 1
            if is_learner_item(u):
                st.learner_asked += 1
            else:
                st.core_asked += 1
            await _ledger(st.session_id, wm, ename, eid)
            return _question(u, lang, wm)
        st.phase = "teach_back"
    if st.phase == "teach_back":
        st.script = await teach_back(wm, lang)
        return st.script
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


# ---- answers that fix under-specified rules ----

GENERAL = re.compile(r"\b(every|all|any|each|whole|always|regardless|no matter|not only|jede[rsnm]?|alle[n]?|"
                     r"immer|egal|nicht nur|tous|toutes|tout|chaque|n'importe|todos|todas|cualquier|siempre|"
                     r"все|всех|любой|любых|всегда|не только)\b", re.I)
ONLY = re.compile(r"\b(only|just|nur|seulement|uniquement|solo|solamente|только|лишь)\b", re.I)
WHEN = re.compile(r"\b(if|when|whenever|unless|depends|only|wenn|falls|sofern|nur|si|quand|seulement|cuando|"
                  r"solo|если|когда|только)\b", re.I)
ROLE = re.compile(r"\b(controller|manager|supervisor|team lead|lead|cfo|director|treasurer|accountant|approver|"
                  r"buyer|purchasing|head of [\w-]+|controllerin|vorgesetzte\w*|leiter\w*|contrôleur|responsable|"
                  r"gerente|jefe|контролер\w*|контролёр\w*|руководител\w*|бухгалтер\w*)\b", re.I)
CONFIRM_EXTRA = re.compile(r"\b(but|except|only|actually|instead|aber|außer|nur|eigentlich|mais|sauf|seulement|"
                           r"plutôt|pero|excepto|solo|en realidad|но|кроме|только|вообще-то)\b", re.I)
HOW_IT_WORKS = re.compile(r"that'?s (how it works|it|right|correct)|genau so|so ist es|c'est (ça|bien ça)|así es|"
                          r"именно так|всё верно", re.I)

PATCH_G_SCHEMA = {"type": "object", "properties": {
    "text": {"type": "string"}, "predicate": {}, "owner": {}, "changed": {"type": "boolean"}}}


def _allowed_vars(wm: WorkMap) -> set[str]:
    base = set(wm.canonical_vars) | set(PRIOR_VARS)
    return base | {k[:-5] + suf for k in wm.canonical_vars if k.endswith("_date") for suf in ("_month", "_year")}


def _str_literals(p: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(p, dict):
        for k, v in p.items():
            if k != "var":
                out |= _str_literals(v)
    elif isinstance(p, list):
        for x in p:
            out |= _str_literals(x)
    elif isinstance(p, str):
        out.add(p)
    return out


def valid_patch_predicate(wm: WorkMap, old: Any, new: Any, answer: str) -> bool:
    """Vars must be observable; every string literal must be in the old predicate or in the expert's words."""
    if new is None:
        return True
    if not isinstance(new, dict):
        return False
    vs = predicate_vars(new)
    if not vs or any(v not in _allowed_vars(wm) and not v.startswith("doc.") for v in vs):
        return False
    try:
        eval_predicate(new, {v: 1 for v in vs})
    except Exception:  # noqa: BLE001
        return False
    old_l = {x.lower() for x in _str_literals(old)}
    low = (answer or "").lower()
    return all(x.lower() in old_l or x.lower() in low for x in _str_literals(new))


def drop_atoms(pred: Any, var: Optional[str], literal: Optional[str]) -> Any:
    """Remove string-literal atoms over `var` (or containing `literal`) from an AND/OR tree; None when empty."""
    def is_target(a: Any) -> bool:
        for v, lit, atom in literal_atoms(a):
            if atom is a and ((var and v == var) or (literal and literal.lower() in json.dumps(a).lower())):
                return True
        return False

    def walk(p: Any) -> Any:
        if not isinstance(p, dict) or len(p) != 1:
            return p
        if is_target(p):
            return None
        op, args = next(iter(p.items()))
        if op in ("and", "or") and isinstance(args, list):
            kept = [x for x in (walk(a) for a in args) if x is not None]
            if not kept:
                return None
            return kept[0] if len(kept) == 1 else {op: kept}
        if op == "!":
            inner = walk(args[0] if isinstance(args, list) else args)
            return None if inner is None else {"!": inner}
        return p
    return walk(copy.deepcopy(pred))


def _owner_from(text: str) -> Optional[str]:
    m = ROLE.search(text or "")
    if m:
        return m.group(1)
    names = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-ZÄÖÜА-Я][a-zäöüßа-я]{2,})\b", text or "")
    return names[0] if names else None


async def patch_guardrail(wm: WorkMap, g: Guardrail, u: Unknown, text: str, q: Quote, expert_name: str,
                          lang: str) -> list[dict]:
    """The expert answered a probe about ONE rule: patch only that guardrail (text, predicate, owner)."""
    m = u.meta or {}
    kind = m.get("probe") or ""
    before = {"text": g.text, "predicate": copy.deepcopy(g.predicate), "owner": g.owner}
    out = await d.chat([
        {"role": "system", "content":
            "An expert answered a follow-up question about ONE rule of a recorded workflow. Return the minimally "
            "patched rule as JSON {text, predicate, owner, changed}. text: the rule in the expert's terms, one "
            "sentence, in the rule's language. predicate: json-logic that is TRUE when a new record breaks the "
            "rule; use ONLY these vars: " + ", ".join(sorted(_allowed_vars(wm))) + ". If the answer widens a named "
            "party/value to a whole class, remove that atom (or use a var that expresses the class); if it adds a "
            "distinguishing condition, add it only when a var can express it; never add literals the expert did "
            "not say; null predicate if the rule cannot be checked from those vars. owner: who decides/is asked "
            "(null if not said). changed=false if the answer confirms the rule as it is."},
        {"role": "user", "content": json.dumps({"rule": g.text, "predicate": g.predicate, "owner": g.owner,
                                                "question": u.spoken_question, "probe": kind,
                                                "literal": m.get("literal"), "answer": text}, ensure_ascii=False)}],
        model_role="fast", json_schema=PATCH_G_SCHEMA)
    applied = False
    if isinstance(out, dict) and ("text" in out or "predicate" in out):
        if out.get("changed") is False:
            applied = True
        else:
            pred = out.get("predicate") if "predicate" in out else g.predicate
            if isinstance(pred, str):
                pred = d.parse_json(pred)
            if valid_patch_predicate(wm, before["predicate"], pred, text):
                if isinstance(out.get("text"), str) and out["text"].strip():
                    g.text = out["text"].strip()
                g.predicate = pred if isinstance(pred, dict) else None
                if isinstance(out.get("owner"), str) and out["owner"].strip():
                    g.owner = out["owner"].strip()
                applied = True
    if not applied:  # deterministic fallback
        first = (expert_name or "").split()[0] if expert_name else ""
        note = f" ({first}: “{_short(text, 90)}”)" if first else f" (“{_short(text, 90)}”)"
        widen = GENERAL.search(text or "") and not ONLY.search(text or "")
        if kind in ("party", "scope") and widen:
            g.predicate = drop_atoms(g.predicate, m.get("var"), m.get("literal")) if g.predicate else None
            g.text = g.text.rstrip(".") + note
        elif kind in ("kind", "threshold") and (ONLY.search(text or "") or WHEN.search(text or "") or
                                                re.search(r"\b(not|never|except|nicht|kein\w*|sauf|no|не|кроме)\b",
                                                          text or "", re.I)) and len(text.split()) >= 4:
            g.text = g.text.rstrip(".") + note  # a distinguishing condition the predicate cannot express yet
        elif kind == "fuzzy" and len(text.split()) >= 4:
            g.text = g.text.rstrip(".") + note  # how the expert spots the case: the fuzzy judge reads it
        if g.action in ("hold", "stop_and_ask") and not g.owner and kind in ("owner", "party", "scope"):
            g.owner = _owner_from(text) or g.owner
    g.fuzzy = not g.predicate
    g.quote_ids = list(dict.fromkeys(g.quote_ids + [q.id]))
    diffs = []
    for f in ("text", "predicate", "owner"):
        if getattr(g, f) != before[f]:
            g.approved = False
            diffs.append({"target": "guardrail", "id": g.id, "field": f, "old": before[f], "new": getattr(g, f),
                          "label": g.text})
    return diffs


async def answer_conflict(wm: WorkMap, u: Unknown, text: str, q: Quote, eid: str, ename: str) -> bool:
    """Record this expert's reason as their attributed variant; clear the conflict when the answer says when each
    way applies (→ decision.counterfactual). Returns True when the conflict is resolved."""
    st_ = step_by_id(wm, u.entity or "")
    if st_ is None:
        return False
    m = u.meta or {}
    mine = next((v for v in st_.variants if v.expert_id == eid), None)
    if mine is not None:
        mine.reason_quote_ids = list(dict.fromkeys(mine.reason_quote_ids + [q.id]))
    elif eid in st_.experts and st_.decision is not None and not m.get("this_did"):
        st_.decision.reason_quote_ids = list(dict.fromkeys(st_.decision.reason_quote_ids + [q.id]))
    else:
        st_.variants.append(Variant(expert_id=eid, description=m.get("this_did") or
                                    (st_.decision.description if st_.decision else st_.title),
                                    reason_quote_ids=[q.id]))
    explains, cond = None, None
    out = await d.chat([
        {"role": "system", "content": "Two experts handle the same step differently. Does this answer say WHEN each "
                                      "way applies (a case distinction), rather than just defending one way? JSON "
                                      "{explains_when: bool, condition: string|null} — condition = the distinction "
                                      "in one short sentence, in the answer's language."},
        {"role": "user", "content": json.dumps({"disagreement": st_.conflict, "question": u.spoken_question,
                                                "answer": text}, ensure_ascii=False)}],
        model_role="fast", json_schema={"type": "object"})
    if isinstance(out, dict) and isinstance(out.get("explains_when"), bool):
        explains, cond = out["explains_when"], out.get("condition")
    if explains is None:
        explains = bool(WHEN.search(text or "")) and len(text.split()) >= 4
    if explains:
        if st_.decision is not None:
            st_.decision.counterfactual = (cond if isinstance(cond, str) and cond.strip() else text).strip()
        st_.conflict = None
        for o in wm.open_unknowns:  # the mirrored side (legacy pairs) is settled too
            if o.id != u.id and o.type == "conflict" and o.entity == u.entity and o.status in OPEN_STATUSES:
                o.status, o.resolution, o.resolution_source = "resolved", text, "expert"
    return bool(explains)


async def publish_confirmed(wm: WorkMap, sid: str, eid: str) -> bool:
    """Explicit confirm: approve + publish. Unanswered core items are NOT left as a list: they become deferred
    'needs a second run' (coverage stays partial). Returns True if anything needs a second run."""
    for s in wm.steps:
        s.approved = True
    for g in wm.guardrails:
        g.approved = True
    wm.approved_by = list(dict.fromkeys(wm.approved_by + [eid]))
    second = False
    for u in wm.open_unknowns:
        if (u.meta or {}).get("origin") == "debrief" and (u.meta or {}).get("secondary") and u.status == "open":
            u.status = "dropped"  # optional extra probe, never needed
            continue
        if u.status in ("open", "asked") and u.scope in EXPERT_SCOPES and not is_learner_item(u):
            u.status = "deferred"
            u.meta = {**(u.meta or {}), "needs_second_run": True}
            second = True
        elif u.status == "deferred" and u.scope in EXPERT_SCOPES and not is_learner_item(u):
            u.meta = {**(u.meta or {}), "needs_second_run": True}
            second = True
    await save_map(wm, sid)
    return second


def is_confirm(text: str, intent: Optional[str]) -> bool:
    txt = text or ""
    if intent == "correction" or NO.search(txt):
        return False
    extra = CONFIRM_EXTRA.search(txt) and len(txt.split()) > 6
    if extra:
        return False
    return intent == "confirm" or bool(YES.search(txt)) or bool(HOW_IT_WORKS.search(txt))


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
                u.meta = {**(u.meta or {}), "skipped": True}
            else:
                q = _quote(session, text, lang)
                wm.quotes.append(q)
                u.status, u.resolution, u.resolution_source = "answered", text, "expert"
                u.answer_utterance_ids.append(q.id)
                ev = Moment(session_id=sid, keyframe_ids=list(u.moment.keyframe_ids) if u.moment else [],
                            t=q.t, utterance_ids=[q.id])
                g_probe = guardrail_by_id(wm, u.entity or "") if (u.meta or {}).get("probe") else None
                if u.type == "conflict":
                    await answer_conflict(wm, u, text, q, eid, ename)
                elif g_probe is not None:
                    await patch_guardrail(wm, g_probe, u, text, q, ename, lang)
                    if ev.keyframe_ids:
                        g_probe.evidence.append(ev)
                else:
                    _link_answer(wm, u, q, ev)
                    await _rule_from_answer(wm, u, text, q, ev, eid)
            await save_map(wm, sid)
        nxt = await next_debrief_utterance(session)
        return f"{t('thanks', lang)} {nxt}"

    if st.phase == "teach_back":
        if is_confirm(text, intent):
            second = await publish_confirmed(wm, sid, eid)
            st.phase = "done"
            return t("done_partial" if second else "done", lang)
        q = _quote(session, text, lang)
        wm.quotes.append(q)
        wm, diffs = await apply_correction(wm, text, lang, q.id)
        await save_map(wm, sid)
        for df in diffs:
            if df["target"] == "step":
                await d.send(sid, {"type": "highlight_step", "step_id": df["id"]})
        return diff_readback(wm, diffs, lang)

    st.phase = "done"
    return t("done", lang)


async def _rule_from_answer(wm: WorkMap, u: Unknown, text: str, q: Quote, ev: Moment, eid: str) -> None:
    """A live leftover / gap answer may state a new rule: restated rules are merged, new ones become guardrails."""
    rule = await _extract_rule(u, text, wm)
    if not rule:
        return
    u.extracted_rule = ExtractedRule(**{k: (str(rule[k]) if rule.get(k) is not None else None)
                                        for k in ("condition", "threshold", "action", "escalate_to")})
    pred = rule.get("predicate") if isinstance(rule.get("predicate"), dict) else None
    if pred and not all(v in wm.canonical_vars or v.startswith("doc.") or v in PRIOR_VARS
                        for v in predicate_vars(pred)):
        rule["predicate"] = None  # unobservable vars would never evaluate on a learner screen
    dup = _similar_guardrail(wm, rule.get("guardrail_text") or "", text) if rule.get("guardrail_text") else None
    if dup is not None:  # same rule restated (e.g. "the controller, Frank, approves UK invoices")
        dup.quote_ids = list(dict.fromkeys(dup.quote_ids + [q.id]))
        if rule.get("escalate_to") and not dup.owner:
            dup.owner = rule["escalate_to"]
    elif rule.get("guardrail_text") and not guardrail_by_id(wm, u.entity or ""):
        g = Guardrail(id=d.new_id("g"), text=rule["guardrail_text"], quote_ids=[q.id],
                      predicate=rule.get("predicate") if isinstance(rule.get("predicate"), dict) else None,
                      evidence=[ev], experts=[eid],
                      action="stop_and_ask" if u.type == "stop_and_ask" else "block_and_explain",
                      owner=rule.get("escalate_to"))
        g.fuzzy = g.predicate is None
        wm.guardrails.append(g)
        step = step_by_id(wm, u.entity or "")
        if step:
            step.guardrail_ids.append(g.id)


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

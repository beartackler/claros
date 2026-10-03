"""Knowledge-scope classifier + self-resolution of non-expert unknowns.

classify_scope(question, context) -> (KnowledgeScope, confidence)
try_resolve(unknown, context)      -> ContextNote | None   (None => ask the expert)

Only `company` and `personal_judgment` cost expert attention. Expert scopes are sticky:
if heuristics see company/tacit signals, an LLM "universal/app" verdict is overridden.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, Optional, Union

from claros.models import ContextNote, KnowledgeScope, Unknown

from . import onet, web
from ._llm import llm_json

log = logging.getLogger("claros.context.scope")

EXPERT_SCOPES = {"company", "personal_judgment"}
SCOPES = ("universal", "app", "occupation", "company", "personal_judgment")

# --- heuristic signals (EN + a few RU/DE/FR/ES) ---
COMPANY = [
    r"\b(our|we|us|ours)\b", r"\bthreshold", r"\blimit\b", r"\bapprov", r"\bpolicy|policies\b",
    r"\bwho (signs|approves|decides|owns)", r"\bsign[- ]off", r"\bin this (company|team|org)",
    r"\byour (rule|team|company|policy)", r"\binternal\b", r"\bcost cent(er|re) \d", r"\b(over|above|below|under) \$?€?\d",
    r"\bvendor list\b", r"\bpreferred (supplier|vendor)", r"\bescalat\w* to (whom|who)",
    r"\b(наш|у нас|порог|лимит|согласов|утвержда)", r"\b(unser|wir|freigabe|grenze)\b",
    r"\b(notre|nous|seuil)\b", r"\b(nuestr|umbral|aprobaci)",
]
PERSONAL = [
    r"\bwhy did you\b", r"\bwhy do you\b", r"\bwhy you\b", r"\bhow do you (decide|know|tell|judge)",
    r"\byou (chose|changed|skipped|picked|decided|preferred)", r"\bgut\b", r"\bsmell", r"\bfeel(s)? (off|wrong|right)",
    r"\bexception\b", r"\bjudg(e)?ment\b", r"\bwhen would you\b", r"\bwhat made you\b", r"\bred flag",
    r"\bпочему (вы|ты)\b", r"\bwarum (haben sie|hast du)\b", r"\bpourquoi (avez-vous|as-tu)\b",
]
UNIVERSAL = [
    r"^\s*what('s| is| are| does .* mean)\b", r"\bdefin(e|ition)\b", r"\bdifference between\b",
    r"\bmeaning of\b", r"\bin accounting\b", r"\bin general\b", r"\bstands? for\b", r"^\s*(что такое|was ist|qu'est-ce que|qué es)",
]
APP = [
    r"\bfield\b", r"\bbutton\b", r"\bcheckbox\b", r"\btab\b", r"\bmenu\b", r"\bdoctype\b", r"\bdropdown\b",
    r"\bscreen\b", r"\bform\b", r"\bmodule\b", r"\bsetting(s)?\b", r"\bin (erpnext|sap|odoo|zammad|netsuite|salesforce|jira|zendesk|servicenow|quickbooks|xero)\b",
    r"\b[a-z]+_[a-z_]+\b", r"\bwhat does (the )?['\"]?[\w ]+['\"]? (do|mean) in\b", r"\bstatus\b",
]
OCCUPATION = [
    r"\btypical(ly)?\b", r"\bstandard (process|procedure|steps)", r"\busually done\b", r"\bresponsibilit",
    r"\bwhat does an? [\w ]+ (do|handle)\b", r"\bgeneric\b", r"\bin most companies\b", r"\bjob of\b",
]


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


def heuristic_scope(question: str, context: Optional[dict] = None) -> tuple[KnowledgeScope, float]:
    q = question or ""
    scores = {
        "company": _hits(COMPANY, q) * 1.2,
        "personal_judgment": _hits(PERSONAL, q) * 1.3,
        "universal": _hits(UNIVERSAL, q) * 1.0,
        "app": _hits(APP, q) * 0.9 + (0.3 if (context or {}).get("app") and _hits(APP, q) else 0),
        "occupation": _hits(OCCUPATION, q) * 1.5,
    }
    best = max(scores, key=lambda s: scores[s])
    if scores[best] == 0:
        return "personal_judgment", 0.4  # unknown -> ask the expert (safe default)
    # expert signals win ties / near-ties (never answer company rules from the web)
    expert = max(("company", "personal_judgment"), key=lambda s: scores[s])
    if scores[expert] > 0 and scores[expert] >= scores[best] * 0.6:
        best = expert
    total = sum(scores.values())
    conf = 0.45 + 0.4 * (scores[best] / total)
    return best, round(min(conf, 0.85), 2)  # type: ignore[return-value]


CLASSIFY_PROMPT = """You decide who can answer a question an AI apprentice has while watching an expert work.
Scopes:
- universal: general domain knowledge anyone could look up (definitions, accounting/legal basics).
- app: what a software field/button/status/feature does, answerable from the app's official docs.
- occupation: generic structure of this kind of job/task, same across companies.
- company: this organisation's specific rules: thresholds, approvers, policies, vendors, cost centers, internal process.
- personal_judgment: the expert's own tacit reasoning, heuristics, exceptions, why they did something.
If any part depends on this company's rules or this person's reasoning, choose company/personal_judgment.
Reply JSON only: {"scope": "...", "confidence": 0.0-1.0, "reason": "..."}"""


async def classify_scope(unknown_question: str, context: Optional[dict] = None) -> tuple[KnowledgeScope, float]:
    context = context or {}
    h_scope, h_conf = heuristic_scope(unknown_question, context)
    ctx = {k: v for k, v in context.items() if k in ("app", "view", "workflow", "entity_type", "lang", "occupation")}
    out = await llm_json(
        [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": f"Context: {ctx}\nQuestion: {unknown_question}"},
        ]
    )
    if not out or out.get("scope") not in SCOPES:
        return h_scope, h_conf
    scope: Any = out["scope"]
    try:
        conf = float(out.get("confidence", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    if scope not in EXPERT_SCOPES and h_scope in EXPERT_SCOPES and h_conf >= 0.6:
        return h_scope, h_conf
    return scope, round(max(0.0, min(conf, 1.0)), 2)


# ---------------------------------------------------------------- resolve

def _question_of(unknown: Union[Unknown, str, dict]) -> str:
    if isinstance(unknown, str):
        return unknown
    if isinstance(unknown, dict):
        unknown = Unknown.model_validate(unknown)
    parts = [unknown.spoken_question or "", unknown.hypothesis or ""]
    if not any(parts):
        parts = [f"{unknown.type} {unknown.entity or ''}"]
    return " ".join(p for p in parts if p).strip()


def _note_id(text: str, source: str) -> str:
    return "ctx_" + hashlib.sha1(f"{source}|{text}".encode()).hexdigest()[:12]


_Q_WORDS = set("what does do did the a an is are this that how why when which mean means for of to on in it its happen happens".split())


def _detect_app(question: str, context: dict) -> Optional[str]:
    if context.get("app"):
        return str(context["app"])
    low = question.lower()
    for name in web.APP_DOCS:
        if name in low:
            return name
    return None


async def _resolve_universal(q: str, context: dict) -> Optional[ContextNote]:
    out = await llm_json(
        [
            {"role": "system", "content": "Answer general domain-knowledge questions in 1-3 sentences. "
             "Do not guess company-specific rules. If the answer depends on a specific company or person, "
             'set "answerable": false. Reply JSON: {"answerable": bool, "answer": "..."}'},
            {"role": "user", "content": f"Context: {context.get('app') or ''} {context.get('workflow') or ''}\nQuestion: {q}"},
        ]
    )
    if not out or not out.get("answerable") or not out.get("answer"):
        return None
    return ContextNote(id=_note_id(out["answer"], "llm"), text=out["answer"].strip(), scope="universal", source="llm")


async def _resolve_app(q: str, context: dict) -> Optional[ContextNote]:
    app = _detect_app(q, context)
    if not app:
        return None
    topic = re.sub(rf"(?i)\bin {re.escape(app)}\b", "", q)
    topic = " ".join(w for w in re.findall(r"[\w.-]+", topic) if w.lower() not in _Q_WORDS) or q
    snippets = await web.app_docs(app, topic)
    if not snippets:
        return None
    numbered = "\n\n".join(f"[{i}] {s['url']}\n{s['snippet']}" for i, s in enumerate(snippets))
    out = await llm_json(
        [
            {"role": "system", "content": "Answer the question about the software using ONLY the doc excerpts. "
             'Reply JSON: {"answerable": bool, "answer": "1-3 sentences", "source_index": int}. '
             "answerable=false if the excerpts do not answer it."},
            {"role": "user", "content": f"App: {app}\nQuestion: {q}\n\nExcerpts:\n{numbered}"},
        ]
    )
    if out is not None:
        if not out.get("answerable") or not out.get("answer"):
            return None
        idx = out.get("source_index", 0)
        src = snippets[idx]["url"] if isinstance(idx, int) and 0 <= idx < len(snippets) else snippets[0]["url"]
        text = out["answer"].strip()
    else:  # no LLM: cite the best snippet verbatim
        if not snippets[0].get("official"):
            return None
        src, text = snippets[0]["url"], snippets[0]["snippet"][:500]
    return ContextNote(id=_note_id(text, src), text=f"{text} (from {app} docs)", scope="app", source=src)


async def _resolve_occupation(q: str, context: dict) -> Optional[ContextNote]:
    if not onet.available():
        return None
    query = " ".join(str(x) for x in (context.get("workflow"), q) if x)
    matches = await onet.amatch(query, k=3)
    if not matches:
        return None
    m = matches[0]
    profile = await asyncio.to_thread(onet.occupation_profile, m.occupation_code, 25)
    tasks = "\n".join(f"- {t['task']}" for t in (profile or {}).get("tasks", [])[:25])
    out = await llm_json(
        [
            {"role": "system", "content": "Answer using ONLY the O*NET occupation tasks given (generic, not company-specific). "
             'Reply JSON: {"answerable": bool, "answer": "1-3 sentences"}.'},
            {"role": "user", "content": f"Occupation: {m.occupation_title} ({m.occupation_code})\nTasks:\n{tasks}\n\nQuestion: {q}"},
        ]
    )
    if out is not None and out.get("answerable") and out.get("answer"):
        text = out["answer"].strip()
    elif out is not None:
        return None
    else:
        dw = "; ".join(m.dwas[:3])
        text = f"Generic task for {m.occupation_title}: {m.task}" + (f" Related activities: {dw}." if dw else "")
    src = f"onet:{m.occupation_code}"
    return ContextNote(id=_note_id(text, src), text=f"{text} (O*NET {m.occupation_code})", scope="occupation", source=src)


async def try_resolve(unknown: Union[Unknown, str, dict], context: Optional[dict] = None) -> Optional[ContextNote]:
    """Answer an unknown without the expert when it's universal/app/occupation scope.
    Returns a cited ContextNote, or None => it must go to the expert."""
    context = context or {}
    q = _question_of(unknown)
    if not q:
        return None
    try:
        scope, conf = await classify_scope(q, context)
        if scope in EXPERT_SCOPES or conf < 0.5:
            return None
        if scope == "universal":
            return await _resolve_universal(q, context)
        if scope == "app":
            return await _resolve_app(q, context)
        if scope == "occupation":
            return await _resolve_occupation(q, context)
    except Exception as e:  # never break the ledger
        log.warning("try_resolve failed: %s", e)
    return None

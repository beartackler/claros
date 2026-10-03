"""Intent classification: multilingual rules → System One choice → LLM → heuristic default."""
from __future__ import annotations

import json
import re
from typing import Optional

from claros.models import IntentResult

from . import deps, systemone

EXPERT_INTENTS = {
    "narration": "Expert is narrating or thinking aloud about the work; not addressed to Claros.",
    "answer": "Expert answers a question Claros just asked (explains why, a limit, a rule).",
    "correction": "Expert corrects something Claros said or a previous answer.",
    "confirm": "Expert confirms Claros' hypothesis or teach-back (yes, right, exactly).",
    "not_now": "Expert asks Claros to wait / ask later.",
    "off_record": "Expert asks to stop recording / go off the record.",
    "strike_that": "Expert asks to delete/forget what was just said.",
    "question_to_claros": "Expert asks Claros a question.",
    "end_session": "Expert says the task/session is done.",
}
LEARNER_INTENTS = {
    "walk_through": "Learner wants to be guided step by step through the task.",
    "what_next": "Learner asks what the next step is.",
    "why_this": "Learner asks why a step/decision is done this way.",
    "check_my_work": "Learner asks Claros to check what they did.",
    "hint": "Learner asks for a hint.",
    "just_watch": "Learner wants Claros to stay quiet and watch.",
    "stop": "Learner wants to stop the session.",
    "ask_expert": "Learner wants to ask a human expert.",
    "answer_prediction": "Learner answers a prediction/quiz prompt from Claros.",
    "off_topic": "Unrelated chatter.",
}

# Ordered (first match wins). Patterns are regex fragments matched on lowercased text.
_EXPERT_RULES: list[tuple[str, list[str]]] = [
    ("off_record", [
        r"off the record", r"stop recording", r"don'?t record", r"pause recording",
        r"nicht aufzeichnen", r"nicht aufnehmen", r"aufnahme (stoppen|pausieren)", r"inoffiziell",
        r"hors micro", r"hors enregistrement", r"n'enregistre pas", r"arr[êe]te (d'|l')enregistr",
        r"fuera de registro", r"no grabes", r"deja de grabar", r"off the record",
        r"не записывай", r"не записывать", r"не для записи", r"без записи", r"выключи запись", r"останови запись",
    ]),
    ("strike_that", [
        r"strike that", r"scratch that", r"forget (that|what i said)", r"delete that", r"ignore that",
        r"streich das", r"vergiss das", r"das l[öo]schen", r"l[öo]sch das",
        r"oublie (ça|ca)", r"efface (ça|ca)", r"raye (ça|ca)",
        r"olv[ií]dalo", r"borra eso", r"tacha eso", r"olvida eso",
        r"забудь", r"удали (это|последнее)", r"вычеркни", r"сотри (это)?",
    ]),
    ("not_now", [
        r"not now", r"^(maybe )?later\b", r"ask me later", r"^hold on", r"^one sec", r"not right now",
        r"nicht jetzt", r"^sp[äa]ter", r"frag (mich )?sp[äa]ter", r"^moment mal",
        r"pas maintenant", r"^plus tard", r"demande(-moi)? plus tard",
        r"ahora no", r"^m[áa]s tarde", r"^luego\b", r"preg[úu]ntame (luego|despu[ée]s|m[áa]s tarde)",
        r"не сейчас", r"^(давай )?потом\b", r"^позже", r"спроси (потом|позже)", r"^подожди",
    ]),
    ("end_session", [
        r"(i'?m|we'?re) done", r"that'?s it for (today|now)", r"end (the )?session",
        r"ich bin fertig", r"das war'?s", r"j'ai fini", r"c'est fini", r"he terminado", r"eso es todo",
        r"я закончил", r"закончили", r"на сегодня всё", r"конец сессии",
    ]),
    ("confirm", [
        r"^(yes|yeah|yep|right|exactly|correct|that'?s right)\b",
        r"^(ja|genau|richtig|stimmt)\b", r"^(oui|exactement|c'est (ça|ca)|tout à fait)\b",
        r"^(s[ií]|exacto|correcto|eso es)\b", r"^(да|верно|точно|именно|правильно)\b",
    ]),
    ("correction", [
        r"^no,? (actually|that'?s wrong)", r"that'?s not (right|what)", r"actually,? it'?s",
        r"^nein,? ", r"das stimmt nicht", r"^non,? ", r"ce n'est pas (ça|ca)", r"^no,? en realidad", r"no es (así|eso)",
        r"^нет,? ", r"не так", r"неправильно",
    ]),
]
_LEARNER_RULES: list[tuple[str, list[str]]] = [
    ("walk_through", [r"walk me through", r"show me how", r"guide me", r"step by step",
                      r"f[üu]hr mich", r"zeig mir wie", r"schritt f[üu]r schritt",
                      r"guide[- ]moi", r"montre[- ]moi comment", r"[ée]tape par [ée]tape",
                      r"gu[íi]ame", r"mu[ée]strame c[óo]mo", r"paso a paso",
                      r"проведи меня", r"покажи как", r"пошагово", r"шаг за шагом"]),
    ("what_next", [r"what('?s| is| do i do)? next", r"what now", r"next step",
                   r"was (kommt )?jetzt", r"n[äa]chste[rn]? schritt", r"et maintenant", r"prochaine [ée]tape",
                   r"qu'est-ce que je fais", r"y ahora", r"siguiente paso", r"qu[ée] sigue",
                   r"что дальше", r"следующий шаг", r"что теперь", r"что делать"]),
    ("hint", [r"\bhint\b", r"give me a clue", r"\btipp\b", r"\bhinweis\b", r"\bindice\b", r"une piste",
              r"\bpista\b", r"подсказк", r"подскажи"]),
    ("why_this", [r"\bwhy\b", r"\bwarum\b", r"\bwieso\b", r"pourquoi", r"por qu[ée]", r"почему", r"зачем"]),
    ("check_my_work", [r"check (my|this)", r"did i (do|get) (it|this) right", r"is this (right|correct)",
                       r"pr[üu]f", r"stimmt das", r"v[ée]rifie", r"c'est bon", r"rev[ií]sa", r"est[áa] bien",
                       r"провер", r"правильно ли"]),
    ("ask_expert", [r"ask (the|an) expert", r"ask (my|the) lead", r"experte?n? fragen", r"demande (à|a) l'expert",
                    r"preg[úu]ntale al experto", r"спроси эксперт", r"спросить эксперт"]),
    ("just_watch", [r"just watch", r"stay quiet", r"be quiet", r"nur zuschauen", r"sei still",
                    r"regarde seulement", r"tais-toi", r"solo mira", r"c[áa]llate", r"просто смотри", r"помолчи"]),
    ("stop", [r"^stop\b", r"\bstop (it|now|the session)", r"\bhör auf\b", r"\bstopp\b", r"\barr[êe]te\b",
              r"^para\b", r"^det[ée]nte", r"\bхватит\b", r"\bстоп\b", r"остановись"]),
]

_COMPILED: dict[str, list[tuple[str, list[re.Pattern]]]] = {
    "expert": [(i, [re.compile(p) for p in ps]) for i, ps in _EXPERT_RULES],
    "learner": [(i, [re.compile(p) for p in ps]) for i, ps in _LEARNER_RULES],
}


def _family(mode: str) -> str:
    return "learner" if mode in ("learn", "request") else "expert"


def rule_intent(mode: str, text: str) -> Optional[str]:
    t = (text or "").lower().strip()
    if not t:
        return None
    for intent, pats in _COMPILED[_family(mode)]:
        if any(p.search(t) for p in pats):
            return intent
    return None


def _default(mode: str, text: str, context: dict) -> str:
    t = (text or "").strip()
    if _family(mode) == "learner":
        if context.get("pending_prediction"):
            return "answer_prediction"
        return "why_this" if t.endswith("?") else "off_topic"
    if context.get("pending_question"):
        return "answer"
    if t.endswith("?") and re.search(r"claros|клар", t.lower()):
        return "question_to_claros"
    return "narration"


async def classify_intent(mode: str, text: str, context: Optional[dict] = None,
                          *, timeout: float = 0.9) -> IntentResult:
    context = context or {}
    r = rule_intent(mode, text)
    if r:
        return IntentResult(intent=r, confidence=0.95, backend="rules")
    labels = LEARNER_INTENTS if _family(mode) == "learner" else EXPERT_INTENTS
    state = {"mode": mode, "utterance": text,
             "pending_question": context.get("pending_question"),
             "last_agent_utterance": context.get("last_agent_utterance"),
             "screen": context.get("screen")}
    q = {"intent": {"type": "choice", "instructions": "Classify the user's latest utterance (any language).",
                    "criteria": labels}}
    try:
        res = await systemone.decide(state, q, timeout=timeout, fallback=False)
        a = res["answers"].get("intent") or {}
        if a.get("choice") in labels and a.get("confidence", 0) >= 0.3:
            return IntentResult(intent=a["choice"], confidence=float(a["confidence"]), backend=res["backend"])
    except Exception:  # noqa: BLE001
        deps.log.debug("systemone intent failed", exc_info=True)
    try:
        import asyncio
        txt = await asyncio.wait_for(deps.llm_chat([
            {"role": "system", "content": "Classify the user's utterance into one intent. Reply JSON "
             '{"intent": <label>, "confidence": 0..1}. Labels: ' + json.dumps(labels)},
            {"role": "user", "content": json.dumps(state, ensure_ascii=False, default=str)},
        ], model_role="fast", json_schema={"type": "object"}), max(timeout * 2, 1.2))
        if txt:
            d = systemone._parse_json(txt)
            if d.get("intent") in labels:
                return IntentResult(intent=d["intent"], confidence=float(d.get("confidence", 0.6)), backend="llm")
    except Exception:  # noqa: BLE001
        deps.log.debug("llm intent failed", exc_info=True)
    return IntentResult(intent=_default(mode, text, context), confidence=0.3, backend="default")


__all__ = ["classify_intent", "rule_intent", "EXPERT_INTENTS", "LEARNER_INTENTS"]

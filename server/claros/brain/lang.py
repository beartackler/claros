"""Tiny language helpers: detection heuristic + localized fixed phrases + question templates."""
from __future__ import annotations

import re
from typing import Optional

LANGS = ("en", "de", "fr", "es", "ru")

_MARKERS = {
    "de": {"ich", "und", "nicht", "das", "ist", "der", "die", "wir", "hier", "weil", "immer", "bitte", "warum",
           "jetzt", "mal", "auf", "mit", "ein", "eine", "kein", "noch", "später", "wie"},
    "fr": {"je", "le", "la", "les", "pas", "est", "et", "que", "pour", "ici", "parce", "toujours", "pourquoi",
           "maintenant", "une", "des", "avec", "ça", "ce", "on", "vous", "plus", "tard"},
    "es": {"el", "la", "los", "que", "por", "es", "y", "no", "aquí", "porque", "siempre", "ahora", "una",
           "con", "para", "esto", "eso", "pero", "más", "tarde", "qué", "cómo"},
    "en": {"the", "and", "is", "i", "this", "that", "it", "not", "because", "always", "why", "now", "we",
           "here", "what", "do", "you", "to", "of", "later", "so"},
}


def detect_lang(text: str, default: Optional[str] = None) -> Optional[str]:
    """Returns a lang code if reasonably confident, else `default`."""
    t = (text or "").lower()
    if not t.strip():
        return default
    letters = [c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if "Ѐ" <= c <= "ӿ") / len(letters) > 0.4:
        return "ru"
    words = re.findall(r"[\w']+", t)
    if len(words) < 3:
        if re.search(r"[äöüß]", t):
            return "de"
        if re.search(r"[ñ¿¡]", t):
            return "es"
        return default
    scores = {lg: sum(1 for w in words if w in ms) for lg, ms in _MARKERS.items()}
    if re.search(r"[äöüß]", t):
        scores["de"] += 2
    if re.search(r"[ñ¿¡]", t):
        scores["es"] += 2
    if re.search(r"[çèêàù]|\b(c'est|j'ai|n'est|qu')", t):
        scores["fr"] += 2
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] >= 2 and ranked[0] > ranked[1]:
        return best
    return default


PHRASES: dict[str, dict[str, str]] = {
    "ack": {"en": "Got it, thanks.", "de": "Verstanden, danke.", "fr": "Compris, merci.",
            "es": "Entendido, gracias.", "ru": "Понял, спасибо."},
    "ack_correction": {"en": "Got it, corrected.", "de": "Okay, korrigiert.", "fr": "D'accord, corrigé.",
                       "es": "Vale, corregido.", "ru": "Понял, исправил."},
    "ack_confirm": {"en": "Great, noted.", "de": "Super, notiert.", "fr": "Parfait, noté.",
                    "es": "Perfecto, anotado.", "ru": "Отлично, записал."},
    "later": {"en": "Sure, later.", "de": "Klar, später.", "fr": "D'accord, plus tard.",
              "es": "Claro, luego.", "ru": "Хорошо, позже."},
    "off_record": {"en": "Off the record.", "de": "Nicht mehr aufgezeichnet.", "fr": "Hors micro.",
                   "es": "Fuera de registro.", "ru": "Не записываю."},
    "on_record": {"en": "Back on the record.", "de": "Aufzeichnung läuft wieder.", "fr": "On reprend l'enregistrement.",
                  "es": "Grabando de nuevo.", "ru": "Снова записываю."},
    "struck": {"en": "Okay, struck that.", "de": "Okay, gestrichen.", "fr": "D'accord, effacé.",
               "es": "Vale, borrado.", "ru": "Хорошо, удалил."},
    "end": {"en": "Thanks, wrapping up.", "de": "Danke, ich schließe ab.", "fr": "Merci, je termine.",
            "es": "Gracias, cerrando.", "ru": "Спасибо, завершаю."},
    "stop": {"en": "Okay, stopping.", "de": "Okay, ich höre auf.", "fr": "D'accord, j'arrête.",
             "es": "Vale, paro.", "ru": "Хорошо, останавливаюсь."},
    "watch": {"en": "Okay, I'll just watch.", "de": "Okay, ich schaue nur zu.", "fr": "D'accord, je regarde.",
              "es": "Vale, solo miro.", "ru": "Хорошо, просто смотрю."},
    "buffer": {"en": "Hmm, let me think.", "de": "Hm, kurz überlegen.", "fr": "Hmm, je réfléchis.",
               "es": "Mmm, déjame pensar.", "ru": "Хм, секунду."},
    "no_llm": {"en": "Sorry, I can't answer that right now.", "de": "Das kann ich gerade nicht beantworten.",
               "fr": "Je ne peux pas répondre pour l'instant.", "es": "Ahora no puedo responder eso.",
               "ru": "Сейчас не могу ответить."},
}


def phrase(key: str, lang: str) -> str:
    d = PHRASES[key]
    return d.get((lang or "en")[:2], d["en"])


LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "ru": "Russian"}

# Spoken question templates (≤15 words). {f}=field, {v}=visible value, {h}=hypothesis, {e}=entity
Q_TEMPLATES: dict[str, dict[str, str]] = {
    "confirm": {"en": "Looks like {h} — is that your rule?",
                "de": "Sieht aus wie: {h} — ist das Ihre Regel?",
                "fr": "On dirait : {h} — c'est votre règle ?",
                "es": "Parece que {h} — ¿es su regla?",
                "ru": "Похоже, {h} — это ваше правило?"},
    "why": {"en": "Why {v} for {f} here?",
            "de": "Warum {v} bei {f} hier?",
            "fr": "Pourquoi {v} pour {f} ici ?",
            "es": "¿Por qué {v} en {f} aquí?",
            "ru": "Почему {v} в поле {f}?"},
    "limit": {"en": "At {v}, where's the limit for {f}?",
              "de": "Bei {v}: Wo liegt die Grenze für {f}?",
              "fr": "À {v}, quelle est la limite pour {f} ?",
              "es": "Con {v}, ¿cuál es el límite para {f}?",
              "ru": "При {v} — где граница для {f}?"},
    "stop_and_ask": {"en": "What made you stop on {v}? Who decides then?",
                     "de": "Warum haben Sie bei {v} gestoppt? Wer entscheidet dann?",
                     "fr": "Pourquoi arrêter sur {v} ? Qui décide alors ?",
                     "es": "¿Por qué paró en {v}? ¿Quién decide entonces?",
                     "ru": "Почему вы остановились на {v}? Кто тогда решает?"},
    "never": {"en": "Is there something you'd never let through, like {v}?",
              "de": "Gibt es etwas, das Sie nie durchlassen, wie {v}?",
              "fr": "Y a-t-il quelque chose que vous ne laissez jamais passer, comme {v} ?",
              "es": "¿Hay algo que nunca dejaría pasar, como {v}?",
              "ru": "Есть ли то, что вы никогда не пропустите, как {v}?"},
    "deliberate": {"en": "You undid {f} — slip, or on purpose?",
                   "de": "Sie haben {f} rückgängig gemacht — Versehen oder Absicht?",
                   "fr": "Vous avez annulé {f} — erreur ou exprès ?",
                   "es": "Deshizo {f} — ¿error o a propósito?",
                   "ru": "Вы отменили {f} — случайно или намеренно?"},
    "never_generic": {"en": "Before submitting {e}, when would you stop and ask someone?",
                      "de": "Wann würden Sie vor dem Buchen von {e} nachfragen?",
                      "fr": "Avant de valider {e}, quand demanderiez-vous à quelqu'un ?",
                      "es": "Antes de enviar {e}, ¿cuándo pararía a preguntar?",
                      "ru": "Когда перед отправкой {e} вы бы остановились и спросили?"},
}

HYP_TEMPLATES: dict[str, dict[str, str]] = {
    "threshold": {"en": "{f} over {th} needs review", "de": "{f} über {th} wird geprüft",
                  "fr": "{f} au-dessus de {th} est vérifié", "es": "{f} sobre {th} se revisa",
                  "ru": "{f} выше {th} проверяется"},
    "override": {"en": "{f} becomes {v} in cases like this", "de": "{f} wird in solchen Fällen {v}",
                 "fr": "{f} devient {v} dans ce cas", "es": "{f} pasa a {v} en estos casos",
                 "ru": "{f} в таких случаях — {v}"},
}


def fill(tpl_key: str, lang: str, table: dict = Q_TEMPLATES, **kw: str) -> str:
    d = table[tpl_key]
    s = d.get((lang or "en")[:2], d["en"])
    for k, v in kw.items():
        s = s.replace("{" + k + "}", _short(v, 70 if k == "h" else 28))
    return s


def fmt_num(x: float, lang: str = "en") -> str:
    s = f"{int(x):,}" if float(x).is_integer() else f"{x:,.2f}"
    lg = (lang or "en")[:2]
    if lg in ("de", "es"):
        return s.replace(",", "§").replace(".", ",").replace("§", ".")
    if lg in ("fr", "ru"):
        return s.replace(",", "\u202f").replace(".", ",")
    return s


def _short(v: object, n: int = 28) -> str:
    s = str(v if v is not None else "").strip() or "this"
    return s if len(s) <= n else s[: n - 1] + "…"


def clamp_words(s: str, n: int = 15) -> str:
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]).rstrip(",;:") + "?"

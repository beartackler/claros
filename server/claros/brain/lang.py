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
    # "that's" counts as "that" (English contractions); a single foreign word with a diacritic ("Rückbuchung")
    # inside an English sentence must not flip the reply language: the diacritic bonus only counts when the
    # language also has marker words, otherwise it is a tie-breaker
    words = words + [w.split("'")[0] for w in words if "'" in w]
    scores = {lg: sum(1 for w in words if w in ms) for lg, ms in _MARKERS.items()}

    def bonus(lg: str) -> float:
        return 2 if scores[lg] >= 1 else 0.5

    if re.search(r"[äöüß]", t):
        scores["de"] += bonus("de")
    if re.search(r"[ñ¿¡]", t):
        scores["es"] += bonus("es")
    if re.search(r"[çèêàù]|\b(c'est|j'ai|n'est|qu')", t):
        scores["fr"] += bonus("fr")
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
    "off_record": {"en": "Off the record — I'm not watching or listening. Tap Resume when you're ready.",
                   "de": "Inoffiziell — ich schaue nicht zu und höre nicht mit. Tippen Sie auf Fortsetzen, wenn Sie "
                         "so weit sind.",
                   "fr": "Hors enregistrement — je ne regarde ni n'écoute. Touchez Reprendre quand vous êtes prêt.",
                   "es": "Fuera de registro — no estoy mirando ni escuchando. Toque Reanudar cuando esté listo.",
                   "ru": "Не для записи — я не смотрю и не слушаю. Нажмите «Продолжить», когда будете готовы."},
    "greet_capture": {"en": "Hi, I'm Claros. Just work as usual and talk me through it — I'll only ask at pauses. What are you about to do?",
                      "de": "Hallo, ich bin Claros. Arbeiten Sie einfach wie immer und erzählen Sie dabei — ich frage nur in Pausen. Was haben Sie vor?",
                      "fr": "Bonjour, je suis Claros. Travaillez comme d'habitude en m'expliquant — je ne pose de questions qu'aux pauses. Qu'allez-vous faire ?",
                      "es": "Hola, soy Claros. Trabaje como siempre y cuénteme lo que hace — solo pregunto en las pausas. ¿Qué va a hacer?",
                      "ru": "Привет, я Claros. Работайте как обычно и рассказывайте — я спрашиваю только в паузах. Что вы собираетесь делать?"},
    "greet_learn": {"en": "Hi, I'm Claros. What are you working on?",
                    "de": "Hallo, ich bin Claros. Woran arbeiten Sie gerade?",
                    "fr": "Bonjour, je suis Claros. Sur quoi travaillez-vous ?",
                    "es": "Hola, soy Claros. ¿En qué está trabajando?",
                    "ru": "Привет, я Claros. Над чем вы работаете?"},
    "on_record": {"en": "Back on the record.", "de": "Wieder offiziell — ich zeichne auf.",
                  "fr": "De retour dans l'enregistrement.", "es": "De vuelta en el registro.",
                  "ru": "Снова под запись."},
    "struck": {"en": "Okay, struck that.", "de": "Okay, gestrichen.", "fr": "D'accord, effacé.",
               "es": "Vale, borrado.", "ru": "Хорошо, удалил."},
    "end": {"en": "Thanks, wrapping up.", "de": "Danke, ich schließe ab.", "fr": "Merci, je termine.",
            "es": "Gracias, cerrando.", "ru": "Спасибо, завершаю."},
    "to_debrief": {"en": "Thanks! Let's do a quick debrief.", "de": "Danke! Kurz nachbesprechen?",
                   "fr": "Merci ! Faisons un court débrief.", "es": "¡Gracias! Hagamos un breve repaso.",
                   "ru": "Спасибо! Давайте коротко подведём итоги."},
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
    "why_field": {"en": "Why this {f} here?",
                  "de": "Warum dieser Wert bei {f} hier?",
                  "fr": "Pourquoi cette valeur pour {f} ici ?",
                  "es": "¿Por qué ese valor en {f} aquí?",
                  "ru": "Почему здесь такое значение в поле {f}?"},
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

# Action-phrased live questions (contract v2.2): describe what the expert DID in plain words, then ask why.
# {f}=field label (plain case), {v}=speakable value, {th}=round threshold, {n}=record noun ("that one"/"that invoice"),
# {a}=action clause, {h}=hypothesis
ACTION_TEMPLATES: dict[str, dict[str, str]] = {
    "edit": {"en": "You changed the {f} to {v} — what made you do that?",
             "de": "Sie haben {f} auf {v} geändert — warum?",
             "fr": "Vous avez changé {f} en {v} — qu'est-ce qui vous a fait faire ça ?",
             "es": "Cambió {f} a {v} — ¿qué le hizo decidir eso?",
             "ru": "Вы поменяли {f} на {v} — почему?"},
    "edit_nov": {"en": "You changed the {f} — what made you do that?",
                 "de": "Sie haben {f} geändert — warum?",
                 "fr": "Vous avez changé {f} — qu'est-ce qui vous a fait faire ça ?",
                 "es": "Cambió {f} — ¿qué le hizo decidir eso?",
                 "ru": "Вы поменяли {f} — почему?"},
    "hold": {"en": "You put {n} on hold — what made you do that? Who decides when it's released?",
             "de": "Sie haben das zurückgehalten — warum? Wer entscheidet über die Freigabe?",
             "fr": "Vous l'avez mis en attente — pourquoi ? Qui décide de le débloquer ?",
             "es": "Lo dejó en espera — ¿por qué? ¿Quién decide cuándo liberarlo?",
             "ru": "Вы поставили это на удержание — почему? Кто решает, когда его отпустить?"},
    "escalate": {"en": "You sent {n} for {appr} — what made you do that?",
                 "de": "Sie haben das zur Freigabe geschickt — warum?",
                 "fr": "Vous l'avez envoyé pour approbation — pourquoi ?",
                 "es": "Lo envió a aprobación — ¿por qué?",
                 "ru": "Вы отправили это на согласование — почему?"},
    "approve": {"en": "You approved {n} — what did you check before that?",
                "de": "Sie haben das freigegeben — was haben Sie vorher geprüft?",
                "fr": "Vous l'avez approuvé — qu'avez-vous vérifié avant ?",
                "es": "Lo aprobó — ¿qué comprobó antes?",
                "ru": "Вы это одобрили — что вы проверили перед этим?"},
    "reject": {"en": "You rejected {n} — what would you never let through?",
               "de": "Sie haben das abgelehnt — was würden Sie nie durchlassen?",
               "fr": "Vous l'avez rejeté — que ne laisseriez-vous jamais passer ?",
               "es": "Lo rechazó — ¿qué nunca dejaría pasar?",
               "ru": "Вы это отклонили — что вы никогда не пропустите?"},
    "undo": {"en": "You undid the {f} — slip, or on purpose?",
             "de": "Sie haben {f} rückgängig gemacht — Versehen oder Absicht?",
             "fr": "Vous avez annulé {f} — erreur ou exprès ?",
             "es": "Deshizo {f} — ¿error o a propósito?",
             "ru": "Вы отменили {f} — случайно или намеренно?"},
    "limit": {"en": "That one is close to {th} — is there a limit there? What happens above it?",
              "de": "Das liegt nahe bei {th} — gibt es da eine Grenze? Was passiert darüber?",
              "fr": "C'est proche de {th} — y a-t-il une limite ? Que se passe-t-il au-dessus ?",
              "es": "Está cerca de {th} — ¿hay un límite ahí? ¿Qué pasa por encima?",
              "ru": "Это около {th} — есть ли там граница? Что происходит выше?"},
    "submit_guard": {"en": "On invoices like this one, when would you stop and ask someone first?",
                     "de": "Bei Rechnungen wie dieser: Wann würden Sie zuerst jemanden fragen?",
                     "fr": "Sur une facture comme celle-ci, quand demanderiez-vous d'abord à quelqu'un ?",
                     "es": "En facturas como esta, ¿cuándo preguntaría primero a alguien?",
                     "ru": "В таких счетах — когда бы вы сначала спросили кого-то?"},
    "generic": {"en": "You did something on {n} just now — what made you do that?",
                "de": "Sie haben da gerade etwas gemacht — warum?",
                "fr": "Vous venez de faire quelque chose — pourquoi ?",
                "es": "Acaba de hacer algo ahí — ¿por qué?",
                "ru": "Вы только что что-то сделали — почему?"},
    "confirm": {"en": "{a} — is it because {h}?",
                "de": "{a} — weil {h}?",
                "fr": "{a} — est-ce parce que {h} ?",
                "es": "{a} — ¿es porque {h}?",
                "ru": "{a} — это потому, что {h}?"},
}

# action clause (no question) per kind — leads hypothesis-confirm questions
ACTION_CLAUSES: dict[str, dict[str, str]] = {
    "edit": {"en": "You changed the {f} to {v}", "de": "Sie haben {f} auf {v} geändert",
             "fr": "Vous avez changé {f} en {v}", "es": "Cambió {f} a {v}", "ru": "Вы поменяли {f} на {v}"},
    "edit_nov": {"en": "You changed the {f}", "de": "Sie haben {f} geändert", "fr": "Vous avez changé {f}",
                 "es": "Cambió {f}", "ru": "Вы поменяли {f}"},
    "hold": {"en": "You put {n} on hold", "de": "Sie haben das zurückgehalten", "fr": "Vous l'avez mis en attente",
             "es": "Lo dejó en espera", "ru": "Вы поставили это на удержание"},
    "escalate": {"en": "You sent {n} for {appr}", "de": "Sie haben das zur Freigabe geschickt",
                 "fr": "Vous l'avez envoyé pour approbation", "es": "Lo envió a aprobación",
                 "ru": "Вы отправили это на согласование"},
}

APPROVAL_WORDS = {"plain": "approval", "second": "a second approval"}

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
        s = s.replace("{" + k + "}", _short(v, 90 if k in ("h", "a") else 28 if k != "appr" else 40))
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
    """Spoken text: cut long values at a word boundary (never a mid-word cut or an ellipsis glyph)."""
    s = str(v if v is not None else "").strip() or "this"
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return cut if len(cut) >= 4 else s[:n]


def clamp_words(s: str, n: int = 15) -> str:
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]).rstrip(",;:") + "?"

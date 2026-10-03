"""PII detection on OCR text lines.

Layer 1 (always, ~0.1 ms/line): regexes with checksums — emails, phones, IBAN (mod-97),
payment cards (Luhn), RU INN/SNILS, URLs with tokens, plus an LLM-free person-name heuristic
(honorifics in en/de/fr/es/ru, Cyrillic surname/patronymic morphology, "Name:" style labels).
Layer 2 (if available): GLiNER2 `fastino/gliner2-privacy-filter-PII-multi` run locally
(officially en/fr/es/de/it/pt/nl; Russian works empirically in our tests but the regex layer
remains the safety net). Loaded in a background thread; until ready only layer 1 runs.
Results are cached per line text, so unchanged lines cost nothing.

Env: CLAROS_PII_MODEL (default fastino/gliner2-privacy-filter-PII-multi, "off" disables),
CLAROS_PII_THRESHOLD (default 0.5; person uses max(0.6, threshold)).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from typing import Optional

log = logging.getLogger("claros.perception.pii")

Span = tuple[int, int, str]  # start, end, label

EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", re.U)
PHONE = re.compile(r"(?<![\w/.,])(?:\+|00)?\d[\d \-(). ]{7,}\d(?![\w/])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")
INN_SNILS = re.compile(r"(?i)(?:ИНН|СНИЛС|INN|SNILS|Steuer-?ID|NIF|NIE|DNI|SSN|NIR|Sozialversicherungs\w*)"
                       r"\s*[:#№]?\s*([\d \-]{9,16}\d)")
SECRET = re.compile(r"(?i)\b(?:api[_\- ]?key|token|secret|password|passwort|mot de passe|contraseña|пароль)"
                    r"\s*[:=]\s*(\S+)")
NAME_LABEL = re.compile(
    r"(?i)^\s*(?:contact(?: person)?|full name|first name|last name|employee|customer name|kontakt(?:person)?|"
    r"ansprechpartner(?:in)?|vorname|nachname|nom|prénom|contacto|nombre completo|apellidos?|"
    r"контакт(?:ное лицо)?|ф\.?и\.?о\.?|имя|фамилия|сотрудник)\s*[:\-]\s*(.+)$")
HONORIFIC = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Herr|Frau|M|Mme|Mlle|Sr|Sra|Srta|Don|Doña|г-н|г-жа|господин|госпожа)\.?\s+"
    r"((?:[A-ZÀ-ÖØ-ÞА-ЯЁ][\w'\-]+)(?:\s+[A-ZÀ-ÖØ-ÞА-ЯЁ][\w'\-]+){0,2})", re.U)
CYR_NAME = re.compile(r"\b([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)(?:\s+([А-ЯЁ][а-яё]+|[А-ЯЁ]\.\s?[А-ЯЁ]\.?)){1,2}", re.U)
_CYR_SURNAME = re.compile(r"(?:ов|ев|ёв|ин|ын|ский|цкий|ова|ева|ёва|ина|ына|ская|цкая|ко|енко|ук|юк)$")
_CYR_PATRONYMIC = re.compile(r"(?:ович|евич|ич|овна|евна|ична|инична)$")
_CYR_FIRST = set("""александр алексей андрей антон артём артем борис вадим валентин василий виктор виталий
владимир владислав вячеслав геннадий георгий глеб григорий даниил денис дмитрий евгений егор иван игорь
илья кирилл константин лев леонид максим михаил никита николай олег павел пётр петр роман руслан сергей
станислав степан тимур фёдор федор юрий ярослав анастасия анна валентина валерия вера виктория галина дарья
евгения екатерина елена елизавета ирина ксения любовь людмила маргарита марина мария надежда наталья
наталия нина ольга полина светлана софия софья татьяна юлия""".split())

DEFAULT_LABELS = ["person", "email", "phone_number", "address", "iban", "bank_account",
                  "credit_card_number", "tax_id", "national_id", "passport_number", "date_of_birth",
                  "api_key", "password"]


def _luhn(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return s % 10 == 0


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if not (15 <= len(s) <= 34):
        return False
    r = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in r)) % 97 == 1
    except ValueError:
        return False


def _looks_like_amount_or_date(s: str) -> bool:
    t = s.strip()
    return bool(re.fullmatch(r"[\d  .,]+(?:[.,]\d{2})", t) or re.fullmatch(r"\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}", t)
                or re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", t))


def regex_spans(text: str) -> list[Span]:
    out: list[Span] = []
    for m in EMAIL.finditer(text):
        out.append((m.start(), m.end(), "email"))
    for m in IBAN.finditer(text):
        if _iban_ok(m.group(0)):
            out.append((m.start(), m.end(), "iban"))
    for m in CARD.finditer(text):
        d = re.sub(r"\D", "", m.group(0))
        if 13 <= len(d) <= 19 and _luhn(d) and not _overlaps(out, m.start(), m.end()):
            out.append((m.start(), m.end(), "credit_card_number"))
    for m in PHONE.finditer(text):
        g = m.group(0)
        d = re.sub(r"\D", "", g)
        if 9 <= len(d) <= 15 and not _overlaps(out, m.start(), m.end()) and not _looks_like_amount_or_date(g) \
                and (g.lstrip().startswith(("+", "00", "(")) or re.search(r"\d[ \-]\d", g)):
            out.append((m.start(), m.end(), "phone_number"))
    for m in INN_SNILS.finditer(text):
        out.append((m.start(1), m.end(1), "tax_id"))
    for m in SECRET.finditer(text):
        out.append((m.start(1), m.end(1), "password"))
    m = NAME_LABEL.match(text)
    if m and re.search(r"[^\W\d_]", m.group(1)):
        out.append((m.start(1), m.end(1), "person"))
    for m in HONORIFIC.finditer(text):
        out.append((m.start(1), m.end(1), "person"))
    for m in CYR_NAME.finditer(text):
        words = re.findall(r"[А-ЯЁ][а-яё]+", m.group(0))
        if any(_CYR_SURNAME.search(w.lower()) for w in words) or any(_CYR_PATRONYMIC.search(w) for w in words) \
                or any(w.lower() in _CYR_FIRST for w in words):
            out.append((m.start(), m.end(), "person"))
    return _dedupe(out)


def _overlaps(spans: list[Span], s: int, e: int) -> bool:
    return any(not (e <= a or b <= s) for a, b, _ in spans)


def _dedupe(spans: list[Span]) -> list[Span]:
    spans = sorted(set(spans))
    out: list[Span] = []
    for s in spans:
        if out and s[0] < out[-1][1]:
            prev = out[-1]
            out[-1] = (prev[0], max(prev[1], s[1]), prev[2])
        else:
            out.append(s)
    return out


class _Gliner:
    def __init__(self) -> None:
        self.model = None
        self.state = "idle"  # idle | loading | ready | unavailable
        self._lock = threading.Lock()
        self.name = os.getenv("CLAROS_PII_MODEL", "fastino/gliner2-privacy-filter-PII-multi")
        self.threshold = float(os.getenv("CLAROS_PII_THRESHOLD", "0.5"))

    def load_async(self) -> None:
        if self.name.lower() in ("off", "none", "") or self.state != "idle":
            if self.name.lower() in ("off", "none", ""):
                self.state = "unavailable"
            return
        self.state = "loading"
        threading.Thread(target=self._load, name="gliner2-load", daemon=True).start()

    def _load(self) -> None:
        try:
            from gliner2 import GLiNER2  # type: ignore
            m = GLiNER2.from_pretrained(self.name)
            self.model = m
            self.state = "ready"
            log.info("GLiNER2 PII model ready: %s", self.name)
        except Exception as e:  # noqa: BLE001
            self.state = "unavailable"
            log.warning("GLiNER2 unavailable (%s); regex PII only", e)

    def spans(self, lines: list[str]) -> Optional[list[list[Span]]]:
        if self.state != "ready" or not lines:
            return None
        joined, offs, pos = "", [], 0
        for ln in lines:
            offs.append(pos)
            joined += ln + "\n"
            pos += len(ln) + 1
        try:
            with self._lock:
                r = self.model.extract_entities(joined, DEFAULT_LABELS, threshold=self.threshold,
                                                include_confidence=True, include_spans=True)
        except Exception as e:  # noqa: BLE001
            log.warning("GLiNER2 failed: %s", e)
            return None
        per: list[list[Span]] = [[] for _ in lines]
        ents = (r or {}).get("entities", {}) if isinstance(r, dict) else {}
        for label, items in ents.items():
            for it in items or []:
                if not isinstance(it, dict) or "start" not in it:
                    continue
                if label == "person" and it.get("confidence", 1.0) < max(0.6, self.threshold):
                    continue
                s, e = int(it["start"]), int(it["end"])
                for i in range(len(lines) - 1, -1, -1):
                    if s >= offs[i]:
                        a, b = s - offs[i], min(e - offs[i], len(lines[i]))
                        if b > a:
                            per[i].append((a, b, label))
                        break
        return per


GLINER = _Gliner()
_CACHE: "OrderedDict[str, list[Span]]" = OrderedDict()
_CACHE_MAX = 5000


def detect(lines: list[str]) -> list[list[Span]]:
    """PII spans per line. Uncached lines go through regex + GLiNER2 (one batched call)."""
    result: list[Optional[list[Span]]] = [None] * len(lines)
    todo = []
    for i, ln in enumerate(lines):
        c = _CACHE.get(ln)
        if c is not None:
            result[i] = c
            _CACHE.move_to_end(ln)
        else:
            todo.append(i)
    if todo:
        texts = [lines[i] for i in todo]
        model = GLINER.spans(texts)
        for k, i in enumerate(todo):
            spans = regex_spans(texts[k])
            if model is not None:
                spans = _dedupe(spans + model[k])
            result[i] = spans
            if model is not None or GLINER.state == "unavailable":
                _CACHE[texts[k]] = spans  # don't cache regex-only results while model is loading
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return [r or [] for r in result]

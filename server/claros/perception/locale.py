"""Locale-aware normalization of on-screen values (numbers, currency, dates, percents) and a
cheap UI-language guess. Languages: en, de, fr, es, ru (others fall back to en rules).

    normalize("12.400,00 €", "de")  -> {"value": 12400.0, "currency": "EUR"}
    normalize("12 400,00 ₽", "ru")  -> {"value": 12400.0, "currency": "RUB"}
    normalize("1,234.56", "en")     -> 1234.56
    normalize("03.10.2026", "de")   -> "2026-10-03"
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

CURRENCY_SYMBOLS = {
    "€": "EUR", "$": "USD", "£": "GBP", "₽": "RUB", "¥": "JPY", "₹": "INR", "₴": "UAH", "₸": "KZT",
    "CHF": "CHF", "руб.": "RUB", "руб": "RUB", "р.": "RUB", "US$": "USD", "Fr.": "CHF",
}
ISO_CODES = {"EUR", "USD", "GBP", "RUB", "CHF", "JPY", "INR", "UAH", "KZT", "PLN", "CZK", "SEK",
             "NOK", "DKK", "CAD", "AUD", "CNY", "TRY", "BYN", "MXN", "BRL", "ARS", "COP", "CLP"}

_SPACES = "     '’"
_CUR_RE = re.compile(
    r"(US\$|руб\.?|р\.|Fr\.|CHF|[€$£₽¥₹₴₸]|\b(?:%s)\b)" % "|".join(sorted(ISO_CODES)), re.I)
_NUM_CORE = re.compile(r"^[+\-−(]?\d[\d" + _SPACES + r".,]*\d?[)\-−]?$|^[+\-−(]?\d[)]?$")

MONTHS: dict[str, int] = {}
for _i, _names in enumerate([
    "jan janv ene янв январ enero january januar janvier",
    "feb febr fév fev фев феврал february februar février febrero",
    "mar mär märz mars marzo мар март march",
    "apr avr abr апр апрел april avril abril",
    "may mai mayo ма май мая",
    "jun juin junio июн june juni",
    "jul juil julio июл july juli juillet",
    "aug août aout ago агу авг august agosto",
    "sep sept septiembre setiembre сен сент september septembre",
    "oct okt octobre octubre окт october oktober",
    "nov noviembre ноя нояб november novembre",
    "dec dez déc dic дек december dezember décembre diciembre",
], start=1):
    for _n in _names.split():
        MONTHS[_n] = _i


def _month(tok: str) -> Optional[int]:
    t = tok.lower().strip(".,")
    if not t:
        return None
    if t in MONTHS:
        return MONTHS[t]
    for k in (4, 3):
        if len(t) >= k and t[:k] in MONTHS:
            return MONTHS[t[:k]]
    return None


def parse_number(s: str, lang: str = "en") -> Optional[float]:
    """Parse a locale-formatted number. Returns None if `s` is not purely numeric."""
    t = s.strip()
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t[:1] in "-−":
        neg, t = True, t[1:]
    elif t[-1:] in "-−":
        neg, t = True, t[:-1]
    t = t.strip("+ ")
    if not t or not _NUM_CORE.match(t):
        return None
    for ch in _SPACES:
        t = t.replace(ch, "")
    if not re.fullmatch(r"[\d.,]+", t) or not t[0].isdigit():
        return None
    dots, commas = t.count("."), t.count(",")
    if dots and commas:
        dec = "." if t.rfind(".") > t.rfind(",") else ","
        thou = "," if dec == "." else "."
        t = t.replace(thou, "").replace(dec, ".")
    elif dots or commas:
        sep = "." if dots else ","
        n = dots or commas
        tail = t.rsplit(sep, 1)[1]
        if n > 1:
            groups = t.split(sep)
            if all(len(g) == 3 for g in groups[1:]):
                t = t.replace(sep, "")
            else:
                return None
        elif len(tail) == 3:
            # ambiguous "1.234" / "1,234": thousands unless the language uses sep as decimal
            decimal_sep = "." if lang == "en" else ","
            t = t.replace(sep, ".") if sep == decimal_sep else t.replace(sep, "")
        else:
            t = t.replace(sep, ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def parse_money(s: str, lang: str = "en") -> Optional[dict]:
    m = _CUR_RE.search(s)
    if not m:
        return None
    sym = m.group(1)
    cur = CURRENCY_SYMBOLS.get(sym) or CURRENCY_SYMBOLS.get(sym.lower()) or sym.upper()
    rest = (s[:m.start()] + " " + s[m.end():]).strip()
    v = parse_number(rest, lang)
    if v is None:
        return None
    return {"value": v, "currency": cur}


def parse_date(s: str, lang: str = "en") -> Optional[str]:
    t = s.strip().rstrip(".")
    t = re.sub(r"\s*(г\.|года|г)$", "", t)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})([./\-])(\d{1,2})\2(\d{2,4})", t)
    if m:
        a, sep, b, y = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
        if y < 100:
            y += 2000
        if lang == "en" and sep == "/":
            mo, d = a, b  # US m/d/y
        else:
            d, mo = a, b
        if mo > 12 and d <= 12:
            d, mo = mo, d
        return _iso(y, mo, d)
    toks = [x for x in re.split(r"[\s,]+", t) if x and x.lower() not in ("de", "del", "of", "the")]
    if 2 <= len(toks) <= 4:
        nums = [x.strip(".") for x in toks if x.strip(".").isdigit()]
        mons = [_month(x) for x in toks if not x.strip(".").isdigit()]
        mons = [x for x in mons if x]
        if len(mons) == 1 and len(nums) == 2:
            a, b = int(nums[0]), int(nums[1])
            y, d = (a, b) if a > 31 else (b, a)
            return _iso(y if y > 99 else 2000 + y, mons[0], d)
    return None


def _iso(y: int, mo: int, d: int) -> Optional[str]:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_percent(s: str, lang: str = "en") -> Optional[dict]:
    m = re.fullmatch(r"\s*(.+?)\s*%\s*", s)
    if not m:
        return None
    v = parse_number(m.group(1), lang)
    return None if v is None else {"value": v, "unit": "%"}


def normalize(value: Optional[str], lang: Optional[str] = "en") -> Any:
    """Best-effort locale normalization. Returns None for plain text."""
    if value is None:
        return None
    s = value.strip()
    if not s or len(s) > 60:
        return None
    lang = (lang or "en")[:2].lower()
    for f in (parse_date, parse_percent, parse_money):
        r = f(s, lang)
        if r is not None:
            return r
    return parse_number(s, lang)


# ---------------- language guess ----------------

_LANG_WORDS = {
    "en": "the and of to save new draft invoice status date total amount supplier submit cancel "
          "settings search details name type due paid",
    "de": "der die das und für mit speichern neu entwurf rechnung datum betrag gesamt lieferant "
          "buchen abbrechen einstellungen suche kostenstelle fällig bezahlt nicht",
    "fr": "le la les des et pour avec enregistrer nouveau brouillon facture date montant total "
          "fournisseur valider annuler paramètres recherche échéance payé du",
    "es": "el la los las y para con guardar nuevo borrador factura fecha importe total proveedor "
          "enviar cancelar configuración buscar vencimiento pagado del",
}
_LANG_SETS = {k: set(v.split()) for k, v in _LANG_WORDS.items()}


def guess_lang(texts: list[str]) -> Optional[str]:
    joined = " ".join(texts)
    letters = [c for c in joined if c.isalpha()]
    if not letters:
        return None
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    if cyr / len(letters) > 0.3:
        return "ru"
    words = re.findall(r"\w+", joined.lower())
    scores = {k: sum(1 for w in words if w in s) for k, s in _LANG_SETS.items()}
    if any(c in joined for c in "äöüßÄÖÜ"):
        scores["de"] += 2
    if any(c in joined for c in "ñ¿¡"):
        scores["es"] += 2
    if any(c in joined for c in "çèêàù"):
        scores["fr"] += 2
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"

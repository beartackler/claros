"""Shared helpers for the Claros eval suite (infra/evals).

- paths + result IO (data/evals/results/<phase>[.<tag>].json)
- metric helpers (CER, normalization, percentiles, P/R/F1)
- an independent LLM judge (NOT the GLM family Claros itself uses): Gemini via OpenRouter if a key exists,
  else Cloudflare Workers AI (Llama 3.3 70B), else None → callers must mark results "unjudged".

Never prints keys.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
EVAL_DATA = ROOT / "data" / "evals"
RESULTS = EVAL_DATA / "results"
GOLDEN = EVAL_DATA / "golden"
API = os.getenv("CLAROS_EVAL_API", "http://localhost:8789")
os.environ.setdefault("CLAROS_E2E_API", API)  # infra/e2e/claros_client reads this at import

sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "infra" / "e2e"))


def load_env() -> None:
    """Load repo-root .env into os.environ (without overriding)."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def log(*a: Any) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ---------------- results IO ----------------

def save_result(phase: str, data: dict, tag: Optional[str] = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / (f"{phase}.{tag}.json" if tag else f"{phase}.json")
    data = {"phase": phase, "tag": tag, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **data}
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    return p


def load_result(phase: str, tag: Optional[str] = None) -> Optional[dict]:
    p = RESULTS / (f"{phase}.{tag}.json" if tag else f"{phase}.json")
    return json.loads(p.read_text()) if p.exists() else None


# ---------------- metrics ----------------

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(truth: str, pred: str) -> float:
    return levenshtein(truth, pred) / max(1, len(truth))


def best_substring_cer(truth: str, texts: Iterable[str]) -> tuple[float, str]:
    """Min CER of `truth` against any whitespace token window in `texts` (OCR lines)."""
    best, best_s = 1.0, ""
    n = len(truth.split())
    for t in texts:
        toks = t.split()
        for w in range(max(1, n - 1), n + 2):
            for i in range(0, max(1, len(toks) - w + 1)):
                s = " ".join(toks[i:i + w])
                c = cer(truth, s)
                if c < best:
                    best, best_s = c, s
        # also raw substring scan for glued tokens
        if truth in t:
            return 0.0, truth
    return round(best, 4), best_s


def pct(xs: list[float], q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 1)


def mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _r(p), "recall": _r(r), "f1": _r(f)}


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 3)


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """95% Wilson interval for a proportion (small-n honesty)."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)


# ---------------- normalization ----------------

def norm_text(s: Any) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = re.sub(r"[\s ]+", " ", s)
    return s.strip(" .:;,*")


def parse_num(s: Any) -> Optional[float]:
    """Locale-tolerant number: 8.400,00 / 8,400.00 / 8 400,00 / € 8,400.00 / 8400 → 8400.0"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = re.sub(r"[^\d,.\-\s ']", "", str(s)).strip()
    t = re.sub(r"[\s ']", "", t)
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        t = t.replace(",", ".") if len(parts[-1]) in (1, 2) and len(parts) == 2 else t.replace(",", "")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[-1]) == 3 and len(t.split(".")[0]) <= 3
                              and len(t) > 4):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


_DATE = [r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})", r"(?P<d>\d{1,2})[-./](?P<m>\d{1,2})[-./](?P<y>\d{4})"]


def parse_date(s: Any) -> Optional[str]:
    for p in _DATE:
        m = re.search(p, str(s or ""))
        if m:
            return f"{int(m['y']):04d}-{int(m['m']):02d}-{int(m['d']):02d}"
    return None


def value_match(truth: Any, pred: Any) -> tuple[bool, bool]:
    """(exact, lenient). exact = equal after locale normalization; lenient = one contains the other / OCR-ish."""
    if pred is None or str(pred).strip() == "":
        return False, False
    td, pd = parse_date(truth), parse_date(pred)
    if td:
        return td == pd, td == pd
    tn = parse_num(truth) if isinstance(truth, (int, float)) or re.fullmatch(r"[\d.,\s€£$-]+", str(truth)) else None
    if tn is not None:
        pn = parse_num(pred)
        ok = pn is not None and abs(pn - tn) < 0.005
        return ok, ok
    a, b = norm_text(truth), norm_text(pred)
    if a == b:
        return True, True
    a2 = re.sub(r"\s+-\s+\w{2,6}$", "", a)  # "Tools and Small Equipment - OPP" vs without company abbr
    b2 = re.sub(r"\s+-\s+\w{2,6}$", "", b)
    lenient = bool(a2 and b2) and (a2 == b2 or a2 in b or b2 in a or levenshtein(a2, b2) <= max(1, len(a2) // 10))
    return False, lenient


# ---------------- independent judge ----------------

class Judge:
    """Different model family than the system under test (GLM). Returns parsed JSON or None."""

    def __init__(self) -> None:
        self.provider = None
        if os.getenv("OPENROUTER_API_KEY"):
            self.provider = ("openrouter", "google/gemini-2.5-flash")
        elif os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID"):
            self.provider = ("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        self.calls = 0
        self.failures = 0

    @property
    def name(self) -> str:
        return f"{self.provider[0]}:{self.provider[1]}" if self.provider else "unjudged"

    def ask(self, system: str, user: str, max_tokens: int = 400) -> Optional[dict]:
        if not self.provider:
            return None
        import httpx
        kind, model = self.provider
        msgs = [{"role": "system", "content": system + "\nReply with ONE JSON object only."},
                {"role": "user", "content": user}]
        for attempt in range(3):
            try:
                self.calls += 1
                if kind == "openrouter":
                    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=60,
                                   headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                                   json={"model": model, "messages": msgs, "max_tokens": max_tokens,
                                         "temperature": 0, "response_format": {"type": "json_object"}})
                    txt = r.json()["choices"][0]["message"]["content"]
                else:
                    acc = os.environ["CLOUDFLARE_ACCOUNT_ID"]
                    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}", timeout=60,
                                   headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"},
                                   json={"messages": msgs, "max_tokens": max_tokens, "temperature": 0})
                    res = r.json().get("result") or {}
                    txt = res.get("response")
                    if isinstance(txt, dict):
                        return txt
                    if txt is None and res.get("choices"):
                        txt = res["choices"][0]["message"]["content"]
                return _json_of(txt)
            except Exception:  # noqa: BLE001
                self.failures += 1
                time.sleep(1.5 * (attempt + 1))
        return None


def _json_of(txt: Any) -> Optional[dict]:
    if isinstance(txt, dict):
        return txt
    if not txt:
        return None
    m = re.search(r"\{.*\}", str(txt), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

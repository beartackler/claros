"""O*NET task prior: local SQLite FTS5 (BM25) over task statements + occupation titles.

No external API needed. Optional Jina rerank (JINA_API_KEY) and query translation.

    match("process supplier invoices in ERP and code to cost centers") -> list[OnetMatch]
    occupation_profile("43-3031.00") -> {code, title, description, tasks, dwas, technologies}

Data: O*NET 31.0 Database, USDOL/ETA, CC BY 4.0 (see onet_fetch.py).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from claros.models import OnetMatch

from . import onet_fetch

log = logging.getLogger("claros.context.onet")

JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_RERANK_MODEL = os.environ.get("JINA_RERANK_MODEL", "jina-reranker-v3.5")
JINA_RERANK_FALLBACK_MODEL = "jina-reranker-v2-base-multilingual"

Translate = Callable[[str], str]

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[Path] = None

STOP = set(
    """a an and are as at be by for from has have in into is it its of on or that the this to was
    were will with we our i you they them their my me do does did how what when where which who why
    can should would could then than so if not no yes all any each per via up out over""".split()
)

# small domain expansions (English) — appended as OR terms
SYNONYMS: dict[str, str] = {
    "aml": "money laundering compliance suspicious transactions",
    "erp": "enterprise resource planning accounting software",
    "ap": "accounts payable",
    "ar": "accounts receivable",
    "invoice": "invoices bills",
    "invoices": "invoice bills",
    "supplier": "vendor suppliers vendors",
    "suppliers": "vendor vendors",
    "vendor": "supplier suppliers",
    "ticket": "inquiries problems requests users customers",
    "tickets": "inquiries problems requests users customers",
    "support": "help users customers inquiries technical",
    "escalate": "refer complex problems supervisors",
    "tier": "refer complex problems",
    "kyc": "verify identity customer accounts applications information compliance fraud",
    "cost": "expenses accounts",
    "center": "accounts",
    "centers": "accounts",
    "code": "classify accounts",
    "capex": "capital expenditure assets",
    "po": "purchase order",
    "gl": "general ledger",
    "reconcile": "reconciliation balance accounts",
    "onboarding": "orientation new employees",
    "refund": "refunds adjust accounts customer",
}

# multilingual glossary: stem prefix -> English terms (offline fallback when no translator)
GLOSSARY: dict[str, str] = {
    # ru
    "счет": "invoice bill account", "счёт": "invoice bill account", "сч": "invoice",
    "фактур": "invoice", "накладн": "invoice delivery", "поставщик": "supplier vendor",
    "обработ": "process", "оплат": "payment pay", "платеж": "payment", "платёж": "payment",
    "заявк": "request ticket", "тикет": "ticket", "обращени": "customer inquiry complaint",
    "клиент": "customer client", "документ": "documents", "провер": "verify check review",
    "бухгалт": "bookkeeping accounting", "учет": "accounting records", "учёт": "accounting records",
    "закуп": "purchasing procurement", "эскалац": "escalate refer", "поддержк": "support",
    "затрат": "cost expense", "расход": "expense cost", "договор": "contract",
    "кредитор": "accounts payable", "дебитор": "accounts receivable", "проводк": "post journal entry",
    "сверк": "reconcile", "налог": "tax", "зарплат": "payroll", "сотрудник": "employee",
    "заказ": "order", "товар": "goods merchandise", "склад": "warehouse inventory",
    "идентификац": "identity verification", "личност": "identity", "паспорт": "passport identity",
    "банк": "bank", "кредит": "loan credit", "страхов": "insurance", "жалоб": "complaint",
    # de
    "rechnung": "invoice", "lieferant": "supplier vendor", "kostenstelle": "cost center",
    "buchung": "posting journal entry", "buchhalt": "bookkeeping accounting", "zahlung": "payment",
    "bestellung": "purchase order", "kunde": "customer", "prüf": "verify check", "pruef": "verify check",
    # es / fr
    "factura": "invoice", "proveedor": "supplier vendor", "pago": "payment", "cliente": "customer",
    "facture": "invoice", "fournisseur": "supplier vendor", "paiement": "payment",
    "comptab": "accounting bookkeeping", "contabil": "accounting bookkeeping",
}


# ---------------------------------------------------------------- index

def index_path() -> Path:
    return onet_fetch.onet_dir() / "onet_index.sqlite"


def _read_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def build_index(force: bool = False, src: Path | None = None, dest: Path | None = None) -> Path:
    """Build the SQLite index from CSVs. Idempotent unless force."""
    src = src or onet_fetch.csv_dir()
    dest = dest or index_path()
    if dest.exists() and not force:
        return dest
    if not onet_fetch.have_csvs(src):
        raise FileNotFoundError(f"O*NET CSVs missing in {src}; run python -m claros.context.onet_fetch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    c = sqlite3.connect(tmp)
    c.executescript(
        """
        CREATE TABLE occ(code TEXT PRIMARY KEY, title TEXT, description TEXT);
        CREATE TABLE task(task_id TEXT PRIMARY KEY, code TEXT, task TEXT, type TEXT);
        CREATE TABLE task_dwa(task_id TEXT, dwa_id TEXT, dwa TEXT);
        CREATE TABLE dwa_ref(dwa_id TEXT PRIMARY KEY, dwa TEXT, iwa TEXT, gwa TEXT);
        CREATE TABLE tech(code TEXT, example TEXT, category TEXT, hot INTEGER);
        CREATE VIRTUAL TABLE fts USING fts5(kind UNINDEXED, code UNINDEXED, task_id UNINDEXED,
            title, body, tokenize='porter unicode61 remove_diacritics 2');
        """
    )
    occ: dict[str, tuple[str, str]] = {}
    for r in _read_csv(src / "occupation_data.csv"):
        occ[r["O*NET-SOC Code"]] = (r["Title"], r["Description"])
    c.executemany("INSERT INTO occ VALUES(?,?,?)", [(k, t, d) for k, (t, d) in occ.items()])

    dwas_by_task: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in _read_csv(src / "tasks_to_dwas.csv"):
        dwas_by_task[r["Task ID"]].append((r["DWA Element ID"], r["DWA Element Name"]))
    c.executemany(
        "INSERT INTO task_dwa VALUES(?,?,?)",
        [(t, i, n) for t, lst in dwas_by_task.items() for i, n in lst],
    )
    ref = src / "gwas_to_iwas_to_dwas.csv"
    if ref.exists():
        rows = {}
        for r in _read_csv(ref):
            rows[r["DWA Element ID"]] = (r["DWA Element ID"], r["DWA Element Name"], r["IWA Element Name"], r["GWA Element Name"])
        c.executemany("INSERT INTO dwa_ref VALUES(?,?,?,?)", list(rows.values()))

    tasks = []
    fts = []
    for r in _read_csv(src / "task_statements.csv"):
        code, tid, text = r["O*NET-SOC Code"], r["Task ID"], r["Task"]
        tasks.append((tid, code, text, r.get("Task Type") or ""))
        dw = " ".join(n for _, n in dwas_by_task.get(tid, []))
        fts.append(("task", code, tid, occ.get(code, (r["Title"], ""))[0], f"{text} {dw}"))

    alt: dict[str, list[str]] = defaultdict(list)
    for name in ("sample_of_reported_titles.csv", "job_titles.csv"):
        p = src / name
        if p.exists():
            col = "Reported Job Title" if "sample" in name else "Job Title"
            for r in _read_csv(p):
                if len(alt[r["O*NET-SOC Code"]]) < 60:
                    alt[r["O*NET-SOC Code"]].append(r[col])
    for code, (title, desc) in occ.items():
        fts.append(("occ", code, None, title, f"{desc} {' '.join(alt.get(code, []))}"))
    c.executemany("INSERT INTO task VALUES(?,?,?,?)", tasks)
    c.executemany("INSERT INTO fts VALUES(?,?,?,?,?)", fts)

    sw = src / "software_skills.csv"
    if sw.exists():
        c.executemany(
            "INSERT INTO tech VALUES(?,?,?,?)",
            [
                (r["O*NET-SOC Code"], r["Workplace Example"], r["Element Name"], 1 if r.get("Hot Technology") == "Y" else 0)
                for r in _read_csv(sw)
            ],
        )
    c.executescript(
        "CREATE INDEX i_task_code ON task(code); CREATE INDEX i_td ON task_dwa(task_id);"
        "CREATE INDEX i_tech ON tech(code);"
    )
    c.commit()
    c.close()
    os.replace(tmp, dest)
    return dest


def _db() -> sqlite3.Connection:
    global _conn, _conn_path
    p = index_path()
    with _lock:
        if _conn is not None and _conn_path == p:
            return _conn
        if not p.exists():
            if not onet_fetch.have_csvs():
                onet_fetch.fetch()
            build_index()
        _conn = sqlite3.connect(p, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn_path = p
        return _conn


def reset() -> None:
    """Drop cached connection (tests / env switches)."""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _conn_path = None, None


def available() -> bool:
    return index_path().exists() or onet_fetch.have_csvs()


# ---------------------------------------------------------------- query

def is_non_english(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for ch in letters if ord(ch) > 127)
    return non_ascii / len(letters) > 0.3


def glossary_translate(text: str) -> str:
    out = []
    for tok in re.findall(r"\w+", text.lower()):
        for stem, eng in GLOSSARY.items():
            if len(stem) >= 3 and tok.startswith(stem):
                out.append(eng)
                break
    return " ".join(out)


def _terms(text: str) -> list[tuple[str, float]]:
    """Query terms with weights: original tokens 1.0, synonym/glossary expansions 0.6."""
    toks = [t for t in re.findall(r"\w+", text.lower()) if t not in STOP and (len(t) > 1 or t.isdigit())]
    out: dict[str, float] = {}
    for t in toks:
        if not t.isdigit():
            out.setdefault(t, 1.0)
    for t in toks:
        for e in SYNONYMS.get(t, "").split():
            out.setdefault(e, 0.6)
    for e in glossary_translate(text).split():
        out.setdefault(e, 0.9)
    return list(out.items())[:40]


def _stem(w: str) -> str:
    return w[:5] if len(w) > 5 else w


def _fts_query(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in terms)


_df_cache: dict[str, int] = {}


def _df(conn: sqlite3.Connection, term: str) -> int:
    if term not in _df_cache:
        try:
            _df_cache[term] = conn.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", (f'"{term}"',)).fetchone()[0]
        except sqlite3.OperationalError:
            _df_cache[term] = 0
    return _df_cache[term]


def _technologies(conn: sqlite3.Connection, code: str, limit: int = 12) -> list[str]:
    rows = conn.execute(
        "SELECT example, category, hot FROM tech WHERE code=? ORDER BY hot DESC", (code,)
    ).fetchall()
    cats: list[str] = []
    hot: list[str] = []
    for r in rows:
        if r["category"] not in cats:
            cats.append(r["category"])
        if r["hot"] and len(hot) < 5:
            hot.append(r["example"])
    return (cats[: limit - len(hot)] + hot)[:limit]


def _task_dwas(conn: sqlite3.Connection, task_id: str) -> list[str]:
    return [r["dwa"] for r in conn.execute("SELECT dwa FROM task_dwa WHERE task_id=?", (task_id,))]


def _candidates(conn: sqlite3.Connection, q: str, per_occ: int = 2, limit_occ: int = 30) -> list[dict]:
    wterms = _terms(q)
    n_docs = conn.execute("SELECT count(*) FROM fts").fetchone()[0] or 1
    # drop terms O*NET never uses (jargon like "kyc"); idf-weight the rest
    weighted = []
    for t, w in wterms:
        df = _df(conn, t)
        if df:
            weighted.append((t, w * math.log(1 + n_docs / df)))
    if not weighted:
        return []
    fq = _fts_query([t for t, _ in weighted])
    total_w = sum(w for _, w in weighted)
    # bm25 weights: kind, code, task_id, title, body
    occ_rows = conn.execute(
        "SELECT code, bm25(fts,0,0,0,3.0,1.0) s FROM fts WHERE fts MATCH ? AND kind='occ' ORDER BY s LIMIT 60",
        (fq,),
    ).fetchall()
    task_rows = conn.execute(
        "SELECT code, task_id, title, body, bm25(fts,0,0,0,1.0,2.0) s FROM fts "
        "WHERE fts MATCH ? AND kind='task' ORDER BY s LIMIT 500",
        (fq,),
    ).fetchall()
    if not task_rows:
        return []
    occ_s = {r["code"]: -r["s"] for r in occ_rows}
    max_occ = max(occ_s.values(), default=1.0) or 1.0
    max_task = max(-r["s"] for r in task_rows) or 1.0
    hits_per_occ: dict[str, int] = defaultdict(int)
    for r in task_rows:
        hits_per_occ[r["code"]] += 1
    by_occ: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for r in task_rows:
        stems = {_stem(w) for w in re.findall(r"\w+", f"{r['title']} {r['body']}".lower())}
        cov = sum(w for t, w in weighted if _stem(t) in stems) / total_w
        s = (
            1.0 * cov
            + 0.35 * (-r["s"]) / max_task
            + 0.25 * occ_s.get(r["code"], 0.0) / max_occ
            + 0.1 * min(hits_per_occ[r["code"]], 6) / 6
        )
        by_occ[r["code"]].append((s, r["task_id"]))
    ranked = sorted(by_occ.items(), key=lambda kv: -max(x[0] for x in kv[1]))[:limit_occ]
    out = []
    for code, lst in ranked:
        lst.sort(key=lambda x: -x[0])
        for s, tid in lst[:per_occ]:
            t = conn.execute(
                "SELECT t.task, o.title FROM task t JOIN occ o ON o.code=t.code WHERE t.task_id=?", (tid,)
            ).fetchone()
            out.append({"code": code, "title": t["title"], "task_id": tid, "task": t["task"], "score": s})
    out.sort(key=lambda c: -c["score"])
    return out


def _jina_rerank(query: str, docs: list[str], top_n: int) -> Optional[list[tuple[int, float]]]:
    key = os.environ.get("JINA_API_KEY")
    if not key or not docs:
        return None
    for model in (JINA_RERANK_MODEL, JINA_RERANK_FALLBACK_MODEL):
        try:
            r = httpx.post(
                JINA_RERANK_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "query": query, "documents": docs, "top_n": top_n},
                timeout=15,
            )
            if r.status_code >= 400:
                log.warning("jina rerank %s -> %s %s", model, r.status_code, r.text[:200])
                continue
            return [(x["index"], float(x["relevance_score"])) for x in r.json()["results"]]
        except Exception as e:  # network etc.
            log.warning("jina rerank failed: %s", e)
            return None
    return None


def match(
    text: str,
    k: int = 5,
    *,
    translate: Optional[Translate] = None,
    rerank: bool = True,
) -> list[OnetMatch]:
    """Best O*NET task per occupation for a free-text description of work (any language)."""
    if not text or not text.strip():
        return []
    conn = _db()
    query = text
    if is_non_english(text) and translate is not None:
        try:
            query = f"{text} {translate(text)}"
        except Exception as e:
            log.warning("translate failed: %s", e)
    cands = _candidates(conn, query)
    if not cands:
        return []
    reranked = False
    if rerank:
        rr = _jina_rerank(text, [f"{c['title']}: {c['task']}" for c in cands], top_n=min(len(cands), k * 3))
        if rr:
            cands = [dict(cands[i], score=s) for i, s in rr]
            reranked = True
    # one per occupation, top k
    seen: set[str] = set()
    picked = []
    for c in cands:
        if c["code"] in seen:
            continue
        seen.add(c["code"])
        picked.append(c)
        if len(picked) >= k:
            break
    top = max((c["score"] for c in picked), default=1.0) or 1.0
    return [
        OnetMatch(
            occupation_code=c["code"],
            occupation_title=c["title"],
            task_id=c["task_id"],
            task=c["task"],
            dwas=_task_dwas(conn, c["task_id"]),
            technologies=_technologies(conn, c["code"]),
            score=round(c["score"] if reranked else c["score"] / top, 4),
        )
        for c in picked
    ]


async def amatch(text: str, k: int = 5, *, translate: Optional[Callable[[str], Any]] = None) -> list[OnetMatch]:
    """Async variant: translates non-English queries with the LLM (if available) first."""
    extra = ""
    if is_non_english(text):
        try:
            if translate is not None:
                res = translate(text)
                extra = await res if asyncio.iscoroutine(res) else res
            else:
                from ._llm import llm_text

                extra = await llm_text(
                    [
                        {"role": "system", "content": "Translate the user's text to English. Output only the translation."},
                        {"role": "user", "content": text},
                    ],
                    role="fast",
                ) or ""
        except Exception as e:
            log.warning("translate failed: %s", e)
    q = f"{text} {extra}".strip()
    return await asyncio.to_thread(match, q, k)


def occupation_profile(code: str, max_tasks: int = 40) -> Optional[dict[str, Any]]:
    conn = _db()
    o = conn.execute("SELECT * FROM occ WHERE code=?", (code,)).fetchone()
    if not o:
        return None
    tasks = []
    dwas: list[str] = []
    for t in conn.execute(
        "SELECT task_id, task, type FROM task WHERE code=? ORDER BY type='Core' DESC, CAST(task_id AS INT) LIMIT ?",
        (code, max_tasks),
    ):
        td = _task_dwas(conn, t["task_id"])
        tasks.append({"task_id": t["task_id"], "task": t["task"], "type": t["type"], "dwas": td})
        for d in td:
            if d not in dwas:
                dwas.append(d)
    tech: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT example, category, hot FROM tech WHERE code=? ORDER BY hot DESC", (code,)):
        e = tech.setdefault(r["category"], {"category": r["category"], "examples": [], "hot": []})
        if len(e["examples"]) < 8:
            e["examples"].append(r["example"])
        if r["hot"]:
            e["hot"].append(r["example"])
    return {
        "code": code,
        "title": o["title"],
        "description": o["description"],
        "tasks": tasks,
        "dwas": dwas,
        "technologies": list(tech.values()),
        "source": f"onet:{code}",
        "attribution": "O*NET 31.0 Database, USDOL/ETA, CC BY 4.0",
    }

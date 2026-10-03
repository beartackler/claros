"""claros.context.onet — offline tests on a tiny fixture DB (+ real-data checks when present)."""
from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest

from claros.context import onet, onet_fetch

OCC = [
    ("43-3031.00", "Bookkeeping, Accounting, and Auditing Clerks", "Compute, classify, and record numerical data to keep financial records complete."),
    ("15-1232.00", "Computer User Support Specialists", "Provide technical assistance to computer users. Answer questions or resolve computer problems."),
    ("43-4141.00", "New Accounts Clerks", "Interview persons desiring to open accounts in financial institutions."),
    ("35-2014.00", "Cooks, Restaurant", "Prepare, season, and cook dishes in restaurants."),
]
TASKS = [
    ("43-3031.00", "1001", "Process invoices from suppliers and code them to the correct accounts and cost centers."),
    ("43-3031.00", "1002", "Reconcile records of bank transactions."),
    ("15-1232.00", "2001", "Answer user inquiries and refer complex problems to senior support staff."),
    ("43-4141.00", "3001", "Verify customer identity documents when opening new accounts."),
    ("35-2014.00", "4001", "Season and cook food according to recipes."),
]
DWAS = [
    ("1001", "4.A.2.a.1.a.1", "Process invoices or payments."),
    ("1001", "4.A.2.a.1.a.2", "Code data or other information."),
    ("2001", "4.A.4.a.2.a.1", "Respond to customer problems or inquiries."),
    ("3001", "4.A.2.a.3.a.1", "Verify personal information."),
]
TECH = [
    ("43-3031.00", "Accounts payable software", "Accounting software", "N"),
    ("43-3031.00", "SAP software", "Enterprise resource planning ERP software", "Y"),
    ("15-1232.00", "ServiceNow", "Help desk or call center software", "Y"),
]


def _w(path: Path, header: list[str], rows: list[tuple]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_fixture(root: Path) -> Path:
    d = root / "csv"
    d.mkdir(parents=True, exist_ok=True)
    titles = {c: t for c, t, _ in OCC}
    _w(d / "occupation_data.csv", ["O*NET-SOC Code", "Title", "Description"], OCC)
    _w(d / "task_statements.csv",
       ["O*NET-SOC Code", "Title", "Task ID", "Task", "Task Type", "Incumbents Responding", "Date", "Domain Source"],
       [(c, titles[c], tid, t, "Core", 10, "08/2025", "Incumbent") for c, tid, t in TASKS])
    by_id = {tid: (c, t) for c, tid, t in TASKS}
    _w(d / "tasks_to_dwas.csv",
       ["O*NET-SOC Code", "Title", "Task ID", "Task", "DWA Element ID", "DWA Element Name", "Date", "Domain Source"],
       [(by_id[tid][0], titles[by_id[tid][0]], tid, by_id[tid][1], did, dn, "08/2025", "Analyst") for tid, did, dn in DWAS])
    _w(d / "gwas_to_iwas_to_dwas.csv",
       ["GWA Element ID", "GWA Element Name", "IWA Element ID", "IWA Element Name", "DWA Element ID", "DWA Element Name"],
       [("g", "Processing Information", "i", "Process data", did, dn) for _, did, dn in DWAS])
    _w(d / "software_skills.csv",
       ["O*NET-SOC Code", "Title", "Workplace Example", "Element ID", "Element Name", "Hot Technology", "In Demand"],
       [(c, titles[c], ex, "2.E", cat, hot, "N") for c, ex, cat, hot in TECH])
    _w(d / "job_titles.csv", ["O*NET-SOC Code", "Title", "Job Title", "Short Title", "Source(s)"],
       [("43-3031.00", titles["43-3031.00"], "Accounts Payable Clerk", "", "08"),
        ("15-1232.00", titles["15-1232.00"], "Help Desk Technician", "", "08")])
    return d


@pytest.fixture
def fixture_onet(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAROS_ONET_DIR", str(tmp_path / "onet"))
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    write_fixture(tmp_path / "onet")
    onet.reset()
    onet._df_cache.clear()
    onet.build_index(force=True)
    yield
    onet.reset()
    onet._df_cache.clear()


def test_build_index_idempotent(fixture_onet):
    p = onet.index_path()
    mtime = p.stat().st_mtime
    assert onet.build_index() == p and p.stat().st_mtime == mtime


def test_match_invoice(fixture_onet):
    ms = onet.match("process supplier invoices in ERP and code to cost centers", k=3)
    assert ms[0].occupation_code == "43-3031.00"
    assert ms[0].task_id == "1001"
    assert "Process invoices or payments." in ms[0].dwas
    assert "SAP software" in ms[0].technologies
    assert 0 < ms[0].score <= 1


def test_match_ticket_and_kyc(fixture_onet):
    assert onet.match("escalate support ticket to tier 2")[0].occupation_code == "15-1232.00"
    assert onet.match("KYC check customer documents")[0].occupation_code == "43-4141.00"


def test_match_russian_via_glossary(fixture_onet):
    assert onet.is_non_english("обработка счетов поставщиков")
    ms = onet.match("обработка счетов поставщиков")
    assert ms and ms[0].occupation_code == "43-3031.00"


def test_match_injected_translate(fixture_onet):
    ms = onet.match("абракадабра", translate=lambda s: "verify customer identity")
    assert ms[0].occupation_code == "43-4141.00"


def test_match_empty(fixture_onet):
    assert onet.match("") == []
    assert onet.match("zzzqqq xxyyzz") == []


def test_rerank_used_when_key(fixture_onet, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "k")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["body"] = json
        # put the last candidate first
        n = len(json["documents"])
        return httpx.Response(200, json={"results": [{"index": n - 1, "relevance_score": 0.9},
                                                     {"index": 0, "relevance_score": 0.5}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(onet.httpx, "post", fake_post)
    ms = onet.match("process invoices and verify customer identity", k=2)
    assert seen["body"]["model"] == onet.JINA_RERANK_MODEL
    assert ms[0].score == 0.9


def test_rerank_failure_falls_back(fixture_onet, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "k")

    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(onet.httpx, "post", boom)
    assert onet.match("process supplier invoices")[0].occupation_code == "43-3031.00"


async def test_amatch_translates_with_llm(fixture_onet, monkeypatch):
    from claros.context import _llm

    async def fake_llm(messages, role="fast", timeout=20.0):
        return "verify identity documents of customers"

    monkeypatch.setattr(_llm, "llm_text", fake_llm)
    ms = await onet.amatch("проверка паспорта клиента")
    assert ms[0].occupation_code == "43-4141.00"


def test_occupation_profile(fixture_onet):
    p = onet.occupation_profile("43-3031.00")
    assert p["title"].startswith("Bookkeeping")
    assert len(p["tasks"]) == 2 and "Code data or other information." in p["dwas"]
    assert any(t["category"] == "Accounting software" for t in p["technologies"])
    assert p["source"] == "onet:43-3031.00"
    assert onet.occupation_profile("99-9999.99") is None


# ---- real O*NET 31.0 (skipped unless data/onet was fetched) ----
REAL = onet_fetch.repo_root() / "data" / "onet" / "onet_index.sqlite"


@pytest.fixture
def real_onet(monkeypatch):
    if not REAL.exists():
        pytest.skip("run python -m claros.context.onet_fetch")
    monkeypatch.delenv("CLAROS_ONET_DIR", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    onet.reset()
    onet._df_cache.clear()
    yield
    onet.reset()


@pytest.mark.parametrize("q,expected", [
    ("process supplier invoices in ERP and code to cost centers", {"43-3031.00", "43-3021.00", "13-2011.00"}),
    ("escalate support ticket to tier 2", {"15-1232.00"}),
    ("KYC check customer documents", {"43-4141.00", "43-4041.00", "43-3071.00"}),
    ("обработка счетов поставщиков", {"43-3031.00", "43-3021.00", "13-2011.00"}),
])
def test_real_queries(real_onet, q, expected):
    codes = {m.occupation_code for m in onet.match(q, k=5)}
    assert codes & expected, codes

"""claros.context.scope — classifier + try_resolve with mocked LLM/web (offline)."""
from __future__ import annotations

import pytest

from claros.context import onet, scope, web
from claros.models import Unknown

from test_context_onet import write_fixture


@pytest.fixture
def no_llm(monkeypatch):
    async def none(*a, **k):
        return None

    monkeypatch.setattr(scope, "llm_json", none)


def llm_returns(monkeypatch, *outs):
    seq = list(outs)
    calls = []

    async def fake(messages, role="fast", timeout=20.0):
        calls.append(messages)
        return seq.pop(0) if seq else None

    monkeypatch.setattr(scope, "llm_json", fake)
    return calls


@pytest.mark.parametrize("q,expected", [
    ("What is the approval threshold for our capex purchases?", "company"),
    ("Who approves invoices over 5000 here at our company?", "company"),
    ("Why did you change the cost center to 0400?", "personal_judgment"),
    ("How do you decide when a vendor invoice smells off?", "personal_judgment"),
    ("What is capex?", "universal"),
    ("What does the is_fixed_asset checkbox do in ERPNext?", "app"),
    ("What are the typical steps of the accounts payable process?", "occupation"),
    ("Почему вы изменили центр затрат?", "personal_judgment"),
    ("hmm", "personal_judgment"),
])
async def test_heuristic(no_llm, q, expected):
    s, c = await scope.classify_scope(q, {})
    assert s == expected, (q, s, c)
    assert 0 < c <= 1


async def test_llm_classification_used(monkeypatch):
    llm_returns(monkeypatch, {"scope": "app", "confidence": 0.9, "reason": "field"})
    assert await scope.classify_scope("What happens on Submit?", {"app": "ERPNext"}) == ("app", 0.9)


async def test_llm_cannot_downgrade_company(monkeypatch):
    llm_returns(monkeypatch, {"scope": "universal", "confidence": 0.8})
    s, _ = await scope.classify_scope("What threshold do we use for our approvals?", {})
    assert s == "company"


async def test_llm_garbage_falls_back(monkeypatch):
    llm_returns(monkeypatch, {"scope": "banana"})
    assert (await scope.classify_scope("What is accrual accounting?", {}))[0] == "universal"


async def test_company_not_resolved(no_llm):
    u = Unknown(id="u1", type="limit", spoken_question="What's our approval threshold for capex?")
    assert await scope.try_resolve(u) is None


async def test_universal_needs_llm(no_llm):
    assert await scope.try_resolve("What is capex?") is None


async def test_universal_with_llm(monkeypatch):
    llm_returns(monkeypatch, {"scope": "universal", "confidence": 0.9},
                {"answerable": True, "answer": "Capex is spending on long-lived assets."})
    note = await scope.try_resolve("What is capex?")
    assert note.scope == "universal" and note.source == "llm" and "long-lived" in note.text


async def test_app_resolved_from_docs_without_llm(no_llm, monkeypatch):
    async def fake_docs(app, topic, **k):
        assert app == "ERPNext" and "is_fixed_asset" in topic
        return [{"url": "https://docs.frappe.io/erpnext/item", "title": "Item",
                 "snippet": "The is_fixed_asset checkbox posts to the Asset account.", "official": True}]

    monkeypatch.setattr(web, "app_docs", fake_docs)
    note = await scope.try_resolve("What does the is_fixed_asset checkbox do?", {"app": "ERPNext"})
    assert note.scope == "app" and note.source == "https://docs.frappe.io/erpnext/item"
    assert "Asset account" in note.text and "ERPNext docs" in note.text


async def test_app_with_llm_cites_chosen_source(monkeypatch):
    async def fake_docs(app, topic, **k):
        return [{"url": "https://a", "snippet": "a", "official": True},
                {"url": "https://b", "snippet": "b", "official": True}]

    monkeypatch.setattr(web, "app_docs", fake_docs)
    llm_returns(monkeypatch, {"scope": "app", "confidence": 0.8},
                {"answerable": True, "answer": "It marks the item as an asset.", "source_index": 1})
    note = await scope.try_resolve("What does the is_fixed_asset field do in ERPNext?")
    assert note.source == "https://b"


async def test_app_llm_says_unanswerable(monkeypatch):
    async def fake_docs(app, topic, **k):
        return [{"url": "https://a", "snippet": "a", "official": True}]

    monkeypatch.setattr(web, "app_docs", fake_docs)
    llm_returns(monkeypatch, {"scope": "app", "confidence": 0.8}, {"answerable": False})
    assert await scope.try_resolve("What does the Hold button do in ERPNext?") is None


async def test_occupation_from_onet(no_llm, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAROS_ONET_DIR", str(tmp_path / "onet"))
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    write_fixture(tmp_path / "onet")
    onet.reset()
    onet._df_cache.clear()
    onet.build_index(force=True)
    try:
        u = Unknown(id="u2", type="why", spoken_question="What is typically done when processing supplier invoices?")
        note = await scope.try_resolve(u, {"workflow": "accounts payable"})
        assert note is not None and note.scope == "occupation"
        assert note.source == "onet:43-3031.00" and "O*NET 43-3031.00" in note.text
    finally:
        onet.reset()
        onet._df_cache.clear()


async def test_try_resolve_never_raises(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(scope, "classify_scope", boom)
    assert await scope.try_resolve("What is capex?") is None


def test_router_and_register():
    import claros.context as ctx

    assert ctx.register(object()) is None
    paths = {r.path for r in ctx.router.routes}
    assert {"/api/context/onet", "/api/context/search", "/api/context/scope"} <= paths

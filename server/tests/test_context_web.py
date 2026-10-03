"""claros.context.web — backends mocked with httpx.MockTransport (offline)."""
from __future__ import annotations

import json

import httpx
import pytest

from claros.context import web

KEYS = ("EXA_API_KEY", "BRIGHTDATA_API_KEY", "JINA_API_KEY", "BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_UNLOCKER_ZONE",
        "CLAROS_CRAWL4AI", "RENDER", "K_SERVICE", "FLY_APP_NAME")

DOC_MD = """# Purchase Invoice

A Purchase Invoice is a bill you receive from your Supplier against which you need to make the payment.

## Is Fixed Asset

If the item is a fixed asset, the is_fixed_asset checkbox on the item makes the invoice post to the Asset account instead of the expense account. This is set on the Item master.

## Other
Unrelated paragraph about printing settings and letterheads for the document.
"""

ZONES = [{"name": "serp_api1", "type": "serp"}, {"name": "web_unlocker1", "type": "unblocker"}]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAROS_CRAWL4AI", "0")  # never launch a browser in tests
    monkeypatch.setenv("CLAROS_CACHE_DIR", str(tmp_path / "cache"))
    web.reset_zone_cache()
    yield
    web._transport = None
    web.reset_zone_cache()


def mock(handler):
    calls: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return handler(req)

    web._transport = httpx.MockTransport(h)
    return calls


def zones_or(req, zones=ZONES):
    if req.url.path == "/zone/get_active_zones":
        return httpx.Response(200, json=zones)
    return None


# ---------------------------------------------------------------- search

async def test_search_exa_highlights_and_domains(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        assert req.url.host == "api.exa.ai" and req.url.path == "/search" and req.headers["x-api-key"] == "ex"
        body = json.loads(req.content)
        assert body["type"] == "auto" and body["numResults"] == 2
        assert body["includeDomains"] == ["docs.frappe.io"]
        assert body["contents"]["highlights"]["query"] == "purchase invoice"
        return httpx.Response(200, json={"results": [
            {"url": "https://docs.frappe.io/x", "title": "X", "highlights": ["h1", "h2"]},
            {"url": "https://docs.frappe.io/y", "title": "Y", "text": "plain text"}]})

    calls = mock(h)
    res = await web.search("purchase invoice", k=2, include_domains=["docs.frappe.io"])
    assert res[0] == {"title": "X", "url": "https://docs.frappe.io/x", "snippet": "h1\n…\nh2",
                      "highlights": ["h1", "h2"], "backend": "exa"}
    assert res[1]["snippet"] == "plain text"
    assert await web.search("purchase invoice", k=2, include_domains=["docs.frappe.io"]) == res  # cached
    assert len(calls) == 1


async def test_search_exa_fails_then_brightdata(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")

    def h(req):
        if req.url.host == "api.exa.ai":
            return httpx.Response(500)
        if (z := zones_or(req)) is not None:
            return z
        assert req.url.path == "/request" and req.headers["authorization"] == "Bearer bd"
        body = json.loads(req.content)
        assert body["zone"] == "serp_api1" and body["data_format"] == "parsed_light"
        assert "google.com/search?q=site%3Adocs.frappe.io+erpnext+purchase+invoice" in body["url"]
        return httpx.Response(200, json={"organic": [
            {"link": "https://docs.frappe.io/x", "title": "X", "description": "d1"},
            {"link": "https://y.com", "title": "Y", "description": "d2"}]})

    mock(h)
    res = await web.search("erpnext purchase invoice", k=1, include_domains=["docs.frappe.io"])
    assert res == [{"title": "X", "url": "https://docs.frappe.io/x", "snippet": "d1", "backend": "brightdata"}]


async def test_brightdata_skipped_when_zone_missing_detected_once(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")
    monkeypatch.setenv("JINA_API_KEY", "jn")

    def h(req):
        if (z := zones_or(req, zones=[])) is not None:
            return z
        if req.url.host == "s.jina.ai":
            return httpx.Response(200, json={"data": [{"title": "J", "url": "https://j.com", "description": "jd"}]})
        raise AssertionError(f"unexpected {req.url}")  # no /request without a zone

    calls = mock(h)
    assert (await web.search("q1"))[0]["backend"] == "jina"
    assert (await web.search("q2"))[0]["backend"] == "jina"
    assert sum(1 for c in calls if c.url.path == "/zone/get_active_zones") == 1


async def test_search_jina_site_filter(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jn")

    def h(req):
        assert req.url.host == "s.jina.ai"
        assert req.url.params["q"] == "(site:a.com OR site:b.com) topic"
        return httpx.Response(200, json={"data": [{"title": "J", "url": "https://a.com/1", "content": "c" * 500}]})

    mock(h)
    res = await web.search("topic", include_domains=["a.com", "b.com"])
    assert res[0]["snippet"] == "c" * 300


async def test_search_no_keys_returns_empty():
    mock(lambda req: (_ for _ in ()).throw(AssertionError(req.url)))
    assert await web.search("anything") == []


async def test_search_all_fail_returns_empty(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")
    monkeypatch.setenv("JINA_API_KEY", "jn")
    mock(lambda req: httpx.Response(503))
    assert await web.search("anything") == []


# ---------------------------------------------------------------- read

async def test_read_exa_contents(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        assert req.url.path == "/contents"
        body = json.loads(req.content)
        assert body["urls"] == ["https://a.com"] and body["text"]
        return httpx.Response(200, json={"results": [{"url": "https://a.com", "title": "Hello", "text": "world"}],
                                         "statuses": [{"id": "https://a.com", "status": "success"}]})

    calls = mock(h)
    assert await web.read("https://a.com") == "# Hello\n\nworld"
    assert await web.read("https://a.com") == "# Hello\n\nworld"
    assert len(calls) == 1


async def test_read_exa_error_status_falls_back_to_jina(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        if req.url.host == "api.exa.ai":
            return httpx.Response(200, json={"results": [], "statuses": [
                {"id": "https://a.com", "status": "error", "error": {"tag": "CRAWL_NOT_FOUND"}}]})
        assert req.url.host == "r.jina.ai"
        return httpx.Response(200, text="from jina")

    mock(h)
    assert await web.read("https://a.com") == "from jina"


async def test_read_brightdata_unlocker_after_jina_fails(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")

    def h(req):
        if req.url.host == "r.jina.ai":
            return httpx.Response(451)
        if (z := zones_or(req)) is not None:
            return z
        body = json.loads(req.content)
        assert body["data_format"] == "markdown" and body["zone"] == "web_unlocker1"
        return httpx.Response(200, text="# Unblocked")

    mock(h)
    assert await web.read("https://blocked.com") == "# Unblocked"


async def test_read_falls_back_to_plain():
    def h(req):
        if req.url.host == "r.jina.ai":
            return httpx.Response(500)
        return httpx.Response(200, text="<html><script>x()</script><h1>T</h1><p>Body &amp; more</p></html>",
                              headers={"content-type": "text/html"})

    mock(h)
    md = await web.read("https://plain.com/page")
    assert "# T" in md and "Body & more" in md and "x()" not in md


async def test_read_jina_no_key():
    def h(req):
        assert req.url.host == "r.jina.ai" and "authorization" not in req.headers
        return httpx.Response(200, text="Title: P\n\nMarkdown Content:\nhello")

    mock(h)
    assert "hello" in await web.read("https://p.com")


async def test_read_uses_crawl4ai_when_enabled(monkeypatch):
    monkeypatch.setenv("CLAROS_CRAWL4AI", "1")
    seen = []

    async def fake_crawl(url):
        seen.append(url)
        return "# crawled"

    fake_crawl.__name__ = "_crawl4ai_read"
    monkeypatch.setattr(web, "READ_BACKENDS", (web._exa_read, fake_crawl, web._jina_read))
    mock(lambda req: (_ for _ in ()).throw(AssertionError(req.url)))
    assert await web.read("https://c.com") == "# crawled" and seen == ["https://c.com"]


def test_crawl4ai_enabled_flag(monkeypatch):
    monkeypatch.delenv("CLAROS_CRAWL4AI")
    assert web._crawl4ai_enabled()  # local default on
    monkeypatch.setenv("RENDER", "true")
    assert not web._crawl4ai_enabled()  # cloud default off
    monkeypatch.setenv("CLAROS_CRAWL4AI", "1")
    assert web._crawl4ai_enabled()
    monkeypatch.setenv("CLAROS_CRAWL4AI", "0")
    assert not web._crawl4ai_enabled()


async def test_crawl4ai_disabled_returns_none():
    assert await web._crawl4ai_read("https://x.com") is None


# ---------------------------------------------------------------- app docs

def test_best_snippets():
    s = web.best_snippets(DOC_MD, "ERPNext Purchase Invoice is_fixed_asset", n=1)
    assert "is_fixed_asset" in s[0]


async def test_app_docs_exa_highlights_no_read(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        assert req.url.path == "/search", "highlights must avoid a second fetch"
        body = json.loads(req.content)
        assert body["includeDomains"] == ["docs.frappe.io", "docs.erpnext.com"]
        assert body["query"] == "ERPNext Purchase Invoice is_fixed_asset"
        return httpx.Response(200, json={"results": [
            {"url": "https://docs.frappe.io/erpnext/purchase-invoice", "title": "PI",
             "highlights": ["is_fixed_asset posts to the Asset account"]},
            {"url": "https://docs.frappe.io/erpnext/asset", "title": "Asset", "highlights": ["Asset record"]}]})

    mock(h)
    res = await web.app_docs("ERPNext", "Purchase Invoice is_fixed_asset", k=2)
    assert [r["url"] for r in res] == ["https://docs.frappe.io/erpnext/purchase-invoice",
                                       "https://docs.frappe.io/erpnext/asset"]
    assert "is_fixed_asset" in res[0]["snippet"] and all(r["official"] for r in res)


async def test_app_docs_non_exa_reads_pages(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jn")
    seen = []

    def h(req):
        if req.url.host == "s.jina.ai":
            seen.append(req.url.params["q"])
            return httpx.Response(200, json={"data": [
                {"title": "PI", "url": "https://docs.frappe.io/erpnext/purchase-invoice", "description": "d1"},
                {"title": "Other", "url": "https://docs.frappe.io/other", "description": "d2"}]})
        if req.url.host == "r.jina.ai":
            return httpx.Response(200, text=DOC_MD)
        raise AssertionError(req.url)

    mock(h)
    res = await web.app_docs("ERPNext", "Purchase Invoice is_fixed_asset", k=2, read_pages=1)
    assert seen[0].startswith("(site:docs.frappe.io OR site:docs.erpnext.com) ERPNext")
    assert "is_fixed_asset" in res[0]["snippet"] and res[0]["official"]
    assert res[1]["snippet"] == "d2"  # beyond read_pages: search snippet


async def test_app_docs_unknown_app_generic_search(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        body = json.loads(req.content)
        assert "includeDomains" not in body and body["query"].startswith("AcmeERP documentation")
        return httpx.Response(200, json={"results": [{"url": "https://blog.x/acme", "title": "B", "highlights": ["hl"]}]})

    mock(h)
    res = await web.app_docs("AcmeERP", "is_fixed_asset")
    assert res and not res[0]["official"]


async def test_app_docs_official_empty_falls_back_unrestricted(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ex")

    def h(req):
        body = json.loads(req.content)
        if "includeDomains" in body:
            return httpx.Response(200, json={"results": []})
        assert body["query"] == "Zammad documentation escalation SLA"
        return httpx.Response(200, json={"results": [
            {"url": "https://community.zammad.org/t/1", "title": "C", "highlights": ["escalation"]}]})

    mock(h)
    res = await web.app_docs("Zammad", "escalation SLA")
    assert res[0]["url"].startswith("https://community.zammad.org") and not res[0]["official"]

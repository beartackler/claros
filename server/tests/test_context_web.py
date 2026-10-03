"""claros.context.web — backends mocked with httpx.MockTransport (offline)."""
from __future__ import annotations

import json

import httpx
import pytest

from claros.context import web

KEYS = ("BRIGHTDATA_API_KEY", "JINA_API_KEY", "BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_UNLOCKER_ZONE")

DDG_HTML = """
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.frappe.io%2Ferpnext%2Fpurchase-invoice&rut=x">Purchase <b>Invoice</b></a>
<a class="result__snippet" href="#">A Purchase Invoice is a bill you receive from a supplier.</a></div>
<div class="result"><a rel="nofollow" class="result__a" href="https://example.com/b">Second</a>
<a class="result__snippet" href="#">second snippet</a></div>
"""

DOC_MD = """# Purchase Invoice

A Purchase Invoice is a bill you receive from your Supplier against which you need to make the payment.

## Is Fixed Asset

If the item is a fixed asset, the is_fixed_asset checkbox on the item makes the invoice post to the Asset account instead of the expense account. This is set on the Item master.

## Other
Unrelated paragraph about printing settings and letterheads for the document.
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAROS_CACHE_DIR", str(tmp_path / "cache"))
    yield
    web._transport = None


def mock(handler):
    calls: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return handler(req)

    web._transport = httpx.MockTransport(h)
    return calls


async def test_search_brightdata(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")

    def h(req):
        assert req.url.host == "api.brightdata.com" and req.headers["authorization"] == "Bearer bd"
        body = json.loads(req.content)
        assert body["zone"] == "serp_api1" and body["data_format"] == "parsed_light"
        assert "google.com/search?q=erpnext+purchase+invoice" in body["url"]
        return httpx.Response(200, json={"organic": [
            {"link": "https://docs.frappe.io/x", "title": "X", "description": "d1"},
            {"link": "https://y.com", "title": "Y", "description": "d2"}]})

    calls = mock(h)
    res = await web.search("erpnext purchase invoice", k=1)
    assert res == [{"title": "X", "url": "https://docs.frappe.io/x", "snippet": "d1", "backend": "brightdata"}]
    # cached: no new network call
    assert await web.search("erpnext purchase invoice", k=1) == res
    assert len(calls) == 1


async def test_search_falls_back_to_jina_then_ddg(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")
    monkeypatch.setenv("JINA_API_KEY", "jn")

    def h(req):
        if req.url.host == "api.brightdata.com":
            return httpx.Response(401, text="bad zone")
        if req.url.host == "s.jina.ai":
            return httpx.Response(200, json={"data": [{"title": "J", "url": "https://j.com", "description": "jd"}]})
        raise AssertionError(req.url)

    mock(h)
    res = await web.search("q1")
    assert res[0]["backend"] == "jina" and res[0]["url"] == "https://j.com"


async def test_search_no_keys_uses_ddg():
    def h(req):
        assert req.url.host == "html.duckduckgo.com"
        return httpx.Response(200, text=DDG_HTML)

    mock(h)
    res = await web.search("purchase invoice", k=5)
    assert res[0]["url"] == "https://docs.frappe.io/erpnext/purchase-invoice"
    assert res[0]["title"] == "Purchase Invoice" and res[0]["backend"] == "ddg"
    assert len(res) == 2


async def test_search_all_fail_returns_empty():
    mock(lambda req: httpx.Response(503))
    assert await web.search("anything") == []


async def test_read_brightdata_markdown(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "bd")

    def h(req):
        body = json.loads(req.content)
        assert body["data_format"] == "markdown" and body["zone"] == "web_unlocker1"
        return httpx.Response(200, text="# Hello\n\nworld")

    calls = mock(h)
    assert await web.read("https://a.com") == "# Hello\n\nworld"
    assert await web.read("https://a.com") == "# Hello\n\nworld"
    assert len(calls) == 1


async def test_read_falls_back_to_jina_reader_then_plain():
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


def test_best_snippets():
    s = web.best_snippets(DOC_MD, "ERPNext Purchase Invoice is_fixed_asset", n=1)
    assert "is_fixed_asset" in s[0]


async def test_app_docs_official_site(monkeypatch):
    seen = []

    def h(req):
        if req.url.host == "html.duckduckgo.com":
            q = dict(httpx.QueryParams(req.content.decode()))["q"]
            seen.append(q)
            return httpx.Response(200, text=DDG_HTML)
        if req.url.host == "r.jina.ai":
            return httpx.Response(200, text=DOC_MD)
        raise AssertionError(req.url)

    mock(h)
    res = await web.app_docs("ERPNext", "Purchase Invoice is_fixed_asset", k=2, read_pages=1)
    assert seen[0].startswith("site:docs.frappe.io")
    assert res[0]["url"] == "https://docs.frappe.io/erpnext/purchase-invoice"
    assert "is_fixed_asset" in res[0]["snippet"] and res[0]["official"]
    assert len(res) == 2  # second from search snippet


async def test_app_docs_unknown_app_generic_search():
    def h(req):
        if req.url.host == "html.duckduckgo.com":
            q = dict(httpx.QueryParams(req.content.decode()))["q"]
            assert q.startswith("AcmeERP documentation")
            return httpx.Response(200, text=DDG_HTML)
        return httpx.Response(200, text=DOC_MD)

    mock(h)
    res = await web.app_docs("AcmeERP", "is_fixed_asset")
    assert res and not res[1]["official"]

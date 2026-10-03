"""Web context tools: search + read (markdown) + official app docs lookup, cached.

Backends, in order (each skipped when its key is missing or it fails):
  search: Bright Data SERP API (BRIGHTDATA_API_KEY, zone BRIGHTDATA_SERP_ZONE) ->
          Jina s.jina.ai (JINA_API_KEY) -> DuckDuckGo HTML (no key)
  read:   Bright Data Web Unlocker markdown (BRIGHTDATA_API_KEY, zone BRIGHTDATA_UNLOCKER_ZONE) ->
          Jina r.jina.ai (key optional) -> plain GET + crude HTML-to-text
Bright Data REST: POST https://api.brightdata.com/request {zone, url, format:"raw", data_format}
(docs.brightdata.com/scraping-automation/serp-api/introduction).
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx

from . import onet_fetch

log = logging.getLogger("claros.context.web")

BRIGHTDATA_URL = "https://api.brightdata.com/request"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
CACHE_TTL = float(os.environ.get("CLAROS_WEB_CACHE_TTL", 7 * 24 * 3600))
MAX_MD = 60_000

# test hook: tests set this to httpx.MockTransport(handler)
_transport: Optional[httpx.AsyncBaseTransport] = None


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": UA})


# ---------------------------------------------------------------- cache

_cache_lock = threading.Lock()


def _cache_path() -> Path:
    d = Path(os.environ.get("CLAROS_CACHE_DIR", onet_fetch.repo_root() / "data" / "cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "context_web.sqlite"


def _cache_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_cache_path())
    c.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, v TEXT, ts REAL)")
    return c


def cache_get(key: str) -> Any:
    with _cache_lock:
        c = _cache_conn()
        try:
            row = c.execute("SELECT v, ts FROM cache WHERE k=?", (key,)).fetchone()
        finally:
            c.close()
    if row and time.time() - row[1] < CACHE_TTL:
        return json.loads(row[0])
    return None


def cache_put(key: str, value: Any) -> None:
    with _cache_lock:
        c = _cache_conn()
        try:
            c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(value), time.time()))
            c.commit()
        finally:
            c.close()


# ---------------------------------------------------------------- search backends

async def _brightdata_search(query: str, k: int) -> list[dict]:
    key = os.environ.get("BRIGHTDATA_API_KEY")
    if not key:
        return []
    zone = os.environ.get("BRIGHTDATA_SERP_ZONE", "serp_api1")
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us&num={max(k, 10)}"
    async with _client(45) as c:
        r = await c.post(BRIGHTDATA_URL, headers={"Authorization": f"Bearer {key}"},
                         json={"zone": zone, "url": url, "format": "raw", "data_format": "parsed_light"})
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and "body" in data and isinstance(data["body"], str):  # format=json envelope
        data = json.loads(data["body"])
    return [
        {"title": o.get("title", ""), "url": o.get("link") or o.get("url", ""),
         "snippet": o.get("description", ""), "backend": "brightdata"}
        for o in (data.get("organic") or [])[:k]
        if o.get("link") or o.get("url")
    ]


async def _jina_search(query: str, k: int) -> list[dict]:
    key = os.environ.get("JINA_API_KEY")
    if not key:
        return []
    async with _client(30) as c:
        r = await c.get(f"https://s.jina.ai/?q={quote_plus(query)}",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                 "X-Respond-With": "no-content"})
        r.raise_for_status()
        data = r.json().get("data") or []
    return [
        {"title": d.get("title", ""), "url": d.get("url", ""),
         "snippet": d.get("description") or (d.get("content") or "")[:300], "backend": "jina"}
        for d in data[:k] if d.get("url")
    ]


_DDG_A = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SN = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _strip_tags(s: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_ddg(html: str, k: int) -> list[dict]:
    links = _DDG_A.findall(html)
    snippets = _DDG_SN.findall(html)
    out = []
    for i, (href, title) in enumerate(links):
        if href.startswith("//"):
            href = "https:" + href
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            href = q["uddg"][0]
        if "duckduckgo.com/y.js" in href:  # ads
            continue
        out.append({"title": _strip_tags(title), "url": href,
                    "snippet": _strip_tags(snippets[i]) if i < len(snippets) else "", "backend": "ddg"})
        if len(out) >= k:
            break
    return out


async def _ddg_search(query: str, k: int) -> list[dict]:
    async with _client(20) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    return parse_ddg(r.text, k)


async def search(query: str, k: int = 5) -> list[dict]:
    """[{title, url, snippet, backend}] — first backend that returns results wins."""
    if not query.strip():
        return []
    ck = f"search:{k}:{query}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    for fn in (_brightdata_search, _jina_search, _ddg_search):
        try:
            res = await fn(query, k)
        except Exception as e:
            log.warning("search backend %s failed: %s", fn.__name__, e)
            continue
        if res:
            cache_put(ck, res)
            return res
    return []


# ---------------------------------------------------------------- read backends

async def _brightdata_read(url: str) -> Optional[str]:
    key = os.environ.get("BRIGHTDATA_API_KEY")
    if not key:
        return None
    zone = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1")
    async with _client(60) as c:
        r = await c.post(BRIGHTDATA_URL, headers={"Authorization": f"Bearer {key}"},
                         json={"zone": zone, "url": url, "format": "raw", "data_format": "markdown"})
        r.raise_for_status()
        return r.text


async def _jina_read(url: str) -> Optional[str]:
    headers = {"X-Return-Format": "markdown"}
    if os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    async with _client(45) as c:
        r = await c.get(f"https://r.jina.ai/{url}", headers=headers)
        r.raise_for_status()
        return r.text


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", html)
    html = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "\n- ", html)
    text = _strip_tags(html)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


async def _plain_read(url: str) -> Optional[str]:
    async with _client(20) as c:
        r = await c.get(url)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        return html_to_text(r.text) if "html" in ct or r.text.lstrip().startswith("<") else r.text


async def read(url: str) -> str:
    """Page as markdown ('' if every backend fails)."""
    ck = f"read:{url}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    for fn in (_brightdata_read, _jina_read, _plain_read):
        try:
            md = await fn(url)
        except Exception as e:
            log.warning("read backend %s failed for %s: %s", fn.__name__, url, e)
            continue
        if md and md.strip():
            md = md[:MAX_MD]
            cache_put(ck, md)
            return md
    return ""


# ---------------------------------------------------------------- app docs

APP_DOCS: dict[str, list[str]] = {
    "erpnext": ["docs.frappe.io", "docs.erpnext.com"],
    "frappe": ["docs.frappe.io"],
    "zammad": ["admin-docs.zammad.org", "user-docs.zammad.org"],
    "odoo": ["odoo.com/documentation"],
    "sap": ["help.sap.com"],
    "netsuite": ["docs.oracle.com"],
    "salesforce": ["help.salesforce.com"],
    "quickbooks": ["quickbooks.intuit.com"],
    "xero": ["central.xero.com"],
    "jira": ["support.atlassian.com", "confluence.atlassian.com"],
    "servicenow": ["docs.servicenow.com", "servicenow.com/docs"],
    "zendesk": ["support.zendesk.com"],
    "hubspot": ["knowledge.hubspot.com"],
    "workday": ["doc.workday.com"],
    "dynamics": ["learn.microsoft.com"],
    "excel": ["support.microsoft.com"],
}


def docs_domains(app: str) -> list[str]:
    a = app.lower()
    for name, doms in APP_DOCS.items():
        if name in a:
            return doms
    return []


def _keywords(text: str) -> list[str]:
    toks = re.findall(r"[\w.]+", text.lower())
    return [t for t in toks if len(t) > 2 and t not in {"the", "and", "what", "does", "for", "how", "with"}]


def best_snippets(md: str, topic: str, n: int = 3, width: int = 700) -> list[str]:
    """Paragraphs of md that best overlap the topic keywords (also matches snake_case as words)."""
    kws = set(_keywords(topic)) | {w for k in _keywords(topic) for w in k.split("_") if len(w) > 2}
    paras = [p.strip() for p in re.split(r"\n\s*\n", md) if len(p.strip()) > 40]
    scored = []
    for i, p in enumerate(paras):
        low = p.lower()
        s = sum(2 if k in low else 0 for k in kws if "_" in k or "." in k) + sum(1 for k in kws if k in low)
        if s:
            scored.append((s, i, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p[:width] for _, _, p in scored[:n]]


async def app_docs(app: str, topic: str, k: int = 3, read_pages: int = 2) -> list[dict]:
    """Search the app's official docs for topic. Returns [{url, title, snippet, official}] (cited)."""
    doms = docs_domains(app)
    results: list[dict] = []
    for d in doms:
        results = await search(f"site:{d} {topic}", k=k + 2)
        if results:
            break
    official = bool(results)
    if not results:
        results = await search(f"{app} documentation {topic}", k=k + 2)
    out: list[dict] = []
    for r in results[:read_pages]:
        md = await read(r["url"])
        snips = best_snippets(md, f"{app} {topic}", n=2) if md else []
        snippet = "\n…\n".join(snips) if snips else r.get("snippet", "")
        if snippet:
            out.append({"url": r["url"], "title": r.get("title", ""), "snippet": snippet,
                        "official": official or any(d in r["url"] for d in doms)})
    for r in results[read_pages:]:
        if len(out) >= k:
            break
        if r.get("snippet"):
            out.append({"url": r["url"], "title": r.get("title", ""), "snippet": r["snippet"],
                        "official": official or any(d in r["url"] for d in doms)})
    return out[:k]


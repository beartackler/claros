"""Web context tools: search + read (markdown) + official app docs lookup, cached.

Backends, in order (each skipped when its key/zone is missing or it fails):
  search: Exa /search (EXA_API_KEY; highlights inline, includeDomains) ->
          Bright Data SERP (BRIGHTDATA_API_KEY + zone BRIGHTDATA_SERP_ZONE that actually exists) ->
          Jina s.jina.ai (JINA_API_KEY) -> nothing
  read:   Exa /contents (EXA_API_KEY) -> Crawl4AI in-process headless browser (optional, CLAROS_CRAWL4AI) ->
          Jina r.jina.ai (key optional) -> Bright Data Web Unlocker (zone BRIGHTDATA_UNLOCKER_ZONE, must exist) ->
          plain GET + crude HTML-to-text
Bright Data zones are detected once via GET /zone/get_active_zones and cached in-process (ZONE_TTL),
so zones created later are picked up without a restart.
Every backend call logs its latency (logger claros.context.web).
"""
from __future__ import annotations

import asyncio
import html as htmlmod
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote_plus

import httpx

from . import onet_fetch

log = logging.getLogger("claros.context.web")

EXA_URL = "https://api.exa.ai"
BRIGHTDATA_URL = "https://api.brightdata.com/request"
BRIGHTDATA_ZONES_URL = "https://api.brightdata.com/zone/get_active_zones"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
CACHE_TTL = float(os.environ.get("CLAROS_WEB_CACHE_TTL", 7 * 24 * 3600))
ZONE_TTL = 600.0
CRAWL4AI_TIMEOUT = 15.0
MAX_MD = 60_000

# test hook: tests set this to httpx.MockTransport(handler)
_transport: Optional[httpx.AsyncBaseTransport] = None


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": UA})


async def _timed(kind: str, fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    """Run one backend, log latency + outcome. Exceptions propagate."""
    t0 = time.perf_counter()
    name = fn.__name__.strip("_")
    try:
        res = await fn(*args)
    except Exception as e:
        log.warning("web.%s backend=%s failed in %.0fms: %s", kind, name, (time.perf_counter() - t0) * 1000, e)
        raise
    n = len(res) if res else 0
    log.info("web.%s backend=%s %.0fms n=%d", kind, name, (time.perf_counter() - t0) * 1000, n)
    return res


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


# ---------------------------------------------------------------- Bright Data zone detection

_zones: dict[str, Any] = {"names": None, "ts": 0.0}


def reset_zone_cache() -> None:
    _zones["names"], _zones["ts"] = None, 0.0


async def _brightdata_zone(env: str, default: str) -> Optional[str]:
    """Zone name if BRIGHTDATA_API_KEY is set and the zone exists (detected once, cached ZONE_TTL)."""
    key = os.environ.get("BRIGHTDATA_API_KEY")
    if not key:
        return None
    zone = os.environ.get(env, default)
    if _zones["names"] is None or time.time() - _zones["ts"] > ZONE_TTL:
        names: set[str] = set()
        try:
            async with _client(15) as c:
                r = await c.get(BRIGHTDATA_ZONES_URL, headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                names = {z.get("name") for z in r.json() if isinstance(z, dict)}
        except Exception as e:
            log.warning("brightdata zone detection failed: %s", e)
        _zones["names"], _zones["ts"] = names, time.time()
    return zone if zone in _zones["names"] else None


# ---------------------------------------------------------------- search backends

def _site_query(query: str, include_domains: Optional[list[str]]) -> str:
    if not include_domains:
        return query
    sites = " OR ".join(f"site:{d}" for d in include_domains)
    return f"{sites} {query}" if len(include_domains) == 1 else f"({sites}) {query}"


async def _exa_search(query: str, k: int, include_domains: Optional[list[str]] = None) -> list[dict]:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return []
    body: dict[str, Any] = {
        "query": query, "type": "auto", "numResults": k,
        "contents": {"highlights": {"query": query, "maxCharacters": 1500}},
    }
    if include_domains:
        body["includeDomains"] = include_domains
    async with _client(30) as c:
        r = await c.post(f"{EXA_URL}/search", headers={"x-api-key": key}, json=body)
        r.raise_for_status()
        data = r.json().get("results") or []
    out = []
    for d in data[:k]:
        if not d.get("url"):
            continue
        hl = [h.strip() for h in (d.get("highlights") or []) if h and h.strip()]
        out.append({"title": d.get("title") or "", "url": d["url"],
                    "snippet": "\n…\n".join(hl) or (d.get("text") or "")[:300],
                    "highlights": hl, "backend": "exa"})
    return out


async def _brightdata_search(query: str, k: int, include_domains: Optional[list[str]] = None) -> list[dict]:
    zone = await _brightdata_zone("BRIGHTDATA_SERP_ZONE", "serp_api1")
    if not zone:
        return []
    q = _site_query(query, include_domains)
    url = f"https://www.google.com/search?q={quote_plus(q)}&num={max(k, 10)}"
    async with _client(45) as c:
        r = await c.post(BRIGHTDATA_URL, headers={"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"},
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


async def _jina_search(query: str, k: int, include_domains: Optional[list[str]] = None) -> list[dict]:
    key = os.environ.get("JINA_API_KEY")
    if not key:
        return []
    q = _site_query(query, include_domains)
    async with _client(30) as c:
        r = await c.get(f"https://s.jina.ai/?q={quote_plus(q)}",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                 "X-Respond-With": "no-content"})
        r.raise_for_status()
        data = r.json().get("data") or []
    return [
        {"title": d.get("title", ""), "url": d.get("url", ""),
         "snippet": d.get("description") or (d.get("content") or "")[:300], "backend": "jina"}
        for d in data[:k] if d.get("url")
    ]


def _strip_tags(s: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).strip()


SEARCH_BACKENDS = (_exa_search, _brightdata_search, _jina_search)


async def search(query: str, k: int = 5, include_domains: Optional[list[str]] = None) -> list[dict]:
    """[{title, url, snippet, backend, highlights?}] — first backend that returns results wins."""
    if not query.strip():
        return []
    ck = f"search:{k}:{','.join(include_domains or [])}:{query}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    for fn in SEARCH_BACKENDS:
        try:
            res = await _timed("search", fn, query, k, include_domains)
        except Exception:
            continue
        if res:
            cache_put(ck, res)
            return res
    return []


# ---------------------------------------------------------------- read backends

async def _exa_read(url: str) -> Optional[str]:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return None
    async with _client(30) as c:
        r = await c.post(f"{EXA_URL}/contents", headers={"x-api-key": key},
                         json={"urls": [url], "text": {"maxCharacters": MAX_MD}})
        r.raise_for_status()
        data = r.json()
    for st in data.get("statuses") or []:
        if st.get("status") not in (None, "success"):
            raise RuntimeError(f"exa status {st.get('status')}: {st.get('error')}")
    res = data.get("results") or []
    if not res:
        return None
    title, text = res[0].get("title") or "", res[0].get("text") or ""
    return f"# {title}\n\n{text}" if title and text and not text.lstrip().startswith("#") else text


def _crawl4ai_enabled() -> bool:
    v = os.environ.get("CLAROS_CRAWL4AI")
    if v is not None:
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    # default: on locally, off in cloud (no browser there)
    return not any(os.environ.get(e) for e in ("RENDER", "K_SERVICE", "FLY_APP_NAME", "RAILWAY_ENVIRONMENT",
                                               "DYNO", "AWS_EXECUTION_ENV", "VERCEL"))


_crawler: dict[str, Any] = {"c": None, "loop": None}
_crawler_lock: Optional[asyncio.Lock] = None


async def _get_crawler() -> Any:
    """Shared AsyncWebCrawler (one headless browser per event loop)."""
    global _crawler_lock
    loop = asyncio.get_running_loop()
    if _crawler["c"] is not None and _crawler["loop"] is loop:
        return _crawler["c"]
    if _crawler_lock is None or getattr(_crawler_lock, "_claros_loop", None) is not loop:
        _crawler_lock = asyncio.Lock()
        _crawler_lock._claros_loop = loop  # type: ignore[attr-defined]
    async with _crawler_lock:
        if _crawler["c"] is not None and _crawler["loop"] is loop:
            return _crawler["c"]
        from crawl4ai import AsyncWebCrawler, BrowserConfig  # import-guarded by caller

        c = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
        await c.start()
        _crawler["c"], _crawler["loop"] = c, loop
        return c


async def close_crawler() -> None:
    c = _crawler["c"]
    _crawler["c"], _crawler["loop"] = None, None
    if c is not None:
        try:
            await c.close()
        except Exception:
            pass


async def _crawl4ai_read(url: str) -> Optional[str]:
    if not _crawl4ai_enabled():
        return None
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator, PruningContentFilter
    except ImportError:
        return None
    crawler = await _get_crawler()
    cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=int(CRAWL4AI_TIMEOUT * 1000), verbose=False,
                           excluded_tags=["nav", "footer", "header", "aside", "form"],
                           markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilter()))
    res = await asyncio.wait_for(crawler.arun(url=url, config=cfg), timeout=CRAWL4AI_TIMEOUT + 2)
    if not getattr(res, "success", False):
        raise RuntimeError(getattr(res, "error_message", "crawl failed"))
    md = res.markdown  # MarkdownGenerationResult: fit_markdown = boilerplate-pruned
    return getattr(md, "fit_markdown", None) or getattr(md, "raw_markdown", None) or (str(md) if md else None)


async def _jina_read(url: str) -> Optional[str]:
    headers = {"X-Return-Format": "markdown"}
    if os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    async with _client(45) as c:
        r = await c.get(f"https://r.jina.ai/{url}", headers=headers)
        r.raise_for_status()
        return r.text


async def _brightdata_read(url: str) -> Optional[str]:
    zone = await _brightdata_zone("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1")
    if not zone:
        return None
    async with _client(60) as c:
        r = await c.post(BRIGHTDATA_URL, headers={"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"},
                         json={"zone": zone, "url": url, "format": "raw", "data_format": "markdown"})
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


READ_BACKENDS = (_exa_read, _crawl4ai_read, _jina_read, _brightdata_read, _plain_read)


async def read(url: str) -> str:
    """Page as markdown ('' if every backend fails)."""
    ck = f"read:{url}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    for fn in READ_BACKENDS:
        try:
            md = await _timed("read", fn, url)
        except Exception:
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
    "zammad": ["admin-docs.zammad.org", "user-docs.zammad.org", "docs.zammad.org"],
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


def _domain_match(url: str, doms: list[str]) -> bool:
    return any(d in url for d in doms)


async def app_docs(app: str, topic: str, k: int = 3, read_pages: int = 2) -> list[dict]:
    """Search the app's official docs for topic. Returns [{url, title, snippet, official}] (cited).

    Exa results carry highlights (no page fetch needed); other backends' results are read + excerpted."""
    doms = docs_domains(app)
    query = f"{app} {topic}"
    results: list[dict] = await search(query, k=k + 2, include_domains=doms) if doms else []
    if not results:
        results = await search(f"{app} documentation {topic}", k=k + 2)
    out: list[dict] = []
    reads = 0
    for r in results:
        if len(out) >= k:
            break
        snippet = "\n…\n".join(r["highlights"]) if r.get("highlights") else ""
        if not snippet and reads < read_pages:
            reads += 1
            md = await read(r["url"])
            snips = best_snippets(md, query, n=2) if md else []
            snippet = "\n…\n".join(snips)
        snippet = snippet or r.get("snippet", "")
        if snippet:
            out.append({"url": r["url"], "title": r.get("title", ""), "snippet": snippet,
                        "official": _domain_match(r["url"], doms)})
    return out

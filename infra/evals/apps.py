"""REST oracles + Playwright helpers for the two test beds (ERPNext :8080, Zammad :8081).

Ground truth for every eval comes from the apps' own REST APIs (never from Claros, never from OCR).
The DOM is used only for (a) visibility of a field in the viewport and (b) the app's own translation
function for localized labels, so localized screens can be scored.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

ERP = os.getenv("ERPNEXT_URL", "http://localhost:8080").rstrip("/")
ZAM = "http://localhost:8081"
PWD = "Claros-Demo-2026!"
ZADMIN = ("admin@helpdesk.example", PWD)
HIDE_ONBOARDING = ("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');"
                   "s.textContent='.onboarding-widget-box,.user-onboarding,[class*=onboarding-]{display:none!important}';"
                   "document.head.appendChild(s)})")


# ---------------- ERPNext REST ----------------

class ErpApi:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=ERP, timeout=60, headers={
            "Authorization": f"token {os.environ['ERPNEXT_API_KEY']}:{os.environ['ERPNEXT_API_SECRET']}",
            "Accept": "application/json"})

    def get(self, doctype: str, name: str) -> dict:
        r = self.h.get(f"/api/resource/{doctype}/{name}")
        r.raise_for_status()
        return r.json()["data"]

    def list(self, doctype: str, fields: list[str], filters: Optional[list] = None, limit: int = 200,
             order_by: Optional[str] = None) -> list[dict]:
        import json
        params = {"fields": json.dumps(fields), "limit_page_length": limit}
        if filters:
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by
        r = self.h.get(f"/api/resource/{doctype}", params=params)
        r.raise_for_status()
        return r.json()["data"]

    def insert(self, doc: dict) -> dict:
        r = self.h.post(f"/api/resource/{doc['doctype']}", json=doc)
        if r.status_code >= 400:
            raise RuntimeError(f"insert {doc['doctype']}: {r.status_code} {r.text[:400]}")
        return r.json()["data"]

    def update(self, doctype: str, name: str, patch: dict) -> dict:
        r = self.h.put(f"/api/resource/{doctype}/{name}", json=patch)
        if r.status_code >= 400:
            raise RuntimeError(f"update {doctype}/{name}: {r.status_code} {r.text[:400]}")
        return r.json()["data"]

    def delete(self, doctype: str, name: str) -> None:
        self.h.delete(f"/api/resource/{doctype}/{name}")

    def set_user(self, user: str, **patch: Any) -> None:
        self.update("User", user, patch)


async def erp_login(page: Any, user: str, pwd: str = PWD) -> None:
    await page.goto(f"{ERP}/login")
    await page.fill("#login_email", user)
    await page.fill("#login_password", pwd)
    await page.click(".btn-login")
    await page.wait_for_url(lambda u: "/app" in u or "/desk" in u, timeout=30000)


async def erp_settle(page: Any, ms: int = 900) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(ms)
    await page.add_style_tag(content=".user-onboarding,.onboarding-widget-box,[class*='onboarding-']{display:none!important}")


async def erp_visible_fields(page: Any, fieldnames: list[str]) -> list[str]:
    return await page.evaluate("""(fns) => fns.filter(fn => {
        const els = [...document.querySelectorAll(`.frappe-control[data-fieldname="${fn}"]`)];
        return els.some(el => { if (!el.offsetParent) return false; const r = el.getBoundingClientRect();
            return r.width > 0 && r.top >= 40 && r.bottom <= window.innerHeight - 5; }); })""", fieldnames)


async def erp_labels(page: Any, doctype: str, fieldnames: list[str]) -> dict[str, str]:
    """The app's own (translated) label for each field."""
    return await page.evaluate("""([dt, fns]) => Object.fromEntries(fns.map(f => {
        let l = ''; try { l = frappe.meta.get_label(dt, f) } catch(e) {}
        try { l = __(l) } catch(e) {}
        return [f, l]; }))""", [doctype, fieldnames])


async def erp_tr(page: Any, s: str) -> str:
    try:
        return await page.evaluate("(s) => __(s)", s)
    except Exception:  # noqa: BLE001
        return s


async def erp_fmt_number(page: Any, v: float) -> str:
    try:
        return await page.evaluate("(v) => format_number(v, null, 2)", v)
    except Exception:  # noqa: BLE001
        return f"{v:,.2f}"


# ---------------- Zammad REST ----------------

class ZamApi:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=ZAM, timeout=60, auth=ZADMIN, headers={"Accept": "application/json"})
        self._lookup: dict[str, dict[int, str]] = {}

    def req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.h.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def ticket(self, tid: int) -> dict:
        return self.req("GET", f"/api/v1/tickets/{tid}", params={"expand": "true"})

    def tickets(self) -> list[dict]:
        return self.req("GET", "/api/v1/tickets", params={"expand": "true", "per_page": 200})

    def articles(self, tid: int) -> list[dict]:
        return self.req("GET", f"/api/v1/ticket_articles/by_ticket/{tid}")

    def user_by_email(self, email: str) -> dict:
        res = self.req("GET", "/api/v1/users/search", params={"query": email, "limit": 5})
        return next(u for u in res if (u.get("email") or "").lower() == email.lower())

    def set_prefs(self, email: str, **prefs: Any) -> None:
        u = self.user_by_email(email)
        merged = {**(u.get("preferences") or {}), **prefs}
        self.req("PUT", f"/api/v1/users/{u['id']}", json={"preferences": merged})

    def create_ticket(self, title: str, customer: str, body: str, group: str = "Tier 1 Support",
                      priority: str = "2 normal", created_days_ago: int = 0) -> dict:
        t = self.req("POST", "/api/v1/tickets", json={
            "title": title, "group": group, "customer_id": f"guess:{customer}", "priority": priority, "state": "new",
            "article": {"subject": title, "body": body, "type": "web", "sender": "Customer", "internal": False,
                        "content_type": "text/plain"}})
        return t

    def update_ticket(self, tid: int, **patch: Any) -> dict:
        return self.req("PUT", f"/api/v1/tickets/{tid}", json=patch)

    def delete_ticket(self, tid: int) -> None:
        try:
            self.req("DELETE", f"/api/v1/tickets/{tid}")
        except Exception:  # noqa: BLE001
            pass


async def zam_login(page: Any, user: str, pwd: str = PWD) -> None:
    await page.goto(f"{ZAM}/#login")
    await page.wait_for_selector("input[name=username]", timeout=30000)
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", pwd)
    await page.click("button[type=submit]")
    await page.wait_for_selector(".navigation, .js-avatar, #navigation", timeout=30000)
    await page.wait_for_timeout(1500)
    await zam_close_tour(page)


async def zam_close_tour(page: Any) -> None:
    for _ in range(4):
        btn = page.locator(".modal--clue .js-close, .modal--clue .js-next, .popover .js-close").first
        if await btn.count() and await btn.is_visible():
            try:
                await btn.click(timeout=2000)
            except Exception:  # noqa: BLE001
                break
            await page.wait_for_timeout(300)
        else:
            break


async def zam_settle(page: Any, ms: int = 1200) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(ms)
    await zam_close_tour(page)


async def zam_open_ticket(page: Any, tid: int) -> None:
    await page.goto(f"{ZAM}/#ticket/zoom/{tid}")
    await page.wait_for_selector(".ticketZoom-header, .ticket-title", timeout=20000)
    await zam_settle(page)


async def zam_open_overview(page: Any, view: str = "all_open") -> None:
    await page.goto(f"{ZAM}/#ticket/view/{view}")
    await page.wait_for_selector(".table, .js-tableBody, table", timeout=20000)
    await zam_settle(page)


def _active(page: Any, sel: str) -> Any:
    return page.locator(f".content.active {sel}, .tabsSidebar:visible {sel}").first


async def zam_select(page: Any, attr: str, label: str) -> None:
    """Native select in the ticket sidebar (state_id, priority_id, owner_id)."""
    sel = page.locator(f".content.active [data-attribute-name={attr}] select").first
    opts = await sel.evaluate("s => [...s.options].map(o => [o.value, o.textContent.trim()])")
    want = label.casefold()
    val = next((v for v, t in opts if t.casefold() == want), None) or \
        next((v for v, t in opts if want in t.casefold()), None)
    if val is None:
        raise RuntimeError(f"no option {label!r} in {attr}: {opts}")
    await sel.select_option(value=val)
    await page.wait_for_timeout(500)


async def zam_set_group(page: Any, group: str) -> None:
    box = page.locator(".content.active [data-attribute-name=group_id] .js-input, "
                       ".content.active [data-attribute-name=group_id] input.searchableSelect-main").first
    await box.click()
    await page.wait_for_timeout(400)
    opt = page.locator(f".content.active [data-attribute-name=group_id] li.js-option[data-display-name='{group}']").first
    await opt.click()
    await page.wait_for_timeout(500)


async def zam_type_note(page: Any, text: str, internal: bool = True, on_typing: Any = None) -> None:
    body = page.locator(".content.active .articleNewEdit-body").first
    await body.click(force=True)
    await body.focus()
    await page.wait_for_timeout(500)
    if on_typing:
        await on_typing()
    await body.type(text, delay=20)
    await page.wait_for_timeout(300)
    # internal toggle: the hidden input [name=internal] holds the current visibility
    cur = await page.evaluate("() => { const e = document.querySelector('.content.active [name=internal]');"
                              " return e ? e.value : null; }")
    if cur is not None and (cur == "true") != internal:
        await page.locator(".content.active .js-toggleVisibility").first.click()
        await page.wait_for_timeout(300)


async def zam_update(page: Any) -> None:
    await page.locator(".content.active .js-submit").first.click()
    await page.wait_for_timeout(1500)


def now() -> float:
    return time.time()

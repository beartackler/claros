"""Playwright driver for the ERPNext test bed (real UI interactions, used by run_e2e.py)."""
from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import Page

ERP = "http://localhost:8080"
PWD = "Claros-Demo-2026!"


async def login(page: Page, user: str) -> None:
    await page.goto(f"{ERP}/login")
    await page.fill("#login_email", user)
    await page.fill("#login_password", PWD)
    await page.click(".btn-login")
    await page.wait_for_url(lambda u: "/app" in u or "/desk" in u, timeout=20000)
    await hide_onboarding(page)


async def hide_onboarding(page: Page) -> None:
    await page.add_style_tag(content=".user-onboarding, .onboarding-widget-box, .onboarding-sidebar,"
                                     "[class*='onboarding-']{display:none !important}")


async def settle(page: Page, ms: int = 700) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(ms)


async def open_list(page: Page, doctype_slug: str = "purchase-invoice", query: str = "") -> None:
    await page.goto(f"{ERP}/app/{doctype_slug}{query}")
    await settle(page)
    await hide_onboarding(page)


async def open_doc(page: Page, name: str, doctype_slug: str = "purchase-invoice") -> None:
    """Click the record in the list view if visible, else navigate."""
    link = page.locator(f".list-row a[data-name='{name}']:visible, .list-row a[href$='/{name}']:visible").first
    if await link.count():
        await link.click()
    else:
        await page.goto(f"{ERP}/app/{doctype_slug}/{name}")
    await page.wait_for_function("window.cur_frm && cur_frm.doc && cur_frm.doc.name === %r" % name, timeout=15000)
    await settle(page)
    await hide_onboarding(page)


async def open_row(page: Page, table: str = "items", idx: int = 1) -> None:
    btn = page.locator(f".frappe-control[data-fieldname={table}] .grid-row[data-idx='{idx}'] .btn-open-row").first
    await btn.scroll_into_view_if_needed()
    await btn.click()
    await page.wait_for_selector(".grid-row-open", timeout=8000)
    await settle(page, 500)


async def scroll_to_field(page: Page, fieldname: str, in_row: bool = False) -> None:
    scope = ".grid-row-open " if in_row else ""
    el = page.locator(f"{scope}.frappe-control[data-fieldname={fieldname}]").first
    await el.scroll_into_view_if_needed()
    await page.evaluate("(sel) => { const e = document.querySelector(sel); if (e) e.scrollIntoView({block:'center'}); }",
                        f"{scope}.frappe-control[data-fieldname={fieldname}]")
    await page.wait_for_timeout(400)


async def set_link(page: Page, fieldname: str, value: str, *, in_row: bool = False, pick: Optional[str] = None,
                   on_typing=None) -> None:
    """Type into a Link field like a person and pick the suggestion."""
    scope = ".grid-row-open " if in_row else ""
    inp = page.locator(f"{scope}.frappe-control[data-fieldname={fieldname}] input").first
    await scroll_to_field(page, fieldname, in_row)
    await inp.click()
    await inp.press("ControlOrMeta+a")
    await inp.press("Backspace")
    if on_typing:
        await on_typing()
    await inp.type(value, delay=45)
    await page.wait_for_timeout(900)
    opt = page.locator(f"{scope}.frappe-control[data-fieldname={fieldname}] .awesomplete li").filter(
        has_text=pick or value).first
    if await opt.count():
        await opt.click()
    else:
        await inp.press("Enter")
    await page.wait_for_timeout(500)
    await page.keyboard.press("Tab")
    await settle(page, 500)


async def set_text(page: Page, fieldname: str, value: str, on_typing=None) -> None:
    el = page.locator(f".frappe-control[data-fieldname={fieldname}] textarea, "
                      f".frappe-control[data-fieldname={fieldname}] input").first
    await scroll_to_field(page, fieldname)
    await el.click()
    if on_typing:
        await on_typing()
    await el.type(value, delay=30)
    await page.keyboard.press("Tab")
    await settle(page, 400)


async def check(page: Page, fieldname: str, on: bool = True) -> None:
    el = page.locator(f".frappe-control[data-fieldname={fieldname}] input[type=checkbox]").first
    await scroll_to_field(page, fieldname)
    if (await el.is_checked()) != on:
        await el.click()
    await settle(page, 400)


async def expand_section(page: Page, label: str) -> None:
    head = page.locator(".section-head.collapsible, .section-head").filter(has_text=label).first
    if await head.count():
        await head.scroll_into_view_if_needed()
        if "collapsed" in (await head.get_attribute("class") or ""):
            await head.click()
        await settle(page, 400)


async def close_row(page: Page) -> None:
    await page.keyboard.press("Escape")
    await settle(page, 500)


async def save(page: Page) -> None:
    await page.keyboard.press("ControlOrMeta+s")
    await page.wait_for_timeout(600)  # toast shows briefly


async def workflow_action(page: Page, action: str) -> None:
    await page.locator("button:visible").filter(has_text="Actions").first.click()
    await page.wait_for_timeout(400)
    await page.locator(".actions-btn-group .dropdown-menu a, .dropdown-menu.show a").filter(has_text=action).first.click()
    await page.wait_for_timeout(800)
    # confirm dialog if any
    yes = page.locator(".modal.show .btn-primary").filter(has_text="Yes")
    if await yes.count():
        await yes.first.click()
    await settle(page, 900)


async def doc_state(page: Page) -> dict:
    return await page.evaluate("""() => { const d = cur_frm && cur_frm.doc; if (!d) return {};
        return {name: d.name, workflow_state: d.workflow_state, docstatus: d.docstatus, on_hold: d.on_hold,
                dirty: !!d.__unsaved,
                items: (d.items||[]).map(i => ({item: i.item_code, ea: i.expense_account, cc: i.cost_center,
                                                 amount: i.amount}))}; }""")


async def set_grid_link(page: Page, fieldname: str, value: str, *, table: str = "items", idx: int = 1,
                        pick: Optional[str] = None, on_typing=None) -> None:
    """Inline-edit a Link cell in a child-table grid (columns configured via GridView user settings)."""
    row = page.locator(f".frappe-control[data-fieldname={table}] .grid-row[data-idx='{idx}']").first
    await row.scroll_into_view_if_needed()
    await row.locator(f"[data-fieldname={fieldname}]").first.click()
    await page.wait_for_timeout(400)
    inp = row.locator(f".frappe-control[data-fieldname={fieldname}] input").first
    await inp.click()
    await inp.press("ControlOrMeta+a")
    await inp.press("Backspace")
    if on_typing:
        await on_typing()
    await inp.type(value, delay=45)
    await page.wait_for_timeout(1000)
    opt = row.locator(f".frappe-control[data-fieldname={fieldname}] .awesomplete li").filter(has_text=pick or value).first
    if await opt.count():
        await opt.click()
    else:
        await inp.press("Enter")
    await page.wait_for_timeout(400)
    # click outside the grid to commit the cell
    await page.locator(".frappe-control[data-fieldname=items] .grid-heading-row, .form-section .section-head").first.click(
        position={"x": 5, "y": 5})
    await settle(page, 600)


async def configure_grid(page: Page) -> None:
    """User column preference for the items grid (what a user sets once via the grid gear)."""
    await page.evaluate("""async () => {
      const cols = [{fieldname:'item_code',columns:2},{fieldname:'qty',columns:1},{fieldname:'amount',columns:2},
                    {fieldname:'expense_account',columns:3},{fieldname:'cost_center',columns:2}];
      await frappe.model.user_settings.save('Purchase Invoice', 'GridView', {'Purchase Invoice Item': cols});
    }""")


async def add_tag(page: Page, tag: str, on_typing=None) -> None:
    await page.locator(".form-tags .add-tags-btn, .tags-label + *, .sidebar-label:has-text('Tags')").first.click()
    await page.wait_for_timeout(500)
    inp = page.locator(".form-tag-row input, .tags-input input, .form-tags input").first
    if on_typing:
        await on_typing()
    await inp.type(tag, delay=50)
    await inp.press("Enter")
    await page.wait_for_timeout(500)
    await page.keyboard.press("Escape")
    await settle(page, 600)


async def add_comment(page: Page, text: str, on_typing=None) -> None:
    await page.locator(".form-tabs .nav-link").filter(has_text="More Info").first.click()
    await settle(page, 500)
    box = page.locator(".comment-input-wrapper .ql-editor, .comment-box .ql-editor").first
    await box.scroll_into_view_if_needed()
    await box.click()
    if on_typing:
        await on_typing()
    await box.type(text, delay=25)
    await page.wait_for_timeout(300)
    await page.locator(".comment-input-wrapper button, .btn-comment").filter(has_text="Comment").first.click()
    await settle(page, 900)


async def details_tab(page: Page) -> None:
    await page.locator(".form-tabs .nav-link").filter(has_text="Details").first.click()
    await settle(page, 400)

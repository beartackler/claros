"""Golden perception set: capture ERPNext + Zammad keyframes with REST-API ground truth.

    cd server && .venv/bin/python ../infra/evals/golden.py [erp|zammad|all]

Writes data/evals/golden/<app>/<id>.jpg + <id>.json. Frames are encoded exactly like the Claros
client (PNG screenshot @2x → long edge 1600 → JPEG q75). Variation: browser zoom (125% / 80% emulated
by the CSS viewport), dark theme, German and Russian UI language. Ground truth = REST doc fields;
the DOM is used only for "is the field in the viewport" and the app's own translation function.
All app-side settings changed here (user language / theme) are restored in `finally`.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402,F401
from apps import (ERP, HIDE_ONBOARDING, ErpApi, ZamApi, erp_fmt_number, erp_labels, erp_login,  # noqa: E402
                  erp_settle, erp_tr, erp_visible_fields, zam_close_tour, zam_login, zam_open_overview,
                  zam_open_ticket, zam_settle)
from common import GOLDEN, log  # noqa: E402

VIEWPORTS = {"100": (1440, 900), "125": (1152, 720), "80": (1800, 1125)}


def encode(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    sc = min(1.0, 1600 / max(w, h))
    if sc < 1:
        im = im.resize((round(w * sc), round(h * sc)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def save(app: str, fid: str, jpeg: bytes, label: dict) -> None:
    d = GOLDEN / app
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fid}.jpg").write_bytes(jpeg)
    (d / f"{fid}.json").write_text(json.dumps(label, indent=1, ensure_ascii=False))


# ======================= ERPNext =======================

PI_FIELDS = ["supplier", "company", "posting_date", "due_date", "bill_no", "bill_date"]
DT_SLUG = {"Purchase Invoice": "purchase-invoice", "Supplier": "supplier", "Item": "item"}
FORM_FIELDS = {"Supplier": ["supplier_name", "supplier_group", "country", "supplier_type"],
               "Item": ["item_code", "item_name", "item_group", "stock_uom"]}

# (cfg, kind, doctype, name|query, scroll_items)
ERP_PLAN: list[tuple[str, str, str, str, bool]] = []
for cfg, specs in {
    "en": [("list", "Purchase Invoice", "", False), ("list", "Purchase Invoice", "?supplier=Velt%20Industrieservice%20GmbH", False),
           ("list", "Supplier", "", False), ("list", "Item", "", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00027", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00028", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00029", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00030", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00031", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00001", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00004", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00010", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00027", True), ("form", "Purchase Invoice", "ACC-PINV-2026-00031", True),
           ("form", "Supplier", "Feldmark Maschinentechnik GmbH", False), ("form", "Item", "FA-COMP-15KW", False)],
    "z125": [("list", "Purchase Invoice", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00027", False),
             ("form", "Purchase Invoice", "ACC-PINV-2026-00029", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00030", True),
             ("list", "Supplier", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00010", False)],
    "z80": [("list", "Purchase Invoice", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00028", False),
            ("form", "Purchase Invoice", "ACC-PINV-2026-00031", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00004", True),
            ("list", "Item", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00001", False)],
    "dark": [("list", "Purchase Invoice", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00027", False),
             ("form", "Purchase Invoice", "ACC-PINV-2026-00030", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00029", False),
             ("form", "Purchase Invoice", "ACC-PINV-2026-00027", True), ("form", "Supplier", "Velt Industrieservice GmbH", False)],
    "de": [("list", "Purchase Invoice", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00027", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00028", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00031", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00029", True), ("form", "Item", "FA-LASER-ALIGN", False)],
    "ru": [("list", "Purchase Invoice", "", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00027", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00030", False), ("form", "Purchase Invoice", "ACC-PINV-2026-00029", False),
           ("form", "Purchase Invoice", "ACC-PINV-2026-00031", True), ("list", "Supplier", "", False)],
}.items():
    for kind, dt, name, scroll in specs:
        ERP_PLAN.append((cfg, kind, dt, name, scroll))


def cfg_parts(cfg: str) -> tuple[str, str, str]:
    """→ (zoom, theme, lang)"""
    return ({"z125": "125", "z80": "80"}.get(cfg, "100"), "dark" if cfg == "dark" else "light",
            cfg if cfg in ("de", "ru") else "en")


async def erp_truth(page: Any, api: ErpApi, kind: str, dt: str, name: str, scroll: bool) -> dict:
    lab: dict[str, Any] = {"app": "ERPNext", "kind": kind, "entity_type": dt,
                           "entity_type_l10n": await erp_tr(page, dt), "entity_id": None, "status": [],
                           "fields": [], "numbers": []}
    if kind == "list":
        return lab
    doc = api.get(dt, name)
    lab["entity_id"] = doc["name"]
    if dt == "Purchase Invoice":
        lab["entity_alias"] = [doc["name"], doc.get("bill_no")]
        st = [s for s in {doc.get("status"), doc.get("workflow_state")} if s]
        if doc.get("docstatus") == 0:
            st.append("Draft")
        lab["status"] = sorted({x for s in st for x in (s, await erp_tr(page, s))})
        fns = PI_FIELDS
    else:
        lab["entity_alias"] = [doc["name"], doc.get("supplier_name") or doc.get("item_name")]
        fns = FORM_FIELDS[dt]
    vis = await erp_visible_fields(page, fns + ["items"])
    labels = await erp_labels(page, dt, fns)
    for fn in fns:
        if fn in vis and doc.get(fn) not in (None, ""):
            lab["fields"].append({"key": fn, "labels": sorted({labels.get(fn) or fn, fn.replace("_", " ")}),
                                  "value": doc[fn]})
    if dt == "Purchase Invoice" and "items" in vis and doc.get("items"):
        it = doc["items"][0]
        il = await erp_labels(page, "Purchase Invoice Item", ["item_code", "qty", "rate", "amount"])
        for fn in ("item_code", "amount"):
            lab["fields"].append({"key": f"items.{fn}", "labels": sorted({il.get(fn) or fn, fn}),
                                  "value": it[fn], "table": True})
        for v in (it["amount"],):
            lab["numbers"].append(await erp_fmt_number(page, v))
    return lab


async def capture_erp() -> list[str]:
    from playwright.async_api import async_playwright
    api = ErpApi()
    admin = api.get("User", "Administrator")
    orig = {"language": admin.get("language") or "en", "desk_theme": admin.get("desk_theme") or "Light"}
    ids: list[str] = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            cur_cfg = None
            page = ctx = None
            n = 0
            for cfg, kind, dt, name, scroll in ERP_PLAN:
                zoom, theme, lang = cfg_parts(cfg)
                if cfg != cur_cfg:
                    api.set_user("Administrator", language=lang, desk_theme="Dark" if theme == "dark" else "Light")
                    if ctx:
                        await ctx.close()
                    vw, vh = VIEWPORTS[zoom]
                    ctx = await b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=2,
                                              locale={"de": "de-DE", "ru": "ru-RU"}.get(lang, "en-US"))
                    await ctx.add_init_script(HIDE_ONBOARDING)
                    page = await ctx.new_page()
                    await erp_login(page, "Administrator", "admin")
                    cur_cfg = cfg
                slug = DT_SLUG[dt]
                url = f"{ERP}/app/{slug}{name}" if kind == "list" else f"{ERP}/app/{slug}/{name}"
                await page.goto(url)
                if kind == "form":
                    await page.wait_for_function("window.cur_frm && cur_frm.doc && cur_frm.doc.name === %r" % name,
                                                 timeout=20000)
                await erp_settle(page, 1200)
                if scroll:
                    await page.evaluate("document.querySelector('.frappe-control[data-fieldname=items]')"
                                        ".scrollIntoView({block:'center'})")
                    await page.wait_for_timeout(600)
                await page.mouse.move(5, 5)
                lab = await erp_truth(page, api, kind, dt, name, scroll)
                png = await page.screenshot(type="png")
                n += 1
                fid = f"erp_{n:02d}_{cfg}_{kind}_{slug}" + (f"_{name[-5:]}" if kind == "form" else "") + ("_items" if scroll else "")
                lab |= {"id": fid, "cfg": {"zoom": zoom, "theme": theme, "lang": lang}, "url": url}
                save("erpnext", fid, encode(png), lab)
                ids.append(fid)
                log("erp", fid, len(lab["fields"]), "fields")
            await b.close()
    finally:
        api.set_user("Administrator", **orig)
    return ids


# ======================= Zammad =======================

ZAM_PLAN = {
    "en": [("overview", "all_open"), ("overview", "all_unassigned"), ("ticket", 2), ("ticket", 3), ("ticket", 4),
           ("ticket", 5), ("ticket", 6), ("ticket", 7)],
    "dark": [("overview", "all_open"), ("ticket", 2), ("ticket", 6), ("ticket", 7)],
    "z125": [("overview", "all_open"), ("ticket", 3), ("ticket", 4), ("ticket", 7)],
    "z80": [("ticket", 5)],
    "de": [("overview", "all_open"), ("ticket", 2), ("ticket", 6), ("ticket", 7)],
    "ru": [("overview", "all_open"), ("ticket", 3), ("ticket", 4), ("ticket", 7)],
}
ZLOCALE = {"en": "en-us", "de": "de-de", "ru": "ru"}


async def ztr(page: Any, s: str) -> str:
    try:
        return await page.evaluate("(s) => App.i18n.translatePlain(s)", s)
    except Exception:  # noqa: BLE001
        return s


async def zam_truth(page: Any, api: ZamApi, kind: str, ref: Any) -> dict:
    lab: dict[str, Any] = {"app": "Zammad", "kind": kind, "entity_type": "Ticket",
                           "entity_type_l10n": await ztr(page, "Ticket"), "entity_id": None, "status": [],
                           "fields": [], "numbers": []}
    if kind == "overview":
        lab["entity_type"] = "Ticket"
        return lab
    t = api.ticket(int(ref))
    lab["entity_id"] = str(t["number"])
    lab["entity_alias"] = [str(t["number"]), f"#{t['number']}", t["title"]]
    lab["status"] = sorted({t["state"], await ztr(page, t["state"])})
    owner = t.get("owner") if t.get("owner") not in (None, "-") else None
    for key, label, val in (("group", "Group", t["group"]), ("state", "State", t["state"]),
                            ("priority", "Priority", t["priority"])):
        loc = await ztr(page, label)
        tv = await ztr(page, val) if key in ("state", "priority") else val
        lab["fields"].append({"key": key, "labels": sorted({label, loc}), "value": val, "value_l10n": tv})
    lab["owner"] = owner
    return lab


async def capture_zammad() -> list[str]:
    from playwright.async_api import async_playwright
    api = ZamApi()
    user = "expert@helpdesk.example"
    orig = dict(api.user_by_email(user).get("preferences") or {})
    ids: list[str] = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            n = 0
            for cfg, specs in ZAM_PLAN.items():
                zoom, theme, lang = cfg_parts(cfg)
                api.set_prefs(user, locale=ZLOCALE[lang], theme="dark" if theme == "dark" else "light", intro=True)
                vw, vh = VIEWPORTS[zoom]
                ctx = await b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=2,
                                          color_scheme="dark" if theme == "dark" else "light")
                page = await ctx.new_page()
                await zam_login(page, user)
                for kind, ref in specs:
                    if kind == "overview":
                        await zam_open_overview(page, str(ref))
                    else:
                        await zam_open_ticket(page, int(ref))
                    await zam_close_tour(page)
                    await page.mouse.move(700, 880)
                    await page.wait_for_timeout(400)
                    lab = await zam_truth(page, api, kind, ref)
                    png = await page.screenshot(type="png")
                    n += 1
                    fid = f"zam_{n:02d}_{cfg}_{kind}_{ref}"
                    lab |= {"id": fid, "cfg": {"zoom": zoom, "theme": theme, "lang": lang}}
                    save("zammad", fid, encode(png), lab)
                    ids.append(fid)
                    log("zam", fid)
                await ctx.close()
            await b.close()
    finally:
        api.set_prefs(user, **{**orig, "locale": orig.get("locale") or "en-us", "theme": orig.get("theme") or "light"})
    return ids


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("erp", "all"):
        await capture_erp()
    if which in ("zammad", "all"):
        await capture_zammad()


if __name__ == "__main__":
    asyncio.run(main())

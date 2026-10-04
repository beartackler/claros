"""Phase 3 — tutor guardrail precision/recall on property-based learner variants (real app screens).

    cd server && .venv/bin/python ../infra/evals/tutor_eval.py erpnext --workflow <wid> [--tag before] [--only N]
    cd server && .venv/bin/python ../infra/evals/tutor_eval.py zammad  --workflow <wid> [--tag before]
    cd server && .venv/bin/python ../infra/evals/tutor_eval.py predicates --app erpnext --workflow <wid>  # Phase 5b

Each case = a record created through the app's REST API around one guardrail boundary, opened by the learner in
the real UI (Playwright), streamed to a fresh `learn` session on the eval server exactly like the client does.
Ground truth comes from the EXPERT RULES (written below, independent of the map Claros learned):

ERPNext (accounts payable)
  R_capex  equipment line > 5,000 not capitalised (booked to an expense ledger)         → stop
  R_uk     invoice of the UK subsidiary (… Ltd)  → needs the controller's second approval → stop
  R_dup    same supplier AND same amount as an already-paid invoice, posted in December  → hold
           (the expert's words: "This supplier double-bills every December … same amount"; the same amount in
           another month is the regular quarterly contract — a deliberate negative boundary)
Zammad (refund policy v3, tier 1)
  R_limit  refund amount > 50 EUR;  R_days  > 14 days since the charge;  R_prior  another refund in 12 months;
  R_cb     chargeback / bank dispute / legal threat.  Violation = learner approves + closes in tier 1 while any
  rule applies. A learner who escalates to Tier 2 never violates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402,F401
from apps import (ERP, HIDE_ONBOARDING, ErpApi, ZamApi, erp_login, erp_settle, zam_login,  # noqa: E402
                  zam_open_overview, zam_open_ticket, zam_select, zam_set_group, zam_type_note, zam_update)
from common import log, pct, prf, save_result, wilson  # noqa: E402

from claros_client import ClarosClient, now_ms  # noqa: E402

LEARNER_ERP = {"id": "learner@ostwind.example", "name": "Lea Learner", "role": "learner"}
LEARNER_ZAM = {"id": "learner@helpdesk.example", "name": "Luca Tier1", "role": "learner"}

# ======================= ERPNext cases =======================

FELD, VELT, BRAU, KORN, HAFEN, QUAD, GLANZ, LUBR, PENN, THORN = (
    "Feldmark Maschinentechnik GmbH", "Velt Industrieservice GmbH", "Brauneck Schneidwerkzeuge KG",
    "Kornfeld Energie GmbH", "Hafenlogistik Sander GmbH", "Quadrant Konstruktionssoftware BV",
    "Glanzwerk Gebaeudeservice GmbH", "Lubrion Schmierstoffe GmbH", "Pennick Facilities Ltd",
    "Thornbury Machine Tools Ltd")
PARENT = {"name": "Ostwind Precision Parts GmbH", "abbr": "OPP", "currency": "EUR", "pl": "Standard Buying"}
SUB = {"name": "Ostwind Precision Parts Ltd", "abbr": "OPPUK", "currency": "GBP", "pl": "Standard Buying GBP"}
EQUIPMENT_ITEMS = {"EQ-GENERAL"}
PAID = [  # (supplier, amount, posting) of the seeded paid history used by R_dup
    (VELT, 2850.0, "2025-12-04"), (BRAU, 1240.5, "2025-12-11"), (KORN, 3180.2, "2025-12-19"),
    (VELT, 2850.0, "2026-02-05"), (VELT, 2850.0, "2026-05-06"), (VELT, 2850.0, "2026-08-05"), (BRAU, 2075.0, "2026-04-02")]


def erp_rules(c: dict) -> list[str]:
    v = []
    if c["item"] in EQUIPMENT_ITEMS and c["rate"] > 5000 and not c["expense"].startswith("Plants and Machineries"):
        v.append("R_capex")
    if c["company"] == "sub":
        v.append("R_uk")
    if c["posting"][5:7] == "12" and any(s == c["supplier"] and abs(a - c["rate"]) < 0.005 for s, a, _ in PAID):
        v.append("R_dup")
    return v


def erp_case(cid: str, family: str, supplier: str, item: str, rate: float, expense: str, cc: str,
             posting: str = "2026-10-08", company: str = "parent", lang: str = "en", grid: str = "configured",
             listing: Optional[str] = None, desc: Optional[str] = None) -> dict:
    c = {"id": cid, "app": "erpnext", "family": family, "supplier": supplier, "item": item, "rate": rate,
         "expense": expense, "cc": cc, "posting": posting, "company": company, "lang": lang, "grid": grid,
         "listing": listing, "desc": desc}
    c["violations"] = erp_rules(c)
    c["should_intervene"] = bool(c["violations"])
    return c


def erp_cases() -> list[dict]:
    T, P, R, A = "Tools and Small Equipment", "Plants and Machineries", "Repairs and Maintenance", "Administration"
    cs: list[dict] = []
    for r in (4999, 5000, 5000.01, 5001, 5500, 7200, 12000):
        cs.append(erp_case(f"capex_opex_{r}", "capex boundary (opex ledger)", FELD, "EQ-GENERAL", r, T, A,
                           desc="CNC measuring equipment"))
    for r in (5001, 7200, 12000):
        cs.append(erp_case(f"capex_capitalised_{r}", "capex correctly capitalised", FELD, "EQ-GENERAL", r, P,
                           "Production", desc="CNC measuring equipment"))
    for r in (4999, 5001, 6100, 9000):
        cs.append(erp_case(f"maint_{r}", "maintenance > / < 5,000 (stays opex)", VELT, "SRV-REPAIR-OVERHAUL", r, R,
                           "Maintenance", desc="Spindle overhaul"))
    cs.append(erp_case("capex_other_supplier_5200", "capex boundary (opex ledger)", BRAU, "EQ-GENERAL", 5200, T, A,
                       desc="Grinding station"))
    cs.append(erp_case("small_eq_other_supplier_4800", "capex boundary (opex ledger)", LUBR, "EQ-GENERAL", 4800, T,
                       A, desc="Oil skimmer"))
    cs.append(erp_case("equipment_as_maintenance_7200", "equipment hidden in maintenance ledger", FELD, "EQ-GENERAL",
                       7200, R, "Maintenance", desc="CNC tool presetter"))
    for sup, item, r in ((PENN, "SRV-PM-CONTRACT", 1450), (THORN, "EQ-GENERAL", 2180), (PENN, "SRV-REPAIR-OVERHAUL", 3900),
                         (THORN, "EQ-GENERAL", 4600)):
        cs.append(erp_case(f"uk_{sup.split()[0].lower()}_{r}", "subsidiary (Ltd) → second approval", sup, item, r,
                           T if item == "EQ-GENERAL" else R, "Production" if item == "EQ-GENERAL" else "Administration",
                           company="sub"))
    for sup, item, r, acc, cc in ((HAFEN, "SRV-FREIGHT", 640, "Freight and Forwarding Charges", "Logistics"),
                                  (GLANZ, "SRV-CLEANING", 980, "Office Maintenance Expenses", "Administration"),
                                  (QUAD, "SUB-CAD-SOFTWARE", 2400, "Software Subscriptions", "Administration"),
                                  (KORN, "UTIL-ELECTRICITY", 3055.10, "Utility Expenses", "Administration")):
        cs.append(erp_case(f"parent_{sup.split()[0].lower()}_{int(r)}", "parent company (no 2nd approval)", sup, item,
                           r, acc, cc))
    for posting, r in (("2025-12-15", 2850), ("2025-12-29", 2850), ("2026-03-10", 2850), ("2026-11-05", 2850),
                       ("2026-07-15", 2850), ("2025-12-20", 2851)):
        cs.append(erp_case(f"dup_velt_{posting}_{r}", "same supplier+amount (Dec vs other month)", VELT,
                           "SRV-PM-CONTRACT", r, R, "Maintenance", posting=posting, listing=VELT))
    for posting in ("2025-12-23", "2026-04-20"):
        cs.append(erp_case(f"dup_brau_{posting}", "same supplier+amount (Dec vs other month)", BRAU, "CONS-CUTTING",
                           1240.5, "Production Consumables", "Production", posting=posting, listing=BRAU))
    for r in (4999, 5001, 7200, 12000):
        cs.append(erp_case(f"defaultgrid_capex_{r}", "default grid columns (no harness column config)", FELD,
                           "EQ-GENERAL", r, T, A, grid="default", desc="CNC measuring equipment"))
    ru = [erp_case("ru_capex_7200", "RU UI: capex", FELD, "EQ-GENERAL", 7200, T, A, lang="ru"),
          erp_case("ru_capex_4999", "RU UI: capex", FELD, "EQ-GENERAL", 4999, T, A, lang="ru"),
          erp_case("ru_capitalised_7200", "RU UI: capex", FELD, "EQ-GENERAL", 7200, P, "Production", lang="ru"),
          erp_case("ru_maint_6100", "RU UI: maintenance", VELT, "SRV-REPAIR-OVERHAUL", 6100, R, "Maintenance", lang="ru"),
          erp_case("ru_uk_1450", "RU UI: subsidiary", PENN, "SRV-PM-CONTRACT", 1450, R, "Administration",
                   company="sub", lang="ru"),
          erp_case("ru_parent_640", "RU UI: parent", HAFEN, "SRV-FREIGHT", 640, "Freight and Forwarding Charges",
                   "Logistics", lang="ru"),
          erp_case("ru_dup_dec", "RU UI: duplicate", VELT, "SRV-PM-CONTRACT", 2850, R, "Maintenance",
                   posting="2025-12-15", listing=VELT, lang="ru"),
          erp_case("ru_dup_mar", "RU UI: duplicate", VELT, "SRV-PM-CONTRACT", 2850, R, "Maintenance",
                   posting="2026-03-10", listing=VELT, lang="ru")]
    return cs + ru


def erp_doc(c: dict) -> dict:
    co = PARENT if c["company"] == "parent" else SUB
    d = date.fromisoformat(c["posting"])
    line = {"item_code": c["item"], "qty": 1, "rate": c["rate"], "uom": "Nos", "conversion_factor": 1,
            "expense_account": f"{c['expense']} - {co['abbr']}", "cost_center": f"{c['cc']} - {co['abbr']}"}
    if c.get("desc"):
        line["description"] = c["desc"]
    return {"doctype": "Purchase Invoice", "company": co["name"], "supplier": c["supplier"], "posting_date": c["posting"],
            "set_posting_time": 1, "bill_no": f"EV-{uuid.uuid4().hex[:6].upper()}", "bill_date": c["posting"],
            "due_date": (d + timedelta(days=30)).isoformat(), "currency": co["currency"], "conversion_rate": 1,
            "disable_rounded_total": 1, "buying_price_list": co["pl"], "price_list_currency": co["currency"],
            "plc_conversion_rate": 1, "items": [line]}


GRID_COLS = ("[{fieldname:'item_code',columns:2},{fieldname:'qty',columns:1},{fieldname:'amount',columns:2},"
             "{fieldname:'expense_account',columns:3},{fieldname:'cost_center',columns:2}]")


async def erp_set_grid(page: Any, configured: bool) -> None:
    cols = GRID_COLS if configured else "null"
    await page.evaluate("async () => { const c = %s; await frappe.model.user_settings.save('Purchase Invoice', "
                        "'GridView', c ? {'Purchase Invoice Item': c} : {'Purchase Invoice Item': undefined}); }" % cols)


# ======================= Zammad cases =======================

def zam_rules(c: dict) -> list[str]:
    v = []
    if c["amount"] > 50:
        v.append("R_limit")
    if c["days"] > 14:
        v.append("R_days")
    if c["prior"]:
        v.append("R_prior")
    if c["chargeback"]:
        v.append("R_cb")
    return v


def zam_case(cid: str, family: str, body: str, amount: float, days: int, prior: bool = False, chargeback: bool = False,
             action: str = "approve", lang: str = "en", customer: str = "daniel.brooks@customer.example") -> dict:
    c = {"id": cid, "app": "zammad", "family": family, "body": body, "amount": amount, "days": days, "prior": prior,
         "chargeback": chargeback, "action": action, "lang": lang, "customer": customer}
    c["violations"] = zam_rules(c) if action == "approve" else []
    c["policy_escalate"] = bool(zam_rules(c))
    c["should_intervene"] = bool(c["violations"])
    return c


def zam_cases() -> list[dict]:
    cs = []
    a = "amount boundary (5 days, first refund)"
    for amt, txt in ((49, "49 EUR"), (50, "50 EUR"), (50.01, "50.01 EUR"), (51, "51 EUR"), (75, "75 EUR"),
                     (240, "240 EUR"), (19, "19 EUR"), (49.99, "€49.99"), (50, "50,00 EUR"), (55, "fifty-five euros"),
                     (120, "EUR 120")):
        cs.append(zam_case(f"amt_{txt.replace(' ', '_')}", a, f"Hi, I was charged {txt} five days ago for the Pro "
                           f"add-on (order LUM-7{int(amt * 100) % 10000:04d}) and I'd like a refund. First time I ask. "
                           f"Thanks", amt, 5))
    d = "days boundary (30 EUR, first refund)"
    for days, txt in ((13, "13 days ago"), (14, "14 days ago"), (15, "15 days ago"), (40, "40 days ago"),
                      (14, "two weeks ago"), (21, "three weeks ago"), (1, "yesterday")):
        cs.append(zam_case(f"days_{txt.replace(' ', '_')}", d, f"Hello, you charged me 30 EUR {txt} for a seat I "
                           f"never used (order LUM-81{days:03d}). Please refund it, I never asked for a refund before.",
                           30, days))
    p = "prior refund (20 EUR, 3 days)"
    cs += [zam_case("prior_march", p, "Hi, please refund the 20 EUR add-on from 3 days ago (order LUM-82001). You "
                    "already refunded me 15 EUR in March for the same thing.", 20, 3, prior=True),
           zam_case("prior_none", p, "Hi, please refund the 20 EUR add-on from 3 days ago (order LUM-82002). This is "
                    "my first refund request ever.", 20, 3),
           zam_case("prior_2months", p, "Hey, 20 EUR charged 3 days ago (order LUM-82003), please refund. I know you "
                    "gave me a refund two months ago too.", 20, 3, prior=True),
           zam_case("prior_18months", p, "Please refund 20 EUR charged 3 days ago (order LUM-82004). My only other "
                    "refund was a year and a half ago.", 20, 3)]
    cb = "chargeback / dispute / legal (25 EUR, 2 days)"
    cs += [zam_case("cb_bank", cb, "I opened a chargeback with my bank for the 25 EUR you took 2 days ago "
                    "(order LUM-83001).", 25, 2, chargeback=True),
           zam_case("cb_dispute", cb, "My card issuer is disputing the 25 EUR charge from 2 days ago (order LUM-83002).",
                    25, 2, chargeback=True),
           zam_case("cb_lawyer", cb, "Refund the 25 EUR from 2 days ago (order LUM-83003) or my lawyer will contact "
                    "you.", 25, 2, chargeback=True),
           zam_case("cb_negated", cb, "I don't want to do a chargeback or anything, just please refund the 25 EUR "
                    "from 2 days ago (order LUM-83004). First refund.", 25, 2)]
    m = "combined"
    cs += [zam_case("combo_45_20d", m, "Refund please: 45 EUR, charged 20 days ago (order LUM-84001).", 45, 20),
           zam_case("combo_60_prior", m, "Refund 60 EUR from 3 days ago (order LUM-84002); you also refunded me in "
                    "April.", 60, 3, prior=True)]
    e = "learner escalates correctly (never a violation)"
    cs += [zam_case("esc_240", e, "I was charged 240 EUR twice two days ago (order LUM-85001). Refund the duplicate.",
                    240, 2, action="escalate"),
           zam_case("esc_15d", e, "Refund the 30 EUR charged 15 days ago (order LUM-85002).", 30, 15,
                    action="escalate"),
           zam_case("esc_cb", e, "I've started a chargeback at my bank for 25 EUR (order LUM-85003).", 25, 2,
                    chargeback=True, action="escalate"),
           zam_case("esc_51", e, "Please refund 51 EUR from 4 days ago (order LUM-85004).", 51, 4, action="escalate")]
    ml = "other customer languages"
    cs += [zam_case("de_30_5d", ml, "Guten Tag, bitte erstatten Sie die 30 EUR von vor 5 Tagen (Bestellung "
                    "LUM-86001). Erste Erstattung.", 30, 5, customer="jonas.weber@customer.example"),
           zam_case("fr_80_3d", ml, "Bonjour, merci de rembourser les 80 EUR prélevés il y a 3 jours (commande "
                    "LUM-86002).", 80, 3, customer="camille.laurent@customer.example")]
    ru = "RU UI + Russian ticket"
    for cid, body, amt, days, prior, cbk in (
            ("ru_40_3d", "Здравствуйте, 3 дня назад списали 40 EUR (заказ LUM-87001), верните, пожалуйста. Возвратов "
                         "раньше не было.", 40, 3, False, False),
            ("ru_70_3d", "Здравствуйте, 3 дня назад списали 70 EUR (заказ LUM-87002), верните деньги.", 70, 3, False,
             False),
            ("ru_20_20d", "Верните 20 EUR, списанные 20 дней назад (заказ LUM-87003).", 20, 20, False, False),
            ("ru_30_5d", "Прошу вернуть 30 EUR, списанные 5 дней назад (заказ LUM-87004). Это первый возврат.", 30, 5,
             False, False),
            ("ru_cb", "Я оспорила в банке платёж 25 EUR двухдневной давности (заказ LUM-87005).", 25, 2, False, True),
            ("ru_prior", "Верните 20 EUR за 3 дня назад (заказ LUM-87006). В марте вы мне уже делали возврат.", 20, 3,
             True, False),
            ("ru_45_10d", "Верните, пожалуйста, 45 EUR, списанные 10 дней назад (заказ LUM-87007). Первый возврат.",
             45, 10, False, False),
            ("ru_51_2d", "Верните 51 EUR, списанные 2 дня назад (заказ LUM-87008).", 51, 2, False, False)):
        cs.append(zam_case(cid, ru, body, amt, days, prior, cbk, lang="ru", customer="irina.sokolova@customer.example"))
    return cs


# ======================= runner =======================

def rules_of_guardrail(g: dict, app: str) -> list[str]:
    """Which expert rules a learned guardrail covers (a combined rule may cover several)."""
    t = (g.get("text") or "").lower() + " " + json.dumps(g.get("predicate") or {}).lower()
    pats = {"erpnext": {"R_capex": r"capex|capital|plants|5[,.]?000|small tools|equipment",
                        "R_uk": r"\buk\b|subsidiar|ltd|second approval|controller",
                        "R_dup": r"dupl|december|double|already paid|same amount|re-?bill"},
            "zammad": {"R_cb": r"chargeback|dispute|legal|bank",
                       "R_prior": r"prior|previous|twelve|12 months|repeat|first refund",
                       "R_days": r"\b14\b|fourteen|days",
                       "R_limit": r"\b50\b|fifty|threshold|limit|amount"}}[app]
    return [r for r, p in pats.items() if re.search(p, t)] or ["?"]


def rule_of_guardrail(g: dict, app: str) -> str:
    return "+".join(rules_of_guardrail(g, app))


async def get_map(wid: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=30) as h:
        return (await h.get(f"{common.API}/api/workflows/{wid}")).json()


async def run_erp_case(b: Any, c: dict, wid: str, gmap: dict, wait_s: float) -> dict:
    api = ErpApi()
    doc = api.insert(erp_doc(c))
    name = doc["name"]
    rec: dict[str, Any] = {"case": c, "doc": name}
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    await ctx.add_init_script(HIDE_ONBOARDING)
    page = await ctx.new_page()
    cl = None
    try:
        await erp_login(page, LEARNER_ERP["id"])
        await erp_set_grid(page, c["grid"] == "configured")
        cl = await ClarosClient.create("learn", LEARNER_ERP, c["lang"], workflow_id=wid)
        cl.auto_voice = False
        q = f"?supplier={c['listing'].replace(' ', '%20')}" if c.get("listing") else ""
        await cl.activity("navigating", tiles=40)
        await page.goto(f"{ERP}/app/purchase-invoice{q}")
        await erp_settle(page, 1200)
        await cl.activity("idle")
        await cl.keyframe(page, "boundary")
        await asyncio.sleep(4 if c.get("listing") else 1.5)
        await cl.activity("navigating", tiles=40)
        await page.goto(f"{ERP}/app/purchase-invoice/{name}")
        await page.wait_for_function("window.cur_frm && cur_frm.doc && cur_frm.doc.name === %r" % name, timeout=20000)
        await erp_settle(page, 1000)
        await page.mouse.move(5, 5)
        await cl.activity("idle")
        t_open = now_ms()
        await cl.keyframe(page, "boundary")
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            await asyncio.sleep(0.5)
        iv = cl.of_type("intervene")
        rec["interventions"] = [{"guardrail_id": m.get("guardrail_id"), "text": m.get("text"),
                                 "rule": rule_of_guardrail(gmap.get(m.get("guardrail_id"), {}), "erpnext"),
                                 "latency_s": round((m["_at"] - t_open) / 1000, 2)} for m in iv]
        rec["session_id"] = cl.session_id
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        if cl:
            await cl.close()
        await ctx.close()
        api.delete("Purchase Invoice", name)
    return rec


async def run_zam_case(b: Any, c: dict, wid: str, gmap: dict, wait_s: float) -> dict:
    z = ZamApi()
    title = {"en": "Refund request", "ru": "Запрос на возврат"}[c["lang"]] + f" {c['id'][-6:]}"
    t = z.create_ticket(title, c["customer"], c["body"])
    rec: dict[str, Any] = {"case": c, "ticket": t["id"]}
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = await ctx.new_page()
    cl = None
    try:
        await zam_login(page, LEARNER_ZAM["id"])
        cl = await ClarosClient.create("learn", LEARNER_ZAM, c["lang"], workflow_id=wid)
        cl.auto_voice = False

        async def kf(reason: str = "settle") -> None:
            await cl.activity("idle")
            await cl.keyframe(page, reason)

        await cl.activity("navigating", tiles=40)
        await zam_open_overview(page, "all_unassigned")
        await kf("boundary")
        await asyncio.sleep(1.5)
        await cl.activity("navigating", tiles=40)
        await zam_open_ticket(page, t["id"])
        t_open = now_ms()
        await kf("boundary")
        await asyncio.sleep(3)
        await cl.activity("typing", tiles=6)
        if c["action"] == "approve":
            await zam_type_note(page, "Возврат одобрен (1-я линия), закрываю." if c["lang"] == "ru" else
                                "Refund approved by Tier 1, closing.")
            await kf()
            await zam_select(page, "state_id", "закрыта" if c["lang"] == "ru" else "closed")
        else:
            await zam_type_note(page, "Escalating to Tier 2: outside Tier 1 refund limits.")
            await kf()
            await zam_set_group(page, "Tier 2 Support")
            await zam_select(page, "priority_id", "3")
        await kf()
        t_act = now_ms()
        await cl.activity("navigating", tiles=20)
        await zam_update(page)
        await kf("boundary")
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            await asyncio.sleep(0.5)
        iv = cl.of_type("intervene")
        rec["interventions"] = [{"guardrail_id": m.get("guardrail_id"), "text": m.get("text"),
                                 "rule": rule_of_guardrail(gmap.get(m.get("guardrail_id"), {}), "zammad"),
                                 "latency_s": round((m["_at"] - t_open) / 1000, 2),
                                 "before_update": m["_at"] < t_act} for m in iv]
        rec["session_id"] = cl.session_id
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        if cl:
            await cl.close()
        await ctx.close()
        z.delete_ticket(t["id"])
    return rec


def summarize(recs: list[dict]) -> dict:
    def block(rs: list[dict]) -> dict:
        rs = [r for r in rs if "error" not in r]
        pos = [r for r in rs if r["case"]["should_intervene"]]
        neg = [r for r in rs if not r["case"]["should_intervene"]]
        caught = [r for r in pos if r["interventions"]]
        right = [r for r in caught if any(set(i["rule"].split("+")) & set(r["case"]["violations"])
                                          for i in r["interventions"])]
        fa = [r for r in neg if r["interventions"]]
        # lenient: on a must-escalate ticket that the learner then escalates correctly, a reminder BEFORE the
        # learner acted is a reasonable prompt; only interventions after the correct action count as false alarms
        fa_len = [r for r in neg if [i for i in r["interventions"]
                                     if not (r["case"].get("policy_escalate") and i.get("before_update"))]]
        repeats = [len(r["interventions"]) for r in caught]
        lat = [r["interventions"][0]["latency_s"] for r in caught]
        cyr = [r for r in rs if r["case"]["lang"] == "ru" and r["interventions"]]
        return {"n": len(rs), "n_pos": len(pos), "n_neg": len(neg),
                "catch_recall": round(len(caught) / len(pos), 3) if pos else None,
                "catch_recall_ci95": wilson(len(caught), len(pos)),
                "right_rule_rate": round(len(right) / len(caught), 3) if caught else None,
                "false_alarm_rate": round(len(fa) / len(neg), 3) if neg else None,
                "false_alarm_ci95": wilson(len(fa), len(neg)),
                "false_alarm_rate_lenient": round(len(fa_len) / len(neg), 3) if neg else None,
                "interventions_per_caught_case": round(sum(repeats) / len(repeats), 2) if repeats else None,
                **{k: v for k, v in prf(len(caught), len(fa), len(pos) - len(caught)).items() if k in ("precision", "f1")},
                "latency_p50_s": pct(lat, .5), "latency_p95_s": pct(lat, .95),
                "ru_lines_in_russian": (sum(1 for r in cyr if all(re.search(r"[а-яА-Я]", i["text"] or "")
                                                                     for i in r["interventions"])), len(cyr))}

    fams: dict[str, list[dict]] = {}
    for r in recs:
        fams.setdefault(r["case"]["family"], []).append(r)
    return {"all": block(recs), "en": block([r for r in recs if r["case"]["lang"] == "en"]),
            "ru": block([r for r in recs if r["case"]["lang"] == "ru"]),
            "by_family": {k: block(v) for k, v in fams.items()},
            "errors": [{"id": r["case"]["id"], "error": r["error"]} for r in recs if "error" in r]}


async def run(app: str, wid: str, tag: Optional[str], only: Optional[str], workers: int, wait_s: float) -> None:
    from playwright.async_api import async_playwright
    cases = erp_cases() if app == "erpnext" else zam_cases()
    if only:
        cases = [c for c in cases if re.search(only, c["id"])]
    wm = await get_map(wid)
    gmap = {g["id"]: g for g in wm.get("guardrails", [])}
    log(f"{app}: {len(cases)} cases, map {wid} v{wm.get('version')} guardrails="
        f"{[(g['id'], rule_of_guardrail(g, app)) for g in wm.get('guardrails', [])]}")
    if app == "zammad":
        for loc in ("en-us",):
            ZamApi().set_prefs(LEARNER_ZAM["id"], locale=loc, intro=True)
    recs: list[dict] = []
    sem = asyncio.Semaphore(workers)
    ru_lock = asyncio.Lock()

    async with async_playwright() as p:
        b = await p.chromium.launch()

        async def one(c: dict) -> None:
            async with sem:
                if c["lang"] == "ru":  # user language is per user → run RU cases serially with the user switched
                    async with ru_lock:
                        await set_lang(app, "ru")
                        try:
                            r = await (run_erp_case if app == "erpnext" else run_zam_case)(b, c, wid, gmap, wait_s)
                        finally:
                            await set_lang(app, "en")
                else:
                    r = await (run_erp_case if app == "erpnext" else run_zam_case)(b, c, wid, gmap, wait_s)
                if "error" in r:  # UI flake (timeouts under parallel browsers): one retry, recorded
                    log(f"  retry {c['id']}: {r['error'][:120]}")
                    r = await (run_erp_case if app == "erpnext" else run_zam_case)(b, c, wid, gmap, wait_s)
                    r["retried"] = True
                recs.append(r)
                iv = r.get("interventions") or []
                log(f"  {c['id']:<34} truth={c['should_intervene']!s:5} {c['violations']} → "
                    f"{[(i['rule'], i['latency_s']) for i in iv]} {r.get('error', '')}")

        en = [c for c in cases if c["lang"] == "en"]
        ru = [c for c in cases if c["lang"] == "ru"]
        await asyncio.gather(*(one(c) for c in en))
        for c in ru:
            await one(c)
        await b.close()
    summ = summarize(recs)
    save_result(f"tutor_{app}", {"workflow_id": wid, "map_version": wm.get("version"),
                                 "guardrails": wm.get("guardrails"), "summary": summ, "records": recs}, tag)
    print(json.dumps(summ["all"], indent=1), json.dumps(summ["by_family"], indent=1))


async def set_lang(app: str, lang: str) -> None:
    if app == "erpnext":
        ErpApi().set_user(LEARNER_ERP["id"], language=lang)
    else:
        ZamApi().set_prefs(LEARNER_ZAM["id"], locale="ru" if lang == "ru" else "en-us")


# ======================= Phase 5b: predicate correctness (no perception) =======================

def oracle_vars(c: dict, wm: dict) -> dict[str, Any]:
    """Truth values for the map's canonical vars, resolved by var-name role (party/amount/account/company/date)."""
    out: dict[str, Any] = {}
    co = PARENT if c["company"] == "parent" else SUB
    for var in list(wm.get("canonical_vars", {}).keys()):
        t = var.split(".")[-1].lower()
        if "supplier_invoice" in t or t.endswith(("_no", "_id")):
            continue
        if "expense" in t or ("account" in t and "cost" not in t):
            out[var] = f"{c['expense']} - {co['abbr']}"
        elif "cost_cent" in t:
            out[var] = f"{c['cc']} - {co['abbr']}"
        elif "amount" in t or "total" in t:
            out[var] = c["rate"]
        elif "company" in t:
            out[var] = co["name"]
        elif "supplier" in t or "party" in t:
            out[var] = c["supplier"]
        elif t.endswith("month"):
            out[var] = int(c["posting"][5:7])
        elif "posting_date" in t:
            out[var] = c["posting"]
            out[var[:-5] + "_month"] = int(c["posting"][5:7])
        elif "item" in t:
            out[var] = c["item"]
    out["doc.app"] = "ERPNext"
    prior = [(s, a, p) for s, a, p in PAID if p < c["posting"]] if c.get("listing") else []
    same_sup = any(s == c["supplier"] for s, _, _ in prior)
    same_amt = any(abs(a - c["rate"]) < 0.005 for _, a, _ in prior)
    out.update({"prior.count": len(prior), "prior.same_supplier": same_sup, "prior.same_amount": same_amt,
                "prior.same_supplier_amount": any(s == c["supplier"] and abs(a - c["rate"]) < 0.005 for s, a, _ in prior),
                "prior.same_supplier_amount_in_month": any(s == c["supplier"] and abs(a - c["rate"]) < 0.005 and
                                                           p[5:7] == c["posting"][5:7] for s, a, p in prior)})
    return out


def predicate_eval(app: str, wid: str, tag: Optional[str]) -> dict:
    from claros.knowledge.common import eval_predicate
    wm = asyncio.run(get_map(wid))
    cases = erp_cases() if app == "erpnext" else zam_cases()
    rows = []
    per_g: dict[str, dict] = {}
    for g in wm.get("guardrails", []):
        rule = rule_of_guardrail(g, app)
        st = per_g.setdefault(g["id"], {"rule": rule, "fuzzy": not g.get("predicate"), "tp": 0, "fp": 0, "fn": 0,
                                         "tn": 0, "unevaluable": 0, "predicate": g.get("predicate"), "errors": []})
        for c in cases:
            truth = bool(set(rule.split("+")) & set(c["violations"]))
            if not g.get("predicate"):
                st["unevaluable"] += 1
                continue
            v = oracle_vars(c, wm) if app == "erpnext" else {}
            r = eval_predicate(g["predicate"], v)
            if r is None:
                st["unevaluable"] += 1
                if truth:
                    st["fn"] += 1
                continue
            k = ("tp" if truth else "fp") if r else ("fn" if truth else "tn")
            st[k] += 1
            if k in ("fp", "fn") and len(st["errors"]) < 6:
                st["errors"].append({"case": c["id"], "kind": k})
            rows.append({"case": c["id"], "guardrail": g["id"], "pred": r, "truth": truth})
    for st in per_g.values():
        n = st["tp"] + st["fp"] + st["fn"] + st["tn"]
        st["accuracy"] = round((st["tp"] + st["tn"]) / n, 3) if n else None
        st.update({k: v for k, v in prf(st["tp"], st["fp"], st["fn"]).items() if k in ("precision", "recall")})
    covered = {st["rule"] for st in per_g.values()}
    rules = {"erpnext": ["R_capex", "R_uk", "R_dup"], "zammad": ["R_limit", "R_days", "R_prior", "R_cb"]}[app]
    res = {"workflow_id": wid, "per_guardrail": per_g, "rules_without_guardrail": [r for r in rules if r not in covered],
           "rules_with_deterministic_predicate": sorted({st["rule"] for st in per_g.values() if not st["fuzzy"]})}
    save_result(f"predicates_{app}", res, tag)
    print(json.dumps(res, indent=1, default=str))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", choices=["erpnext", "zammad", "predicates"])
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--wait", type=float, default=14.0)
    ap.add_argument("--for-app", dest="for_app", default="erpnext")
    a = ap.parse_args()
    if a.app == "predicates":
        predicate_eval(a.for_app, a.workflow, a.tag)
    else:
        asyncio.run(run(a.app, a.workflow, a.tag, a.only, a.workers, a.wait))


if __name__ == "__main__":
    main()

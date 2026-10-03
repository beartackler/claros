#!/usr/bin/env python3
"""Seed an unmodified ERPNext v16 site with fictional accounts-payable data for Claros.

Uses only the public REST API (`/api/resource/<DocType>`, `/api/method/...`) -- no ERPNext
code changes, no instrumentation. Idempotent: every record is looked up first and only
created when missing, so re-running is safe.

Usage
  python seed.py --dry-run              # print planned requests, no network
  python seed.py                        # seed http://localhost:8080
  python seed.py --learner-lang ru      # learner sees the desk in Russian

Auth
  If ERPNEXT_API_KEY / ERPNEXT_API_SECRET are set (env or repo-root .env) they are used
  (`Authorization: token key:secret`). Otherwise the script logs in as Administrator with
  ERPNEXT_ADMIN_PASSWORD (default "admin"), calls
  `frappe.core.doctype.user.user.generate_keys` for Administrator, prints the keys and
  writes them to infra/erpnext/.erpnext-keys.env. NOTE: generate_keys rotates the secret,
  so it is only called when no keys are configured.

All field names were checked against the version-16 branch of frappe/erpnext and
frappe/frappe (purchase_invoice.json, purchase_invoice_item.json, accounts_settings.json,
company.json, asset_category(_account).json, item.json, item_default.json, supplier.json,
cost_center.json, account.json, fiscal_year.json, workflow*.json, user.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

try:
    import httpx
except ImportError:  # dry-run works without httpx
    httpx = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# --------------------------------------------------------------------------------------
# Fictional data. Change names here only.
# --------------------------------------------------------------------------------------
PARENT = {"name": "Ostwind Precision Parts GmbH", "abbr": "OPP", "country": "Germany",
          "currency": "EUR", "tz": "Europe/Berlin"}
SUB = {"name": "Ostwind Precision Parts Ltd", "abbr": "OPPUK", "country": "United Kingdom",
       "currency": "GBP"}
# buying price list per currency (wizard creates "Standard Buying" in the parent currency)
PRICE_LISTS = {"EUR": "Standard Buying", "GBP": "Standard Buying GBP"}
EMAIL_DOMAIN = "ostwind.example"  # .example is reserved -> never a real mailbox
USER_PASSWORD = "Claros-Demo-2026!"
CAPEX_THRESHOLD_NOTE = 5000  # the company rule Claros must learn; NOT written anywhere in ERPNext


def acc(name: str, co: dict) -> str:
    return f"{name} - {co['abbr']}"


def cc(name: str, co: dict) -> str:
    return f"{name} - {co['abbr']}"


# custom expense ledgers (created under "Indirect Expenses"); copied to SUB on company creation
CUSTOM_EXPENSE_ACCOUNTS = [
    "Repairs and Maintenance",
    "Tools and Small Equipment",
    "Production Consumables",
    "Software Subscriptions",
]
BANK_ACCOUNT = "Operating Bank Account"

COST_CENTERS = {
    "parent": ["Production", "Maintenance", "Administration", "Logistics"],
    "sub": ["Production", "Administration"],
}

ASSET_CATEGORIES = [
    # name, fixed asset ledger (Standard chart)
    ("Production Machinery", "Plants and Machineries"),
    ("IT Hardware", "Electronic Equipment"),
]

ITEM_GROUPS = ["Capital Equipment", "Maintenance Services", "Production Supplies", "Overheads"]
SUPPLIER_GROUPS = ["Equipment Vendors", "Service Providers", "Materials and Utilities"]

FIXED_ASSET_ITEMS = [
    # code, name, asset category
    ("FA-COMP-15KW", "Rotary screw air compressor 15 kW", "Production Machinery"),
    ("FA-LASER-ALIGN", "Laser shaft alignment system", "Production Machinery"),
    ("FA-CNC-SPINDLE", "CNC spindle unit 24k rpm", "Production Machinery"),
    ("FA-CMM-PROBE", "CMM touch probe system", "Production Machinery"),
    ("FA-TOOL-PRESETTER", "CNC tool presetter", "Production Machinery"),
    ("FA-CAD-WS", "CAD workstation", "IT Hardware"),
]

OPEX_ITEMS = [
    # code, name, group, expense ledger, default cost center
    ("SRV-PM-CONTRACT", "Preventive maintenance (contract visit)", "Maintenance Services",
     "Repairs and Maintenance", "Maintenance"),
    ("SRV-REPAIR-OVERHAUL", "Machine repair / overhaul service", "Maintenance Services",
     "Repairs and Maintenance", "Maintenance"),
    ("EQ-GENERAL", "Workshop equipment and tools (general)", "Production Supplies",
     "Tools and Small Equipment", "Production"),
    ("CONS-CUTTING", "Cutting tools and inserts", "Production Supplies",
     "Production Consumables", "Production"),
    ("CONS-COOLANT", "Coolants and lubricants", "Production Supplies",
     "Production Consumables", "Production"),
    ("UTIL-ELECTRICITY", "Electricity", "Overheads", "Utility Expenses", "Administration"),
    ("SRV-FREIGHT", "Inbound freight", "Overheads", "Freight and Forwarding Charges", "Logistics"),
    ("SUB-CAD-SOFTWARE", "CAD/CAM software subscription", "Overheads",
     "Software Subscriptions", "Administration"),
    ("SRV-CLEANING", "Facility cleaning", "Overheads", "Office Maintenance Expenses",
     "Administration"),
]

SUPPLIERS = [
    # name, group, country, currency
    ("Feldmark Maschinentechnik GmbH", "Equipment Vendors", "Germany", "EUR"),
    ("Velt Industrieservice GmbH", "Service Providers", "Germany", "EUR"),
    ("Brauneck Schneidwerkzeuge KG", "Materials and Utilities", "Germany", "EUR"),
    ("Kornfeld Energie GmbH", "Materials and Utilities", "Germany", "EUR"),
    ("Hafenlogistik Sander GmbH", "Service Providers", "Germany", "EUR"),
    ("Quadrant Konstruktionssoftware BV", "Service Providers", "Netherlands", "EUR"),
    ("Glanzwerk Gebaeudeservice GmbH", "Service Providers", "Germany", "EUR"),
    ("Lubrion Schmierstoffe GmbH", "Materials and Utilities", "Germany", "EUR"),
    ("Pennick Facilities Ltd", "Service Providers", "United Kingdom", "GBP"),
    ("Thornbury Machine Tools Ltd", "Equipment Vendors", "United Kingdom", "GBP"),
]

FELD, VELT, BRAU, KORN, HAFEN, QUAD, GLANZ, LUBR, PENN, THORN = (s[0] for s in SUPPLIERS)

# Historical, submitted + paid (is_paid=1). (company, supplier, posting_date, bill_no, item,
# rate, description override or None)
HISTORY = [
    ("parent", VELT, "2025-12-04", "VIS-25-1187", "SRV-PM-CONTRACT", 2850.00,
     "Preventive maintenance, December visit, machining hall A"),
    ("parent", BRAU, "2025-12-11", "BSW-88231", "CONS-CUTTING", 1240.50, None),
    ("parent", KORN, "2025-12-19", "KE-2025-12-0442", "UTIL-ELECTRICITY", 3180.20, None),
    ("parent", FELD, "2026-01-15", "FMT-2026-0012", "FA-CNC-SPINDLE", 18400.00, None),
    ("parent", HAFEN, "2026-01-22", "HLS-10552", "SRV-FREIGHT", 640.00, None),
    ("parent", QUAD, "2026-01-30", "QCS-INV-7781", "SUB-CAD-SOFTWARE", 2400.00, None),
    ("parent", VELT, "2026-02-05", "VIS-26-0094", "SRV-PM-CONTRACT", 2850.00, None),
    ("parent", GLANZ, "2026-02-14", "GW-2602-118", "SRV-CLEANING", 980.00, None),
    ("parent", LUBR, "2026-02-20", "LUB-41207", "CONS-COOLANT", 1615.80, None),
    ("parent", VELT, "2026-03-03", "VIS-26-0211", "SRV-REPAIR-OVERHAUL", 7950.00,
     "Gearbox overhaul, press brake PB-2 (parts and labour)"),
    ("parent", FELD, "2026-03-12", "FMT-2026-0087", "EQ-GENERAL", 1180.00,
     "Torque wrench set and bench vices"),
    ("parent", KORN, "2026-03-19", "KE-2026-03-0217", "UTIL-ELECTRICITY", 2940.75, None),
    ("parent", BRAU, "2026-04-02", "BSW-89410", "CONS-CUTTING", 2075.00, None),
    ("parent", FELD, "2026-04-16", "FMT-2026-0131", "FA-LASER-ALIGN", 6300.00, None),
    ("parent", VELT, "2026-05-06", "VIS-26-0388", "SRV-PM-CONTRACT", 2850.00, None),
    ("parent", HAFEN, "2026-05-21", "HLS-11098", "SRV-FREIGHT", 515.00, None),
    ("parent", FELD, "2026-06-03", "FMT-2026-0174", "EQ-GENERAL", 3420.00,
     "Mobile workbench and tool trolleys (6 pcs)"),
    ("parent", GLANZ, "2026-06-18", "GW-2606-301", "SRV-CLEANING", 980.00, None),
    ("parent", VELT, "2026-07-08", "VIS-26-0517", "SRV-REPAIR-OVERHAUL", 5480.00,
     "Hydraulic unit repair, injection moulder IM-1"),
    ("parent", FELD, "2026-07-23", "FMT-2026-0220", "FA-CMM-PROBE", 11250.00, None),
    ("parent", VELT, "2026-08-05", "VIS-26-0602", "SRV-PM-CONTRACT", 2850.00, None),
    ("parent", LUBR, "2026-08-27", "LUB-43090", "CONS-COOLANT", 1402.40, None),
    ("parent", KORN, "2026-09-10", "KE-2026-09-0398", "UTIL-ELECTRICITY", 3055.10, None),
    ("sub", PENN, "2026-04-09", "PFL-3301", "SRV-PM-CONTRACT", 1450.00, None),
    ("sub", THORN, "2026-06-25", "TMT-INV-2210", "EQ-GENERAL", 2180.00,
     "Hand tools restock, Sheffield workshop"),
    ("sub", PENN, "2026-08-14", "PFL-3519", "SRV-REPAIR-OVERHAUL", 3900.00, None),
]

# Drafts. expense_account / cost_center override = deliberately pre-filled values.
DRAFTS = [
    # --- expert demo -------------------------------------------------------------------
    {"key": "expert-1-equipment", "company": "parent", "supplier": FELD,
     "posting_date": "2026-10-01", "bill_no": "FMT-2026-0261", "item": "EQ-GENERAL",
     "rate": 8400.00,
     "description": "Rotary screw air compressor 15 kW incl. installation",
     # WRONG on purpose: equipment > 5,000 should be capitalised (FA-COMP-15KW,
     # Plants and Machineries) on the Production cost center.
     "expense_account": "Tools and Small Equipment", "cost_center": "Administration"},
    {"key": "expert-2-rebill", "company": "parent", "supplier": VELT,
     "posting_date": "2025-12-22", "bill_no": "VIS-25-1187-A", "item": "SRV-PM-CONTRACT",
     "rate": 2850.00,
     # Same service as paid VIS-25-1187 (2025-12-04); suffix defeats the uniqueness check.
     "description": "Preventive maintenance, December visit, machining hall A"},
    {"key": "expert-3-subsidiary", "company": "sub", "supplier": PENN,
     "posting_date": "2026-10-02", "bill_no": "PFL-3622", "item": "SRV-REPAIR-OVERHAUL",
     "rate": 4250.00, "description": "Extraction system repair, Sheffield workshop"},
    # --- held-out learner cases ----------------------------------------------------------
    {"key": "learner-1-equipment-7200", "company": "parent", "supplier": FELD,
     "posting_date": "2026-10-05", "bill_no": "FMT-2026-0274", "item": "EQ-GENERAL",
     "rate": 7200.00, "description": "CNC tool presetter incl. calibration kit",
     "expense_account": "Tools and Small Equipment", "cost_center": "Administration"},
    {"key": "learner-2-maintenance-trap-6100", "company": "parent", "supplier": VELT,
     "posting_date": "2026-10-06", "bill_no": "VIS-26-0731", "item": "SRV-REPAIR-OVERHAUL",
     "rate": 6100.00,
     # Trap: > 5,000 but maintenance -> stays OPEX (Repairs and Maintenance).
     "description": "Spindle bearing replacement and overhaul, machining centre MC-4"},
]

USERS = [
    # key, first, last, roles
    ("expert", "Erika", "Expert", ["Accounts User", "Purchase User"]),
    ("learner", "Leon", "Learner", ["Accounts User", "Purchase User"]),
    ("approver", "Anja", "Approver", ["Accounts Manager", "Accounts User", "Purchase User"]),
]

WORKFLOW_NAME = "Purchase Invoice Approval"


# --------------------------------------------------------------------------------------
# HTTP client with dry-run
# --------------------------------------------------------------------------------------
class Missing(Exception):
    pass


class ERP:
    def __init__(self, base: str, dry: bool, verbose: bool = False):
        self.base = base.rstrip("/")
        self.dry = dry
        self.verbose = verbose
        self.headers: dict[str, str] = {"Accept": "application/json"}
        self.http = None if dry else httpx.Client(base_url=self.base, timeout=300)
        self.created = 0
        self.skipped = 0

    # low level -------------------------------------------------------------------------
    def _log(self, method: str, path: str, body=None):
        line = f"{method:6} {path}"
        if body is not None:
            line += "  " + json.dumps(body, ensure_ascii=False, default=str)
        print(line)

    def req(self, method: str, path: str, body=None, params=None, ok404=False):
        if self.dry:
            q = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
            self._log(method, path + q, body)
            return None
        if self.verbose:
            self._log(method, path, body)
        r = self.http.request(method, path, json=body, params=params, headers=self.headers)
        if r.status_code == 404 and ok404:
            raise Missing(path)
        if r.status_code >= 400:
            msg = r.text
            try:
                j = r.json()
                msg = j.get("exception") or j.get("_server_messages") or j.get("message") or msg
            except Exception:
                pass
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {str(msg)[:800]}")
        return r.json() if r.content else {}

    @staticmethod
    def r(doctype: str, name: str | None = None) -> str:
        p = f"/api/resource/{quote(doctype)}"
        return p + (f"/{quote(name, safe='')}" if name is not None else "")

    # helpers ---------------------------------------------------------------------------
    def exists(self, doctype: str, name: str) -> bool:
        if self.dry:
            return False
        try:
            self.req("GET", self.r(doctype, name), ok404=True)
            return True
        except Missing:
            return False

    def get(self, doctype: str, name: str) -> dict:
        return (self.req("GET", self.r(doctype, name)) or {}).get("data", {})

    def find(self, doctype: str, filters: list, fields=("name",)) -> list[dict]:
        if self.dry:
            return []
        res = self.req("GET", self.r(doctype), params={
            "filters": json.dumps(filters), "fields": json.dumps(list(fields)),
            "limit_page_length": 0})
        return res.get("data", [])

    def insert(self, doctype: str, doc: dict) -> dict:
        self.created += 1
        res = self.req("POST", self.r(doctype), body=doc)
        return (res or {}).get("data", {"name": doc.get("name", f"<new {doctype}>")})

    def update(self, doctype: str, name: str, patch: dict) -> dict:
        res = self.req("PUT", self.r(doctype, name), body=patch)
        return (res or {}).get("data", {})

    def method(self, path: str, body: dict | None = None):
        res = self.req("POST", f"/api/method/{path}", body=body or {})
        return (res or {}).get("message")

    def ensure(self, doctype: str, name: str, doc: dict, label: str | None = None) -> str:
        """Create `doc` unless a record called `name` exists. Returns the record name."""
        if self.exists(doctype, name):
            self.skipped += 1
            return name
        print(f"  + {doctype}: {label or name}")
        return self.insert(doctype, {"doctype": doctype, **doc}).get("name", name)


# --------------------------------------------------------------------------------------
# Auth + setup wizard
# --------------------------------------------------------------------------------------
def load_dotenv():
    for p in (REPO / ".env", HERE / ".erpnext-keys.env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if v and not os.environ.get(k.strip()):
                        os.environ[k.strip()] = v


def authenticate(erp: ERP, admin_password: str):
    key, secret = os.environ.get("ERPNEXT_API_KEY"), os.environ.get("ERPNEXT_API_SECRET")
    if key and secret:
        erp.headers["Authorization"] = f"token {key}:{secret}"
        if not erp.dry:
            who = erp.req("GET", "/api/method/frappe.auth.get_logged_user")
            print(f"auth: token for {who.get('message')}")
        return
    print("auth: no ERPNEXT_API_KEY/SECRET -> password login + generate_keys")
    erp.req("POST", "/api/method/login", body={"usr": "Administrator", "pwd": admin_password})
    keys = erp.method("frappe.core.doctype.user.user.generate_keys", {"user": "Administrator"})
    if erp.dry:
        keys = {"api_key": "<dry-run>", "api_secret": "<dry-run>"}
    erp.headers["Authorization"] = f"token {keys['api_key']}:{keys['api_secret']}"
    out = HERE / ".erpnext-keys.env"
    if not erp.dry:
        out.write_text(f"ERPNEXT_API_KEY={keys['api_key']}\nERPNEXT_API_SECRET={keys['api_secret']}\n")
    print(f"auth: generated Administrator API key -> {out.name}\n"
          f"      ERPNEXT_API_KEY={keys['api_key']}\n      ERPNEXT_API_SECRET={keys['api_secret']}\n"
          "      (copy both into the repo-root .env)")


def setup_wizard(erp: ERP):
    if erp.find("Company", [], ["name"]):
        print("setup: wizard already completed")
        return
    print("setup: running setup wizard (1-3 min) ...")
    args = {
        "language": "English", "country": PARENT["country"], "timezone": PARENT["tz"],
        "currency": PARENT["currency"], "company_name": PARENT["name"],
        "company_abbr": PARENT["abbr"], "chart_of_accounts": "Standard",
        "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31",
        "setup_demo": 0, "enable_telemetry": 0,
    }
    res = erp.method("frappe.desk.page.setup_wizard.setup_wizard.setup_complete", {"args": args})
    if res and isinstance(res, dict) and res.get("status") not in (None, "ok"):
        raise RuntimeError(f"setup wizard failed: {res}")


# --------------------------------------------------------------------------------------
# Seed steps
# --------------------------------------------------------------------------------------
def co(which: str) -> dict:
    return PARENT if which == "parent" else SUB


def seed_masters(erp: ERP):
    print("masters")
    # fiscal year 2025 (history + December re-billing); wizard created 2026
    erp.ensure("Fiscal Year", "2025", {"year": "2025", "year_start_date": "2025-01-01",
                                       "year_end_date": "2025-12-31"})
    erp.ensure("Fiscal Year", "2026", {"year": "2026", "year_start_date": "2026-01-01",
                                       "year_end_date": "2026-12-31"})
    erp.update("Currency", "GBP", {"enabled": 1})
    erp.ensure("Price List", PRICE_LISTS["GBP"], {
        "price_list_name": PRICE_LISTS["GBP"], "currency": "GBP", "buying": 1, "selling": 0,
        "enabled": 1})

    # parent must be a group company before a subsidiary can point at it
    if erp.dry or not erp.get("Company", PARENT["name"]).get("is_group"):
        print(f"  ~ Company {PARENT['name']}: is_group=1")
        erp.update("Company", PARENT["name"], {"is_group": 1})

    # ledgers in the parent first: the subsidiary copies the parent chart on creation
    for name in CUSTOM_EXPENSE_ACCOUNTS:
        erp.ensure("Account", acc(name, PARENT), {
            "account_name": name, "parent_account": acc("Indirect Expenses", PARENT),
            "company": PARENT["name"], "is_group": 0})
    erp.ensure("Account", acc(BANK_ACCOUNT, PARENT), {
        "account_name": BANK_ACCOUNT, "parent_account": acc("Bank Accounts", PARENT),
        "company": PARENT["name"], "account_type": "Bank", "is_group": 0})

    erp.ensure("Company", SUB["name"], {
        "company_name": SUB["name"], "abbr": SUB["abbr"], "country": SUB["country"],
        "default_currency": SUB["currency"], "parent_company": PARENT["name"],
        "create_chart_of_accounts_based_on": "Existing Company",
        "existing_company": PARENT["name"]})
    # (re-run safety) make sure the copied ledgers exist in the subsidiary too
    for name in CUSTOM_EXPENSE_ACCOUNTS:
        erp.ensure("Account", acc(name, SUB), {
            "account_name": name, "parent_account": acc("Indirect Expenses", SUB),
            "company": SUB["name"], "is_group": 0})
    erp.ensure("Account", acc(BANK_ACCOUNT, SUB), {
        "account_name": BANK_ACCOUNT, "parent_account": acc("Bank Accounts", SUB),
        "company": SUB["name"], "account_type": "Bank", "account_currency": SUB["currency"],
        "is_group": 0})

    for which, names in COST_CENTERS.items():
        c = co(which)
        for n in names:
            erp.ensure("Cost Center", cc(n, c), {
                "cost_center_name": n, "parent_cost_center": f"{c['name']} - {c['abbr']}",
                "company": c["name"], "is_group": 0})

    for cat, fa_account in ASSET_CATEGORIES:
        erp.ensure("Asset Category", cat, {
            "asset_category_name": cat, "enable_cwip_accounting": 0,
            "accounts": [{"company_name": c["name"],
                          "fixed_asset_account": acc(fa_account, c),
                          "accumulated_depreciation_account": acc("Accumulated Depreciation", c),
                          "depreciation_expense_account": acc("Depreciation", c)}
                         for c in (PARENT, SUB)]})

    for g in ITEM_GROUPS:
        erp.ensure("Item Group", g, {"item_group_name": g,
                                     "parent_item_group": "All Item Groups", "is_group": 0})
    for g in SUPPLIER_GROUPS:
        erp.ensure("Supplier Group", g, {"supplier_group_name": g,
                                         "parent_supplier_group": "All Supplier Groups",
                                         "is_group": 0})

    for code, name, cat in FIXED_ASSET_ITEMS:
        erp.ensure("Item", code, {
            "item_code": code, "item_name": name, "item_group": "Capital Equipment",
            "stock_uom": "Nos", "is_stock_item": 0, "is_fixed_asset": 1,
            "asset_category": cat, "auto_create_assets": 0, "is_purchase_item": 1,
            "is_sales_item": 0})
    for code, name, group, exp, ccn in OPEX_ITEMS:
        erp.ensure("Item", code, {
            "item_code": code, "item_name": name, "item_group": group, "stock_uom": "Nos",
            "is_stock_item": 0, "is_fixed_asset": 0, "is_purchase_item": 1,
            "is_sales_item": 0,
            "item_defaults": [
                {"company": c["name"], "expense_account": acc(exp, c),
                 "buying_cost_center": cc(ccn if ccn in COST_CENTERS[w] else "Main", c)}
                for w, c in (("parent", PARENT), ("sub", SUB))]})


def supplier_names(erp: ERP) -> dict[str, str]:
    """Supplier naming may be by name or by series -> resolve by supplier_name."""
    out = {}
    for name, group, country, cur in SUPPLIERS:
        found = erp.find("Supplier", [["supplier_name", "=", name]])
        if found:
            out[name] = found[0]["name"]
            erp.skipped += 1
            continue
        print(f"  + Supplier: {name}")
        doc = erp.insert("Supplier", {"doctype": "Supplier", "supplier_name": name,
                                      "supplier_group": group, "supplier_type": "Company",
                                      "country": country, "default_currency": cur})
        out[name] = name if erp.dry else doc.get("name", name)
    return out


def seed_settings_and_users(erp: ERP, learner_lang: str, expert_lang: str):
    print("settings + users")
    erp.update("Accounts Settings", "Accounts Settings", {"check_supplier_invoice_uniqueness": 1})
    langs = {"expert": expert_lang, "learner": learner_lang, "approver": "en"}
    for key, first, last, roles in USERS:
        email = f"{key}@{EMAIL_DOMAIN}"
        if erp.exists("User", email):
            erp.skipped += 1
            erp.update("User", email, {"language": langs[key]})
            continue
        print(f"  + User: {email} ({', '.join(roles)}, lang={langs[key]})")
        erp.insert("User", {"doctype": "User", "email": email, "first_name": first,
                            "last_name": last, "enabled": 1, "send_welcome_email": 0,
                            "language": langs[key], "new_password": USER_PASSWORD,
                            "roles": [{"role": r} for r in roles]})


def invoice_doc(c: dict, supplier: str, posting: str, bill_no: str, item: str, rate: float,
                description: str | None, expense: str | None = None,
                cost_center: str | None = None, paid: bool = False) -> dict:
    d = date.fromisoformat(posting)
    line = {"item_code": item, "qty": 1, "rate": rate, "uom": "Nos", "conversion_factor": 1}
    if description:
        line["description"] = description
    if expense:
        line["expense_account"] = acc(expense, c)
    if cost_center:
        line["cost_center"] = cc(cost_center, c)
    doc = {"doctype": "Purchase Invoice", "company": c["name"], "supplier": supplier,
           "posting_date": posting, "set_posting_time": 1, "bill_no": bill_no,
           "bill_date": posting, "due_date": (d + timedelta(days=30)).isoformat(),
           "currency": c["currency"], "conversion_rate": 1, "disable_rounded_total": 1,
           "buying_price_list": PRICE_LISTS[c["currency"]], "price_list_currency": c["currency"],
           "plc_conversion_rate": 1,
           "items": [line]}
    if paid:
        doc.update({"is_paid": 1, "cash_bank_account": acc(BANK_ACCOUNT, c),
                    "paid_amount": rate, "docstatus": 1})
    return doc


def pi_exists(erp: ERP, supplier: str, bill_no: str) -> bool:
    return bool(erp.find("Purchase Invoice", [["supplier", "=", supplier],
                                              ["bill_no", "=", bill_no],
                                              ["docstatus", "<", 2]]))


def seed_invoices(erp: ERP, sup: dict[str, str]):
    print("purchase invoices")
    todo_hist = [h for h in HISTORY if not pi_exists(erp, sup[h[1]], h[3])]
    erp.skipped += len(HISTORY) - len(todo_hist)

    # a Workflow blocks direct submission; pause it while back-filling submitted history
    paused = False
    if todo_hist and erp.exists("Workflow", WORKFLOW_NAME) \
            and erp.get("Workflow", WORKFLOW_NAME).get("is_active"):
        print(f"  ~ pausing workflow {WORKFLOW_NAME!r} to back-fill history")
        erp.update("Workflow", WORKFLOW_NAME, {"is_active": 0})
        paused = True
    try:
        for which, s, posting, bill_no, item, rate, desc in todo_hist:
            print(f"  + PI paid  {co(which)['abbr']:5} {posting} {bill_no:16} {rate:>10,.2f}")
            erp.insert("Purchase Invoice", invoice_doc(co(which), sup[s], posting, bill_no,
                                                       item, rate, desc, paid=True))
    finally:
        if paused:
            erp.update("Workflow", WORKFLOW_NAME, {"is_active": 1})

    for dft in DRAFTS:
        s = sup[dft["supplier"]]
        if pi_exists(erp, s, dft["bill_no"]):
            erp.skipped += 1
            continue
        c = co(dft["company"])
        print(f"  + PI draft {c['abbr']:5} {dft['posting_date']} {dft['bill_no']:16} "
              f"{dft['rate']:>10,.2f}  [{dft['key']}]")
        erp.insert("Purchase Invoice", invoice_doc(
            c, s, dft["posting_date"], dft["bill_no"], dft["item"], dft["rate"],
            dft.get("description"), dft.get("expense_account"), dft.get("cost_center")))


def seed_workflow(erp: ERP):
    print("workflow")
    states = ["Draft", "Pending Second Approval", "Approved", "Rejected"]
    styles = {"Draft": "", "Pending Second Approval": "Warning", "Approved": "Success",
              "Rejected": "Danger"}
    for s in states:
        erp.ensure("Workflow State", s, {"workflow_state_name": s, "style": styles[s]})
    for a in ["Submit", "Request Approval", "Approve", "Reject", "Revise"]:
        erp.ensure("Workflow Action Master", a, {"workflow_action_name": a})
    is_sub = f'doc.company == "{SUB["name"]}"'
    not_sub = f'doc.company != "{SUB["name"]}"'
    erp.ensure("Workflow", WORKFLOW_NAME, {
        "workflow_name": WORKFLOW_NAME, "document_type": "Purchase Invoice",
        "is_active": 1, "override_status": 0, "send_email_alert": 0,
        "workflow_state_field": "workflow_state",
        "states": [
            {"state": "Draft", "doc_status": "0", "allow_edit": "Accounts User"},
            {"state": "Pending Second Approval", "doc_status": "0",
             "allow_edit": "Accounts Manager"},
            {"state": "Approved", "doc_status": "1", "allow_edit": "Accounts Manager"},
            {"state": "Rejected", "doc_status": "0", "allow_edit": "Accounts User"},
        ],
        "transitions": [
            {"state": "Draft", "action": "Submit", "next_state": "Approved",
             "allowed": "Accounts User", "allow_self_approval": 1, "condition": not_sub},
            {"state": "Draft", "action": "Request Approval",
             "next_state": "Pending Second Approval", "allowed": "Accounts User",
             "allow_self_approval": 1, "condition": is_sub},
            {"state": "Pending Second Approval", "action": "Approve", "next_state": "Approved",
             "allowed": "Accounts Manager", "allow_self_approval": 0},
            {"state": "Pending Second Approval", "action": "Reject", "next_state": "Rejected",
             "allowed": "Accounts Manager", "allow_self_approval": 0},
            {"state": "Rejected", "action": "Revise", "next_state": "Draft",
             "allowed": "Accounts User", "allow_self_approval": 1},
        ]}, label=f"{WORKFLOW_NAME} (subsidiary -> second approval)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=None, help="default: $ERPNEXT_URL or http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true", help="print planned requests only")
    ap.add_argument("--admin-password", default=None)
    ap.add_argument("--learner-lang", default="en", help="en|ru|fr|es|de")
    ap.add_argument("--expert-lang", default="en", help="en|ru|fr|es|de")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    load_dotenv()
    url = a.url or os.environ.get("ERPNEXT_URL") or "http://localhost:8080"
    if not a.dry_run and httpx is None:
        sys.exit("pip install httpx  (or run with --dry-run)")
    if a.dry_run:
        print(f"# DRY RUN against {url} -- every lookup assumed missing\n")
    erp = ERP(url, a.dry_run, a.verbose)
    authenticate(erp, a.admin_password or os.environ.get("ERPNEXT_ADMIN_PASSWORD", "admin"))
    setup_wizard(erp)
    seed_masters(erp)
    sup = supplier_names(erp)
    seed_settings_and_users(erp, a.learner_lang, a.expert_lang)
    seed_invoices(erp, sup)
    seed_workflow(erp)  # last: history above is inserted as submitted
    print(f"\ndone: {erp.created} created, {erp.skipped} already present.")
    if not erp.dry:
        print(f"users: expert|learner|approver@{EMAIL_DOMAIN} / {USER_PASSWORD}\n"
              "next:  ./reset.sh snapshot   (save this state for demo resets)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Seed an unmodified Zammad with a tier-1 -> tier-2 refund-escalation scenario.

Generalization test bed for Claros (second app besides ERPNext). REST only
(`/api/v1/...`, HTTP basic auth as the admin created by ./up.sh). Idempotent: groups,
users, text modules, macros and tickets are matched by name/email/title first.

  python seed.py --dry-run            # print planned requests, no network
  python seed.py                      # seed http://localhost:8081
  python seed.py --learner-locale ru  # tier-1 learner sees Zammad in Russian

The refund rule below is the "company policy" Claros should learn; the *judgment* (what
counts as a duplicate charge, when to stop and escalate) is what the expert demonstrates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import httpx
except ImportError:
    httpx = None

ADMIN_EMAIL = os.environ.get("ZAMMAD_ADMIN_EMAIL", "admin@helpdesk.example")
ADMIN_PASSWORD = os.environ.get("ZAMMAD_ADMIN_PASSWORD", "Claros-Demo-2026!")
USER_PASSWORD = "Claros-Demo-2026!"

T1, T2 = "Tier 1 Support", "Tier 2 Support"
ORG = "Lumora Customers"  # fictional SaaS ("Lumora") whose helpdesk this is

REFUND_POLICY_HTML = """
<p><b>Refund policy v3 (internal, Tier 1)</b></p>
<ol>
<li>Tier 1 may approve a refund itself only if <b>all</b> hold: amount &le; 50 EUR,
request within 14 days of the charge, and no other refund for this customer in the last
12 months.</li>
<li>Anything else: <b>do not promise a refund</b>. Add an internal note (order id, amount,
days since charge, prior refunds), move the ticket to <i>Tier 2 Support</i>, priority high.</li>
<li>Chargeback / bank dispute / legal threat: move to Tier 2 immediately, no reply on the
substance.</li>
<li>Always reply in the customer's language.</li>
</ol>
"""

AGENTS = [
    # key, first, last, groups->access, default locale
    ("expert", "Elena", "Tier2", {T1: ["full"], T2: ["full"]}, "en-us"),
    ("learner", "Luca", "Tier1", {T1: ["full"], T2: ["create"]}, "en-us"),
]

CUSTOMERS = [
    ("Maya", "Okafor", "maya.okafor@customer.example"),
    ("Daniel", "Brooks", "daniel.brooks@customer.example"),
    ("Ирина", "Соколова", "irina.sokolova@customer.example"),
    ("Camille", "Laurent", "camille.laurent@customer.example"),
    ("Jonas", "Weber", "jonas.weber@customer.example"),
    ("Priya", "Nair", "priya.nair@customer.example"),
]

TICKETS = [
    {"title": "Charged twice for annual plan", "customer": "maya.okafor@customer.example",
     "lang": "en", "expected": "escalate (240 EUR > 50)",
     "body": "Hi, my card was charged 240 EUR twice for the annual Pro plan on 30 Sept "
             "(order LUM-58213). Please refund the duplicate. Thanks, Maya"},
    {"title": "Refund for add-on I never used", "customer": "daniel.brooks@customer.example",
     "lang": "en", "expected": "tier 1 approves (19 EUR, 6 days, first refund)",
     "body": "Hello, I bought the Export add-on (19 EUR, order LUM-58877) six days ago by "
             "mistake and never used it. Could I get my money back? Daniel"},
    {"title": "Возврат средств за подписку", "customer": "irina.sokolova@customer.example",
     "lang": "ru", "expected": "escalate (89 EUR, 40 days)",
     "body": "Здравствуйте! 40 дней назад с меня списали 89 EUR за подписку Team "
             "(заказ LUM-55120), но сервисом я так и не воспользовалась. Прошу вернуть "
             "деньги. С уважением, Ирина Соколова"},
    {"title": "Remboursement – commande passée deux fois",
     "customer": "camille.laurent@customer.example", "lang": "fr",
     "expected": "tier 1 approves (35 EUR duplicate, 5 days, first refund)",
     "body": "Bonjour, j'ai passé deux fois la même commande (LUM-58740, 35 EUR) il y a "
             "cinq jours. Pourriez-vous rembourser la seconde ? Merci, Camille Laurent"},
    {"title": "Rückbuchung bei meiner Bank eingeleitet",
     "customer": "jonas.weber@customer.example", "lang": "de",
     "expected": "tier 2 immediately (chargeback)",
     "body": "Guten Tag, da auf meine E-Mails niemand reagiert, habe ich bei meiner Bank eine "
             "Rückbuchung über 59 EUR (Bestellung LUM-57301) veranlasst. Jonas Weber"},
]

# Closed precedent: shows the expert's escalation pattern in the history.
PRECEDENT = {
    "title": "Refund request – 3 months unused", "customer": "priya.nair@customer.example",
    "body": "Hi, I paid 120 EUR for a quarterly plan (LUM-50418) but couldn't use it due to "
            "a project cancellation. Can I get a refund? Priya",
    "notes": [
        "Tier 1: 120 EUR, 52 days since charge -> outside Tier 1 limits. Escalating. "
        "Order LUM-50418, no prior refunds.",
        "Tier 2: approved pro-rata refund of 80 EUR (2 unused months) as goodwill; "
        "customer informed. Closing.",
    ],
}


class Missing(Exception):
    pass


class Z:
    def __init__(self, base: str, dry: bool):
        self.base = base.rstrip("/")
        self.dry = dry
        self.http = None if dry else httpx.Client(
            base_url=self.base, auth=(ADMIN_EMAIL, ADMIN_PASSWORD), timeout=120,
            headers={"Accept": "application/json"})
        self.created = 0
        self.skipped = 0
        self._fake_id = 1000

    def req(self, method: str, path: str, body=None, params=None):
        if self.dry:
            q = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
            line = f"{method:6} {path}{q}"
            if body is not None:
                line += "  " + json.dumps(body, ensure_ascii=False)
            print(line)
            self._fake_id += 1
            return {"id": self._fake_id} if method == "POST" else []
        r = self.http.request(method, path, json=body, params=params)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:600]}")
        return r.json() if r.content else {}

    def all(self, path: str) -> list[dict]:
        if self.dry:
            return []
        out, page = [], 1
        while True:
            chunk = self.req("GET", path, params={"page": page, "per_page": 100})
            out += chunk
            if len(chunk) < 100:
                return out
            page += 1

    def post(self, path: str, body: dict) -> dict:
        self.created += 1
        return self.req("POST", path, body)


def ensure_by(z: Z, path: str, key: str, value: str, body: dict, label: str) -> int:
    for row in z.all(path):
        if str(row.get(key, "")).lower() == value.lower():
            z.skipped += 1
            return row["id"]
    print(f"  + {label}")
    return z.post(path, body)["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("ZAMMAD_URL", "http://localhost:8081"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--learner-locale", default="en-us", help="en-us|ru|fr-fr|de-de|es-es")
    ap.add_argument("--expert-locale", default="en-us")
    a = ap.parse_args()
    if not a.dry_run and httpx is None:
        sys.exit("pip install httpx  (or run with --dry-run)")
    z = Z(a.url, a.dry_run)
    if a.dry_run:
        print(f"# DRY RUN against {a.url} -- every lookup assumed missing\n")
    else:
        me = z.req("GET", "/api/v1/users/me")
        print(f"auth: {me.get('login')}")

    print("groups + org")
    gid = {g: ensure_by(z, "/api/v1/groups", "name", g,
                        {"name": g, "active": True,
                         "note": "first-line support" if g == T1 else "escalations, refunds > policy"},
                        f"Group: {g}") for g in (T1, T2)}
    ensure_by(z, "/api/v1/organizations", "name", ORG, {"name": ORG, "active": True},
              f"Organization: {ORG}")

    print("agents + customers")
    users = {} if a.dry_run else {u["email"].lower(): u for u in z.all("/api/v1/users")
                                  if u.get("email")}
    # admin needs group access to create/move tickets
    admin = users.get(ADMIN_EMAIL.lower())
    if admin or a.dry_run:
        z.req("PUT", f"/api/v1/users/{admin['id'] if admin else '<admin_id>'}",
              {"group_ids": {str(gid[T1]): ["full"], str(gid[T2]): ["full"]}})
    locales = {"expert": a.expert_locale, "learner": a.learner_locale}
    for key, first, last, access, _ in AGENTS:
        email = f"{key}@helpdesk.example"
        body = {"firstname": first, "lastname": last, "email": email, "login": email,
                "password": USER_PASSWORD, "roles": ["Agent"], "active": True,
                "group_ids": {str(gid[g]): acc for g, acc in access.items()},
                "preferences": {"locale": locales[key]}}
        if email in users:
            z.skipped += 1
            z.req("PUT", f"/api/v1/users/{users[email]['id']}",
                  {"preferences": {"locale": locales[key]}})
        else:
            print(f"  + Agent: {email} ({locales[key]})")
            z.post("/api/v1/users", body)
    for first, last, email in CUSTOMERS:
        if email in users:
            z.skipped += 1
            continue
        print(f"  + Customer: {email}")
        z.post("/api/v1/users", {"firstname": first, "lastname": last, "email": email,
                                 "login": email, "roles": ["Customer"], "organization": ORG,
                                 "active": True})

    print("policy text module + macro")
    ensure_by(z, "/api/v1/text_modules", "name", "Refund policy v3",
              {"name": "Refund policy v3", "keywords": "refund remboursement возврат",
               "content": REFUND_POLICY_HTML, "active": True}, "Text module: Refund policy v3")
    try:
        ensure_by(z, "/api/v1/macros", "name", "Escalate to Tier 2",
                  {"name": "Escalate to Tier 2", "active": True, "ux_flow_next_up": "next_task",
                   "perform": {"ticket.group_id": {"value": str(gid[T2])},
                               "ticket.priority_id": {"value": "3"}}},
                  "Macro: Escalate to Tier 2")
    except RuntimeError as e:  # macro schema differs across versions -> non-fatal
        print(f"  ! macro skipped: {e}")

    print("tickets")
    existing = {t["title"]: t["id"] for t in z.all("/api/v1/tickets")}
    for t in TICKETS:
        if t["title"] in existing:
            z.skipped += 1
            continue
        print(f"  + Ticket [{t['lang']}] {t['title']}   -> expected: {t['expected']}")
        z.post("/api/v1/tickets", {
            "title": t["title"], "group": T1, "customer_id": f"guess:{t['customer']}",
            "priority": "2 normal", "state": "new",
            "article": {"subject": t["title"], "body": t["body"], "type": "web",
                        "sender": "Customer", "internal": False,
                        "content_type": "text/plain"}})
    if PRECEDENT["title"] in existing:
        z.skipped += 1
    else:
        print(f"  + Ticket (closed precedent) {PRECEDENT['title']}")
        tid = z.post("/api/v1/tickets", {
            "title": PRECEDENT["title"], "group": T1,
            "customer_id": f"guess:{PRECEDENT['customer']}", "priority": "2 normal",
            "state": "open",
            "article": {"subject": PRECEDENT["title"], "body": PRECEDENT["body"],
                        "type": "web", "sender": "Customer", "internal": False,
                        "content_type": "text/plain"}})["id"]
        z.post("/api/v1/ticket_articles", {"ticket_id": tid, "body": PRECEDENT["notes"][0],
                                           "type": "note", "internal": True,
                                           "content_type": "text/plain"})
        z.req("PUT", f"/api/v1/tickets/{tid}", {"group_id": gid[T2], "priority": "3 high"})
        z.post("/api/v1/ticket_articles", {"ticket_id": tid, "body": PRECEDENT["notes"][1],
                                           "type": "note", "internal": True,
                                           "content_type": "text/plain"})
        z.req("PUT", f"/api/v1/tickets/{tid}", {"state": "closed"})
        z.req("POST", "/api/v1/tags/add", {"object": "Ticket", "o_id": tid, "item": "refund"})

    print(f"\ndone: {z.created} created, {z.skipped} already present.")
    if not a.dry_run:
        print("agents: expert@helpdesk.example (Tier 2), learner@helpdesk.example (Tier 1) "
              f"/ {USER_PASSWORD}")


if __name__ == "__main__":
    main()

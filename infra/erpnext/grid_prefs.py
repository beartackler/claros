"""Show Expense Account + Cost Center as columns in the Purchase Invoice items table for the demo users.

A per-user grid preference (what a user would set once via the table's gear icon), so a person driving
ERPNext by hand edits account/cost center inline instead of opening each row. Run after every restore
(reset.sh does it). Stdlib only.
"""
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request

ERP = os.getenv("ERP_URL", "http://localhost:8080")
PW = os.getenv("CLAROS_DEMO_PW", "Claros-Demo-2026!")
USERS = ["expert@ostwind.example", "learner@ostwind.example", "approver@ostwind.example"]
COLS = [{"fieldname": "item_code", "columns": 2}, {"fieldname": "qty", "columns": 1},
        {"fieldname": "amount", "columns": 2}, {"fieldname": "expense_account", "columns": 3},
        {"fieldname": "cost_center", "columns": 2}]


def post(opener, path: str, data: dict) -> dict:
    req = urllib.request.Request(f"{ERP}{path}", data=urllib.parse.urlencode(data).encode(), method="POST")
    with opener.open(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    ok = True
    for user in USERS:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            post(opener, "/api/method/login", {"usr": user, "pwd": PW})
            settings = {"GridView": {"Purchase Invoice Item": COLS}}
            post(opener, "/api/method/frappe.model.utils.user_settings.save",
                 {"doctype": "Purchase Invoice", "user_settings": json.dumps(settings)})
            print(f"grid columns set for {user}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"grid columns FAILED for {user}: {e}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

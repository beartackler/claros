"""Phase 4 — questioning quality.

    cd server && .venv/bin/python ../infra/evals/questions_eval.py [--tag before] [--runs erpnext/before,zammad/before]

(a) Capture-run metrics from recorded capture sessions (data/evals/<app>/<tag>/A): asks per 10 min,
    guardrail-question share, answerable-from-screen rate, duplicate-question rate. Judgments come from an
    independent judge (different model family; see common.Judge) AND from hand labels in HAND_LABELS below
    (keyed by question text; unlabeled questions are reported as such, never guessed).
(b) Scope classification accuracy of Claros' scope router on 40 hand-labeled unknowns (8 per scope),
    plus judge-vs-hand agreement (Cohen's kappa) so the hand labels themselves are checked.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from common import API, EVAL_DATA, Judge, log, save_result  # noqa: E402

SCOPES = ["universal", "app", "occupation", "company", "personal_judgment"]

UNKNOWNS: list[tuple[str, str, str]] = [  # (question, app context, hand label)
    ("What is the difference between capex and opex?", "ERPNext", "universal"),
    ("What does accrual mean in accounting?", "ERPNext", "universal"),
    ("What is a chargeback?", "Zammad", "universal"),
    ("What does VAT reverse charge mean?", "ERPNext", "universal"),
    ("What is a pro-rata refund?", "Zammad", "universal"),
    ("What is depreciation?", "ERPNext", "universal"),
    ("What does net 30 mean on an invoice?", "ERPNext", "universal"),
    ("What is a duplicate payment in accounts payable?", "ERPNext", "universal"),
    ("What does the Is Paid checkbox do on an ERPNext purchase invoice?", "ERPNext", "app"),
    ("What does the Update Stock checkbox do on an ERPNext purchase invoice?", "ERPNext", "app"),
    ("How do I add a tag to a document in ERPNext?", "ERPNext", "app"),
    ("What does the Expense Head column in the ERPNext items table mean?", "ERPNext", "app"),
    ("In Zammad, what is the difference between an internal note and a public reply?", "Zammad", "app"),
    ("What does the pending reminder state do in Zammad?", "Zammad", "app"),
    ("How do I insert a text module into a Zammad reply?", "Zammad", "app"),
    ("What does the Owner field mean on a Zammad ticket?", "Zammad", "app"),
    ("What does an accounts payable clerk typically check before booking a supplier invoice?", "ERPNext", "occupation"),
    ("What is the standard three-way match process?", "ERPNext", "occupation"),
    ("What are the usual responsibilities of a tier 1 support agent?", "Zammad", "occupation"),
    ("How is a refund request usually handled in customer support?", "Zammad", "occupation"),
    ("What steps does a typical invoice approval process have?", "ERPNext", "occupation"),
    ("What does a helpdesk escalation process usually look like?", "Zammad", "occupation"),
    ("In most companies, who reviews vendor invoices before payment?", "ERPNext", "occupation"),
    ("What is the generic procedure for handling a disputed charge in customer service?", "Zammad", "occupation"),
    ("What is our capitalization threshold for equipment?", "ERPNext", "company"),
    ("Who approves invoices from the UK subsidiary here?", "ERPNext", "company"),
    ("What refund amount may tier 1 approve on its own?", "Zammad", "company"),
    ("Which cost center do we use for production machines?", "ERPNext", "company"),
    ("Is Velt on our preferred vendor list?", "ERPNext", "company"),
    ("What priority do escalated refund tickets get in this team?", "Zammad", "company"),
    ("How many days after the charge is a refund still allowed under our policy?", "Zammad", "company"),
    ("Who is the controller who signs off second approvals?", "ERPNext", "company"),
    ("Why did you change the expense head on this invoice?", "ERPNext", "personal_judgment"),
    ("What made you suspect this invoice was a duplicate?", "ERPNext", "personal_judgment"),
    ("Why did you put this ticket on high priority?", "Zammad", "personal_judgment"),
    ("How do you decide whether a customer is likely to dispute a charge?", "Zammad", "personal_judgment"),
    ("What told you this was a chargeback even though the ticket is in German?", "Zammad", "personal_judgment"),
    ("When would you make an exception and refund more than the limit yourself?", "Zammad", "personal_judgment"),
    ("Why did you skip replying to the customer before escalating?", "Zammad", "personal_judgment"),
    ("What would a new person most likely get wrong on this invoice?", "ERPNext", "personal_judgment"),
]

# Cue-free / adversarial phrasing (no "our", "why did you", "in ERPNext" keywords) — hand labels
UNKNOWNS_HARD: list[tuple[str, str, str]] = [
    ("Should this compressor go to Plants and Machineries?", "ERPNext", "company"),
    ("Is 7,200 too much for small tools?", "ERPNext", "company"),
    ("Does Velt tend to send the same invoice twice?", "ERPNext", "personal_judgment"),
    ("Pennick invoices — straight submit or something else?", "ERPNext", "company"),
    ("Production or Administration for this one?", "ERPNext", "personal_judgment"),
    ("Why 3 high for PRIORITY here?", "Zammad", "personal_judgment"),
    ("Tier 2 for a 59 euro refund?", "Zammad", "company"),
    ("Can tier 1 close this after refunding?", "Zammad", "company"),
    ("Rückbuchung means what exactly?", "Zammad", "universal"),
    ("Where do I see the customer's earlier tickets?", "Zammad", "app"),
    ("Is a goodwill refund normal for a SaaS helpdesk?", "Zammad", "occupation"),
    ("Mark as internal or public here?", "Zammad", "personal_judgment"),
    ("What does the little lock icon mean?", "Zammad", "app"),
    ("Four-eyes principle for payables, is that standard?", "ERPNext", "occupation"),
    ("Was that a mistake or on purpose?", "ERPNext", "personal_judgment"),
]

# Hand labels for capture-run questions (filled in after reading each run; key = normalized question text).
# guardrail: is it about a limit / stop condition / must-not rule?  screen: answerable from the visible screen
# alone?  dup_of: index of an earlier ask in the same session asking the same thing (or None).
HAND_LABELS: dict[str, dict[str, Any]] = {}
HAND_FILE = Path(__file__).parent / "hand_labels.json"
if HAND_FILE.exists():
    HAND_LABELS = json.loads(HAND_FILE.read_text())

SCOPE_JUDGE = """You label who can answer a question that an AI apprentice has while watching an expert work.
universal = general domain knowledge (definitions); app = what a software feature/field does (official docs);
occupation = the generic structure of this kind of job, same in any company; company = this organisation's own
rules/thresholds/approvers/vendors/policies; personal_judgment = this expert's own reasoning, heuristics, exceptions.
Return {"scope": one of [universal, app, occupation, company, personal_judgment]}."""

ASK_JUDGE = """You audit questions an AI apprentice asked an expert during a screen-recorded work session.
Given the question, the visible screen content at that moment, and the earlier questions in the session, return
{"guardrail": bool  (is it about a limit, threshold, stop condition, approval rule or must-not rule?),
 "answerable_from_screen": bool  (could a careful observer answer it from the visible screen content alone,
     without asking the expert?),
 "duplicate_of": int or null  (0-based index of an EARLIER question that asks essentially the same thing),
 "well_formed": bool (grammatical, specific, no garbled values)}"""


def norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def kappa(a: list[str], b: list[str]) -> Optional[float]:
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def scope_eval(judge: Judge, items: Optional[list] = None) -> dict:
    rows = []
    for q, app, hand in (items or UNKNOWNS):
        r = httpx.get(f"{API}/api/context/scope", params={"q": q, "app": app}, timeout=60).json()
        pred = r.get("scope")
        j = judge.ask(SCOPE_JUDGE, f"App: {app}\nQuestion: {q}")
        js = (j or {}).get("scope") if isinstance(j, dict) else None
        rows.append({"q": q, "app": app, "hand": hand, "claros": pred, "conf": r.get("confidence"), "judge": js})
        log(f"  scope {hand:<18} claros={pred!s:<18} judge={js!s:<18} {q[:60]}")
    acc_hand = sum(r["claros"] == r["hand"] for r in rows) / len(rows)
    jr = [r for r in rows if r["judge"] in SCOPES]
    acc_judge = sum(r["claros"] == r["judge"] for r in jr) / len(jr) if jr else None
    expert = {"company", "personal_judgment"}
    # the costly error: an expert-only question answered by the LLM/docs (company rule invented) or vice versa
    leaked = [r for r in rows if r["hand"] in expert and r["claros"] not in expert]
    wasted = [r for r in rows if r["hand"] not in expert and r["claros"] in expert]
    conf = {h: Counter(r["claros"] for r in rows if r["hand"] == h) for h in SCOPES}
    return {"n": len(rows), "accuracy_vs_hand": round(acc_hand, 3),
            "accuracy_vs_judge": round(acc_judge, 3) if acc_judge is not None else None,
            "hand_vs_judge_kappa": kappa([r["hand"] for r in jr], [r["judge"] for r in jr]) if jr else None,
            "expert_only_answered_by_claros": len(leaked), "generic_sent_to_expert": len(wasted),
            "ask_routing_accuracy": round(sum((r["hand"] in expert) == (r["claros"] in expert) for r in rows) / len(rows), 3),
            "confusion": {h: dict(c) for h, c in conf.items()}, "rows": rows}


def load_run(run: str) -> Optional[dict]:
    d = EVAL_DATA / run / "A"
    if not (d / "summary.json").exists():
        return None
    summ = json.loads((d / "summary.json").read_text())
    msgs = [json.loads(x) for x in (d / "messages.jsonl").read_text().splitlines()]
    logs = [json.loads(x) for x in (d / "server_log.jsonl").read_text().splitlines()]
    return {"summary": summ, "messages": msgs, "logs": logs}


def screen_text(logs: list[dict], t: float) -> str:
    states = [e for e in logs if e["kind"] == "screen.state" and (e.get("t") or 0) <= t]
    if not states:
        return "(no screen state)"
    s = states[-1]["payload"]
    parts = [f"{s.get('app')} | {s.get('view')} | {s.get('entity_type')} {s.get('entity_id')} | status {s.get('status')}"]
    parts += [f"{f.get('label')}: {f.get('value')}" for f in (s.get("fields") or [])[:40]]
    for tb in (s.get("tables") or [])[:2]:
        parts.append("table " + json.dumps({"columns": tb.get("columns"), "rows": (tb.get("rows") or [])[:3]},
                                          ensure_ascii=False))
    return "\n".join(parts)[:3000]


def run_metrics(run: str, judge: Judge) -> Optional[dict]:
    r = load_run(run)
    if r is None:
        return None
    summ = r["summary"]
    asks = [m for m in r["messages"] if m.get("dir") == "s2c" and m.get("type") == "ask"
            and m.get("unknown_id") != "phase-debrief"]
    spoken = {a["ask"]["unknown_id"]: a["ask"].get("_spoken") for a in summ.get("answered", [])}
    unk = {u["id"]: u for u in (summ.get("ledger") or {}).get("unknowns", [])}
    dur = summ.get("duration_s") or 1
    rows = []
    for i, m in enumerate(asks):
        u = unk.get(m["unknown_id"], {})
        q = m.get("text") or ""
        hand = HAND_LABELS.get(norm_q(q))
        j = judge.ask(ASK_JUDGE, f"Screen at that moment:\n{screen_text(r['logs'], m['_at'])}\n\nEarlier questions:\n" +
                      "\n".join(f"[{k}] {a.get('text')}" for k, a in enumerate(asks[:i])) + f"\n\nQuestion: {q}")
        rows.append({"q": q, "spoken": spoken.get(m["unknown_id"]), "type": u.get("type"), "scope": u.get("scope"),
                     "system_guardrail": u.get("type") in ("limit", "stop_and_ask", "never"), "judge": j, "hand": hand})
    n = len(rows)

    def share(key: str, src: str) -> Optional[float]:
        xs = [x[src].get(key) for x in rows if isinstance(x.get(src), dict) and key in x[src]]
        xs = [bool(v) if key != "duplicate_of" else v is not None for v in xs]
        return round(sum(xs) / len(xs), 3) if xs else None

    answered = summ.get("answered", [])
    seeded = {"erpnext": {"R_capex", "R_dup", "R_uk"}, "zammad": {"R_limit", "R_cb", "R_note"}}.get(run.split("/")[0], set())
    asked_rules = {(x["hand"] or {}).get("rule") for x in rows} - {None}
    return {"run": run, "duration_s": dur, "asks": n, "asks_per_10min": round(n / dur * 600, 2),
            "answered": len(answered),
            "guardrail_share_system": round(sum(x["system_guardrail"] for x in rows) / n, 3) if n else None,
            "guardrail_share_judge": share("guardrail", "judge"), "guardrail_share_hand": share("guardrail", "hand"),
            "answerable_from_screen_judge": share("answerable_from_screen", "judge"),
            "answerable_from_screen_hand": share("answerable_from_screen", "hand"),
            "duplicate_rate_judge": share("duplicate_of", "judge"), "duplicate_rate_hand": share("duplicate_of", "hand"),
            "well_formed_judge": share("well_formed", "judge"), "well_formed_hand": share("well_formed", "hand"),
            "seeded_rule_recall_hand": f"{len(asked_rules & seeded)}/{len(seeded)}",
            "unlabeled_by_hand": [x["q"] for x in rows if x["hand"] is None], "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None)
    ap.add_argument("--runs", default="erpnext/before,zammad/before")
    ap.add_argument("--skip-scope", action="store_true")
    a = ap.parse_args()
    judge = Judge()
    log("judge:", judge.name)
    runs = [m for m in (run_metrics(x, judge) for x in a.runs.split(",")) if m]
    for m in runs:
        log(f"{m['run']}: {m['asks']} asks / {m['duration_s']}s → {m['asks_per_10min']}/10min; "
            f"guardrail sys={m['guardrail_share_system']} judge={m['guardrail_share_judge']}; screen-answerable "
            f"judge={m['answerable_from_screen_judge']} hand={m['answerable_from_screen_hand']}")
    res: dict[str, Any] = {"judge": judge.name, "runs": runs}
    if not a.skip_scope:
        res["scope"] = scope_eval(judge)
        res["scope_hard"] = scope_eval(judge, UNKNOWNS_HARD)
        log(f"scope_hard: acc vs hand {res['scope_hard']['accuracy_vs_hand']} routing {res['scope_hard']['ask_routing_accuracy']}")
        s = res["scope"]
        log(f"scope: acc vs hand {s['accuracy_vs_hand']} vs judge {s['accuracy_vs_judge']} kappa(hand,judge)="
            f"{s['hand_vs_judge_kappa']} expert-only answered by Claros={s['expert_only_answered_by_claros']}")
    res["judge_calls"], res["judge_failures"] = judge.calls, judge.failures
    save_result("questions", res, a.tag)


if __name__ == "__main__":
    main()

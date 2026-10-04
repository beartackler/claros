"""Phase 5 — Work Map quality vs hand-written reference SOPs (WONDERBREAD-style step recall/precision).

    cd server && .venv/bin/python ../infra/evals/map_eval.py --erp-workflow <wid> --zam-workflow <wid> [--tag before]

Alignment of map steps to reference steps: independent judge (different model family), plus a lexical
fallback when no judge is configured. Rule coverage: which expert rules have a guardrail at all, and which
of those are deterministic predicates (Phase 5b numbers come from tutor_eval.py predicates).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from common import API, Judge, log, prf, save_result  # noqa: E402
from tutor_eval import rules_of_guardrail  # noqa: E402

REFERENCE = {
    "erpnext": [
        ("Open the purchase invoice list and pick the next draft supplier invoice", True),
        ("Open the invoice and check supplier, amount and the pre-filled expense account / cost center", True),
        ("If the line is equipment over 5,000, recode it to the fixed-asset account (Plants and Machineries) and "
         "the Production cost center", True),
        ("Save the corrected invoice", True),
        ("For a supplier with earlier paid invoices, compare amount / invoice number with already-paid invoices "
         "(December re-bills)", True),
        ("If it duplicates a paid invoice, put it on hold (tag + comment) and ask the supplier to confirm; do not book",
         True),
        ("Save the held invoice", True),
        ("For an invoice of the UK subsidiary (Ltd, GBP), request second approval from the controller instead of "
         "submitting", True),
    ],
    "zammad": [
        ("Open the unassigned / open ticket queue and pick a refund ticket", True),
        ("Read the request: amount, days since the charge, prior refunds, order id", True),
        ("If it is a chargeback / bank dispute / legal threat, move it to Tier 2 immediately without answering the "
         "substance", True),
        ("If amount <= 50 EUR, <= 14 days since the charge and no refund in 12 months, tier 1 approves the refund "
         "itself", True),
        ("Document the approved refund in an internal note and close the ticket", True),
        ("Otherwise do not promise a refund; add an internal note with order id, amount, days since charge and "
         "prior refunds", True),
        ("Move the ticket to Tier 2 Support with priority high", True),
        ("Reply to the customer in the customer's language", False),  # policy only — never demonstrated
    ],
}
RULES = {"erpnext": ["R_capex", "R_uk", "R_dup"], "zammad": ["R_limit", "R_days", "R_prior", "R_cb"]}

ALIGN = """You compare an AI-generated procedure (MAP steps) with a reference SOP (REF steps) for the same task.
A map step matches a ref step if it describes the same action/decision (wording may differ; a map step may
cover part of a ref step; one map step may match several ref steps). Return
{"matches": [[ref_index, map_index], ...], "map_steps_wrong": [map_index, ...] (steps that are factually wrong
or describe something the expert did not do)}. Indices are 0-based."""


def lexical_align(ref: list[str], steps: list[str]) -> list[list[int]]:
    def toks(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]{4,}", s.lower())}
    out = []
    for i, r in enumerate(ref):
        for j, s in enumerate(steps):
            a, b = toks(r), toks(s)
            if a and b and len(a & b) / min(len(a), len(b)) >= 0.4:
                out.append([i, j])
    return out


def evaluate(app: str, wm: dict, judge: Judge) -> dict:
    ref = [r for r, _ in REFERENCE[app]]
    demo = [i for i, (_, d) in enumerate(REFERENCE[app]) if d]
    steps = [f"{s['title']}" + (f" — decision: {s['decision']['description']}" if s.get("decision") else "")
             for s in sorted(wm.get("steps", []), key=lambda s: s.get("order", 0))]
    j = judge.ask(ALIGN, "REF:\n" + "\n".join(f"[{i}] {r}" for i, r in enumerate(ref)) +
                  "\n\nMAP:\n" + "\n".join(f"[{i}] {s}" for i, s in enumerate(steps)), max_tokens=600)
    matches = (j or {}).get("matches") if isinstance(j, dict) else None
    source = judge.name
    if not isinstance(matches, list):
        matches, source = lexical_align(ref, steps), "lexical"
    matches = [m for m in matches if isinstance(m, list) and len(m) == 2 and all(isinstance(x, int) for x in m)]
    ref_hit = {m[0] for m in matches if 0 <= m[0] < len(ref)}
    map_hit = {m[1] for m in matches if 0 <= m[1] < len(steps)}
    wrong = [x for x in ((j or {}).get("map_steps_wrong") or []) if isinstance(x, int)] if isinstance(j, dict) else []
    cov = {}
    for g in wm.get("guardrails", []):
        for r in rules_of_guardrail(g, app):
            cov.setdefault(r, []).append({"id": g["id"], "predicate": bool(g.get("predicate"))})
    return {"workflow_id": wm.get("workflow_id"), "version": wm.get("version"), "n_steps": len(steps),
            "n_ref": len(ref), "aligner": source,
            "step_recall": round(len(ref_hit) / len(ref), 3),
            "step_recall_demonstrated": round(len(ref_hit & set(demo)) / len(demo), 3),
            "step_precision": round(len(map_hit) / len(steps), 3) if steps else None,
            "map_steps_wrong": wrong, "missed_ref_steps": [ref[i] for i in range(len(ref)) if i not in ref_hit],
            "unmatched_map_steps": [steps[i] for i in range(len(steps)) if i not in map_hit],
            "rules_with_guardrail": sorted(cov), "rules_missing": [r for r in RULES[app] if r not in cov],
            "rules_with_predicate": sorted(r for r, gs in cov.items() if any(x["predicate"] for x in gs)),
            "guardrails": [{"id": g["id"], "text": g["text"], "predicate": g.get("predicate"), "fuzzy": g.get("fuzzy")}
                           for g in wm.get("guardrails", [])],
            "coverage": wm.get("coverage"), "exam": wm.get("exam"), "apps": wm.get("apps"), "steps": steps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--erp-workflow")
    ap.add_argument("--zam-workflow")
    ap.add_argument("--erp-file", help="evaluate a saved map json instead of the live one")
    ap.add_argument("--zam-file")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    judge = Judge()
    res: dict[str, Any] = {"judge": judge.name}
    for app, wid, f in (("erpnext", a.erp_workflow, a.erp_file), ("zammad", a.zam_workflow, a.zam_file)):
        if f:
            wm = json.loads(Path(f).read_text())
        elif wid:
            wm = httpx.get(f"{API}/api/workflows/{wid}", timeout=30).json()
        else:
            continue
        res[app] = evaluate(app, wm, judge)
        r = res[app]
        log(f"{app}: steps={r['n_steps']} recall={r['step_recall']} (demo {r['step_recall_demonstrated']}) "
            f"precision={r['step_precision']} rules missing={r['rules_missing']} predicates={r['rules_with_predicate']}")
    save_result("map", res, a.tag)


if __name__ == "__main__":
    main()

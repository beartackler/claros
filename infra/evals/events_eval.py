"""Phase 1b — screen-event precision/recall on the scripted capture sequences (live, as perceived in the run).

    cd server && .venv/bin/python ../infra/evals/events_eval.py [--tag before]

Ground truth = the scripted UI actions of erp_e2e A (infra/e2e/run_e2e.py phase_a) and zammad_e2e A. An emitted event
matches a truth action if the kind is compatible and (for edits) the field label matches. `navigate`/`open` of
list views are not scored (no action); duplicate events for one action count as false positives.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EVAL_DATA, log, prf, save_result  # noqa: E402

# (kind set, field regex or None, entity regex or None)
TRUTH = {
    "erpnext": [({"open"}, None, r"00027|0261|feldmark"), ({"edit", "select"}, r"expense", None),
                ({"edit", "select"}, r"cost", None), ({"save"}, None, None),
                ({"open"}, None, r"00028|1187|velt"), ({"hold", "edit", "select"}, r"tag|hold|status", None),
                ({"save"}, None, None), ({"open"}, None, r"00029|3622|pennick"),
                ({"escalate", "select", "edit", "other"}, r"status|workflow|pending|approv|state", None)],
    "zammad": [({"open"}, None, r"6801\d|charged twice"), ({"edit", "select", "escalate"}, r"group", None),
               ({"edit", "select"}, r"priorit", None), ({"save"}, None, None),
               ({"open"}, None, r"6801\d|add-on"), ({"edit", "select", "other"}, r"state|status", None),
               ({"save"}, None, None), ({"open"}, None, r"6801\d|rückbuchung"),
               ({"edit", "select", "escalate"}, r"group", None), ({"edit", "select"}, r"priorit", None),
               ({"save"}, None, None)],
}


def score(app: str, tag: str) -> dict:
    p = EVAL_DATA / app / tag / "A" / "server_log.jsonl"
    evs = [json.loads(x)["payload"] for x in p.read_text().splitlines() if json.loads(x)["kind"] == "screen.event"]
    # list-level open/navigate are not actions on a record
    evs = [e for e in evs if not (e["kind"] in ("navigate",) or (e["kind"] == "open" and not e.get("entity_id")))]
    used, tp, missed = set(), 0, []
    for kinds, fre, ere in TRUTH[app]:
        hit = None
        for i, e in enumerate(evs):
            if i in used or e["kind"] not in kinds:
                continue
            text = f"{e.get('field') or ''} {e.get('canonical') or ''} {e.get('summary') or ''}".lower()
            if fre and not re.search(fre, text):
                continue
            if ere and not re.search(ere, f"{e.get('entity_id') or ''} {e.get('summary') or ''}".lower()):
                continue
            hit = i
            break
        if hit is None:
            missed.append(f"{'/'.join(sorted(kinds))} {fre or ere or ''}")
        else:
            used.add(hit)
            tp += 1
    fps = [f"{e['kind']}: {e.get('summary')}" for i, e in enumerate(evs) if i not in used]
    r = prf(tp, len(fps), len(TRUTH[app]) - tp)
    return {**r, "n_truth": len(TRUTH[app]), "n_events": len(evs), "missed": missed, "false_positives": fps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="before")
    a = ap.parse_args()
    res = {}
    for app in ("erpnext", "zammad"):
        try:
            res[app] = score(app, a.tag)
            log(app, json.dumps({k: res[app][k] for k in ("precision", "recall", "missed")}))
        except FileNotFoundError:
            pass
    save_result("events", res, a.tag)


if __name__ == "__main__":
    main()

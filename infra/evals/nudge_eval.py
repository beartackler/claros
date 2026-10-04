"""Phase 6 — learner-nudge grading on cases the expert never showed (replay of real learner screens).

    cd server && .venv/bin/python ../infra/evals/nudge_eval.py [--tag after] [--no-decide]

For every learner case of the tutor eval (records created around each rule boundary, opened in the real app, read
by the real perception pipeline), take the FIRST record screen — the moment a prediction nudge is shown, before the
learner acts — and ask the nudge engine which option is right for that record. Ground truth = the expert rules'
CASE conditions from tutor_eval (facts only, independent of what the learner then chose):
  ERPNext  R_capex: equipment line > 5,000 · R_uk: UK subsidiary · R_dup: December + same supplier & amount as paid
  Zammad   policy_escalate: amount > 50 or > 14 days or a prior refund or a chargeback
A step's truth = "the rule's case applies" for the rules its guardrails cover (tutor_eval.rules_of_guardrail).

Reported per app: coverage (share of nudges graded), accuracy when graded, WRONG GRADES (graded and wrong — the
number that must stay ~0: a tutor that tells a learner they are wrong when they are right is worse than one that
says "I can't tell"), and the baseline that always marks the expert's demonstrated value right (the skew this
design removes). Limitation: the replay has no OCR free text (not logged), so facts that live only in a message
body (Zammad) are invisible to the decision model here; the live tutor does see them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402
from common import EVAL_DATA, RESULTS, log, save_result, wilson  # noqa: E402

import tutor_eval as TE  # noqa: E402


def case_truth(app: str, c: dict) -> dict[str, bool]:
    if app == "erpnext":
        return {"R_capex": c["item"] in TE.EQUIPMENT_ITEMS and c["rate"] > 5000,
                "R_uk": c["company"] == "sub",
                "R_dup": c["posting"][5:7] == "12" and any(s == c["supplier"] and abs(a - c["rate"]) < 0.005
                                                          for s, a, _ in TE.PAID)}
    pol = bool(TE.zam_rules(c))
    return {r: pol for r in ("R_limit", "R_days", "R_prior", "R_cb")}


def first_record_state(db: sqlite3.Connection, sid: str) -> list[dict]:
    rows = [json.loads(p) for (p,) in db.execute(
        "select payload from log where session_id=? and kind='screen.state' order by id", (sid,))]
    out, seen_record = [], False
    for s in rows:  # list screens before the record (session memory) + the first record screen
        out.append(s)
        if s.get("entity_id"):
            seen_record = True
            break
    return out if seen_record else []


async def grade(app: str, rec: dict, wm: Any, db: sqlite3.Connection, use_decide: bool) -> list[dict]:
    from claros.knowledge import nudges, tutor
    from claros.models import ScreenState
    states = first_record_state(db, rec.get("session_id") or "")
    if not states:
        return []
    sid = f"replay-{rec['session_id']}"
    tutor._states.pop(sid, None)
    st = tutor.start(sid, wm.workflow_id, rec["case"].get("lang", "en"),
                     {"id": "eval-learner", "name": "Learner", "role": "learner"})
    st.wm = wm
    for s in states:
        st.last_state = ScreenState.model_validate(s)
        tutor.remember(st, wm, st.last_state)
    truth = case_truth(app, rec["case"])
    out = []
    for step in wm.steps:
        if not (step.decision and step.decision.kind == "judgment" and step.guardrail_ids):
            continue
        rules = set()
        for gid in step.guardrail_ids:
            g = next((x for x in wm.guardrails if x.id == gid), None)
            if g:
                rules |= set(TE.rules_of_guardrail(g.model_dump(), app))
        rules &= set(truth)
        if not rules:
            continue
        applies = any(truth[r] for r in rules)
        opts = nudges.fallback_options(st, wm, step, st.lang)
        if not opts:
            continue
        how = await nudges.cross_check(st, wm, step, opts) if use_decide else "rules"
        dec = step.decision
        rule_side = {o.id for o in opts if o.action == "escalate" or (o.value and o.value == dec.to_value)}
        default_side = {o.id for o in opts if o.value and o.value == dec.from_value}
        correct = {o.id for o in opts if o.correct}
        graded = bool(correct)
        said_case = bool(correct & rule_side) if graded else None
        right = graded and ((said_case and applies) or (not said_case and applies is False and correct <= default_side))
        out.append({"case": rec["case"]["id"], "step": step.id, "rules": sorted(rules), "applies": applies,
                    "graded": graded, "how": how if graded else None, "said_case": said_case, "right": bool(right),
                    "baseline_right": applies,  # "the expert's demonstrated value is right" is right only in the case
                    "options": [o.label for o in opts], "correct": [o.label for o in opts if o.correct]})
    tutor._states.pop(sid, None)
    return out


def pct(k: int, n: int) -> Optional[float]:
    return round(100 * k / n, 1) if n else None


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    g = [r for r in rows if r["graded"]]
    right = [r for r in g if r["right"]]
    wrong = [r for r in g if not r["right"]]
    return {"nudges": n, "graded": len(g), "coverage": pct(len(g), n), "accuracy_when_graded": pct(len(right), len(g)),
            "accuracy_ci": wilson(len(right), len(g)), "wrong_grades": len(wrong), "wrong_grade_rate": pct(len(wrong), n),
            "by": {k: sum(1 for r in g if r["how"] == k) for k in ("rules", "agree", "decision_model")},
            "baseline_expert_value_accuracy": pct(sum(1 for r in rows if r["baseline_right"]), n),
            "wrong_examples": [{k: r[k] for k in ("case", "step", "applies", "correct")} for r in wrong[:8]]}


async def run(tag: Optional[str], use_decide: bool, source: str = "after") -> dict:
    common.load_env()
    src = EVAL_DATA / "eval.db"
    tmp = EVAL_DATA / "nudge_replay.db"
    shutil.copy(src, tmp)
    from claros.knowledge import _deps as d
    from claros.models import WorkMap
    from claros.store import Store
    d.STORE = Store(str(tmp))
    sent: list = []

    class _Bus:  # nothing leaves the replay
        def publish(self, sid, topic, payload=None):
            sent.append((topic, payload))

        def subscribe(self, *a):
            pass
    d.BUS = _Bus()
    db = sqlite3.connect(str(tmp))
    result: dict[str, Any] = {"source": source, "decision_model": use_decide, "apps": {}}
    for app in ("erpnext", "zammad"):
        path = RESULTS / f"tutor_{app}.{source}.json"
        if not path.exists():
            continue
        tr = json.loads(path.read_text())
        row = db.execute("select value from workflows where workflow_id=? and version=?",
                         (tr["workflow_id"], tr["map_version"])).fetchone()
        if not row:
            log(f"{app}: map {tr['workflow_id']} v{tr['map_version']} not in eval.db")
            continue
        wm = WorkMap.model_validate(json.loads(row[0]))
        rows: list[dict] = []
        for rec in tr["records"]:
            rows += await grade(app, rec, wm, db, use_decide)
        s = summarize(rows)
        log(f"{app}: {s['nudges']} nudges · graded {s['coverage']} · right when graded {s['accuracy_when_graded']} · "
            f"wrong grades {s['wrong_grades']} · baseline (expert's value) {s['baseline_expert_value_accuracy']}")
        result["apps"][app] = {"workflow_id": tr["workflow_id"], "summary": s, "rows": rows}
    db.close()
    tmp.unlink(missing_ok=True)
    save_result("nudges", result, tag)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None)
    ap.add_argument("--source", default="after", help="tutor eval run to replay (tutor_<app>.<source>.json)")
    ap.add_argument("--no-decide", dest="decide", action="store_false")
    a = ap.parse_args()
    asyncio.run(run(a.tag, a.decide, a.source))


if __name__ == "__main__":
    main()

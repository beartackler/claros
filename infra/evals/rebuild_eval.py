"""Rebuild the ERPNext eval map from the SAME expert capture session N times per builder prompt, then grade learner
nudges on the unseen cases against each map (nudge_eval). Measures whether the map's rules carry every condition
the expert stated — independent of prompt examples taken from the test bed.

    cd server && .venv/bin/python ../infra/evals/rebuild_eval.py --runs 3 [--old-prompt path/to/old_builder.py]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402
from common import EVAL_DATA, RESULTS, log, save_result  # noqa: E402

import nudge_eval as NE  # noqa: E402
import tutor_eval as TE  # noqa: E402


def old_system(path: str) -> str:
    src = Path(path).read_text()
    m = re.search(r'^SYSTEM = """(.*?)"""', src, re.S | re.M)
    return m.group(1)


async def main(runs: int, old: str | None) -> None:
    common.load_env()
    tmp = EVAL_DATA / "rebuild_replay.db"
    shutil.copy(EVAL_DATA / "eval.db", tmp)
    from claros.knowledge import _deps as d, builder
    from claros.models import WorkMap
    from claros.store import Store
    d.STORE = Store(str(tmp))

    class _Bus:
        def publish(self, *a, **k):
            pass

        def subscribe(self, *a):
            pass
    d.BUS = _Bus()
    tr = json.loads((RESULTS / "tutor_erpnext.after.json").read_text())
    db = sqlite3.connect(str(tmp))
    base = WorkMap.model_validate(json.loads(db.execute(
        "select value from workflows where workflow_id=? and version=?",
        (tr["workflow_id"], tr["map_version"])).fetchone()[0]))
    sid = base.session_ids[0]
    prompts = {"new": builder.SYSTEM}
    if old:
        prompts = {"old": old_system(old), **prompts}
    out: dict = {"session": sid, "runs": {}}
    for name, system in prompts.items():
        builder.SYSTEM = system
        for i in range(runs):
            wm = await builder.build_map(sid, workflow_id=f"rebuild_{name}_{i}", expert=base.experts[0], persist=False)
            rows = []
            for rec in tr["records"]:
                rows += await NE.grade("erpnext", rec, wm, db, use_decide=False)
            s = NE.summarize(rows)
            g = [{"text": x.text[:100], "action": x.action, "predicate": x.predicate,
                  "rules": TE.rules_of_guardrail(x.model_dump(), "erpnext")} for x in wm.guardrails]
            log(f"{name}#{i}: {len(wm.steps)} steps, {len(wm.guardrails)} guardrails · nudges {s['nudges']} graded "
                f"{s['coverage']}% right {s['accuracy_when_graded']}% wrong {s['wrong_grades']}")
            for x in g:
                log(f"    {x['action']:<18} {x['rules']} {json.dumps(x['predicate'])[:160]}")
            out["runs"][f"{name}#{i}"] = {"summary": s, "guardrails": g}
    db.close()
    tmp.unlink(missing_ok=True)
    save_result("rebuild", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--old-prompt", dest="old", default=None)
    a = ap.parse_args()
    asyncio.run(main(a.runs, a.old))

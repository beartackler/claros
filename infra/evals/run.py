"""Claros eval suite — one entry point. Produces docs/EVALS.md (auto block between AUTO markers).

Prereqs: ERPNext :8080 + Zammad :8081 seeded (infra/README.md); a PRIVATE non-reloading Claros server on a scratch
DB copy (never the shared :8787):

    sqlite3 data/claros.db ".backup data/evals/eval.db"
    cd server && set -a && . ../.env && set +a && \\
      CLAROS_DB=$PWD/../data/evals/eval.db .venv/bin/uvicorn claros.app:app --port 8789 > ../data/evals/server.log 2>&1 &

Then (from server/):
    .venv/bin/python ../infra/evals/run.py all --tag before      # full suite, ~45 min, resets ERPNext once
    .venv/bin/python ../infra/evals/run.py <step> --tag after     # steps: golden perception erpnext zammad tutor
                                                                  #        predicates questions map safety voice report
    .venv/bin/python ../infra/evals/run.py report                 # rebuild docs/EVALS.md from data/evals/results

Judge: Gemini via OpenRouter if OPENROUTER_API_KEY, else Cloudflare Workers AI Llama-3.3-70B, else "unjudged".
Ground truth never comes from Claros: REST APIs of the apps + the expert rules written in tutor_eval.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from common import EVAL_DATA, ROOT, log  # noqa: E402

PY = sys.executable


def sh(*args: str) -> None:
    log("$", " ".join(args))
    subprocess.run([PY, *args], check=False, cwd=ROOT / "server")


def wid(app: str, tag: str) -> str:
    st = EVAL_DATA / app / tag / "state.json"
    if not st.exists():
        st = EVAL_DATA / app / "before" / "state.json"
    return json.loads(st.read_text())["workflow_id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step")
    ap.add_argument("--tag", default="before")
    a = ap.parse_args()
    t, s = a.tag, a.step
    steps = ["golden", "perception", "erpnext", "zammad", "events", "tutor", "predicates", "questions", "map", "safety",
             "voice", "report"] if s == "all" else [s]
    for st in steps:
        if st == "golden":
            sh(str(HERE / "golden.py"), "all")
        elif st == "perception":
            sh(str(HERE / "perception_eval.py"), "--tag", t)
        elif st in ("erpnext", "zammad"):
            script = "erp_e2e.py" if st == "erpnext" else "zammad_e2e.py"
            for ph in ("A", "B", "C", "RU"):
                sh(str(HERE / script), ph, "--tag", t)
        elif st == "events":
            sh(str(HERE / "events_eval.py"), "--tag", t)
        elif st == "tutor":
            sh(str(HERE / "tutor_eval.py"), "erpnext", "--workflow", wid("erpnext", t), "--tag", t)
            sh(str(HERE / "tutor_eval.py"), "zammad", "--workflow", wid("zammad", t), "--tag", t)
        elif st == "predicates":
            sh(str(HERE / "tutor_eval.py"), "predicates", "--for-app", "erpnext", "--workflow", wid("erpnext", t),
               "--tag", t)
            sh(str(HERE / "tutor_eval.py"), "predicates", "--for-app", "zammad", "--workflow", wid("zammad", t),
               "--tag", t)
        elif st == "questions":
            sh(str(HERE / "questions_eval.py"), "--tag", t, "--runs", f"erpnext/{t},zammad/{t}")
        elif st == "map":
            sh(str(HERE / "map_eval.py"), "--erp-workflow", wid("erpnext", t), "--zam-workflow", wid("zammad", t),
               "--tag", t)
        elif st == "safety":
            sh(str(HERE / "safety_eval.py"), "all", "--erp-workflow", wid("erpnext", t), "--zam-workflow",
               wid("zammad", t), "--tag", t)
        elif st == "voice":
            sh(str(HERE / "voice_metrics.py"), "--tag", t)
        elif st == "report":
            import report
            report.write()


if __name__ == "__main__":
    main()

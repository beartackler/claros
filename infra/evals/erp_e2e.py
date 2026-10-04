"""Re-run the existing ERPNext E2E harness (infra/e2e/run_e2e.py, unchanged) against the private eval server,
writing to data/evals/erpnext/<tag>/ instead of the shared fixture folder.

    cd server && .venv/bin/python ../infra/evals/erp_e2e.py A|B|C|RU [--tag before]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402,F401  (sets CLAROS_E2E_API before claros_client import)
from common import EVAL_DATA  # noqa: E402

import run_e2e  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase")
    ap.add_argument("--tag", default="before")
    a = ap.parse_args()
    out = EVAL_DATA / "erpnext" / a.tag
    out.mkdir(parents=True, exist_ok=True)
    run_e2e.OUT = out
    run_e2e.STATE = out / "state.json"
    ph = a.phase.upper()
    fn = {"A": run_e2e.phase_a, "B": run_e2e.phase_b, "C": lambda: run_e2e.phase_c("en"),
          "RU": lambda: run_e2e.phase_c("ru")}[ph]
    asyncio.run(fn())


if __name__ == "__main__":
    main()

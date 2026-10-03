"""Download the O*NET database (CC BY 4.0) into data/onet/ and build the FTS index.

Idempotent: skips the download if the CSVs are present and the index if it is newer.

    uv run python -m claros.context.onet_fetch [--force]

O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training
Administration (USDOL/ETA). Used under the CC BY 4.0 license.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger("claros.context.onet")

ONET_VERSION = os.environ.get("ONET_VERSION", "31_0")
ONET_URL = f"https://www.onetcenter.org/dl_files/database/db_{ONET_VERSION}_csv.zip"

# files we need (O*NET 31 names; "Technology Skills" is now software_skills.csv,
# DWA reference lives in gwas_to_iwas_to_dwas.csv)
NEEDED = [
    "occupation_data.csv",
    "task_statements.csv",
    "tasks_to_dwas.csv",
    "gwas_to_iwas_to_dwas.csv",
    "software_skills.csv",
    "job_titles.csv",
    "sample_of_reported_titles.csv",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def onet_dir() -> Path:
    return Path(os.environ.get("CLAROS_ONET_DIR", repo_root() / "data" / "onet"))


def csv_dir() -> Path:
    return onet_dir() / "csv"


def have_csvs(d: Path | None = None) -> bool:
    d = d or csv_dir()
    return all((d / f).exists() for f in NEEDED[:3])


def fetch(force: bool = False) -> Path:
    d = csv_dir()
    if have_csvs(d) and not force:
        log.info("O*NET CSVs already present at %s", d)
        return d
    d.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", ONET_URL)
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        r = c.get(ONET_URL)
        r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base in NEEDED:
                (d / base).write_bytes(z.read(name))
    (onet_dir() / "VERSION").write_text(f"{ONET_VERSION}\n{ONET_URL}\nCC BY 4.0 USDOL/ETA\n")
    return d


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    force = "--force" in argv
    fetch(force=force)
    from claros.context import onet

    path = onet.build_index(force=force)
    print(f"O*NET index ready: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

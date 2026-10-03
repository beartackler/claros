"""Claros server package."""
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

# CLAROS_PROFILE=slim (cloud, 512 MB): defaults only — any explicitly set env var wins.
import os as _os

if _os.getenv("CLAROS_PROFILE", "").lower() == "slim":
    for _k, _v in {"CLAROS_PII_MODEL": "off", "CLAROS_GLINER2": "0", "CLAROS_CRAWL4AI": "0",
                   "CLAROS_DECIDER_OLLAMA": "0", "CLAROS_OCR_THREADS": "1", "CLAROS_AUTOSEED": "1"}.items():
        _os.environ.setdefault(_k, _v)

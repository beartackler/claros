"""Phase 7 — voice-loop latency from REAL ElevenLabs calls (run after a manual test call).

    cd server && .venv/bin/python ../infra/evals/voice_metrics.py [--since-hours 24] [--conversation <id>] [--tag manual]

ElevenLabs only exposes per-turn latency after a real conversation exists:
  GET https://api.elevenlabs.io/v1/convai/conversations?agent_id=<ELEVENLABS_AGENT_ID>   (list, newest first)
  GET https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}                 (details)
Per agent turn, `transcript[i].conversation_turn_metrics.metrics` holds named timings, each {"elapsed_time": s}
(e.g. convai_llm_service_ttfb, convai_llm_service_ttf_sentence, convai_tts_service_ttfb, ...; names vary by
release, so every metric found is aggregated). Our custom LLM (/llm/v1/chat/completions) is the
"llm_service" part; its server-side TTFT is also measured headlessly in the E2E runs (turn.ttft_ms).
Header: xi-api-key (never printed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from common import EVAL_DATA, log, pct, save_result  # noqa: E402

BASE = "https://api.elevenlabs.io/v1/convai"


def headless_ttft() -> dict:
    """Server-side custom-LLM TTFT measured by the headless E2E harness (no audio)."""
    out = defaultdict(list)
    for p in EVAL_DATA.glob("*/*/*/summary.json"):
        try:
            s = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        for t in s.get("turns", []):
            if t.get("ttft_ms") is not None:
                out[t.get("kind", "user")].append(t["ttft_ms"])
    return {k: {"n": len(v), "p50_ms": pct(v, .5), "p95_ms": pct(v, .95)} for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-hours", type=float, default=24)
    ap.add_argument("--conversation", default=None)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    key, agent = os.getenv("ELEVENLABS_API_KEY"), os.getenv("ELEVENLABS_AGENT_ID")
    res: dict = {"headless_custom_llm_ttft": headless_ttft()}
    if not key:
        log("ELEVENLABS_API_KEY missing; only headless numbers")
        save_result("voice", res, a.tag)
        return
    h = httpx.Client(timeout=30, headers={"xi-api-key": key})
    if a.conversation:
        ids = [a.conversation]
    else:
        r = h.get(f"{BASE}/conversations", params={"agent_id": agent, "page_size": 50}).json()
        cutoff = time.time() - a.since_hours * 3600
        ids = [c["conversation_id"] for c in r.get("conversations", []) if (c.get("start_time_unix_secs") or 0) >= cutoff]
    metrics: dict[str, list[float]] = defaultdict(list)
    convs = []
    for cid in ids:
        d = h.get(f"{BASE}/conversations/{cid}").json()
        turns = d.get("transcript") or []
        n_agent = 0
        for t in turns:
            m = ((t.get("conversation_turn_metrics") or {}).get("metrics") or {})
            if t.get("role") == "agent":
                n_agent += 1
            for name, v in m.items():
                el = v.get("elapsed_time") if isinstance(v, dict) else v
                if isinstance(el, (int, float)):
                    metrics[name].append(el * 1000)
        # user-end → agent-start gap from transcript timestamps (coarse, 1 s resolution)
        for prev, cur in zip(turns, turns[1:]):
            if prev.get("role") == "user" and cur.get("role") == "agent" and \
                    isinstance(prev.get("time_in_call_secs"), (int, float)) and isinstance(cur.get("time_in_call_secs"), (int, float)):
                metrics["transcript_user_to_agent_gap"].append((cur["time_in_call_secs"] - prev["time_in_call_secs"]) * 1000)
        convs.append({"id": cid, "status": d.get("status"), "duration_s": (d.get("metadata") or {}).get("call_duration_secs"),
                      "turns": len(turns), "agent_turns": n_agent})
    res["conversations"] = convs
    res["elevenlabs_turn_metrics_ms"] = {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95)} for k, v in metrics.items()}
    if not ids:
        res["note"] = "no ElevenLabs conversations in window — run a real call in the web app, then re-run this script"
    log(json.dumps(res, indent=1))
    save_result("voice", res, a.tag)


if __name__ == "__main__":
    main()

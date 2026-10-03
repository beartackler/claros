"""Replay a folder of keyframe JPEGs through the perception pipeline (for evals).

    python -m claros.perception.replay data/fixtures/frames/de [--vision] [--json] [--step-ms 1500]
        [--lang de] [--activity typing]

Frames are processed in filename order with t = i * step_ms. Without --vision only the OCR
heuristics run (no LLM calls). Nothing is persisted unless --persist.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from .pipeline import SessionPipeline


async def run(folder: str, vision: bool = False, step_ms: float = 1500.0, as_json: bool = False,
              persist: bool = False, lang: str | None = None, activity: str | None = None) -> dict:
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    out: list[dict[str, Any]] = []

    def publish(sid: str, topic: str, payload: Any) -> None:
        if topic == "screen.state":
            out.append({"topic": topic, "seq": payload.seq, "state": payload.model_dump(mode="json")})
        elif topic == "screen.events":
            out.append({"topic": topic, "events": [e.model_dump(mode="json") for e in payload]})
        elif topic == "ws.out" and payload.get("type") == "context_update":
            out.append({"topic": "context_update", "text": payload["text"]})

    async def no_vision(msgs, jpeg):  # noqa: ANN001
        return None

    pipe = SessionPipeline(f"replay_{Path(folder).name}", publish, vision=None if vision else no_vision,
                           persist=persist, lang_hint=lang)
    for i, p in enumerate(files):
        t = i * step_ms
        if activity:
            pipe.on_activity({"t": t - 300, "kind": activity, "tiles_changed": []})
        data = p.read_bytes()
        if p.suffix.lower() == ".png":
            import io

            from PIL import Image
            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).convert("RGB").save(buf, "JPEG", quality=85)
            data = buf.getvalue()
        await pipe.on_keyframe({"t": t, "seq": i, "reason": "boundary" if i == 0 else "settle",
                                "jpeg_b64": base64.b64encode(data).decode()})
        if vision:
            await pipe.drain(30)
        if not as_json:
            st = pipe.published
            print(f"\n== {p.name}  seq={i}")
            if st:
                from .state import summarize
                print("  state:", summarize(st, max_fields=20))
    await pipe.drain(30 if vision else 1)
    pipe.close()
    timings = pipe.timings
    summary = {
        "frames": len(files),
        "ocr_ms_median": statistics.median([x["ocr_ms"] for x in timings]) if timings else None,
        "pii_ms_median": statistics.median([x["pii_ms"] for x in timings]) if timings else None,
        "total_ms_median": statistics.median([x["total_ms"] for x in timings]) if timings else None,
    }
    result = {"items": out, "timings": timings, "summary": summary}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print("\n== events")
        for it in out:
            if it["topic"] == "screen.events":
                for e in it["events"]:
                    print(f"  [{e['seq']}] {e['kind']:<8} {e['source']:<7} {e['summary']}")
            elif it["topic"] == "context_update":
                print(f"  ctx> {it['text']}")
        print("\n== timings", json.dumps(summary))
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m claros.perception.replay")
    ap.add_argument("folder")
    ap.add_argument("--vision", action="store_true", help="call the vision LLM (claros.llm)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--persist", action="store_true", help="write redacted keyframes + store rows")
    ap.add_argument("--step-ms", type=float, default=1500.0)
    ap.add_argument("--lang", default=None)
    ap.add_argument("--activity", default=None, help="inject this activity kind before every frame")
    a = ap.parse_args(argv)
    import logging
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(a.folder, a.vision, a.step_ms, a.json, a.persist, a.lang, a.activity))


if __name__ == "__main__":
    main(sys.argv[1:])

"""Phase 1a — perception vs. golden REST labels (single keyframes).

    cd server && .venv/bin/python ../infra/evals/perception_eval.py [--tag before] [--limit N] [--app erpnext|zammad]

Every golden frame runs through a FRESH claros.perception SessionPipeline (persist=False), i.e. the
production path: redaction → OCR → heuristic ScreenState → vision LLM → merged ScreenState.
Scores both the heuristic-only state (before vision lands) and the final state.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
from common import GOLDEN, best_substring_cer, log, mean, norm_text, pct, save_result, value_match  # noqa: E402

from claros.perception.pipeline import SessionPipeline, default_vision  # noqa: E402

LIST_KINDS = {"list", "overview"}


def _n(s: Any) -> str:
    return re.sub(r"[^\w#]+", " ", norm_text(s)).strip()


def label_match(pred_label: str, labels: list[str], strict: bool = True) -> bool:
    """strict: normalized equality; else the on-screen label may carry a suffix ("Amount (EUR) *", "Status:")."""
    pl = _n(re.sub(r"\(.*?\)|\*|\[\d+\]$", "", pred_label or ""))
    for lab in labels:
        ll = _n(lab)
        if not ll or not pl:
            continue
        if pl == ll or (not strict and len(ll) > 3 and pl.startswith(ll + " ")):
            return True
    return False


def find_value(st: dict, fld: dict) -> tuple[Optional[str], str]:
    """Value Claros read for this truth field: (value, where). Exact label first, then suffixed label."""
    for strict in (True, False):
        for f in st.get("fields") or []:
            if label_match(f.get("label") or "", fld["labels"], strict):
                return f.get("value"), "field"
        for t in st.get("tables") or []:
            cols = t.get("columns") or []
            rows = t.get("rows") or []
            for ci, c in enumerate(cols):
                if label_match(str(c), fld["labels"], strict) and rows:
                    r0 = rows[0]
                    if isinstance(r0, dict):
                        v = r0.get(c)
                    elif isinstance(r0, list) and ci < len(r0):
                        v = r0[ci]
                    else:
                        v = None
                    return (str(v) if v is not None else None), "table"
    return None, "missing"


def entity_ok(pred: Optional[str], lab: dict) -> bool:
    if not pred:
        return False
    p = re.sub(r"^(ticket|заявка)?\s*#?\s*", "", norm_text(pred))
    for a in lab.get("entity_alias") or [lab.get("entity_id")]:
        a2 = re.sub(r"^#", "", norm_text(a or ""))
        if a2 and (p == a2 or (len(a2) >= 5 and a2 in norm_text(pred))):
            return True
    return False


def score(lab: dict, st: Optional[dict], lines: list[str]) -> dict:
    st = st or {}
    out: dict[str, Any] = {}
    out["app_ok"] = lab["app"].lower() in norm_text(st.get("app"))
    vt = _n(f"{st.get('view') or ''} {st.get('entity_type') or ''}")
    types = [_n(lab["entity_type"]), _n(lab.get("entity_type_l10n") or "")]
    type_ok = any(t and t in vt for t in types)
    is_list = lab["kind"] in LIST_KINDS
    kind_ok = (not st.get("entity_id")) if is_list else bool(st.get("entity_id"))
    out["view_ok"] = bool(type_ok and kind_ok)
    out["kind_ok"] = kind_ok
    if not is_list:
        out["entity_ok"] = entity_ok(st.get("entity_id"), lab)
        if lab.get("status"):
            ps = norm_text(st.get("status"))
            out["status_ok"] = bool(ps) and any(norm_text(s) == ps or norm_text(s) in ps for s in lab["status"])
    fres = []
    for fld in lab.get("fields") or []:
        v, where = find_value(st, fld)
        ex, le = value_match(fld["value"], v)
        if not ex and v and str(v).strip().startswith(f"{fld['value']}:"):
            ex = le = True  # link shown as "CODE: name…" on screen
        if not ex and fld.get("value_l10n"):
            ex2, le2 = value_match(fld["value_l10n"], v)
            ex, le = ex or ex2, le or le2
        fres.append({"key": fld["key"], "truth": fld["value"], "pred": v, "where": where, "exact": ex, "lenient": le})
    out["fields"] = fres
    cers = []
    for num in lab.get("numbers") or []:
        c, got = best_substring_cer(num, lines)
        cers.append({"truth": num, "ocr": got, "cer": c})
    out["numbers"] = cers
    return out


async def run_frame(jpg: Path, lab: dict, vision: bool = True) -> dict:
    heur: dict = {}
    states: list[dict] = []
    vision_ms: list[float] = []

    def publish(sid: str, topic: str, payload: Any) -> None:
        if topic == "screen.state":
            states.append(payload.model_dump(mode="json"))

    async def timed_vision(msgs: list[dict], jpeg: bytes) -> Any:
        t0 = time.perf_counter()
        try:
            return await default_vision(msgs, jpeg)
        finally:
            vision_ms.append(round((time.perf_counter() - t0) * 1000))

    async def no_vision(msgs: list[dict], jpeg: bytes) -> Any:
        return None

    pipe = SessionPipeline(f"eval_{lab['id']}", publish, vision=timed_vision if vision else no_vision, persist=False,
                           lang_hint=None)
    data = jpg.read_bytes()
    t0 = time.perf_counter()
    await pipe.on_keyframe({"t": 0.0, "seq": 0, "reason": "boundary", "jpeg_b64": base64.b64encode(data).decode(),
                            "dims": [1600, 1000]})
    heur_ms = round((time.perf_counter() - t0) * 1000)
    heur = dict(states[-1]) if states else {}
    lines = [ln.text for ln in (pipe.tracker.prev_lines or [])]
    await pipe.drain(60)
    total_ms = round((time.perf_counter() - t0) * 1000)
    final = states[-1] if states else {}
    pipe.close()
    tm = pipe.timings[-1] if pipe.timings else {}
    return {"id": lab["id"], "cfg": lab["cfg"], "app": lab["app"], "kind": lab["kind"],
            "heuristic": score(lab, heur, lines), "final": score(lab, final, lines),
            "pred": {k: final.get(k) for k in ("app", "view", "entity_type", "entity_id", "status")},
            "state_final": final, "state_heuristic": heur, "ocr_lines": lines,
            "lat": {"ocr_redact_ms": tm.get("total_ms"), "ocr_ms": tm.get("ocr_ms"), "pii_ms": tm.get("pii_ms"),
                    "heuristic_state_ms": heur_ms, "vision_ms": vision_ms[0] if vision_ms else None,
                    "final_state_ms": total_ms, "vision_ok": bool(vision_ms) and final is not heur}}


def aggregate(rows: list[dict], which: str = "final") -> dict:
    def rate(key: str, sub: list[dict]) -> Optional[float]:
        xs = [r[which][key] for r in sub if key in r[which]]
        return round(sum(xs) / len(xs), 3) if xs else None

    def fields(sub: list[dict], k: str) -> Optional[float]:
        xs = [f[k] for r in sub for f in r[which]["fields"]]
        return round(sum(xs) / len(xs), 3) if xs else None

    def found(sub: list[dict]) -> Optional[float]:
        xs = [f["where"] != "missing" for r in sub for f in r[which]["fields"]]
        return round(sum(xs) / len(xs), 3) if xs else None

    def cer(sub: list[dict]) -> Optional[float]:
        return mean([n["cer"] for r in sub for n in r[which]["numbers"]])

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        groups[r["app"]].append(r)
        groups[f"{r['app']}/{r['cfg']['theme'] if r['cfg']['theme'] == 'dark' else ''}"
               f"{'z' + r['cfg']['zoom'] if r['cfg']['zoom'] != '100' else ''}{r['cfg']['lang'] if r['cfg']['lang'] != 'en' else ''}"
               or "x"].append(r)
    out = {}
    for g, sub in sorted(groups.items()):
        g = g.rstrip("/") if not g.endswith("/") else g + "en"
        out[g] = {"n": len(sub), "app": rate("app_ok", sub), "view": rate("view_ok", sub),
                  "entity": rate("entity_ok", sub), "status": rate("status_ok", sub),
                  "field_exact": fields(sub, "exact"), "field_lenient": fields(sub, "lenient"),
                  "field_found": found(sub), "n_fields": sum(len(r[which]["fields"]) for r in sub),
                  "num_cer": cer(sub), "n_numbers": sum(len(r[which]["numbers"]) for r in sub)}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--app", default=None)
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--rescore", action="store_true", help="re-score saved states of --tag (no model calls)")
    a = ap.parse_args()
    if a.rescore:
        from common import load_result
        old = load_result("perception", a.tag)
        labs = {json.loads(j.read_text())["id"]: json.loads(j.read_text()) for j in GOLDEN.glob("*/*.json")}
        for r in old["rows"]:
            lab = labs[r["id"]]
            r["final"] = score(lab, r["state_final"], r["ocr_lines"])
            r["heuristic"] = score(lab, r["state_heuristic"], r["ocr_lines"])
        old["final"], old["heuristic"] = aggregate(old["rows"], "final"), aggregate(old["rows"], "heuristic")
        for k in ("phase", "tag", "at"):
            old.pop(k, None)
        save_result("perception", old, a.tag)
        print(json.dumps(old["final"]["all"], indent=1))
        return
    frames = sorted(GOLDEN.glob("*/*.json"))
    if a.app:
        frames = [f for f in frames if f.parent.name == a.app]
    if a.limit:
        frames = frames[: a.limit]
    rows = []
    for jf in frames:
        lab = json.loads(jf.read_text())
        r = await run_frame(jf.with_suffix(".jpg"), lab, vision=not a.no_vision)
        f = r["final"]
        log(f"{lab['id']:<48} view={f['view_ok']!s:5} ent={f.get('entity_ok')!s:5} st={f.get('status_ok')!s:5} "
            f"fields={sum(x['exact'] for x in f['fields'])}/{len(f['fields'])} vis={r['lat']['vision_ms']}ms "
            f"pred={r['pred']['view']!r}/{r['pred']['entity_id']!r}/{r['pred']['status']!r}")
        rows.append(r)
    lat = {k: {"p50": pct([r["lat"][k] for r in rows], .5), "p95": pct([r["lat"][k] for r in rows], .95)}
           for k in ("ocr_redact_ms", "heuristic_state_ms", "vision_ms", "final_state_ms")}
    res = {"n_frames": len(rows), "final": aggregate(rows, "final"), "heuristic": aggregate(rows, "heuristic"),
           "latency": lat, "vision_failures": sum(1 for r in rows if r["lat"]["vision_ms"] is None), "rows": rows}
    p = save_result("perception", res, a.tag)
    log("saved", p)
    print(json.dumps({"final": res["final"], "latency": lat}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())

"""Trust controls on the stored record: "strike that" and going off the record.

- strike(session_id): everything captured in the last STRIKE_WINDOW_S seconds (utterances, screen states/events,
  keyframes, ledger items) is tombstoned in the session log (kind → "struck:<kind>", payload → {"struck": true}),
  redacted keyframe files are deleted, and a `privacy.struck` row records the interval. Map builder replay skips
  tombstones, so struck content never reaches the Work Map or the debrief.
- redact_before_off_record(session_id): the web mutes the mic while off the record, so the server hears nothing
  until the user taps Resume. What it did hear is the request itself, and often the sentence right before it
  ("my salary is… let's go off the record"): user utterances logged within OFF_RECORD_LOOKBACK_S are redacted.
Interval checks use the server receive time (`ts`), never client clocks.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("claros.privacy")

STRIKE_WINDOW_S = 30.0
OFF_RECORD_LOOKBACK_S = 5.0
_STRIKABLE = ("utterance", "event", "state", "keyframe", "unknown", "ledger", "dialog.say", "intent")


def _store() -> Any:
    from .store import store
    return store


def _ids_in(payload: Any) -> tuple[set[str], set[str], set[str]]:
    """(event ids, keyframe ids, utterance ids) referenced by a log payload."""
    ev, kf, ut = set(), set(), set()
    items = payload.get("items", [payload]) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        if x.get("id") and "kind" in x and "seq" in x:
            ev.add(str(x["id"]))
        if x.get("keyframe_id"):
            kf.add(str(x["keyframe_id"]))
        if x.get("event_id"):
            ut.add(str(x["event_id"]))
    return ev, kf, ut


def _tombstone(rows: list[dict]) -> None:
    st = _store()
    for r in rows:
        st.execute("UPDATE log SET kind=?, payload=? WHERE id=?",
                   (f"struck:{r['kind']}", json.dumps({"struck": True}), r["id"]))


def _rows_since(session_id: str, since_ts: float, kinds: tuple[str, ...]) -> list[dict]:
    st = _store()
    rows = st.execute("SELECT id, kind, ts, payload FROM log WHERE session_id=? AND ts>=? ORDER BY id",
                      (session_id, since_ts)).fetchall()
    out = []
    for r in rows:
        k = r["kind"] or ""
        if k.startswith("struck:") or not any(x in k for x in kinds):
            continue
        try:
            p = json.loads(r["payload"])
        except Exception:  # noqa: BLE001
            p = None
        out.append({"id": r["id"], "kind": k, "ts": r["ts"], "payload": p})
    return out


def _delete_keyframes(ids: set[str]) -> int:
    st = _store()
    n = 0
    for kid in ids:
        try:
            kf = st.get_keyframe(kid)
            if kf and kf.get("path") and not (kf.get("meta") or {}).get("fixture"):
                p = Path(kf["path"])
                if p.is_file():
                    os.remove(p)
            st.execute("DELETE FROM keyframes WHERE id=?", (kid,))
            n += 1
        except Exception:  # noqa: BLE001
            log.debug("keyframe delete failed %s", kid, exc_info=True)
    return n


def strike(session_id: str, window_s: float = STRIKE_WINDOW_S, now: Optional[float] = None) -> dict:
    """Tombstone the last `window_s` seconds of capture. Returns {from_ts, to_ts, rows, events, keyframes, utterances}."""
    now = now if now is not None else time.time()
    rows = _rows_since(session_id, now - window_s, _STRIKABLE)
    ev, kf, ut = set(), set(), set()
    for r in rows:
        e, k, u = _ids_in(r["payload"])
        ev |= e
        kf |= k
        ut |= u
        if "keyframe" in r["kind"] and isinstance(r["payload"], dict):
            seq = r["payload"].get("seq")
            if seq is not None:
                kf.add(f"{session_id}_{seq}")
    _tombstone(rows)
    nk = _delete_keyframes(kf)
    out = {"from_ts": now - window_s, "to_ts": now, "rows": len(rows), "events": sorted(ev),
           "keyframes": nk, "utterances": sorted(ut)}
    _store().log(session_id, "privacy.struck", out, None)
    _forget_live(session_id, out)
    return out


def _forget_live(session_id: str, struck: dict) -> None:
    """Drop struck items from the live ledger (unknowns opened in the window, their events, answers given in it)."""
    try:
        from .brain.ledger import get_ledger
    except Exception:  # noqa: BLE001
        return
    lg = get_ledger(session_id)
    evs = set(struck["events"])
    utts = set(struck["utterances"])
    since_ms = struck["from_ts"] * 1000.0
    lg.events = [e for e in lg.events if e.id not in evs]
    for u in lg.unknowns.values():
        if u.created_t >= since_ms or (set(u.about_event_ids) and set(u.about_event_ids) <= evs):
            u.status = "dropped"
            u.expires_t = None
        elif u.status == "answered" and u.resolution_source == "expert" and (
                set(u.answer_utterance_ids) & utts or lg.meta.get(u.id, {}).get("answered_ms", 0) >= since_ms):
            u.status, u.resolution, u.extracted_rule, u.answer_utterance_ids = "open", None, None, []


def redact_before_off_record(session_id: str, lookback_s: float = OFF_RECORD_LOOKBACK_S,
                             now: Optional[float] = None) -> int:
    """Redact user utterances logged in the last `lookback_s` s (the off-record request and what preceded it)."""
    now = now if now is not None else time.time()
    st = _store()
    n = 0
    for r in _rows_since(session_id, now - lookback_s, ("utterance",)):
        p = r["payload"]
        if isinstance(p, dict) and p.get("role", "user") == "user" and p.get("text") not in (None, "[off the record]"):
            st.execute("UPDATE log SET payload=? WHERE id=?", (json.dumps({**p, "text": "[off the record]"}), r["id"]))
            n += 1
    return n

"""Expert voice clips (consented, PII-free) so the tutor can replay the expert's own words. No voice clones.

hello {..., consent: {voice_clips: true}}          → session.extra["consent_voice_clips"] = True
POST /api/sessions/{sid}/clips {event_id, t_start, t_end, mime, audio_b64}
    stored as data/clips/<sid>_<event_id>.<ext> only if: consent given, not off the record, ≤30 s, ≤1.5 MB, and the
    utterance's transcript has no PII (if the transcript is not logged yet the clip is kept as `pending` and
    re-checked by `clip_for` at map build).
GET  /api/clips/{name}                              → the audio (path confined to data/clips)
clip_for(session_id, utterance_id) → "/api/clips/<file>" | None    (map builder: Quote.audio_clip)
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from claros import REPO_ROOT

from . import _deps as d

router = APIRouter()
CLIP_DIR = REPO_ROOT / "data" / "clips"
MAX_BYTES = 1_500_000
MAX_MS = 30_000
EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _session(sid: str) -> Any:
    try:
        from claros.session import sessions
        return sessions.get(sid)
    except Exception:  # noqa: BLE001
        return d.get_session(sid)


def consented(sid: str) -> bool:
    s = _session(sid)
    return bool(s is not None and (getattr(s, "extra", None) or {}).get("consent_voice_clips"))


async def on_hello(session_id: str, payload: Any) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("consent"), dict):
        return
    s = _session(session_id)
    if s is None or not hasattr(s, "extra"):
        return
    s.extra["consent_voice_clips"] = bool(payload["consent"].get("voice_clips"))
    try:
        from claros.session import sessions
        sessions.save(s)
    except Exception:  # noqa: BLE001
        pass


def transcript(sid: str, event_id: str) -> Optional[str]:
    """Final transcript logged for this utterance (None = not logged yet)."""
    for e in d.st_call("iter_log", sid, ["ws.in.utterance"], default=[]) or []:
        p = e.get("payload") or {}
        if p.get("event_id") == event_id or p.get("id") == event_id:
            return p.get("text") or ""
    return None


def has_pii(text: str) -> bool:
    try:
        from claros.perception import pii
        return bool(pii.detect([text])[0])
    except Exception:  # noqa: BLE001
        return True  # cannot check → do not keep the voice


def _ok_transcript(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    if not text.strip() or text.strip() == "[off the record]":
        return False
    return not has_pii(text)


@router.post("/api/sessions/{sid}/clips")
async def upload_clip(sid: str, body: dict = Body(...)) -> dict:
    s = _session(sid)
    if s is None:
        raise HTTPException(404, "session not found")
    if not consented(sid):
        return {"stored": False, "reason": "no consent"}
    if getattr(s, "off_record", False):
        return {"stored": False, "reason": "off the record"}
    eid = _SAFE.sub("", str(body.get("event_id") or ""))[:64]
    if not eid:
        raise HTTPException(400, "event_id required")
    mime = str(body.get("mime") or "audio/webm").split(";")[0].strip().lower()
    if mime not in EXT:
        raise HTTPException(415, f"unsupported mime {mime}")
    try:
        dur = float(body.get("t_end") or 0) - float(body.get("t_start") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur > MAX_MS:
        return {"stored": False, "reason": "longer than 30 s"}
    try:
        raw = base64.b64decode(str(body.get("audio_b64") or ""), validate=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "bad audio_b64") from e
    if not raw:
        raise HTTPException(400, "empty audio")
    if len(raw) > MAX_BYTES:
        return {"stored": False, "reason": "larger than 1.5 MB"}
    ok = _ok_transcript(transcript(sid, eid))
    if ok is False:
        return {"stored": False, "reason": "transcript contains personal data or is off the record"}
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{_SAFE.sub('', sid)}_{eid}.{EXT[mime]}"
    (CLIP_DIR / name).write_bytes(raw)
    rec = {"session_id": sid, "event_id": eid, "file": name, "mime": mime, "bytes": len(raw), "duration_ms": dur,
           "pending": ok is None}
    d.st_call("kv_put", "clips", f"{sid}:{eid}", rec)
    return {"stored": True, "url": f"/api/clips/{name}", "pending_pii_check": ok is None}


def clip_for(session_id: str, utterance_id: str) -> Optional[str]:
    """URL of the consented clip for this utterance (quote id 'q_<event_id>' also accepted), or None."""
    uid = utterance_id[2:] if utterance_id.startswith("q_") else utterance_id
    rec = d.st_call("kv_get", "clips", f"{session_id}:{_SAFE.sub('', uid)}")
    if not isinstance(rec, dict) or not (CLIP_DIR / rec.get("file", "")).is_file():
        return None
    if rec.get("pending"):
        ok = _ok_transcript(transcript(session_id, rec["event_id"]))
        if not ok:
            if ok is False:  # PII / off record after all: delete the voice
                try:
                    (CLIP_DIR / rec["file"]).unlink()
                except OSError:
                    pass
                d.st_call("kv_delete", "clips", f"{session_id}:{rec['event_id']}")
            return None
        rec["pending"] = False
        d.st_call("kv_put", "clips", f"{session_id}:{rec['event_id']}", rec)
    return f"/api/clips/{rec['file']}"


@router.get("/api/clips/{name}")
async def get_clip(name: str) -> FileResponse:
    path = (CLIP_DIR / _SAFE.sub("", name)).resolve()
    if not str(path).startswith(str(CLIP_DIR.resolve())) or not path.is_file():
        raise HTTPException(404, "clip not found")
    mime = next((m for m, e in EXT.items() if path.suffix == "." + e), "application/octet-stream")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


def as_path(url: Optional[str]) -> Optional[Path]:
    return CLIP_DIR / url.rsplit("/", 1)[-1] if url else None

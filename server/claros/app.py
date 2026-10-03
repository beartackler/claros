"""Claros FastAPI app (port 8787).  Run: `make server` or
`cd server && uv run uvicorn claros.app:app --reload --port 8787`.

Feature packages claros.{perception,brain,knowledge,context} are imported dynamically; each may expose
`router` and `register(bus)`. Their routers are mounted BEFORE core routes, so a package may override a
core fallback route (e.g. GET /api/workflows/{id}). Import/register failures are logged, never fatal.
"""
from __future__ import annotations

import importlib
import logging
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import REPO_ROOT, el, llm, ws
from .bus import bus
from .models import CaptureRequest, Mode, Moment, User
from .session import sessions
from .store import store

logging.basicConfig(level=os.getenv("CLAROS_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("claros.app")

PACKAGES = ["perception", "brain", "knowledge", "context"]
PACKAGE_STATUS: dict[str, str] = {}
KEYFRAME_DIR = REPO_ROOT / "data" / "keyframes"
ENV_KEYS = ["ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ISOQUANT_API_KEY", "JINA_API_KEY", "FASTINO_API_KEY",
            "BRIGHTDATA_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "CLAROS_PUBLIC_URL", "CLAROS_DB",
            "OLLAMA_URL", "ERPNEXT_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    ws.install(bus)
    yield
    if llm.HTTP is not None:
        await llm.HTTP.aclose()
        llm.HTTP = None


app = FastAPI(title="Claros", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"] +
                  [o for o in os.getenv("CLAROS_CORS_ORIGINS", "").split(",") if o],
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


def _load_packages() -> None:
    for name in PACKAGES:
        try:
            mod = importlib.import_module(f"claros.{name}")
            parts = []
            if callable(getattr(mod, "register", None)):
                mod.register(bus)
                parts.append("register")
            r = getattr(mod, "router", None)
            if r is not None:
                app.include_router(r)
                parts.append("router")
            PACKAGE_STATUS[name] = "loaded(" + ",".join(parts) + ")"
            log.info("package claros.%s loaded: %s", name, parts)
        except ModuleNotFoundError as e:
            if e.name == f"claros.{name}":
                PACKAGE_STATUS[name] = "missing"
                log.warning("package claros.%s not present; skipping", name)
            else:
                PACKAGE_STATUS[name] = f"error: {e}"
                log.error("package claros.%s failed: %s", name, traceback.format_exc())
        except Exception as e:  # noqa: BLE001
            PACKAGE_STATUS[name] = f"error: {type(e).__name__}: {e}"
            log.error("package claros.%s failed: %s", name, traceback.format_exc())


_load_packages()
app.include_router(ws.router)
app.include_router(el.router)


# ---------------- core REST ----------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "env": {k: bool(os.getenv(k)) for k in ENV_KEYS}, "packages": PACKAGE_STATUS,
            "llm_keys": llm.keys_present(), "sqlite_vec": bool(store.has_vec),
            "llm_recent": list(llm.STATS)[-10:]}


class SessionCreate(BaseModel):
    mode: Mode = "capture"
    user: Optional[User] = None
    lang: str = "en"
    workflow_id: Optional[str] = None


@app.post("/api/sessions")
async def create_session(body: SessionCreate) -> dict:
    s = sessions.create(mode=body.mode, user=body.user, lang=body.lang, workflow_id=body.workflow_id)
    store.log(s.id, "session.created", s.model_dump(mode="json"), ws.now_ms())
    bus.publish(s.id, "session.created", s.model_dump(mode="json"))
    return {"session_id": s.id}


@app.get("/api/sessions/{sid}")
async def get_session(sid: str) -> dict:
    s = sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    return s.model_dump(mode="json")


@app.post("/api/sessions/{sid}/end")
async def end_session(sid: str) -> dict:
    s = sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    s.ended = True
    sessions.save(s)
    payload = {"session_id": sid, "mode": s.mode}
    store.log(sid, "session.ended", payload, ws.now_ms())
    bus.publish(sid, "session.ended", payload)
    return {"ok": True, **payload}


@app.get("/api/sessions/{sid}/log")
async def session_log(sid: str, kinds: Optional[str] = None, after_id: int = 0) -> list[dict]:
    return list(store.iter_log(sid, kinds.split(",") if kinds else None, after_id))


@app.get("/api/workflows")
async def list_workflows() -> list[dict]:
    return [{"workflow_id": w["workflow_id"], "id": w.get("id"), "name": w.get("name"), "version": w.get("version"),
             "updated_at": w.get("updated_at"),
             "apps": w.get("apps", []), "coverage": w.get("coverage"), "experts": w.get("experts", []),
             "onet": w.get("onet")} for w in store.list_workflows()]


@app.get("/api/workflows/{wid}")
async def get_workflow(wid: str, version: Optional[int] = None) -> dict:
    w = store.get_workflow(wid, version)
    if not w:
        raise HTTPException(404, "workflow not found")
    return w


@app.get("/api/workflows/{wid}/coverage")
async def workflow_coverage(wid: str) -> dict:
    w = store.get_workflow(wid)
    if not w:
        return {"status": "missing"}
    return w.get("coverage") or {"status": "missing"}


FIXTURE_FRAMES = REPO_ROOT / "data" / "fixtures" / "frames"


def _fixture_frame(kid: str) -> Optional[Path]:
    """Unregistered seeded ids like kf_a_003 -> data/fixtures/frames/<lang>/<nth frame> (demo fallback)."""
    import re
    m = re.fullmatch(r"kf_[a-z0-9]+_(\d+)", kid)
    d = FIXTURE_FRAMES / os.getenv("CLAROS_FIXTURE_LANG", "en")
    if not m or not d.is_dir():
        return None
    frames = sorted(d.glob("*.jpg"))
    return frames[(int(m.group(1)) - 1) % len(frames)] if frames else None


@app.get("/api/keyframes/{kid}.jpg")
async def keyframe(kid: str) -> FileResponse:
    """Serves REDACTED keyframes only (perception writes them; raw frames never hit disk)."""
    kf = store.get_keyframe(kid)
    path = Path(kf["path"]) if kf else KEYFRAME_DIR / f"{kid}.jpg"
    if not kf and not path.is_file():
        path = _fixture_frame(kid) or path
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not path.is_file() or not str(path).startswith(str((REPO_ROOT / "data").resolve())):
        raise HTTPException(404, "keyframe not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# Fallback capture-request inbox (a package router registered earlier takes precedence).
class RequestCreate(BaseModel):
    workflow_hint: str
    requested_by: User
    moment: Optional[Moment] = None


@app.get("/api/requests")
async def list_requests() -> list[dict]:
    return store.kv_list("requests")


@app.post("/api/requests")
async def create_request(body: RequestCreate) -> dict:
    req = CaptureRequest(id=f"r_{uuid.uuid4().hex[:10]}", workflow_hint=body.workflow_hint,
                         requested_by=body.requested_by, moment=body.moment, created_at=time.time() * 1000.0)
    d = req.model_dump(mode="json")
    store.kv_put("requests", req.id, d)
    bus.publish(body.moment.session_id if body.moment else "_", "request.created", d)
    return d


@app.post("/api/requests/{rid}/accept")
async def accept_request(rid: str) -> dict:
    d: Any = store.kv_get("requests", rid)
    if not d:
        raise HTTPException(404, "request not found")
    d["status"] = "accepted"
    store.kv_put("requests", rid, d)
    s = sessions.create(mode="capture", user=User(id="expert", name="Expert", role="expert"))
    s.extra.update(request_id=rid, workflow_hint=d.get("workflow_hint"), moment=d.get("moment"),
                   requested_by=d.get("requested_by"))
    sessions.save(s)
    bus.publish(s.id, "request.accepted", d)
    return {"request": d, "session_id": s.id, "mode": "capture", "session": s.model_dump(mode="json")}

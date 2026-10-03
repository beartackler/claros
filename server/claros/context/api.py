"""Debug REST endpoints for claros.context."""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from . import onet, scope, web

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("/onet")
async def onet_match(q: str = Query(..., min_length=1), k: int = 5):
    try:
        return [m.model_dump() for m in await onet.amatch(q, k=k)]
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))


@router.get("/onet/{code}")
async def onet_profile(code: str):
    p = await asyncio.to_thread(onet.occupation_profile, code)
    if not p:
        raise HTTPException(404, "unknown occupation code")
    return p


@router.get("/search")
async def web_search(q: str = Query(..., min_length=1), k: int = 5):
    return await web.search(q, k=k)


@router.get("/read")
async def web_read(url: str):
    return {"url": url, "markdown": await web.read(url)}


@router.get("/app_docs")
async def app_docs(app: str, topic: str):
    return await web.app_docs(app, topic)


@router.get("/scope")
async def classify(q: str, app: Optional[str] = None, resolve: bool = False):
    ctx = {"app": app} if app else {}
    s, c = await scope.classify_scope(q, ctx)
    note = await scope.try_resolve(q, ctx) if resolve else None
    return {"scope": s, "confidence": c, "note": note.model_dump() if note else None}

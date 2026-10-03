"""ElevenLabs Agents auth helpers (server holds the API key).

GET /api/el/token?agent=claros       -> {"token", "agent_id"}       WebRTC conversation token
GET /api/el/signed-url?agent=claros  -> {"signed_url", "agent_id"}  WebSocket signed URL
Upstream: GET https://api.elevenlabs.io/v1/convai/conversation/token?agent_id=...      (xi-api-key)
          GET https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=...
`agent` = "claros" (-> ELEVENLABS_AGENT_ID), or ELEVENLABS_AGENT_ID_<NAME> env, or a raw agent id.
Without a key, returns 503 with {"degraded": true} so the web client can show text-only mode.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/el")
EL_BASE = "https://api.elevenlabs.io/v1/convai/conversation"


def resolve_agent(agent: str) -> Optional[str]:
    specific = os.getenv(f"ELEVENLABS_AGENT_ID_{agent.upper().replace('-', '_')}")
    if specific:
        return specific
    if agent in ("claros", "default", ""):
        return os.getenv("ELEVENLABS_AGENT_ID") or None
    return agent  # assume a raw agent id


async def _get(path: str, agent: str, extra: dict) -> dict:
    key = os.getenv("ELEVENLABS_API_KEY")
    agent_id = resolve_agent(agent)
    if not key or not agent_id:
        raise HTTPException(503, {"degraded": True, "error": "ELEVENLABS_API_KEY / agent id not configured"})
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{EL_BASE}/{path}", params={"agent_id": agent_id, **extra},
                            headers={"xi-api-key": key})
    except httpx.HTTPError as e:
        raise HTTPException(502, {"error": f"elevenlabs unreachable: {e}"}) from e
    if r.status_code >= 400:
        raise HTTPException(502, {"error": "elevenlabs error", "status": r.status_code, "body": r.text[:500]})
    return {**r.json(), "agent_id": agent_id}


@router.get("/token")
async def token(agent: str = Query("claros"), participant_name: Optional[str] = None) -> dict:
    return await _get("token", agent, {"participant_name": participant_name} if participant_name else {})


@router.get("/signed-url")
async def signed_url(agent: str = Query("claros")) -> dict:
    return await _get("get-signed-url", agent, {})

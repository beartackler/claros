"""LLM access for claros.context: claros.llm.chat if present, else a minimal local
OpenAI-compatible fallback (Isoquant -> OpenRouter). Never raises: returns None when no LLM."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

log = logging.getLogger("claros.context.llm")

try:  # owned by server-core
    from claros.llm import chat as _shared_chat  # type: ignore
except Exception:  # pragma: no cover - only when llm.py missing
    _shared_chat = None


def _local_providers() -> list[tuple[str, str, str]]:
    out = []
    if os.getenv("ISOQUANT_API_KEY"):
        out.append(("https://api.isoquant.ai/v1", os.environ["ISOQUANT_API_KEY"], os.getenv("CLAROS_CONTEXT_MODEL", "glm-5.3-flash")))
    if os.getenv("OPENROUTER_API_KEY"):
        out.append(("https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"], os.getenv("CLAROS_OPENROUTER_MODEL", "google/gemini-2.5-flash")))
    return out


async def _local_chat(messages: list[dict], timeout: float) -> Optional[str]:
    for base, key, model in _local_providers():
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                 json={"model": model, "messages": messages, "temperature": 0.1})
                r.raise_for_status()
                return r.json()["choices"][0]["message"].get("content") or ""
        except Exception as e:
            log.warning("context llm %s failed: %s", base, e)
    return None


async def llm_text(messages: list[dict], role: str = "fast", timeout: float = 20.0) -> Optional[str]:
    if _shared_chat is not None:
        try:
            out = await _shared_chat(messages, model_role=role, timeout=timeout)
            return out if isinstance(out, str) else None
        except Exception as e:
            log.info("claros.llm unavailable: %s", e)
            return None
    return await _local_chat(messages, timeout)


def parse_json(text: Optional[str]) -> Optional[dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except Exception:
        return None


async def llm_json(messages: list[dict], role: str = "fast", timeout: float = 20.0) -> Optional[dict[str, Any]]:
    return parse_json(await llm_text(messages, role=role, timeout=timeout))

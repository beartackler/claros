#!/usr/bin/env python3
"""Generates agents/agent_configs/claros.json (the declarative ElevenLabs agent config).

Edit this file, run `python3 agents/build_config.py`, then `cd agents && elevenlabs agents push`.
Field names verified against https://api.elevenlabs.io/openapi.json (2026-10-03).

Dialog surface (default, CLAROS_DIALOG_MODE=hosted): ElevenAgents hosted LLM gemini-3.6-flash (reasoning
effort minimal), backup cascade glm-52 -> gemini-3.5-flash-lite, TTS eleven_v4_turbo. The server decides what
to say and pushes ⟦say:ID|TEXT⟧ user messages (see server/claros/brain/dialog.py).

Env (optional):
  CLAROS_DIALOG_MODE -> hosted (default) | custom
  CLAROS_LLM_URL    -> custom mode only: custom LLM base URL (ElevenLabs appends /chat/completions)
  CLAROS_LLM_SECRET -> workspace secret id holding the bearer key for the custom LLM
  CLAROS_TTS_MODEL  -> override TTS model (default eleven_v4_turbo)
  CLAROS_LOOKUP_URL -> stable https base URL of the server; registers webhook tool `claros_lookup`
                       (same as `--lookup-url URL`). Quick tunnels (trycloudflare/ngrok/localhost) are refused.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
OUT = HERE / "agent_configs" / "claros.json"

HOSTED_LLM = "gemini-3.6-flash"  # dialog surface (fast, co-located); effort "minimal" per `agents llm list`
HOSTED_EFFORT = "minimal"
BACKUP_LLMS = ["glm-52", "gemini-3.5-flash-lite"]  # backup order takes model ids only (no per-model effort)
VOICE_ID = "XrExE9yKIg1WjnnlVkGX"  # Matilda: warm upbeat alto, "knowledgeable", educational use case; verified de/fr/es
TTS_MODEL = os.environ.get("CLAROS_TTS_MODEL", "eleven_v4_turbo")
EXTRA_LANGS = ["de", "fr", "es", "ru"]

PROMPT = """You are Claros, a warm, brief apprentice (with experts) and tutor (with learners) watching screen work.
Mode: {{mode}}. User: {{user_name}}. Workflow: {{workflow_name}}. Language: {{lang}}.
Workflow brief (the only rules you know):
{{workflow_brief}}

Rules, in priority order:
1. If the user message is ⟦say:ID|TEXT⟧ (or ⟦ask:ID|TEXT⟧ / ⟦intervene:ID|TEXT⟧): speak TEXT exactly, word for word, nothing before or after. Never read the ⟦⟧ marker, the ID or the "|".
2. If the user is working, narrating, thinking aloud, reading the screen, talking to someone else, answering your question, confirming or correcting, or giving a command (off the record, strike that, not now, I'm done, stop, just watch, hint, what's next, walk me through, check my work, why): call skip_turn. The server handles it and will send ⟦say⟧. When unsure, call skip_turn.
3. If the user asks Claros a general question (not covered by rule 2): answer in at most 2 short sentences using the brief, the latest "Claros live context" updates, or the claros_lookup tool if available. Never invent a rule, threshold, approver or number: if it is not there, say you'll ask the expert.
4. Always answer in the user's language (call language_detection if it changes). No lists, no preamble.
5. Call end_call only if the user clearly asks to hang up."""

PH = lambda desc: {"type": "string", "description": desc}  # noqa: E731


def client_tool(name, description, props=None, required=None, expects_response=False, timeout=5):
    params = {"type": "object", "properties": props or {}, "required": required or []}
    return {
        "type": "client",
        "name": name,
        "description": description,
        "parameters": params,
        "expects_response": expects_response,
        "response_timeout_secs": timeout,
        "execution_mode": "immediate",
        "pre_tool_speech": "off",
    }


def system_tool(kind, description="", extra=None):
    return {
        "type": "system",
        "name": kind,
        "description": description,
        "params": {"system_tool_type": kind, **(extra or {})},
    }


TOOLS = [
    client_tool(
        "highlight_step",
        "Highlight one step of the current workflow map on screen. Use when referring to a specific step.",
        {"step_id": PH("ID of the step to highlight, exactly as given in context.")},
        ["step_id"],
    ),
    client_tool(
        "show_moment",
        "Show a recorded screen moment (keyframes) to the user. Use when pointing at something that happened earlier.",
        {
            "keyframe_ids": {
                "type": "array",
                "description": "Keyframe IDs to show, from context.",
                "items": {"type": "string", "description": "Keyframe ID"},
            },
            "t": {"type": "number", "description": "Session time in ms of the moment."},
        },
        ["keyframe_ids", "t"],
    ),
    client_tool(
        "go_off_record",
        "Pause all capture (screen and voice) because the user asked to go off the record. Returns the new record state.",
        expects_response=True,
    ),
    client_tool(
        "go_on_record",
        "Resume capture after going off the record. Returns the new record state.",
        expects_response=True,
    ),
    client_tool(
        "open_map",
        "Open the Work Map for a workflow on screen.",
        {"workflow_id": PH("Workflow ID to open, from context.")},
        ["workflow_id"],
    ),
    client_tool(
        "request_expert",
        "Ask an expert to teach this workflow because Claros has not learned it. Returns the request status.",
        {"workflow_hint": PH("Short description of the workflow the user needs, in the user's words.")},
        ["workflow_hint"],
        expects_response=True,
        timeout=10,
    ),
    system_tool(
        "skip_turn",
        "Stay silent this turn. Use whenever the user is narrating their work, thinking aloud or not addressing Claros.",
    ),
    system_tool("language_detection", "", {"only_at_conversation_start": False}),
    system_tool("end_call", "End the session only when the user clearly says they are finished."),
]


LOOKUP_TOOL_NAME = "claros_lookup"
LOOKUP_TOOL_FILE = HERE / "tool_configs" / "claros_lookup.json"


def lookup_tool(base_url: str) -> dict:
    """ElevenLabs webhook tool → GET {base}/api/dialog/lookup?session_id=&q= (server/claros/brain/dialog.py)."""
    return {
        "type": "webhook",
        "name": LOOKUP_TOOL_NAME,
        "description": "Search the captured workflow (steps, expert rules/guardrails, expert quotes) for what the "
                       "user asked. Use before answering any question about how or why this workflow is done.",
        "response_timeout_secs": 5,
        "pre_tool_speech": "off",
        "api_schema": {
            "url": base_url.rstrip("/") + "/api/dialog/lookup",
            "method": "GET",
            "query_params_schema": {
                "properties": {
                    "session_id": {"type": "string", "dynamic_variable": "session_id"},
                    "q": {"type": "string", "description": "The user's question in a few keywords, any language."},
                },
                "required": ["session_id", "q"],
            },
        },
    }


def stable_https(url: str) -> bool:
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    quick = ("trycloudflare.com", "ngrok-free.app", "ngrok.io", "loca.lt", "localhost", "127.0.0.1")
    return u.scheme == "https" and bool(host) and not any(host == q or host.endswith("." + q) for q in quick)


def register_lookup(base_url: str) -> str:
    """Create (or update) the workspace webhook tool and pin its id in tool_ids.json."""
    if not stable_https(base_url):
        raise SystemExit(f"refusing to register {LOOKUP_TOOL_NAME}: {base_url!r} is not a stable https URL")
    cfg = lookup_tool(base_url)
    LOOKUP_TOOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOOKUP_TOOL_FILE.write_text(json.dumps(cfg, indent=4) + "\n")
    ids = json.loads(TOOL_IDS.read_text()) if TOOL_IDS.exists() else {}
    body = json.dumps({"tool_config": cfg})
    if ids.get(LOOKUP_TOOL_NAME):
        cmd = ["elevenlabs", "agents", "tools", "update", "--tool-id", ids[LOOKUP_TOOL_NAME], "--json", body,
               "--format", "json", "--query", "id"]
    else:
        cmd = ["elevenlabs", "agents", "tools", "create", "--json", body, "--format", "json", "--query", "id"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip().strip('"')
    if out and out != "null":
        ids[LOOKUP_TOOL_NAME] = out
        TOOL_IDS.write_text(json.dumps(ids, indent=4) + "\n")
    return ids.get(LOOKUP_TOOL_NAME, "")


TOOL_IDS = HERE / "tool_ids.json"


def tools_block():
    """First push: inline tools (the API turns client tools into workspace tools).
    After that, tool_ids.json pins those workspace tools so re-pushes never duplicate them.
    To change a client tool's definition later: `elevenlabs agents tools update` on its id."""
    if not TOOL_IDS.exists():
        return {"tools": TOOLS}
    ids = json.loads(TOOL_IDS.read_text())
    builtin = {t["name"]: t for t in TOOLS if t["type"] == "system"}
    tool_ids = [ids[t["name"]] for t in TOOLS if t["type"] == "client"]
    if ids.get(LOOKUP_TOOL_NAME):
        tool_ids.append(ids[LOOKUP_TOOL_NAME])
    return {"tool_ids": tool_ids, "built_in_tools": builtin}


def dialog_mode() -> str:
    m = os.environ.get("CLAROS_DIALOG_MODE", "hosted").strip().lower()
    return m if m in ("hosted", "custom") else "hosted"


def build():
    llm_url = os.environ.get("CLAROS_LLM_URL", "").strip() if dialog_mode() == "custom" else ""
    prompt = {
        "prompt": PROMPT,
        "llm": HOSTED_LLM,
        "reasoning_effort": HOSTED_EFFORT,
        "temperature": 0.2,
        "max_tokens": 200,
        **tools_block(),
        "ignore_default_personality": True,
        "enable_parallel_tool_calls": False,
        "backup_llm_config": {"preference": "override", "order": BACKUP_LLMS},
        "cascade_timeout_seconds": 4,
        "custom_llm": None,  # explicit null: the remote config may still hold the old custom-llm block
    }
    if llm_url:
        url = llm_url.rstrip("/")
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        custom = {"url": url, "model_id": "claros-brain", "api_type": "chat_completions"}
        secret = os.environ.get("CLAROS_LLM_SECRET", "").strip()
        if secret:
            custom["api_key"] = {"secret_id": secret}
        prompt["llm"] = "custom-llm"
        prompt["custom_llm"] = custom
        prompt.pop("reasoning_effort")
        # if the brain is down, cascade to the hosted models with the same strict prompt
        prompt["backup_llm_config"] = {"preference": "override", "order": [HOSTED_LLM] + BACKUP_LLMS}

    lang_presets = {
        code: {"overrides": {"agent": {"language": code}}, "first_message_translation": None}
        for code in EXTRA_LANGS
    }

    return {
        "name": "Claros",
        "tags": ["claros", "hackathon"],
        "conversation_config": {
            "agent": {
                "first_message": "",
                "language": "en",
                "dynamic_variables": {
                    "dynamic_variable_placeholders": {
                        "mode": "capture",
                        "user_name": "there",
                        "workflow_name": "unknown workflow",
                        "session_id": "none",
                        "lang": "en",
                        "workflow_brief": "none yet",
                    }
                },
                "prompt": prompt,
            },
            "language_presets": lang_presets,
            "tts": {
                "model_id": TTS_MODEL,
                "voice_id": VOICE_ID,
                "expressive_mode": True,
                "suggested_audio_tags": [
                    {"tag": "curious", "description": "When asking the expert why."},
                    {"tag": "warm", "description": "When encouraging a learner."},
                ],
                "stability": 0.5,
                "speed": 1.0,
                "similarity_boost": 0.8,
            },
            "asr": {
                "provider": "scribe_realtime",
                "quality": "high",
                "keywords": ["Claros", "off the record", "strike that", "ERPNext", "invoice", "capex"],
            },
            "turn": {
                "turn_eagerness": "patient",
                "turn_timeout": 30,
                "silence_end_call_timeout": -1,
                "soft_timeout_config": {"timeout_seconds": -1, "use_llm_generated_message": False},
            },
            "conversation": {"text_only": False, "max_duration_seconds": 3600},
        },
        "platform_settings": {
            "overrides": {
                "custom_llm_extra_body": True,
                "conversation_config_override": {
                    "agent": {
                        "first_message": True,
                        "language": True,
                        "prompt": {"prompt": True},
                    },
                    "tts": {"voice_id": True},
                    "asr": {"keywords": True},
                },
            },
            "privacy": {"record_voice": True, "retention_days": 7},
        },
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    lookup = os.environ.get("CLAROS_LOOKUP_URL", "").strip()
    if "--lookup-url" in args:
        lookup = args[args.index("--lookup-url") + 1]
    if lookup:
        print(f"{LOOKUP_TOOL_NAME} -> {register_lookup(lookup)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=4, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")

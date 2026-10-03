#!/usr/bin/env python3
"""Generates agents/agent_configs/claros.json (the declarative ElevenLabs agent config).

Edit this file, run `python3 agents/build_config.py`, then `cd agents && elevenlabs agents push`.
Field names verified against https://api.elevenlabs.io/openapi.json (2026-10-03).

Env (optional):
  CLAROS_LLM_URL    -> if set, use custom LLM at this base URL (ElevenLabs appends /chat/completions)
  CLAROS_LLM_SECRET -> workspace secret id holding the bearer key for the custom LLM
  CLAROS_TTS_MODEL  -> override TTS model (default eleven_v3_conversational)
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "agent_configs" / "claros.json"

HOSTED_LLM = "gemini-3.5-flash-lite"  # lowest-latency non-deprecated hosted model in `elevenlabs agents llm list`
VOICE_ID = "XrExE9yKIg1WjnnlVkGX"  # Matilda: warm upbeat alto, "knowledgeable", educational use case; verified de/fr/es
TTS_MODEL = os.environ.get("CLAROS_TTS_MODEL", "eleven_v3_conversational")
EXTRA_LANGS = ["de", "fr", "es", "ru"]

PROMPT = """You are Claros, a curious, patient apprentice (with experts) and tutor (with learners) who watches screen work.
Session mode: {{mode}}. User: {{user_name}}. Workflow: {{workflow_name}}.

Hard rules (never break them):
1. Markers. If the latest user message contains ⟦ask:ID⟧ or ⟦intervene:ID⟧, say EXACTLY the text supplied with that marker (text after a "|" inside the marker, or the most recent context update for that ID). Say nothing else. If no text was supplied, call skip_turn.
2. Silence while working. If the user is narrating their own work, thinking aloud, reading the screen, or talking to someone else, and is not addressing Claros, call skip_turn. When unsure, call skip_turn.
3. One question at a time, at most 15 words. No preamble, no lists, no summaries unless asked.
4. Never invent rules, thresholds, policies, approvers, or numbers. If you do not know, say so and offer to ask an expert (request_expert).
5. Always reply in the language the user is speaking. If it changes, call language_detection.
6. Learn mode: give hints and ask the learner to predict the next step before telling them. Explain only what the confirmed map says.
7. "Off the record" means call go_off_record and stay silent until "back on record" (go_on_record).
8. Call end_call only when the user clearly says they are done.
Tone: warm, curious, brief. You may use one gentle audio tag like [curious] or [warm], never more."""

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


TOOL_IDS = HERE / "tool_ids.json"


def tools_block():
    """First push: inline tools (the API turns client tools into workspace tools).
    After that, tool_ids.json pins those workspace tools so re-pushes never duplicate them.
    To change a client tool's definition later: `elevenlabs agents tools update` on its id."""
    if not TOOL_IDS.exists():
        return {"tools": TOOLS}
    ids = json.loads(TOOL_IDS.read_text())
    builtin = {t["name"]: t for t in TOOLS if t["type"] == "system"}
    return {"tool_ids": [ids[t["name"]] for t in TOOLS if t["type"] == "client"], "built_in_tools": builtin}


def build():
    llm_url = os.environ.get("CLAROS_LLM_URL", "").strip()
    prompt = {
        "prompt": PROMPT,
        "llm": HOSTED_LLM,
        "temperature": 0.3,
        "max_tokens": 200,
        **tools_block(),
        "ignore_default_personality": True,
        "enable_parallel_tool_calls": False,
        "backup_llm_config": {"preference": "default"},
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
        # if the brain is down, cascade to the hosted model with the strict prompt above
        prompt["backup_llm_config"] = {"preference": "override", "order": [HOSTED_LLM, "gemini-3.1-flash-lite"]}
        prompt["cascade_timeout_seconds"] = 4

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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=4, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")

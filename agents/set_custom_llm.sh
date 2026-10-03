#!/usr/bin/env bash
# Switch the Claros ElevenLabs agent to our custom-LLM brain (or back to the hosted backup).
#
#   agents/set_custom_llm.sh https://xyz.trycloudflare.com     # uses ${URL}/llm/v1
#   agents/set_custom_llm.sh                                   # uses $CLAROS_PUBLIC_URL from ../.env
#   agents/set_custom_llm.sh --hosted                          # revert to hosted gemini-3.5-flash-lite
#   CLAROS_LLM_KEY=sometoken agents/set_custom_llm.sh <url>    # also send "Authorization: Bearer sometoken"
#
# ElevenLabs appends "/chat/completions" to the configured URL, so the agent is pointed at
# ${CLAROS_PUBLIC_URL}/llm/v1  ->  requests hit POST /llm/v1/chat/completions (see docs/CONTRACTS.md).
# The brain does not need an API key; set CLAROS_LLM_KEY only if the brain checks one.
# If the brain is unreachable for 4 s, ElevenLabs cascades to the hosted model with the strict backup prompt.
set -euo pipefail
cd "$(dirname "$0")"

ROOT_ENV="../.env"
if [[ -f "$ROOT_ENV" ]]; then set -a; source "$ROOT_ENV"; set +a; fi

AGENT_ID="$(cat AGENT_ID)"

if [[ "${1:-}" == "--hosted" ]]; then
  python3 build_config.py
  elevenlabs agents push --version-description "hosted backup LLM"
  echo "Agent $AGENT_ID now uses the hosted LLM."
  exit 0
fi

BASE="${1:-${CLAROS_PUBLIC_URL:-}}"
if [[ -z "$BASE" ]]; then
  echo "usage: $0 <public-base-url> | --hosted   (or set CLAROS_PUBLIC_URL in .env)" >&2
  exit 1
fi
BASE="${BASE%/}"
BASE="${BASE%/chat/completions}"
case "$BASE" in
  */llm/v1) LLM_URL="$BASE" ;;
  *)        LLM_URL="$BASE/llm/v1" ;;
esac

SECRET_ID=""
if [[ -n "${CLAROS_LLM_KEY:-}" ]]; then
  SECRET_ID="$(elevenlabs agents secrets list --format json \
      --query "secrets[?name=='claros_llm_key'].secret_id | [0]" 2>/dev/null | tr -d '"' || true)"
  if [[ -z "$SECRET_ID" || "$SECRET_ID" == "null" ]]; then
    SECRET_ID="$(elevenlabs agents secrets create --format json --query secret_id \
        --json "{\"type\":\"new\",\"name\":\"claros_llm_key\",\"value\":\"${CLAROS_LLM_KEY}\"}" | tr -d '"')"
    echo "Created workspace secret claros_llm_key -> $SECRET_ID"
  else
    elevenlabs agents secrets update --secret-id "$SECRET_ID" \
        --json "{\"type\":\"update\",\"name\":\"claros_llm_key\",\"value\":\"${CLAROS_LLM_KEY}\"}" >/dev/null \
      && echo "Updated workspace secret claros_llm_key ($SECRET_ID)" \
      || echo "warn: could not update secret value; reusing $SECRET_ID" >&2
  fi
fi

CLAROS_LLM_URL="$LLM_URL" CLAROS_LLM_SECRET="$SECRET_ID" python3 build_config.py
elevenlabs agents push --version-description "custom LLM -> $LLM_URL"

echo
echo "Agent $AGENT_ID now uses custom LLM: $LLM_URL/chat/completions"
elevenlabs agents get --agent-id "$AGENT_ID" --format json \
  --query '{llm: conversation_config.agent.prompt.llm, custom_llm: conversation_config.agent.prompt.custom_llm, backup: conversation_config.agent.prompt.backup_llm_config}'

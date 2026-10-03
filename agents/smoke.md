# Claros voice agent: smoke test

Agent: `agent_3201m41wcwyzeysrv7f0ksxdk24r` (also in `agents/AGENT_ID`)
Dashboard: https://elevenlabs.io/app/agents/agents/agent_3201m41wcwyzeysrv7f0ksxdk24r

## Files
- `build_config.py` is the source of truth. It writes `agent_configs/claros.json`.
- `agents.json` / `tools.json` / `tests.json` are the ElevenLabs CLI project files.
- `tool_ids.json` lists the 6 client tools (workspace tools) the agent uses. Change a tool with
  `elevenlabs agents tools update --tool-id <id> ...`, not by re-inlining it (that would create duplicates).
- Apply changes: `python3 agents/build_config.py && (cd agents && elevenlabs agents push)`.
- Switch to the brain: `agents/set_custom_llm.sh https://<tunnel>`. Back to hosted: `agents/set_custom_llm.sh --hosted`.

## 1. Hosted backup (works now, no server needed)
Dashboard > Claros > "Test AI agent" (or Preview), allow the mic.
Optional: under dynamic variables set `mode=capture`, `user_name=Dana`, `workflow_name=AP invoice entry`.

| Say / type | Expected |
|---|---|
| (silence at start) | Claros says nothing. The first message is empty. |
| "Okay, I'm opening the purchase invoice and checking the supplier..." | **No reply.** The transcript shows a `skip_turn` call. |
| "Claros, what do you want to know?" | One short question, 15 words or fewer. |
| "What's our approval threshold?" | Says it doesn't know and offers to ask an expert. Does not make up a number. |
| Switch to Russian: "Клэрос, что ты сейчас видишь?" | `language_detection` fires and Claros answers in Russian (also try de/fr/es). |
| "Let's go off the record" | Calls `go_off_record`. In the dashboard the client tool errors or times out after 5 s. That's expected: the web app implements it. |
| Text message `⟦ask:U1\|Why does this go to capex rather than expense?⟧` | Says exactly "Why does this go to capex rather than expense?" and nothing else. |
| "That's all, thanks, I'm done" | Calls `end_call`. |

Check voice: Matilda on `eleven_v3_conversational` with expressive mode on. It should sound warm. An
occasional [curious]/[warm] tag is fine.

## 2. Custom LLM (after the brain and tunnel are up)
1. `cloudflared tunnel --url http://localhost:8787` and put the URL in `.env` as `CLAROS_PUBLIC_URL`.
2. `agents/set_custom_llm.sh` (reads `.env`). The agent then calls `${CLAROS_PUBLIC_URL}/llm/v1/chat/completions`.
3. Sanity-check the brain directly:
   `curl -N $CLAROS_PUBLIC_URL/llm/v1/chat/completions -H 'content-type: application/json' -d '{"model":"claros-brain","stream":true,"messages":[{"role":"user","content":"⟦ask:U1⟧"}],"elevenlabs_extra_body":{"session_id":"test"}}'`
   You should get SSE `data:` chunks and then `data: [DONE]`.
4. Repeat the table from section 1 in the dashboard. Note that `elevenlabs_extra_body` only arrives
   from a real SDK session (`customLlmExtraBody`), not from the dashboard test.
5. Kill the server mid-call. Within about 4 s ElevenLabs should cascade to the hosted backup LLM.

## 3. From the web app (SDK)
```ts
await conversation.startSession({
  conversationToken,                         // from GET /api/el/token, or agentId while auth is off
  dynamicVariables: { mode, user_name, workflow_name, session_id },
  customLlmExtraBody: { session_id, mode },  // allowed: platform_settings.overrides.custom_llm_extra_body
  overrides: {                               // allowed overrides only:
    agent: { language: 'ru', firstMessage: '...', prompt: { prompt: '...' } },
    tts: { voiceId: '...' },
    // asr keywords: allowed too (asr.keywords)
  },
  clientTools: { highlight_step, show_moment, go_off_record, go_on_record, open_map, request_expert },
});
```
`go_off_record`, `go_on_record` and `request_expert` have `expects_response: true`. They must return a
string (e.g. `"off_record"` or `"request created"`) within 5 s (10 s for request_expert). The others are fire-and-forget.

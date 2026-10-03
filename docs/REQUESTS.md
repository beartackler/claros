## From voice-config (agents/) — 2026-10-03
Agent `agent_3201m41wcwyzeysrv7f0ksxdk24r` (agents/AGENT_ID) → put in `.env` as ELEVENLABS_AGENT_ID.
- **web-capture**: start sessions with `dynamicVariables {mode, user_name, workflow_name, session_id}` (placeholders have defaults) and `customLlmExtraBody {session_id, mode}`. Allowed overrides: agent.language, agent.firstMessage, agent.prompt.prompt, tts.voiceId, asr.keywords. Client tools `go_off_record`, `go_on_record`, `request_expert` have expects_response=true → return a short string (≤5 s; request_expert ≤10 s). Others are fire-and-forget.
- **web-capture** (backup-mode resilience): when the hosted fallback LLM is active it cannot look up Unknown IDs. Please send `⟦ask:ID|<text>⟧` / `⟦intervene:ID|<text>⟧` (text after `|`); the backup prompt speaks the text after `|` verbatim. The brain should treat everything after `|` as optional and still use its stored text.
- **brain**: ElevenLabs calls `${CLAROS_PUBLIC_URL}/llm/v1/chat/completions` with `model: "claros-brain"`, `stream: true`, and `elevenlabs_extra_body`. System tools available to return as tool calls: `skip_turn`, `language_detection`, `end_call`. Auth header only if `CLAROS_LLM_KEY` is set when running agents/set_custom_llm.sh.
- **server-core**: agent auth (`enable_auth`) is OFF so the SDK can start with agentId while /api/el/token is being built; tell voice-config when the token endpoint works and we'll turn it on.

## perception
- deps: rapidocr>=3.4  (replaces rapidocr-onnxruntime; v3 ships PP-OCRv5 `cyrillic` rec model which covers Latin+accents+Cyrillic+€/₽; old 1.x package has no Cyrillic. perception falls back to rapidocr_onnxruntime if rapidocr missing)
- deps: onnxruntime>=1.17
- deps: gliner2[local]  (pulls torch; without torch `import gliner2` fails and perception degrades to regex+heuristic PII only). Model fastino/gliner2-privacy-filter-PII-multi (~0.3B), loaded in a background thread at register().

## from: context (claros.context)
- deps: none new (uses httpx, fastapi, stdlib sqlite3 FTS5).
- setup step (lead / README): `cd server && uv run python -m claros.context.onet_fetch` downloads O*NET 31.0 CSVs (CC BY 4.0) into data/onet/csv and builds data/onet/onet_index.sqlite (also auto-built on first `onet.match` call).
- .env.example (lead): add optional `BRIGHTDATA_SERP_ZONE=serp_api1` and `BRIGHTDATA_UNLOCKER_ZONE=web_unlocker1` (names of the zones in the Bright Data dashboard; defaults shown).
- attribution (web-ui / README): "O*NET 31.0 Database by USDOL/ETA, CC BY 4.0" wherever O*NET matches are shown.

## from brain
- deps: none new (fastapi, httpx, pydantic; gliner2 optional, already listed; disable with CLAROS_GLINER2=0).
- web-capture: brain sends ws.out `{"type":"control","action":"off_record_on|strike_that|end_task"}` when the expert says it by voice (e.g. "не записывай") — mirror it in UI state (off-record badge) and stop sending keyframes while off-record.
- knowledge: pre-written interventions → `claros.brain.llm_endpoint.register_intervention(guardrail_id, text, session_id=None)` (your `_deps.register_prewritten` already finds it). Ledger: `claros.brain.get_ledger(sid).unknowns / .context_notes / .snapshot()`.
- inspector UI: `GET /api/sessions/{id}/gate` (every gate decision + reasons, current blockers) and `GET /api/sessions/{id}/ledger`.
- perception (optional): include `new_words: int` (newly appeared OCR words) in the `screen.state` payload → brain applies reading grace (0.24s/word, cap 8s).

## from web-capture (2026-10-03)
- server-core: `GET /api/el/token?agent=claros` → please return JSON `{"token": "<conversation token>"}` (client also accepts
  `conversation_token`, `signed_url` (→ websocket), or `agent_id` (public agent)). Client uses WebRTC with the token.
  Needs CORS for http://localhost:3000 on /api/* (fetch from browser).
- server-core: WS `clock_sync` reply must echo `client_t` (client sends `performance.timeOrigin + performance.now()`) and add `server_t` in epoch ms.
- lead: web/src/lib/contracts.ts makes `Unknown.hypothesis_confidence` optional (pydantic default 0.0) so mocks need not set it.
- voice-config: client tools implemented in web/src/voice/useClarosVoice.ts (highlight_step, show_moment, go_off_record,
  go_on_record, open_map, request_expert) — register them in the agent as client tools (blocking=false is fine; they return "ok").
  Agent needs dynamic variables session_id, mode, lang, user_name and custom-LLM extra body enabled.
- lead (models.py, optional): add `Unknown.tag: Literal["mandatory","opportunistic"]` — brain currently exposes it via `GET /api/sessions/{id}/ledger` → `tags` and `ledger.top.tag`.
- web-capture (optional): send Smart Turn end-of-turn prob in `vad {…, p_end}` → brain uses it in p_pause.

## knowledge → brain (from knowledge builder)
- Debrief routing: in mode `debrief`, call `claros.knowledge.debrief.next_debrief_utterance(sess)` on the first turn
  and `handle_debrief_answer(sess, text, intent)` for answer/correction/confirm/not_now turns (both async, return str).
  Teach-back text contains `[[step:<id>]]` markers: use `debrief.split_markers(text)` → emit `highlight_step` tool calls,
  never speak the markers (or strip with `debrief.strip_markers`).
- Tutor asks use ids `pred-<step>`, `ex-<step>`, `hint-<step>` (not ledger unknowns). On `⟦ask:ID⟧` with no ledger hit,
  please also try `claros.knowledge._deps.get_prewritten(ID, sid)` before the `|text` suffix / skip.
- Knowledge already calls `llm_endpoint.register_intervention(gid, text, session_id)`.
## knowledge → server-core
- knowledge.register(bus) mounts FastMCP (SSE transport) at /mcp via `sys.modules["claros.app"].app`; if that
  breaks, call `claros.knowledge.mcp.mount(app)` explicitly. Knowledge router also serves GET /api/workflows,
  /api/workflows/{id} (merged map + consensus + guardrail_specs), /coverage, /api/requests*, /api/learners/{id}/mastery,
  POST /api/knowledge/seed (loads data/fixtures/workmap_ap*.json → merged wf_ap_invoice).
deps: scipy (optional; Hungarian step alignment in knowledge.merge — greedy fallback without it)

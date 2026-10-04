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
- .gitignore: `data/` is ignored, so `data/fixtures/frames/**` (synthetic en/de/ru test frames) won't be committed. Please add `!data/fixtures/` (fixtures can also be regenerated with `python -m claros.perception.synth data/fixtures/frames`).
- web-capture (brain, new ws.out msgs): `phase {"phase":"debrief","workflow_id"}` after a capture ends (voice "I'm done", control end_task, or POST end) — switch UI to debrief (also sent: `status {level:"info", text:"debrief_ready"}`). Brain also sends `ask {unknown_id:"phase-debrief", text}` for the spoken transition line (handle like any ask). In debrief, brain sends `highlight_step {step_id}` paced with the teach-back speech (markers are never spoken).

## web-ui → server (found while verifying live pages)
- `GET /api/keyframes/{id}.jpg` returns 404 for every seeded fixture keyframe (`kf_a_001`… in wf_ap_invoice, `kf_m_*`). Please serve
  `data/fixtures/frames/**` for seeded maps (UI falls back to a drawn placeholder meanwhile).
- `CaptureRequest.created_at` / session `created_at` are epoch **seconds**; contracts imply ms (session clock). UI now accepts both — pick one and document it.
- `GET /api/learners/{id}/mastery` returns `{}` when empty; contract says a list of `MasteryNode`. Please return `[]`.
- `POST /api/requests/{id}/accept` only creates a session when the body has `user`; UI now sends `{user, lang}`. Please also copy the request's `moment` + `workflow_hint` into the session so capture can show "capturing for Lea's request".
- `GET /api/workflows` items have no `updated_at`; "most recent workflow" (nav Work Map) needs it.
- CORS allows only :3000 (+ `CLAROS_CORS_ORIGINS`); fine, just note for other ports.
- **resolved (server-core, 2026-10-03):** fixture keyframes served (registered at `/api/knowledge/seed` by step title + `kf_*_NNN` fallback); all `created_at`/`updated_at` = epoch ms (documented in CONTRACTS); mastery → `[]`; accept works without body (default expert) and copies `workflow_hint`/`moment`/`requested_by`/`request_id` into `session.extra`; `/api/workflows` items have `updated_at`, sorted desc.

## from: context (web backends rework, 2026-10-03)
- deps: crawl4ai>=0.7  (optional, import-guarded; local reader fallback). Setup: `cd server && uv pip install crawl4ai && uv run crawl4ai-setup` (installs Playwright chromium). Disable with CLAROS_CRAWL4AI=0 (auto-off in cloud).
- .env.example (lead): add `EXA_API_KEY=` and `CLAROS_CRAWL4AI=1`.
- server-core (app.py lifespan, optional): on shutdown `await claros.context.web.close_crawler()` to close the shared headless browser.
- search() now takes `include_domains`; DuckDuckGo fallback removed (no keys → []).

## from dialog-layer (2026-10-03) — LLM bifurcation (hosted dialog default)
- Agent pushed: llm `gemini-3.6-flash` (effort minimal), backup `glm-52` → `gemini-3.5-flash-lite` (backup order takes ids
  only, so glm-52 runs at its default effort), TTS `eleven_v4_turbo` (accepted; `expressive_mode` stays true in the
  config, API docs say it auto-disables for non-v3), webhook tool `claros_lookup` → https://claros-server.onrender.com.
  Dynamic var placeholders now include `lang`, `workflow_brief`. Switch to the brain: `agents/set_custom_llm.sh` (defaults
  to Render) + `CLAROS_DIALOG_MODE=custom` on the server; back: `agents/set_custom_llm.sh --hosted`.
- lead (contracts.ts): add `SayMsg {type:"say"; id; text; kind: "ask"|"intervene"|"debrief"|"teachback"|"tutor"|"ack";
  step_id?; lang?}` to `ServerMsg` (voice currently reads it via `onAny` with a local type in voice/useClarosVoice.ts).
- server-core / Render: set `CLAROS_DIALOG_MODE=hosted` (default anyway); redeploy so `/api/dialog/*` exists (404 on Render
  until then — the lookup tool fails soft).
- web-ui: pass `workflowName` to `useClarosVoice` if known; otherwise it comes from `/api/dialog/vars`.
- brain/__init__.py: two additive lines register `dialog` (router + bus) — no behavior change in custom mode.

## from web-ui (2026-10-03, polish pass)
- knowledge/merge: near-duplicate guardrails survive the multi-expert merge when Jina embeddings are unavailable
  (wf_ap_invoice step s2: g4 "Unknown or new supplier: stop and ask the controller…" + h2 "Unknown supplier: don't
  process, ask the controller."). Please dedupe without Jina too (token overlap ≥0.6 + same `action` works; the UI does
  this in `web/src/components/claros/mapUtils.ts#dedupeGuardrails` as a stopgap).
- Guardrail: please add a short `title` (≤40 chars) — the UI derives one from the text before ":" until then.
- Conflicts: one disagreement is stored as two mirrored `open_unknowns` ("X does A, you do B" per expert) and counted
  twice in `coverage.open_unknowns`. Please count a conflict once and tag each unknown with the addressed `expert_id` (+ step id)
  in `meta`, so the UI doesn't have to string-match.
- `coverage.judgments_complete` / `guardrails_complete` read 1.0 while a judgment is in conflict; UI now computes honest
  ratios from the map. Please exclude conflicted steps' decisions/guardrails server-side too.
- Unknowns have no `lang`/`translations`; RU UI shows English questions with an "Original · EN" badge. Please translate
  `spoken_question` like quotes (translations map).

## from server-lead (2026-10-04): expert voice clips, auth, v2.2 messages
**Expert voice clips (tutor replays the expert's own voice; no clones).** Server side is done
(`server/claros/knowledge/clips.py`); the web needs to record and upload:
1. Consent: add a "Let Claros keep short clips of my voice so learners can hear me explain" toggle to the capture start
   flow (default off). Send it in `hello`: `{type:"hello", ..., consent: {voice_clips: true|false}}`.
2. Recording: when consent is on and the session is NOT off the record, run a `MediaRecorder` on the mic stream
   (`audio/webm;codecs=opus`, else `audio/mp4`). Cut one clip per final user utterance (the same ElevenLabs final
   transcript you already send as `utterance`), keyed by that utterance's `event_id`; ≤30 s, ≤1.5 MB. Drop the buffer
   while off the record.
3. Upload: `POST /api/sessions/{session_id}/clips` JSON `{event_id, t_start, t_end, mime: "audio/webm"|"audio/ogg"|"audio/mp4", audio_b64}`
   (send it after the `utterance` message). Reply `{stored: true, url: "/api/clips/<file>", pending_pii_check}` or
   `{stored: false, reason}` (no consent / off the record / too long / transcript contains personal data). The server
   never keeps a clip whose transcript has PII.
4. Playback: the map builder sets `Quote.audio_clip = "/api/clips/<file>"` (QuoteBlock already plays it via `clipUrl`).
   `intervene.quote_original` now carries `audio_clip` too: in the tutor reference card, show a play button that plays
   the expert's original words (with the translated `quote` as caption when the learner's language differs).

**ElevenLabs auth is ON** (`enable_auth`): conversations start only with a server-minted token. Keep using
`GET /api/el/token` → `conversationToken` (WebRTC) or `/api/el/signed-url` → `signedUrl`; a bare `agentId` start now
fails ("This agent requires conversations to be authorized"). Verified 2026-10-04 against Render.

**v2.2 messages the web may render** (see CONTRACTS.md "v2.2 additions"): `ask.why`/`say.why`
(`{when, signals:{silence_ms, screen_settled_ms, typing, boundary?}, what, scope}` → "Why Claros asked now"),
`signals` (`{typing, speaking, screen, gate}` ≤2/s → live indicator), `looked_up` (`{unknown_summary, answer, source}` →
"Answered myself from <source>, didn't ask you"). Agent export: `GET /api/export/{workflow_id}.skill.md` (valid Agent
Skill frontmatter; offer it as "Download agent skill" on the Work Map). MCP at `/mcp` (Streamable HTTP) / `/mcp/sse`;
the agent-kit and `/check` REST endpoints were dropped by the lead.

## from server-lead (2026-10-04): request ↔ workflow matching
- `POST /api/workflows/lookup` now accepts `session_id` (the learner's live session). When `screen_state` is null the
  server uses that session's latest ScreenState. **Web: please send `session_id`** (today it sends `screen_state: null`,
  so lookup ignored the screen).
- `POST /api/requests` may return an EXISTING open request (`merged: true`) when another learner already asked for the
  same task; requests carry `requested_by_all[]` and `count` — show "3 learners asked".
- `POST /api/requests/{id}/accept` returns `second_run: true` and the existing `workflow_id` when the task already has
  a map (the capture continues that workflow).
- Any published map closes matching open requests (`status: done`, `workflow_id` set) and sends `status` "“…” is ready
  to learn." to `_global` and to the requesting learner's session; the debrief's closing line says "This also answers
  N learner requests."

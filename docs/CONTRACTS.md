# Claros — integration contracts (source of truth)

Python models: `server/claros/models.py` (authoritative). TS mirror: `web/src/lib/contracts.ts`
(keep in sync by hand; field names identical, camelCase NOT used — snake_case on the wire).

## Repo layout & ownership
```
server/                 FastAPI (Python 3.11, uv)       port 8787
  claros/models.py      shared pydantic models (owner: lead)
  claros/app.py         FastAPI app, routers mount       (owner: server-core)
  claros/store.py       SQLite session log + FTS5 + sqlite-vec (owner: server-core)
  claros/perception/    frames → redaction → OCR → vision → ScreenState → events (owner: perception)
  claros/brain/         ledger, pause gate, custom-LLM endpoint, intents, systemone adapter (owner: brain)
  claros/knowledge/     map builder, debrief, teach-back, exam, merge, tutor, guardrails, MCP (owner: knowledge)
  claros/context/       O*NET, web search/read tools (Bright Data / Jina), scope classifier (owner: context)
  claros/llm.py         OpenAI-compatible client w/ provider fallback router (owner: server-core)
web/                    Next.js (App Router, TS, Tailwind v4, neobrutalism.dev/shadcn) port 3000
  src/lib/contracts.ts  TS mirror of models
  src/capture/          capture worker, frame sampler, activity classifier (owner: web-capture)
  src/voice/            ElevenLabs session, PiP companion, client tools (owner: web-capture)
  src/app/...           surfaces: library, capture, debrief, map, learn, inbox (owner: web-ui)
infra/erpnext/          docker compose + seed scripts (owner: erpnext)
agents/                 ElevenLabs agent configs (CLI) (owner: voice-config)
docs/research/          research notes
```
Rule: only edit files in your owned paths; if you need a change elsewhere, write it in
`docs/REQUESTS.md` (append) instead. Do not `git commit` — the lead commits.

## Env (`.env` at repo root, see `.env.example`)
ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ISOQUANT_API_KEY, JINA_API_KEY, FASTINO_API_KEY,
BRIGHTDATA_API_KEY, OPENROUTER_API_KEY (fallback), GEMINI_API_KEY (fallback),
CLAROS_PUBLIC_URL (tunnel URL for ElevenLabs → brain), CLAROS_DB=./data/claros.db,
OLLAMA_URL=http://localhost:11434, ERPNEXT_URL=http://localhost:8080, ERPNEXT_API_KEY/SECRET.
EXA_API_KEY (context web search/read, primary), CLAROS_CRAWL4AI=1|0 (in-process Crawl4AI reader; default on
locally, off when a cloud env like RENDER/K_SERVICE/FLY_APP_NAME is set), optional BRIGHTDATA_SERP_ZONE=serp_api1 /
BRIGHTDATA_UNLOCKER_ZONE=web_unlocker1 (used only if the zone exists in the account).
Every component must run in a degraded mode when its key is missing (log + fallback), never crash.

## Session modes
`capture` (expert works, Claros watches/asks) · `debrief` (expert) · `learn` (learner) ·
`request` (learner asked for an uncovered workflow).

## Browser ↔ server WebSocket  `ws://localhost:8787/ws/session/{session_id}`
All messages JSON `{type, ...}`; client stamps `t` = ms on the session clock
(`performance.timeOrigin + performance.now()` minus offset from `clock_sync`).

Client → server:
- `hello {session_id, mode, user:{id,name,role}, lang, workflow_id?}`
- `clock_sync {client_t}` → server replies `clock_sync {client_t, server_t}`
- `activity {t, kind: typing|scrolling|navigating|idle|away, tiles_changed, dims:[w,h]}` (≤2/s)
- `keyframe {t, seq, reason: settle|boundary|heartbeat|toast, jpeg_b64, dims:[w,h], changed_tiles:[[x,y,w,h]]}`
- `vad {t, speaking: bool, score}` (on change)
- `utterance {t_start, t_end, role: user|agent, text, lang?, event_id}` (final transcripts from ElevenLabs onMessage)
- `agent_state {t, mode: speaking|listening}`
- `control {t, action: off_record_on|off_record_off|strike_that|not_now|end_task}`

Server → client:
- `events {items: ScreenEvent[]}`  (for the notebook)
- `ledger {open: int, saved_for_later: int, top?: Unknown}`
- `say {id, text, kind: ask|intervene|debrief|teachback|tutor|ack, step_id?, lang}` → client queues it and, only when
  the agent is listening and the user has been quiet ~450 ms, calls `sendUserMessage("⟦say:" + id + "|" + text + "⟧")`;
  the agent speaks `text` verbatim. A segment with `step_id` highlights that step when sent (client dedupes the agent's
  own `highlight_step` for the same step within 8 s). Teach-back = one `say` per segment, sent in order.
- `ask {unknown_id, text}` (legacy, still emitted by gate/tutor) → client maps to `say {id: unknown_id, kind: ask|tutor}`
- `intervene {guardrail_id, text, moment: Moment, rule?, quote?, quote_original?: {id, speaker, lang, text}, lang?, trigger?,
  escalated?}` (legacy) → client maps to `say {id: guardrail_id, kind: intervene}` and shows moment. All spoken text
  (`text`, `rule`, `quote`) is in the learner's lang (map text GLM-translated, cached per (map version, lang));
  `quote_original` keeps the expert's words. Deduped per (session, guardrail, entity) until the violating values
  change; a save/submit of the same violating state sends at most one `escalated: true` reminder.
- `context_update {text}` → client calls `sendContextualUpdate(text)`
- `show_moment {moment: Moment}` / `highlight_step {step_id}`
- `map_updated {workflow_id, version}`
- `status {level: info|warn|error, text}`

## Dialog mode  `CLAROS_DIALOG_MODE=hosted|custom` (default `hosted`)
- **hosted** (default): ElevenAgents hosted LLM speaks (`gemini-3.6-flash`, reasoning `minimal`; backup cascade
  `glm-52` → `gemini-3.5-flash-lite`; TTS `eleven_v4_turbo`). The server never is the LLM: `claros/brain/dialog.py`
  consumes final `utterance` events → intents (rules → systemone) → control actions (off record, strike that, not now,
  end task → phase debrief), capture acks, debrief turns (`knowledge.debrief`) and learner turns (`knowledge.tutor`),
  and pushes `say`. It waits for the floor (agent listening + user quiet) before speaking. On `hello mode=debrief` it
  pushes the first debrief question. It also pushes throttled `context_update` (≤400 chars: screen, current Work Map
  step, open questions, learner mastery hint).
- **custom**: ElevenLabs calls the brain endpoint below (`agents/set_custom_llm.sh [url]`, default Render URL); the
  dialog loop is idle. Thinking surface in both modes = GLM-5.3-Flash via `claros.llm` (server-side, async).
- Agent prompt rules (hosted): ⟦say:ID|TEXT⟧ → speak TEXT exactly; user working/answering/commanding → `skip_turn`;
  general question → ≤2 sentences from `{{workflow_brief}}` + context updates + `claros_lookup`, never invent rules;
  answer in the user's language; never read ⟦⟧.
- `GET /api/dialog/vars?session_id=` → `{session_id, mode, user_name, lang, workflow_name, workflow_brief, dialog_mode}`;
  client passes these as ElevenLabs dynamic variables. `workflow_brief` (≤1500 chars) is GLM-written from the map
  (steps + guardrails with expert attribution, unconfirmed/conflict flags), prefetched on hello, template fallback.
- `GET /api/dialog/lookup?session_id=&q=` → `{found, answer (≤300 chars, attributed), items:[{kind: step|guardrail|quote,
  id, text, expert, confirmed?}], workflow?}`. Registered as ElevenLabs webhook tool `claros_lookup`
  (`python3 agents/build_config.py --lookup-url https://claros-server.onrender.com`; quick tunnels are refused).

## Custom LLM endpoint (optional mode; ElevenLabs → brain)  `POST /llm/v1/chat/completions`
OpenAI chat-completions, `stream: true` → SSE `data: {chunk}\n\n` ... `data: [DONE]`.
Session binding: `elevenlabs_extra_body.session_id` (sent by client via `customLlmExtraBody`).
Routing of the latest user message:
- `⟦say:ID|T⟧` / `⟦ask:U⟧` → stream ledger `spoken_question` / pre-written text for the id, else `T` verbatim (no model call).
- `⟦intervene:G⟧` → stream pre-written intervention text verbatim.
- otherwise → intent classification (mode-specific) → handler. Handlers may stream text or
  return a tool call to `skip_turn` (silence). Default in `capture` for `narration` = `skip_turn`.
Response language = session lang (switch if `language_detection` fires).

## Agent client tools (registered in ElevenLabs, implemented in web/src/voice)
`highlight_step {step_id}` · `show_moment {keyframe_ids, t}` · `go_off_record {}` ·
`go_on_record {}` · `open_map {workflow_id}` · `request_expert {workflow_hint}`.
Webhook tool: `claros_lookup {q}` (session_id from dynamic variable) → `GET /api/dialog/lookup`.

## REST (server)
**Timestamps:** every wall-clock field on the wire (`created_at`, `updated_at`, session `created_at`, `server_t`)
is **epoch milliseconds** (float). Session-clock `t` fields are also ms.
- `GET /api/workflows` → items include `updated_at` (ms), sorted most recent first.
- `GET /api/learners/{id}/mastery?workflow_id=` → `MasteryNode[]` (`[]` when empty).
- `POST /api/requests/{id}/accept` (body optional `{user, lang}`; default user = expert "Expert") → `{request, session_id,
  session, workflow_id, mode}`; the new capture session's `extra` carries `request_id, workflow_hint, moment, requested_by`.
- Seeded fixture keyframe ids (`kf_a_001`…) are served from `data/fixtures/frames/<CLAROS_FIXTURE_LANG|en>/`.
- `POST /api/sessions {mode, user, lang, workflow_id?}` → `{session_id}`
- `POST /api/sessions/{id}/end` → triggers map build (capture) / report (learn)
- `GET /api/workflows` · `GET /api/workflows/{id}` (merged WorkMap) · `GET /api/workflows/{id}/coverage`
- `POST /api/workflows/lookup {screen_state?, utterance, lang}` → `{match?: {workflow_id, score, coverage}, onet?: OnetMatch}`
- `GET /api/requests` · `POST /api/requests {workflow_hint, requested_by, moment?}` · `POST /api/requests/{id}/accept`
- `GET /api/keyframes/{id}.jpg` (redacted only)
- `GET /api/el/token?agent=claros` → ElevenLabs conversation token (server holds API key)
- `GET /api/export/{workflow_id}.skill.md` · MCP at `/mcp`
- `GET /api/learners/{id}/mastery?workflow_id=`

## Server internals (in-process)
- `claros/bus.py` (server-core): `bus.publish(session_id, topic, payload)`, `bus.subscribe(topic, async handler(session_id, payload))`.
  Topics: `ws.in.<type>` (every client message, payload = dict), `ws.out` (payload = server→client msg dict;
  server-core forwards to the socket), `screen.state` (ScreenState), `screen.events` (list[ScreenEvent]),
  `utterance` (dict), `ledger.changed`, `session.ended` ({session_id, mode}), `map.updated`.
- `claros/store.py` (server-core): append-only `log(session_id, kind, payload, t)`; `iter_log(session_id)`;
  kv tables for sessions, workflows (WorkMap JSON by version), requests, keyframes (redacted JPEG path),
  mastery; FTS5 + sqlite-vec helpers `index_text(ns, id, text)`, `search_text(ns, q)`, `index_vec(ns, id, vec)`, `search_vec(ns, vec, k)`.
- `claros/llm.py` (server-core): `async chat(messages, *, model_role: "vision"|"fast"|"smart", json_schema=None, images=None, stream=False)`
  with provider fallback (Isoquant GLM-5.3-Flash → OpenRouter → Gemini); `async embed(inputs, task)` via Jina (text or image);
  `async rerank(query, docs)` via Jina.
- Each package exposes `router: APIRouter` (optional) and `def register(bus)`; `app.py` imports
  `claros.{perception,brain,knowledge,context}` and calls `register(bus)` + `include_router` if present
  (wrapped in try/except so a missing package never breaks boot).
- Session state object: `claros/session.py` (server-core) `Session{id, mode, user, lang, workflow_id, clock_offset, off_record: bool, ...}` with `sessions.get(id)`.

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
- `activity {t, kind: typing|scrolling|navigating|idle|away, tiles_changed, rects?: typing rects [x,y,w,h] in keyframe coords, dims:[w,h]}` (≤2/s)
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
- **relay** (agent side, `agents/set_custom_llm.sh`): the agent's custom LLM is `/llm/v1/chat/completions`, which
  returns the pending `⟦TEXT⟧` verbatim or `skip_turn`. The server dialog loop runs the same either way.
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

## Learner nudges (v2.1)
Server → client: `nudge {id, step_id, kind: predict|why|check|diverge|confirm_step, question, options:[{id,label}], allow_dont_know: true, reference: {keyframe_ids, quote:{text, speaker, lang, translation?}}, spoken: text}`
  (client shows the nudge card in the companion AND speaks `spoken` via the say queue).
Client → server: `nudge_response {id, via: voice|click|key|close|implicit, choice_id?|null, dont_know?: bool, text?: string, t}`.
Server → client: `nudge_result {id, outcome: correct|incorrect|dont_know|skipped|implicit_correct|implicit_incorrect|noted, feedback_spoken, show_reference: bool}` then card closes or shows the reference.
Voice answers arrive as normal `utterance`s; the server matches them to the open nudge (ordinals "first/second/B", option labels, translations; decision model choice) before intent routing.

Implementation: `server/claros/knowledge/nudges.py` (bus `ws.in.nudge_response`; `on_voice` is called by `brain.dialog`
(hosted) and `llm_endpoint` (custom) for learn sessions before intent classification). Feedback is spoken via `say`
(hosted) / `ask` (custom). The client speaks `nudge.spoken` through the say queue and drops it if a `nudge_result` or an
`intervene` arrives first. Options never carry their grade to the client.

**Grading is per record, never "what the expert did on their demo record".** The learner works cases the expert never
showed. For a step's rules: split each predicate into CHOICE conditions (vars whose on-screen label appears in the
decision's own values) and CASE conditions (facts: amounts, dates, counterparties); the case part on this record's facts
→ True: the expert's handling of the rule's case (+ the escalation option) is right; False: the default (`from_value`) is
right; None: the decision model reads the record against the expert's rule text. Field rules and the decision model must
not contradict each other; if they do, or neither can tell, the nudge is **not graded** → `noted` (the expert's
reasoning, neutral, no mastery change; the learner's real action + guardrails decide). Experts differ → every attributed
way is `correct`. Kinds: `predict` (judgment step), `check` (learner already past the step when the floor came free),
`confirm_step` (step match score < 0.7 → "Are you on this step?"; yes → predict), `diverge` (entered a step without its
prerequisite → "on purpose?"; yes → novel case: map unknown + capture request for the expert). 3 dismissals → "just
watch" (said once). A hard stop on the nudge's step closes it as `implicit_incorrect`.

## v2.2 additions (2026-10-04)
### "How Claros decided" (judge-visible evidence for the Apprentice Test)
- `say` / `ask` messages gain optional `why: {when: string, signals: {silence_ms, screen_settled_ms, typing: bool, boundary?: string}, what: string, scope: "company"|"personal_judgment"}`
  e.g. when "pause after Save · 2.1 s quiet", what "you changed a pre-filled value".
- New ws.out `looked_up {unknown_summary, answer, source: {kind: "app_docs"|"onet"|"general", title, url?}}` — Claros answered something itself instead of asking the expert.
- New ws.out `signals {typing, speaking, screen: "changing"|"settled"|"away", gate: "quiet"|"ready"|"asking"}` (≤2/s) for the live indicator.
### Agents (descoped 2026-10-04)
MCP = internal plumbing: optional ElevenLabs MCP tool for Claros's own guardrail lookup during Teach (claros_lookup webhook remains). SKILL.md = a simple export (stretch goal), one item in the Work Map Export menu. No agent-kit UI, no /check panel. "People first, then agents" belongs to the moonshot slide, not the product flow.

### v2.2 as implemented (server, 2026-10-04)
- `ask.why` = `{when: "pause after Save · 1.2 s quiet", signals: {silence_ms, screen_settled_ms, typing, boundary:
  "save"|"submit"|"list"|null}, what: "you changed a pre-filled value" | "you put a record on hold" | "you sent it for
  approval" | "an amount near a round limit" | "no stop rule heard yet" | …, scope}`. Live `ask.text` is action-first
  plain speech ("You changed the expense head to Plants and Machineries — what made you do that?"), ≤22 words.
- `signals {typing, speaking, screen, gate}`: on change only, ≤2/s, 5 s heartbeat, capture + debrief sessions.
- `looked_up {unknown_id, unknown_summary, answer (≤300), source: {kind: app_docs|onet|general, title, url?}}`.
- **Not built (scope cut by the user):** `GET /api/workflows/{id}/agent-kit`, `POST /api/workflows/{id}/check`.
  `GET /api/export/{id}.skill.md` stays (Agent-Skill frontmatter `name`/`description`, guardrails as STOP conditions with
  quotes, how to call the MCP `check_action`). MCP at `/mcp` is Streamable HTTP (stateless, JSON responses), SSE at
  `/mcp/sse`; tools `get_workflow`, `find_guardrails`, `check_action`, `quote`. ElevenLabs MCP registration is blocked
  on this account (`convai_mcp_servers_disabled`), so `claros_lookup` stays the agent's lookup tool.
- ElevenLabs agent auth is ON: clients must start sessions with `GET /api/el/token` (conversationToken) or
  `/api/el/signed-url`; a bare agent_id connect is rejected.
- Debrief (no exam): ≥3 questions not answered live (conflicts addressed to this expert → never-answered live items →
  rules it is unsure about → unseen cases), one per rule/step, learner-originated items capped at 1 and asked last;
  teach-back ≤140 words with `[[step:id]]` segments (`say kind=teachback, step_id`); a correction reads back only the
  changed sentences; explicit confirm publishes. Anything still unanswered becomes `deferred` + `meta.needs_second_run`
  (coverage partial = "needs a second run"), never a list.
- Unknown.meta keys: `origin: builder|debrief|merge|learner`, `probe: scope|threshold|kind|owner|fuzzy`, `var`,
  `literal`, `label`, `gap: reason`, conflict: `ask_expert_id/_name, other_expert_id/_name, other_did, other_why,
  this_did, step_title`, `needs_second_run`. One conflict unknown per disagreement (addressed to the newest expert).
- Off the record / strike that: voice "off the record" (5 langs) → `control off_record_on` + say "Off the record — I'm
  not watching or listening. Tap Resume when you're ready."; user utterances of the previous 5 s are redacted. Resume is
  only the client's `control off_record_off` → say "Back on the record.". `strike_that` tombstones the last 30 s in the
  log (`struck:<kind>`), deletes those keyframes, drops ledger items and (in a debrief) the answers given; the map
  builder/debrief skip tombstones.
- Voice clips: `hello.consent.voice_clips`; `POST /api/sessions/{sid}/clips {event_id, t_start, t_end, mime, audio_b64}`;
  `GET /api/clips/{name}`; `Quote.audio_clip` and `intervene.quote_original.audio_clip` (see REQUESTS.md).
- Matching: `POST /api/workflows/lookup` takes `session_id` (server reads that session's latest screen); requests
  merge (`requested_by_all`, `count`, `merged`), published maps close matching requests, accept → `second_run`.

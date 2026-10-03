# Claros server

FastAPI on :8787 (Python 3.11, uv). Contracts: `../docs/CONTRACTS.md`, models: `claros/models.py`.

```
make sync     # uv sync
make server   # uvicorn --reload on :8787
make test     # pytest
make tunnel   # cloudflared quick tunnel -> set CLAROS_PUBLIC_URL for the ElevenLabs custom LLM
```
Env comes from `../.env` (see `.env.example`). Every key is optional: no LLM keys -> `llm.chat` raises
`LLMUnavailable` (callers degrade); no `JINA_API_KEY` -> deterministic hash embeddings/rerank; no ElevenLabs
key -> `/api/el/token` returns 503 `{degraded: true}`. `GET /healthz` shows key presence + package load status.

## Core modules
- `bus.py` — `bus.publish(sid, topic, payload)`, `bus.subscribe(topic, handler)` (`"ws.in.*"` wildcards), `bus.send(sid, msg)` = publish to `ws.out`.
- `store.py` — `store.log/iter_log`, `kv_put/kv_get/kv_list`, `put_workflow/get_workflow/list_workflows`,
  `put_keyframe/get_keyframe`, `index_text/search_text` (FTS5), `index_vec/search_vec` (sqlite-vec cosine).
- `session.py` — `sessions.get(id)` -> `Session{id, mode, user, lang, workflow_id, clock_offset, off_record, extra}`; `sessions.save(s)`.
- `llm.py` — `chat(messages, model_role=vision|fast|smart, json_schema=dict|PydanticModel, images=[bytes|b64|url], stream=bool, tools=...)`,
  fallback Isoquant -> OpenRouter -> Gemini; timeouts vision 8s / fast 4s / smart 20s; `embed()`, `rerank()` via Jina. Model override: `CLAROS_<PROVIDER>_<ROLE>_MODEL`.
- `ws.py` — `/ws/session/{id}`: answers `clock_sync`, applies `hello`/off-record control, logs + publishes `ws.in.<type>`,
  forwards `ws.out`, queues while disconnected and flushes on re-attach. Also `ws.connected` / `ws.disconnected` topics.
- `el.py` — `GET /api/el/token?agent=claros` (WebRTC conversation token), `GET /api/el/signed-url?agent=claros` (WebSocket).
- `app.py` — mounts packages `claros.{perception,brain,knowledge,context}` (`router` + `register(bus)`, before core routes so
  they can override core fallbacks), sessions, workflows, keyframes (`data/keyframes`), requests inbox fallback.

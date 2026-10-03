# Claros — cloud deploy (public demo)

```
browser (Vercel, Next.js)  ──HTTPS/WSS──▶  Render free web service (Docker, FastAPI :$PORT)
        │                                      ├─ /api/*, /ws/session/{id}, /mcp, /healthz
        │ ElevenLabs WebRTC                    ├─ /llm/v1/chat/completions  (custom-LLM SSE)
        ▼                                      └─ calls out: Isoquant/OpenRouter/Gemini (LLM), Jina, Exa,
ElevenLabs agent ──custom LLM──────────────────▶    Bright Data, Fastino (optional)
```

| Piece    | Where                                   | Config                                   |
|----------|-----------------------------------------|------------------------------------------|
| Repo     | https://github.com/beartackler/claros (private) | `main` auto-deploys on Render     |
| Backend  | Render web service `claros-server`, plan **free** | `render.yaml`, `server/Dockerfile` |
| Frontend | Vercel Hobby project from `web/`        | `web/vercel.json`, env `NEXT_PUBLIC_CLAROS_API` |
| Voice    | ElevenLabs agent (`agents/AGENT_ID`)    | `agents/set_custom_llm.sh <render-url>`  |

URLs (fill in once created): backend `https://claros-server.onrender.com` (Render may suffix it),
frontend `https://<project>.vercel.app`.

## Backend image (`server/Dockerfile`, build context = repo root)
`CLAROS_PROFILE=slim` (default build arg + env) targets the 512 MB free instance:
- Heavy deps live in the optional extra `local` in `server/pyproject.toml` (gliner2[local]/torch, crawl4ai,
  scipy, presidio); the slim image installs without it (local dev: `uv sync --extra local`). PII redaction = regex layer only
  (`CLAROS_PII_MODEL=off`); ledger rule extraction = LLM → regex (`CLAROS_GLINER2=0`).
- RapidOCR (onnxruntime, PP-OCRv5 mobile) kept; models are downloaded at build time. `CLAROS_OCR_THREADS=1`.
- Crawl4AI off (`CLAROS_CRAWL4AI=0`); web reads use Exa → Jina Reader.
- O*NET 31.0 index (CC BY 4.0) built into the image at build time.
- `CLAROS_AUTOSEED=1`: on boot, if the DB has no workflows, the fixture work maps
  (`data/fixtures/workmap_*.json`) are seeded in the background (idempotent).
The slim defaults live in `server/claros/__init__.py` and only apply when the variable is not already set.
`--build-arg CLAROS_PROFILE=full` installs the `local` extra too (needs ≫ 512 MB).

Local check:
```
make deploy-build                     # docker build -f server/Dockerfile -t claros-server:slim .
make deploy-run                       # docker run --memory=512m -p 8787:8787 --env-file .env (OLLAMA_URL dropped)
```

Measured (local Colima, arm64, `--memory=512m`, seed + WS capture session with 30 fixture keyframes en/de/ru
+ custom-LLM SSE + decisions): idle after boot/autoseed ≈ 145 MiB, cgroup peak ≈ 374 MiB, uvicorn process
VmHWM ≈ 438 MiB, steady ≈ 305 MiB, no OOM. Fits 512 MB, but with ~70 MB headroom — keep one worker.

## Environment (Render)
Set in the dashboard or via the Render MCP `update_environment_variables` — never committed:
`ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ISOQUANT_API_KEY, JINA_API_KEY, EXA_API_KEY, BRIGHTDATA_API_KEY`,
optional `FASTINO_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY`.
Plain: `CLAROS_PROFILE=slim`, `CLAROS_CORS_ORIGINS=https://*.vercel.app[,https://your-domain]`
(entries with `*` become an origin regex). Do **not** set `OLLAMA_URL` / `ERPNEXT_URL` in the cloud.

Vercel: `NEXT_PUBLIC_CLAROS_API=https://<render-url>` (WS base is derived: `https→wss`),
optional `NEXT_PUBLIC_ELEVENLABS_AGENT_ID`.

## Redeploy
- Backend: `git push origin main` → Render auto-deploys (or "Manual Deploy" / MCP `trigger_deploy`).
  Note: only committed code ships — uncommitted local edits are not in the cloud build.
- Frontend: `make deploy-web` (= `cd web && vercel deploy --prod`) or connect the GitHub repo in Vercel
  with Root Directory `web`.
- Voice: after the backend URL changes, `bash agents/set_custom_llm.sh https://<render-url>`;
  `bash agents/set_custom_llm.sh --hosted` reverts to the hosted LLM.

## Free-tier limits
- **Spin-down after ~15 min without traffic**; the next request cold-starts the container
  (~30–60 s, plus OCR warm-up). Open `/healthz` (or the web app) a minute before a demo. The ElevenLabs
  custom LLM gives up after ~4 s and cascades to the hosted backup LLM, so a cold backend means a
  degraded first turn, not a dead call.
- **Ephemeral disk**: SQLite (`/app/data/claros.db`), redacted keyframes and captured maps vanish on every
  deploy/restart/spin-down. Fixture maps come back via autoseed; anything captured live does not.
- 512 MB RAM, 0.1 CPU: OCR on full frames is slow (seconds per keyframe); one uvicorn worker.
- 750 free instance-hours/month per workspace.

## Decisions without the local Ollama
`claros.brain.systemone` picks backends by env: Ollama only if `OLLAMA_URL` is set (it is not in the cloud,
and slim sets `CLAROS_DECIDER_OLLAMA=0`), then **Fastino GLiDE** if `FASTINO_API_KEY` is set, then the
**LLM** (`claros.llm` "fast" role: Isoquant → OpenRouter → Gemini), then deterministic **rules**. Each
backend is time-boxed; failures mark it down briefly and fall through, so a decision always returns.
`/healthz` → `llm_recent` shows which provider answered.

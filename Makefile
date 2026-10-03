.PHONY: server web tunnel test sync

sync:
	cd server && uv sync

server:
	cd server && uv run uvicorn claros.app:app --reload --port 8787

web:
	cd web && pnpm dev

tunnel:
	cloudflared tunnel --url http://localhost:8787

test:
	cd server && uv run pytest -q

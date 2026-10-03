.PHONY: server web tunnel test sync

sync:
	cd server && uv sync --extra local

server:
	cd server && uv run --extra local uvicorn claros.app:app --reload --port 8787

web:
	cd web && pnpm dev

tunnel:
	cloudflared tunnel --url http://localhost:8787

test:
	cd server && uv run --extra local pytest -q

# ---- deploy (see docs/DEPLOY.md) ----
.PHONY: deploy-build deploy-run deploy-web deploy-llm
deploy-build:
	docker build -f server/Dockerfile -t claros-server:slim .

deploy-run:
	grep -v '^OLLAMA_URL=\|^ERPNEXT_URL=\|^CLAROS_DB=' .env > /tmp/claros-cloud.env; \
	docker run --rm --name claros-slim -p 8787:8787 --memory=512m --env-file /tmp/claros-cloud.env claros-server:slim

deploy-web:
	cd web && vercel deploy --prod

deploy-llm:
	@test -n "$(URL)" || (echo "usage: make deploy-llm URL=https://<render-url>"; exit 1)
	bash agents/set_custom_llm.sh $(URL)

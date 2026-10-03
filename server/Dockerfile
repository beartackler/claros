# Claros server image. Build from the REPO ROOT (needs data/fixtures):
#   docker build -f server/Dockerfile -t claros-server .
#   docker run --rm -p 8787:8787 --memory=512m --env-file .env claros-server
#
# CLAROS_PROFILE=slim (default) fits Render's free 512 MB instance:
#   - no torch / GLiNER2 / presidio+spaCy (PII = regex layer only; rule extraction = LLM/regex)
#   - RapidOCR (onnxruntime, PP-OCRv5 mobile) kept, models baked into the image, 1 OCR thread
#   - Crawl4AI off, no Ollama (System One decider: Fastino GLiDE if FASTINO_API_KEY, else LLM, else rules)
#   - fixtures auto-seeded on boot (free tier disk is ephemeral)
# Build with --build-arg CLAROS_PROFILE=full to keep every dependency.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS build
ARG CLAROS_PROFILE=slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv UV_NO_CACHE=1
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# slim: drop the torch-backed deps from the manifest, re-lock (keeps all other pinned versions), install.
RUN \
    if [ "$CLAROS_PROFILE" = "slim" ]; then \
      sed -i -e '/"gliner2\[local\]"/d' -e '/"presidio-analyzer/d' pyproject.toml && uv lock ; \
    fi && \
    uv sync --locked --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim
ARG CLAROS_PROFILE=slim
# libGL/glib for opencv-python (pulled in by rapidocr)
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/server PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    CLAROS_PROFILE=${CLAROS_PROFILE} \
    CLAROS_DB=/app/data/claros.db \
    CLAROS_OCR_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    PORT=8787
WORKDIR /app/server
COPY server/claros ./claros
COPY data/fixtures /app/data/fixtures
# Bake heavy downloads into the image so a cold start does no network fetches:
# RapidOCR onnx models (fetched on first init) and the O*NET index (CC BY 4.0).
RUN python -c "from claros.perception.ocr import ENGINE; assert ENGINE.warm(), ENGINE.error" \
 && python -m claros.context.onet_fetch \
 && rm -f /app/data/claros.db*
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"
CMD ["sh", "-c", "exec uvicorn claros.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]

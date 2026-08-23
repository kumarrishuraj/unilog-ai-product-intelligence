# =============================================================================
# Unilog Product Intelligence — production image
#
# Single service by design: FastAPI serves the built React SPA from the same
# origin. That is not a shortcut — the job store is in-process memory, so the UI
# and the API must be the same instance. See docs/DEPLOYMENT.md.
#
# The image is offline-first: it needs no API key, no network at runtime, and no
# model download. Everything it needs to enrich a catalogue ships inside it.
# =============================================================================

# ---- stage 1: build the SPA -------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui

# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# Empty base = same-origin requests, which is what the single-service deploy wants.
# A split deploy overrides it at build time: --build-arg VITE_API_BASE=https://api...
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build


# ---- stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# The optional provider SDKs are pulled in by requirements.txt but are never
# imported unless a key is present, so the image stays useful offline.
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/    ./backend/
COPY evaluation/ ./evaluation/
COPY scripts/    ./scripts/

# Data the pipeline genuinely needs at runtime:
#   packs/ — the seed vocabulary, UOM table and taxonomy
#   raw/   — the delivery-format template (the output schema is READ from it) and
#            the sample feed that powers the one-click demo
COPY data/packs/ ./data/packs/
COPY data/raw/   ./data/raw/

COPY --from=ui /ui/dist ./frontend/dist

# Writable scratch dirs. data/reference stays empty: the official Unilog pack is
# licensed material and is mounted in, not baked into a public image.
RUN mkdir -p data/reference data/processed data/uploads

# Run unprivileged.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# Shell form so $PORT expands — Render, Railway and Fly all inject it.
# One worker on purpose: the job store lives in process memory, so a second worker
# would answer status polls for jobs it has never seen.
CMD uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT} --workers 1

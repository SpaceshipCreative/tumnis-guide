# syntax=docker/dockerfile:1
# One image for every process: `tumnis api|worker|migrate|seed` (FR-12.4). Build from the
# repository root: docker build -f deploy/Dockerfile -t tumnis:dev .

# --- frontend: the built shell, served by the api ------------------------------------
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# --- uv, pinned ----------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv

# --- runtime -------------------------------------------------------------------------
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app/backend

# Dependencies first (cached until uv.lock changes), then the project itself. The source
# stays at /app/backend: the CLI reads alembic.ini and fixtures/ beside the package.
# uv's cache lives in a BuildKit cache mount, never in a layer: the image carries only the
# virtualenv, and Trivy scans only what runs (P0-16).
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN --mount=from=uv,source=/uv,target=/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# The runtime needs no pip (uv built the virtualenv); its vendored libraries only add
# findings to the Trivy scan (P0-16).
RUN /usr/local/bin/python -m pip uninstall --yes --quiet pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin tumnis
# The release this image is (a tag, or sha-<commit> for main): tumnis.__version__ and the
# Tumnis-Version header on /health/live (P0-30). Last, so a new version reuses every layer.
ARG VERSION=0.0.0+dev
ENV TUMNIS_BUILD_VERSION=${VERSION}
USER tumnis
EXPOSE 8080
CMD ["tumnis", "api"]

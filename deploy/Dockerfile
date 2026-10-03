# syntax=docker/dockerfile:1
# Two images from one file, built from the repository root:
#   - `app`, the default (last) stage: `tumnis api|worker|migrate|seed` (FR-12.4).
#       docker build -f deploy/Dockerfile -t tumnis:dev .
#   - `app-extract`: worker-extract (`tumnis worker --queues extract`), the only process
#     that runs Docling (P1-16, ADR-0007, Scott decision 97): the same backend plus the
#     `docling` dependency group (CPU torch) and Docling's models, fetched at build time.
#       docker build -f deploy/Dockerfile --target app-extract -t tumnis:dev-extract .

# --- frontend: the built shell, served by the api ------------------------------------
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# --- uv, pinned ----------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv

# --- base: the runtime's OS packages and the backend's locked dependencies -------------
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

# Debian security updates that no python:3.13-slim digest ships yet, taken from
# trixie-security:
#   - DSA-6530-1 pcre2 (CVE-2026-103111): libpcre2-8-0 10.46-1~deb13u3
#   - DSA-6531-1 OpenSSL (CVE-2026-75804, CVE-2026-84782): libssl3t64, openssl and
#     openssl-provider-legacy 3.5.7-1~deb13u3
# --only-upgrade installs nothing new. Unpinned on purpose: the security suite keeps only the
# current version, so a pin would break the build at the next DSA. Once Renovate bumps the
# digest above to one that ships these versions or later, drop the package from this list;
# when the list is empty, delete the layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        libpcre2-8-0 \
        libssl3t64 \
        openssl \
        openssl-provider-legacy \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend

# libmagic reads an upload's real type from its content (P1-16, SEC-10). git and the ssh
# client pull an Obsidian vault from its Git remote with a read-only deploy key (P3-12).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached until uv.lock changes), then the project itself in each image
# below. The source stays at /app/backend: the CLI reads alembic.ini and fixtures/ beside
# the package. uv's cache lives in a BuildKit cache mount, never in a layer: the image
# carries only the virtualenv, and Trivy scans only what runs (P0-16).
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# --- extract-deps: + Docling, CPU torch and the models they read -----------------------
# Cached until uv.lock changes: a source change never re-installs torch or fetches models.
FROM base AS extract-deps
# RapidOCR imports OpenCV (the opencv-python wheel), which links the X11 client library
# and OpenGL even when nothing is drawn. Liberation fonts stand in for the PDF base fonts
# (Helvetica, Times, Courier) that a PDF names without embedding them: without them the
# page images the layout model reads lose bold, and headings come out as body text.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0t64 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
# torch and torchvision resolve from the PyTorch CPU index only (backend/pyproject.toml,
# tool.uv.sources), so no CUDA wheels.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --group docling --no-install-project
# The models, fetched here once so a running worker needs no Hugging Face or ModelScope
# egress: Docling's layout and TableFormer models and RapidOCR's weights (the standard
# pipeline's OCR) into DOCLING_ARTIFACTS_PATH, which Docling reads instead of downloading,
# and the chunker's tokenizer (KnowledgeSettings.chunk_tokenizer; pass this build argument
# when that setting changes) into the Hugging Face cache. Root owns them; the worker only
# reads them.
ENV HF_HOME=/opt/tumnis/huggingface \
    DOCLING_ARTIFACTS_PATH=/opt/tumnis/docling-models \
    HF_HUB_DISABLE_TELEMETRY=1
ARG CHUNK_TOKENIZER=sentence-transformers/all-MiniLM-L6-v2
RUN docling-tools models download --quiet -o "$DOCLING_ARTIFACTS_PATH" layout tableformer rapidocr \
    && python -c "import sys; from transformers import AutoTokenizer; AutoTokenizer.from_pretrained(sys.argv[1])" "$CHUNK_TOKENIZER" \
    && rm -rf "$HF_HOME/xet" /root/.cache
# At run time everything comes from the image: no call to the Hub, ever.
ENV HF_HUB_OFFLINE=1

# --- app-extract: worker-extract's image ---------------------------------------------
FROM extract-deps AS app-extract
COPY backend/ ./
RUN --mount=from=uv,source=/uv,target=/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group docling
# As in `app` below: no pip, the tumnis user and its runtime folders.
RUN /usr/local/bin/python -m pip uninstall --yes --quiet pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin tumnis \
    && install -d -o tumnis -g tumnis /var/lib/tumnis/spool /scratch /var/lib/tumnis/obsidian
ARG VERSION=0.0.0+dev
ENV TUMNIS_BUILD_VERSION=${VERSION}
USER tumnis
CMD ["tumnis", "worker", "--queues", "extract"]

# --- app: every other process; last, so a plain build makes it -------------------------
FROM base AS app
COPY backend/ ./
RUN --mount=from=uv,source=/uv,target=/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# The runtime needs no pip (uv built the virtualenv); its vendored libraries only add
# findings to the Trivy scan (P0-16).
# Every folder the processes write at runtime (the KnowledgeSettings *_dir defaults) is made
# here, owned by tumnis: /var/lib/tumnis itself stays root's, and an empty named volume
# mounted on one of these takes its contents and owner from the image. The spool (api,
# worker, worker-extract), the extract worker's scratch, and the worker's Git vault clones
# (P3-12).
RUN /usr/local/bin/python -m pip uninstall --yes --quiet pip \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin tumnis \
    && install -d -o tumnis -g tumnis /var/lib/tumnis/spool /scratch /var/lib/tumnis/obsidian
# The release this image is (a tag, or sha-<commit> for main): tumnis.__version__ and the
# Tumnis-Version header on /health/live (P0-30). Last, so a new version reuses every layer.
ARG VERSION=0.0.0+dev
ENV TUMNIS_BUILD_VERSION=${VERSION}
USER tumnis
EXPOSE 8080
CMD ["tumnis", "api"]

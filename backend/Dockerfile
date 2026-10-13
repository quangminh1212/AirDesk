# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# AirDesk backend — production image (multi-stage, non-root, slim).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl for healthchecks, build tools for wheels that need compiling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Builder: install dependencies into an isolated prefix.
# ---------------------------------------------------------------------------
FROM base AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --prefix=/install .

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM base AS runtime
WORKDIR /app

COPY --from=builder /install /usr/local
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 airdesk \
    && chown -R airdesk:airdesk /app
USER airdesk

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

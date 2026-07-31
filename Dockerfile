# syntax=docker/dockerfile:1

# ---- Builder: resolve and install dependencies with uv -------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first so this layer is cached independently of
# application code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# ---- Runtime: minimal image, non-root user --------------------------------
FROM python:3.12-slim

RUN groupadd --gid 1000 hermes \
    && useradd --uid 1000 --gid hermes --create-home --home-dir /home/hermes hermes

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_REPOSITORIES=/data/repos \
    HERMES_KNOWLEDGE=/data/knowledge \
    HERMES_SKILLS=/data/skills \
    HERMES_LOGS=/data/logs

WORKDIR /app

COPY --from=builder --chown=hermes:hermes /app/.venv /app/.venv

# Bundled defaults. HERMES_KNOWLEDGE / HERMES_SKILLS are typically
# overridden by bind mounts at runtime (see docker-compose.yml); the
# workspaces registry has no dedicated mount and always ships with the
# image.
COPY --chown=hermes:hermes knowledge/ knowledge/
COPY --chown=hermes:hermes skills/ skills/
COPY --chown=hermes:hermes profiles/ profiles/
COPY --chown=hermes:hermes workspaces/ workspaces/

RUN mkdir -p /data/repos /data/knowledge /data/skills /data/logs \
    && chown -R hermes:hermes /data

USER hermes

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD hermes --help > /dev/null || exit 1

ENTRYPOINT ["hermes"]
CMD ["--help"]

# All-in-one image: web (Next.js, public port $PORT) + API + worker in one container.
# Hosting platforms that build a repository's root Dockerfile (e.g. Railway) need only a PostgreSQL database,
# a volume mounted at /data and a few variables (docs/DEPLOYMENT.md). Also runs on any Docker host.
FROM node:22.21.1-bookworm-slim AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
COPY packages/ /app/packages/
RUN npm run build

FROM python:3.13.9-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg postgresql-client ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
WORKDIR /app/services/core
COPY services/core/pyproject.toml services/core/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/core/ ./
COPY fixtures/ /app/fixtures/
RUN uv sync --frozen --no-dev
COPY --from=web /app/apps/web/.next/standalone /app/web
COPY --from=web /app/apps/web/.next/static /app/web/.next/static
COPY --from=web /app/apps/web/public /app/web/public
COPY infra/docker/allinone-entrypoint.sh /usr/local/bin/lq-allinone
ENV PATH="/app/services/core/.venv/bin:$PATH" LQ_FIXTURES_DIR=/app/fixtures LQ_ENV=prod LQ_ENV_FILE=none \
    LQ_STORAGE_BACKEND=local LQ_STORAGE_LOCAL_DIR=/data/storage LQ_WORK_DIR=/data/work LQ_OUTBOX_DIR=/data/outbox \
    LQ_COOKIE_SECURE=true LQ_PORT=8000 API_INTERNAL_URL=http://127.0.0.1:8000 PORT=3000
RUN useradd --system --uid 10001 lq && mkdir -p /data && chown lq /data && chmod +x /usr/local/bin/lq-allinone \
    && command -v setpriv
# Starts as root only to take ownership of a root-owned mounted volume, then drops to uid 10001 (setpriv).
EXPOSE 3000
CMD ["lq-allinone"]

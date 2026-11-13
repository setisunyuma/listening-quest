# API and worker image (same Python package, two commands).
FROM python:3.13.9-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
WORKDIR /app/services/core
COPY services/core/pyproject.toml services/core/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/core/ ./
COPY fixtures/ /app/fixtures/
RUN uv sync --frozen --no-dev
ENV PATH="/app/services/core/.venv/bin:$PATH" LQ_FIXTURES_DIR=/app/fixtures
RUN useradd --system --uid 10001 lq && mkdir -p /data && chown lq /data
USER lq
EXPOSE 8000
CMD ["uvicorn", "lq.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

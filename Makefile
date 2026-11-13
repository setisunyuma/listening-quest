# Entry points. Without Docker: local PostgreSQL (scripts/dev-postgres.sh) + local file storage.
# With Docker: `make compose-up`.
SHELL := /bin/bash
CORE := cd services/core &&
WEB := cd apps/web &&
export UV_SYSTEM_CERTS ?= 1

.PHONY: bootstrap db user dev api worker web demo test test-core test-web e2e lint typecheck contracts backup restore compose-up compose-down stop

bootstrap:            ## install dependencies, start the local database, migrate, register example units
	command -v ffmpeg >/dev/null || { echo "install FFmpeg first"; exit 1; }
	$(CORE) uv sync
	$(WEB) npm ci
	scripts/dev-postgres.sh start
	$(CORE) uv run lq migrate
	$(CORE) uv run lq seed --realm live
	@echo "next: create your account with  make user  (or: cd services/core && uv run lq create-user <name>)"

db:
	scripts/dev-postgres.sh start

user:
	$(CORE) uv run lq create-user $${LQ_USER:-owner}

dev: db               ## API :8000 + worker + Next.js :3000 (Ctrl-C stops all)
	@trap 'kill 0' EXIT; \
	($(CORE) uv run uvicorn lq.api.app:app --port 8000 --reload) & \
	($(CORE) uv run lq worker) & \
	($(WEB) npm run dev) & \
	wait

demo: db              ## demo data area (free demo providers, tone audio "音声動作確認用")
	$(CORE) uv run lq migrate
	$(CORE) uv run lq demo

test: test-core test-web  ## everything that needs no browser

test-core: db
	$(CORE) uv run ruff check lq tests && uv run ruff format --check lq tests && uv run pytest

test-web:
	$(WEB) npm run lint && npm run typecheck && npm run contracts:check && npm run build

e2e: db               ## browser tests (fresh isolated data set)
	$(WEB) npm run build && npm run e2e

contracts:            ## regenerate OpenAPI + JSON Schema + TypeScript types after an API change
	$(CORE) uv run lq openapi --out ../../packages/contracts/openapi.json && uv run lq schema --out ../../packages/contracts/unit.schema.json
	$(WEB) npm run contracts

backup:               ## database dump + audio objects into backups/<timestamp>/
	scripts/backup.sh

restore:              ## restore a backup made by `make backup`: make restore DIR=backups/2026...
	scripts/restore.sh $(DIR)

compose-up:
	docker compose -f infra/compose.yaml --env-file .env up -d --build

compose-down:
	docker compose -f infra/compose.yaml --env-file .env down

stop:
	-pkill -f "uvicorn lq.api.app"; -pkill -f "lq worker"; -pkill -f next-server; scripts/dev-postgres.sh stop

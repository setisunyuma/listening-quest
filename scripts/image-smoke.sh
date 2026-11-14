#!/usr/bin/env bash
# Smoke test of the all-in-one image (root Dockerfile) the way a hosting platform runs it:
# one container + PostgreSQL + a volume at /data, configured only through environment variables.
# The volume is made root-owned first, like Railway's volumes, so the ownership hand-over is exercised.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${TAG:-listening-quest:smoke}"
NET=lq-smoke; DB=lq-smoke-db; APP=lq-smoke-app; VOL=lq-smoke-data; ROVOL=lq-smoke-ro
JAR="$(mktemp)"
cleanup() { docker rm -f "$APP" "$DB" lq-smoke-ro-app >/dev/null 2>&1 || true; docker volume rm "$VOL" "$ROVOL" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; rm -f "$JAR"; }
cleanup; trap cleanup EXIT
[ -n "${SKIP_BUILD:-}" ] || docker build -t "$TAG" "$ROOT"
docker network create "$NET" >/dev/null
docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL:/data" --entrypoint sh postgres:16.10-bookworm -c 'chown 0:0 /data && chmod 755 /data && ls -ld /data'
docker run -d --name "$DB" --network "$NET" -e POSTGRES_USER=lq -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=lq postgres:16.10-bookworm >/dev/null
for i in $(seq 1 60); do docker exec "$DB" pg_isready -U lq >/dev/null 2>&1 && break; sleep 1; done
run_app() {
  # DATABASE_URL in the platform's own form; LQ_COOKIE_SECURE=false only because this test uses plain http
  docker run -d --name "$APP" --network "$NET" -p 3999:3000 -v "$VOL:/data" \
    -e DATABASE_URL=postgres://lq:pw@$DB:5432/lq -e LQ_SETUP_CODE=smoke-setup-code-123 -e LQ_COOKIE_SECURE=false "$TAG" >/dev/null
  for i in $(seq 1 90); do curl -fsS http://localhost:3999/api/health >/dev/null 2>&1 && return 0; sleep 2; done
  docker logs --tail 100 "$APP"; return 1
}
run_app
echo "== root-owned volume handed to the app user; app processes do not run as root"
docker logs "$APP" 2>&1 | grep -q "giving /data to the app user"
docker exec "$APP" sh -c 'stat -c %u /data/storage' | grep -qx 10001
docker exec "$APP" sh -c 'for p in /proc/[0-9]*; do u=$(awk "/^Uid:/{print \$2}" $p/status 2>/dev/null); c=$(tr "\0" " " < $p/cmdline 2>/dev/null); case "$c" in *uvicorn*|*server.js*|*"lq.cli worker"*) [ "$u" = 10001 ] || { echo "runs as uid $u: $c"; exit 1; };; esac; done'
echo "== web page and API through one port"
curl -fsS http://localhost:3999/setup | grep -q "Listening Quest"
curl -fsS http://localhost:3999/api/auth/setup | grep -q '"available":true'
echo "== one-time owner setup from the browser API"
curl -fsS -c "$JAR" -H 'Content-Type: application/json' -d '{"code":"smoke-setup-code-123","username":"owner","password":"owner-password-123"}' http://localhost:3999/api/auth/setup >/dev/null
curl -fsS -b "$JAR" http://localhost:3999/api/home | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["realm"]=="live";print("home ok for", d["username"])'
curl -fsS http://localhost:3999/api/auth/setup | grep -q '"account_exists"'
echo "== the pipeline stores audio on the /data volume (free demo providers)"
docker exec "$APP" lq generate common_test 3 --realm demo --no-send | python3 -c 'import sys,json;j=json.load(sys.stdin);assert j[0]["unit_status"]=="READY", j;print("demo unit READY")'
before=$(docker exec "$APP" sh -c 'find /data/storage/units -type f | wc -l')
[ "$before" -gt 0 ] && echo "audio files on the volume: $before"
docker exec "$APP" lq config | python3 -c 'import sys,json;c=json.load(sys.stdin);assert c["openai_api_key"]=="not set" and c["database_url"].startswith("postgresql+psycopg://lq:***@");print("config ok (secrets hidden)")'
echo "== restart keeps the account and the stored audio"
docker rm -f "$APP" >/dev/null
run_app
curl -fsS -c "$JAR" -H 'Content-Type: application/json' -d '{"username":"owner","password":"owner-password-123"}' http://localhost:3999/api/auth/login >/dev/null
after=$(docker exec "$APP" sh -c 'find /data/storage/units -type f | wc -l')
[ "$after" = "$before" ] && echo "audio files after restart: $after"
echo "== an unwritable volume stops the container with a clear error (never a silent failure)"
docker volume create "$ROVOL" >/dev/null
docker run -d --name lq-smoke-ro-app --network "$NET" -v "$ROVOL:/data:ro" -e DATABASE_URL=postgres://lq:pw@$DB:5432/lq "$TAG" >/dev/null
for i in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Running}}' lq-smoke-ro-app)" = false ] && break; sleep 1; done
[ "$(docker inspect -f '{{.State.Running}}' lq-smoke-ro-app)" = false ] && [ "$(docker inspect -f '{{.State.ExitCode}}' lq-smoke-ro-app)" != 0 ]
docker logs lq-smoke-ro-app 2>&1 | grep -E "FATAL" | head -2
echo "image smoke test passed"

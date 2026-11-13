#!/usr/bin/env bash
# Docker を使わずにローカル PostgreSQL を起動する補助スクリプト (CI・コンテナ内開発用)。
# 使い方: scripts/dev-postgres.sh start|stop   (既定: port 55432, DB lq / lq_test, user lq, password lq)
set -euo pipefail
PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${LQ_PGDATA:-$ROOT/var/pgdata}"
PORT="${LQ_PGPORT:-55432}"
run() { if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi; }
case "${1:-start}" in
  start)
    mkdir -p "$DATA"
    if [ "$(id -u)" = "0" ]; then chown -R postgres "$DATA"; chmod o+x "$ROOT" "$ROOT/var" 2>/dev/null || true; fi
    if [ ! -f "$DATA/PG_VERSION" ]; then
      run "$PGBIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
    fi
    if ! run "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      run "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp -c fsync=off" -l "$DATA/server.log" -w start >/dev/null
    fi
    psql -h 127.0.0.1 -p "$PORT" -U postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='lq'" | grep -q 1 || \
      psql -h 127.0.0.1 -p "$PORT" -U postgres -c "CREATE ROLE lq LOGIN PASSWORD 'lq' CREATEDB" >/dev/null
    for db in lq lq_test lq_e2e; do
      psql -h 127.0.0.1 -p "$PORT" -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || \
        psql -h 127.0.0.1 -p "$PORT" -U postgres -c "CREATE DATABASE $db OWNER lq" >/dev/null
    done
    echo "postgres ready on 127.0.0.1:$PORT (db: lq, lq_test, lq_e2e)"
    ;;
  stop) run "$PGBIN/pg_ctl" -D "$DATA" -m fast stop ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac

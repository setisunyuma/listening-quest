#!/bin/sh
# Start API + worker (lq serve --role all, migrations first) and the web server; if any of them stops,
# stop the container so the platform restarts it.
set -e
mkdir -p /data/storage /data/work /data/outbox 2>/dev/null || true
lq serve --role all &
CORE=$!
for i in $(seq 1 60); do
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" 2>/dev/null && break
  kill -0 "$CORE" 2>/dev/null || exit 1
  sleep 1
done
cd /app/web
HOSTNAME=0.0.0.0 node server.js &
WEB=$!
trap 'kill $CORE $WEB 2>/dev/null' TERM INT
while kill -0 "$CORE" 2>/dev/null && kill -0 "$WEB" 2>/dev/null; do sleep 2; done
kill $CORE $WEB 2>/dev/null || true
wait
exit 1

#!/bin/sh
# All-in-one container entry point.
# 1. As root (only if started as root): make /data usable by the unprivileged "lq" user. Hosting platforms
#    such as Railway mount volumes owned by root; the app itself never runs as root.
# 2. As lq: prove /data is writable (fail loudly otherwise), then start API + worker and the web server.
#    If any of them stops, the container stops so the platform restarts it.
set -e
if [ "$(id -u)" = "0" ]; then
  if ! mkdir -p /data/storage /data/work /data/outbox 2>/dev/null; then
    echo "lq: FATAL: cannot create directories in /data (volume missing, read-only or full). Audio cannot be stored; refusing to start." >&2
    exit 1
  fi
  if [ "$(stat -c %u /data/storage)" != "10001" ] || [ "$(stat -c %u /data)" != "10001" ]; then
    echo "lq: giving /data to the app user (volume was owned by $(stat -c %U:%G /data))"
    chown -R 10001:10001 /data || { echo "lq: FATAL: cannot change ownership of /data (read-only or restricted volume)" >&2; exit 1; }
  fi
  exec setpriv --reuid=10001 --regid=10001 --clear-groups --inh-caps=-all "$0" "$@"
fi
probe="/data/storage/.write-check-$$"
if ! ( mkdir -p /data/storage /data/work /data/outbox && echo ok > "$probe" && rm -f "$probe" ) 2>/dev/null; then
  echo "lq: FATAL: /data is not writable by uid $(id -u) (owner: $(stat -c %U:%G /data 2>/dev/null || echo missing)). Audio cannot be stored; refusing to start." >&2
  exit 1
fi
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

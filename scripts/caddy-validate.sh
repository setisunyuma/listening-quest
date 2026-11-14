#!/usr/bin/env bash
# Validate infra/Caddyfile the way compose.prod.yaml runs it: with and without the optional ACME_EMAIL.
#   scripts/caddy-validate.sh [path-to-caddy-binary]     (CI runs it inside the caddy image)
set -euo pipefail
CADDY="${1:-caddy}"
FILE="$(cd "$(dirname "$0")/.." && pwd)/infra/Caddyfile"
run() {
  local email="$1"
  ( export LQ_DOMAIN=lq.example.com ACME_EMAIL="$email" CADDY_EMAIL_DIRECTIVE=""
    if [ -n "$ACME_EMAIL" ]; then export CADDY_EMAIL_DIRECTIVE="email $ACME_EMAIL"; fi
    "$CADDY" validate --config "$FILE" --adapter caddyfile >/dev/null 2>&1 || { "$CADDY" validate --config "$FILE" --adapter caddyfile; exit 1; }
    "$CADDY" adapt --config "$FILE" --adapter caddyfile 2>/dev/null )
}
cfg_without="$(run "")"
cfg_with="$(run "owner@example.org")"
echo "$cfg_without" | grep '"lq.example.com"' || { echo "site address missing"; exit 1; } >/dev/null
echo "$cfg_without" | grep '"dial":"api:8000"' || { echo "/api route to api:8000 missing"; exit 1; } >/dev/null
echo "$cfg_without" | grep '"dial":"web:3000"' || { echo "route to web:3000 missing"; exit 1; } >/dev/null
echo "$cfg_with" | grep 'owner@example.org' || { echo "ACME e-mail not applied"; exit 1; } >/dev/null
if echo "$cfg_without" | grep '"email"'; then echo "unexpected email when ACME_EMAIL is empty"; exit 1; fi >/dev/null
echo "Caddyfile OK (with and without ACME_EMAIL)"

#!/usr/bin/env bash
# Real-API verification of ONE short Common Test unit, then the same unit used in the app.
#   OPENAI_API_KEY=... scripts/live-verify.sh <cost-cap-usd>
# 1. fresh, separate database (lq_verify) and storage: nothing here touches your learning data
# 2. `lq verify-live`: write -> blind review -> real speech -> independent speech recognition -> READY,
#    only this one job may spend, up to the cap (registered examples / auto-replenish are not run)
# 3. Playwright (phone width, Chromium): log in, open the unit, play the real audio to the end, answer,
#    read the explanation, confirm it is saved in the history. Screenshots go to var/verify/.
# This is not an iPhone test; the iPhone checklist in README stays separate.
set -euo pipefail
CAP="${1:?usage: scripts/live-verify.sh <cost-cap-usd> [--rehearsal]}"
REHEARSAL="${2:-}"   # --rehearsal: same flow with free demo providers (tone audio), to test the script itself
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export LQ_DATABASE_URL="${LQ_VERIFY_DATABASE_URL:-postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_verify}"
export LQ_STORAGE_BACKEND=local LQ_STORAGE_LOCAL_DIR="$ROOT/var/verify-storage" LQ_WORK_DIR="$ROOT/var"
export LQ_VERIFY_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
mkdir -p "$ROOT/var/verify"
cd "$ROOT/services/core"
uv run python - <<'PY'
from sqlalchemy import text
from lq.db.base import get_engine
with get_engine().begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
PY
rm -rf "$LQ_STORAGE_LOCAL_DIR"
uv run lq migrate >/dev/null
REALM=live; [ -n "$REHEARSAL" ] && REALM=demo
uv run lq create-user verifier --password-env LQ_VERIFY_PASSWORD --realm "$REALM" >/dev/null
set +e
uv run lq verify-live --cost-cap-usd "$CAP" ${REHEARSAL:+--rehearsal} | tee "$ROOT/var/verify/verify-live.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" = 0 ] || { echo "generation did not reach READY; see the report above (nothing further was run)"; exit "$rc"; }
cd "$ROOT/apps/web"
[ -d .next ] || npm run build
[ -n "$REHEARSAL" ] && export LQ_VERIFY_REHEARSAL=1
npx playwright test --config playwright.live.config.ts --trace off
echo "live verification finished: report and screenshots in var/verify/"

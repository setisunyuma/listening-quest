#!/usr/bin/env bash
# Real-API verification of ONE short Common Test unit, then the same unit used in the app.
#   OPENAI_API_KEY=... scripts/live-verify.sh <cost-cap-usd>
# 1. separate database (lq_verify) and storage, kept between runs (they hold the budget record);
#    nothing here touches your learning data
# 2. `lq verify-live`: write -> blind review -> real speech -> independent speech recognition -> READY,
#    only this one job may spend, up to the cap (registered examples / auto-replenish are not run)
# 3. Playwright (phone width, Chromium): log in, open the unit, play the real audio to the end, answer,
#    read the explanation, confirm it is saved in the history. Screenshots go to var/verify/.
# This is not an iPhone test; the iPhone checklist in README stays separate.
set -euo pipefail
CAP="${1:?usage: scripts/live-verify.sh <cost-cap-usd> [--rehearsal]   (the cap is limited further by the remaining shared verification budget)}"
export REHEARSAL="${2:-}"   # --rehearsal: same flow with free demo providers (tone audio), to test the script itself
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export LQ_DATABASE_URL="${LQ_VERIFY_DATABASE_URL:-postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_verify}"
export LQ_STORAGE_BACKEND=local LQ_STORAGE_LOCAL_DIR="$ROOT/var/verify-storage" LQ_WORK_DIR="$ROOT/var"
export LQ_VERIFY_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
mkdir -p "$ROOT/var/verify"
cd "$ROOT/services/core"
# The verification database is kept between runs: it holds the record of what verification has already
# cost, and that budget must not reset. Only the free --rehearsal uses (and resets) a separate database.
if [ -n "$REHEARSAL" ]; then
  export LQ_DATABASE_URL="${LQ_DATABASE_URL%/*}/lq_e2e"
  uv run python - <<'PY'
from sqlalchemy import text
from lq.db.base import get_engine
with get_engine().begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
PY
fi
uv run lq migrate >/dev/null
REALM=live; [ -n "$REHEARSAL" ] && REALM=demo
# a fresh browser password every run (the account itself is kept)
uv run python - <<'PY'
import os
from sqlalchemy import select
from lq.db import models as m
from lq.db.base import session_scope
from lq.services import auth
realm = "demo" if os.environ.get("REHEARSAL") else "live"
with session_scope() as s:
    u = s.execute(select(m.User).where(m.User.username == "verifier")).scalar_one_or_none()
    if u is None:
        auth.create_user(s, "verifier", os.environ["LQ_VERIFY_PASSWORD"], realm=realm)
    else:
        u.password_hash = auth.hash_password(os.environ["LQ_VERIFY_PASSWORD"])
PY
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

#!/usr/bin/env bash
# End-to-end check of the Docker Compose stack (web + api + worker + PostgreSQL + S3 storage).
# Runs in CI; works locally wherever Docker is available. Uses only the free demo providers.
#   scripts/compose-smoke.sh            (expects `docker compose -f infra/compose.yaml up -d --build` done)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DC=(docker compose -f "$ROOT/infra/compose.yaml")
BASE="${BASE:-http://localhost:3000}"
JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT
step() { echo "== $*"; }

step "wait for web and api"
for i in $(seq 1 90); do curl -fsS "$BASE/api/health" >/dev/null 2>&1 && break; sleep 2; [ "$i" = 90 ] && { "${DC[@]}" ps; "${DC[@]}" logs --tail 80; exit 1; }; done
curl -fsS "$BASE/login" | grep "Listening Quest" >/dev/null

step "create the demo user and seed the example units (CLI inside the api container)"
"${DC[@]}" exec -T -e SMOKE_PW=smoke-password-123 api lq create-user smoke --realm demo --password-env SMOKE_PW
"${DC[@]}" exec -T api lq seed --realm demo >/dev/null

step "log in through the web origin"
csrf=$(curl -fsS -c "$JAR" -H 'Content-Type: application/json' -d '{"username":"smoke","password":"smoke-password-123"}' "$BASE/api/auth/login" | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrf_token"])')
H=(-b "$JAR" -c "$JAR" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json')

step "queue one generation through the API; the worker container must finish it (S3 upload included)"
curl -fsS "${H[@]}" -d '{"mode":"common_test","part":"3","primary_skill":"numbers_negation","band":3,"count":1}' "$BASE/api/admin/jobs" >/dev/null
for i in $(seq 1 120); do
  ready=$(curl -fsS "${H[@]}" "$BASE/api/library?mode=common_test" | python3 -c 'import sys,json;print(json.load(sys.stdin)["count"])')
  [ "$ready" -ge 1 ] && break; sleep 3
  [ "$i" = 120 ] && { curl -fsS "${H[@]}" "$BASE/api/admin/jobs"; "${DC[@]}" logs --tail 120 worker; exit 1; }
done
echo "READY units visible in the library: $ready"

step "answer one unit: start, fetch audio with Range from S3, submit, read feedback"
unit=$(curl -fsS "${H[@]}" "$BASE/api/library?mode=common_test" | python3 -c 'import sys,json;u=[x for x in json.load(sys.stdin)["units"] if "train" in x["session_types"]];print(u[0]["id"])')
att=$(curl -fsS "${H[@]}" -d "{\"attempt_id\":\"smoke$(date +%s)abcdef\",\"unit_id\":\"$unit\"}" "$BASE/api/attempts")
aid=$(echo "$att" | python3 -c 'import sys,json;print(json.load(sys.stdin)["attempt_id"])')
url=$(echo "$att" | python3 -c 'import sys,json;print(json.load(sys.stdin)["unit"]["segments"][0]["audio"]["url"])')
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -H 'Range: bytes=0-99' "$BASE$url")
[ "$code" = 206 ] || { echo "expected 206 for a Range request, got $code"; exit 1; }
answers=$(echo "$att" | python3 -c 'import sys,json;q=json.load(sys.stdin)["unit"]["questions"];print(json.dumps({"answers":{x["id"]:{"choice_id":x["choices"][0]["id"]} for x in q}}))')
res=$(curl -fsS "${H[@]}" -d "$answers" "$BASE/api/attempts/$aid/submit")
echo "$res" | python3 -c 'import sys,json;r=json.load(sys.stdin);assert r["status"]=="submitted" and r["questions"][0]["explanation_ja"];print("graded:",r["correct_count"],"/",r["total"])'
curl -fsS "${H[@]}" -d '{}' "$BASE/api/attempts/$aid/ack" | python3 -c 'import sys,json;r=json.load(sys.stdin);print("xp:",r["total_xp"])'

step "the stored object is in the S3 bucket, not on the api container's disk"
"${DC[@]}" exec -T api python -c "
from lq.storage import get_storage
st = get_storage(); keys = st.list('units')
assert keys and type(st).__name__ == 'S3Storage', keys
print('S3 objects:', len(keys))"
echo "compose smoke test passed"

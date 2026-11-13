#!/usr/bin/env bash
# Back up the database (pg_dump custom format) and every stored audio object.
#   scripts/backup.sh [target-dir]       default: backups/<UTC timestamp>
# Works for the local setup (LQ_STORAGE_BACKEND=local) and, with the AWS CLI installed, for S3/MinIO.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
DB="${LQ_DATABASE_URL:-postgresql+psycopg://lq:lq@127.0.0.1:55432/lq}"
DB="${DB/postgresql+psycopg/postgresql}"
OUT="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
pg_dump --format=custom --no-owner --file "$OUT/db.dump" "$DB"
if [ "${LQ_STORAGE_BACKEND:-local}" = "s3" ]; then
  AWS_ACCESS_KEY_ID="$LQ_S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$LQ_S3_SECRET_KEY" aws --endpoint-url "$LQ_S3_ENDPOINT_URL" s3 sync "s3://${LQ_S3_BUCKET:-listening-quest}" "$OUT/storage" --only-show-errors
else
  SRC="${LQ_STORAGE_LOCAL_DIR:-$ROOT/var/storage}"
  mkdir -p "$OUT/storage" && [ -d "$SRC" ] && cp -a "$SRC/." "$OUT/storage/"
fi
( cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
echo "backup written to $OUT ($(du -sh "$OUT" | cut -f1))"

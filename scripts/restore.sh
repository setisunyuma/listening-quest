#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh into an EMPTY database / storage.
# It refuses to overwrite a database that already has learning data: restore into a fresh database,
# check it, then switch LQ_DATABASE_URL over. (Per-user JSON restore in 設定 merges without overwriting.)
#   scripts/restore.sh backups/<timestamp> [--force-into-nonempty]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:?usage: scripts/restore.sh <backup-dir>}"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
DB="${LQ_DATABASE_URL:-postgresql+psycopg://lq:lq@127.0.0.1:55432/lq}"
DB="${DB/postgresql+psycopg/postgresql}"
( cd "$SRC" && sha256sum --quiet -c SHA256SUMS ) || { echo "checksum mismatch: backup is damaged"; exit 1; }
rows=$(psql "$DB" -tAc "select count(*) from attempts" 2>/dev/null || echo 0)
if [ "${rows:-0}" != "0" ] && [ "${2:-}" != "--force-into-nonempty" ]; then
  echo "target database already contains $rows attempts; refusing to overwrite. Use a new empty database."; exit 1
fi
pg_restore --clean --if-exists --no-owner --dbname "$DB" "$SRC/db.dump"
if [ "${LQ_STORAGE_BACKEND:-local}" = "s3" ]; then
  AWS_ACCESS_KEY_ID="$LQ_S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$LQ_S3_SECRET_KEY" aws --endpoint-url "$LQ_S3_ENDPOINT_URL" s3 sync "$SRC/storage" "s3://${LQ_S3_BUCKET:-listening-quest}" --only-show-errors
else
  DST="${LQ_STORAGE_LOCAL_DIR:-$ROOT/var/storage}"
  mkdir -p "$DST" && cp -an "$SRC/storage/." "$DST/"
fi
( cd "$ROOT/services/core" && uv run lq migrate )
echo "restored from $SRC"

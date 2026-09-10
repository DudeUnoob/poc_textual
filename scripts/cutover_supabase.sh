#!/usr/bin/env bash
# Operator cutover after schema migration and private bucket creation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
: "${SUPABASE_URL:?Load operator environment first}"
: "${SUPABASE_SECRET_KEY:?Load the server-only secret key first}"
: "${MIGRATION_ID:?Set a unique migration identifier}"
: "${LOCAL_WRITES_STOPPED:?Set true only after stopping the local web and worker}"
[[ "$LOCAL_WRITES_STOPPED" == "true" ]] || exit 1
mkdir -p data/recovery
python3 scripts/migrate_to_supabase.py \
  --database data/workbench/workbench.db \
  --storage-root data/workbench \
  --migration-id "$MIGRATION_ID" > "data/recovery/migrate-${MIGRATION_ID}.json"
echo 'Migration completed; inspect the manifest, then set WORKBENCH_BACKEND=supabase.'
echo 'Bootstrap the verified first administrator with scripts/bootstrap_admin.py.'
echo 'Start the web and worker with the same server-only environment.'

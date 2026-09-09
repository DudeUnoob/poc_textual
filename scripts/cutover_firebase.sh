#!/usr/bin/env bash
# Run after Firestore + Auth + Storage are enabled in project poc-textual.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ "${WORKBENCH_BACKEND:-}" == "firebase" ]]; then
  echo "Already in firebase mode. Use migrate with a new migration-id only if intentional."
fi

echo "== Probe Firestore =="
python3 - <<'PY'
import os, firebase_admin
from firebase_admin import credentials, firestore, storage
cred=os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
for name in list(firebase_admin._apps):
    firebase_admin.delete_app(firebase_admin.get_app(None if name=="[DEFAULT]" else name))
firebase_admin.initialize_app(credentials.Certificate(cred), {
    "projectId": os.environ["FIREBASE_PROJECT_ID"],
    "storageBucket": os.environ["FIREBASE_STORAGE_BUCKET"],
})
db=firestore.client()
print("collections", [c.id for c in db.collections()] or "(empty)")
b=storage.bucket()
print("bucket", b.name, "exists", b.exists())
if not b.exists():
    raise SystemExit("Storage bucket missing. Create default bucket in Firebase Console → Storage.")
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
MIG_ID="cutover-${STAMP}"
echo "== Freeze local writes: stop the local workbench/worker before continuing =="
read -r -p "Local app stopped and this is the final migrate? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || exit 1

echo "== Migrate $MIG_ID =="
python3 scripts/migrate_to_firestore.py \
  --database data/workbench/workbench.db \
  --storage-root data/workbench \
  --migration-id "$MIG_ID" | tee "data/recovery/migrate-${MIG_ID}.json"

echo "== Flip backend =="
python3 - <<'PY'
from pathlib import Path
path=Path(".env")
text=path.read_text().splitlines()
out=[]
for line in text:
    if line.startswith("WORKBENCH_BACKEND="):
        out.append("WORKBENCH_BACKEND=firebase")
    else:
        out.append(line)
path.write_text("\n".join(out)+"\n")
print("WORKBENCH_BACKEND=firebase")
PY

echo
echo "Next: bootstrap first admin with a real Firebase Auth UID:"
echo "  WORKBENCH_BACKEND=firebase python3 scripts/bootstrap_admin.py --uid <UID> --email you@utexas.edu"
echo "Then start:"
echo "  uvicorn workbench.web:app --app-dir src --reload --port 8000"
echo "Open http://localhost:8000/login"

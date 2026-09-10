# Shared Supabase deployment

The application uses Supabase Auth, a private Storage bucket, and PostgreSQL RPC
transactions. Local SQLite remains an isolated development mode. There are no
ongoing dual writes. The shared project URL is
`https://ynsutdkbpzrgfetnrxax.supabase.co`.

## Provisioned project status — September 9, 2026

Project `ynsutdkbpzrgfetnrxax` is provisioned. The existing game tables were
preserved. Census migrations are recorded as `20260910012733`, `20260910012747`,
and `20260910014526`; local filenames match their applied versions. The older
`create_multiplayer_games` migration belongs to the existing project and is not
part of this repository. Do not reset the remote database from this repository.

The `census-media` bucket is private. Server RPC and actual file upload/download
checks passed; the publishable key was denied RPC and private-file access.
The Auth Before User Created hook is enabled. Approved domains are `utexas.edu`,
`eid.utexas.edu`, and `my.utexas.edu`. The exact address `to.baladev@gmail.com` is
an approved exception and its verified account is the first administrator.
Other Gmail addresses are not allowed. Keep the private `allowed_emails` and
`allowed_email_domains` tables synchronized with `WORKBENCH_ALLOWED_EMAILS` and
`WORKBENCH_ALLOWED_DOMAINS` in both web and worker environments.

Email confirmation is required; anonymous sign-ins are disabled. The built-in
Supabase sender successfully accepted the administrator invitation, and the user
completed verification. The default sender is only suitable for initial team-account
testing; [Supabase requires custom SMTP for production delivery to other users](https://supabase.com/docs/guides/auth/auth-smtp).

Site URL is `http://localhost:8000`, with exact `/login` and `/login?recovery=1`
callbacks for both localhost and 127.0.0.1 on port 8000. The ignored local `.env`
selects Supabase. Extraction remains disabled.

Import `census-initial-20260910` completed and verified record hashes and file bytes:
3 batches, 66 pages, 6 extraction runs, 27,300 candidates, 20 calibration bands,
1 export, and 810 review rows. All 70 file references map to 43 verified objects.
The original SQLite source is preserved. The SQLite snapshot, verified file copies,
inventory and COMPLETE manifest are in
`data/recovery/supabase-import-20260910T013756Z/` (ignored by Git).
Legacy candidates and decisions remain intact. Review rows preserve the local
page's selected run and latest human corrections; imported rows require review
under verified shared accounts. Local filenames are explicitly marked as unverified
source identities, not invented Ancestry identifiers. Prior exports remain archived
objects rather than being promoted to independently reviewed releases.

`render.yaml` prepares a Docker web service and one worker from this branch.
It has not been deployed or validated by Render's CLI. Both instance plans are
paid: review pricing before creating them. Set its prompted secret values and
HTTPS public origin, publish the branch, then deploy the Blueprint. Add the actual
HTTPS `/login` callbacks to Supabase and replace the site URL before sharing it.
An independently administered backup target and restore drill remain pending.

The security advisor reported intentionally policy-free private census tables,
which are accessible only through narrowly granted backend functions. Existing
game/cron/realtime policies and disabled leaked-password protection were also
reported; anonymous sign-in has since been disabled. See the
[advisor explanation](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
and [password protection configuration](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection).

## Configure and deploy

1. Apply all checked-in SQL migrations in filename order under `supabase/migrations/` using an
   operator PostgreSQL connection or Supabase SQL editor. Verify the private
   document schema and service-only RPC grants. Never grant browser roles access
   to `workbench_private` or transaction RPCs.
2. Create `census-media` as a **private** Storage bucket. Do not add permissive
   policies for `anon` or `authenticated`. All files are served by the authorized
   backend. Original and artifact keys are immutable; never use upsert or delete
   an object referenced by a record or release.
3. Enable email/password Auth, confirm-email, production SMTP, and approved
   redirect URLs. Configure the exact site origin and password-reset callback.
   Test verification/reset delivery and expired links. Exact university domain
   checks and member/admin authorization also run on the server. Enable
   `public.workbench_before_user_created` in Auth > Hooks > Before User Created
   to enforce the university domain at registration. This hook affects every
   application using the project: use a dedicated census project if other apps
   need unrestricted registration. Keep `workbench_private.allowed_email_domains`
   synchronized with `WORKBENCH_ALLOWED_DOMAINS`.
4. Configure `.env.example` values through the host secret manager. Set
   `WORKBENCH_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   `SUPABASE_SECRET_KEY`, `SUPABASE_STORAGE_BUCKET`, and the HTTPS public origin.
   Only the publishable key may reach a browser. Use the secret key solely in
   web/worker server environments. Never include backup credentials there.
5. Start FastAPI and one worker using the same configuration. Keep extraction
   disabled until model-call and page/upload limits are explicitly configured.
   The database worker fence controls the single active batch across instances.
6. Register and verify the first administrator's university email. Run
   `WORKBENCH_BACKEND=supabase python scripts/bootstrap_admin.py --uid UUID --email you@utexas.edu`
   using operator secrets. The script validates the live identity, rejects a
   mismatched/unverified email, and cannot replace an existing administrator.
7. Test two independent accounts editing the same row, stale revisions, retries,
   disabling users, recovery links, uploads, worker restart, and private downloads
   before opening team access. Enable database backups/PITR appropriate to the
   purchased Supabase plan; verify actual retention in the project settings.

## Import existing local data

Stop both local web and worker writes. Preserve a read-only copy of SQLite and
all referenced files. Do not migrate a changing SQLite/WAL database.

```bash
python scripts/migrate_to_supabase.py --database data/workbench/workbench.db \
  --storage-root data/workbench --migration-id pilot-001 --dry-run
```

Inspect the inventory, then run without `--dry-run` against an isolated target.
Verify counts, relationships, object checksums, decisions, and exports. The
script preserves legacy IDs deterministically and returns the original manifest
for an exact completed retry. A failed import stays failed and requires operator
inspection; it is not silently restarted. Trial imports do not change the source.

For final cutover, repeat against the production target while local writes remain
stopped. `scripts/cutover_supabase.sh` requires explicit operator environment and
`LOCAL_WRITES_STOPPED=true`; it does not silently rewrite `.env`. Set the backend
to `supabase` only after verifying the manifest. Do not point back to stale SQLite
after shared writes; reconcile those writes before any rollback.

## Independent recovery

Target: restore within one business day; at most 24 hours of loss after total
project loss. This is an operational target, not a guarantee demonstrated by unit
tests. Complete an isolated restore drill before launch and quarterly thereafter.

Run `python scripts/recovery_backup.py` every **six hours** as a dedicated operator
identity. Install a compatible `pg_dump` and configure `SUPABASE_DB_URL` as a
**direct or session** connection, never the transaction pooler. The consistent
custom-format dump covers `workbench_private`, `public`, and `auth`, preserving
UIDs and document versions. Dumps contain sensitive Auth state; treat them as
secrets. The separate identity inventory is operational metadata, while the
Auth schema in the dump is authoritative at the database snapshot.

Set `SUPABASE_RECOVERY_URL`, `SUPABASE_RECOVERY_SECRET_KEY`, and
`WORKBENCH_RECOVERY_BUCKET` to an **independently administered Supabase project**
with a private bucket. The tool rejects the same project, copies all source
Storage bytes separately, verifies SHA256 after upload, and writes `COMPLETE.json`
only after all components succeed. Storage objects must remain immutable and
must not be deleted during backup. Native database backups contain Storage
metadata, **not file bytes**.

Retain complete sets for **30 days** using a separate retention identity; do not
let the application runtime list or delete recovery sets. Alert the named owner
and operational recipient at 12 hours without a complete set, escalate at
24 hours. Scheduler, monitoring, retention, billing ownership and recovery-project
permissions must be configured and verified by the operator; these scripts do
not provision them.

Download a complete set to an isolated restore environment and run
`python scripts/recovery_restore.py --manifest COMPLETE.json` for the checklist.
Before restoring, verify the database and every object checksum. Keep web/worker
processes stopped, restore the custom dump with operator-reviewed `pg_restore`
commands compatible with the target Supabase version, preserve Auth schema
ownership, and recreate the private bucket through the Storage API before
uploading the recovered bytes. Do not blindly overwrite a running project.

Before any process starts: set `system/access.closed=true` in the restored
document store; clear row leases, increment worker fences, pause queued/running
jobs, clear application sessions, and remove restored `auth.sessions` and
`auth.refresh_tokens`. Rotate deployment signing/secret configuration as required.
Require a fresh password reset and review current memberships/revocations before
reopening. Verify the last-admin invariant and that old cookies cannot authenticate.
The restore tool produces a plan; it never wipes production or reopens access.

Documentation: [Python client](https://supabase.com/docs/reference/python/introduction),
[database backups](https://supabase.com/docs/guides/platform/backups), and
[Storage access control](https://supabase.com/docs/guides/storage/security/access-control).

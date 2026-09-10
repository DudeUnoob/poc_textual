# Shared census review architecture

Supabase replaces the earlier cloud-provider proposal: Auth manages verified
accounts and password recovery, PostgreSQL service-only RPCs commit review state
atomically, and private Storage holds immutable source scans and artifacts.
See [deployment and recovery](docs/DEPLOYMENT.md) for operator steps.

The workbook catalog drives all supported Bastrop census years and schedules.
Ancestry images remain transcription evidence; researcher-derived values and
unknown columns never become invented image answers. Geometry stays full-frame
until a specific form layout is validated. Missing physical row identities or
ambiguous ground-truth page ranges require explicit correction.

Members freely upload, extract, review and export drafts. Administrators oversee
progress, change access, resolve exceptions and authorize releases. There are
only member/admin roles; no invitation or assignment prerequisite. Authentication
requires a confirmed exact-domain university email. Server-derived actors are
recorded in append-only history; there is no self-promotion or last-admin removal.

Rows use tab-specific expiring leases, revision checks and idempotency receipts.
Conflicting edits preserve drafts. Database state, audit and receipts commit
atomically; Storage, Auth and model calls are external effects with explicit
pending/ready/failed recovery. Global worker fencing prevents stale results from
committing. A provider call can be billed twice after a crash even though only one
result is accepted. Review corrections remain separate from original model output.

Primary human review is required for releases, with independent 10% sampled QA.
Checking one's own work does not count as independent QA. Reopened work does not
alter an earlier immutable release. Selective auto-acceptance is deferred pending
held-out calibration and research-lead approval. The model migration still needs
the approved ten-page same-image benchmark before any accuracy claim.

The initial operating envelope is five reviewers and one active extraction batch.
Deploy only after two-account concurrency checks, access denial checks, a verified
trial migration, private-file tests and an isolated restore drill. Backup sets run
every six hours with 30-day independent retention; alert at 12 hours and escalate
at 24 hours. Recovery target is one business day and no more than 24 hours of loss
for total project loss. Live provisioning, backups and restore drills remain
operator tasks until executed and independently verified.

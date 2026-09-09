# Shared review workbench roadmap

Engineering review implementation, 2026-09-08. Branch: `cursor/generalize-census-forms`.
Status: local implementation and cloud adapters are under test; no production
Firebase deployment or migration has been performed.

## Requirements

Support every cleaned Bastrop County census workbook from 1850–1950, across
available districts and population/slave schedules. The cleaned XLSX worksheet
drives exact field mappings and export labels; authorized Ancestry scans remain
the only image-transcription evidence. Provide zoomable evidence, Gemini 3.8
Flash, authenticated reviewers and administrators, shared durable state,
protected media, recoverable accounts, attributable activity, and retry-safe
operations.

## What already exists

- FastAPI/Jinja review screens and JavaScript shortcuts: reuse, without a frontend rewrite.
- `src/workbench/db.py`: SQLAlchemy models for batches, pages, runs, candidates, append-only decisions, calibration, exports. Preserve their domain concepts and historical identifiers when migrating to Firestore.
- `src/workbench/services.py:274`: decision validation and audit creation; reuse business rules, replace persistence and caller-supplied reviewer identity.
- `src/workbench/services.py:303`: versioned exports; preserve formats, replace local directory allocation and mutable source selection with a reserved export and immutable snapshot.
- `src/workbench/worker.py:23`: checkpoint recovery; preserve page checkpoints, replace global recovery with expiring ownership leases.
- `src/workbench/templates/review_row.html:18`: row crop and full page evidence already available; add one reusable viewer for both row and field screens.
- `src/extract.py:73`: generation configuration needs migration, not only a model name replacement.
- `tests/test_workbench.py` and `tests/test_extract.py`: existing workflow and extraction regression coverage to extend.
- No TODOS.md or dedicated roadmap found. README and IMPLEMENTATION describe the local POC. Recent commits improved extraction speed and row review, so preserve those behaviors.

## Step 0: Scope challenge

This necessarily spans more than eight files. Reduce architectural churn by retaining Python, templates, extraction logic, and one existing worker program. Introduce only a Firebase access boundary and an authentication boundary; do not build a second application, custom identity system, or generic event platform. Firestore replaces SQLite as the shared deployment's source of truth; avoid ongoing dual writes. Separate delivery into independently verified phases while retaining all requested functionality.

Proposed phases:
1. Zoom/pan/fit/reset/fullscreen controls and the 3.8 configuration migration, with extraction comparisons.
2. Firebase Auth, server authorization, Firestore persistence, Cloud Storage, migration tooling, concurrent saves and worker recovery tested together before shared access.
3. Self-registration for verified allowed university domains, role management,
   optional assignments, activity/progress views, recovery flows, and shared
   rollout verification.

## Proposed architecture [Layer 1]

```text
Browser -- Firebase Auth --> identity / password reset
   |
   +-- authenticated requests --> existing FastAPI
   |                                |
   |                                +--> Firestore transactions
   |                                |    users, batches, pages, runs,
   |                                |    candidates, decisions, operations
   |                                +--> Cloud Storage private objects
   |
   +-- authorized live subscriptions --> assigned queue / run / progress

Existing Python worker --> claim lease --> Gemini --> stored artifact
                                          |
                         fenced transaction publishes page checkpoint
```

Use the web Firebase SDK for authentication and bounded live subscriptions; use Python firebase-admin on the existing backend. Do not add Node just to use the supplied Admin SDK example. Keep the supplied service-account JSON outside the repository/browser; configure a credential file path locally and attached workload credentials in hosting. No credential content belongs in this document. Firebase Analytics is not needed for operational reviewer history.

### Accounts and authorization

Firebase Auth handles registration, passwords, verification, and reset links;
the application never stores passwords. A verified address at an exact allowed
domain creates a server-side member record with role `member`; the client never
supplies a role. Check active membership and role for every protected operation,
including image, crop, export, import, queue, and admin routes.

Proposed roles: analyst uploads/starts assigned batches; reviewer resolves assigned work; admin invites/disables users, assigns roles and work, and inspects all activity. Decide whether analysts should also review. Bootstrap the first admin by an explicit trusted setup step; prohibit self-promotion and removal of the last active admin. Derive audit actor from verified UID, never a form field. Preserve legacy names as unattributed legacy labels rather than inventing UID ownership.

For server-rendered pages, exchange a recent Firebase ID token for a Secure, HttpOnly, SameSite session cookie, protect mutations against CSRF, and check revocation and current membership. Browser subscriptions also require Firebase identity and restrictive rules. Logout clears both sessions. Server Admin SDK access requires explicit authorization because it bypasses client rules.

Forgot password: generic reset acknowledgement, expiring provider link, invalid/expired link recovery. Forgotten email or lost mailbox: admin-assisted identity verification and audited account recovery; never reveal account lists publicly. Disable/revoke access without deleting historical attribution. Use HTTPS and managed encryption at rest; custom encryption/key management is not proposed.

### Shared saves [Layer 3]

```text
Edit -> saving -> server transaction -> saved acknowledgement
                    | duplicate operation -> original result
                    | stale revision -> conflict + preserve local draft
                    | denied -> access message
                    | network failure -> unsaved + retry same operation ID
```

Each logical command carries a stable operation ID, expected revision, and payload hash. Transaction reads membership, operation receipt, current revision and run identity before writing. Identical retry returns the prior result; reused ID with a different payload fails. Commit field/row changes, append-only audit entries, revision increment, progress counts, and operation result together. Row saves are all-or-nothing. A changed revision produces an explicit conflict, never silent last-writer-wins. Live updates must not overwrite unsaved edits. Offline drafts remain clearly unsaved; no offline final decisions in the proposed first release.

Apply the same contract to imports, manifest edits, extraction requests, cancel/resume/replacement, assignments, role changes, and export requests. External Auth changes cannot share a Firestore transaction: record intended administrative operation, perform it retry-safely, reconcile and mark completion; deny application access first when disabling a member.

### Files, extraction, and exports

Store originals and extraction/export artifacts in private Cloud Storage; Firestore contains metadata and object references, not image bytes or growing arrays of all history. Stable object keys include content hash or immutable operation/version identity. Validate file type, size and checksum server-side. Represent uploads as pending -> ready/failed and finalize only after object verification. Reconcile abandoned uploads; Firestore and Storage are not one atomic transaction.

Worker claims have owner, expiry, heartbeat and increasing fencing token. Only the current owner/token may publish results or progress. Recovery reclaims expired leases, not every running job. Never call Gemini or upload an object inside a retried Firestore transaction. A crash after Gemini answers may cause another billable call: guarantee one committed page result, not exactly-once provider execution. Replacement and cancellation invalidate stale worker commits.

Reserve exports transactionally and freeze their source run and review revisions before rendering. Concurrent editing must not yield a mixed-time export. Publish a download only when every artifact is complete and verified; retry the same export operation safely. Preserve original scan, raw extraction, review history, and supplied ground truth independently.

### Gemini migration

Use `gemini-3.8-flash` for new runs; preserve historical run model metadata and retain explicit configuration overrides. Remove deprecated sampling parameters and replace thinking_budget with supported thinking_level; 3.8 does not accept minimal. Compare low and medium on a bounded held-out set before selecting the default. Preserve structured JSON validation, retries, adaptive crops and raw handwritten values.

Calibration currently lacks model/prompt identity in its key. Version calibration by model, prompt, schema and extraction strategy; do not reuse 3.5 auto-accept thresholds automatically. Evaluate already-cleaned Bastrop pages with verified physical-page alignment, compare against 3.5 and Ancestry baseline where paired data exists, and report row omissions, field accuracy, latency and cost. Separate researcher race codes and ground-truth typos from image transcription errors.

### Data migration and rollout

Inventory local DB records and all referenced files; back up and trial-migrate without changing source. Use deterministic destination IDs and a mapping manifest so reruns cannot duplicate data. Verify counts, hashes, relationships, audit order, page identities, and export equivalence. Freeze local writes for final migration, switch one shared deployment to Firestore, and verify with two accounts. Keep source backup read-only. A rollback after new cloud writes must reconcile those writes; never simply point back to stale SQLite.

## Test diagram (proposed; not yet reviewed or executed)

```text
Login/invite/reset -> valid / expired / uninvited / disabled [auth + browser tests]
Protected operation -> allowed role / denied role / forged actor [route + rules tests]
Viewer -> zoom / pan / fit / reset / fullscreen / keyboard [browser tests]
Upload -> ready / duplicate / invalid / interrupted [storage integration tests]
Save -> committed / duplicate / conflict / offline / revoked [two-client tests]
Row save -> all fields commit / zero fields commit [transaction tests]
Live update -> clean form refresh / dirty form preserved [two-browser tests]
Worker -> claim / heartbeat / expiry / stale owner / cancel [race + crash tests]
Gemini -> valid JSON / invalid output / quota / timeout [unit + held-out eval]
Export -> frozen snapshot / duplicate / partial files / retry [integration tests]
Admin -> invite / change role / disable / recovery / last admin [auth tests]
Migration -> first run / repeat / interrupted / cutover [fixture + checksum tests]
```

## Failure modes and coverage to add

| Path | Realistic failure | Planned handling and test | Current assessment |
|---|---|---|---|
| Authentication | Expired or revoked session | Clear sign-in state; reject mutations; auth tests | Implemented; deployment test pending |
| Authorization | Member calls admin route directly | Server membership/role checks; denied-route tests | Implemented; deployment test pending |
| Viewer | Image fails or zoom loses evidence position | Retry message, bounded transforms, reset; browser tests | Controls implemented; browser test pending |
| Upload | Object succeeds but metadata commit fails | Pending state, reconcile by stable object key; interruption test | Adapter and tests implemented |
| Save | Two reviewers submit same revision | One commit; other sees conflict with draft retained; race test | Repository and lease tests implemented |
| Save retry | Server commits but response is lost | Same receipt returned; no duplicate audit/count; retry test | Repository tests implemented |
| Live sync | Another reviewer changes dirty form | Preserve draft and expose new revision; two-browser test | Integration absent |
| Worker | Dead owner returns after lease reassigned | Fenced commit rejected; stale-worker test | Repository/worker tests implemented |
| Gemini | Migration config rejected | Supported config tests and model smoke eval | Config tests implemented; live ten-page eval pending |
| Export | Concurrent request allocates same version | Transactional reservation and frozen source; concurrency test | Shared-use gap |
| Admin Auth change | Provider succeeds before receipt saved | Reconciliation of intended operation; replay test | Integration absent |
| Migration | Partial import is rerun | Stable IDs, checksums and resume manifest; rerun test | Tooling implemented; trial migration pending |

## Performance review candidates

Paginate queues and activity; subscribe only to current batch/run and assigned work. Avoid a global progress document updated by all workers. Keep decisions in separate documents and materialize bounded summaries rather than loading all history per row. Cache immutable crops by source checksum plus crop parameters and protect every download. Stream large objects, bound image decode size and worker parallelism. Define required Firestore indexes from actual queue queries and test with realistic batch sizes. Choose colocated compute/database/storage regions before provisioning. Confirm project billing, enabled services and resource locations during implementation setup; not yet inspected.

## NOT in scope

- Frontend framework rewrite: existing screens support the requirements.
- Custom password storage or cryptography: use managed identity and transport/storage protection.
- Public or non-university registration.
- Offline final review commits: conflict-safe online acknowledgement is the proposed initial contract.
- Exactly-once Gemini billing: unavailable without provider-level guarantees; bound retries and deduplicate committed results.
- Ethnicity inference or changes to ground-truth coding: separate research decisions.
- General analytics platform: admin activity comes from attributable application events.

## Remaining launch gates

1. Configure the Firebase project, IAM, billing owner, operators, recovery
   destination, and alerts without committing service-account credentials.
2. Run the ten-page Gemini 3.5 versus 3.8 low/medium comparison.
3. Trial-migrate a backup, verify counts/relationships/hashes/export
   equivalence, then run the final freeze-and-cutover procedure.
4. Pass five-reviewer concurrency/load tests and an isolated full restore drill.

Implementation tests do not authorize deployment. These operator-visible launch
gates remain mandatory.

## Sources

- https://ai.google.dev/gemini-api/docs/generate-content/latest-model
- https://firebase.google.com/docs/firestore/manage-data/transactions
- https://firebase.google.com/docs/auth/web/manage-users
- https://firebase.google.com/docs/reference/admin/python

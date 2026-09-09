"""Authoritative shared-state operations. External effects never run in transactions."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import secrets
import time


class CloudError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    uid: str
    email: str
    role: str = "member"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def key(value):
    if not isinstance(value, str) or not value or '/' in value or len(value) > 256:
        raise CloudError('Invalid identifier', 400)
    return value


def source_identity_id(source) -> str:
    return digest(source)


def page_version_id(source, checksum: str) -> str:
    return digest([source_identity_id(source), checksum])


class CloudRepository:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def get(self, collection, identifier):
        return self.store.get(f'{key(collection)}/{key(identifier)}')

    def list(self, collection):
        return self.store.list(key(collection))

    def _member(self, tx, actor, admin=False, allow_closed=False):
        policy = tx.get('system/access') or {}
        if policy.get('closed', False) and not allow_closed:
            raise CloudError('Portal is closed for recovery', 503)
        member = tx.get(f'members/{key(actor.uid)}')
        if not member or member.get('disabled') or member.get('email') != actor.email:
            raise CloudError('Access denied', 403)
        if admin and member.get('role') != 'admin':
            raise CloudError('Administrator required', 403)
        return member

    def ensure_member(self, uid, email):
        def action(tx):
            if (tx.get('system/access') or {}).get('closed', False):
                raise CloudError('Portal is closed for recovery', 503)
            path = f'members/{key(uid)}'
            current = tx.get(path)
            if current and current.get('disabled'):
                raise CloudError('Account disabled', 403)
            if not current:
                current = dict(uid=uid, email=email, role='member', disabled=False, created_at=self.clock())
                tx.set(path, current)
            elif current['email'] != email:
                # Auth verified the new email/domain; existing sessions carrying old email fail.
                current = dict(current, email=email)
                tx.set(path, current)
            return Principal(uid, email, current['role'])
        return self.store.atomic(action)

    def bootstrap_admin(self, uid, email):
        """Operator-only CLI entrypoint; never expose through an HTTP route."""
        def action(tx):
            registry = tx.get('system/admins') or {'uids': []}
            if registry['uids']:
                raise CloudError('First administrator already exists')
            tx.set(f'members/{key(uid)}', dict(uid=uid, email=email, role='admin', disabled=False, created_at=self.clock()))
            tx.set('system/admins', {'uids': [uid]})
        self.store.atomic(action)

    def _operation(self, actor, operation_id, payload, callback, admin=False, allow_closed=False):
        receipt_path = f'receipts/{digest([actor.uid, key(operation_id)])}'
        payload_hash = digest(payload)
        now = self.clock()
        def action(tx):
            self._member(tx, actor, admin, allow_closed=allow_closed)
            existing = tx.get(receipt_path)
            if existing:
                if existing['payload_hash'] != payload_hash:
                    raise CloudError('Operation ID reused with different data')
                return existing['result']
            result = callback(tx, now)
            receipt = dict(payload_hash=payload_hash, result=result, uid=actor.uid, at=now)
            tx.set(receipt_path, receipt)
            tx.set(f'audit/{receipt_path.split("/")[1]}', dict(uid=actor.uid, email=actor.email, action=payload, at=now))
            return result
        return self.store.atomic(action)

    def close_access(self, actor: Principal, closed: bool, operation_id: str):
        def action(tx, now):
            policy = dict(closed=bool(closed), updated_by=actor.uid, updated_at=now)
            tx.set('system/access', policy)
            return policy
        return self._operation(actor, operation_id, ['close_access', bool(closed)], action, admin=True, allow_closed=True)

    def takeover_lease(self, actor: Principal, row_id: str, session_id: str, operation_id: str):
        def action(tx, now):
            path = f'rows/{key(row_id)}'
            row = tx.get(path)
            if not row or row.get('finalized'):
                raise CloudError('Row unavailable')
            lease = dict(uid=actor.uid, session_id=key(session_id), token=secrets.token_urlsafe(24), expires_at=now + 120)
            tx.set(path, dict(row, lease=lease))
            return dict(lease, revision=row['revision'])
        return self._operation(actor, operation_id, ['takeover_lease', row_id, session_id], action, admin=True)

    def save_draft(
        self, actor, row_id, values, expected_revision, operation_id,
        lease_token, session_id, states=None,
    ):
        states = states or {
            field: 'blank' if value is None else 'value'
            for field, value in values.items()
        }
        payload_hash = digest([
            'save_draft', row_id, values, states, expected_revision,
            session_id, lease_token,
        ])
        receipt_path = f'receipts/{digest([actor.uid, key(operation_id)])}'
        draft_path = f'drafts/{digest([actor.uid, key(row_id)])}'
        def action(tx):
            self._member(tx, actor)
            existing = tx.get(receipt_path)
            if existing:
                if existing['payload_hash'] != payload_hash:
                    raise CloudError('Operation ID reused with different data')
                return existing['result']
            row = tx.get(f'rows/{key(row_id)}')
            self._lease(row, actor, session_id, lease_token, self.clock())
            if row['revision'] != expected_revision:
                raise CloudError('Row changed; preserve this draft and reload')
            if set(values) != set(row['original_values']) or set(states) != set(values):
                raise CloudError('Draft must match the complete row schema', 400)
            draft = {
                'row_id': row_id, 'uid': actor.uid, 'values': values,
                'reading_states': states, 'shared_revision': expected_revision,
                'updated_at': self.clock(),
            }
            tx.set(draft_path, draft)
            tx.set(receipt_path, {
                'payload_hash': payload_hash, 'result': draft,
                'uid': actor.uid, 'at': self.clock(),
            })
            tx.set(f'audit/{receipt_path.split("/")[1]}', {
                'uid': actor.uid, 'email': actor.email,
                'action': ['save_draft', row_id], 'at': self.clock(),
            })
            return draft
        return self.store.atomic(action)

    def ensure_budget(self, actor: Principal, total: int, operation_id: str):
        total = int(total)
        if total < 0:
            raise CloudError('Invalid budget total', 400)
        def action(tx, now):
            budget = tx.get('system/budget') or {}
            if 'remaining' not in budget:
                budget = dict(remaining=total, total=total, set_by=actor.uid, set_at=now)
                tx.set('system/budget', budget)
            return budget
        return self._operation(actor, operation_id, ['ensure_budget', total], action, admin=True)

    def consume_budget(self, actor_or_system: Principal | str | None, units: int):
        units = int(units)
        if units < 0:
            raise CloudError('Invalid budget consumption', 400)
        def action(tx):
            if isinstance(actor_or_system, Principal):
                self._member(tx, actor_or_system)
            elif (tx.get('system/access') or {}).get('closed', False):
                raise CloudError('Portal is closed for recovery', 503)
            budget = tx.get('system/budget') or {}
            remaining = int(budget.get('remaining') or 0)
            if remaining < units:
                raise CloudError('Extraction budget exhausted', 429)
            budget = dict(budget, remaining=remaining - units)
            tx.set('system/budget', budget)
            return budget
        return self.store.atomic(action)

    def create_batch(self, actor, batch_id, metadata, operation_id):
        key(batch_id)
        allowed = {
            'year', 'schedule_type', 'district', 'name', 'schema_version',
            'sheet_name', 'workbook_sha256', 'catalog_sha256',
        }
        if (
            set(metadata) - allowed
            or metadata.get('year') not in (1850,1860,1870,1880,1900,1910,1920,1930,1940,1950)
            or metadata.get('schedule_type') not in ('population', 'slave')
            or not str(metadata.get('district') or '').strip()
            or not str(metadata.get('name') or '').strip()
        ):
            raise CloudError('Invalid batch metadata', 400)
        def action(tx, now):
            if tx.get(f'batches/{batch_id}'):
                raise CloudError('Batch already exists')
            value = dict(metadata, id=batch_id, created_by=actor.uid, created_at=now, revision=0, page_ids=[], status='open')
            tx.set(f'batches/{batch_id}', value)
            return value
        return self._operation(actor, operation_id, ['create_batch', batch_id, metadata], action)

    def register_page(self, actor, batch_id, source, checksum, operation_id):
        """Source is stable Ancestry image identifier + year/schedule/district, not display label."""
        required = {'image_id', 'year', 'schedule_type', 'district'}
        if set(source) != required or not all(str(v).strip() for v in source.values()) or len(checksum) != 64:
            raise CloudError('Complete source identity and SHA256 required', 400)
        source_id = source_identity_id(source)
        page_id = page_version_id(source, checksum)
        def action(tx, now):
            batch = tx.get(f'batches/{key(batch_id)}')
            if not batch or batch['status'] != 'open':
                raise CloudError('Batch unavailable')
            if any(batch.get(k) != source[k] for k in ('year', 'schedule_type', 'district')):
                raise CloudError('Source does not match batch')
            page = tx.get(f'pages/{page_id}')
            if not page:
                page = dict(
                    id=page_id, source_id=source_id, source=source,
                    checksum=checksum, object_name=f'originals/{source_id}/{checksum}',
                    storage_status='pending', revision=0, row_ids=[],
                    complete=False, created_at=now,
                )
                tx.set(f'pages/{page_id}', page)
            registry = tx.get(f'sources/{source_id}') or {
                'id': source_id, 'identity': source, 'version_ids': [],
                'created_at': now,
            }
            version_ids = list(registry.get('version_ids') or [])
            if page_id not in version_ids:
                version_ids.append(page_id)
                registry = dict(
                    registry,
                    version_ids=version_ids,
                    reconciliation_required=len(version_ids) > 1,
                    updated_at=now,
                )
                tx.set(f'sources/{source_id}', registry)
            if page_id not in batch['page_ids']:
                tx.set(f'batches/{batch_id}', dict(batch, page_ids=batch['page_ids']+[page_id], revision=batch['revision']+1))
            return page
        return self._operation(actor, operation_id, ['register_page', batch_id, source, checksum], action)

    def mark_upload_ready(
        self, actor, page_id, generation, operation_id,
        content_type=None, size=None,
    ):
        def action(tx, now):
            page = tx.get(f'pages/{key(page_id)}')
            if not page:
                raise CloudError('Page missing', 404)
            if page.get('generation') and str(page['generation']) != str(generation):
                raise CloudError('Immutable object generation changed')
            page = dict(
                page,
                storage_status='ready',
                generation=str(generation),
                content_type=content_type or page.get('content_type') or 'image/jpeg',
                size=int(size) if size is not None else page.get('size'),
            )
            tx.set(f'pages/{page_id}', page)
            return page
        return self._operation(
            actor, operation_id,
            ['upload_ready', page_id, str(generation), content_type, size],
            action,
        )

    def acquire_lease(self, actor, row_id, session_id):
        now, token = self.clock(), secrets.token_urlsafe(24)
        def action(tx):
            self._member(tx, actor)
            path = f'rows/{key(row_id)}'
            row = tx.get(path)
            if not row or row.get('finalized'):
                raise CloudError('Row unavailable')
            lease = row.get('lease') or {}
            if lease.get('expires_at', 0) > now:
                if lease.get('uid') != actor.uid or lease.get('session_id') != session_id:
                    raise CloudError('Another browser is editing this row')
                lease = dict(lease, expires_at=now+120)
            else:
                lease = dict(uid=actor.uid, session_id=key(session_id), token=token, expires_at=now+120)
            tx.set(path, dict(row, lease=lease))
            return dict(lease, revision=row['revision'])
        return self.store.atomic(action)

    def renew_lease(self, actor, row_id, session_id, lease_token):
        def action(tx):
            self._member(tx, actor)
            row = tx.get(f'rows/{key(row_id)}')
            self._lease(row, actor, session_id, lease_token, self.clock())
            lease = dict(row['lease'], expires_at=self.clock()+120)
            tx.set(f'rows/{row_id}', dict(row, lease=lease))
            return lease
        return self.store.atomic(action)

    @staticmethod
    def _lease(row, actor, session_id, token, now):
        lease = (row or {}).get('lease') or {}
        if lease.get('uid') != actor.uid or lease.get('session_id') != session_id or lease.get('token') != token or lease.get('expires_at', 0) <= now:
            raise CloudError('Edit lease expired or replaced')

    def save_row(
        self, actor, row_id, values, expected_revision, operation_id,
        lease_token, session_id, complete=True, states=None,
    ):
        states = states or {
            field: 'blank' if value is None else 'value'
            for field, value in values.items()
        }
        valid_states = {'value', 'blank', 'unreadable', 'not_applicable', 'unresolved'}
        def action(tx, now):
            row = tx.get(f'rows/{key(row_id)}')
            self._lease(row, actor, session_id, lease_token, now)
            if row['revision'] != expected_revision or row.get('finalized'):
                raise CloudError('Row changed; reload before saving')
            if set(values) != set(row['original_values']) or any(not isinstance(v, (str, int, float, type(None))) or isinstance(v, bool) for v in values.values()):
                raise CloudError('Values must match the complete row schema', 400)
            if set(states) != set(values) or set(states.values()) - valid_states:
                raise CloudError('Reading states must match the complete row schema', 400)
            if complete and 'unresolved' in states.values():
                raise CloudError('Submitted review cannot contain unresolved fields', 400)
            row = dict(
                row, values=values, reading_states=states,
                revision=expected_revision+1, primary_complete=bool(complete),
                reviewed_by=actor.uid, reviewed_at=now, qa_complete=False,
                lease=None,
            )
            tx.set(f'rows/{row_id}', row)
            return row
        return self._operation(
            actor, operation_id,
            ['save_row', row_id, values, states, expected_revision, session_id, lease_token, complete],
            action,
        )

    def complete_qa(self, actor, row_id, expected_revision, operation_id):
        def action(tx, now):
            row = tx.get(f'rows/{key(row_id)}')
            if not row or row['revision'] != expected_revision or not row.get('primary_complete') or row.get('reviewed_by') == actor.uid or not row.get('qa_selected') or row.get('finalized'):
                raise CloudError('Independent QA is not available for this row')
            row = dict(row, qa_complete=True, qa_by=actor.uid, revision=expected_revision+1)
            tx.set(f'rows/{row_id}', row)
            return row
        return self._operation(actor, operation_id, ['qa', row_id, expected_revision], action)

    def mark_page_complete(self, actor, page_id, expected_revision, operation_id):
        def action(tx, now):
            page = tx.get(f'pages/{key(page_id)}')
            if (
                not page
                or page['revision'] != expected_revision
                or page['storage_status'] != 'ready'
                or not page['row_ids']
                or page.get('open_missing_rows', 0)
            ):
                raise CloudError('Page changed or has no extracted rows')
            page = dict(page, complete=True, revision=expected_revision+1, completeness_by=actor.uid)
            tx.set(f'pages/{page_id}', page)
            return page
        return self._operation(actor, operation_id, ['page_complete', page_id, expected_revision], action)

    def report_missing_row(self, actor, page_id, location, note, operation_id):
        if not str(location).strip():
            raise CloudError('Missing-row location is required', 400)
        def action(tx, now):
            page = tx.get(f'pages/{key(page_id)}')
            if not page:
                raise CloudError('Page missing', 404)
            issue_id = digest([page_id, operation_id])
            issue = {
                'id': issue_id, 'page_id': page_id,
                'location': str(location).strip(),
                'note': str(note or '').strip() or None,
                'status': 'open', 'reported_by': actor.uid, 'created_at': now,
            }
            tx.set(f'missing_rows/{issue_id}', issue)
            tx.set(
                f'pages/{page_id}',
                dict(
                    page,
                    open_missing_rows=page.get('open_missing_rows', 0) + 1,
                    complete=False,
                    revision=page.get('revision', 0) + 1,
                ),
            )
            return issue
        return self._operation(
            actor, operation_id,
            ['report_missing_row', page_id, location, note],
            action,
        )

    def resolve_missing_row(self, actor, issue_id, resolution, operation_id):
        if not str(resolution).strip():
            raise CloudError('Resolution is required', 400)
        def action(tx, now):
            issue = tx.get(f'missing_rows/{key(issue_id)}')
            if not issue or issue.get('status') != 'open':
                raise CloudError('Missing-row report is unavailable', 404)
            page = tx.get(f'pages/{issue["page_id"]}')
            if not page:
                raise CloudError('Page missing', 404)
            updated = dict(
                issue, status='resolved', resolution=str(resolution).strip(),
                resolved_by=actor.uid, resolved_at=now,
            )
            tx.set(f'missing_rows/{issue_id}', updated)
            tx.set(
                f'pages/{page["id"]}',
                dict(
                    page,
                    open_missing_rows=max(0, page.get('open_missing_rows', 0) - 1),
                    revision=page.get('revision', 0) + 1,
                ),
            )
            return updated
        return self._operation(
            actor, operation_id,
            ['resolve_missing_row', issue_id, resolution],
            action,
        )

    def add_row(self, actor, page_id, row_key, values, operation_id):
        if not values or not str(row_key).strip():
            raise CloudError('Row key and values are required', 400)
        row_id = digest([page_id, str(row_key)])
        def action(tx, now):
            page = tx.get(f'pages/{key(page_id)}')
            if not page or page.get('released'):
                raise CloudError('Page unavailable')
            if tx.get(f'rows/{row_id}'):
                raise CloudError('Row already exists')
            row = {
                'id': row_id, 'page_id': page_id, 'row_key': str(row_key),
                'original_values': dict(values), 'values': dict(values),
                'reading_states': {
                    field: 'blank' if value is None else 'value'
                    for field, value in values.items()
                },
                'revision': 0, 'primary_complete': False,
                'qa_selected': int(row_id[:8], 16) % 10 == 0,
                'qa_complete': False, 'finalized': False, 'lease': None,
                'source': 'manual', 'added_by': actor.uid, 'created_at': now,
            }
            tx.set(f'rows/{row_id}', row)
            tx.set(
                f'pages/{page_id}',
                dict(
                    page,
                    row_ids=[*(page.get('row_ids') or []), row_id],
                    revision=page.get('revision', 0) + 1,
                    complete=False,
                ),
            )
            return row
        return self._operation(
            actor, operation_id, ['add_row', page_id, row_key, values], action,
        )

    def exclude_row(self, actor, row_id, expected_revision, reason, operation_id):
        if not str(reason).strip():
            raise CloudError('Exclusion requires a reason', 400)
        def action(tx, now):
            row = tx.get(f'rows/{key(row_id)}')
            if not row or row.get('revision') != expected_revision or row.get('finalized'):
                raise CloudError('Row changed or unavailable')
            updated = dict(
                row, excluded=True, exclusion_reason=str(reason).strip(),
                excluded_by=actor.uid, excluded_at=now,
                revision=expected_revision + 1, lease=None,
            )
            tx.set(f'rows/{row_id}', updated)
            return updated
        return self._operation(
            actor, operation_id,
            ['exclude_row', row_id, expected_revision, reason],
            action,
        )

    def change_member(
        self, actor, uid, role, disabled, operation_id, assignments=None,
    ):
        if role not in ('member', 'admin'):
            raise CloudError('Invalid role', 400)
        normalized_assignments = (
            None
            if assignments is None
            else sorted({key(str(batch_id).strip()) for batch_id in assignments})
        )
        def action(tx, now):
            member = tx.get(f'members/{key(uid)}')
            registry = tx.get('system/admins') or {'uids': []}
            if not member:
                raise CloudError('Member not found', 404)
            for batch_id in normalized_assignments or []:
                if not tx.get(f'batches/{batch_id}'):
                    raise CloudError(f'Assignment batch not found: {batch_id}', 404)
            admins = set(registry['uids']) - {uid}
            if role == 'admin' and not disabled:
                admins.add(uid)
            if not admins:
                raise CloudError('Cannot remove the last administrator')
            member = dict(member, role=role, disabled=bool(disabled))
            if normalized_assignments is not None:
                member = dict(
                    member,
                    assignments=normalized_assignments,
                    assignments_updated_by=actor.uid,
                    assignments_updated_at=now,
                )
            tx.set(f'members/{uid}', member)
            tx.set('system/admins', {'uids': sorted(admins)})
            return member
        return self._operation(
            actor, operation_id,
            ['member', uid, role, disabled, normalized_assignments],
            action, admin=True,
        )

    def assign_member(self, actor, uid, batch_ids, operation_id):
        normalized = sorted({key(str(batch_id).strip()) for batch_id in batch_ids})
        def action(tx, now):
            member = tx.get(f'members/{key(uid)}')
            if not member:
                raise CloudError('Member not found', 404)
            for batch_id in normalized:
                if not tx.get(f'batches/{batch_id}'):
                    raise CloudError(f'Assignment batch not found: {batch_id}', 404)
            updated = dict(
                member, assignments=normalized,
                assignments_updated_by=actor.uid, assignments_updated_at=now,
            )
            tx.set(f'members/{uid}', updated)
            return updated
        return self._operation(
            actor, operation_id, ['assign_member', uid, normalized], action,
            admin=True,
        )

    def enqueue_job(self, actor, job_id, batch_id, config, operation_id):
        def action(tx, now):
            batch = tx.get(f'batches/{key(batch_id)}')
            if not batch or batch['status'] != 'open' or not batch['page_ids']:
                raise CloudError('Batch has no available pages')
            if tx.get(f'jobs/{key(job_id)}'):
                raise CloudError('Job already exists')
            budget = tx.get('system/budget') or {}
            if int(budget.get('remaining') or 0) <= 0:
                raise CloudError('Extraction budget is not configured or is exhausted', 429)
            if len(batch['page_ids']) > 200:
                raise CloudError('Split batches larger than 200 pages', 400)
            pages = [tx.get(f'pages/{p}') for p in batch['page_ids']]
            if any(p['storage_status'] != 'ready' for p in pages):
                raise CloudError('Uploads are not ready')
            # Existing reviewed pages cannot be silently overwritten by a rerun.
            if any(p['row_ids'] for p in pages):
                raise CloudError('Extraction already exists; create an explicit revision workflow before rerunning')
            value = dict(id=job_id, batch_id=batch_id, page_ids=list(batch['page_ids']), config=config,
                         source_generations={p['id']: p['generation'] for p in pages}, status='queued',
                         created_by=actor.uid, created_at=now, completed_pages=[], fence=0, attempts=0)
            tx.set(f'jobs/{job_id}', value)
            return value
        return self._operation(actor, operation_id, ['enqueue', job_id, batch_id, config], action)

    def claim_job(self, job_id, worker_id):
        def action(tx):
            now = self.clock()
            if (tx.get('system/access') or {}).get('closed', False):
                raise CloudError('Portal closed', 503)
            global_lease = tx.get('system/worker') or {}
            job = tx.get(f'jobs/{key(job_id)}')
            if global_lease.get('expires_at', 0) > now:
                raise CloudError('An extraction worker is already active')
            if not job or job['status'] not in ('queued', 'running'):
                raise CloudError('Job is not runnable')
            if job.get('attempts', 0) >= 3:
                raise CloudError('Job exhausted its retry limit')
            fence = global_lease.get('fence', 0)+1
            lease = dict(job_id=job_id, worker_id=key(worker_id), fence=fence, expires_at=now+120)
            tx.set('system/worker', lease)
            job = dict(job, status='running', worker_id=worker_id, fence=fence, attempts=job.get('attempts', 0)+1, updated_at=now)
            tx.set(f'jobs/{job_id}', job)
            return job
        return self.store.atomic(action)

    def _worker(self, tx, job_id, worker_id, fence):
        if (tx.get('system/access') or {}).get('closed', False):
            raise CloudError('Portal closed', 503)
        lease = tx.get('system/worker') or {}
        job = tx.get(f'jobs/{key(job_id)}')
        if not job or job['status'] != 'running' or lease.get('job_id') != job_id or lease.get('worker_id') != worker_id or lease.get('fence') != fence or lease.get('expires_at', 0) <= self.clock():
            raise CloudError('Worker lease expired or superseded')
        return job, lease

    def heartbeat_job(self, job_id, worker_id, fence):
        def action(tx):
            job, lease = self._worker(tx, job_id, worker_id, fence)
            tx.set('system/worker', dict(lease, expires_at=self.clock()+120))
            return job
        return self.store.atomic(action)

    def commit_page(self, job_id, worker_id, fence, page_id, records):
        if not records or len(records) > 100 or len({str(r['row_key']) for r in records}) != len(records):
            raise CloudError('Extraction must contain 1–100 uniquely identified rows', 400)
        # Leave room below Firestore document and transaction limits.
        if len(json.dumps(records).encode()) > 750_000:
            raise CloudError('Page extraction exceeds size limit', 400)
        result_hash = digest(records)
        def action(tx):
            job, _ = self._worker(tx, job_id, worker_id, fence)
            if page_id not in job['page_ids']:
                raise CloudError('Page is outside frozen job manifest')
            page = tx.get(f'pages/{key(page_id)}')
            prior = tx.get(f'jobs/{job_id}/results/{page_id}')
            if prior:
                if prior['hash'] != result_hash:
                    raise CloudError('Page already committed with different extraction')
                return page
            if page['row_ids'] or page['generation'] != job['source_generations'][page_id]:
                raise CloudError('Canonical page changed')
            row_ids = []
            for record in records:
                row_id = digest([page_id, str(record['row_key'])])
                row_ids.append(row_id)
                states = {
                    field: 'unresolved'
                    for field in record['values']
                }
                tx.set(f'rows/{row_id}', dict(id=row_id, page_id=page_id, row_key=str(record['row_key']),
                    original_values=record['values'], values=record['values'],
                    original_states=states, reading_states=states,
                    revision=0, primary_complete=False,
                    qa_selected=int(row_id[:8], 16) % 10 == 0, qa_complete=False, finalized=False, lease=None, job_id=job_id))
            page = dict(page, row_ids=row_ids, revision=page['revision']+1, complete=False)
            tx.set(f'pages/{page_id}', page)
            tx.set(f'jobs/{job_id}/results/{page_id}', dict(hash=result_hash, records=records, fence=fence, committed_at=self.clock()))
            tx.set(f'jobs/{job_id}', dict(job, completed_pages=job['completed_pages']+[page_id], updated_at=self.clock()))
            return page
        return self.store.atomic(action)

    def finish_job(self, job_id, worker_id, fence, error=None):
        def action(tx):
            job, lease = self._worker(tx, job_id, worker_id, fence)
            if error is None and set(job['completed_pages']) != set(job['page_ids']):
                raise CloudError('Job still has unfinished pages')
            job = dict(job, status='failed' if error else 'completed', error=str(error)[:1000] if error else None, updated_at=self.clock())
            tx.set(f'jobs/{job_id}', job)
            tx.set('system/worker', dict(lease, expires_at=0))
            return job
        return self.store.atomic(action)

    def finalize_release(self, actor, batch_id, release_id, operation_id):
        def action(tx, now):
            batch = tx.get(f'batches/{key(batch_id)}')
            if not batch or batch['status'] != 'open' or not batch['page_ids']:
                raise CloudError('Batch is not ready')
            if tx.get(f'releases/{key(release_id)}'):
                raise CloudError('Release identifier already exists')
            pages = [tx.get(f'pages/{p}') for p in batch['page_ids']]
            if len(pages) > 100 or any(not p.get('complete') for p in pages):
                raise CloudError('Release requires complete pages; maximum 100 pages per batch')
            total_size = 0
            for page in pages:
                rows = [tx.get(f'rows/{r}') for r in page['row_ids']]
                if not rows or any(
                    not r.get('excluded')
                    and (
                        not r.get('primary_complete')
                        or (r.get('qa_selected') and not r.get('qa_complete'))
                    )
                    for r in rows
                ):
                    raise CloudError('Primary review and independent sampled QA must be complete')
                snapshot = dict(page=page, rows=rows)
                total_size += len(json.dumps(snapshot).encode())
                if len(json.dumps(snapshot).encode()) > 750_000 or total_size > 5_000_000:
                    raise CloudError('Release is too large; split batch before releasing')
                tx.set(f'releases/{release_id}/pages/{page["id"]}', snapshot)
                tx.set(f'pages/{page["id"]}', dict(page, released=True, release_id=release_id))
            release = dict(id=release_id, batch_id=batch_id, page_ids=batch['page_ids'], created_by=actor.uid, created_at=now)
            tx.set(f'releases/{release_id}', release)
            tx.set(f'batches/{batch_id}', dict(batch, status='released', release_id=release_id, revision=batch['revision']+1))
            return release
        return self._operation(actor, operation_id, ['release', batch_id, release_id], action, admin=True)

    def reopen(self, actor, batch_id, reason, operation_id):
        if not reason.strip():
            raise CloudError('Reopening requires a reason', 400)
        def action(tx, now):
            batch = tx.get(f'batches/{key(batch_id)}')
            if not batch or batch['status'] != 'released':
                raise CloudError('Batch is not released')
            for page_id in batch['page_ids']:
                page = tx.get(f'pages/{page_id}')
                tx.set(f'pages/{page_id}', dict(page, released=False, complete=False, revision=page['revision']+1))
            batch = dict(batch, status='open', revision=batch['revision']+1)
            tx.set(f'batches/{batch_id}', batch)
            return batch
        return self._operation(actor, operation_id, ['reopen', batch_id, reason], action, admin=True)

from __future__ import annotations

import pytest

from workbench.cloud import CloudError, CloudRepository, MemoryStore, Principal
from workbench.cloud.repository import digest, page_version_id, source_identity_id

META = {
    'year': 1950,
    'schedule_type': 'population',
    'district': '11-2A',
    'name': 'Bastrop',
    'schema_version': 1,
}
SOURCE = {'image_id': 'anc-11-2A-1', 'year': 1950, 'schedule_type': 'population', 'district': '11-2A'}
CHECKSUM = 'a' * 64


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def repo(store: MemoryStore, clock: Clock) -> CloudRepository:
    return CloudRepository(store, clock=clock)


def bootstrap(repo: CloudRepository) -> tuple[Principal, Principal]:
    repo.bootstrap_admin('admin', 'admin@utexas.edu')
    repo.ensure_member('reviewer', 'reviewer@utexas.edu')
    admin = Principal('admin', 'admin@utexas.edu', 'admin')
    reviewer = Principal('reviewer', 'reviewer@utexas.edu', 'member')
    return admin, reviewer


def put_row(store: MemoryStore, row_id: str = 'row-1', **overrides) -> dict:
    row = dict(
        id=row_id, page_id='page-1', row_key='1',
        original_values={'Surname': 'Lewis'}, values={'Surname': 'Lewis'},
        revision=0, primary_complete=False, qa_selected=True, qa_complete=False,
        finalized=False, lease=None,
    )
    row.update(overrides)
    store.documents[f'rows/{row_id}'] = row
    return row


def records_for(page_id: str) -> tuple[list[dict], str, str]:
    qa_key = other_key = None
    index = 0
    while qa_key is None or other_key is None:
        candidate = str(index)
        selected = int(digest([page_id, candidate])[:8], 16) % 10 == 0
        if selected:
            qa_key = qa_key or candidate
        else:
            other_key = other_key or candidate
        index += 1
    records = [
        {'row_key': qa_key, 'values': {'Surname': 'Lewis'}},
        {'row_key': other_key, 'values': {'Surname': 'Craney'}},
    ]
    return records, qa_key, other_key


def row_id_for(page_id: str, row_key: str) -> str:
    return digest([page_id, str(row_key)])


def queued_job(repo: CloudRepository, admin: Principal, batch_id: str = 'batch-1', job_id: str = 'job-1'):
    repo.ensure_budget(admin, 20, f'budget-{job_id}')
    repo.create_batch(admin, batch_id, META, f'create-{batch_id}')
    page = repo.register_page(admin, batch_id, SOURCE, CHECKSUM, f'register-{batch_id}')
    repo.mark_upload_ready(admin, page['id'], 'gen-1', f'ready-{batch_id}')
    job = repo.enqueue_job(admin, job_id, batch_id, {'model': 'test'}, f'enqueue-{job_id}')
    return page, job


def test_bootstrap_first_admin_and_reject_second(repo: CloudRepository):
    repo.bootstrap_admin('admin', 'admin@utexas.edu')
    member = repo.get('members', 'admin')
    assert member['role'] == 'admin'
    assert repo.get('system', 'admins')['uids'] == ['admin']
    with pytest.raises(CloudError, match='already exists'):
        repo.bootstrap_admin('other', 'other@utexas.edu')


def test_ensure_member_creates_and_disabled_denied(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    created = repo.ensure_member('analyst', 'analyst@utexas.edu')
    assert created.role == 'member'
    assert repo.get('members', 'analyst')['email'] == 'analyst@utexas.edu'
    repo.change_member(admin, 'analyst', 'member', True, 'disable-analyst')
    with pytest.raises(CloudError, match='Account disabled'):
        repo.ensure_member('analyst', 'analyst@utexas.edu')
    with pytest.raises(CloudError, match='Access denied'):
        repo.create_batch(Principal('analyst', 'analyst@utexas.edu', 'member'), 'batch-x', META, 'nope')


def test_ensure_member_updates_email(repo: CloudRepository):
    bootstrap(repo)
    repo.ensure_member('analyst', 'old@utexas.edu')
    updated = repo.ensure_member('analyst', 'new@utexas.edu')
    assert updated.email == 'new@utexas.edu'
    assert repo.get('members', 'analyst')['email'] == 'new@utexas.edu'
    with pytest.raises(CloudError, match='Access denied'):
        repo.create_batch(Principal('analyst', 'old@utexas.edu', 'member'), 'batch-x', META, 'stale-email')


def test_last_admin_protection_on_sequential_demotions(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    repo.ensure_member('second', 'second@utexas.edu')
    repo.change_member(admin, 'second', 'admin', False, 'promote-second')
    repo.change_member(admin, 'second', 'member', False, 'demote-second')
    with pytest.raises(CloudError, match='last administrator'):
        repo.change_member(admin, 'admin', 'member', False, 'demote-self')
    with pytest.raises(CloudError, match='last administrator'):
        repo.change_member(admin, 'admin', 'admin', True, 'disable-self')
    assert repo.get('system', 'admins')['uids'] == ['admin']


def test_member_role_and_assignments_commit_together(repo: CloudRepository):
    admin, reviewer = bootstrap(repo)
    repo.create_batch(admin, 'batch-a', META, 'create-a')
    updated = repo.change_member(
        admin, reviewer.uid, 'member', False, 'member-update-1',
        assignments=['batch-a'],
    )
    assert updated['assignments'] == ['batch-a']
    with pytest.raises(CloudError, match='Assignment batch not found'):
        repo.change_member(
            admin, reviewer.uid, 'member', False, 'member-update-2',
            assignments=['missing-batch'],
        )
    assert repo.get('members', reviewer.uid)['assignments'] == ['batch-a']


def test_register_page_same_source_identity_is_stable(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    repo.create_batch(admin, 'batch-a', META, 'create-a')
    repo.create_batch(admin, 'batch-b', META, 'create-b')
    first = repo.register_page(admin, 'batch-a', SOURCE, CHECKSUM, 'reg-a')
    second = repo.register_page(admin, 'batch-b', SOURCE, CHECKSUM, 'reg-b')
    assert first['id'] == second['id'] == page_version_id(SOURCE, CHECKSUM)
    assert first['id'] in repo.get('batches', 'batch-a')['page_ids']
    assert second['id'] in repo.get('batches', 'batch-b')['page_ids']


def test_register_page_preserves_distinct_scan_versions(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    repo.create_batch(admin, 'batch-a', META, 'create-a')
    first = repo.register_page(admin, 'batch-a', SOURCE, CHECKSUM, 'reg-a')
    second = repo.register_page(admin, 'batch-a', SOURCE, 'b' * 64, 'reg-version')
    assert first['id'] != second['id']
    registry = repo.get('sources', source_identity_id(SOURCE))
    assert registry['version_ids'] == [first['id'], second['id']]
    assert registry['reconciliation_required'] is True


def test_acquire_renew_and_expired_lease(repo: CloudRepository, store: MemoryStore, clock: Clock):
    admin, reviewer = bootstrap(repo)
    put_row(store)
    first = repo.acquire_lease(admin, 'row-1', 'session-a')
    with pytest.raises(CloudError, match='Another browser'):
        repo.acquire_lease(reviewer, 'row-1', 'session-b')
    renewed = repo.renew_lease(admin, 'row-1', 'session-a', first['token'])
    assert renewed['expires_at'] == clock.now + 120
    same_session = repo.acquire_lease(admin, 'row-1', 'session-a')
    assert same_session['token'] == first['token']
    clock.advance(121)
    taken = repo.acquire_lease(reviewer, 'row-1', 'session-b')
    assert taken['uid'] == reviewer.uid
    assert taken['token'] != first['token']


def test_save_row_commit_retry_and_conflicts(repo: CloudRepository, store: MemoryStore):
    admin, _ = bootstrap(repo)
    put_row(store)
    lease = repo.acquire_lease(admin, 'row-1', 'session-a')
    values = {'Surname': 'Lewis'}
    committed = repo.save_row(
        admin, 'row-1', values, 0, 'save-1', lease['token'], 'session-a', complete=True,
    )
    assert committed['primary_complete'] is True
    assert committed['revision'] == 1
    retried = repo.save_row(
        admin, 'row-1', values, 0, 'save-1', lease['token'], 'session-a', complete=True,
    )
    assert retried == committed
    with pytest.raises(CloudError, match='different data'):
        repo.save_row(
            admin, 'row-1', {'Surname': 'Luis'}, 0, 'save-1', lease['token'], 'session-a', complete=True,
        )
    lease = repo.acquire_lease(admin, 'row-1', 'session-a')
    with pytest.raises(CloudError, match='reload before saving'):
        repo.save_row(
            admin, 'row-1', values, 0, 'save-stale', lease['token'], 'session-a', complete=True,
        )
    draft_lease = repo.acquire_lease(admin, 'row-1', 'session-a')
    draft = repo.save_draft(
        admin, 'row-1', {'Surname': 'Lewis H'}, 1, 'save-draft',
        draft_lease['token'], 'session-a',
    )
    assert draft['values']['Surname'] == 'Lewis H'
    assert repo.get('rows', 'row-1')['values']['Surname'] == 'Lewis'
    stored = store.get(f"drafts/{digest([admin.uid, 'row-1'])}")
    assert stored['uid'] == admin.uid


def test_submitted_row_rejects_unresolved_but_accepts_unreadable(
    repo: CloudRepository, store: MemoryStore,
):
    admin, _ = bootstrap(repo)
    put_row(store)
    lease = repo.acquire_lease(admin, 'row-1', 'session-a')
    with pytest.raises(CloudError, match='unresolved'):
        repo.save_row(
            admin, 'row-1', {'Surname': 'Lewis'}, 0, 'state-save-1',
            lease['token'], 'session-a', complete=True,
            states={'Surname': 'unresolved'},
        )
    saved = repo.save_row(
        admin, 'row-1', {'Surname': None}, 0, 'state-save-2',
        lease['token'], 'session-a', complete=True,
        states={'Surname': 'unreadable'},
    )
    assert saved['reading_states'] == {'Surname': 'unreadable'}


def test_complete_qa_rejects_same_reviewer(repo: CloudRepository, store: MemoryStore):
    admin, reviewer = bootstrap(repo)
    put_row(store, reviewed_by='admin', primary_complete=True, revision=1)
    with pytest.raises(CloudError, match='Independent QA'):
        repo.complete_qa(admin, 'row-1', 1, 'qa-self')
    done = repo.complete_qa(reviewer, 'row-1', 1, 'qa-ok')
    assert done['qa_complete'] is True
    assert done['qa_by'] == reviewer.uid


def test_claim_job_blocks_second_worker_and_stale_fence(repo: CloudRepository, clock: Clock):
    admin, _ = bootstrap(repo)
    page, _ = queued_job(repo, admin)
    claimed = repo.claim_job('job-1', 'worker-a')
    with pytest.raises(CloudError, match='already active'):
        repo.claim_job('job-1', 'worker-b')
    stale_fence = claimed['fence']
    records, _, _ = records_for(page['id'])
    clock.advance(121)
    successor = repo.claim_job('job-1', 'worker-b')
    assert successor['fence'] != stale_fence
    with pytest.raises(CloudError, match='expired or superseded'):
        repo.commit_page('job-1', 'worker-a', stale_fence, page['id'], records)


def test_commit_page_idempotent_hash_and_conflict(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    page, _ = queued_job(repo, admin)
    job = repo.claim_job('job-1', 'worker-a')
    records, _, _ = records_for(page['id'])
    first = repo.commit_page('job-1', 'worker-a', job['fence'], page['id'], records)
    second = repo.commit_page('job-1', 'worker-a', job['fence'], page['id'], records)
    assert first['row_ids'] == second['row_ids']
    with pytest.raises(CloudError, match='different extraction'):
        repo.commit_page(
            'job-1', 'worker-a', job['fence'], page['id'],
            [{'row_key': '9', 'values': {'Surname': 'Other'}}],
        )


def test_finalize_release_requires_qa_and_reopen_keeps_snapshot(repo: CloudRepository, store: MemoryStore):
    admin, reviewer = bootstrap(repo)
    page, _ = queued_job(repo, admin)
    job = repo.claim_job('job-1', 'worker-a')
    records, qa_key, other_key = records_for(page['id'])
    page = repo.commit_page('job-1', 'worker-a', job['fence'], page['id'], records)
    page = repo.mark_page_complete(admin, page['id'], page['revision'], 'page-complete')
    with pytest.raises(CloudError, match='Primary review and independent sampled QA'):
        repo.finalize_release(admin, 'batch-1', 'rel-1', 'release-early')
    for key, operation in ((qa_key, 'primary-qa'), (other_key, 'primary-other')):
        rid = row_id_for(page['id'], key)
        row = repo.get('rows', rid)
        lease = repo.acquire_lease(admin, rid, f'sess-{key}')
        repo.save_row(admin, rid, row['original_values'], row['revision'], operation, lease['token'], f'sess-{key}')
    with pytest.raises(CloudError, match='Primary review and independent sampled QA'):
        repo.finalize_release(admin, 'batch-1', 'rel-1', 'release-no-qa')
    qa_row = repo.get('rows', row_id_for(page['id'], qa_key))
    repo.complete_qa(reviewer, qa_row['id'], qa_row['revision'], 'qa-row')
    release = repo.finalize_release(admin, 'batch-1', 'rel-1', 'release-ok')
    snapshot = store.get(f'releases/rel-1/pages/{page["id"]}')
    assert snapshot is not None
    page_before = repo.get('pages', page['id'])
    reopened = repo.reopen(admin, 'batch-1', 'Needs another pass', 'reopen-1')
    assert reopened['status'] == 'open'
    assert reopened['revision'] == repo.get('batches', 'batch-1')['revision']
    assert store.get(f'releases/rel-1') == release
    assert store.get(f'releases/rel-1/pages/{page["id"]}') == snapshot
    assert repo.get('pages', page['id'])['revision'] == page_before['revision'] + 1
    assert repo.get('pages', page['id'])['released'] is False


def test_close_access_blocks_membership_and_commands(repo: CloudRepository):
    admin, reviewer = bootstrap(repo)
    with pytest.raises(CloudError, match='Administrator required'):
        repo.close_access(reviewer, True, 'close-denied')
    repo.close_access(admin, True, 'close-1')
    assert repo.get('system', 'access')['closed'] is True
    with pytest.raises(CloudError, match='closed'):
        repo.ensure_member('fresh', 'fresh@utexas.edu')
    with pytest.raises(CloudError, match='closed'):
        repo.create_batch(admin, 'batch-closed', META, 'create-closed')
    with pytest.raises(CloudError, match='closed'):
        repo.consume_budget(admin, 0)
    repo.close_access(admin, False, 'open-1')
    repo.ensure_member('fresh', 'fresh@utexas.edu')


def test_takeover_lease_is_admin_audited(repo: CloudRepository, store: MemoryStore):
    admin, reviewer = bootstrap(repo)
    put_row(store)
    original = repo.acquire_lease(reviewer, 'row-1', 'reviewer-session')
    with pytest.raises(CloudError, match='Administrator required'):
        repo.takeover_lease(reviewer, 'row-1', 'reviewer-session', 'takeover-denied')
    taken = repo.takeover_lease(admin, 'row-1', 'admin-session', 'takeover-1')
    assert taken['uid'] == admin.uid
    assert taken['session_id'] == 'admin-session'
    with pytest.raises(CloudError, match='expired or replaced'):
        repo.renew_lease(reviewer, 'row-1', 'reviewer-session', original['token'])
    again = repo.takeover_lease(admin, 'row-1', 'admin-session', 'takeover-1')
    assert again == taken


def test_missing_row_addition_resolution_and_exclusion_are_audited(
    repo: CloudRepository, store: MemoryStore,
):
    admin, _ = bootstrap(repo)
    store.documents["pages/page-1"] = {
        "id": "page-1", "storage_status": "ready", "revision": 0,
        "row_ids": [], "complete": False,
    }
    issue = repo.report_missing_row(
        admin, "page-1", "between lines 3 and 4", "faint entry", "missing-1",
    )
    page = repo.get("pages", "page-1")
    assert page["open_missing_rows"] == 1
    added = repo.add_row(
        admin, "page-1", "3a", {"Surname": "Lewis"}, "add-row-1",
    )
    assert added["source"] == "manual"
    resolved = repo.resolve_missing_row(
        admin, issue["id"], "added as row 3a", "resolve-missing-1",
    )
    assert resolved["status"] == "resolved"
    excluded = repo.exclude_row(
        admin, added["id"], 0, "duplicate enumerator entry", "exclude-row-1",
    )
    assert excluded["excluded"] is True
    assert repo.get("pages", "page-1")["open_missing_rows"] == 0


def test_ensure_and_consume_budget(repo: CloudRepository):
    admin, _ = bootstrap(repo)
    created = repo.ensure_budget(admin, 5, 'budget-1')
    assert created['remaining'] == 5
    assert repo.consume_budget(admin, 2)['remaining'] == 3
    assert repo.consume_budget('system', 3)['remaining'] == 0
    with pytest.raises(CloudError, match='budget'):
        repo.consume_budget('system', 1)
    repo.ensure_budget(admin, 99, 'budget-2')
    assert repo.get('system', 'budget')['remaining'] == 0

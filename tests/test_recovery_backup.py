from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = spec_from_file_location('recovery_backup', ROOT / 'scripts' / 'recovery_backup.py')
assert _SPEC is not None and _SPEC.loader is not None
recovery_backup = module_from_spec(_SPEC)
_SPEC.loader.exec_module(recovery_backup)

FIXED_NOW = datetime(2026, 9, 8, 21, 0, 30, tzinfo=timezone.utc)
SNAPSHOT = (FIXED_NOW - timedelta(minutes=1)).replace(second=0, microsecond=0)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


def _blob_store() -> tuple[MagicMock, dict[str, MagicMock]]:
    created: dict[str, MagicMock] = {}

    def blob(name: str) -> MagicMock:
        if name not in created:
            item = MagicMock()
            item.name = name
            stored: dict[str, bytes] = {}

            def upload(data, **_kwargs):
                stored['data'] = data if isinstance(data, bytes) else data.encode()

            item.upload_from_string.side_effect = upload
            item.download_as_bytes.side_effect = lambda: stored['data']
            created[name] = item
        return created[name]

    destination = MagicMock()
    destination.project_number = 2
    destination.blob.side_effect = blob
    return destination, created


def _source_blob(*, crc32c: str = 'abc', size: int = 4) -> MagicMock:
    blob = MagicMock()
    blob.name = 'originals/page.jpg'
    blob.generation = 11
    blob.crc32c = crc32c
    blob.size = size
    return blob


def _stack(*, checksum_ok: bool = True):
    source = MagicMock()
    source.project_number = 1
    original = _source_blob()
    source.list_blobs.return_value = [original]
    copied = MagicMock()
    copied.crc32c = original.crc32c if checksum_ok else 'mismatch'
    copied.size = original.size
    copied.generation = 99
    source.copy_blob.return_value = copied
    destination, blobs = _blob_store()

    def client(project=None):
        handle = MagicMock()
        handle.get_bucket.return_value = source if project == 'src-proj' else destination
        return handle

    export = MagicMock()
    export.output_uri_prefix = 'gs://dst-bucket/recovery/x/firestore'
    admin_client = MagicMock()
    admin_client.export_documents.return_value.result.return_value = export
    user = MagicMock(uid='u1', email='admin@utexas.edu', email_verified=True, disabled=False)
    return {
        'client': client,
        'admin_client': admin_client,
        'export_documents': admin_client.export_documents,
        'blobs': blobs,
        'users': [user],
    }


def _install_cloud_stubs(monkeypatch: pytest.MonkeyPatch, stack: dict) -> None:
    firebase_admin = ModuleType('firebase_admin')
    firebase_admin.initialize_app = MagicMock()
    auth = ModuleType('firebase_admin.auth')
    auth.list_users = MagicMock()
    auth.list_users.return_value.iterate_all.return_value = stack['users']
    firebase_admin.auth = auth
    storage = ModuleType('google.cloud.storage')
    storage.Client = MagicMock(side_effect=stack['client'])
    admin_v1 = ModuleType('google.cloud.firestore_admin_v1')
    admin_v1.FirestoreAdminClient = MagicMock(return_value=stack['admin_client'])
    for name, module in {
        'firebase_admin': firebase_admin,
        'firebase_admin.auth': auth,
        'google.cloud.storage': storage,
        'google.cloud.firestore_admin_v1': admin_v1,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(recovery_backup, 'datetime', _FrozenDateTime)


def test_recovery_requires_different_project_and_bucket():
    with pytest.raises(ValueError, match='different project and bucket'):
        recovery_backup.create_backup('same', 'src', 'same', 'dst')
    with pytest.raises(ValueError, match='different project and bucket'):
        recovery_backup.create_backup('src-proj', 'shared', 'dst-proj', 'shared')


def test_complete_marker_not_written_before_checksum_verification(monkeypatch: pytest.MonkeyPatch):
    stack = _stack(checksum_ok=False)
    _install_cloud_stubs(monkeypatch, stack)
    with pytest.raises(RuntimeError, match='Backup verification failed'):
        recovery_backup.create_backup('src-proj', 'src-bucket', 'dst-proj', 'dst-bucket')
    complete = [name for name in stack['blobs'] if name.endswith('COMPLETE.json')]
    assert complete == []
    assert all(not blob.upload_from_string.called for blob in stack['blobs'].values())


def test_snapshot_time_is_passed_to_export_documents(monkeypatch: pytest.MonkeyPatch):
    stack = _stack(checksum_ok=True)
    _install_cloud_stubs(monkeypatch, stack)
    recovery_backup.create_backup('src-proj', 'src-bucket', 'dst-proj', 'dst-bucket')
    request = stack['export_documents'].call_args.kwargs['request']
    assert request['snapshot_time'] == SNAPSHOT
    complete = [name for name, blob in stack['blobs'].items() if name.endswith('COMPLETE.json')]
    assert len(complete) == 1
    assert stack['blobs'][complete[0]].upload_from_string.called

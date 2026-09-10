"""Recovery checks exercise Supabase copies and real manifest integrity rules."""
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SPEC = spec_from_file_location('recovery_backup', Path(__file__).parents[1] / 'scripts/recovery_backup.py')
backup = module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class Bucket:
    def __init__(self, objects=None, corrupt=False):
        self.objects = dict(objects or {})
        self.corrupt = corrupt

    def upload(self, key, payload, options):
        assert options['upsert'] == 'false'
        assert key not in self.objects
        self.objects[key] = payload

    def download(self, key):
        return b'corrupted' if self.corrupt else self.objects[key]

    def list(self, prefix, options):
        return [{'name': 'page.jpg', 'id': 'object-id'}] if not prefix else []


def run_backup(monkeypatch, destination):
    source = MagicMock()
    source.storage.from_.return_value = Bucket({'page.jpg': b'scan'})
    source.auth.admin.list_users.return_value = [SimpleNamespace(
        id='u1', email='admin@utexas.edu', email_confirmed_at='2026-09-09')]
    recovery = MagicMock()
    recovery.storage.from_.return_value = destination
    monkeypatch.setattr(backup, 'database_dump', lambda url, output: output.write_bytes(b'consistent database dump'))
    return backup.create_backup('https://source.supabase.co', 'census-media',
        'https://recovery.supabase.co', 'census-media', source_client=source,
        recovery_client=recovery, database_url='postgresql://example')


@pytest.mark.parametrize('target', ['https://source.supabase.co', 'invalid'])
def test_recovery_requires_different_project(target):
    with pytest.raises(ValueError, match='different Supabase project'):
        backup.create_backup('https://source.supabase.co', 'source', target, 'destination')


def test_complete_marker_not_written_before_checksum_verification(monkeypatch):
    destination = Bucket(corrupt=True)
    with pytest.raises(RuntimeError, match='Backup verification failed'):
        run_backup(monkeypatch, destination)
    assert not any(key.endswith('COMPLETE.json') for key in destination.objects)


def test_complete_manifest_matches_copied_database_objects_and_identities(monkeypatch):
    destination = Bucket()
    result = run_backup(monkeypatch, destination)
    manifest = json.loads(destination.objects[result['prefix'] + '/COMPLETE.json'])
    assert result['objects'] == result['identities'] == 1
    assert manifest['provider'] == 'supabase'
    assert manifest['restore_access'] == 'closed'
    assert manifest['password_policy'] == 'reset-required'
    for key, checksum in [('database_export', 'database_sha256'), ('identities', 'identities_sha256')]:
        assert backup.sha256(destination.objects[manifest[key]]) == manifest[checksum]
    item = manifest['objects'][0]
    assert destination.objects[item['target']] == b'scan'
    assert item['sha256'] == backup.sha256(b'scan')
    assert item['size'] == 4


def test_database_dump_keeps_credentials_out_of_arguments(monkeypatch, tmp_path):
    output = tmp_path / 'database.dump'
    observed = {}
    def run(args, **kwargs):
        observed.update(args=args, **kwargs)
        output.write_bytes(b'dump')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(backup.subprocess, 'run', run)
    url = 'postgresql://operator:test-password@db.example/postgres'
    backup.database_dump(url, output)
    assert url not in ' '.join(observed['args'])
    assert observed['env']['PGDATABASE'] == url
    assert {'--schema=workbench_private', '--schema=public', '--schema=auth'} <= set(observed['args'])


def test_storage_inventory_recurses_and_paginates():
    bucket = MagicMock()
    def listing(prefix, options):
        if prefix == 'originals':
            return [{'name': 'scan.jpg', 'id': 'nested'}]
        if options['offset'] == 0:
            return [{'name': 'originals', 'id': None}] + [
                {'name': str(i), 'id': str(i)} for i in range(999)]
        return [{'name': 'last.jpg', 'id': 'last'}]
    bucket.list.side_effect = listing
    objects = list(backup.storage_objects(bucket))
    assert len(objects) == 1001
    assert objects[0] == 'originals/scan.jpg'
    assert objects[-1] == 'last.jpg'

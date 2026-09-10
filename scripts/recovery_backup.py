"""Verified independent Supabase recovery set; run every six hours as operator.

Requires pg_dump and a direct/session PostgreSQL URL. The database dump contains
Auth identity state and private application documents in one consistent snapshot.
Storage bytes are separately copied because PostgreSQL backups exclude them.
Never give the web service recovery-project credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_objects(bucket, prefix=''):
    """Paginate every directory; folders have no object id."""
    offset = 0
    while True:
        entries = bucket.list(prefix, {'limit': 1000, 'offset': offset, 'sortBy': {'column': 'name', 'order': 'asc'}})
        for entry in entries:
            key = '/'.join(part for part in (prefix, entry['name']) if part)
            if entry.get('id') is None:
                yield from storage_objects(bucket, key)
            else:
                yield key
        if len(entries) < 1000:
            break
        offset += len(entries)


def verified_upload(bucket, key: str, payload: bytes) -> str:
    digest = sha256(payload)
    bucket.upload(key, payload, {'upsert': 'false', 'content-type': 'application/octet-stream'})
    if sha256(bucket.download(key)) != digest:
        raise RuntimeError(f'Backup verification failed for {key}')
    return digest


def database_dump(database_url: str, output: Path) -> None:
    # Keep credentials out of process arguments and command output. pg_dump uses
    # a transactionally consistent snapshot across all selected schemas.
    environment = dict(os.environ, PGDATABASE=database_url)
    result = subprocess.run([
        'pg_dump', '--format=custom', '--no-owner', '--no-acl',
        '--schema=workbench_private', '--schema=public', '--schema=auth',
        '--file', str(output),
    ], env=environment, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('pg_dump failed; check operator connectivity, PostgreSQL version and privileges')
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError('pg_dump produced no backup')


def create_backup(source_url: str, source_bucket: str, recovery_url: str, recovery_bucket: str,
                  *, source_client=None, recovery_client=None, database_url: str | None = None) -> dict:
    source_host = urlparse(source_url).hostname
    recovery_host = urlparse(recovery_url).hostname
    if not source_host or not recovery_host or source_host == recovery_host:
        raise ValueError('Recovery must use a different Supabase project')
    database_url = database_url or os.environ['SUPABASE_DB_URL']
    if source_client is None:
        from supabase import create_client
        source_client = create_client(source_url, os.environ['SUPABASE_SECRET_KEY'])
    if recovery_client is None:
        from supabase import create_client
        recovery_client = create_client(recovery_url, os.environ['SUPABASE_RECOVERY_SECRET_KEY'])
    source = source_client.storage.from_(source_bucket)
    destination = recovery_client.storage.from_(recovery_bucket)
    started = datetime.now(timezone.utc)
    prefix = 'recovery/' + started.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex
    with tempfile.TemporaryDirectory(prefix='census-backup-') as temporary:
        output = Path(temporary) / 'database.dump'
        database_dump(database_url, output)
        dump_hash = verified_upload(destination, f'{prefix}/database.dump', output.read_bytes())
    objects = []
    # Application objects must stay immutable and must not be deleted while a
    # snapshot is running. Additional post-snapshot objects are harmless.
    for key in storage_objects(source):
        payload = source.download(key)
        target = f'{prefix}/objects/{key}'
        checksum = verified_upload(destination, target, payload)
        objects.append({'source': key, 'target': target, 'sha256': checksum, 'size': len(payload)})
    # This inventory assists operators; auth.* inside database.dump is the
    # authoritative point-in-time UID/password state, not this later list.
    identities = []
    page = 1
    while True:
        users = source_client.auth.admin.list_users(page=page, per_page=1000)
        identities.extend({'uid': user.id, 'email': user.email,
                           'email_verified': bool(user.email_confirmed_at)} for user in users)
        if len(users) < 1000:
            break
        page += 1
    identities_data = json.dumps(identities, sort_keys=True).encode()
    identities_hash = verified_upload(destination, f'{prefix}/identities.json', identities_data)
    manifest = {'version': 2, 'provider': 'supabase', 'status': 'COMPLETE',
                'source_project': source_host, 'source_bucket': source_bucket,
                'snapshot_started_at': started.isoformat(),
                'database_export': f'{prefix}/database.dump', 'database_sha256': dump_hash,
                'database_schemas': ['workbench_private', 'public', 'auth'],
                'objects': objects, 'identities': f'{prefix}/identities.json',
                'identities_sha256': identities_hash, 'restore_access': 'closed',
                'password_policy': 'reset-required',
                'completed_at': datetime.now(timezone.utc).isoformat()}
    verified_upload(destination, f'{prefix}/COMPLETE.json', json.dumps(manifest, sort_keys=True).encode())
    return {'prefix': prefix, 'objects': len(objects), 'identities': len(identities)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(create_backup(os.environ['SUPABASE_URL'],
        os.environ.get('SUPABASE_STORAGE_BUCKET', 'census-media'),
        os.environ['SUPABASE_RECOVERY_URL'], os.environ['WORKBENCH_RECOVERY_BUCKET'])))


if __name__ == '__main__':
    main()

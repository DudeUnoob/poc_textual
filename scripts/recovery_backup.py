"""Create a verified recovery set in an independently administered project.

Run as a dedicated backup identity every six hours, never as the web service.
No COMPLETE marker is written unless database export, all immutable object
versions and UID inventory have been verified. Passwords are reset on restore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone


def create_backup(project: str, source_bucket: str, recovery_project: str, recovery_bucket: str) -> dict:
    if project == recovery_project or source_bucket == recovery_bucket:
        raise ValueError('Recovery must use a different project and bucket')
    import firebase_admin
    from firebase_admin import auth
    from google.cloud import storage
    from google.cloud.firestore_admin_v1 import FirestoreAdminClient

    app = firebase_admin.initialize_app(options={'projectId': project}, name='recovery-export')
    source = storage.Client(project=project).get_bucket(source_bucket)
    destination = storage.Client(project=recovery_project).get_bucket(recovery_bucket)
    if source.project_number == destination.project_number:
        raise ValueError('Recovery bucket belongs to the source project')
    snapshot = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(second=0, microsecond=0)
    prefix = 'recovery/' + snapshot.strftime('%Y%m%dT%H%M%SZ')
    # A fixed PITR timestamp is essential: an ordinary export is not consistent.
    export = FirestoreAdminClient().export_documents(request={
        'name': f'projects/{project}/databases/(default)',
        'output_uri_prefix': f'gs://{recovery_bucket}/{prefix}/firestore',
        'snapshot_time': snapshot,
    }).result(timeout=18000)
    objects = []
    # Include every generation: older releases may reference a superseded version.
    for blob in source.list_blobs(versions=True):
        target_name = f'{prefix}/objects/{blob.generation}/{blob.name}'
        copied = source.copy_blob(blob, destination, target_name,
                                  source_generation=blob.generation,
                                  if_generation_match=0)
        copied.reload()
        if copied.crc32c != blob.crc32c or copied.size != blob.size:
            raise RuntimeError(f'Backup verification failed for object {blob.name}')
        objects.append({'source': blob.name, 'source_generation': str(blob.generation),
                        'target': target_name, 'generation': str(copied.generation),
                        'crc32c': copied.crc32c, 'size': copied.size})
    identities = [{'uid': u.uid, 'email': u.email, 'email_verified': u.email_verified,
                   'disabled': u.disabled} for u in auth.list_users(app=app).iterate_all()]
    uid_data = json.dumps(identities, sort_keys=True).encode()
    identity_blob = destination.blob(f'{prefix}/identities.json')
    identity_blob.upload_from_string(uid_data, content_type='application/json', if_generation_match=0)
    if identity_blob.download_as_bytes() != uid_data:
        raise RuntimeError('Identity inventory verification failed')
    manifest = {'version': 1, 'status': 'COMPLETE', 'source_project': project,
                'snapshot_time': snapshot.isoformat(), 'firestore_export': export.output_uri_prefix,
                'objects': objects, 'identities': identity_blob.name,
                'identities_sha256': hashlib.sha256(uid_data).hexdigest(),
                'restore_access': 'closed', 'password_policy': 'reset-required',
                'completed_at': datetime.now(timezone.utc).isoformat()}
    destination.blob(f'{prefix}/COMPLETE.json').upload_from_string(
        json.dumps(manifest, sort_keys=True), content_type='application/json', if_generation_match=0)
    return {'prefix': prefix, 'objects': len(objects), 'identities': len(identities)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('project', 'source-bucket', 'recovery-project', 'recovery-bucket'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    print(json.dumps(create_backup(args.project, args.source_bucket, args.recovery_project, args.recovery_bucket)))


if __name__ == '__main__':
    main()

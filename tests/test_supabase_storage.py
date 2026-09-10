from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest

from workbench.cloud.storage import StorageService, StorageOperationError, ObjectVerificationError
from workbench.cloud.supabase_storage import SupabaseBucket


class Files:
    def __init__(self):
        self.objects = {}
        self.options = []

    def upload(self, name, data, options):
        self.options.append(options)
        if name in self.objects:
            raise RuntimeError('already exists')
        self.objects[name] = data

    def download(self, name):
        return self.objects[name]


def storage():
    files = Files()
    client = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: files))
    return StorageService(SupabaseBucket(client, 'census-media')), files


def test_supabase_create_only_retry_and_content_version():
    service, files = storage()
    data = b'original scan'
    kwargs = dict(page_id='page', filename='page.jpg', content_type='image/jpeg')
    first = service.upload_original(**kwargs, stream=BytesIO(data))
    second = service.upload_original(**kwargs, stream=BytesIO(data))
    assert first == second
    assert first.generation == str(int(sha256(data).hexdigest(), 16))
    assert files.options == [{'content-type': 'image/jpeg', 'upsert': 'false'}] * 2
    assert service.authorized_download({'object_name': first.object_name, 'generation': first.generation}) == data


def test_out_of_band_overwrite_never_becomes_authoritative():
    service, files = storage()
    stored = service.upload_original(page_id='page', filename='page.png', content_type='image/png', stream=BytesIO(b'original'))
    files.objects[stored.object_name] = b'different'
    with pytest.raises((StorageOperationError, ObjectVerificationError)):
        service.authorized_download({'object_name': stored.object_name, 'generation': stored.generation})
    with pytest.raises(StorageOperationError):
        service.upload_original(page_id='page', filename='page.png', content_type='image/png', stream=BytesIO(b'original'))


def test_wrong_version_and_unsafe_object_names_rejected():
    service, files = storage()
    stored = service.upload_original(page_id='page', filename='page.png', content_type='image/png', stream=BytesIO(b'original'))
    with pytest.raises((StorageOperationError, ObjectVerificationError)):
        service.authorized_download({'object_name': stored.object_name, 'generation': '1'})
    from workbench.cloud.storage import UploadValidationError
    for path in ('../secret', '/absolute', 'a/../secret', 'a//b'):
        with pytest.raises(UploadValidationError):
            service._bucket.blob(path)

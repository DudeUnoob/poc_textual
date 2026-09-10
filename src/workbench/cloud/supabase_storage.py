"""Content-addressed private Supabase Storage adapter.

Supabase does not expose GCS object generations. The retained `generation`
interface is a decimal SHA-256 content version, verified against bytes on every
read. Objects use create-only uploads; an out-of-band overwrite fails closed.
"""
from hashlib import sha256
from pathlib import PurePosixPath

from .storage import ObjectVerificationError, UploadValidationError


class SupabaseBucket:
    def __init__(self, client, name):
        self.storage = client.storage.from_(name)
        self.name = name

    def blob(self, name, generation=None):
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or str(path) != name:
            raise UploadValidationError('Invalid storage object path')
        return SupabaseBlob(self, name, generation)


class SupabaseBlob:
    def __init__(self, bucket, name, generation=None):
        self.bucket, self.name = bucket, name
        self.generation = generation
        self.selected_generation = generation
        self.metadata = {}
        self.size = None

    def _verify(self, content, expected=None):
        checksum = sha256(content).hexdigest()
        version = int(checksum, 16)
        if expected is not None and int(expected) != version:
            raise ObjectVerificationError('Stored bytes no longer match the immutable content version')
        # Every original key embeds its expected checksum; metadata alone is insufficient.
        if self.name.startswith('originals/') and self.name.rsplit('/', 1)[-1] != checksum:
            raise ObjectVerificationError('Original checksum does not match its content-addressed path')
        self.generation, self.size, self.metadata = version, len(content), {'sha256': checksum}
        return content

    def upload_from_file(self, stream, *, size, content_type, if_generation_match, rewind=False):
        if if_generation_match != 0:
            raise UploadValidationError('Only create-only uploads are supported')
        if rewind:
            stream.seek(0)
        content = stream.read(size + 1)
        if len(content) != size:
            raise UploadValidationError('Upload size changed')
        self._verify(content)
        self.bucket.storage.upload(self.name, content, {'content-type': content_type, 'upsert': 'false'})

    def reload(self, if_generation_match=None):
        self._verify(self.bucket.storage.download(self.name),
                     if_generation_match if if_generation_match is not None else self.selected_generation)

    def download_as_bytes(self, if_generation_match=None):
        return self._verify(self.bucket.storage.download(self.name),
                            if_generation_match if if_generation_match is not None else self.selected_generation)


def private_bucket():
    from workbench.settings import supabase_storage_bucket
    from workbench.supabase_client import create_admin_client
    return SupabaseBucket(create_admin_client(), supabase_storage_bucket())

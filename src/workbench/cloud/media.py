"""Private Cloud Storage access. Browsers never talk to Storage directly."""
from __future__ import annotations

from datetime import timedelta

from workbench.settings import firebase_storage_bucket


def signed_read_url(object_name: str, generation: str, minutes: int = 10) -> str:
    from firebase_admin import storage

    if not object_name or "/" not in object_name:
        raise ValueError("Invalid storage object name")
    bucket = storage.bucket(firebase_storage_bucket() or None)
    generation_number = int(generation)
    blob = bucket.blob(object_name, generation=generation_number)
    return blob.generate_signed_url(expiration=timedelta(minutes=minutes), method="GET")


def download_bytes(object_name: str, generation: str) -> bytes:
    from firebase_admin import storage

    bucket = storage.bucket(firebase_storage_bucket() or None)
    generation_number = int(generation)
    return bucket.blob(object_name, generation=generation_number).download_as_bytes(
        if_generation_match=generation_number,
    )

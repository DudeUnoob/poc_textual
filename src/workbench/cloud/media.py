"""Verified private media reads, always through the authenticated backend."""
from .supabase_storage import private_bucket


def download_bytes(object_name: str, generation: str) -> bytes:
    version = int(generation)
    return private_bucket().blob(object_name, generation=version).download_as_bytes(
        if_generation_match=version,
    )

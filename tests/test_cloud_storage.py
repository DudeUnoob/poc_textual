from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pytest

from workbench.cloud.storage import (
    ObjectVerificationError,
    StorageOperationError,
    StorageService,
    UploadTooLargeError,
    UploadValidationError,
)


@dataclass
class FakeObject:
    data: bytes
    generation: int
    size: int
    metadata: dict[str, str]
    content_type: str


@dataclass
class FakeBucket:
    objects: dict[str, dict[int, FakeObject]] = field(default_factory=dict)
    upload_calls: list[dict[str, Any]] = field(default_factory=list)
    download_calls: list[dict[str, Any]] = field(default_factory=list)
    interrupt_upload: bool = False
    corrupt_sha_metadata: bool = False
    next_generation: int = 1

    def blob(self, blob_name: str, generation: int | None = None) -> "FakeBlob":
        return FakeBlob(self, blob_name, generation)


class FakeBlob:
    def __init__(
        self,
        bucket: FakeBucket,
        name: str,
        selected_generation: int | None = None,
    ):
        self.bucket = bucket
        self.name = name
        self.selected_generation = selected_generation
        self.generation: int | None = selected_generation
        self.size: int | None = None
        self.metadata: dict[str, str] = {}

    def upload_from_file(self, stream, **kwargs: Any) -> None:
        self.bucket.upload_calls.append(dict(name=self.name, **kwargs))
        if self.bucket.interrupt_upload:
            raise OSError("simulated connection loss")
        if kwargs.get("if_generation_match") == 0 and self.name in self.bucket.objects:
            raise RuntimeError("precondition failed")
        data = stream.read()
        generation = self.bucket.next_generation
        self.bucket.next_generation += 1
        stored = FakeObject(
            data=data,
            generation=generation,
            size=len(data),
            metadata=dict(self.metadata),
            content_type=kwargs["content_type"],
        )
        self.bucket.objects.setdefault(self.name, {})[generation] = stored
        self.generation = generation
        self.size = len(data)

    def reload(self, **kwargs: Any) -> None:
        versions = self.bucket.objects.get(self.name)
        if not versions:
            raise FileNotFoundError(self.name)
        generation = self.selected_generation or max(versions)
        if generation not in versions:
            raise FileNotFoundError(f"{self.name}#{generation}")
        if_generation_match = kwargs.get("if_generation_match")
        if if_generation_match is not None and int(if_generation_match) != generation:
            raise RuntimeError("generation precondition failed")
        stored = versions[generation]
        self.generation = stored.generation
        self.size = stored.size
        self.metadata = dict(stored.metadata)
        if self.bucket.corrupt_sha_metadata:
            self.metadata["sha256"] = "0" * 64

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        self.bucket.download_calls.append(
            dict(name=self.name, generation=self.selected_generation, **kwargs)
        )
        versions = self.bucket.objects[self.name]
        generation = self.selected_generation or max(versions)
        if int(kwargs["if_generation_match"]) != generation:
            raise RuntimeError("generation precondition failed")
        return versions[generation].data


@dataclass
class FakeRepository:
    pages: list[dict[str, Any]]
    ready_calls: list[tuple[str, str]] = field(default_factory=list)

    def list(self, collection: str) -> list[dict[str, Any]]:
        assert collection == "pages"
        return self.pages

    def mark_upload_ready(self, page_id: str, generation: str) -> None:
        self.ready_calls.append((page_id, generation))
        for page in self.pages:
            if page["id"] == page_id:
                page["storage_status"] = "ready"
                page["generation"] = generation


def upload(service: StorageService, data: bytes = b"census image bytes"):
    return service.upload_original(
        page_id="page-1",
        filename="sheet-1.jpg",
        content_type="image/jpeg",
        stream=BytesIO(data),
    )


def test_rejects_unsupported_extension_and_content_type() -> None:
    service = StorageService(FakeBucket(), upload_limit_bytes=100)
    with pytest.raises(UploadValidationError, match="allowed extensions"):
        service.upload_original(
            page_id="page-1",
            filename="sheet.pdf",
            content_type="image/jpeg",
            stream=BytesIO(b"x"),
        )
    with pytest.raises(UploadValidationError, match="allowed types"):
        service.upload_original(
            page_id="page-1",
            filename="sheet.jpg",
            content_type="application/octet-stream",
            stream=BytesIO(b"x"),
        )


def test_stream_is_bounded_by_operator_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKBENCH_MAX_UPLOAD_BYTES", "4")
    bucket = FakeBucket()
    service = StorageService(bucket)
    with pytest.raises(UploadTooLargeError, match="WORKBENCH_MAX_UPLOAD_BYTES=4"):
        upload(service, b"12345")
    assert bucket.upload_calls == []
    assert bucket.objects == {}


def test_upload_hashes_and_verifies_immutable_object() -> None:
    bucket = FakeBucket()
    stored = upload(StorageService(bucket, upload_limit_bytes=100))
    assert stored.object_name == f"originals/page-1/{stored.sha256}"
    assert stored.generation == "1"
    assert bucket.upload_calls[0]["if_generation_match"] == 0
    assert bucket.objects[stored.object_name][1].metadata == {"sha256": stored.sha256}


def test_identical_upload_retry_returns_existing_generation() -> None:
    bucket = FakeBucket()
    service = StorageService(bucket, upload_limit_bytes=100)
    first = upload(service)
    second = upload(service)
    assert second == first
    assert len(bucket.objects[first.object_name]) == 1


def test_interrupted_upload_leaves_page_pending() -> None:
    bucket = FakeBucket(interrupt_upload=True)
    service = StorageService(bucket, upload_limit_bytes=100)
    page = {
        "id": "page-1",
        "checksum": "a" * 64,
        "object_name": f"originals/page-1/{'a' * 64}",
        "storage_status": "pending",
    }
    repo = FakeRepository([page])
    with pytest.raises(StorageOperationError, match="remains pending"):
        upload(service)
    result = service.reconcile_pending(repo)
    assert result.ready_page_ids == ()
    assert "page-1" in result.failures
    assert repo.ready_calls == []
    assert page["storage_status"] == "pending"


def test_authorized_download_uses_exact_generation() -> None:
    bucket = FakeBucket()
    service = StorageService(bucket, upload_limit_bytes=100)
    stored = upload(service, b"first")
    bucket.objects[stored.object_name][2] = FakeObject(
        data=b"newer",
        generation=2,
        size=5,
        metadata={"sha256": stored.sha256},
        content_type="image/jpeg",
    )
    downloaded = service.authorized_download(
        {"object_name": stored.object_name, "generation": stored.generation}
    )
    assert downloaded == b"first"
    assert bucket.download_calls == [
        {
            "name": stored.object_name,
            "generation": 1,
            "if_generation_match": 1,
        }
    ]


def test_checksum_metadata_mismatch_never_marks_ready() -> None:
    bucket = FakeBucket()
    service = StorageService(bucket, upload_limit_bytes=100)
    stored = upload(service)
    page = {
        "id": "page-1",
        "checksum": stored.sha256,
        "object_name": stored.object_name,
        "storage_status": "pending",
        "size": stored.size,
    }
    repo = FakeRepository([page])
    bucket.corrupt_sha_metadata = True
    result = service.reconcile_pending(repo)
    assert repo.ready_calls == []
    assert "SHA-256 metadata mismatch" in result.failures["page-1"]


def test_upload_rejects_checksum_metadata_mismatch_after_reload() -> None:
    bucket = FakeBucket(corrupt_sha_metadata=True)
    with pytest.raises(ObjectVerificationError, match="SHA-256 metadata mismatch"):
        upload(StorageService(bucket, upload_limit_bytes=100))


def test_crop_cache_key_is_deterministic_and_identity_sensitive() -> None:
    page = {"checksum": "a" * 64, "generation": "7"}
    arguments = {
        "year": 1950,
        "schedule_type": "population",
        "sheet": "11-2A",
        "line": 12,
        "field": "Surname",
        "geometry_version": 3,
    }
    first = StorageService.crop_cache_key(page, **arguments)
    second = StorageService.crop_cache_key(dict(reversed(list(page.items()))), **arguments)
    changed = StorageService.crop_cache_key(page, **dict(arguments, line=13))
    assert first == second
    assert first.startswith(f"crops/{'a' * 64}/7/")
    assert changed != first

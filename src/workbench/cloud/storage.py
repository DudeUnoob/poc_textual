"""Private, immutable Cloud Storage operations for census scan images."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import inspect
import json
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, BinaryIO, Iterable, Mapping, Protocol

from workbench.budgets import max_upload_bytes

ALLOWED_IMAGE_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
STREAM_CHUNK_BYTES = 1024 * 1024


class StorageServiceError(RuntimeError):
    """Base exception safe to display to a workbench operator."""


class UploadValidationError(StorageServiceError):
    """The proposed upload is not an allowed census image."""


class UploadTooLargeError(UploadValidationError):
    """The streamed upload exceeded the operator-configured limit."""


class ObjectVerificationError(StorageServiceError):
    """Cloud Storage metadata does not match the immutable page record."""


class StorageOperationError(StorageServiceError):
    """Cloud Storage failed before an object could be verified."""


class BucketLike(Protocol):
    def blob(self, blob_name: str, generation: int | None = None) -> Any: ...


@dataclass(frozen=True)
class StoredObject:
    object_name: str
    generation: str
    size: int
    sha256: str
    content_type: str


@dataclass(frozen=True)
class ReconcileResult:
    ready_page_ids: tuple[str, ...]
    failures: Mapping[str, str]


def _validated_content_type(filename: str, content_type: str) -> str:
    safe_name = Path(filename or "").name
    extension = Path(safe_name).suffix.casefold()
    expected_type = ALLOWED_IMAGE_TYPES.get(extension)
    normalized_type = (content_type or "").strip().casefold()
    if not safe_name or expected_type is None:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_TYPES))
        raise UploadValidationError(
            f"Unsupported image filename {filename!r}; allowed extensions: {allowed}."
        )
    if normalized_type not in set(ALLOWED_IMAGE_TYPES.values()):
        raise UploadValidationError(
            f"Unsupported image content type {content_type!r}; "
            "allowed types: image/jpeg, image/png, image/tiff."
        )
    if normalized_type != expected_type:
        raise UploadValidationError(
            f"Image filename {safe_name!r} requires content type {expected_type}, "
            f"not {normalized_type}."
        )
    return normalized_type


def _generation(value: Any, context: str) -> str:
    if value is None or str(value).strip() == "":
        raise ObjectVerificationError(f"{context} has no immutable object generation.")
    return str(value)


class StorageService:
    """Typed Cloud Storage boundary around an injected private bucket."""

    def __init__(self, bucket: BucketLike, upload_limit_bytes: int | None = None):
        self._bucket = bucket
        self._upload_limit_bytes = (
            max_upload_bytes() if upload_limit_bytes is None else int(upload_limit_bytes)
        )
        if self._upload_limit_bytes < 0:
            raise ValueError("Upload byte limit cannot be negative.")

    def upload_original(
        self,
        *,
        page_id: str,
        filename: str,
        content_type: str,
        stream: BinaryIO,
    ) -> StoredObject:
        """Bound, hash, upload, and verify one immutable original image."""
        if not page_id or "/" in page_id:
            raise UploadValidationError("Page ID must be a non-empty path segment.")
        normalized_type = _validated_content_type(filename, content_type)

        digest = sha256()
        size = 0
        with TemporaryFile(mode="w+b") as buffered:
            while True:
                remaining = self._upload_limit_bytes - size
                chunk = stream.read(min(STREAM_CHUNK_BYTES, remaining + 1))
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise UploadValidationError("Upload stream must return bytes.")
                size += len(chunk)
                if size > self._upload_limit_bytes:
                    raise UploadTooLargeError(
                        "Upload exceeds "
                        f"WORKBENCH_MAX_UPLOAD_BYTES={self._upload_limit_bytes}; "
                        "choose a smaller image or raise the operator limit."
                    )
                digest.update(chunk)
                buffered.write(chunk)

            checksum = digest.hexdigest()
            object_name = f"originals/{page_id}/{checksum}"
            blob = self._bucket.blob(object_name)
            blob.metadata = {"sha256": checksum}
            buffered.seek(0)
            try:
                blob.upload_from_file(
                    buffered,
                    size=size,
                    content_type=normalized_type,
                    if_generation_match=0,
                    rewind=False,
                )
            except Exception as exc:
                # An identical retry may encounter the immutable object created
                # by the first attempt after its HTTP response was lost.
                try:
                    existing = self._bucket.blob(object_name)
                    existing.reload()
                    existing_generation = _generation(
                        getattr(existing, "generation", None), object_name,
                    )
                    self._verify_loaded_blob(
                        existing,
                        object_name=object_name,
                        generation=existing_generation,
                        checksum=checksum,
                        expected_size=size,
                    )
                    return StoredObject(
                        object_name, existing_generation, size, checksum,
                        normalized_type,
                    )
                except Exception:
                    raise StorageOperationError(
                        f"Cloud Storage upload failed for page {page_id}; "
                        "the page remains pending and may be retried."
                    ) from exc

        generation = _generation(getattr(blob, "generation", None), object_name)
        self._verify_blob(
            object_name=object_name,
            generation=generation,
            checksum=checksum,
            expected_size=size,
        )
        return StoredObject(object_name, generation, size, checksum, normalized_type)

    def authorized_download(self, page_doc: Mapping[str, Any]) -> bytes:
        """Download only the exact object generation authorized by page metadata."""
        object_name = str(page_doc.get("object_name") or "")
        generation = _generation(page_doc.get("generation"), "Page metadata")
        if not object_name.startswith("originals/"):
            raise ObjectVerificationError("Page metadata has an invalid original object name.")
        try:
            blob = self._bucket.blob(object_name, generation=int(generation))
            return blob.download_as_bytes(if_generation_match=int(generation))
        except StorageServiceError:
            raise
        except Exception as exc:
            raise StorageOperationError(
                f"Unable to download authorized generation {generation} of {object_name}."
            ) from exc

    @staticmethod
    def crop_cache_key(
        page_doc: Mapping[str, Any],
        *,
        year: int,
        schedule_type: str,
        sheet: str | int,
        line: str | int,
        field: str,
        geometry_version: str | int,
    ) -> str:
        """Return a deterministic key that invalidates with source or geometry changes."""
        checksum = str(page_doc.get("checksum") or "")
        generation = _generation(page_doc.get("generation"), "Page metadata")
        if len(checksum) != 64:
            raise ObjectVerificationError("Page metadata requires a valid SHA-256 checksum.")
        identity = {
            "checksum": checksum,
            "field": str(field),
            "generation": generation,
            "geometry_version": str(geometry_version),
            "line": str(line),
            "schedule_type": str(schedule_type),
            "sheet": str(sheet),
            "year": int(year),
        }
        cache_digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"crops/{checksum}/{generation}/{cache_digest}"

    def reconcile_pending(
        self,
        repo: Any,
        pages: Iterable[Mapping[str, Any]] | None = None,
        *,
        actor: Any = None,
    ) -> ReconcileResult:
        """Mark pending pages ready only after immutable object verification."""
        candidates = list(repo.list("pages") if pages is None else pages)
        ready_page_ids: list[str] = []
        failures: dict[str, str] = {}
        for page in candidates:
            if page.get("storage_status") != "pending":
                continue
            page_id = str(page.get("id") or "")
            object_name = str(page.get("object_name") or "")
            checksum = str(page.get("checksum") or "")
            try:
                blob = self._bucket.blob(object_name)
                blob.reload()
                generation = _generation(getattr(blob, "generation", None), object_name)
                expected_size = page.get("size")
                self._verify_loaded_blob(
                    blob,
                    object_name=object_name,
                    generation=generation,
                    checksum=checksum,
                    expected_size=int(expected_size) if expected_size is not None else None,
                )
                self._mark_upload_ready(repo, actor, page_id, generation)
            except Exception as exc:
                failures[page_id or "<missing-page-id>"] = str(exc)
                continue
            ready_page_ids.append(page_id)
        return ReconcileResult(tuple(ready_page_ids), failures)

    def _verify_blob(
        self,
        *,
        object_name: str,
        generation: str,
        checksum: str,
        expected_size: int | None,
    ) -> None:
        try:
            blob = self._bucket.blob(object_name, generation=int(generation))
            blob.reload(if_generation_match=int(generation))
        except Exception as exc:
            raise ObjectVerificationError(
                f"Unable to reload generation {generation} of {object_name} after upload."
            ) from exc
        self._verify_loaded_blob(
            blob,
            object_name=object_name,
            generation=generation,
            checksum=checksum,
            expected_size=expected_size,
        )

    @staticmethod
    def _verify_loaded_blob(
        blob: Any,
        *,
        object_name: str,
        generation: str,
        checksum: str,
        expected_size: int | None,
    ) -> None:
        actual_generation = _generation(getattr(blob, "generation", None), object_name)
        if actual_generation != str(generation):
            raise ObjectVerificationError(
                f"Generation mismatch for {object_name}: expected {generation}, "
                f"found {actual_generation}."
            )
        actual_size = getattr(blob, "size", None)
        if actual_size is None or int(actual_size) < 0:
            raise ObjectVerificationError(f"Cloud Storage did not report a size for {object_name}.")
        if expected_size is not None and int(actual_size) != expected_size:
            raise ObjectVerificationError(
                f"Size mismatch for {object_name}: expected {expected_size}, "
                f"found {actual_size}."
            )
        metadata = getattr(blob, "metadata", None) or {}
        if metadata.get("sha256") != checksum:
            raise ObjectVerificationError(
                f"SHA-256 metadata mismatch for {object_name}; the page remains pending."
            )

    @staticmethod
    def _mark_upload_ready(repo: Any, actor: Any, page_id: str, generation: str) -> None:
        method = repo.mark_upload_ready
        parameter_count = len(inspect.signature(method).parameters)
        operation_id = f"reconcile-{page_id}-{generation}"
        if parameter_count == 2:
            method(page_id, generation)
            return
        if parameter_count == 3:
            method(page_id, generation, operation_id)
            return
        if actor is None:
            raise StorageServiceError(
                "Repository reconciliation requires an authorized actor to mark uploads ready."
            )
        method(actor, page_id, generation, operation_id)

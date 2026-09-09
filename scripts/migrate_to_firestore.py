#!/usr/bin/env python3
"""Deterministically migrate a local workbench SQLite database to Firestore."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from workbench.db import (  # noqa: E402
    Batch, CalibrationBand, Export, ExtractionRun, FieldCandidate, Page, ReviewDecision,
)

ENTITY_MODELS = {
    "batches": ("batch", Batch),
    "pages": ("page", Page),
    "runs": ("run", ExtractionRun),
    "candidates": ("candidate", FieldCandidate),
    "decisions": ("decision", ReviewDecision),
    "exports": ("export", Export),
    "calibration_bands": ("calibration", CalibrationBand),
}
RELATION_FIELDS = {
    "pages": {"batch_id": "batch"},
    "runs": {"batch_id": "batch"},
    "candidates": {"run_id": "run", "page_id": "page"},
    "decisions": {"candidate_id": "candidate"},
    "exports": {"batch_id": "batch"},
}


class Store(Protocol):
    def get(self, path: str) -> dict[str, Any] | None: ...


StorageCopy = Callable[[Path, str, str], Mapping[str, Any]]
Clock = Callable[[], datetime]


class MigrationError(RuntimeError):
    pass


class MigrationConflict(MigrationError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stable_id(kind: str, legacy_id: int) -> str:
    return f"{kind}-{int(legacy_id):020d}"


def file_sha256(path: Path) -> str:
    if not path.is_file():
        raise MigrationError(f"Referenced file is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _database_path(database: str | Path) -> Path:
    value = str(database)
    if value.startswith("sqlite:///"):
        value = value.removeprefix("sqlite:///")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise MigrationError(f"SQLite database is missing: {path}")
    return path


def _resolve_file(value: str, storage_root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (storage_root / path).resolve()


def _row_document(row: Any, collection: str, kind: str) -> dict[str, Any]:
    document = {
        column.key: _json_value(getattr(row, column.key))
        for column in inspect(type(row)).columns
    }
    legacy_id = int(document["id"])
    document.update(legacy_id=legacy_id, id=stable_id(kind, legacy_id))
    for field, related_kind in RELATION_FIELDS.get(collection, {}).items():
        related_id = document.get(field)
        document[field] = stable_id(related_kind, related_id) if related_id is not None else None
    return document


def _add_relationship_lists(documents: dict[str, list[dict[str, Any]]]) -> None:
    indexes = {
        collection: {document["id"]: document for document in values}
        for collection, values in documents.items()
    }
    for batch in documents["batches"]:
        batch.update(page_ids=[], run_ids=[], export_ids=[])
    for page in documents["pages"]:
        indexes["batches"][page["batch_id"]]["page_ids"].append(page["id"])
        page["candidate_ids"] = []
    for run in documents["runs"]:
        indexes["batches"][run["batch_id"]]["run_ids"].append(run["id"])
        run["candidate_ids"] = []
    for candidate in documents["candidates"]:
        indexes["pages"][candidate["page_id"]]["candidate_ids"].append(candidate["id"])
        indexes["runs"][candidate["run_id"]]["candidate_ids"].append(candidate["id"])
        candidate["decision_ids"] = []
    for decision in documents["decisions"]:
        indexes["candidates"][decision["candidate_id"]]["decision_ids"].append(decision["id"])
    for export in documents["exports"]:
        indexes["batches"][export["batch_id"]]["export_ids"].append(export["id"])
    for values in documents.values():
        for document in values:
            for key, value in tuple(document.items()):
                if key.endswith("_ids") and isinstance(value, list):
                    document[key] = sorted(value)


def _referenced_files(
    documents: dict[str, list[dict[str, Any]]], storage_root: Path,
) -> list[dict[str, Any]]:
    references: list[tuple[str, str, str, Path]] = []
    for page in documents["pages"]:
        if page.get("stored_path"):
            references.append(("pages", page["id"], "stored_path", _resolve_file(page["stored_path"], storage_root)))
    for batch in documents["batches"]:
        if batch.get("ground_truth_path"):
            references.append(("batches", batch["id"], "ground_truth_path", _resolve_file(batch["ground_truth_path"], storage_root)))
    for export in documents["exports"]:
        if not export.get("directory"):
            continue
        directory = _resolve_file(export["directory"], storage_root)
        if not directory.is_dir():
            raise MigrationError(f"Referenced export directory is missing: {directory}")
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            references.append(("exports", export["id"], f"directory/{path.relative_to(directory).as_posix()}", path))

    files: list[dict[str, Any]] = []
    for collection, owner_id, field, path in sorted(references, key=lambda item: item[:3]):
        checksum = file_sha256(path)
        files.append({
            "owner_collection": collection,
            "owner_id": owner_id,
            "field": field,
            "source_path": str(path),
            "sha256": checksum,
            "size": path.stat().st_size,
            "destination_key": f"migrations/objects/{checksum}/{path.name}",
        })
    return files


def inventory_source(database: str | Path, storage_root: str | Path) -> dict[str, Any]:
    """Read source rows and files without mutating either system."""
    database_path = _database_path(database)
    before = file_sha256(database_path)
    engine = create_engine(f"sqlite:///{database_path}")
    local_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with local_session() as session:
            documents = {
                collection: [
                    _row_document(row, collection, kind)
                    for row in session.query(model).order_by(model.id).all()
                ]
                for collection, (kind, model) in ENTITY_MODELS.items()
            }
    finally:
        engine.dispose()
    if file_sha256(database_path) != before:
        raise MigrationError("Source database changed while inventory was being created")

    _add_relationship_lists(documents)
    files = _referenced_files(documents, Path(storage_root).expanduser().resolve())
    source_fingerprint = hashlib.sha256(json.dumps(
        {"database": before, "files": [(item["source_path"], item["sha256"]) for item in files]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    relationships = {
        collection: {
            document["id"]: {
                key: value for key, value in document.items()
                if key != "legacy_id" and (key.endswith("_id") or key.endswith("_ids"))
            }
            for document in values
        }
        for collection, values in documents.items()
    }
    return {
        "source_database": str(database_path),
        "source_db_fingerprint": before,
        "source_fingerprint": source_fingerprint,
        "source_files": files,
        "counts": {collection: len(values) for collection, values in documents.items()},
        "destination_ids": {
            collection: [document["id"] for document in values]
            for collection, values in documents.items()
        },
        "relationships": relationships,
        "documents": documents,
    }


def _store_set(store: Store, path: str, value: dict[str, Any]) -> None:
    setter = getattr(store, "set", None)
    if callable(setter):
        setter(path, value)
        return
    atomic = getattr(store, "atomic", None)
    if not callable(atomic):
        raise TypeError("Destination store must provide set/get or atomic/get")
    atomic(lambda transaction: transaction.set(path, value))


def _manifest_path(migration_id: str) -> str:
    if not migration_id or "/" in migration_id:
        raise ValueError("migration_id must be a non-empty Firestore-safe string")
    return f"migration_manifests/{migration_id}"


def _public_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in inventory.items() if key != "documents"}


def verify_migration(
    store: Store,
    manifest: Mapping[str, Any],
    storage_stat: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare destination counts, relationship references, and object hashes."""
    destination_ids = manifest["destination_ids"]
    documents: dict[str, dict[str, Any]] = {}
    for collection, identifiers in destination_ids.items():
        if len(identifiers) != int(manifest["counts"][collection]):
            raise MigrationError(f"Count mismatch for {collection}")
        for identifier in identifiers:
            document = store.get(f"{collection}/{identifier}")
            if document is None:
                raise MigrationError(f"Destination document is missing: {collection}/{identifier}")
            documents[f"{collection}/{identifier}"] = document

    all_ids = {identifier for values in destination_ids.values() for identifier in values}
    for collection, owners in manifest["relationships"].items():
        for owner_id, relationships in owners.items():
            document = documents[f"{collection}/{owner_id}"]
            for field, expected in relationships.items():
                if document.get(field) != expected:
                    raise MigrationError(f"Relationship mismatch: {collection}/{owner_id}.{field}")
                targets = expected if isinstance(expected, list) else [expected]
                if any(target is not None and target not in all_ids for target in targets):
                    raise MigrationError(f"Dangling relationship: {collection}/{owner_id}.{field}")

    for source_file in manifest["source_files"]:
        copied = source_file.get("copied") or {}
        if copied.get("sha256") != source_file["sha256"] or not copied.get("generation"):
            raise MigrationError(f"Object verification failed: {source_file['destination_key']}")
        if storage_stat:
            current = storage_stat(source_file["destination_key"])
            if (
                current.get("sha256") != source_file["sha256"]
                or str(current.get("generation")) != str(copied["generation"])
            ):
                raise MigrationError(f"Stored object changed: {source_file['destination_key']}")
    return {"verified": True, "counts": dict(manifest["counts"])}


def migrate_to_firestore(
    database: str | Path,
    storage_root: str | Path,
    migration_id: str,
    store: Store,
    copy_object: StorageCopy,
    clock: Clock = utc_now,
) -> dict[str, Any]:
    """Migrate once; exact completed reruns return the original manifest."""
    path = _manifest_path(migration_id)
    existing = store.get(path)
    try:
        inventory = inventory_source(database, storage_root)
    except Exception as error:
        if not existing:
            database_path = _database_path(database)
            failed = {
                "version": 1,
                "migration_id": migration_id,
                "source_database": str(database_path),
                "source_db_fingerprint": file_sha256(database_path),
                "status": "failed",
                "started_at": _json_value(clock()),
                "completed_at": None,
                "error": f"{type(error).__name__}: {error}",
            }
            _store_set(store, path, failed)
        raise
    if existing:
        if existing.get("source_fingerprint") != inventory["source_fingerprint"]:
            raise MigrationConflict(f"Migration ID {migration_id!r} belongs to a different source database")
        if existing.get("status") == "complete":
            return existing
        raise MigrationConflict(f"Migration ID {migration_id!r} already exists with status {existing.get('status')}")

    manifest = {
        "version": 1,
        "migration_id": migration_id,
        **_public_inventory(inventory),
        "status": "pending",
        "started_at": _json_value(clock()),
        "completed_at": None,
        "error": None,
    }
    _store_set(store, path, manifest)
    try:
        copied_files = []
        for source_file in inventory["source_files"]:
            result = dict(copy_object(Path(source_file["source_path"]), source_file["destination_key"], source_file["sha256"]))
            returned_checksum = result.get("sha256") or result.get("checksum")
            generation = result.get("generation")
            if returned_checksum != source_file["sha256"] or generation in (None, ""):
                raise MigrationError(f"Storage checksum/generation mismatch: {source_file['source_path']}")
            copied_files.append({
                **source_file,
                "copied": {"sha256": returned_checksum, "generation": str(generation)},
            })
        manifest["source_files"] = copied_files
        documents_by_id = {
            (collection, document["id"]): document
            for collection, values in inventory["documents"].items()
            for document in values
        }
        for source_file in copied_files:
            owner = documents_by_id[(source_file["owner_collection"], source_file["owner_id"])]
            owner.setdefault("migrated_objects", []).append({
                "field": source_file["field"],
                "key": source_file["destination_key"],
                "sha256": source_file["sha256"],
                "generation": source_file["copied"]["generation"],
            })
        for collection, values in inventory["documents"].items():
            for document in values:
                _store_set(store, f"{collection}/{document['id']}", document)
        verify_migration(store, manifest)
        manifest = {**manifest, "status": "complete", "completed_at": _json_value(clock())}
        _store_set(store, path, manifest)
        return manifest
    except Exception as error:
        failed = {
            **manifest,
            "status": "failed",
            "completed_at": None,
            "error": f"{type(error).__name__}: {error}",
        }
        _store_set(store, path, failed)
        raise


def _firebase_dependencies() -> tuple[Store, StorageCopy]:
    import firebase_admin
    from firebase_admin import firestore, storage
    from workbench.cloud.store import FirebaseStore

    app = firebase_admin.initialize_app()
    destination_store = FirebaseStore(firestore.client(app=app))
    bucket = storage.bucket(app=app)

    def copy_object(source: Path, destination_key: str, checksum: str) -> Mapping[str, Any]:
        blob = bucket.blob(destination_key)
        blob.upload_from_filename(str(source), if_generation_match=0)
        blob.reload()
        blob.metadata = {**(blob.metadata or {}), "sha256": checksum}
        blob.patch(if_metageneration_match=blob.metageneration)
        return {"sha256": checksum, "generation": str(blob.generation)}

    return destination_store, copy_object


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(json.dumps(_public_inventory(inventory_source(args.database, args.storage_root)), indent=2, sort_keys=True))
        return 0
    store, copy_object = _firebase_dependencies()
    result = migrate_to_firestore(args.database, args.storage_root, args.migration_id, store, copy_object)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

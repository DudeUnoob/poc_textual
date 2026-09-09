from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from workbench.cloud.store import MemoryStore
from workbench.db import (
    Base,
    Batch,
    Export,
    ExtractionRun,
    FieldCandidate,
    Page,
    ReviewDecision,
)


def load_migration_module():
    path = Path(__file__).parents[1] / "scripts" / "migrate_to_firestore.py"
    spec = importlib.util.spec_from_file_location("migrate_to_firestore", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def source_database(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "source.db"
    image = tmp_path / "sheet.jpg"
    image.write_bytes(b"scan")
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "reviewed_records.json").write_text("[]")
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = Batch(
            name="Bastrop", county="Bastrop", state="Texas",
            census_year=1950, schedule_type="population",
            enumeration_district="11-2A", ground_truth_sheet="Bastrop 11-2A",
        )
        page = Page(
            batch=batch, original_filename="sheet.jpg", stored_path=str(image),
            sha256="a" * 64, page_number=1, metadata_confirmed=True,
        )
        session.add_all([batch, page])
        session.flush()
        run = ExtractionRun(
            batch_id=batch.id, model="gemini-3.8-flash",
            prompt_version="schema-v1", config={"thinking_level": "medium"},
        )
        session.add(run)
        session.flush()
        candidate = FieldCandidate(
            run_id=run.id, page=page, line_number=1, field_name="Surname",
            raw_value="Lewis", normalized_value="Lewis", model_confidence="high",
            source_pass="full_page", row_legibility="clear",
        )
        session.add(candidate)
        session.flush()
        session.add(ReviewDecision(
            candidate=candidate, reviewer="reviewer@utexas.edu",
            action="confirmed", previous_value="Lewis", value="Lewis",
        ))
        session.add(Export(
            batch_id=batch.id, version=1, directory=str(export_dir), summary={},
        ))
        session.commit()
    engine.dispose()
    return database, tmp_path


def object_copier(_source: Path, _key: str, checksum: str):
    return {"sha256": checksum, "generation": "1"}


def test_migration_is_deterministic_and_preserves_relationships(tmp_path):
    module = load_migration_module()
    database, storage_root = source_database(tmp_path)
    store = MemoryStore()
    clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)

    first = module.migrate_to_firestore(
        database, storage_root, "pilot", store, object_copier, clock,
    )
    second = module.migrate_to_firestore(
        database, storage_root, "pilot", store, object_copier, clock,
    )

    assert first == second
    assert first["status"] == "complete"
    assert first["counts"]["batches"] == 1
    batch_id = first["destination_ids"]["batches"][0]
    page_id = first["destination_ids"]["pages"][0]
    assert store.get(f"batches/{batch_id}")["page_ids"] == [page_id]
    assert module.verify_migration(store, first)["verified"] is True


def test_migration_rejects_changed_source_for_same_id(tmp_path):
    module = load_migration_module()
    database, storage_root = source_database(tmp_path)
    store = MemoryStore()
    module.migrate_to_firestore(database, storage_root, "pilot", store, object_copier)
    Path(storage_root / "sheet.jpg").write_bytes(b"changed scan")

    with pytest.raises(module.MigrationConflict):
        module.migrate_to_firestore(
            database, storage_root, "pilot", store, object_copier,
        )


def test_migration_checksum_failure_never_marks_complete(tmp_path):
    module = load_migration_module()
    database, storage_root = source_database(tmp_path)
    store = MemoryStore()

    with pytest.raises(module.MigrationError):
        module.migrate_to_firestore(
            database, storage_root, "pilot", store,
            lambda *_: {"sha256": "wrong", "generation": "1"},
        )
    assert store.get("migration_manifests/pilot")["status"] == "failed"


def test_inventory_fails_for_missing_referenced_file(tmp_path):
    module = load_migration_module()
    database, storage_root = source_database(tmp_path)
    Path(storage_root / "sheet.jpg").unlink()
    with pytest.raises(module.MigrationError, match="missing"):
        module.inventory_source(database, storage_root)

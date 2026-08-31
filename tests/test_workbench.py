from __future__ import annotations

from io import BytesIO

from PIL import Image
from starlette.datastructures import UploadFile


def _workbench(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "workbench"))
    from workbench.db import Base, Batch, Page, SessionLocal, engine
    from workbench.services import (apply_decision, batch_ready, import_files,
                                    parse_image_hint, update_page_manifest)
    Base.metadata.create_all(engine)
    return Batch, Page, SessionLocal, parse_image_hint, import_files, update_page_manifest, batch_ready, apply_decision


def _upload(name: str) -> UploadFile:
    image = Image.new("RGB", (20, 20), "white")
    body = BytesIO()
    image.save(body, "JPEG")
    body.seek(0)
    return UploadFile(filename=name, file=body)


def test_parse_image_hints_and_manifest_confirmation(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, parse_hint, import_files, update_manifest, ready, _ = _workbench(tmp_path, monkeypatch)
    assert parse_hint("sheet_07.jpg") == (7, "census")
    assert parse_hint("cover_01.jpg") == (1, "cover")
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        session.add(batch)
        session.flush()
        pages = import_files(session, batch, [_upload("sheet_01.jpg"), _upload("sheet_01_copy.jpg")])
        assert pages[1].import_error == "Duplicate image hash in this batch"
        assert ready(batch)[0] is False
        assert pages[0].page_number == 1
        update_manifest(pages[0], 1, "census")
        assert pages[0].metadata_confirmed is True


def test_review_decision_is_append_only(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, _, _, _, _, decide = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun, FieldCandidate
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        page = Page(batch=batch, original_filename="sheet_01.jpg", stored_path="/tmp/x.jpg", sha256="x", page_number=1, metadata_confirmed=True)
        session.add_all([batch, page])
        session.flush()
        run = ExtractionRun(batch_id=batch.id, model="test")
        session.add(run)
        session.flush()
        candidate = FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Surname", normalized_value="Wite", raw_value="Wite", model_confidence="low", source_pass="full_page", row_legibility="partial")
        session.add(candidate)
        session.flush()
        first = decide(session, candidate, "DK", "corrected", "White", "clear handwriting")
        second = decide(session, candidate, "DK", "confirmed", None, None)
        assert first.previous_value == "Wite"
        assert second.previous_value == "Wite"
        assert len(candidate.decisions) == 2


def test_run_snapshot_exposes_queue_and_live_progress(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun
    from workbench.progress import run_snapshot, update_progress
    with SessionLocal() as session:
        batch = Batch(name="Live", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        session.add(batch)
        session.flush()
        session.add_all([
            Page(batch_id=batch.id, original_filename="sheet_01.jpg", stored_path="/tmp/1.jpg", sha256="1", page_number=1, kind="census", metadata_confirmed=True),
            Page(batch_id=batch.id, original_filename="sheet_02.jpg", stored_path="/tmp/2.jpg", sha256="2", page_number=2, kind="census", metadata_confirmed=True),
        ])
        run = ExtractionRun(batch_id=batch.id, model="test")
        session.add(run)
        session.flush()
        queued = run_snapshot(session, run)
        assert queued["queue_position"] == 1
        assert queued["total_pages"] == 2

        run.status = "running"
        update_progress(run, completed_pages=1, total_pages=2, current_page=2,
                        retry_count=1, message="Reading page 2")
        running = run_snapshot(session, run)
        assert running["percent"] == 50
        assert running["current_page"] == 2
        assert running["retry_count"] == 1
        assert running["step_elapsed_seconds"] is not None


def test_worker_restart_requeues_interrupted_run(tmp_path, monkeypatch):
    Batch, _, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun
    from workbench.worker import recover_interrupted_runs
    with SessionLocal() as session:
        batch = Batch(name="Recovery", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        session.add(batch)
        session.flush()
        run = ExtractionRun(batch_id=batch.id, model="test", status="running",
                            config={"progress": {"completed_pages": 1}})
        session.add(run)
        session.commit()
        run_id = run.id

    assert recover_interrupted_runs() >= 1
    with SessionLocal() as session:
        recovered = session.get(ExtractionRun, run_id)
        assert recovered.status == "queued"
        assert recovered.config["progress"]["completed_pages"] == 1
        assert "safely resuming" in recovered.config["progress"]["message"]


def test_billing_failure_explains_required_user_action(tmp_path, monkeypatch):
    _workbench(tmp_path, monkeypatch)
    from workbench.progress import classify_failure
    failure = classify_failure("429 RESOURCE_EXHAUSTED: prepayment credits are depleted")
    assert failure["kind"] == "billing"
    assert "Google AI Studio" in failure["action_required"]

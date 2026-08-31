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


def test_review_queue_uses_only_latest_run_with_candidates(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun, FieldCandidate
    from workbench.services import queue_query, review_queue_count, review_row_count
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        page = Page(batch=batch, original_filename="sheet_01.jpg", stored_path="/tmp/x.jpg", sha256="x", page_number=1, metadata_confirmed=True)
        session.add_all([batch, page])
        session.flush()
        old_run = ExtractionRun(batch_id=batch.id, model="old")
        new_run = ExtractionRun(batch_id=batch.id, model="new")
        session.add_all([old_run, new_run])
        session.flush()
        session.add_all([
            FieldCandidate(run_id=old_run.id, page=page, line_number=1, field_name="Surname", normalized_value="Old", model_confidence="low", source_pass="full_page", row_legibility="partial"),
            FieldCandidate(run_id=new_run.id, page=page, line_number=1, field_name="Surname", normalized_value="Current", model_confidence="low", source_pass="full_page", row_legibility="partial"),
        ])
        session.flush()
        queued = session.scalars(queue_query(batch.id)).all()
        assert [candidate.normalized_value for candidate in queued] == ["Current"]
        assert review_queue_count(session, batch.id) == 1
        assert review_row_count(session, batch.id) == 1


def test_review_row_count_groups_multiple_fields_for_one_person(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun, FieldCandidate
    from workbench.services import review_queue_count, review_row_count
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        page = Page(batch=batch, original_filename="sheet_01.jpg", stored_path="/tmp/x.jpg", sha256="x", page_number=1, metadata_confirmed=True)
        run = ExtractionRun(batch_id=1, model="new")
        session.add_all([batch, page])
        session.flush()
        run.batch_id = batch.id
        session.add(run)
        session.flush()
        session.add_all([
            FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Surname", model_confidence="low", source_pass="crop_1", row_legibility="partial"),
            FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Given Name", model_confidence="low", source_pass="crop_1", row_legibility="partial"),
            FieldCandidate(run_id=run.id, page=page, line_number=2, field_name="Race", model_confidence="low", source_pass="crop_1", row_legibility="partial"),
        ])
        session.flush()
        assert review_queue_count(session, batch.id) == 3
        assert review_row_count(session, batch.id) == 2


def test_reclassify_run_excludes_calibration_page(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench import services
    from workbench.db import CalibrationBand, ExtractionRun, FieldCandidate
    monkeypatch.setattr(services, "deterministic_sample", lambda *args: False)
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        page1 = Page(batch=batch, original_filename="1.jpg", stored_path="/tmp/1.jpg", sha256="1", page_number=1, metadata_confirmed=True)
        page2 = Page(batch=batch, original_filename="2.jpg", stored_path="/tmp/2.jpg", sha256="2", page_number=2, metadata_confirmed=True)
        session.add_all([batch, page1, page2])
        session.flush()
        run = ExtractionRun(batch_id=batch.id, model="gemini-3.5-flash")
        band = CalibrationBand(census_year=1950, field_name="Birth Place", confidence="high", total=30, correct=30)
        session.add_all([run, band])
        session.flush()
        candidates = [
            FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Birth Place", normalized_value="Texas", raw_value="Tex", model_confidence="high", source_pass="crop_1", row_legibility="clear", reasons=[], validation_warnings=[])
            for page in (page1, page2)
        ]
        session.add_all(candidates)
        session.flush()
        services.reclassify_run_candidates(session, run.id, exclude_page_ids={page1.id})
        assert candidates[0].status == "review_required"
        assert candidates[1].status == "auto_accepted"


def test_review_queue_advances_past_skipped_and_deferred_fields(tmp_path, monkeypatch):
    Batch, Page, SessionLocal, *_ = _workbench(tmp_path, monkeypatch)
    from workbench.db import ExtractionRun, FieldCandidate
    from workbench.services import queue_query
    with SessionLocal() as session:
        batch = Batch(name="B", county="Bastrop", state="Texas", census_year=1950, enumeration_district="11-1")
        page = Page(batch=batch, original_filename="sheet_01.jpg", stored_path="/tmp/x.jpg", sha256="x", page_number=1, metadata_confirmed=True)
        session.add(batch)
        session.flush()
        run = ExtractionRun(batch_id=batch.id, model="new")
        session.add_all([page, run])
        session.flush()
        first = FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Surname", normalized_value="Deferred", model_confidence="low", source_pass="full_page", row_legibility="partial", status="deferred")
        second = FieldCandidate(run_id=run.id, page=page, line_number=1, field_name="Given Name", normalized_value="Next", model_confidence="low", source_pass="full_page", row_legibility="partial")
        session.add_all([first, second])
        session.flush()
        assert session.scalar(queue_query(batch.id)).id == second.id
        assert session.scalar(queue_query(batch.id, exclude_candidate_id=second.id)).id == first.id


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

        run.status = "completed"
        from datetime import datetime, timedelta
        run.started_at = datetime.utcnow()
        run.finished_at = run.started_at + timedelta(seconds=125)
        completed = run_snapshot(session, run)
        assert completed["terminal"] is True
        assert completed["step_elapsed_seconds"] is None
        assert completed["elapsed_seconds"] == 125


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


def test_new_calibration_band_counter_starts_from_zero(tmp_path, monkeypatch):
    _workbench(tmp_path, monkeypatch)
    from workbench.db import CalibrationBand
    band = CalibrationBand(census_year=1950, field_name="Race", confidence="high")
    matches = [True, True, False]
    band.total = (band.total or 0) + len(matches)
    band.correct = (band.correct or 0) + sum(matches)
    assert (band.total, band.correct) == (3, 2)


def test_row_crop_converts_rgba_upload_to_jpeg(tmp_path, monkeypatch):
    _workbench(tmp_path, monkeypatch)
    from workbench.schema import get_schema
    from workbench.web import render_row_crop
    image_path = tmp_path / "rgba-disguised-as-jpg.jpg"
    Image.new("RGBA", (200, 300), (255, 255, 255, 180)).save(image_path, format="PNG")
    crop = render_row_crop(str(image_path), 1, get_schema(1950))
    assert crop[:2] == b"\xff\xd8"
    focused = render_row_crop(str(image_path), 1, get_schema(1950), "Surname")
    with Image.open(BytesIO(crop)) as row_image, Image.open(BytesIO(focused)) as focused_image:
        assert focused_image.width < row_image.width

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from models import FIELD_TO_GT_COLUMN_1950
from validate import validate_records

from .config import (EXPORTS_DIR, MIN_CALIBRATION_SAMPLES, PRECISION_TARGET,
                     RUNS_DIR, SAMPLE_RATE, STORAGE_DIR)
from .db import (Batch, CalibrationBand, Export, ExtractionRun, FieldCandidate,
                 Page, ReviewDecision)
from .schema import get_schema

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
FINAL_STATUSES = {"confirmed", "corrected", "unreadable"}
QUEUE_STATUSES = {"review_required", "sample_review", "deferred"}


def parse_image_hint(filename: str) -> tuple[int | None, str]:
    """Return a conservative page-number hint and whether this looks like a cover."""
    stem = Path(filename).stem.casefold()
    kind = "cover" if "cover" in stem else "census"
    match = re.search(r"(?:sheet|page|image|cover)[_\- ]*(\d+)", stem)
    return (int(match.group(1)) if match else None), kind


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_files(session: Session, batch: Batch, uploads: list) -> list[Page]:
    """Copy uploads into immutable local storage and create confirmable pages."""
    stored_pages: list[Page] = []
    batch_dir = STORAGE_DIR / f"batch_{batch.id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    seen_hashes = {p.sha256 for p in batch.pages}
    for sequence, upload in enumerate(uploads, start=1):
        filename = Path(upload.filename or "").name
        suffix = Path(filename).suffix.casefold()
        if not filename or suffix not in IMAGE_EXTENSIONS:
            page = Page(batch_id=batch.id, original_filename=filename or "unnamed", stored_path="", sha256="", import_error="Unsupported or missing image file")
            session.add(page)
            stored_pages.append(page)
            continue
        destination = batch_dir / f"{sequence:04d}_{filename}"
        with destination.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        digest = sha256_file(destination)
        hint, kind = parse_image_hint(filename)
        if digest in seen_hashes:
            destination.unlink(missing_ok=True)
            page = Page(batch_id=batch.id, original_filename=filename, stored_path="", sha256=digest, import_error="Duplicate image hash in this batch")
        else:
            seen_hashes.add(digest)
            page = Page(
                batch_id=batch.id, original_filename=filename, stored_path=str(destination),
                sha256=digest, page_number=hint, kind=kind,
            )
        session.add(page)
        stored_pages.append(page)
    session.flush()
    return stored_pages


def update_page_manifest(page: Page, page_number: int | None, kind: str) -> None:
    if kind not in {"census", "cover"}:
        raise ValueError("Page type must be census or cover.")
    if kind == "census" and (page_number is None or page_number < 1):
        raise ValueError("Census pages require a positive physical page number.")
    page.page_number = page_number
    page.kind = kind
    page.metadata_confirmed = not bool(page.import_error)


def batch_ready(batch: Batch) -> tuple[bool, str | None]:
    pages = batch.pages
    if not pages:
        return False, "Import at least one scan image."
    if any(p.import_error for p in pages):
        return False, "Resolve import errors before extraction."
    if any(not p.metadata_confirmed for p in pages):
        return False, "Confirm page order and type for every imported file."
    census = [p for p in pages if p.kind == "census"]
    if not census:
        return False, "Mark at least one file as a census page."
    numbers = [p.page_number for p in census]
    if len(numbers) != len(set(numbers)):
        return False, "Census page numbers must be unique within a batch."
    return True, None


def priority_for(candidate: dict, warnings: list[str], year: int) -> int:
    value = candidate.get("normalized_value")
    if value in (None, "") or candidate.get("row_legibility") == "illegible":
        return 0
    if candidate["field"] in {"Race", "Gender"}:
        return 1
    if warnings:
        return 2
    if candidate.get("conflict"):
        return 3
    return 4


def calibration_allows(session: Session, year: int, field: str, confidence: str) -> bool:
    band = session.scalar(select(CalibrationBand).where(
        CalibrationBand.census_year == year,
        CalibrationBand.field_name == field,
        CalibrationBand.confidence == confidence,
    ))
    return bool(band and band.total >= MIN_CALIBRATION_SAMPLES and band.precision >= PRECISION_TARGET)


def deterministic_sample(page_id: int, line: int, field: str) -> bool:
    value = hashlib.sha256(f"{page_id}:{line}:{field}".encode()).digest()
    return int.from_bytes(value[:4], "big") / (2 ** 32) < SAMPLE_RATE


def persist_extraction(session: Session, run: ExtractionRun, page: Page, payload: dict) -> None:
    """Persist a run payload without overwriting an earlier draft or decision."""
    year = page.batch.census_year
    records = payload["records"]
    warnings_by_line = {
        int(record.get("Line Number")): record.get("_warnings", [])
        for record in validate_records([dict(record) for record in records], year)
        if record.get("Line Number") is not None
    }
    for item in payload.get("field_candidates", []):
        line = int(item["line_number"])
        reasons = list(item.get("reasons", []))
        validation_warnings = warnings_by_line.get(line, [])
        warnings = reasons + validation_warnings
        can_auto_accept = (
            item.get("model_confidence") == "high"
            and item.get("row_legibility") == "clear"
            and not item.get("conflict")
            and not warnings
            and item.get("normalized_value") not in (None, "")
            and calibration_allows(session, year, item["field"], "high")
        )
        sampled = can_auto_accept and deterministic_sample(page.id, line, item["field"])
        status = "sample_review" if sampled else ("auto_accepted" if can_auto_accept else "review_required")
        session.add(FieldCandidate(
            run_id=run.id, page_id=page.id, line_number=line, field_name=item["field"],
            raw_value=_text_or_none(item.get("raw_value")),
            normalized_value=_text_or_none(item.get("normalized_value")),
            model_confidence=item.get("model_confidence", "low"), source_pass=item.get("source_pass", "unknown"),
            row_legibility=item.get("row_legibility", "partial"), has_conflict=bool(item.get("conflict")),
            reasons=reasons, validation_warnings=validation_warnings, evidence=item.get("evidence", {}),
            priority=priority_for(item, warnings, year), status=status,
            auto_accepted=can_auto_accept, qc_sampled=sampled,
        ))
    session.flush()


def _text_or_none(value) -> str | None:
    return None if value is None else str(value)


def latest_candidate_run_id_query(batch_id: int):
    """Return the newest extraction run that actually produced review data."""
    return (select(func.max(FieldCandidate.run_id))
        .join(Page, Page.id == FieldCandidate.page_id)
        .where(Page.batch_id == batch_id)
        .scalar_subquery())


def queue_query(batch_id: int, exclude_candidate_id: int | None = None):
    latest_run_id = latest_candidate_run_id_query(batch_id)
    query = (select(FieldCandidate)
        .join(Page)
        .where(
            Page.batch_id == batch_id,
            FieldCandidate.run_id == latest_run_id,
            FieldCandidate.status.in_(QUEUE_STATUSES),
        )
        .order_by(
            case((FieldCandidate.status == "deferred", 1), else_=0),
            FieldCandidate.priority,
            Page.page_number,
            FieldCandidate.line_number,
            FieldCandidate.field_name,
        ))
    if exclude_candidate_id is not None:
        query = query.where(FieldCandidate.id != exclude_candidate_id)
    return query


def review_queue_count(session: Session, batch_id: int) -> int:
    return session.scalar(select(func.count()).select_from(queue_query(batch_id).subquery())) or 0


def apply_decision(session: Session, candidate: FieldCandidate, reviewer: str, action: str,
                   value: str | None, rationale: str | None) -> ReviewDecision:
    if not reviewer.strip():
        raise ValueError("Reviewer name or initials are required.")
    if action not in {"confirmed", "corrected", "unreadable", "deferred"}:
        raise ValueError("Invalid review action.")
    if action == "corrected" and not (value or "").strip():
        raise ValueError("A corrected value is required.")
    final_value = None if action == "unreadable" else (value.strip() if action == "corrected" else candidate.normalized_value)
    decision = ReviewDecision(
        candidate_id=candidate.id, reviewer=reviewer.strip(), action=action,
        previous_value=candidate.normalized_value, value=final_value,
        rationale=(rationale or "").strip() or None,
    )
    candidate.status = action
    session.add(decision)
    session.flush()
    return decision


def canonical_value(candidate: FieldCandidate) -> str | None:
    decisions = sorted(candidate.decisions, key=lambda d: d.created_at)
    if decisions and decisions[-1].action in FINAL_STATUSES:
        return decisions[-1].value
    if candidate.status == "auto_accepted":
        return candidate.normalized_value
    return None


def create_export(session: Session, batch: Batch) -> Export:
    schema = get_schema(batch.census_year)
    version = (session.scalar(select(Export.version).where(Export.batch_id == batch.id).order_by(Export.version.desc())) or 0) + 1
    target = EXPORTS_DIR / f"batch_{batch.id}" / f"v{version:03d}"
    target.mkdir(parents=True, exist_ok=False)
    latest_run_id = session.scalar(select(func.max(FieldCandidate.run_id)).join(Page).where(Page.batch_id == batch.id))
    pages = session.scalars(select(Page).where(Page.batch_id == batch.id).options(joinedload(Page.candidates).joinedload(FieldCandidate.decisions)).order_by(Page.page_number)).unique().all()
    rows: list[dict] = []
    audit: list[dict] = []
    unresolved = 0
    for page in pages:
        if page.kind != "census":
            continue
        by_line: dict[int, dict] = defaultdict(dict)
        current_candidates = (candidate for candidate in page.candidates if candidate.run_id == latest_run_id)
        for candidate in sorted(current_candidates, key=lambda c: (c.line_number, c.field_name)):
            value = canonical_value(candidate)
            if candidate.status not in FINAL_STATUSES and candidate.status != "auto_accepted":
                unresolved += 1
            by_line[candidate.line_number][candidate.field_name] = value
            audit.append({
                "run_id": candidate.run_id, "page_id": page.id, "page_number": page.page_number, "source_image": page.original_filename,
                "line_number": candidate.line_number, "field": candidate.field_name,
                "raw_value": candidate.raw_value, "ai_normalized_value": candidate.normalized_value,
                "canonical_value": value, "status": candidate.status, "confidence": candidate.model_confidence,
                "warnings": candidate.validation_warnings, "decisions": [
                    {"reviewer": d.reviewer, "action": d.action, "value": d.value, "rationale": d.rationale,
                     "at": d.created_at.isoformat()} for d in candidate.decisions
                ],
            })
        for line, values in sorted(by_line.items()):
            row = {"Page Number": page.page_number, "Line Number": line}
            row.update({field: values.get(field) for field in schema.fields})
            rows.append(row)
    pd.DataFrame(rows).to_csv(target / "reviewed_records.csv", index=False)
    pd.DataFrame(rows).to_excel(target / "reviewed_records.xlsx", index=False)
    (target / "reviewed_records.json").write_text(json.dumps(rows, indent=2, default=str))
    summary = {"batch_id": batch.id, "extraction_run_id": latest_run_id, "version": version, "created_at": datetime.utcnow().isoformat(), "rows": len(rows), "unresolved_fields": unresolved, "schema_year": batch.census_year}
    (target / "audit.json").write_text(json.dumps({"summary": summary, "fields": audit}, indent=2, default=str))
    export = Export(batch_id=batch.id, version=version, directory=str(target), summary=summary)
    session.add(export)
    session.flush()
    return export

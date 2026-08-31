from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from extract import DEFAULT_MODEL, extract_with_review_data

from .config import RUNS_DIR
from .db import ExtractionRun, FieldCandidate, Page, SessionLocal, init_db
from .progress import classify_failure, progress_data, update_progress
from .services import persist_extraction


class RunCancelled(Exception):
    pass


def recover_interrupted_runs() -> int:
    """Re-queue work left in ``running`` state by a worker restart/crash."""
    with SessionLocal() as session:
        runs = session.scalars(select(ExtractionRun).where(ExtractionRun.status == "running")).all()
        for run in runs:
            run.status = "queued"
            update_progress(
                run,
                phase="queued",
                message="Worker restarted; safely resuming from the last committed page",
                event={"type": "worker_recovery", "message": "Interrupted run re-queued"},
            )
        session.commit()
        return len(runs)


def process_next_run() -> bool:
    """Claim and process one queued batch; safe for the single local worker."""
    with SessionLocal() as session:
        run = session.scalar(select(ExtractionRun).where(ExtractionRun.status == "queued").order_by(ExtractionRun.created_at))
        if run is None:
            return False
        run.status, run.started_at = "running", datetime.utcnow()
        total_pages = session.scalar(select(func.count(Page.id)).where(
            Page.batch_id == run.batch_id, Page.kind == "census"
        )) or 0
        completed_pages = session.scalar(select(func.count(func.distinct(FieldCandidate.page_id))).where(
            FieldCandidate.run_id == run.id
        )) or 0
        prior = progress_data(run)
        update_progress(
            run,
            phase="starting",
            message="Worker claimed this run and is preparing the next page",
            total_pages=total_pages,
            completed_pages=completed_pages,
            job_attempt=int(prior.get("job_attempt") or 0) + 1,
            current_page=None,
            current_filename=None,
            event={"type": "run_started", "message": "Worker started processing"},
        )
        session.commit()
        run_id, batch_id, model = run.id, run.batch_id, run.model
    try:
        with SessionLocal() as session:
            run = session.get(ExtractionRun, run_id)
            pages = session.scalars(select(Page).where(Page.batch_id == batch_id, Page.kind == "census").options(joinedload(Page.batch)).order_by(Page.page_number)).all()
            completed_pages = session.scalar(select(func.count(func.distinct(FieldCandidate.page_id))).where(
                FieldCandidate.run_id == run.id
            )) or 0
            for page in pages:
                # A resumed run preserves candidates already committed before a
                # transient API failure, rather than duplicating field tasks.
                if session.scalar(select(func.count(FieldCandidate.id)).where(
                    FieldCandidate.run_id == run.id, FieldCandidate.page_id == page.id
                )):
                    continue

                update_progress(
                    run,
                    phase="preparing_page",
                    message=f"Preparing page {page.page_number} of {len(pages)}",
                    current_page=page.page_number,
                    current_filename=page.original_filename,
                    current_pass="preprocessing",
                    api_attempt=None,
                    max_api_attempts=None,
                    event={"type": "page_started", "page": page.page_number,
                           "message": f"Started {page.original_filename}"},
                )
                session.commit()

                def on_extraction_event(event: dict) -> None:
                    session.refresh(run, attribute_names=["status"])
                    if run.status == "cancel_requested":
                        raise RunCancelled("Fast extraction requested")
                    current = progress_data(run)
                    retry_count = int(current.get("retry_count") or 0)
                    if event.get("type") == "api_error" and event.get("retry_in_seconds") is not None:
                        retry_count += 1
                    update_progress(
                        run,
                        phase="retry_wait" if event.get("retry_in_seconds") else "extracting",
                        message=event.get("message", "Extracting page"),
                        current_pass=event.get("source"),
                        api_attempt=event.get("attempt"),
                        max_api_attempts=event.get("max_attempts"),
                        retry_count=retry_count,
                        event=event,
                    )
                    session.commit()

                records, diagnostics, candidates = extract_with_review_data(
                    page.stored_path, page.batch.census_year, model=model,
                    event_callback=on_extraction_event,
                    use_crops=bool((run.config or {}).get("use_crops", True)),
                    expected_lines=int((run.config or {}).get("expected_lines", 30)),
                    strategy=(run.config or {}).get("strategy", "adaptive_full_page"),
                    n_blocks=int((run.config or {}).get("n_blocks", 3)),
                    parallelism=int((run.config or {}).get("parallelism", 3)),
                )
                payload = {"source_image": page.stored_path, "census_year": page.batch.census_year,
                           "model": model, "records": records, "diagnostics": diagnostics,
                           "field_candidates": candidates}
                output_path = RUNS_DIR / f"run_{run_id}" / f"page_{page.id}.json"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(payload, indent=2, default=str))
                persist_extraction(session, run, page, payload)
                completed_pages += 1
                update_progress(
                    run,
                    phase="page_completed",
                    message=f"Completed page {page.page_number} of {len(pages)}",
                    completed_pages=completed_pages,
                    current_pass=None,
                    api_attempt=None,
                    max_api_attempts=None,
                    event={"type": "page_completed", "page": page.page_number,
                           "message": f"Committed {len(candidates)} review fields"},
                )
                session.commit()
            run.status, run.finished_at = "completed", datetime.utcnow()
            update_progress(
                run,
                phase="completed",
                message=f"Completed all {len(pages)} census pages",
                completed_pages=len(pages),
                current_page=None,
                current_filename=None,
                current_pass=None,
                api_attempt=None,
                max_api_attempts=None,
                event={"type": "run_completed", "message": "Extraction run completed"},
            )
            session.commit()
    except RunCancelled:
        with SessionLocal() as session:
            run = session.get(ExtractionRun, run_id)
            run.status, run.error, run.finished_at = "cancelled", None, datetime.utcnow()
            update_progress(
                run, phase="cancelled",
                message="Stopped safely; the fast replacement run is next",
                event={"type": "run_cancelled", "message": "Old extraction stopped safely"},
            )
            session.commit()
        return True
    except Exception as exc:
        with SessionLocal() as session:
            run = session.get(ExtractionRun, run_id)
            run.status, run.error, run.finished_at = "failed", str(exc), datetime.utcnow()
            failure = classify_failure(str(exc))
            update_progress(
                run,
                phase="failed",
                message=failure["message"],
                event={"type": "run_failed", "error": str(exc)[:500],
                       "message": "Run paused; completed pages are preserved"},
            )
            session.commit()
        return True
    return True


def main() -> None:
    init_db()
    recovered = recover_interrupted_runs()
    print(f"Census review worker started (recovered {recovered} interrupted run(s))")
    while True:
        if not process_next_run():
            time.sleep(2)


if __name__ == "__main__":
    main()

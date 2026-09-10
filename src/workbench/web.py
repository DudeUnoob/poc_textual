from __future__ import annotations

import mimetypes
import os
import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.base import BaseHTTPMiddleware

from census_schemas import SUPPORTED_YEARS, workbook_filename, workbook_sheets
from paths import GROUND_TRUTH_DIR
from .access import (attach_principal, attach_response_cookies, current_principal,
                     enforce, supabase_mode, require_admin, require_principal,
                     reviewer_identity, submitted_csrf_token, template_context)
from .db import (Batch, Export, ExtractionRun, FieldCandidate, Page,
                 SessionLocal, init_db, utc_now)
from .leases import ReviewLease
from .progress import now_iso, run_snapshot, update_progress
from .schema import get_schema
from .services import (QUEUE_STATUSES, apply_decision, batch_ready, create_export,
                       import_files, queue_query, review_queue_count,
                       review_row_count, update_page_manifest)
from .cloud.api import router as shared_router
from .cloud.portal import router as shared_portal_router

TEMPLATES = Jinja2Templates(directory=str(__file__.replace("web.py", "templates")))
STATIC_DIR = __file__.replace("web.py", "static")
from model_config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MODEL, thinking_level
TEMPLATES.env.globals["default_model"] = DEFAULT_MODEL


def extraction_config(total_pages: int, schema) -> dict:
    timestamp = now_iso()
    has_calibrated_geometry = bool(schema.field_bounds)
    return {
        "model": DEFAULT_MODEL,
        "thinking_level": thinking_level(),
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "use_crops": has_calibrated_geometry,
        "expected_lines": schema.expected_lines,
        "schedule_type": schema.schedule_type,
        "sheet_name": getattr(schema, "sheet_name", None),
        "strategy": "row_blocks" if has_calibrated_geometry else "adaptive_full_page",
        "n_blocks": 3, "parallelism": 3,
        "progress": {
            "phase": "queued", "message": "Waiting for the local extraction worker",
            "total_pages": total_pages, "completed_pages": 0,
            "retry_count": 0, "job_attempt": 0, "last_update": timestamp,
            "events": [{"type": "run_queued", "message": "Fast row-band run added to the worker queue", "at": timestamp}],
        },
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Census Review Workbench", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(shared_router)
app.include_router(shared_portal_router)


class AccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        attach_principal(request)
        csrf_token = None
        if supabase_mode() and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_token = await submitted_csrf_token(request)
        blocked = enforce(request, csrf_token)
        if blocked is not None:
            return blocked
        response = await call_next(request)
        return attach_response_cookies(request, response)


app.add_middleware(AccessMiddleware)


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200):
    return TEMPLATES.TemplateResponse(
        request, template, template_context(request, context), status_code=status_code,
    )


def schema_for_batch(batch: Batch):
    try:
        return get_schema(batch.census_year, batch.schedule_type, batch.ground_truth_sheet)
    except TypeError:
        return get_schema(batch.census_year, batch.schedule_type)


def enforce_extraction_budget(page_count: int, schema=None) -> None:
    try:
        from .budgets import (
            extraction_enabled,
            max_model_calls_per_run,
            max_pages_per_run,
        )
    except ImportError:
        return
    if not extraction_enabled():
        raise HTTPException(
            status_code=403,
            detail="Extraction is disabled until an administrator configures a model budget.",
        )
    limit = max_pages_per_run()
    if page_count > limit:
        raise HTTPException(
            status_code=400,
            detail=f"This batch has {page_count} pages; the configured limit is {limit}.",
        )
    estimated_calls = page_count * (4 if schema and schema.field_bounds else 1)
    try:
        max_model_calls_per_run(estimated_calls)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def page_or_404(session: Session, page_id: int) -> Page:
    page = session.get(Page, page_id)
    if not page or not page.stored_path:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


def render_row_crop(image_path: str, line: int, schema, field: str | None = None) -> bytes:
    """Render browser-safe JPEG evidence even when the uploaded file is RGBA PNG data."""
    with Image.open(image_path) as image:
        width, height = image.size
        top = schema.data_top + (schema.data_bottom - schema.data_top) * (line - 1) / schema.expected_lines
        bottom = schema.data_top + (schema.data_bottom - schema.data_top) * line / schema.expected_lines
        padding = int(height * 0.01)
        left, right = schema.field_bounds.get(field, schema.row_bounds)
        horizontal_padding = 0.012 if field else 0
        crop = image.crop((
            max(0, int(width * (left - horizontal_padding))),
            max(0, int(height * top) - padding),
            min(width, int(width * (right + horizontal_padding))),
            min(height, int(height * bottom) + padding),
        ))
        if crop.mode != "RGB":
            crop = crop.convert("RGB")
        buffer = BytesIO()
        crop.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


@app.get("/")
def dashboard(request: Request):
    if supabase_mode():
        return RedirectResponse("/shared", status_code=303)
    with SessionLocal() as session:
        batches = session.scalars(select(Batch).options(joinedload(Batch.pages)).order_by(Batch.created_at.desc())).unique().all()
        counts = {batch.id: review_row_count(session, batch.id) for batch in batches}
        summaries = {}
        for batch in batches:
            latest_run = session.scalar(select(ExtractionRun).where(ExtractionRun.batch_id == batch.id).order_by(ExtractionRun.created_at.desc()))
            ready, _ = batch_ready(batch)
            census_pages = sum(page.kind == "census" and not page.import_error for page in batch.pages)
            summaries[batch.id] = {
                "pages": census_pages,
                "ready": ready,
                "run_status": latest_run.status if latest_run else None,
                "next_step": (
                    f"Review {counts[batch.id]} flagged rows" if counts[batch.id]
                    else "Resume the paused extraction" if latest_run and latest_run.status == "failed"
                    else "Extraction is in progress" if latest_run and latest_run.status in {"queued", "running"}
                    else "Ready to start extraction" if ready and not latest_run
                    else "Confirm the page manifest" if not ready
                    else "No review items are waiting"
                ),
            }
        return render(request, "dashboard.html", {
            "batches": batches,
            "counts": counts,
            "summaries": summaries,
            "supported_years": SUPPORTED_YEARS,
        })


@app.post("/batches")
def create_batch(
    request: Request,
    name: str = Form(...), county: str = Form(...), state: str = Form(...),
    census_year: int = Form(...), enumeration_district: str = Form(...),
    schedule_type: str = Form("population"),
    source_reference: str | None = Form(None), ground_truth_path: str | None = Form(None),
    ground_truth_sheet: str | None = Form(None), ground_truth_page: str | None = Form(None),
    files: list[UploadFile] = File(...),
):
    ground_truth_sheet = (ground_truth_sheet or "").strip() or None
    try:
        get_schema(census_year, schedule_type, ground_truth_sheet)
    except TypeError:
        get_schema(census_year, schedule_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        parsed_ground_truth_page = int(ground_truth_page) if (ground_truth_page or "").strip() else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Starting physical page must be a whole number when provided.")
    if ground_truth_sheet and not ground_truth_path:
        ground_truth_path = str(GROUND_TRUTH_DIR / workbook_filename(census_year))
    with SessionLocal() as session:
        batch = Batch(name=name.strip(), county=county.strip(), state=state.strip(),
                      census_year=census_year, schedule_type=schedule_type,
                      enumeration_district=enumeration_district.strip(), source_reference=source_reference or None,
                      ground_truth_path=ground_truth_path or None, ground_truth_sheet=ground_truth_sheet or None,
                      ground_truth_page=parsed_ground_truth_page)
        session.add(batch)
        session.flush()
        try:
            import_files(session, batch, files)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return RedirectResponse(f"/batches/{batch.id}", status_code=303)


@app.get("/batches/{batch_id}")
def batch_detail(request: Request, batch_id: int):
    with SessionLocal() as session:
        batch = session.scalar(select(Batch).where(Batch.id == batch_id).options(joinedload(Batch.pages)))
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        ready, reason = batch_ready(batch)
        runs = session.scalars(select(ExtractionRun).where(ExtractionRun.batch_id == batch_id).order_by(ExtractionRun.created_at.desc())).all()
        run_snapshots = {run.id: run_snapshot(session, run) for run in runs}
        queue_count = review_queue_count(session, batch_id)
        row_count = review_row_count(session, batch_id)
        return render(request, "batch.html", {"batch": batch, "ready": ready, "reason": reason, "runs": runs, "run_snapshots": run_snapshots, "queue_count": queue_count, "row_count": row_count})


@app.post("/pages/{page_id}/manifest")
def confirm_page(page_id: int, page_number: str | None = Form(None), kind: str = Form(...)):
    try:
        parsed_page_number = int(page_number) if (page_number or "").strip() else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Physical page must be a whole number when provided.")
    with SessionLocal() as session:
        page = session.get(Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        try:
            update_page_manifest(page, parsed_page_number, kind)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        session.commit()
        return RedirectResponse(f"/batches/{page.batch_id}", status_code=303)


@app.post("/pages/{page_id}/remove-error")
def remove_import_error(page_id: int):
    """Remove only a rejected import row; original accepted scans are immutable."""
    with SessionLocal() as session:
        page = session.get(Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        if not page.import_error:
            raise HTTPException(status_code=400, detail="Only rejected import rows can be removed here.")
        batch_id = page.batch_id
        session.delete(page)
        session.commit()
        return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.post("/batches/{batch_id}/runs")
def queue_run(batch_id: int):
    with SessionLocal() as session:
        batch = session.scalar(select(Batch).where(Batch.id == batch_id).options(joinedload(Batch.pages)))
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        ready, reason = batch_ready(batch)
        if not ready:
            raise HTTPException(status_code=400, detail=reason)
        total_pages = sum(1 for page in batch.pages if page.kind == "census")
        schema = schema_for_batch(batch)
        enforce_extraction_budget(total_pages, schema)
        run = ExtractionRun(
            batch_id=batch.id, model=DEFAULT_MODEL,
            config=extraction_config(total_pages, schema),
        )
        session.add(run)
        session.commit()
        return RedirectResponse(f"/batches/{batch.id}", status_code=303)


@app.post("/runs/{run_id}/resume")
def resume_run(run_id: int):
    with SessionLocal() as session:
        run = session.get(ExtractionRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Extraction run not found")
        if run.status != "failed":
            raise HTTPException(status_code=400, detail="Only failed runs can be resumed.")
        run.status, run.error = "queued", None
        update_progress(
            run,
            phase="queued",
            message="Resume requested; waiting for the worker. Completed pages will be skipped.",
            event={"type": "resume_queued", "message": "Resume requested by reviewer"},
        )
        session.commit()
        return RedirectResponse(f"/batches/{run.batch_id}", status_code=303)


@app.post("/runs/{run_id}/switch-to-fast")
def switch_to_fast_run(run_id: int):
    """Preserve the old audit run and queue a calibrated low-latency extraction."""
    with SessionLocal() as session:
        run = session.get(ExtractionRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Extraction run not found")
        if run.status not in {"queued", "running", "failed"}:
            raise HTTPException(status_code=400, detail="This run can no longer be switched.")
        duplicate = session.scalar(select(ExtractionRun).where(
            ExtractionRun.batch_id == run.batch_id,
            ExtractionRun.status.in_({"queued", "running"}),
            ExtractionRun.id != run.id,
            ExtractionRun.model == DEFAULT_MODEL,
        ))
        if duplicate:
            raise HTTPException(status_code=400, detail="A fast extraction run is already queued.")
        if run.status == "running":
            run.status = "cancel_requested"
            update_progress(
                run, phase="cancel_requested",
                message="Finishing the current API call, then switching to fast extraction",
                event={"type": "cancel_requested", "message": "Reviewer requested fast extraction"},
            )
        else:
            run.status, run.finished_at = "cancelled", utc_now()
        total_pages = session.scalar(select(func.count(Page.id)).where(
            Page.batch_id == run.batch_id, Page.kind == "census"
        )) or 0
        batch = session.get(Batch, run.batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        schema = schema_for_batch(batch)
        enforce_extraction_budget(total_pages, schema)
        replacement = ExtractionRun(
            batch_id=run.batch_id, model=DEFAULT_MODEL,
            config=extraction_config(total_pages, schema),
        )
        session.add(replacement)
        session.commit()
        return RedirectResponse(f"/batches/{run.batch_id}", status_code=303)


@app.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int):
    """Request a checkpoint-safe stop without deleting completed output."""
    with SessionLocal() as session:
        run = session.get(ExtractionRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Extraction run not found")
        if run.status not in {"queued", "running"}:
            raise HTTPException(status_code=400, detail="Only queued or running jobs can be stopped.")
        if run.status == "running":
            run.status = "cancel_requested"
            update_progress(
                run, phase="cancel_requested",
                message="Stopping after the current API call; completed pages are preserved",
                event={"type": "cancel_requested", "message": "Reviewer requested a safe stop"},
            )
        else:
            run.status, run.finished_at = "cancelled", utc_now()
        batch_id = run.batch_id
        session.commit()
        return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.get("/runs/{run_id}/status")
def run_status(run_id: int):
    with SessionLocal() as session:
        run = session.get(ExtractionRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Extraction run not found")
        return run_snapshot(session, run)


@app.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(request: Request, run_id: int):
    """Stream durable snapshots; reconnecting browsers lose no progress."""
    previous: dict | None = None
    event_id = 0
    while not await request.is_disconnected():
        if supabase_mode():
            attach_principal(request)
            if current_principal(request) is None:
                raise HTTPException(status_code=401, detail="Authentication required")
        with SessionLocal() as session:
            run = session.get(ExtractionRun, run_id)
            if not run:
                raise HTTPException(status_code=404, detail="Extraction run not found")
            snapshot = run_snapshot(session, run)
        if snapshot != previous:
            event_id += 1
            yield ServerSentEvent(data=snapshot, id=str(event_id), retry=2000)
            previous = snapshot
        if snapshot["terminal"]:
            break
        await asyncio.sleep(1)


@app.get("/pages/{page_id}/image")
def full_image(page_id: int):
    with SessionLocal() as session:
        page = page_or_404(session, page_id)
        return FileResponse(page.stored_path, media_type=mimetypes.guess_type(page.stored_path)[0] or "image/jpeg")


@app.get("/pages/{page_id}/crop")
def row_crop(page_id: int, line: int, field: str | None = None):
    with SessionLocal() as session:
        page = page_or_404(session, page_id)
        schema = schema_for_batch(page.batch)
        if not schema.field_bounds:
            return FileResponse(page.stored_path)
        if not 1 <= line <= schema.expected_lines:
            raise HTTPException(status_code=400, detail="Line outside page layout")
        return Response(render_row_crop(page.stored_path, line, schema, field), media_type="image/jpeg")


@app.get("/review/next")
def review_next(request: Request, batch_id: int, skip: int | None = None):
    with SessionLocal() as session:
        candidate = session.scalar(queue_query(batch_id, exclude_candidate_id=skip).options(joinedload(FieldCandidate.page).joinedload(Page.batch)))
        if not candidate:
            batch = session.get(Batch, batch_id)
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            return render(request, "queue_empty.html", {"batch": batch})
        line_fields = session.scalars(select(FieldCandidate).where(
            FieldCandidate.run_id == candidate.run_id,
            FieldCandidate.page_id == candidate.page_id,
            FieldCandidate.line_number == candidate.line_number,
        ).order_by(FieldCandidate.field_name)).all()
        queue_count = review_queue_count(session, batch_id)
        review_reasons = list(dict.fromkeys([*candidate.reasons, *candidate.validation_warnings]))
        return render(request, "review.html", {"candidate": candidate, "page": candidate.page, "batch": candidate.page.batch, "line_fields": line_fields, "queue_count": queue_count, "review_reasons": review_reasons, "geometry_validated": bool(schema_for_batch(candidate.page.batch).field_bounds)})


@app.get("/review/rows/next")
def review_row_next(request: Request, batch_id: int, skip_page_id: int | None = None,
                    skip_line: int | None = None):
    """Review all risky fields on one visible census row in one action."""
    with SessionLocal() as session:
        exclude_row = (
            (skip_page_id, skip_line)
            if skip_page_id is not None and skip_line is not None else None
        )
        candidate = session.scalar(
            queue_query(batch_id, exclude_row=exclude_row)
            .options(joinedload(FieldCandidate.page).joinedload(Page.batch))
        )
        if not candidate:
            batch = session.get(Batch, batch_id)
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            return render(request, "queue_empty.html", {"batch": batch})
        row_candidates = session.scalars(select(FieldCandidate).where(
            FieldCandidate.run_id == candidate.run_id,
            FieldCandidate.page_id == candidate.page_id,
            FieldCandidate.line_number == candidate.line_number,
            FieldCandidate.status.in_(QUEUE_STATUSES),
        ).order_by(FieldCandidate.priority, FieldCandidate.field_name)).all()
        all_fields = session.scalars(select(FieldCandidate).where(
            FieldCandidate.run_id == candidate.run_id,
            FieldCandidate.page_id == candidate.page_id,
            FieldCandidate.line_number == candidate.line_number,
        ).order_by(FieldCandidate.field_name)).all()
        reasons = {
            item.id: list(dict.fromkeys([*item.reasons, *item.validation_warnings]))
            for item in row_candidates
        }
        lease = None
        if supabase_mode():
            from .leases import LeaseError, acquire_lease, review_session_id, row_key
            principal = require_principal(request)
            try:
                lease = acquire_lease(
                    session,
                    row_key(candidate.run_id, candidate.page_id, candidate.line_number),
                    principal.uid,
                    review_session_id(request),
                )
                session.commit()
            except LeaseError as exc:
                batch = candidate.page.batch
                return render(request, "review_busy.html", {
                    "batch": batch,
                    "message": str(exc),
                }, status_code=409)
        return render(request, "review_row.html", {
            "page": candidate.page, "batch": candidate.page.batch,
            "line_number": candidate.line_number, "candidates": row_candidates,
            "all_fields": all_fields, "reasons": reasons,
            "row_count": review_row_count(session, batch_id),
            "field_count": review_queue_count(session, batch_id),
            "geometry_validated": bool(schema_for_batch(candidate.page.batch).field_bounds),
            "lease": lease,
        })


@app.post("/review/rows/{run_id}/{page_id}/{line_number}")
async def decide_row(request: Request, run_id: int, page_id: int, line_number: int):
    form = await request.form()
    reviewer = reviewer_identity(request, str(form.get("reviewer") or ""))
    rationale = str(form.get("rationale") or "") or None
    try:
        candidate_ids = [int(value) for value in form.getlist("candidate_id")]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid row review items")
    with SessionLocal() as session:
        candidates = session.scalars(select(FieldCandidate).where(
            FieldCandidate.id.in_(candidate_ids),
            FieldCandidate.run_id == run_id,
            FieldCandidate.page_id == page_id,
            FieldCandidate.line_number == line_number,
            FieldCandidate.status.in_(QUEUE_STATUSES),
        )).all()
        if len(candidates) != len(set(candidate_ids)):
            raise HTTPException(status_code=400, detail="One or more row fields are no longer reviewable")
        if supabase_mode():
            from .leases import LeaseError, require_lease, release_lease, review_session_id, row_key
            principal = require_principal(request)
            try:
                expected = int(form.get("expected_revision") or 0)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid row revision")
            try:
                held = require_lease(
                    session,
                    row_key(run_id, page_id, line_number),
                    principal.uid,
                    str(form.get("review_session") or review_session_id(request)),
                    str(form.get("lease_token") or ""),
                    expected,
                )
            except LeaseError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        else:
            held = None
        deferred = False
        try:
            for candidate in candidates:
                resolution = str(form.get(f"resolution_{candidate.id}") or "value")
                submitted = str(form.get(f"value_{candidate.id}") or "").strip()
                if resolution == "unreadable":
                    action, value = "unreadable", None
                elif resolution == "deferred":
                    action, value, deferred = "deferred", None, True
                elif resolution == "value":
                    from .services import current_value
                    current = (current_value(candidate) or "").strip()
                    action = "confirmed" if submitted == current else "corrected"
                    value = submitted if action == "corrected" else None
                else:
                    raise ValueError("Invalid row resolution")
                apply_decision(session, candidate, reviewer, action, value, rationale)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        page = session.get(Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        batch_id = page.batch_id
        if held is not None:
            from .leases import release_lease
            release_lease(session, held)
        session.commit()
        skip = f"&skip_page_id={page_id}&skip_line={line_number}" if deferred else ""
        return RedirectResponse(f"/review/rows/next?batch_id={batch_id}{skip}", status_code=303)


@app.post("/review/leases/{run_id}/{page_id}/{line_number}/renew")
def renew_review_lease(request: Request, run_id: int, page_id: int, line_number: int,
                       lease_token: str = Form(...), review_session: str = Form(...)):
    if not supabase_mode():
        return {"ok": True}
    from .leases import LeaseError, renew_lease, row_key
    principal = require_principal(request)
    with SessionLocal() as session:
        try:
            lease = renew_lease(
                session, row_key(run_id, page_id, line_number),
                principal.uid, review_session, lease_token,
            )
            session.commit()
        except LeaseError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return lease


@app.post("/candidates/{candidate_id}/decision")
def decide(request: Request, candidate_id: int, reviewer: str | None = Form(None), action: str = Form(...), value: str | None = Form(None), rationale: str | None = Form(None)):
    with SessionLocal() as session:
        candidate = session.get(FieldCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Review item not found")
        try:
            apply_decision(session, candidate, reviewer_identity(request, reviewer), action, value, rationale)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        batch_id = candidate.page.batch_id
        session.commit()
        skip = f"&skip={candidate.id}" if action == "deferred" else ""
        return RedirectResponse(f"/review/next?batch_id={batch_id}{skip}", status_code=303)


@app.post("/batches/{batch_id}/exports")
def export_batch(batch_id: int):
    with SessionLocal() as session:
        batch = session.get(Batch, batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        export = create_export(session, batch)
        session.commit()
        return RedirectResponse(f"/exports/{export.id}", status_code=303)


@app.get("/exports/{export_id}")
def export_detail(request: Request, export_id: int):
    with SessionLocal() as session:
        export = session.get(Export, export_id)
        if not export:
            raise HTTPException(status_code=404, detail="Export not found")
        return render(request, "export.html", {"export": export})


@app.get("/exports/{export_id}/{filename}")
def download_export(export_id: int, filename: str):
    if filename not in {"reviewed_records.csv", "reviewed_records.xlsx", "reviewed_records.json", "audit.json"}:
        raise HTTPException(status_code=404, detail="Unknown export file")
    with SessionLocal() as session:
        export = session.get(Export, export_id)
        if not export:
            raise HTTPException(status_code=404, detail="Export not found")
        path = (Path(export.directory) / filename).resolve()
        if not path.is_file() or path.parent != Path(export.directory).resolve():
            raise HTTPException(status_code=404, detail="Export file not found")
        return FileResponse(path, filename=filename)


@app.get("/health")
def health():
    return {"ok": True, "supabase": supabase_mode()}


@app.get("/api/workbook-sheets")
def available_workbook_sheets(year: int, schedule_type: str = "population"):
    try:
        return {
            "year": year,
            "schedule_type": schedule_type,
            "sheets": workbook_sheets(year, schedule_type),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/login")
def login_page(request: Request):
    if current_principal(request) is not None and request.query_params.get("recovery") != "1":
        return RedirectResponse("/shared" if supabase_mode() else "/", status_code=303)
    return render(request, "login.html", {})


@app.post("/session")
async def create_session(request: Request):
    from fastapi.responses import JSONResponse
    from .auth import AuthError, establish_session
    from .cloud.repository import CloudError

    if not supabase_mode():
        raise HTTPException(status_code=400, detail="Sessions are only used in the shared deployment.")
    payload = await request.json()
    response = RedirectResponse("/shared", status_code=303)
    try:
        establish_session(request, response, payload.get("idToken") or payload.get("id_token") or "")
    except (AuthError, CloudError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=getattr(exc, "status", 401))
    return response


@app.post("/logout")
def logout(request: Request):
    response = RedirectResponse("/login" if supabase_mode() else "/", status_code=303)
    from .auth import clear_session
    try:
        from .settings import session_cookie_name
        from .supabase_client import create_admin_client
        token = request.cookies.get(session_cookie_name())
        if supabase_mode() and token:
            create_admin_client().auth.admin.sign_out(token, scope="local")
    except Exception:
        # Local cookies must clear even when the identity provider is unavailable.
        pass
    finally:
        clear_session(response)
    return response


@app.get("/admin")
def admin_home(request: Request):
    require_admin(request)
    if not supabase_mode():
        return render(request, "admin.html", {
            "members": [],
            "notice": "Membership administration is available in the shared Supabase deployment.",
        })
    from uuid import uuid4
    from .cloud.repository import CloudRepository
    from .cloud.api import get_repository
    members = get_repository().list("members")
    notice = None
    if request.query_params.get("revocation") == "pending":
        notice = (
            "Application access is disabled. Supabase session revocation is "
            "pending operator reconciliation."
        )
    return render(request, "admin.html", {
        "members": members,
        "operation_ids": {member["uid"]: str(uuid4()) for member in members},
        "notice": notice,
    })


@app.post("/admin/members/{uid}")
def admin_update_member(
    request: Request,
    uid: str,
    role: str = Form(...),
    disabled: str | None = Form(None),
    assignments: str = Form(""),
    operation_id: str = Form(...),
):
    actor = require_admin(request)
    if not supabase_mode():
        raise HTTPException(status_code=400, detail="Membership changes require the Supabase backend.")
    from .cloud.repository import CloudRepository, Principal
    from .cloud.api import get_repository
    principal = Principal(uid=actor.uid, email=actor.email, role=getattr(actor, "role", "admin"))
    repository = get_repository()
    repository.change_member(
        principal,
        uid,
        role,
        disabled in {"1", "true", "on", "yes"},
        operation_id,
        assignments=[
            item.strip() for item in assignments.split(",") if item.strip()
        ],
    )
    if disabled in {"1", "true", "on", "yes"}:
        try:
            from .supabase_client import create_admin_client
            create_admin_client().auth.admin.update_user_by_id(uid, {"ban_duration": "876000h"})
        except Exception:
            return RedirectResponse("/admin?revocation=pending", status_code=303)
    else:
        try:
            from .supabase_client import create_admin_client
            create_admin_client().auth.admin.update_user_by_id(uid, {"ban_duration": "none"})
        except Exception:
            return RedirectResponse("/admin?revocation=pending", status_code=303)
    return RedirectResponse("/admin", status_code=303)

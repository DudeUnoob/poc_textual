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

from .db import Batch, Export, ExtractionRun, FieldCandidate, Page, SessionLocal, init_db
from .progress import now_iso, run_snapshot, update_progress
from .schema import get_schema
from .services import (apply_decision, batch_ready, create_export, import_files,
                       queue_query, review_queue_count, update_page_manifest)

TEMPLATES = Jinja2Templates(directory=str(__file__.replace("web.py", "templates")))
STATIC_DIR = __file__.replace("web.py", "static")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Census Review Workbench", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


def render_row_crop(image_path: str, line: int, schema) -> bytes:
    """Render browser-safe JPEG evidence even when the uploaded file is RGBA PNG data."""
    with Image.open(image_path) as image:
        width, height = image.size
        top = schema.data_top + (schema.data_bottom - schema.data_top) * (line - 1) / schema.expected_lines
        bottom = schema.data_top + (schema.data_bottom - schema.data_top) * line / schema.expected_lines
        padding = int(height * 0.01)
        crop = image.crop((0, max(0, int(height * top) - padding), width, min(height, int(height * bottom) + padding)))
        if crop.mode != "RGB":
            crop = crop.convert("RGB")
        buffer = BytesIO()
        crop.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


@app.get("/")
def dashboard(request: Request):
    with SessionLocal() as session:
        batches = session.scalars(select(Batch).order_by(Batch.created_at.desc())).all()
        counts = {batch.id: review_queue_count(session, batch.id) for batch in batches}
        return TEMPLATES.TemplateResponse(request, "dashboard.html", {"batches": batches, "counts": counts})


@app.post("/batches")
def create_batch(
    request: Request,
    name: str = Form(...), county: str = Form(...), state: str = Form(...),
    census_year: int = Form(...), enumeration_district: str = Form(...),
    source_reference: str | None = Form(None), ground_truth_path: str | None = Form(None),
    ground_truth_sheet: str | None = Form(None), ground_truth_page: str | None = Form(None),
    files: list[UploadFile] = File(...),
):
    try:
        get_schema(census_year)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        parsed_ground_truth_page = int(ground_truth_page) if (ground_truth_page or "").strip() else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Starting physical page must be a whole number when provided.")
    with SessionLocal() as session:
        batch = Batch(name=name.strip(), county=county.strip(), state=state.strip(), census_year=census_year,
                      enumeration_district=enumeration_district.strip(), source_reference=source_reference or None,
                      ground_truth_path=ground_truth_path or None, ground_truth_sheet=ground_truth_sheet or None,
                      ground_truth_page=parsed_ground_truth_page)
        session.add(batch)
        session.flush()
        import_files(session, batch, files)
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
        return TEMPLATES.TemplateResponse(request, "batch.html", {"batch": batch, "ready": ready, "reason": reason, "runs": runs, "run_snapshots": run_snapshots, "queue_count": queue_count})


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
        timestamp = now_iso()
        run = ExtractionRun(batch_id=batch.id, model=DEFAULT_MODEL, config={
            "use_crops": True,
            "expected_lines": 30,
            "progress": {
                "phase": "queued", "message": "Waiting for the local extraction worker",
                "total_pages": total_pages, "completed_pages": 0,
                "retry_count": 0, "job_attempt": 0, "last_update": timestamp,
                "events": [{"type": "run_queued", "message": "Run added to the worker queue", "at": timestamp}],
            },
        })
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
def row_crop(page_id: int, line: int):
    with SessionLocal() as session:
        page = page_or_404(session, page_id)
        schema = get_schema(page.batch.census_year)
        if not 1 <= line <= schema.expected_lines:
            raise HTTPException(status_code=400, detail="Line outside page layout")
        return Response(render_row_crop(page.stored_path, line, schema), media_type="image/jpeg")


@app.get("/review/next")
def review_next(request: Request, batch_id: int):
    with SessionLocal() as session:
        candidate = session.scalar(queue_query(batch_id).options(joinedload(FieldCandidate.page).joinedload(Page.batch)))
        if not candidate:
            batch = session.get(Batch, batch_id)
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            return TEMPLATES.TemplateResponse(request, "queue_empty.html", {"batch": batch})
        line_fields = session.scalars(select(FieldCandidate).where(
            FieldCandidate.run_id == candidate.run_id,
            FieldCandidate.page_id == candidate.page_id,
            FieldCandidate.line_number == candidate.line_number,
        ).order_by(FieldCandidate.field_name)).all()
        queue_count = review_queue_count(session, batch_id)
        return TEMPLATES.TemplateResponse(request, "review.html", {"candidate": candidate, "page": candidate.page, "batch": candidate.page.batch, "line_fields": line_fields, "queue_count": queue_count})


@app.post("/candidates/{candidate_id}/decision")
def decide(candidate_id: int, reviewer: str = Form(...), action: str = Form(...), value: str | None = Form(None), rationale: str | None = Form(None)):
    with SessionLocal() as session:
        candidate = session.get(FieldCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Review item not found")
        try:
            apply_decision(session, candidate, reviewer, action, value, rationale)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        batch_id = candidate.page.batch_id
        session.commit()
        return RedirectResponse(f"/review/next?batch_id={batch_id}", status_code=303)


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
        return TEMPLATES.TemplateResponse(request, "export.html", {"export": export})


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

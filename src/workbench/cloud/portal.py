"""Server-rendered Supabase Postgres portal used when WORKBENCH_BACKEND=supabase."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from census_schemas import SUPPORTED_YEARS, get_census_schema, workbook_sheets
from model_config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MODEL, thinking_level
from .api import get_actor, get_repository, get_storage_service, run_cloud
from .repository import CloudError, CloudRepository, Principal, source_identity_id

router = APIRouter(prefix="/shared", tags=["shared-portal"])
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


def render(request: Request, name: str, context: dict):
    from workbench.access import template_context

    return templates.TemplateResponse(request, name, template_context(request, context))


def require_assignment(actor: Principal, repository: CloudRepository, batch_id: str) -> None:
    member = repository.get("members", actor.uid) or {}
    assignments = member.get("assignments") or []
    if assignments and batch_id not in assignments and actor.role != "admin":
        raise HTTPException(status_code=403, detail="This batch is not assigned to you")


@router.get("")
def dashboard(
    request: Request,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    batches = repository.list("batches")
    member = repository.get("members", actor.uid) or {}
    assignments = member.get("assignments") or []
    if assignments and actor.role != "admin":
        batches = [batch for batch in batches if batch["id"] in assignments]
    return render(request, "shared_dashboard.html", {
        "batches": sorted(batches, key=lambda item: item.get("created_at", 0), reverse=True),
        "supported_years": SUPPORTED_YEARS,
        "batch_id": uuid4().hex,
        "operation_id": uuid4().hex,
    })


@router.post("/batches")
def create_batch(
    year: int = Form(...),
    schedule_type: str = Form(...),
    district: str = Form(...),
    name: str = Form(...),
    sheet_name: str = Form(...),
    batch_id: str = Form(...),
    operation_id: str = Form(...),
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    schema = get_census_schema(year, schedule_type, sheet_name)
    metadata = {
        "year": year,
        "schedule_type": schedule_type,
        "district": district.strip(),
        "name": name.strip(),
        "sheet_name": sheet_name,
        "schema_version": 1,
        "catalog_sha256": schema.catalog_version,
    }
    run_cloud(lambda: repository.create_batch(
        actor, batch_id, metadata, operation_id,
    ))
    return RedirectResponse(f"/shared/batches/{batch_id}", status_code=303)


@router.get("/batches/{batch_id}")
def batch_detail(
    request: Request,
    batch_id: str,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    require_assignment(actor, repository, batch_id)
    batch = repository.get("batches", batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    pages = [repository.get("pages", page_id) for page_id in batch.get("page_ids", [])]
    jobs = [
        job for job in repository.list("jobs")
        if job.get("batch_id") == batch_id
    ]
    return render(request, "shared_batch.html", {
        "batch": batch,
        "pages": [page for page in pages if page],
        "jobs": sorted(jobs, key=lambda item: item.get("created_at", 0), reverse=True),
        "operation_id": uuid4().hex,
        "upload_operation_id": uuid4().hex,
        "job_id": uuid4().hex,
        "job_operation_id": uuid4().hex,
        "release_id": uuid4().hex,
        "release_operation_id": uuid4().hex,
        "is_admin": actor.role == "admin",
    })


@router.post("/batches/{batch_id}/pages")
def upload_page(
    batch_id: str,
    image_id: str = Form(...),
    operation_id: str = Form(...),
    image: UploadFile = File(...),
    repository: CloudRepository = Depends(get_repository),
    storage_service=Depends(get_storage_service),
    actor: Principal = Depends(get_actor),
):
    require_assignment(actor, repository, batch_id)
    batch = repository.get("batches", batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    source = {
        "image_id": image_id.strip(),
        "year": batch["year"],
        "schedule_type": batch["schedule_type"],
        "district": batch["district"],
    }
    from .storage import StorageServiceError
    try:
        stored = storage_service.upload_original(
            page_id=source_identity_id(source),
            filename=image.filename or "",
            content_type=image.content_type or "",
            stream=image.file,
        )
        page = repository.register_page(
            actor, batch_id, source, stored.sha256, operation_id,
        )
        repository.mark_upload_ready(
            actor, page["id"], stored.generation, f"{operation_id}-ready",
            content_type=stored.content_type, size=stored.size,
        )
    except (CloudError, StorageServiceError) as exc:
        raise HTTPException(
            status_code=getattr(exc, "status", 400), detail=str(exc),
        ) from exc
    return RedirectResponse(f"/shared/batches/{batch_id}", status_code=303)


@router.post("/batches/{batch_id}/jobs")
def queue_job(
    batch_id: str,
    job_id: str = Form(...),
    operation_id: str = Form(...),
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    require_assignment(actor, repository, batch_id)
    batch = repository.get("batches", batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    schema = get_census_schema(
        batch["year"], batch["schedule_type"], batch.get("sheet_name"),
    )
    config = {
        "year": batch["year"],
        "schedule_type": batch["schedule_type"],
        "sheet_name": batch.get("sheet_name"),
        "model": DEFAULT_MODEL,
        "thinking_level": thinking_level(),
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "expected_lines": schema.expected_lines,
        "use_crops": bool(schema.field_bounds),
        "strategy": "row_blocks" if schema.field_bounds else "adaptive_full_page",
        "n_blocks": 3,
        "parallelism": 3,
        "budget_units_per_operation": 1,
    }
    run_cloud(lambda: repository.enqueue_job(
        actor, job_id, batch_id, config, operation_id,
    ))
    return RedirectResponse(f"/shared/batches/{batch_id}", status_code=303)


def reviewable_rows(repository: CloudRepository, batch: dict, actor: Principal) -> list[dict]:
    page_ids = set(batch.get("page_ids") or [])
    rows = [
        row for row in repository.list("rows")
        if row.get("page_id") in page_ids and not row.get("excluded")
    ]
    primary = [row for row in rows if not row.get("primary_complete")]
    qa = [
        row for row in rows
        if row.get("primary_complete")
        and row.get("qa_selected")
        and not row.get("qa_complete")
        and row.get("reviewed_by") != actor.uid
    ]
    return sorted([*primary, *qa], key=lambda row: (row["page_id"], str(row["row_key"])))


@router.get("/batches/{batch_id}/review")
def review_next(
    request: Request,
    batch_id: str,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    require_assignment(actor, repository, batch_id)
    batch = repository.get("batches", batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    rows = reviewable_rows(repository, batch, actor)
    if not rows:
        return RedirectResponse(f"/shared/batches/{batch_id}", status_code=303)
    row = rows[0]
    session_id = request.cookies.get("review_session") or uuid4().hex
    lease = run_cloud(lambda: repository.acquire_lease(actor, row["id"], session_id))
    page = repository.get("pages", row["page_id"])
    response = render(request, "shared_review.html", {
        "batch": batch,
        "page": page,
        "row": row,
        "lease": lease,
        "operation_id": uuid4().hex,
        "is_qa": bool(row.get("primary_complete")),
    })
    response.set_cookie(
        "review_session", session_id, httponly=True, secure=True,
        samesite="lax", max_age=12 * 60 * 60,
    )
    return response


@router.post("/batches/{batch_id}/rows/{row_id}")
async def save_review(
    request: Request,
    batch_id: str,
    row_id: str,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    require_assignment(actor, repository, batch_id)
    form = await request.form()
    row = repository.get("rows", row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    if form.get("qa") == "1":
        run_cloud(lambda: repository.complete_qa(
            actor, row_id, int(form["expected_revision"]), str(form["operation_id"]),
        ))
    else:
        fields = row["original_values"].keys()
        values = {field: str(form.get(f"value:{field}") or "") or None for field in fields}
        states = {
            field: str(form.get(f"state:{field}") or ("blank" if values[field] is None else "value"))
            for field in fields
        }
        action = str(form.get("action") or "submit")
        callback = repository.save_draft if action == "draft" else repository.save_row
        run_cloud(lambda: callback(
            actor, row_id, values, int(form["expected_revision"]),
            str(form["operation_id"]), str(form["lease_token"]),
            str(form["review_session"]), states=states,
        ))
        if action == "draft":
            return RedirectResponse(
                f"/shared/batches/{batch_id}/review", status_code=303,
            )
    return RedirectResponse(f"/shared/batches/{batch_id}/review", status_code=303)

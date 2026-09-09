"""Backend-only API for shared Firestore state.

Browser code never talks to Firestore or Storage directly. Every mutation
crosses this boundary with identity, revision, lease, and operation metadata.
"""
from __future__ import annotations

from typing import Any

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .repository import CloudError, CloudRepository, Principal, source_identity_id
from .store import FirebaseStore

router = APIRouter(prefix="/api/shared", tags=["shared"])


class Command(BaseModel):
    operation_id: str = Field(min_length=8, max_length=256)


class BatchCommand(Command):
    batch_id: str
    metadata: dict[str, Any]


class PageCommand(Command):
    batch_id: str
    source: dict[str, Any]
    checksum: str = Field(min_length=64, max_length=64)


class LeaseCommand(BaseModel):
    session_id: str


class RenewCommand(LeaseCommand):
    lease_token: str


class SaveRowCommand(Command, RenewCommand):
    values: dict[str, str | int | float | None]
    states: dict[str, str] | None = None
    expected_revision: int = Field(ge=0)
    complete: bool = True


class QaCommand(Command):
    expected_revision: int = Field(ge=0)


class MissingRowCommand(Command):
    location: str = Field(min_length=1)
    note: str | None = None


class ResolveMissingRowCommand(Command):
    resolution: str = Field(min_length=1)


class AddRowCommand(Command):
    row_key: str = Field(min_length=1)
    values: dict[str, str | int | float | None]


class ExcludeRowCommand(Command):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1)


class JobCommand(Command):
    job_id: str
    batch_id: str
    config: dict[str, Any]


class ReleaseCommand(Command):
    release_id: str


class ReopenCommand(Command):
    reason: str = Field(min_length=1)


class BudgetCommand(Command):
    total: int = Field(ge=0)


class AccessCommand(Command):
    closed: bool


class AssignmentCommand(Command):
    batch_ids: list[str]


def get_repository() -> CloudRepository:
    from firebase_admin import firestore as firebase_firestore
    from workbench.auth import init_firebase

    init_firebase()
    return CloudRepository(FirebaseStore(firebase_firestore.client()))


def get_storage_service():
    from firebase_admin import storage as firebase_storage
    from .storage import StorageService

    return StorageService(firebase_storage.bucket())


def get_actor(request: Request) -> Principal:
    actor = getattr(request.state, "principal", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return Principal(uid=actor.uid, email=actor.email, role=actor.role)


def run_cloud(operation):
    try:
        return operation()
    except CloudError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/batches")
def list_batches(
    repository: CloudRepository = Depends(get_repository),
    _: Principal = Depends(get_actor),
):
    return {"batches": repository.list("batches")}


@router.post("/batches")
def create_batch(
    command: BatchCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.create_batch(
        actor, command.batch_id, command.metadata, command.operation_id,
    ))


@router.post("/pages")
def register_page(
    command: PageCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.register_page(
        actor, command.batch_id, command.source, command.checksum,
        command.operation_id,
    ))


@router.post("/pages/upload")
def upload_page(
    batch_id: str = Form(...),
    source_json: str = Form(...),
    operation_id: str = Form(...),
    image: UploadFile = File(...),
    repository: CloudRepository = Depends(get_repository),
    storage_service=Depends(get_storage_service),
    actor: Principal = Depends(get_actor),
):
    try:
        source = json.loads(source_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid source identity") from exc
    source_id = source_identity_id(source)
    from .storage import StorageServiceError
    try:
        stored = storage_service.upload_original(
            page_id=source_id,
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
        return repository.get("pages", page["id"])
    except (CloudError, StorageServiceError) as exc:
        raise HTTPException(
            status_code=getattr(exc, "status", 400), detail=str(exc),
        ) from exc


@router.get("/pages/{page_id}/media")
def page_media(
    page_id: str,
    repository: CloudRepository = Depends(get_repository),
    storage_service=Depends(get_storage_service),
    _: Principal = Depends(get_actor),
):
    page = repository.get("pages", page_id)
    if not page or page.get("storage_status") != "ready":
        raise HTTPException(status_code=404, detail="Page media is unavailable")
    try:
        content = storage_service.authorized_download(page)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Page media could not be read") from exc
    return Response(content, media_type=page.get("content_type") or "image/jpeg")


@router.post("/jobs")
def enqueue_job(
    command: JobCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.enqueue_job(
        actor, command.job_id, command.batch_id, command.config,
        command.operation_id,
    ))


@router.post("/rows/{row_id}/lease")
def acquire_row_lease(
    row_id: str,
    command: LeaseCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.acquire_lease(actor, row_id, command.session_id))


@router.post("/rows/{row_id}/lease/renew")
def renew_row_lease(
    row_id: str,
    command: RenewCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.renew_lease(
        actor, row_id, command.session_id, command.lease_token,
    ))


@router.post("/rows/{row_id}")
def save_row(
    row_id: str,
    command: SaveRowCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    if not command.complete:
        return run_cloud(lambda: repository.save_draft(
            actor, row_id, command.values, command.expected_revision,
            command.operation_id, command.lease_token, command.session_id,
            states=command.states,
        ))
    return run_cloud(lambda: repository.save_row(
        actor, row_id, command.values, command.expected_revision,
        command.operation_id, command.lease_token, command.session_id,
        complete=True, states=command.states,
    ))


@router.post("/rows/{row_id}/qa")
def complete_qa(
    row_id: str,
    command: QaCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.complete_qa(
        actor, row_id, command.expected_revision, command.operation_id,
    ))


@router.post("/pages/{page_id}/missing-rows")
def report_missing_row(
    page_id: str,
    command: MissingRowCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.report_missing_row(
        actor, page_id, command.location, command.note, command.operation_id,
    ))


@router.post("/missing-rows/{issue_id}/resolve")
def resolve_missing_row(
    issue_id: str,
    command: ResolveMissingRowCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.resolve_missing_row(
        actor, issue_id, command.resolution, command.operation_id,
    ))


@router.post("/pages/{page_id}/rows")
def add_row(
    page_id: str,
    command: AddRowCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.add_row(
        actor, page_id, command.row_key, command.values, command.operation_id,
    ))


@router.post("/rows/{row_id}/exclude")
def exclude_row(
    row_id: str,
    command: ExcludeRowCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.exclude_row(
        actor, row_id, command.expected_revision, command.reason,
        command.operation_id,
    ))


@router.post("/batches/{batch_id}/release")
def release_batch(
    batch_id: str,
    command: ReleaseCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.finalize_release(
        actor, batch_id, command.release_id, command.operation_id,
    ))


@router.post("/batches/{batch_id}/reopen")
def reopen_batch(
    batch_id: str,
    command: ReopenCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.reopen(
        actor, batch_id, command.reason, command.operation_id,
    ))


@router.get("/releases/{release_id}/export")
def download_release(
    release_id: str,
    repository: CloudRepository = Depends(get_repository),
    _: Principal = Depends(get_actor),
):
    from .exports import build_release_zip
    try:
        content = build_release_zip(repository, release_id)
    except CloudError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="release-{release_id}.zip"',
        },
    )


@router.post("/admin/budget")
def configure_budget(
    command: BudgetCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.ensure_budget(
        actor, command.total, command.operation_id,
    ))


@router.post("/admin/access")
def configure_access(
    command: AccessCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.close_access(
        actor, command.closed, command.operation_id,
    ))


@router.post("/admin/members/{uid}/assignments")
def configure_assignments(
    uid: str,
    command: AssignmentCommand,
    repository: CloudRepository = Depends(get_repository),
    actor: Principal = Depends(get_actor),
):
    return run_cloud(lambda: repository.assign_member(
        actor, uid, command.batch_ids, command.operation_id,
    ))

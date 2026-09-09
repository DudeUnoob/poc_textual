"""One-shot Firestore extraction worker.

Provider calls and object downloads run outside repository transactions. Budget
receipts make each observed provider operation idempotent, but provider billing
is still at-least-once: a process can die after the provider accepts a request
and before the corresponding result is committed.
"""
from __future__ import annotations

import argparse
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol

from extract import extract_with_review_data
from model_config import DEFAULT_MODEL, thinking_level

from .cloud.repository import CloudError, CloudRepository, digest, key
from .schema import get_schema


class StorageService(Protocol):
    def fetch_exact(self, object_name: str, generation: str) -> bytes: ...


class FirebaseStorageService:
    """Read immutable object bytes using a generation precondition."""

    def __init__(self, bucket) -> None:
        self.bucket = bucket

    def fetch_exact(self, object_name: str, generation: str) -> bytes:
        generation_number = int(generation)
        blob = self.bucket.blob(object_name, generation=generation_number)
        return blob.download_as_bytes(if_generation_match=generation_number)


class WorkerCancelled(CloudError):
    pass


class LeaseHeartbeat:
    """Keep a claimed lease alive, including while a provider call blocks."""

    def __init__(
        self,
        callback: Callable[[], object],
        *,
        clock: Callable[[], float] = time.monotonic,
        interval_seconds: float = 25.0,
    ) -> None:
        if interval_seconds <= 0 or interval_seconds > 30:
            raise ValueError("Heartbeat interval must be between 0 and 30 seconds")
        self.callback = callback
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.last_heartbeat = float("-inf")
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def pulse(self, *, force: bool = False) -> None:
        if self.error:
            raise self.error
        now = self.clock()
        with self._lock:
            if not force and now - self.last_heartbeat < self.interval_seconds:
                return
            try:
                self.callback()
                self.last_heartbeat = now
            except BaseException as exc:
                self.error = exc
                raise

    def __enter__(self) -> "LeaseHeartbeat":
        self.pulse(force=True)

        def run() -> None:
            while not self._stop.wait(self.interval_seconds):
                try:
                    self.pulse(force=True)
                except BaseException:
                    return

        self._thread = threading.Thread(
            target=run, name="cloud-worker-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 1)
        if self.error:
            raise self.error


def records_for_commit(extracted: list[dict]) -> list[dict]:
    committed = []
    for index, record in enumerate(extracted, start=1):
        row_key = record.get("Line Number")
        if row_key is None:
            row_key = record.get("line_number", index)
        committed.append({"row_key": str(row_key), "values": dict(record)})
    return committed


def download_page_to_path(
    storage: StorageService,
    page: dict,
    generation: str,
    destination: Path,
) -> Path:
    object_name = str(page.get("object_name") or "")
    if not object_name:
        raise CloudError("Page has no source object", 404)
    destination.write_bytes(storage.fetch_exact(object_name, generation))
    return destination


def _assert_worker_current(
    repo: CloudRepository, job_id: str, worker_id: str, fence: int
) -> None:
    job = repo.get("jobs", job_id)
    if not job or job.get("status") != "running":
        if job and job.get("status") in {"cancel_requested", "cancelled"}:
            raise WorkerCancelled("Job cancellation requested")
        raise CloudError("Job is no longer running")
    repo.heartbeat_job(job_id, worker_id, fence)


def _consume_operation_budget_once(
    repo: CloudRepository,
    *,
    job_id: str,
    worker_id: str,
    fence: int,
    operation_key: str,
    units: int,
) -> None:
    """Atomically validate ownership, debit budget, and write a receipt."""
    units = int(units)
    if units < 0:
        raise CloudError("Invalid budget consumption", 400)
    receipt_id = digest([job_id, operation_key])
    receipt_path = f"jobs/{key(job_id)}/operations/{receipt_id}"

    def action(tx) -> None:
        job, _lease = repo._worker(tx, job_id, worker_id, fence)
        if job.get("status") != "running":
            raise WorkerCancelled("Job cancellation requested")
        receipt = tx.get(receipt_path)
        if receipt:
            if receipt.get("operation_key") != operation_key or receipt.get("units") != units:
                raise CloudError("Provider operation receipt conflict")
            return
        budget = tx.get("system/budget") or {}
        remaining = int(budget.get("remaining") or 0)
        if remaining < units:
            raise CloudError("Extraction budget exhausted", 429)
        tx.set("system/budget", dict(budget, remaining=remaining - units))
        tx.set(
            receipt_path,
            {
                "job_id": job_id,
                "operation_key": operation_key,
                "units": units,
                "created_at": repo.clock(),
            },
        )

    repo.store.atomic(action)


def _execution_config(repo: CloudRepository, job: dict) -> dict:
    """Freeze all extraction inputs before any external operation."""
    config = dict(job.get("config") or {})
    batch = repo.get("batches", job["batch_id"]) or {}
    raw_year = config.get("year") or config.get("census_year") or batch.get("year")
    if raw_year is None:
        raise CloudError("Frozen job configuration is missing census year", 400)
    year = int(raw_year)
    schedule_type = str(
        config.get("schedule_type") or batch.get("schedule_type") or "population"
    )
    sheet_name = config.get("sheet_name") or batch.get("sheet_name")
    schema = get_schema(year, schedule_type, sheet_name)
    raw_expected_lines = config.get("expected_lines")
    return {
        "year": year,
        "schedule_type": schedule_type,
        "sheet_name": sheet_name,
        "model": str(config.get("model") or DEFAULT_MODEL),
        "use_crops": bool(config.get("use_crops", True)),
        "expected_lines": (
            int(raw_expected_lines)
            if raw_expected_lines is not None
            else schema.expected_lines
        ),
        "strategy": config.get("strategy", "adaptive_full_page"),
        "n_blocks": int(config.get("n_blocks", 3)),
        "parallelism": int(config.get("parallelism", 3)),
        "thinking": thinking_level(config.get("thinking_level")),
        "max_output_tokens": config.get("max_output_tokens"),
        "budget_units": int(config.get("budget_units_per_operation", 1)),
    }


def process_claimed_job(
    repo: CloudRepository,
    job: dict,
    worker_id: str,
    *,
    extract_page: Callable[..., tuple[list[dict], dict, list]] | None = None,
    storage: StorageService,
    clock: Callable[[], float] = time.monotonic,
    heartbeat_callback: Callable[[], object] | None = None,
    heartbeat_interval_seconds: float = 25.0,
) -> dict:
    """Process uncommitted pages. Exceptions intentionally leave the job recoverable."""
    extract_page = extract_page or extract_with_review_data
    job_id = job["id"]
    fence = job["fence"]
    config = _execution_config(repo, job)
    completed = set(job.get("completed_pages") or [])
    source_generations = dict(job.get("source_generations") or {})
    heartbeat = heartbeat_callback or (
        lambda: _assert_worker_current(repo, job_id, worker_id, fence)
    )

    for page_id in job["page_ids"]:
        if page_id in completed:
            continue
        page = repo.get("pages", page_id)
        if not page:
            raise CloudError(f"Page {page_id} missing", 404)
        generation = str(source_generations.get(page_id) or "")
        if not generation:
            raise CloudError("Frozen source generation is missing", 409)
        suffix = Path(str(page.get("source", {}).get("image_id") or page_id)).suffix or ".jpg"

        with LeaseHeartbeat(
            heartbeat, clock=clock, interval_seconds=heartbeat_interval_seconds
        ) as lease_heartbeat:
            with tempfile.TemporaryDirectory() as tmp:
                image_path = Path(tmp) / f"{page_id}{suffix}"
                download_page_to_path(storage, page, generation, image_path)

                def on_extraction_event(event: dict) -> None:
                    lease_heartbeat.pulse()
                    if event.get("type") != "api_attempt":
                        return
                    source = str(event.get("source") or "provider")
                    attempt = int(event.get("attempt") or 1)
                    _consume_operation_budget_once(
                        repo,
                        job_id=job_id,
                        worker_id=worker_id,
                        fence=fence,
                        # A replacement worker may repeat a provider request
                        # after an ambiguous crash. Count that fenced attempt
                        # separately because Gemini billing is not exactly-once.
                        operation_key=f"{page_id}:{source}:{attempt}:fence-{fence}",
                        units=config["budget_units"],
                    )

                records, _diagnostics, _candidates = extract_page(
                    str(image_path),
                    config["year"],
                    schedule_type=config["schedule_type"],
                    sheet_name=config["sheet_name"],
                    model=config["model"],
                    event_callback=on_extraction_event,
                    use_crops=config["use_crops"],
                    expected_lines=config["expected_lines"],
                    strategy=config["strategy"],
                    n_blocks=config["n_blocks"],
                    parallelism=config["parallelism"],
                    thinking=config["thinking"],
                    max_output_tokens=config["max_output_tokens"],
                )
                lease_heartbeat.pulse(force=True)
        repo.commit_page(job_id, worker_id, fence, page_id, records_for_commit(records))
        completed.add(page_id)
    return repo.finish_job(job_id, worker_id, fence)


def process_next_job(
    repo: CloudRepository,
    worker_id: str,
    *,
    storage: StorageService,
    job_id: str | None = None,
    **kwargs,
) -> bool:
    jobs = [
        job for job in repo.list("jobs")
        if job.get("status") in {"queued", "running"}
        and (job_id is None or job.get("id") == job_id)
    ]
    jobs.sort(key=lambda job: job.get("created_at") or 0)
    for job in jobs:
        try:
            claimed = repo.claim_job(job["id"], worker_id)
        except CloudError:
            continue
        process_claimed_job(repo, claimed, worker_id, storage=storage, **kwargs)
        return True
    return False


def firebase_services() -> tuple[CloudRepository, FirebaseStorageService]:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
    from firebase_admin import storage as firebase_storage
    from .cloud.store import FirebaseStore
    from .settings import firebase_project_id, firebase_storage_bucket

    try:
        app = firebase_admin.get_app()
    except ValueError:
        options = {
            key: value
            for key, value in {
                "projectId": firebase_project_id(),
                "storageBucket": firebase_storage_bucket(),
            }.items()
            if value
        }
        app = firebase_admin.initialize_app(options=options)
    repository = CloudRepository(FirebaseStore(firebase_firestore.client(app=app)))
    storage = FirebaseStorageService(
        firebase_storage.bucket(firebase_storage_bucket() or None, app=app)
    )
    return repository, storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process one cloud extraction job")
    parser.add_argument("job_id", nargs="?", default=os.environ.get("WORKBENCH_JOB_ID"))
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("WORKBENCH_WORKER_ID")
        or f"worker-{uuid.uuid4().hex[:12]}",
    )
    args = parser.parse_args(argv)
    if not args.job_id:
        parser.error("job_id is required (argument or WORKBENCH_JOB_ID)")
    repo, storage = firebase_services()
    processed = process_next_job(
        repo, args.worker_id, storage=storage, job_id=args.job_id
    )
    if not processed:
        raise CloudError("Requested job is not runnable", 409)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

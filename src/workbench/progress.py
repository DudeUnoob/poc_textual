from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .db import ExtractionRun, FieldCandidate, Page

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def progress_data(run: ExtractionRun) -> dict:
    return dict((run.config or {}).get("progress") or {})


def update_progress(run: ExtractionRun, *, event: dict | None = None, **changes) -> dict:
    config = dict(run.config or {})
    progress = dict(config.get("progress") or {})
    progress.update(changes)
    progress["last_update"] = now_iso()
    if event:
        entry = {**event, "at": now_iso()}
        progress["last_event"] = entry
        progress["events"] = [*(progress.get("events") or []), entry][-20:]
    config["progress"] = progress
    run.config = config
    return progress


def run_snapshot(session: Session, run: ExtractionRun) -> dict:
    progress = progress_data(run)
    total_pages = progress.get("total_pages") or session.scalar(
        select(func.count(Page.id)).where(Page.batch_id == run.batch_id, Page.kind == "census")
    ) or 0
    committed_pages = session.scalar(
        select(func.count(distinct(FieldCandidate.page_id))).where(FieldCandidate.run_id == run.id)
    ) or 0
    completed_pages = max(int(progress.get("completed_pages") or 0), committed_pages)
    review_items = session.scalar(
        select(func.count(FieldCandidate.id)).where(FieldCandidate.run_id == run.id)
    ) or 0
    queue_position = None
    if run.status == "queued":
        queue_position = 1 + (session.scalar(
            select(func.count(ExtractionRun.id)).where(
                ExtractionRun.status == "queued",
                ExtractionRun.created_at < run.created_at,
            )
        ) or 0)
    percent = round((completed_pages / total_pages) * 100) if total_pages else 0
    if run.status == "completed":
        percent = 100
    failure = classify_failure(run.error)
    last_update = progress.get("last_update")
    step_elapsed_seconds = None
    if last_update:
        try:
            updated_at = datetime.fromisoformat(last_update)
            step_elapsed_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
        except (TypeError, ValueError):
            pass
    return {
        "run_id": run.id,
        "batch_id": run.batch_id,
        "status": run.status,
        "phase": progress.get("phase", run.status),
        "message": failure["message"] if run.status == "failed" and failure else (progress.get("message") or _default_message(run.status, queue_position)),
        "total_pages": total_pages,
        "completed_pages": completed_pages,
        "percent": percent,
        "current_page": progress.get("current_page"),
        "current_filename": progress.get("current_filename"),
        "current_pass": progress.get("current_pass"),
        "api_attempt": progress.get("api_attempt"),
        "max_api_attempts": progress.get("max_api_attempts"),
        "retry_count": int(progress.get("retry_count") or 0),
        "job_attempt": int(progress.get("job_attempt") or 0),
        "queue_position": queue_position,
        "review_items": review_items,
        "last_update": last_update,
        "server_time": now_iso(),
        "step_elapsed_seconds": step_elapsed_seconds,
        "possibly_stalled": run.status == "running" and (step_elapsed_seconds or 0) > 600,
        "events": progress.get("events") or [],
        "error": run.error,
        "failure_kind": failure["kind"] if failure else None,
        "action_required": failure["action_required"] if failure else None,
        "can_resume": run.status == "failed",
        "terminal": run.status in TERMINAL_STATUSES,
    }


def classify_failure(error: str | None) -> dict | None:
    if not error:
        return None
    lowered = error.casefold()
    if "prepayment credits are depleted" in lowered:
        return {
            "kind": "billing",
            "message": "Extraction paused because the Gemini project has no prepaid credits remaining.",
            "action_required": "Add prepaid credits in Google AI Studio, then use Resume remaining pages. Completed pages are preserved.",
        }
    if "api key not valid" in lowered or "permission_denied" in lowered or "permission denied" in lowered:
        return {
            "kind": "credentials",
            "message": "Extraction paused because Gemini rejected the API credentials.",
            "action_required": "Replace or authorize the API key, restart the worker, then resume. Completed pages are preserved.",
        }
    if "429" in lowered or "resource_exhausted" in lowered:
        return {
            "kind": "rate_limit",
            "message": "Extraction paused after Gemini rate limits exhausted the automatic retries.",
            "action_required": "Wait for the quota window to reset, then resume. Completed pages are preserved.",
        }
    if "json" in lowered or "unterminated string" in lowered or "expecting ',' delimiter" in lowered:
        return {
            "kind": "invalid_response",
            "message": "Extraction paused after Gemini repeatedly returned an incomplete structured response.",
            "action_required": "Resume to retry the unfinished page with the adaptive output budget. Completed pages are preserved.",
        }
    if "timeout" in lowered or "disconnected" in lowered or "connection" in lowered:
        return {
            "kind": "network",
            "message": "Extraction paused after repeated network failures.",
            "action_required": "Check connectivity, then resume. Completed pages are preserved.",
        }
    return {
        "kind": "provider_error",
        "message": "Extraction paused after all automatic recovery attempts failed.",
        "action_required": "Open Failure details, resolve the reported problem, then resume. Completed pages are preserved.",
    }


def _default_message(status: str, queue_position: int | None) -> str:
    if status == "queued":
        return f"Waiting for the worker (queue position {queue_position or 1})"
    if status == "running":
        return "Extraction is running"
    if status == "completed":
        return "Extraction completed"
    if status == "failed":
        return "Extraction stopped after a recoverable failure"
    return status.replace("_", " ").capitalize()

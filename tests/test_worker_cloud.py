from __future__ import annotations

from pathlib import Path

import pytest

from workbench.cloud import CloudError, CloudRepository, MemoryStore
from workbench.cloud.repository import digest
from workbench.worker_cloud import process_claimed_job, process_next_job


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeStorage:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def fetch_exact(self, object_name: str, generation: str) -> bytes:
        self.requests.append((object_name, generation))
        return b"image"


def make_repository(
    *,
    page_count: int = 1,
    budget: int = 20,
    config: dict | None = None,
) -> tuple[CloudRepository, MemoryStore, Clock, list[str]]:
    clock = Clock()
    store = MemoryStore()
    page_ids = [f"page-{index}" for index in range(1, page_count + 1)]
    store.documents["system/budget"] = {"remaining": budget, "total": budget}
    store.documents["batches/batch-1"] = {
        "id": "batch-1",
        "year": 1950,
        "schedule_type": "population",
        "sheet_name": "Bastrop 11-2A",
    }
    for index, page_id in enumerate(page_ids, start=1):
        store.documents[f"pages/{page_id}"] = {
            "id": page_id,
            "source": {"image_id": f"source-{index}.jpg"},
            "object_name": f"originals/{page_id}/checksum",
            "generation": f"10{index}",
            "storage_status": "ready",
            "row_ids": [],
            "revision": 0,
        }
    frozen_config = {
        "year": 1950,
        "schedule_type": "population",
        "sheet_name": "Bastrop 11-2A",
        "model": "gemini-test",
        "thinking_level": "high",
        "max_output_tokens": 12345,
        "use_crops": False,
        "expected_lines": 28,
        "strategy": "row_blocks",
        "n_blocks": 4,
        "parallelism": 2,
        "budget_units_per_operation": 2,
    }
    frozen_config.update(config or {})
    store.documents["jobs/job-1"] = {
        "id": "job-1",
        "batch_id": "batch-1",
        "page_ids": page_ids,
        "source_generations": {
            page_id: store.documents[f"pages/{page_id}"]["generation"]
            for page_id in page_ids
        },
        "config": frozen_config,
        "status": "queued",
        "created_at": clock.now,
        "completed_pages": [],
        "fence": 0,
        "attempts": 0,
    }
    return CloudRepository(store, clock=clock), store, clock, page_ids


def successful_extractor(calls: list[dict]):
    def extract(image_path: str, year: int, **kwargs):
        assert Path(image_path).read_bytes() == b"image"
        calls.append({"image_path": image_path, "year": year, **kwargs})
        kwargs["event_callback"](
            {"type": "api_attempt", "source": "full_page", "attempt": 1}
        )
        return ([{"Line Number": 1, "Surname": "Lewis"}], {}, [])

    return extract


def test_happy_path_claims_downloads_extracts_commits_and_finishes() -> None:
    repo, _store, clock, page_ids = make_repository()
    storage = FakeStorage()
    calls: list[dict] = []

    assert process_next_job(
        repo,
        "worker-a",
        storage=storage,
        job_id="job-1",
        extract_page=successful_extractor(calls),
        clock=clock,
    )

    assert repo.get("jobs", "job-1")["status"] == "completed"
    assert repo.get("jobs", "job-1")["completed_pages"] == page_ids
    assert repo.get("pages", page_ids[0])["row_ids"]
    assert storage.requests == [("originals/page-1/checksum", "101")]
    assert repo.get("system", "budget")["remaining"] == 18


def test_duplicate_launch_cannot_claim_global_worker_lease() -> None:
    repo, _store, _clock, _page_ids = make_repository()
    repo.claim_job("job-1", "worker-a")

    assert not process_next_job(
        repo, "worker-b", storage=FakeStorage(), job_id="job-1"
    )


def test_crash_after_api_result_is_recoverable_and_counts_possible_rebill() -> None:
    repo, _store, clock, page_ids = make_repository()
    storage = FakeStorage()

    def crashing_extractor(_path: str, _year: int, **kwargs):
        kwargs["event_callback"](
            {"type": "api_attempt", "source": "full_page", "attempt": 1}
        )
        raise RuntimeError("crash after provider result")

    claimed = repo.claim_job("job-1", "worker-a")
    with pytest.raises(RuntimeError, match="provider result"):
        process_claimed_job(
            repo,
            claimed,
            "worker-a",
            storage=storage,
            extract_page=crashing_extractor,
            clock=clock,
        )
    assert not repo.get("pages", page_ids[0])["row_ids"]
    assert repo.get("system", "budget")["remaining"] == 18

    clock.advance(121)
    successor = repo.claim_job("job-1", "worker-b")
    process_claimed_job(
        repo,
        successor,
        "worker-b",
        storage=storage,
        extract_page=successful_extractor([]),
        clock=clock,
    )
    assert repo.get("jobs", "job-1")["status"] == "completed"
    assert repo.get("system", "budget")["remaining"] == 16


def test_stale_fence_is_rejected_before_external_work() -> None:
    repo, _store, clock, _page_ids = make_repository()
    stale = repo.claim_job("job-1", "worker-a")
    clock.advance(121)
    repo.claim_job("job-1", "worker-b")
    storage = FakeStorage()

    with pytest.raises(CloudError, match="expired or superseded"):
        process_claimed_job(
            repo,
            stale,
            "worker-a",
            storage=storage,
            extract_page=successful_extractor([]),
            clock=clock,
        )
    assert storage.requests == []


def test_partial_page_recovery_skips_committed_page() -> None:
    repo, _store, clock, page_ids = make_repository(page_count=2)
    storage = FakeStorage()
    call_count = 0

    def extractor(_path: str, _year: int, **kwargs):
        nonlocal call_count
        call_count += 1
        kwargs["event_callback"](
            {"type": "api_attempt", "source": "full_page", "attempt": 1}
        )
        if call_count == 2:
            raise RuntimeError("second page crashed")
        return ([{"Line Number": 1, "Surname": "Lewis"}], {}, [])

    claimed = repo.claim_job("job-1", "worker-a")
    with pytest.raises(RuntimeError, match="second page"):
        process_claimed_job(
            repo,
            claimed,
            "worker-a",
            storage=storage,
            extract_page=extractor,
            clock=clock,
        )
    assert repo.get("jobs", "job-1")["completed_pages"] == [page_ids[0]]

    clock.advance(121)
    successor = repo.claim_job("job-1", "worker-b")
    process_claimed_job(
        repo,
        successor,
        "worker-b",
        storage=storage,
        extract_page=successful_extractor([]),
        clock=clock,
    )
    assert [name for name, _generation in storage.requests].count(
        "originals/page-1/checksum"
    ) == 1
    assert repo.get("jobs", "job-1")["completed_pages"] == page_ids


def test_cancellation_stops_before_provider_call() -> None:
    repo, store, clock, _page_ids = make_repository()
    provider_calls = 0

    def cancelling_extractor(_path: str, _year: int, **kwargs):
        nonlocal provider_calls
        job = dict(store.documents["jobs/job-1"], status="cancel_requested")
        store.documents["jobs/job-1"] = job
        kwargs["event_callback"](
            {"type": "api_attempt", "source": "full_page", "attempt": 1}
        )
        provider_calls += 1
        return ([{"Line Number": 1}], {}, [])

    claimed = repo.claim_job("job-1", "worker-a")
    with pytest.raises(CloudError):
        process_claimed_job(
            repo,
            claimed,
            "worker-a",
            storage=FakeStorage(),
            extract_page=cancelling_extractor,
            clock=clock,
        )
    assert provider_calls == 0


def test_budget_exhaustion_stops_before_provider_call() -> None:
    repo, _store, clock, _page_ids = make_repository(budget=1)
    provider_calls = 0

    def extractor(_path: str, _year: int, **kwargs):
        nonlocal provider_calls
        kwargs["event_callback"](
            {"type": "api_attempt", "source": "full_page", "attempt": 1}
        )
        provider_calls += 1
        return ([{"Line Number": 1}], {}, [])

    claimed = repo.claim_job("job-1", "worker-a")
    with pytest.raises(CloudError, match="budget exhausted"):
        process_claimed_job(
            repo,
            claimed,
            "worker-a",
            storage=FakeStorage(),
            extract_page=extractor,
            clock=clock,
        )
    assert provider_calls == 0
    assert repo.get("system", "budget")["remaining"] == 1


def test_exact_frozen_config_is_forwarded() -> None:
    repo, _store, clock, _page_ids = make_repository()
    calls: list[dict] = []
    claimed = repo.claim_job("job-1", "worker-a")
    process_claimed_job(
        repo,
        claimed,
        "worker-a",
        storage=FakeStorage(),
        extract_page=successful_extractor(calls),
        clock=clock,
    )

    call = calls[0]
    assert call["year"] == 1950
    assert call["schedule_type"] == "population"
    assert call["sheet_name"] == "Bastrop 11-2A"
    assert call["model"] == "gemini-test"
    assert call["thinking"] == "high"
    assert call["max_output_tokens"] == 12345
    assert call["expected_lines"] == 28
    assert call["strategy"] == "row_blocks"
    assert call["n_blocks"] == 4
    assert call["parallelism"] == 2


def test_repository_limits_job_claims_to_three_attempts() -> None:
    repo, _store, clock, _page_ids = make_repository()
    for worker_index in range(3):
        repo.claim_job("job-1", f"worker-{worker_index}")
        clock.advance(121)
    with pytest.raises(CloudError, match="retry limit"):
        repo.claim_job("job-1", "worker-4")


def test_operation_receipt_identifier_is_stable() -> None:
    assert digest(["job-1", "page-1:full_page:1"]) == digest(
        ["job-1", "page-1:full_page:1"]
    )

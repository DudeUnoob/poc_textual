from __future__ import annotations

import pytest

from workbench.budgets import (
    extraction_enabled,
    max_model_calls_per_run,
    max_pages_per_run,
    max_upload_bytes,
)


def test_extraction_disabled_blocks_paid_work(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('WORKBENCH_EXTRACTION_ENABLED', raising=False)
    monkeypatch.setenv('WORKBENCH_MAX_PAGES_PER_RUN', '10')
    monkeypatch.setenv('WORKBENCH_MAX_UPLOAD_BYTES', '100')
    monkeypatch.setenv('WORKBENCH_MAX_MODEL_CALLS_PER_RUN', '4')
    assert extraction_enabled() is False
    assert max_pages_per_run() == 10
    with pytest.raises(ValueError, match='WORKBENCH_EXTRACTION_ENABLED=true'):
        max_pages_per_run(1)
    with pytest.raises(ValueError, match='WORKBENCH_EXTRACTION_ENABLED=true'):
        max_upload_bytes(1)
    with pytest.raises(ValueError, match='WORKBENCH_EXTRACTION_ENABLED=true'):
        max_model_calls_per_run(1)


def test_enabled_limits_and_operator_messages(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('WORKBENCH_EXTRACTION_ENABLED', 'true')
    monkeypatch.setenv('WORKBENCH_MAX_PAGES_PER_RUN', '2')
    monkeypatch.setenv('WORKBENCH_MAX_UPLOAD_BYTES', '50')
    monkeypatch.setenv('WORKBENCH_MAX_MODEL_CALLS_PER_RUN', '3')
    assert extraction_enabled() is True
    assert max_pages_per_run(2) == 2
    assert max_upload_bytes(50) == 50
    assert max_model_calls_per_run(3) == 3
    with pytest.raises(ValueError, match='WORKBENCH_MAX_PAGES_PER_RUN=2'):
        max_pages_per_run(3)
    with pytest.raises(ValueError, match='WORKBENCH_MAX_UPLOAD_BYTES=50'):
        max_upload_bytes(51)
    with pytest.raises(ValueError, match='WORKBENCH_MAX_MODEL_CALLS_PER_RUN=3'):
        max_model_calls_per_run(4)


@pytest.mark.parametrize('value', ['1', 'TRUE', 'Yes', 'on'])
def test_truthy_extraction_flags(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv('WORKBENCH_EXTRACTION_ENABLED', value)
    assert extraction_enabled() is True


def test_invalid_limit_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('WORKBENCH_MAX_PAGES_PER_RUN', 'ten')
    with pytest.raises(ValueError, match='WORKBENCH_MAX_PAGES_PER_RUN must be an integer'):
        max_pages_per_run()

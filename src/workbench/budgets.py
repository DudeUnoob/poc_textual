from __future__ import annotations

import os

DISABLED_MESSAGE = (
    'Paid extraction is disabled. An operator must set WORKBENCH_EXTRACTION_ENABLED=true '
    'after configuring WORKBENCH_MAX_PAGES_PER_RUN, WORKBENCH_MAX_UPLOAD_BYTES, '
    'and WORKBENCH_MAX_MODEL_CALLS_PER_RUN.'
)


def _truthy(raw: str | None) -> bool:
    return (raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _int_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be an integer (got {raw!r}).') from exc


def extraction_enabled() -> bool:
    return _truthy(os.environ.get('WORKBENCH_EXTRACTION_ENABLED'))


def _require_enabled() -> None:
    if not extraction_enabled():
        raise ValueError(DISABLED_MESSAGE)


def max_pages_per_run(requested: int | None = None) -> int:
    limit = _int_env('WORKBENCH_MAX_PAGES_PER_RUN', '10')
    if requested is None:
        return limit
    _require_enabled()
    if requested > limit:
        raise ValueError(
            f'Requested {requested} pages exceeds WORKBENCH_MAX_PAGES_PER_RUN={limit}. '
            'Reduce the run size or raise the operator limit.'
        )
    return limit


def max_upload_bytes(requested: int | None = None) -> int:
    limit = _int_env('WORKBENCH_MAX_UPLOAD_BYTES', '26214400')
    if requested is None:
        return limit
    _require_enabled()
    if requested > limit:
        raise ValueError(
            f'Requested {requested} bytes exceeds WORKBENCH_MAX_UPLOAD_BYTES={limit}. '
            'Split the upload or raise the operator limit.'
        )
    return limit


def max_model_calls_per_run(requested: int | None = None) -> int:
    limit = _int_env('WORKBENCH_MAX_MODEL_CALLS_PER_RUN', '0')
    if requested is None:
        return limit
    _require_enabled()
    if requested > limit:
        raise ValueError(
            f'Requested {requested} model calls exceeds WORKBENCH_MAX_MODEL_CALLS_PER_RUN={limit}. '
            'Reduce extraction work or raise the operator limit.'
        )
    return limit

"""
Census image -> structured JSON records via the Gemini vision API.

Primary model: gemini-3.1-pro-preview. No GPU required; a stateless API call.
Pipeline: full-page pass + overlapping row-block crops, reconcile by line
number, one targeted retry for gaps, then per-decade normalization. Emits the
canonical JSON envelope (source_image / census_year / record_count / records)
that compare.py already understands, plus a diagnostics block.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import OUTPUTS_DIR, PROMPTS_DIR
from preprocess import Crop, make_targeted_crop, prepare_full_page
from models import (FIELD_TO_GT_COLUMN_1950, ExtractionBatch, FieldConfidence,
                    Legibility, PersonRecord1950, to_gt_record)
from reconcile import reconcile_page
from utils import (BIRTHPLACE_COLUMN, GENDER_COLUMN, normalize_gender,
                   normalize_marital_status, normalize_race, propagate_dittos)

load_dotenv()

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
DEFAULT_TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.0"))
MAX_RETRIES_API = 3
# Thinking-tier models spend output-token budget on internal reasoning before
# emitting the actual JSON; bounding thinking_budget and giving max_output_tokens
# plenty of headroom keeps a full ~30-record page from being truncated mid-string.
DEFAULT_THINKING_BUDGET = int(os.environ.get("GEMINI_THINKING_BUDGET", "1024"))
DEFAULT_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "8192"))


def get_client() -> genai.Client:
    """Build a Gemini client from GEMINI_API_KEY (or GOOGLE_API_KEY)."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return genai.Client(api_key=api_key)


def load_prompt(year: int) -> tuple[str, str]:
    """Load system + per-decade user prompt."""
    system = (PROMPTS_DIR / "system.txt").read_text()
    decade_file = PROMPTS_DIR / f"{year}.txt"
    user = decade_file.read_text() if decade_file.exists() else (PROMPTS_DIR / "generic.txt").read_text()
    return system, user


def _config(system_prompt: str, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=DEFAULT_TEMPERATURE,
        response_mime_type="application/json",
        response_schema=ExtractionBatch,
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        thinking_config=types.ThinkingConfig(thinking_budget=DEFAULT_THINKING_BUDGET),
        max_output_tokens=max_output_tokens,
    )


def call_gemini(client: genai.Client, model: str, system_prompt: str,
                user_prompt: str, image_bytes: bytes, mime_type: str,
                event_callback: Callable[[dict], None] | None = None,
                source_label: str = "full_page") -> list[PersonRecord1950]:
    """One schema-constrained Gemini call. Returns parsed person records."""
    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        types.Part.from_text(text=user_prompt),
    ]
    last_err: Exception | None = None
    output_token_budget = DEFAULT_MAX_OUTPUT_TOKENS
    attempts_used = 0
    for attempt in range(1, MAX_RETRIES_API + 1):
        attempts_used = attempt
        if event_callback:
            event_callback({
                "type": "api_attempt", "source": source_label,
                "attempt": attempt, "max_attempts": MAX_RETRIES_API,
                "max_output_tokens": output_token_budget,
                "message": f"Reading {source_label.replace('_', ' ')} (API attempt {attempt}/{MAX_RETRIES_API})",
            })
        try:
            response = client.models.generate_content(
                model=model, contents=contents,
                config=_config(system_prompt, max_output_tokens=output_token_budget),
            )
            parsed = response.parsed
            if isinstance(parsed, ExtractionBatch):
                records = parsed.records
            else:
                data = json.loads(response.text)
                records = ExtractionBatch.model_validate(data).records
            if event_callback:
                event_callback({
                    "type": "api_success", "source": source_label,
                    "attempt": attempt, "max_attempts": MAX_RETRIES_API,
                    "message": f"Finished and validated {source_label.replace('_', ' ')}",
                })
            return records
        except Exception as e:
            last_err = e
            error_text = str(e).casefold()
            requires_user_action = any(marker in error_text for marker in (
                "prepayment credits are depleted", "api key not valid",
                "permission_denied", "permission denied",
            ))
            retry_in = 2 ** attempt if attempt < MAX_RETRIES_API and not requires_user_action else None
            truncated_json = isinstance(e, json.JSONDecodeError)
            if truncated_json and retry_in is not None:
                output_token_budget = min(output_token_budget * 2, 32768)
            if event_callback:
                event_callback({
                    "type": "api_error", "source": source_label,
                    "attempt": attempt, "max_attempts": MAX_RETRIES_API,
                    "retry_in_seconds": retry_in,
                    "next_max_output_tokens": output_token_budget if retry_in else None,
                    "error": str(e)[:500],
                    "message": (
                        "Gemini rejected the request until account or credential settings are fixed"
                        if requires_user_action else
                        f"Response JSON was incomplete; retrying in {retry_in}s with a larger output budget"
                        if truncated_json and retry_in else
                        f"{source_label.replace('_', ' ')} attempt {attempt} failed; retrying in {retry_in}s"
                        if retry_in else f"{source_label.replace('_', ' ')} failed after {attempt} attempts"
                    ),
                })
            if requires_user_action:
                break
            if attempt < MAX_RETRIES_API:
                time.sleep(retry_in)
    raise RuntimeError(f"Gemini call stopped after {attempts_used} attempt(s): {last_err}")


def _post_process(records: list[dict], year: int) -> list[dict]:
    """Ditto propagation + per-decade normalization on GT-column-keyed dicts."""
    birthplace_field = BIRTHPLACE_COLUMN.get(year, "Birthplace")
    records = propagate_dittos(records, surname_field="Surname", birthplace_field=birthplace_field)
    gender_field = GENDER_COLUMN.get(year, "Gender")
    for rec in records:
        if rec.get("Race"):
            rec["Race"] = normalize_race(rec["Race"], year)
        if rec.get("Marital Status"):
            rec["Marital Status"] = normalize_marital_status(rec["Marital Status"], year)
        if rec.get(gender_field):
            rec[gender_field] = normalize_gender(rec[gender_field])
        elif rec.get("Gender"):
            rec["Gender"] = normalize_gender(rec["Gender"])
    return records


def _flagged_lines(
    full_records: list[PersonRecord1950], expected_lines: int
) -> list[int]:
    """Lines the full-page pass didn't confidently read.

    A line is flagged if it's missing entirely, or present but marked
    ``partial``/``illegible`` -- both are signals that a closer-up crop of
    that region might recover a better read.
    """
    by_line = {r.line_number: r for r in full_records if r.line_number is not None}
    return [
        ln for ln in range(1, expected_lines + 1)
        if ln not in by_line or by_line[ln].legibility != Legibility.clear
    ]


def _cluster_flagged_lines(
    flagged: list[int], max_clusters: int, gap: int = 3
) -> list[tuple[int, int]]:
    """Group flagged line numbers into (lo, hi) ranges for targeted crops.

    Flagged lines within ``gap`` of each other are merged into one range.
    If more than ``max_clusters`` ranges result, the ranges with the
    smallest gap between them are merged first, down to ``max_clusters`` --
    a safety ceiling so a maximally scattered page doesn't cost more calls
    than the old always-3-crops behavior did.
    """
    if not flagged:
        return []
    flagged = sorted(set(flagged))
    clusters = [[flagged[0], flagged[0]]]
    for ln in flagged[1:]:
        if ln - clusters[-1][1] <= gap:
            clusters[-1][1] = ln
        else:
            clusters.append([ln, ln])

    while len(clusters) > max_clusters:
        gaps = [clusters[i + 1][0] - clusters[i][1] for i in range(len(clusters) - 1)]
        idx = gaps.index(min(gaps))
        clusters[idx] = [clusters[idx][0], clusters[idx + 1][1]]
        del clusters[idx + 1]

    return [(lo, hi) for lo, hi in clusters]


def _review_candidates(
    merged: list[PersonRecord1950], raw_records: list[dict], normalized_records: list[dict],
    diagnostics: dict, expected_lines: int,
) -> list[dict]:
    """Build a lossless field-level sidecar for the human review workbench."""
    candidates: list[dict] = []
    conflicts = diagnostics.get("conflict_fields_by_line", {})
    sources = diagnostics.get("line_sources", {})
    for person, raw, normalized in zip(merged, raw_records, normalized_records):
        line = person.line_number
        if line is None:
            continue
        row_conflicts = set(conflicts.get(str(line), []))
        for field_name, gt_column in FIELD_TO_GT_COLUMN_1950.items():
            if field_name == "line_number":
                continue
            value = normalized.get(gt_column)
            raw_value = raw.get(gt_column)
            declared = getattr(person.field_confidence, field_name, None)
            if declared is not None:
                confidence = declared.value if isinstance(declared, FieldConfidence) else str(declared)
            elif person.legibility == Legibility.clear and value not in (None, ""):
                confidence = "high"
            elif person.legibility == Legibility.illegible or value in (None, ""):
                confidence = "low"
            else:
                confidence = "medium"
            reasons: list[str] = []
            if value in (None, ""):
                reasons.append("missing value")
            if person.legibility != Legibility.clear:
                reasons.append(f"row marked {person.legibility.value}")
            if field_name in row_conflicts:
                reasons.append("conflicting extraction passes")
            candidates.append({
                "line_number": line,
                "field": gt_column,
                "raw_value": raw_value,
                "normalized_value": value,
                "model_confidence": confidence,
                "source_pass": sources.get(str(line), "full_page"),
                "row_legibility": person.legibility.value,
                "conflict": field_name in row_conflicts,
                "reasons": reasons,
                # A normalized line band is portable across image dimensions;
                # the app resolves it to a pixel crop when rendering evidence.
                "evidence": {"line_band": [(line - 1) / expected_lines, line / expected_lines]},
            })
    return candidates


def extract_with_review_data(
    image_path: str,
    year: int,
    client: genai.Client | None = None,
    model: str = DEFAULT_MODEL,
    use_crops: bool = True,
    n_blocks: int = 3,
    expected_lines: int = 30,
    event_callback: Callable[[dict], None] | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    """Extract one page and return canonical records plus review provenance."""
    if client is None:
        client = get_client()
    system_prompt, user_prompt = load_prompt(year)

    page_bytes, page_mime = prepare_full_page(image_path)
    full_records = call_gemini(
        client, model, system_prompt, user_prompt, page_bytes, page_mime,
        event_callback=event_callback, source_label="full_page",
    )
    per_source: dict[str, list[PersonRecord1950]] = {"full_page": full_records}

    if use_crops:
        flagged = _flagged_lines(full_records, expected_lines)
        if flagged:
            for i, (lo_line, hi_line) in enumerate(
                _cluster_flagged_lines(flagged, max_clusters=n_blocks)
            ):
                lo = (lo_line - 1) / expected_lines
                hi = hi_line / expected_lines
                crop: Crop = make_targeted_crop(image_path, lo, hi, label=f"crop_{i + 1}")
                crop_records = call_gemini(
                    client, model, system_prompt, user_prompt, crop.image_bytes, crop.mime_type,
                    event_callback=event_callback, source_label=crop.label,
                )
                per_source[crop.label] = crop_records

    def retry_fn(missing_lines: list[int]) -> list[PersonRecord1950]:
        """Targeted retry: crop the page region covering the missing lines."""
        lo = (min(missing_lines) - 1) / expected_lines
        hi = max(missing_lines) / expected_lines
        crop = make_targeted_crop(image_path, lo, hi, label="retry")
        return call_gemini(
            client, model, system_prompt, user_prompt, crop.image_bytes, crop.mime_type,
            event_callback=event_callback, source_label="targeted_retry",
        )

    merged, diagnostics = reconcile_page(
        per_source, expected_lines=expected_lines, retry_fn=retry_fn if use_crops else None
    )

    raw_records = [to_gt_record(p) for p in merged]
    records = _post_process([dict(record) for record in raw_records], year)
    diagnostics_data = diagnostics.model_dump()
    return records, diagnostics_data, _review_candidates(
        merged, raw_records, records, diagnostics_data, expected_lines
    )


def extract_from_image(
    image_path: str,
    year: int,
    client: genai.Client | None = None,
    model: str = DEFAULT_MODEL,
    use_crops: bool = True,
    n_blocks: int = 3,
    expected_lines: int = 30,
    event_callback: Callable[[dict], None] | None = None,
) -> tuple[list[dict], dict]:
    """Backward-compatible extraction interface used by CLI and tests."""
    records, diagnostics, _ = extract_with_review_data(
        image_path, year, client=client, model=model, use_crops=use_crops,
        n_blocks=n_blocks, expected_lines=expected_lines,
        event_callback=event_callback,
    )
    return records, diagnostics


def process_sheet(image_path: str, year: int, output_path: str,
                  use_crops: bool = True, model: str = DEFAULT_MODEL) -> list[dict]:
    """Extract one sheet and save the canonical JSON envelope."""
    print(f"Extracting: {image_path} (year={year}, model={model})")
    client = get_client()
    records, diagnostics, field_candidates = extract_with_review_data(
        image_path, year, client=client, model=model, use_crops=use_crops
    )
    output = {
        "source_image": str(image_path),
        "census_year": year,
        "model": model,
        "record_count": len(records),
        "records": records,
        "diagnostics": diagnostics,
        "field_candidates": field_candidates,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    cov = diagnostics.get("coverage_pct", "?")
    print(f"  -> {len(records)} records (coverage {cov}) saved to {output_path}")
    return records


if __name__ == "__main__":
    _a = sys.argv[1:]
    if len(_a) < 2:
        raise SystemExit("usage: extract.py IMAGE YEAR [OUTPUT] [MODEL] [nocrops]")
    _out = _a[2] if len(_a) > 2 else str(OUTPUTS_DIR / "extracted.json")
    _model = _a[3] if len(_a) > 3 else DEFAULT_MODEL
    process_sheet(_a[0], int(_a[1]), _out,
                  use_crops=("nocrops" not in _a[4:]), model=_model)

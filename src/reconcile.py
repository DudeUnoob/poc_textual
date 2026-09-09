"""
Reconcile multiple Gemini passes (full page + overlapping crops + retry) into
one authoritative set of person records per physical census page.

Why this exists:
- A single full-page pass on a dense ~30-row form tends to drop or merge rows.
- Overlapping row-block crops each see a subset of lines at higher resolution,
  so the same line often appears in 2+ passes. We must pick the best copy and
  drop duplicates.
- Any line still missing after merging triggers ONE targeted retry crop.

Selection rule per line number:
  1. Prefer records the model marked ``clear`` over ``partial``/``illegible``.
  2. Among equal legibility, prefer the one with more non-null fields
     (more complete read).
  3. Ties break by source priority (crops before full_page, since crops are
     higher effective resolution), then first-seen.
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from models import Legibility, PageDiagnostics

# Higher = preferred when legibility and completeness tie.
_SOURCE_PRIORITY = {"retry": 3, "crop": 2, "full_page": 1}
_LEGIBILITY_RANK = {Legibility.clear: 2, Legibility.partial: 1, Legibility.illegible: 0}


def _source_priority(source: str) -> int:
    if source.startswith("crop"):
        return _SOURCE_PRIORITY["crop"]
    return _SOURCE_PRIORITY.get(source, 0)


def _completeness(rec: BaseModel) -> int:
    dumped = rec.model_dump()
    return sum(1 for k, v in dumped.items() if k != "legibility" and v is not None)


def _is_better(candidate: tuple[BaseModel, str],
               current: tuple[BaseModel, str]) -> bool:
    """True if candidate (record, source) should replace current."""
    c_rec, c_src = candidate
    o_rec, o_src = current
    c_leg = _LEGIBILITY_RANK[c_rec.legibility]
    o_leg = _LEGIBILITY_RANK[o_rec.legibility]
    if c_leg != o_leg:
        return c_leg > o_leg
    c_comp, o_comp = _completeness(c_rec), _completeness(o_rec)
    if c_comp != o_comp:
        return c_comp > o_comp
    return _source_priority(c_src) > _source_priority(o_src)


def _merge_sources(
    per_source: dict[str, list[BaseModel]],
    valid_range: range,
) -> tuple[dict[int, tuple[BaseModel, str]], list[int], dict[int, list[str]]]:
    """Merge all passes into best-record-per-line.

    Returns (best_by_line, out_of_range_lines, seen_in_sources).
    """
    best: dict[int, tuple[BaseModel, str]] = {}
    out_of_range: list[int] = []
    seen_in: dict[int, list[str]] = {}

    for source, records in per_source.items():
        for rec in records:
            ln = rec.line_number
            if ln is None:
                continue
            if ln not in valid_range:
                out_of_range.append(ln)
                continue
            seen_in.setdefault(ln, []).append(source)
            if ln not in best or _is_better((rec, source), best[ln]):
                best[ln] = (rec, source)
    return best, sorted(set(out_of_range)), seen_in


def reconcile_page(
    per_source: dict[str, list[BaseModel]],
    expected_lines: int = 30,
    retry_fn: Callable[[list[int]], list[BaseModel]] | None = None,
) -> tuple[list[BaseModel], PageDiagnostics]:
    """Merge passes, run one targeted retry for gaps, return sorted records.

    Args:
        per_source: mapping of pass label -> extracted records for that pass.
        expected_lines: number of main data lines on the form (30 for 1950).
        retry_fn: called once with the list of missing line numbers; should
            return records for a targeted crop covering them. If None, no retry.
    """
    valid_range = range(1, expected_lines + 1)
    best, out_of_range, seen_in = _merge_sources(per_source, valid_range)

    expected = list(valid_range)
    missing = [ln for ln in expected if ln not in best]

    diagnostics = PageDiagnostics(
        expected_lines=expected,
        out_of_range_lines=out_of_range,
        crops_used=len(per_source),
    )

    # One targeted retry for the missing lines.
    if missing and retry_fn is not None:
        diagnostics.retry_attempted = True
        try:
            retry_records = retry_fn(missing)
        except Exception:
            retry_records = []
        per_source["retry"] = retry_records
        for rec in retry_records:
            ln = rec.line_number
            if ln is None or ln not in valid_range:
                continue
            seen_in.setdefault(ln, []).append("retry")
            if ln not in best or _is_better((rec, "retry"), best[ln]):
                if ln in missing:
                    diagnostics.retry_recovered_lines.append(ln)
                best[ln] = (rec, "retry")
        missing = [ln for ln in expected if ln not in best]

    # Duplicates = lines that appeared in more than one pass.
    duplicates = sorted(ln for ln, sources in seen_in.items() if len(set(sources)) > 1)
    illegible = sorted(
        ln for ln, (rec, _) in best.items() if rec.legibility == Legibility.illegible
    )

    # Keep provenance and disagreements for the review workbench.  We do not
    # discard disagreement merely because reconciliation picked a winner.
    diagnostics.line_sources = {str(ln): source for ln, (_, source) in best.items()}
    conflict_fields: dict[str, list[str]] = {}
    for ln, sources in seen_in.items():
        values_by_field: dict[str, set[str]] = {}
        for records in per_source.values():
            for rec in records:
                if rec.line_number != ln:
                    continue
                for field, value in rec.model_dump().items():
                    if field in {"line_number", "legibility", "field_confidence"} or value is None:
                        continue
                    values_by_field.setdefault(field, set()).add(str(value).strip().casefold())
        conflicts = sorted(field for field, values in values_by_field.items() if len(values) > 1)
        if conflicts:
            conflict_fields[str(ln)] = conflicts
    diagnostics.conflict_fields_by_line = conflict_fields
    diagnostics.extracted_lines = sorted(best.keys())
    diagnostics.missing_lines = missing
    diagnostics.duplicate_lines = duplicates
    diagnostics.illegible_lines = illegible

    merged = [best[ln][0] for ln in sorted(best.keys())]
    return merged, diagnostics

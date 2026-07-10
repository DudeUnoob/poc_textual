#!/usr/bin/env python3
"""Batch extract + compare for Bastrop ED 11-1 (1950) with the Gemini pipeline.

Imports the pipeline directly (no subprocess) so it is resilient and records
model/config metadata. Outputs are resumable: an existing per-page extraction
JSON is reused unless you pass a fresh run.

Usage:
  python scripts/run_11_1_batch.py                 # all 11 pages
  python scripts/run_11_1_batch.py 3               # only page 3
  python scripts/run_11_1_batch.py extractonly     # extract, skip comparison
  python scripts/run_11_1_batch.py compareonly     # compare existing outputs
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extract import DEFAULT_MODEL, process_sheet  # noqa: E402
from compare import compare, print_report  # noqa: E402

IMAGE_DIR = ROOT / "data/raw_images/1950_11-1"
OUTPUT_DIR = ROOT / "data/outputs/1950_11-1"
RESULTS_DIR = ROOT / "results/1950_11-1"
GT = ROOT / "data/ground_truth/Bastrop County 1950 Clean.xlsx"
SHEET, YEAR = "Bastrop 11-1", 1950

# Gemini calls are blocking I/O, so a small thread pool is enough -- no need
# for an asyncio rewrite of extract.py. Kept conservative (not one worker per
# page) so a burst of concurrent rate-limit errors doesn't compound against
# call_gemini's own MAX_RETRIES_API exponential backoff.
BATCH_CONCURRENCY = int(os.environ.get("BATCH_CONCURRENCY", "3"))


def _parse_args(argv: list[str]) -> tuple[list[int], bool, bool]:
    pages = list(range(1, 12))
    extract_only = "extractonly" in argv
    compare_only = "compareonly" in argv
    nums = [int(a) for a in argv if a.isdigit()]
    if nums:
        pages = nums
    return pages, extract_only, compare_only


def main() -> None:
    argv = sys.argv[1:]
    pages, extract_only, compare_only = _parse_args(argv)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not (ROOT / ".env").exists() and not compare_only:
        print("ERROR: cp .env.example .env and add GEMINI_API_KEY")
        sys.exit(1)

    def _extract_one(page: int) -> None:
        img = IMAGE_DIR / f"sheet_{page:02d}.jpg"
        out_json = OUTPUT_DIR / f"sheet_{page:02d}_extracted.json"
        if not img.exists():
            print(f"SKIP page {page}: missing {img.name}")
            return
        if out_json.exists():
            print(f"SKIP page {page}: already extracted ({out_json.name})")
            return
        process_sheet(str(img), YEAR, str(out_json))

    if not compare_only:
        with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
            futures = {pool.submit(_extract_one, page): page for page in pages}
            for fut in as_completed(futures):
                page = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    print(f"FAILED page {page}: {e}")

    if extract_only:
        return

    summary = []
    for page in pages:
        img = IMAGE_DIR / f"sheet_{page:02d}.jpg"
        out_json = OUTPUT_DIR / f"sheet_{page:02d}_extracted.json"
        result_json = RESULTS_DIR / f"sheet_{page:02d}_comparison.json"
        if not img.exists():
            continue
        if not out_json.exists():
            print(f"SKIP page {page}: no extraction output yet")
            continue

        metrics, results = compare(str(out_json), str(GT), SHEET, YEAR, page)
        print_report(metrics)
        with open(result_json, "w") as f:
            json.dump({"metrics": metrics, "results": results}, f, indent=2, default=str)
        summary.append({
            "page": page,
            "rows": metrics["rows_compared"],
            "row_accuracy": metrics["row_accuracy"],
            "avg_row_field_match_rate": metrics.get("avg_row_field_match_rate"),
            "row_accuracy_at_80pct": metrics.get("row_accuracy_at_80pct"),
            "field_accuracy": metrics["overall_field_accuracy"],
            "priority_field_accuracy": metrics.get("priority_field_accuracy"),
            "extraction_coverage": metrics.get("extraction_coverage"),
        })

    if summary:
        print("\nBATCH SUMMARY  (model:", DEFAULT_MODEL + ")")
        for r in summary:
            print(f"  page {r['page']:2d}: row={r['row_accuracy']:.1%} "
                  f"row@80%={r['row_accuracy_at_80pct']:.1%} "
                  f"field={r['field_accuracy']:.1%} "
                  f"priority={r['priority_field_accuracy']:.1%} "
                  f"coverage={(r['extraction_coverage'] or 0):.1%}")
        avg_row = sum(r["row_accuracy"] for r in summary) / len(summary)
        avg_row_80 = sum(r["row_accuracy_at_80pct"] for r in summary) / len(summary)
        avg_field_match = sum(r["avg_row_field_match_rate"] for r in summary) / len(summary)
        avg_field = sum(r["field_accuracy"] for r in summary) / len(summary)
        print(f"  AVG: row={avg_row:.1%} row@80%={avg_row_80:.1%} "
              f"avg_field_match={avg_field_match:.1%} field={avg_field:.1%}")
        (RESULTS_DIR / "batch_summary.json").write_text(
            json.dumps({"model": DEFAULT_MODEL, "pages": summary}, indent=2, default=str)
        )


if __name__ == "__main__":
    main()

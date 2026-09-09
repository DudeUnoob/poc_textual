"""
Compare extracted JSON records against human-cleaned XLSX ground truth.
Produces field-level and row-level accuracy metrics.

Two things confirmed by directly running this pipeline against real data
(see CLAUDE.md "VERIFIED Data Quality Findings" #5 and #7):

1. Column names are NOT consistent across decades in the ground truth.
   This script resolves fields by keyword classification rather than a fixed
   column list, so it works whether the sheet says "Birthplace" or "Birth Place",
   "Gender" or "Sex", etc.

2. CRITICAL: a ground-truth workbook sheet (e.g. "Bastrop 11-2A") is not one
   physical census page -- it is many physical pages concatenated end to end,
   and Line Number resets to 1 at the start of every new physical page. A
   naive `{line_number: row}` dict silently keeps only the LAST occurrence of
   each line number and drops the rest, which will compare your extraction
   against the wrong page entirely. This script splits the sheet into
   physical-page blocks first and requires you to specify which page.
"""
import json
import pandas as pd
import argparse
import sys
import math
from pathlib import Path
from fuzzywuzzy import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from census_schemas import get_census_schema
from workbook_catalog import canonical_field
from paths import RESULTS_DIR

# Keyword → match strategy. Order matters: more specific keywords first.
STRATEGY_KEYWORDS = [
    (["race"], "exact"),
    (["gender", "sex"], "exact"),
    (["marital status"], "exact"),
    (["age"], "numeric"),
    (["dwelling number", "house number"], "numeric"),
    (["surname", "given name", "first name", "last name"], "fuzzy_name"),
    (["relation"], "fuzzy"),
    (["birthplace", "birth place"], "fuzzy"),
    (["occupation", "industry"], "fuzzy"),
]

# Threshold for the partial-credit "row_accuracy_at_80pct" metric: fraction of
# compared fields that must match for a row to count, as an alternative to the
# strict all-fields-must-match `row_accuracy`.
ROW_ACCURACY_THRESHOLD = 0.8

def classify_strategy(field_name: str) -> str:
    fl = field_name.lower()
    for keywords, strategy in STRATEGY_KEYWORDS:
        if any(k in fl for k in keywords):
            return strategy
    if "number" in fl or fl in {"hours worked", "weeks worked", "income", "family number"}:
        return "numeric_exact"
    return "fuzzy"  # default for anything else compared


def _norm(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip().lower()


def field_match(ext_val, gt_val, strategy: str) -> bool:
    e, g = _norm(ext_val), _norm(gt_val)
    if e == "" and g == "":
        return True
    if e == "" or g == "":
        return False
    if strategy == "numeric_exact":
        try:
            return float(e) == float(g)
        except (ValueError, TypeError):
            return e == g
    if strategy == "exact":
        return e == g
    if strategy in ("fuzzy", "fuzzy_name"):
        threshold = 90 if strategy == "fuzzy_name" else 85
        return fuzz.ratio(e, g) >= threshold
    if strategy == "numeric":
        try:
            return abs(int(float(e)) - int(float(g))) <= 1
        except (ValueError, TypeError):
            return e == g
    return e == g


def load_ground_truth(xlsx_path: str, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(xlsx_path, sheet_name=sheet_name)


def find_line_number_column(columns) -> str | None:
    for c in columns:
        if c and str(c).strip().lower() == "line number":
            return c
    return None


def split_into_physical_pages(gt_df: pd.DataFrame, line_col: str, *, year: int | None = None) -> list[pd.DataFrame]:
    """
    A ground-truth sheet concatenates many physical census pages. Line Number
    resets to 1 at the start of each new page -- detect those resets and
    split accordingly. Returns a list of DataFrames, one per physical page,
    in original sheet order (page 1 first).
    """
    line_numbers = pd.to_numeric(gt_df[line_col], errors="coerce")
    page_starts = [0]
    for i in range(1, len(line_numbers)):
        prev, curr = line_numbers.iloc[i - 1], line_numbers.iloc[i]
        if pd.notna(curr) and ((pd.notna(prev) and curr < prev)
                               or (year == 1940 and curr >= 41 and pd.notna(prev) and prev <= 40)):
            page_starts.append(i)
    page_starts.append(len(gt_df))

    pages = []
    for start, end in zip(page_starts, page_starts[1:]):
        pages.append(gt_df.iloc[start:end].reset_index(drop=True))
    return pages


def align_by_household_order(
    extracted_records: list[dict],
    ground_truth_records: list[dict],
) -> tuple[dict[int, dict], dict[int, dict]]:
    """Sequence-align no-line forms without hiding missing or extra rows."""
    def match_score(extracted: dict, ground_truth: dict) -> int:
        score = 0
        extracted_household = (
            _norm(extracted.get("Dwelling Number")),
            _norm(extracted.get("Family Number")),
        )
        ground_truth_household = (
            _norm(ground_truth.get("Dwelling Number")),
            _norm(ground_truth.get("Family Number")),
        )
        if any(extracted_household) and any(ground_truth_household):
            score += 8 if extracted_household == ground_truth_household else -5
        for field in ("Surname", "Given Name", "Slave Owner Name", "Age"):
            extracted_value = _norm(extracted.get(field))
            ground_truth_value = _norm(ground_truth.get(field))
            if not extracted_value or not ground_truth_value:
                continue
            score += 2 if fuzz.ratio(extracted_value, ground_truth_value) >= 85 else -1
        return score

    row_count = len(extracted_records)
    truth_count = len(ground_truth_records)
    gap_penalty = -3
    scores = [[0] * (truth_count + 1) for _ in range(row_count + 1)]
    moves = [[""] * (truth_count + 1) for _ in range(row_count + 1)]
    for row in range(1, row_count + 1):
        scores[row][0] = row * gap_penalty
        moves[row][0] = "extra"
    for column in range(1, truth_count + 1):
        scores[0][column] = column * gap_penalty
        moves[0][column] = "missing"
    for row in range(1, row_count + 1):
        for column in range(1, truth_count + 1):
            options = (
                (
                    scores[row - 1][column - 1]
                    + match_score(
                        extracted_records[row - 1],
                        ground_truth_records[column - 1],
                    ),
                    "match",
                ),
                (scores[row - 1][column] + gap_penalty, "extra"),
                (scores[row][column - 1] + gap_penalty, "missing"),
            )
            scores[row][column], moves[row][column] = max(options, key=lambda item: item[0])

    aligned_pairs: list[tuple[dict | None, dict | None]] = []
    row, column = row_count, truth_count
    while row or column:
        move = moves[row][column]
        if move == "match":
            aligned_pairs.append((
                extracted_records[row - 1],
                ground_truth_records[column - 1],
            ))
            row -= 1
            column -= 1
        elif move == "extra":
            aligned_pairs.append((extracted_records[row - 1], None))
            row -= 1
        else:
            aligned_pairs.append((None, ground_truth_records[column - 1]))
            column -= 1
    aligned_pairs.reverse()

    extracted_by_row: dict[int, dict] = {}
    ground_truth_by_row: dict[int, dict] = {}
    for row_number, (extracted, ground_truth) in enumerate(aligned_pairs, start=1):
        if extracted is not None:
            extracted_by_row[row_number] = extracted
        if ground_truth is not None:
            ground_truth_by_row[row_number] = ground_truth
    return extracted_by_row, ground_truth_by_row


def compare(extracted_json: str, gt_xlsx: str, gt_sheet: str, year: int,
            physical_page: int,
            schedule_type: str = "population",
            ground_truth_row_range: tuple[int, int] | None = None) -> tuple[dict, list]:
    """
    Compare extracted records against ONE physical page of ground truth.

    physical_page: 1-indexed physical page number within gt_sheet, matching
    the "Sheet Number" printed/handwritten on your source scan image. Page 1
    is the first physical page in the sheet, not necessarily "Enumeration
    District X sheet 1" -- always cross-check against the scan itself.
    """
    with open(extracted_json) as f:
        data = json.load(f)
    records = data["records"]
    extraction_diag = data.get("diagnostics", {})
    if data.get("census_year", year) != year or data.get("schedule_type", schedule_type) != schedule_type:
        raise ValueError("Extraction year/schedule does not match the requested ground truth.")
    extracted_sheet = data.get("sheet_name")
    if extracted_sheet is not None and extracted_sheet != gt_sheet:
        raise ValueError(
            f"Extraction sheet_name {extracted_sheet!r} does not match ground truth sheet {gt_sheet!r}."
        )
    schema = get_census_schema(year, schedule_type, sheet_name=gt_sheet)

    gt_df = load_ground_truth(gt_xlsx, gt_sheet)
    line_col = find_line_number_column(gt_df.columns)
    if ground_truth_row_range is not None:
        start, end = ground_truth_row_range
        if not (0 <= start < end <= len(gt_df)):
            raise ValueError("ground_truth_row_range must be 0-based [start,end) within the selected sheet.")
        page_df = gt_df.iloc[start:end].reset_index(drop=True)
        page_count = None  # The confirmed range establishes this page, not all page boundaries.
    elif schema.has_ground_truth_line_number:
        if line_col is None:
            raise ValueError(f"No 'Line Number' column found in sheet '{gt_sheet}'.")
        pages = split_into_physical_pages(gt_df, line_col, year=year)
        page_count = len(pages)
        if not (1 <= physical_page <= page_count):
            raise ValueError(f"physical_page={physical_page} out of range (1..{page_count}).")
        page_df = pages[physical_page - 1]
    else:
        raise ValueError("This workbook has no physical line numbers. Supply a confirmed "
                         "ground_truth_row_range=(start,end); fixed-size page guesses are unsafe.")

    gt_records = page_df.to_dict(orient="records")
    if schema.has_ground_truth_line_number:
        def unique_lines(rows, column, source):
            indexed = {}
            for row in rows:
                value = row.get(column)
                if value is None or pd.isna(value):
                    raise ValueError(f"{source} row lacks a physical line number; resolve alignment first.")
                number = float(value)
                if not number.is_integer() or number < 1:
                    raise ValueError(f"Invalid physical line number: {value}")
                line = int(number)
                if line in indexed:
                    raise ValueError(f"Duplicate line {line} in {source}; cannot safely compare.")
                indexed[line] = row
            return indexed
        ext_by_line = unique_lines(records, "Line Number", "extraction")
        gt_by_line = unique_lines(gt_records, line_col, "ground truth")
    else:
        ext_by_line, gt_by_line = align_by_household_order(records, gt_records)

    # Resolve the selected sheet's labels to the same canonical field IDs as
    # extraction. Never treat the year-level preferred alias as the only spelling.
    mapped_ids = {canonical_field(column) for column in schema.columns}
    compare_columns = [column for column in page_df.columns if canonical_field(str(column)) in mapped_ids]
    if len({canonical_field(str(c)) for c in compare_columns}) != len(compare_columns):
        raise ValueError("Ambiguous duplicate field aliases in the selected sheet.")
    if not compare_columns:
        raise ValueError("No image-transcribable fields map to the selected sheet.")
    priority_ids = {canonical_field(c) for c in schema.priority_fields}
    priority_fields = [c for c in compare_columns if canonical_field(c) in priority_ids]
    results = []
    field_scores = {c: [] for c in compare_columns}
    priority_scores = {c: [] for c in compare_columns if c in priority_fields}

    for line_num in sorted(set(gt_by_line) | set(ext_by_line)):
        gt_row = gt_by_line.get(line_num, {})
        ext_row = ext_by_line.get(line_num, {})

        source_line = ext_row.get("Line Number", ext_row.get("_line_number"))
        row_result = {"line_number": source_line if not schema.has_ground_truth_line_number else line_num,
                      "alignment_position": line_num, "extraction_line_number": source_line,
                      "row_present_in_extraction": bool(ext_row), "row_present_in_ground_truth": bool(gt_row),
                      "all_match": bool(ext_row) and bool(gt_row), "fields": {}}
        ext_fields = {}
        for key, value in ext_row.items():
            field_id = canonical_field(key)
            if field_id in ext_fields:
                raise ValueError(f"Ambiguous aliases in extraction: {key}")
            ext_fields[field_id] = value
        n_fields = 0
        n_matched = 0

        for field in compare_columns:
            strategy = classify_strategy(field)
            gt_val = gt_row.get(field)
            ext_val = ext_fields.get(canonical_field(field))
            matched = bool(ext_row) and bool(gt_row) and field_match(ext_val, gt_val, strategy)

            row_result["fields"][field] = {
                "extracted": ext_val, "ground_truth": gt_val,
                "match": matched, "strategy": strategy
            }
            field_scores[field].append(matched)
            if field in priority_scores:
                priority_scores[field].append(matched)
            if not matched:
                row_result["all_match"] = False
            n_fields += 1
            if matched:
                n_matched += 1

        row_result["fields_matched"] = n_matched
        row_result["fields_total"] = n_fields
        row_result["field_match_rate"] = n_matched / n_fields if n_fields else 1.0

        results.append(row_result)

    n = len(results)
    priority_scored = {f: sum(s) / len(s) for f, s in priority_scores.items() if s}
    metrics = {
        "census_year": year,
        "schedule_type": schedule_type,
        "sheet": gt_sheet,
        "physical_page": physical_page,
        "total_physical_pages_in_sheet": page_count,
        "rows_compared": n,
        "row_accuracy": sum(r["all_match"] for r in results) / n if n else 0,
        "avg_row_field_match_rate": (
            sum(r["field_match_rate"] for r in results) / n if n else 0
        ),
        "row_accuracy_at_80pct": (
            sum(1 for r in results if r["field_match_rate"] >= ROW_ACCURACY_THRESHOLD) / n
            if n else 0
        ),
        "field_accuracy": {
            f: (sum(scores) / len(scores) if scores else None)
            for f, scores in field_scores.items()
        },
    }
    scored_fields = {k: v for k, v in metrics["field_accuracy"].items() if v is not None}
    metrics["overall_field_accuracy"] = (
        sum(scored_fields.values()) / len(scored_fields) if scored_fields else 0
    )
    metrics["priority_field_accuracy"] = (
        sum(priority_scored.values()) / len(priority_scored) if priority_scored else 0
    )
    metrics["fields_compared"] = compare_columns
    metrics["unscored_workbook_columns"] = [str(c) for c in page_df.columns if c not in compare_columns and c != line_col]
    metrics["ground_truth_row_range"] = ground_truth_row_range

    # Extraction coverage diagnostics (from the Gemini pipeline, if present).
    expected = extraction_diag.get("expected_lines") or []
    missing = extraction_diag.get("missing_lines") or []
    metrics["extraction_coverage"] = (
        (len(expected) - len(missing)) / len(expected) if expected else None
    )
    metrics["missing_lines"] = missing
    metrics["missing_row_rate"] = (len(missing) / len(expected)) if expected else None
    metrics["retry_attempted"] = extraction_diag.get("retry_attempted", False)
    metrics["retry_recovered_lines"] = extraction_diag.get("retry_recovered_lines", [])
    metrics["illegible_lines"] = extraction_diag.get("illegible_lines", [])

    return metrics, results


def print_report(metrics: dict):
    print(f"\n{'='*55}")
    print(f"  ACCURACY REPORT — {metrics['census_year']} Census, sheet '{metrics['sheet']}',"
          f" page {metrics['physical_page']}/{metrics['total_physical_pages_in_sheet']}")
    print(f"{'='*55}")
    print(f"  Rows compared:          {metrics['rows_compared']}")
    print(f"  Row-level accuracy:     {metrics['row_accuracy']:.1%}")
    print(f"  Avg per-row field match:  {metrics['avg_row_field_match_rate']:.1%}")
    print(f"  Row accuracy @{ROW_ACCURACY_THRESHOLD:.0%} fields: {metrics['row_accuracy_at_80pct']:.1%}")
    print(f"  Overall field accuracy: {metrics['overall_field_accuracy']:.1%}")
    if metrics.get("priority_field_accuracy") is not None:
        print(f"  Priority field accuracy:  {metrics['priority_field_accuracy']:.1%}")
    print(f"  (Compared {len(metrics.get('fields_compared', []))} extracted fields)")
    cov = metrics.get("extraction_coverage")
    if cov is not None:
        print(f"  Extraction coverage:    {cov:.1%}"
              f"  (missing lines: {metrics.get('missing_lines') or 'none'})")
        if metrics.get("retry_attempted"):
            print(f"  Retry recovered lines:  {metrics.get('retry_recovered_lines') or 'none'}")
        if metrics.get("illegible_lines"):
            print(f"  Illegible lines:        {metrics.get('illegible_lines')}")
    print(f"\n  Field breakdown (worst → best, only fields with data):")
    scored = {k: v for k, v in metrics["field_accuracy"].items() if v is not None}
    for field, acc in sorted(scored.items(), key=lambda x: x[1]):
        bar = "█" * int(acc * 25)
        print(f"    {field:<35} {acc:.1%}  {bar}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted", required=True)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--schedule", default="population",
                        choices=["population", "slave"])
    parser.add_argument("--page", type=int, required=True,
                        help="1-indexed physical page within --sheet, matching your scan")
    parser.add_argument("--gt-row-range", type=int, nargs=2, metavar=("START", "END"),
                        help="Confirmed 0-based data-row slice [START,END), required when no line numbers exist")
    parser.add_argument("--save", default=str(RESULTS_DIR / "comparison.json"))
    args = parser.parse_args()

    metrics, results = compare(args.extracted, args.ground_truth, args.sheet,
                                args.year, args.page, args.schedule,
                                tuple(args.gt_row_range) if args.gt_row_range else None)
    print_report(metrics)

    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    with open(args.save, "w") as f:
        json.dump({"metrics": metrics, "results": results}, f, indent=2)
    print(f"  Detailed results → {args.save}")

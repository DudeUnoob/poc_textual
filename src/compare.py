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
from pathlib import Path
from fuzzywuzzy import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import RESULTS_DIR
from utils import HAS_LINE_NUMBER, COMPARE_FIELDS_1950, PRIORITY_FIELDS_1950

COMPARE_FIELDS_BY_YEAR = {1950: COMPARE_FIELDS_1950}
PRIORITY_FIELDS_BY_YEAR = {1950: PRIORITY_FIELDS_1950}

# Keyword → match strategy. Order matters: more specific keywords first.
STRATEGY_KEYWORDS = [
    (["race"], "exact"),
    (["gender", "sex"], "exact"),
    (["marital status"], "exact"),
    (["age"], "numeric"),
    (["surname", "given name", "first name", "last name"], "fuzzy_name"),
    (["relation"], "fuzzy"),
    (["birthplace", "birth place"], "fuzzy"),
    (["occupation", "industry"], "fuzzy"),
]

def classify_strategy(field_name: str) -> str:
    fl = field_name.lower()
    for keywords, strategy in STRATEGY_KEYWORDS:
        if any(k in fl for k in keywords):
            return strategy
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


def split_into_physical_pages(gt_df: pd.DataFrame, line_col: str) -> list[pd.DataFrame]:
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
        if pd.notna(curr) and curr == 1 and (pd.isna(prev) or prev != 1):
            page_starts.append(i)
    page_starts.append(len(gt_df))

    pages = []
    for start, end in zip(page_starts, page_starts[1:]):
        pages.append(gt_df.iloc[start:end].reset_index(drop=True))
    return pages


def compare(extracted_json: str, gt_xlsx: str, gt_sheet: str, year: int,
            physical_page: int) -> tuple[dict, list]:
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

    gt_df = load_ground_truth(gt_xlsx, gt_sheet)

    if not HAS_LINE_NUMBER.get(year, True):
        raise ValueError(
            f"{year} sheets have no Line Number column (confirmed absent in "
            f"all 1860 sheets). Row alignment needs a different key for this "
            f"decade -- do not assume line-number join will work."
        )

    line_col = find_line_number_column(gt_df.columns)
    if line_col is None:
        raise ValueError(f"No 'Line Number' column found in sheet '{gt_sheet}'.")

    pages = split_into_physical_pages(gt_df, line_col)
    if not (1 <= physical_page <= len(pages)):
        raise ValueError(
            f"physical_page={physical_page} out of range -- sheet '{gt_sheet}' "
            f"contains {len(pages)} physical pages (1..{len(pages)})."
        )
    page_df = pages[physical_page - 1]

    gt_records = page_df.to_dict(orient="records")
    ext_by_line = {int(r["Line Number"]): r for r in records if r.get("Line Number") is not None}
    gt_by_line = {int(r[line_col]): r for r in gt_records if r.get(line_col) is not None}

    allowlist = COMPARE_FIELDS_BY_YEAR.get(year)
    if allowlist:
        compare_columns = [c for c in allowlist if c in page_df.columns]
    else:
        extracted_keys = {k for r in records for k in r if k != line_col}
        compare_columns = [c for c in page_df.columns if c and c != line_col and c in extracted_keys]

    priority_fields = PRIORITY_FIELDS_BY_YEAR.get(year, [])
    results = []
    field_scores = {c: [] for c in compare_columns}
    priority_scores = {c: [] for c in compare_columns if c in priority_fields}

    for line_num in sorted(gt_by_line.keys()):
        gt_row = gt_by_line[line_num]
        ext_row = ext_by_line.get(line_num, {})

        row_result = {"line_number": line_num, "all_match": True, "fields": {}}

        for field in compare_columns:
            strategy = classify_strategy(field)
            gt_val = gt_row.get(field)
            ext_val = ext_row.get(field)
            matched = field_match(ext_val, gt_val, strategy)

            row_result["fields"][field] = {
                "extracted": ext_val, "ground_truth": gt_val,
                "match": matched, "strategy": strategy
            }
            field_scores[field].append(matched)
            if field in priority_scores:
                priority_scores[field].append(matched)
            if not matched:
                row_result["all_match"] = False

        results.append(row_result)

    n = len(results)
    priority_scored = {f: sum(s) / len(s) for f, s in priority_scores.items() if s}
    metrics = {
        "census_year": year,
        "sheet": gt_sheet,
        "physical_page": physical_page,
        "total_physical_pages_in_sheet": len(pages),
        "rows_compared": n,
        "row_accuracy": sum(r["all_match"] for r in results) / n if n else 0,
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

    return metrics, results


def print_report(metrics: dict):
    print(f"\n{'='*55}")
    print(f"  ACCURACY REPORT — {metrics['census_year']} Census, sheet '{metrics['sheet']}',"
          f" page {metrics['physical_page']}/{metrics['total_physical_pages_in_sheet']}")
    print(f"{'='*55}")
    print(f"  Rows compared:          {metrics['rows_compared']}")
    print(f"  Row-level accuracy:     {metrics['row_accuracy']:.1%}")
    print(f"  Overall field accuracy: {metrics['overall_field_accuracy']:.1%}")
    if metrics.get("priority_field_accuracy") is not None:
        print(f"  Priority field accuracy:  {metrics['priority_field_accuracy']:.1%}")
    print(f"  (Compared {len(metrics.get('fields_compared', []))} extracted fields)")
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
    parser.add_argument("--page", type=int, required=True,
                        help="1-indexed physical page within --sheet, matching your scan")
    parser.add_argument("--save", default=str(RESULTS_DIR / "comparison.json"))
    args = parser.parse_args()

    metrics, results = compare(args.extracted, args.ground_truth, args.sheet,
                                args.year, args.page)
    print_report(metrics)

    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    with open(args.save, "w") as f:
        json.dump({"metrics": metrics, "results": results}, f, indent=2)
    print(f"  Detailed results → {args.save}")

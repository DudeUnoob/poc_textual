"""
Turn real pipeline output (extract.py JSON) + ground truth (xlsx) into the
(incorrect, high_confidence) cells that confidence_reliability_test.py needs,
then run Jayden's test.

Run from the repo root (poc_textual). Example:
  python convert_to_test_format.py ^
    --run results/page1.json 1 --run results/page2.json 2 ^
    --ground-truth "path/to/GT.xlsx" --sheet "Bastrop 11-2A" --year 1950

--match lenient : "Incorrect" = compare.py says no match (same as team accuracy)
--match strict  : "Incorrect" = normalized text differs at all (verbatim standard)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from compare import compare, _norm  # noqa: E402
from confidence_reliability_test import run_confidence_test, scenario_breakdown  # noqa: E402


def _strict_equal(ext, gt) -> bool:
    e, g = _norm(ext), _norm(gt)
    if e == g:
        return True
    try:  # 35 vs 35.0 is the same number; 35 vs 34 is not
        return float(e) == float(g)
    except (ValueError, TypeError):
        return False


def collect_rows(run_json, page, gt_xlsx, sheet, year, confident_levels):
    with open(run_json) as f:
        data = json.load(f)
    if "field_candidates" not in data:
        raise ValueError(
            f"{run_json} has no 'field_candidates' (older output format). "
            "Re-run extraction with the current pipeline, or ask Damodar for a newer file."
        )
    conf = {}
    for c in data["field_candidates"]:
        conf[(int(c["line_number"]), c["field"])] = c.get("model_confidence")

    _, results = compare(str(run_json), gt_xlsx, sheet, year, page)
    rows = []
    for r in results:
        for field, d in r["fields"].items():
            level = conf.get((r["line_number"], field))
            both_blank = _norm(d["extracted"]) == "" and _norm(d["ground_truth"]) == ""
            incorrect = not d["match"]
            incorrect_strict = not _strict_equal(d["extracted"], d["ground_truth"])
            high = (level or "").lower() in confident_levels
            rows.append({
                "Sheet": sheet, "Page": page, "Line": r["line_number"], "Field": field,
                "Ground_Truth": d["ground_truth"], "AI_Value": d["extracted"],
                "Model_Confidence": level or "(none)",
                "Incorrect": incorrect, "Incorrect_Strict": incorrect_strict,
                "High_Confidence": high, "Both_Blank": both_blank,
                "BAD_SCENARIO": incorrect and high,
                "BAD_SCENARIO_STRICT": incorrect_strict and high,
            })
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", nargs=2, action="append", metavar=("JSON", "PAGE"), required=True)
    p.add_argument("--ground-truth", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--year", type=int, default=1950)
    p.add_argument("--tolerance", type=float, default=0.02)
    p.add_argument("--match", choices=["lenient", "strict"], default="lenient")
    p.add_argument("--confident-levels", default="high", help="comma list, e.g. high,medium")
    p.add_argument("--exclude-both-blank", action="store_true")
    p.add_argument("--out", default=None, help="optional .xlsx with every cell")
    a = p.parse_args()

    levels = {x.strip().lower() for x in a.confident_levels.split(",")}
    rows = []
    for path, page in a.run:
        rows += collect_rows(path, int(page), a.ground_truth, a.sheet, a.year, levels)
    if a.exclude_both_blank:
        rows = [r for r in rows if not r["Both_Blank"]]

    key = "Incorrect" if a.match == "lenient" else "Incorrect_Strict"
    cells = [(r[key], r["High_Confidence"]) for r in rows]
    print(f"\nMatch mode: {a.match} | confident = {sorted(levels)} | "
          f"both-blank cells {'excluded' if a.exclude_both_blank else 'included'}")
    print(f"Cells: {len(cells)}")
    for k, v in scenario_breakdown(cells).items():
        print(f"  {v:>5}  {k}")
    print(run_confidence_test(cells, a.tolerance))

    if a.out:
        import pandas as pd
        pd.DataFrame(rows).to_excel(a.out, index=False)
        print(f"\nCell-level detail -> {a.out}")


if __name__ == "__main__":
    main()

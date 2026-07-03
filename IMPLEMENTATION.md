# Implementation Guide — Census OCR Pipeline

## Setup

```bash
mkdir census-ocr-poc && cd census-ocr-poc
python -m venv venv && source venv/bin/activate
pip install anthropic openpyxl pandas opencv-python Pillow fuzzywuzzy python-levenshtein python-dotenv tqdm jupyter
```

`.env`:
```
ANTHROPIC_API_KEY=your_key_here
```

---

## src/preprocess.py — Image Enhancement Before LLM

```python
"""
Enhance census scan images before sending to vision LLM.
Goal: improve handwriting legibility, reduce token waste on margins.
"""
import cv2
import numpy as np
from PIL import Image
from pathlib import Path


def enhance_census_image(image_path: str, output_path: str = None) -> np.ndarray:
    """
    Deskew, denoise, and enhance contrast on a census scan.
    Returns processed image as numpy array.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    
    # 1. Deskew (correct rotation from scanning)
    img = _deskew(img)
    
    # 2. Denoise
    img = cv2.fastNlMeansDenoising(img, h=10)
    
    # 3. Adaptive threshold — improves ink vs paper contrast
    img = cv2.adaptiveThreshold(
        img, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15, C=8
    )
    
    # 4. Slight sharpening
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)
    
    if output_path:
        cv2.imwrite(output_path, img)
    
    return img


def _deskew(img: np.ndarray) -> np.ndarray:
    """Detect and correct skew angle."""
    coords = np.column_stack(np.where(img < 128))
    if len(coords) == 0:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:  # Skip if nearly straight
        return img
    h, w = img.shape
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def image_to_base64(image_path: str) -> tuple[str, str]:
    """
    Load (and optionally enhance) image, return (base64_string, media_type).
    """
    import base64
    
    # Enhance first
    enhanced = enhance_census_image(image_path)
    
    # Convert back to JPEG bytes
    _, buffer = cv2.imencode('.jpg', enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])
    b64 = base64.standard_b64encode(buffer).decode('utf-8')
    
    return b64, "image/jpeg"
```

---

## src/utils.py — Schema, Ditto Propagation, Normalization

**All values below were verified directly against the ground truth XLSX files —
not assumed from historical census documentation.** See CLAUDE.md section
"VERIFIED Data Quality Findings" for the investigation behind these choices.

```python
"""
Shared utilities: schema loading, ditto mark propagation, field normalization.

IMPORTANT: The ground truth files use a research-team controlled vocabulary for
Race that is richer than what's handwritten on the census form, and that
vocabulary is NOT consistent across decades. RACE_NORMALIZE_MAP below maps raw
values a vision model would read off the image to the correct ground-truth
string for a given decade. If a formal codebook exists from the research team,
replace this map with it.
"""
import json
from pathlib import Path

# Column that holds the person's birthplace, keyed by decade (verified — not uniform!)
BIRTHPLACE_COLUMN = {
    1850: "Birth Place", 1860: "Birth Place", 1950: "Birth Place",
    1870: "Birthplace", 1880: "Birthplace", 1900: "Birthplace",
    1910: "Birthplace", 1920: "Birthplace", 1930: "Birthplace", 1940: "Birthplace",
}

FATHER_BIRTHPLACE_COLUMN = {
    1880: "Father's Birthplace", 1900: "Father's Birthplace",
    1910: "Father's Birthplace", 1920: "Father's Birthplace",
    1930: "Father's Birthplace",
    1940: "Father's Birth Place",
    1950: "Father Birth Place",
    # 1850, 1860, 1870 have no father's birthplace column
}

# 1920 uses "Sex" instead of "Gender" — every other decade uses "Gender"
GENDER_COLUMN = {1920: "Sex"}  # falls back to "Gender" for all other years

# Does this decade's sheets have a Line Number column? (1860 = no, confirmed
# across all 17 sheets in that workbook)
HAS_LINE_NUMBER = {
    1850: True,   # true for 2 of 3 sheets (Slave Schedule lacks it)
    1860: False,  # NO sheet in the 1860 workbook has Line Number
    1870: True, 1880: True, 1900: True, 1910: True,
    1920: True, 1930: True, 1940: True, 1950: True,
}

# Race values ACTUALLY FOUND in the ground truth, per decade (verified by
# scanning every sheet in every workbook — do not add "textbook" categories
# that were not observed; the research team is not using a fixed universal set)
VALID_RACE = {
    1850: ["White", "Black", "Mulatto"],
    1860: ["White", "Black", "Mulatto", "Indian (Native American)"],
    1870: ["White", "Black", "Mulatto"],
    1880: ["White", "Black", "Mulatto", "Filipino"],
    1900: ["White", "Black", "Mulatto", "Chinese", "Mexican (Latino)"],
    1910: ["White", "Black", "Mulatto", "Octoroon", "Mexican (Latino)", "Other"],
    1920: ["White", "Black", "Mulatto", "Mexican (Latino)"],
    1930: ["White", "Black", "Negro (Black)", "Mulatto", "Mexican (Latino)"],
    1940: ["White", "Negro (Black)"],
    1950: ["White", "Negro (Black)", "Chinese", "W0", "WO"],
}

# Maps what a vision model reads directly off the FORM (raw handwritten
# code/word) to the ground-truth controlled-vocabulary string, per decade.
# NOTE: cannot produce "W0"/"WO" (1950) or resolve "Black" vs "Negro (Black)"
# (1930) from image alone — these require research-team business logic on top.
RACE_NORMALIZE_MAP = {
    1850: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto"},
    1860: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "In": "Indian (Native American)", "Ind": "Indian (Native American)"},
    1870: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto"},
    1880: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "Fil": "Filipino"},
    1900: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "Ch": "Chinese", "Mex": "Mexican (Latino)"},
    1910: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "Ot": "Octoroon", "Mex": "Mexican (Latino)"},
    1920: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "Mex": "Mexican (Latino)"},
    1930: {"W": "White", "B": "Black", "Mu": "Mulatto", "Mul": "Mulatto",
           "Mex": "Mexican (Latino)", "Neg": "Negro (Black)"},  # confirm canonical w/ Jaden
    1940: {"W": "White", "Ne": "Negro (Black)", "Neg": "Negro (Black)"},
    1950: {"W": "White", "Neg": "Negro (Black)", "Ch": "Chinese"},
    # "W0"/"WO" deliberately excluded — cannot be derived from the image (see CLAUDE.md)
}

VALID_GENDER = ["Male", "Female"]

# Marital status vocabulary — confirmed different for 1950 vs earlier decades
VALID_MARITAL_STATUS = {
    1880: ["Married", "Single", "Widowed", "Widower", "Divorced", "Na"],
    1900: ["Married", "Single", "Widowed", "Divorced"],
    1910: ["Married", "Single", "Widowed", "Divorced"],
    1920: ["Married", "Single", "Widowed", "Divorced"],
    1930: ["Married", "Single", "Widowed", "Divorced"],
    1940: ["Married", "Single", "Widowed", "Divorced"],
    1950: ["Married", "Never Married (Single)", "Widowed", "Divorced", "Separated"],
    # 1870 has no Marital Status column; 1850/1860 use "Married within the Year" (Y/N) instead
}

# Maps the raw abbreviation a vision model reads off the form (Mar, Wd, D, S...)
# to the ground-truth string for that decade. Confirmed necessary: fuzzy string
# matching FAILS on these (e.g. "mar" vs "married" scores only 60/100, well
# below any reasonable threshold; "d" vs "divorced" scores 22/100) — an exact
# raw-vs-expanded comparison would silently mark every correct extraction as
# wrong without this normalization step.
MARITAL_STATUS_NORMALIZE_MAP = {
    1950: {"Mar": "Married", "Wd": "Widowed", "D": "Divorced", "Sep": "Separated",
           "S": "Never Married (Single)"},
    # For 1880-1940, ground truth already uses "Single" not "Never Married (Single)"
    "default": {"Mar": "Married", "M": "Married", "Wd": "Widowed", "W": "Widowed",
                "D": "Divorced", "S": "Single", "Sep": "Separated"},
}


def normalize_marital_status(raw: str, year: int) -> str:
    """Map a raw vision-model marital-status reading to the ground-truth string."""
    if not raw:
        return raw
    raw = raw.strip()
    decade_map = MARITAL_STATUS_NORMALIZE_MAP.get(year, MARITAL_STATUS_NORMALIZE_MAP["default"])
    if raw in decade_map:
        return decade_map[raw]
    valid = VALID_MARITAL_STATUS.get(year, [])
    for v in valid:
        if raw.lower() == v.lower():
            return v
    return raw


def propagate_dittos(records: list[dict], surname_field: str = "Surname",
                     birthplace_field: str = "Birthplace") -> list[dict]:
    """
    Census enumerators used ditto marks (— or ") for repeated values within a
    household. Propagate surname and birthplace forward when blank/ditto.
    """
    prev_surname, prev_birthplace = None, None
    ditto_markers = [None, "", "——", '"', "''", "ditto", "do", "Do", "DO"]

    for rec in records:
        surname = rec.get(surname_field)
        if surname in ditto_markers:
            if prev_surname:
                rec[surname_field] = prev_surname
        else:
            prev_surname = surname

        birthplace = rec.get(birthplace_field)
        if birthplace in ditto_markers:
            if prev_birthplace:
                rec[birthplace_field] = prev_birthplace
        else:
            prev_birthplace = birthplace

    return records


def normalize_race(raw: str, year: int) -> str:
    """Map a raw vision-model race reading to the ground-truth controlled
    vocabulary for the given decade. Falls back to title-casing if unmapped."""
    if not raw:
        return raw
    raw = raw.strip()
    decade_map = RACE_NORMALIZE_MAP.get(year, {})
    if raw in decade_map:
        return decade_map[raw]
    # Already a full word matching a known value for this decade
    valid = VALID_RACE.get(year, [])
    for v in valid:
        if raw.lower() == v.lower():
            return v
    return raw  # leave as-is; will get flagged by validate.py


def normalize_gender(raw: str) -> str:
    if not raw:
        return raw
    raw = raw.strip().upper()
    if raw in ["M", "MALE"]:
        return "Male"
    if raw in ["F", "FEMALE"]:
        return "Female"
    return raw


def gender_column_for_year(year: int) -> str:
    return GENDER_COLUMN.get(year, "Gender")


def birthplace_column_for_year(year: int) -> str:
    return BIRTHPLACE_COLUMN.get(year, "Birthplace")
```

---

## src/extract.py — Core Extraction Pipeline

```python
"""
Main extraction pipeline: census image → structured JSON records via vision LLM.
"""
import anthropic
import json
import re
import argparse
from pathlib import Path
from dotenv import load_dotenv
from preprocess import image_to_base64
from utils import (propagate_dittos, normalize_race, normalize_gender,
                    normalize_marital_status, BIRTHPLACE_COLUMN, GENDER_COLUMN)

load_dotenv()


def load_prompt(year: int) -> tuple[str, str]:
    """Load system + user prompt for a given census decade."""
    prompts_dir = Path("prompts")
    system = (prompts_dir / "system.txt").read_text()
    decade_file = prompts_dir / f"{year}.txt"
    user = decade_file.read_text() if decade_file.exists() else \
           (prompts_dir / "generic.txt").read_text()
    return system, user


def extract_from_image(image_path: str, year: int,
                       client: anthropic.Anthropic = None) -> list[dict]:
    """
    Send census image to Claude vision API. Returns list of person records.
    """
    if client is None:
        client = anthropic.Anthropic()
    
    b64_data, media_type = image_to_base64(image_path)
    system_prompt, user_prompt = load_prompt(year)
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64_data}
                },
                {"type": "text", "text": user_prompt}
            ]
        }]
    )
    
    raw = response.content[0].text
    # Strip any markdown fences the model might add
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    
    records = json.loads(raw)
    
    # Post-process. Birthplace column name genuinely varies by decade
    # ("Birth Place" for 1850/1860/1950, "Birthplace" for everything else --
    # verified, see CLAUDE.md) so it must be looked up per-decade here, not
    # hardcoded, or ditto propagation silently no-ops on the wrong field.
    birthplace_field = BIRTHPLACE_COLUMN.get(year, "Birthplace")
    records = propagate_dittos(records, surname_field="Surname",
                                birthplace_field=birthplace_field)
    gender_field = GENDER_COLUMN.get(year, "Gender")
    for rec in records:
        if "Race" in rec:
            rec["Race"] = normalize_race(rec["Race"], year)
        if "Marital Status" in rec:
            rec["Marital Status"] = normalize_marital_status(rec["Marital Status"], year)
        if gender_field in rec:
            rec[gender_field] = normalize_gender(rec[gender_field])
        elif "Gender" in rec:
            rec["Gender"] = normalize_gender(rec["Gender"])
    
    return records


def process_sheet(image_path: str, year: int, output_path: str) -> list[dict]:
    """Extract one sheet and save results."""
    print(f"Extracting: {image_path} (year={year})")
    client = anthropic.Anthropic()
    records = extract_from_image(image_path, year, client)
    
    output = {
        "source_image": str(image_path),
        "census_year": year,
        "record_count": len(records),
        "records": records
    }
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"  → {len(records)} records saved to {output_path}")
    return records


def process_batch(image_dir: str, year: int, output_dir: str):
    """Process all images in a directory."""
    from tqdm import tqdm
    images = sorted(Path(image_dir).glob("*.jpg")) + \
             sorted(Path(image_dir).glob("*.jpeg"))
    
    client = anthropic.Anthropic()
    all_records = []
    
    for img_path in tqdm(images, desc=f"Processing {year} census"):
        out_path = Path(output_dir) / f"{img_path.stem}_extracted.json"
        if out_path.exists():
            print(f"  Skipping {img_path.name} (already done)")
            continue
        try:
            records = extract_from_image(str(img_path), year, client)
            process_sheet(str(img_path), year, str(out_path))
            all_records.extend(records)
        except Exception as e:
            print(f"  ERROR on {img_path.name}: {e}")
    
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--output", default="data/outputs/extracted.json")
    args = parser.parse_args()
    process_sheet(args.image, args.year, args.output)
```

---

## src/compare.py — Accuracy Comparison

```python
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
from pathlib import Path
from fuzzywuzzy import fuzz
from utils import HAS_LINE_NUMBER

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

    # Compare every ground-truth column that exists in the extracted schema too
    compare_columns = [c for c in page_df.columns if c and c != line_col]

    results = []
    field_scores = {c: [] for c in compare_columns}

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
            if not matched:
                row_result["all_match"] = False

        results.append(row_result)

    n = len(results)
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

    return metrics, results


def print_report(metrics: dict):
    print(f"\n{'='*55}")
    print(f"  ACCURACY REPORT — {metrics['census_year']} Census, sheet '{metrics['sheet']}',"
          f" page {metrics['physical_page']}/{metrics['total_physical_pages_in_sheet']}")
    print(f"{'='*55}")
    print(f"  Rows compared:          {metrics['rows_compared']}")
    print(f"  Row-level accuracy:     {metrics['row_accuracy']:.1%}")
    print(f"  Overall field accuracy: {metrics['overall_field_accuracy']:.1%}")
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
    parser.add_argument("--save", default="results/comparison.json")
    args = parser.parse_args()

    metrics, results = compare(args.extracted, args.ground_truth, args.sheet,
                                args.year, args.page)
    print_report(metrics)

    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    with open(args.save, "w") as f:
        json.dump({"metrics": metrics, "results": results}, f, indent=2)
    print(f"  Detailed results → {args.save}")
```

---

## src/validate.py — Rule-Based Field Validation

```python
"""
Post-extraction validation: flag records with impossible or suspicious values.
Run AFTER extraction (and normalization), BEFORE comparison.
"""
from utils import VALID_RACE, VALID_GENDER, VALID_MARITAL_STATUS

def validate_records(records: list[dict], year: int) -> list[dict]:
    valid_races = [r.lower() for r in VALID_RACE.get(year, [])]
    valid_marital = [m.lower() for m in VALID_MARITAL_STATUS.get(year, [])]

    for rec in records:
        warnings = []

        race = str(rec.get("Race", "") or "").strip()
        if race and valid_races and race.lower() not in valid_races:
            warnings.append(
                f"Race '{race}' not in observed ground-truth values for {year}: "
                f"{VALID_RACE.get(year)}. This may be correct (a value the "
                f"ground truth simply hasn't seen yet) or an extraction error "
                f"-- flag for human review rather than auto-rejecting."
            )

        gender_field = "Sex" if year == 1920 else "Gender"
        gender = str(rec.get(gender_field) or rec.get("Gender") or rec.get("Sex") or "").strip()
        if gender and gender not in VALID_GENDER:
            warnings.append(f"Invalid gender: '{gender}'")

        if valid_marital:
            marital = str(rec.get("Marital Status", "") or "").strip()
            if marital and marital.lower() not in valid_marital:
                warnings.append(
                    f"Marital status '{marital}' not in observed values for {year}: "
                    f"{VALID_MARITAL_STATUS.get(year)}"
                )

        age = rec.get("Age")
        if age is not None:
            try:
                a = int(float(age))
                if not (0 <= a <= 120):
                    warnings.append(f"Suspicious age: {a}")
            except (ValueError, TypeError):
                warnings.append(f"Non-numeric age: '{age}'")

        for field, val in rec.items():
            if val == "[illegible]":
                warnings.append(f"Illegible: '{field}'")

        rec["_warnings"] = warnings

    flagged = sum(1 for r in records if r.get("_warnings"))
    print(f"Validation: {flagged}/{len(records)} records flagged")
    return records
```

---

## Running the Full POC

```bash
# Step 1: Extract (image 2 from your upload = Line 1 "Lewis Jasper H")
python src/extract.py \
  --image data/raw_images/43290879-Texas-112079-0002.jpg \
  --year 1950 \
  --output data/outputs/sheet2_extracted.json

# Step 2: Compare against ground truth
# CONFIRMED: sheet "Bastrop 11-2A", PHYSICAL PAGE 1 (of 24 pages concatenated
# in that sheet) is the exact match for this image (Line 1 = Lewis Jasper H)
# and has no known data-entry typos. Do not omit --page -- the sheet contains
# 24 physical census pages stacked together with Line Number resetting each
# time (see CLAUDE.md finding #7); comparing without specifying the page will
# silently match against the wrong data.
python src/compare.py \
  --extracted data/outputs/sheet2_extracted.json \
  --ground-truth "data/ground_truth/Bastrop County 1950 Clean.xlsx" \
  --sheet "Bastrop 11-2A" \
  --year 1950 \
  --page 1 \
  --save results/poc_accuracy.json
```

Avoid `Bastrop all Manipulated`, `Bastrop 11-2B`, `Smithville 11-7/8/9`, and
`Bastrop 11-1` as comparison targets until the stray Race values in those
sheets (`'Whiite'`, `'White0'`, `'Wo'`, `'1'`, `'72'`, `'S'`) are cleaned up —
otherwise your accuracy numbers will be artificially penalized by ground-truth
errors, not extraction errors.

---

## Cost Estimates

| Scale | Images | Est. Cost |
|-------|--------|-----------|
| POC (1 district, 1950) | ~5 images | ~$0.50 |
| Full 1950 Bastrop | ~26 sheets | ~$2–3 |
| All decades, all places | ~2,000 images | ~$150–200 |
| With Batch API (50% off) | ~2,000 images | ~$75–100 |

Use `anthropic.Anthropic().messages.batches.create(...)` for production scale.

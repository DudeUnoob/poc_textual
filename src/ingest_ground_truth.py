"""Index cleaned XLSX ground truth files into schemas/."""
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import GROUND_TRUTH_DIR, SCHEMAS_DIR

YEAR_PATTERN = re.compile(r"Bastrop County (\d{4}) Clean\.xlsx$")


def ingest() -> dict:
    index = {}
    columns_by_decade = {}
    for xlsx in sorted(GROUND_TRUTH_DIR.glob("Bastrop County * Clean.xlsx")):
        match = YEAR_PATTERN.search(xlsx.name)
        if not match:
            continue
        year = match.group(1)
        xl = pd.ExcelFile(xlsx)
        sheets = {}
        decade_columns: set[str] = set()
        for sheet in xl.sheet_names:
            df = pd.read_excel(xlsx, sheet_name=sheet)
            cols = [str(c) for c in df.columns if c and str(c).strip()]
            sheets[sheet] = {"columns": cols, "row_count": len(df)}
            decade_columns.update(cols)
        index[year] = {"file": xlsx.name, "sheets": sheets}
        columns_by_decade[year] = sorted(decade_columns)

    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    (SCHEMAS_DIR / "ground_truth_index.json").write_text(json.dumps(index, indent=2))
    (SCHEMAS_DIR / "columns_by_decade.json").write_text(json.dumps(columns_by_decade, indent=2))
    print(f"Indexed {len(index)} decades -> {SCHEMAS_DIR}")
    for year, meta in sorted(index.items()):
        print(f"  {year}: {len(meta['sheets'])} sheets ({meta['file']})")
    return index


if __name__ == "__main__":
    ingest()

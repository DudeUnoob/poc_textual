"""
Post-extraction validation: flag records with impossible or suspicious values.
Run AFTER extraction (and normalization), BEFORE comparison.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

"""Record measured confidence precision from a cleaned physical census page.

Usage:
python -m workbench.calibrate --extracted path/to/page.json --ground-truth clean.xlsx \
  --sheet "Bastrop 11-1" --page 1 --year 1950
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from compare import compare

from .db import CalibrationBand, SessionLocal, init_db, utc_now


def record_calibration(
    extracted: str,
    ground_truth: str,
    sheet: str,
    page: int,
    year: int,
    schedule_type: str = "population",
) -> dict:
    with open(extracted) as fh:
        payload = json.load(fh)
    schedule_type = payload.get("schedule_type", schedule_type)
    metrics, rows = compare(
        extracted, ground_truth, sheet, year, page, schedule_type
    )
    candidates = {
        (int(c["line_number"]), c["field"]): c
        for c in payload.get("field_candidates", [])
    }
    if not candidates:
        raise ValueError(
            "This extraction has no field_candidates sidecar. Re-run it with the v3 extractor before calibration."
        )
    observed: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        for field, result in row["fields"].items():
            candidate = candidates.get((row["line_number"], field))
            if candidate is None:
                continue
            observed.setdefault((field, candidate.get("model_confidence", "low")), []).append(result["match"])
    with SessionLocal() as session:
        for (field, confidence), matches in observed.items():
            band = session.scalar(select(CalibrationBand).where(
                CalibrationBand.census_year == year,
                CalibrationBand.schedule_type == schedule_type,
                CalibrationBand.field_name == field,
                CalibrationBand.confidence == confidence,
                CalibrationBand.model == payload.get("model", ""),
                CalibrationBand.prompt_version == payload.get("prompt_version", "schema-v1"),
            ))
            if band is None:
                band = CalibrationBand(
                    census_year=year, schedule_type=schedule_type,
                    field_name=field, confidence=confidence,
                    model=payload.get("model", ""),
                    prompt_version=payload.get("prompt_version", "schema-v1"),
                )
                session.add(band)
            band.total = (band.total or 0) + len(matches)
            band.correct = (band.correct or 0) + sum(matches)
            band.updated_at = utc_now()
        session.commit()
    return {"metrics": metrics, "bands": {f"{field}:{confidence}": {"total": len(values), "correct": sum(values)} for (field, confidence), values in observed.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted", required=True)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--page", required=True, type=int)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--schedule", default="population",
                        choices=["population", "slave"])
    args = parser.parse_args()
    init_db()
    print(json.dumps(record_calibration(
        args.extracted, args.ground_truth, args.sheet, args.page,
        args.year, args.schedule,
    ), indent=2))


if __name__ == "__main__":
    main()

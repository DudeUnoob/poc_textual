from __future__ import annotations

from census_schemas import CensusSchema, get_census_schema


def get_schema(
    year: int,
    schedule_type: str = "population",
    sheet_name: str | None = None,
) -> CensusSchema:
    return get_census_schema(year, schedule_type, sheet_name)

from __future__ import annotations

from dataclasses import dataclass

from utils import COMPARE_FIELDS_1950, PRIORITY_FIELDS_1950


@dataclass(frozen=True)
class CensusSchema:
    year: int
    fields: list[str]
    priority_fields: list[str]
    expected_lines: int
    data_top: float
    data_bottom: float


SCHEMAS = {
    1950: CensusSchema(
        year=1950,
        fields=COMPARE_FIELDS_1950,
        priority_fields=PRIORITY_FIELDS_1950,
        expected_lines=30,
        data_top=0.13,
        data_bottom=0.88,
    )
}


def get_schema(year: int) -> CensusSchema:
    try:
        return SCHEMAS[year]
    except KeyError as exc:
        raise ValueError(f"No review schema is configured for {year}.") from exc

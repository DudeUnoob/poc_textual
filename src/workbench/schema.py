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
    field_bounds: dict[str, tuple[float, float]]


SCHEMAS = {
    1950: CensusSchema(
        year=1950,
        fields=COMPARE_FIELDS_1950,
        priority_fields=PRIORITY_FIELDS_1950,
        expected_lines=30,
        data_top=0.32,
        data_bottom=0.645,
        field_bounds={
            "Street Name": (0.265, 0.305),
            "House Number": (0.285, 0.325),
            "Dwelling Number": (0.305, 0.335),
            "Surname": (0.325, 0.415),
            "Given Name": (0.365, 0.455),
            "Relation to Head of House": (0.445, 0.482),
            "Race": (0.475, 0.497),
            "Gender": (0.488, 0.510),
            "Age": (0.500, 0.528),
            "Marital Status": (0.518, 0.545),
            "Birth Place": (0.535, 0.590),
            "Occupation": (0.645, 0.705),
            "Industry": (0.690, 0.748),
            "Worker Class": (0.735, 0.765),
        },
    )
}


def get_schema(year: int) -> CensusSchema:
    try:
        return SCHEMAS[year]
    except KeyError as exc:
        raise ValueError(f"No review schema is configured for {year}.") from exc

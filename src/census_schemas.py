"""Single source of truth for supported census forms."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CensusSchema:
    year: int
    schedule_type: str
    columns: tuple[str, ...]
    priority_fields: tuple[str, ...]
    expected_lines: int
    has_ground_truth_line_number: bool = True
    data_top: float = 0.0
    data_bottom: float = 1.0
    field_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    row_bounds: tuple[float, float] = (0.0, 1.0)


COMMON = ("Surname", "Given Name", "Race", "Gender", "Age")
RELATION = {
    1880: "Relation to Head of House",
    1900: "Relationship",
    1910: "Relationship",
    1920: "Relationship",
    1930: "Relation to Head",
    1940: "Relation to Head of House",
    1950: "Relation to Head of House",
}
BIRTHPLACE = {1850: "Birth Place", 1860: "Birth Place", 1950: "Birth Place"}
EXPECTED_LINES = {
    1850: 40,
    1860: 40,
    1870: 40,
    1880: 50,
    1900: 50,
    1910: 50,
    1920: 50,
    1930: 50,
    1940: 40,
    1950: 30,
}
EXTRA_COLUMNS = {
    1850: (
        "Dwelling Number", "Family Number", "Birth Date", "Occupation", "Industry",
        "Value of Real Estate", "Married within the Year", "Attended School",
        "Cannot Read, Write", "Condition",
    ),
    1860: (
        "Dwelling Number", "Family Number", "Birth Year", "Occupation",
        "Real Estate Value", "Personal Estate Value", "Married Within Year",
        "Attended School", "Cannot Read, Write", "Disability Condition",
    ),
    1870: (
        "Dwelling Number", "Family Number", "Birth Year", "Occupation",
        "Real Estate Value", "Personal Estate Value", "Father of Foreign Birth",
        "Mother of Foreign Birth", "Cannot Read", "Cannot Write", "Condition",
        "Birth Month", "Marriage Month", "Attended School",
        "Male Citizen Over 21", "Denied Voting Rights",
    ),
    1880: (
        "Street", "Dwelling Number", "Family Number", "Marital Status",
        "Occupation", "Father's Birthplace", "Mother's Birthplace",
        "Birth Month", "Birth Year", "Married During Census Year",
        "Months Not Employed", "Sick", "Blind", "Deaf and Dumb", "Idiotic",
        "Insane", "Maimed, Crippled, or Bedridden", "Attended School",
        "Cannot Read", "Cannot Write",
    ),
    1900: (
        "Family Number", "Street", "House Number",
        "Number of Dwelling in Order of Visitation", "Birth Month", "Birth Year",
        "Marital Status", "Years Married", "Number of Children Born",
        "Number of Children Living", "Father's Birthplace",
        "Mother's Birthplace", "Occupation", "Can Read", "Can Write",
        "Can Speak English", "House Owned or Rented", "Farm or House",
        "Immigration Year", "Years in US", "Naturalization",
        "Months Not Employed", "Attended School",
        "House Owned Free or Mortgaged",
    ),
    1910: (
        "Street Name", "Estimated Birth Year", "Marital Status",
        "Number of Years of Present Marriage", "Father's Birthplace",
        "Mother's Birthplace", "Occupation", "Industry",
    ),
    1920: (
        "Street Number", "House Number", "Family Number", "Home Owned or Rented",
        "Home Free or Mortgaged", "Estimated Birth Year", "Marital Status",
        "Father's Birthplace", "Mother's Birthplace", "Occupation", "Industry",
    ),
    1930: (
        "Estimated Birth Year", "Marital Status", "Age at First Marriage",
        "Father's Birthplace", "Mother's Birthplace", "Language Spoken",
        "Occupation", "Industry",
    ),
    1940: (
        "Street Name", "Estimated Birth Year", "Marital Status",
        "Father's Birth Place", "Mother's Birth Place", "Occupation", "Industry",
        "Class of Worker", "Weeks Worked in 1939", "Income",
    ),
    1950: (
        "Street Name", "House Number", "Dwelling Number", "Marital Status",
        "Occupation", "Industry", "Worker Class",
    ),
}
FIELD_BOUNDS_1950 = {
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
}


def _population_schema(year: int) -> CensusSchema:
    gender = "Sex" if year == 1920 else "Gender"
    birthplace = BIRTHPLACE.get(year, "Birthplace")
    common = tuple(gender if item == "Gender" else item for item in COMMON)
    relation = (RELATION[year],) if year in RELATION else ()
    columns = tuple(dict.fromkeys((*common, birthplace, *relation, *EXTRA_COLUMNS[year])))
    priority = tuple(dict.fromkeys((*common, birthplace, *relation)))
    return CensusSchema(
        year=year,
        schedule_type="population",
        columns=columns,
        priority_fields=priority,
        expected_lines=EXPECTED_LINES[year],
        has_ground_truth_line_number=year != 1860,
        data_top=0.32 if year == 1950 else 0.0,
        data_bottom=0.645 if year == 1950 else 1.0,
        field_bounds=FIELD_BOUNDS_1950 if year == 1950 else {},
        row_bounds=(0.245, 0.775) if year == 1950 else (0.0, 1.0),
    )


SCHEMAS = {
    (year, "population"): _population_schema(year)
    for year in EXPECTED_LINES
}
for year in (1850, 1860):
    SCHEMAS[(year, "slave")] = CensusSchema(
        year=year,
        schedule_type="slave",
        columns=(
            "Name", "Slave Owner Name", "Age", "Birth Date", "Gender", "Race",
            "Fugitive", "Manumitted",
        ),
        priority_fields=("Slave Owner Name", "Age", "Gender", "Race"),
        expected_lines=40,
        has_ground_truth_line_number=False,
    )

SUPPORTED_YEARS = tuple(sorted(EXPECTED_LINES))
SUPPORTED_FORMS = tuple(sorted(SCHEMAS))


def get_census_schema(
    year: int,
    schedule_type: str = "population",
) -> CensusSchema:
    try:
        return SCHEMAS[(year, schedule_type)]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported census form: {year} {schedule_type}. "
            f"Supported forms: {SUPPORTED_FORMS}."
        ) from exc


def schema_field_name(column: str) -> str:
    overrides = {
        "Relation to Head of House": "relation_to_head",
        "Relation to Head": "relation_to_head",
        "Birth Place": "birth_place",
        "Father's Birth Place": "father_birth_place",
        "Father's Birthplace": "father_birthplace",
        "Mother's Birth Place": "mother_birth_place",
        "Mother's Birthplace": "mother_birthplace",
        "Cannot Read, Write": "cannot_read_write",
    }
    if column in overrides:
        return overrides[column]
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", column.casefold())).strip("_")


def field_mapping(schema: CensusSchema) -> dict[str, str]:
    return {schema_field_name(column): column for column in schema.columns}

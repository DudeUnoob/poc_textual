import json
from pathlib import Path

import pytest

from census_schemas import get_census_schema
from workbook_catalog import canonical_field, classify_column

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((ROOT / "schemas" / "workbook_catalog.json").read_text())


def _image_headers(year: int, sheet_name: str) -> tuple[str, ...]:
    fields = CATALOG["workbooks"][str(year)]["sheets"][sheet_name]["fields"]
    return tuple(
        column["header"]
        for column in fields
        if column["classification"] == "image"
    )


def test_canonical_aliases_sex_gender_birthplace_and_relation():
    assert canonical_field("Sex") == canonical_field("Gender") == "gender"
    assert canonical_field("Birth Place") == canonical_field("Birthplace") == "birth_place"
    assert canonical_field("Relationship") == "relation_to_head"
    assert canonical_field("Relation to Head") == "relation_to_head"
    assert canonical_field("Relation to Head of House") == "relation_to_head"


def test_classify_column_derived_vs_image():
    assert classify_column("Birth Date", 1950) == "derived"
    assert classify_column("Estimated Birth Year", 1910) == "derived"
    assert classify_column("Birth Year", 1860) == "derived"
    assert classify_column("Industry", 1850) == "derived"
    assert classify_column("Birth Month", 1910) == "derived"
    assert classify_column("Surname", 1950) == "image"
    assert classify_column("Slave Owner Name", 1860) == "image"
    assert classify_column("Line Number", 1950) == "identity"
    assert classify_column("Name", 1860) == "unknown"


def test_1950_bastrop_sheet_columns_match_catalog_image_order():
    schema = get_census_schema(1950, "population", "Bastrop 11-2A")
    expected = _image_headers(1950, "Bastrop 11-2A")
    assert schema.columns == expected
    assert "Birth Date" not in schema.columns
    assert schema.sheet_name == "Bastrop 11-2A"


def test_mapping_issue_sheet_raises_when_selected():
    with pytest.raises(ValueError, match="Ambiguous mapping"):
        get_census_schema(1950, "population", "Bastrop all Manipulated")


def test_1860_slave_bastrop_columns_match_catalog_image_headers():
    schema = get_census_schema(1860, "slave", "Slave Schedule Bastrop")
    expected = _image_headers(1860, "Slave Schedule Bastrop")
    assert schema.columns == expected
    assert "Name" not in schema.columns
    assert "Birth Date" not in schema.columns
    assert schema.columns == (
        "Slave Owner Name",
        "Age",
        "Gender",
        "Race",
        "Fugitive",
        "Manumitted",
    )

from models import (FIELD_TO_GT_COLUMN_1950, ExtractionBatch, FieldConfidence,
                    Legibility, PersonRecord1950, to_gt_record)


def test_to_gt_record_maps_to_ground_truth_columns():
    person = PersonRecord1950(line_number=1, surname="Whitehead", given_name="Alec G",
                              gender="Male", age=40, race="W")
    rec = to_gt_record(person)
    assert rec["Line Number"] == 1
    assert rec["Surname"] == "Whitehead"
    assert rec["Given Name"] == "Alec G"
    assert rec["Race"] == "W"
    # Every mapped GT column is present, even when None.
    for gt_col in FIELD_TO_GT_COLUMN_1950.values():
        assert gt_col in rec


def test_blank_fields_stay_none():
    rec = to_gt_record(PersonRecord1950(line_number=5))
    assert rec["Surname"] is None
    assert rec["Occupation"] is None


def test_legibility_default_is_clear():
    assert PersonRecord1950(line_number=1).legibility == Legibility.clear


def test_field_confidence_uses_fixed_gemini_compatible_schema():
    person = PersonRecord1950(
        line_number=1,
        field_confidence={"surname": "low", "race": "high"},
    )
    assert person.field_confidence.surname == FieldConfidence.low
    assert person.field_confidence.race == FieldConfidence.high

    def contains_additional_properties(value) -> bool:
        if isinstance(value, dict):
            return "additionalProperties" in value or any(
                contains_additional_properties(child) for child in value.values()
            )
        if isinstance(value, list):
            return any(contains_additional_properties(child) for child in value)
        return False

    assert not contains_additional_properties(ExtractionBatch.model_json_schema())

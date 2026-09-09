from models import (FIELD_TO_GT_COLUMN_1950, ExtractionBatch, FieldConfidence,
                    Legibility, PersonRecord1950, get_year_models, to_gt_record,
                    to_year_gt_record)


def _contains_additional_properties(value) -> bool:
    if isinstance(value, dict):
        return "additionalProperties" in value or any(
            _contains_additional_properties(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_additional_properties(child) for child in value)
    return False


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
    assert not _contains_additional_properties(ExtractionBatch.model_json_schema())


def test_get_year_models_keeps_1950_sheet_fields():
    record_model, batch_model, gt_columns = get_year_models(
        1950, "population", "Bastrop 11-2A",
    )
    assert gt_columns["surname"] == "Surname"
    assert gt_columns["birth_place"] == "Birth Place"
    person = record_model(line_number=1, surname="Lewis")
    assert person.surname == "Lewis"
    assert to_year_gt_record(person, 1950, "population", "Bastrop 11-2A")["Surname"] == "Lewis"
    assert not _contains_additional_properties(batch_model.model_json_schema())

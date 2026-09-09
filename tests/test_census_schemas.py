from census_schemas import SUPPORTED_FORMS, SUPPORTED_YEARS, get_census_schema
from models import get_year_models, to_year_gt_record


def _contains_additional_properties(value) -> bool:
    if isinstance(value, dict):
        return "additionalProperties" in value or any(
            _contains_additional_properties(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_additional_properties(child) for child in value)
    return False


def test_all_project_decades_have_population_schemas():
    assert SUPPORTED_YEARS == (
        1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, 1940, 1950,
    )
    assert (1850, "slave") in SUPPORTED_FORMS
    assert (1860, "slave") in SUPPORTED_FORMS
    assert not get_census_schema(1850, "slave").has_ground_truth_line_number
    assert "Birth Date" in get_census_schema(1860, "slave").columns


def test_each_form_builds_a_fixed_gemini_schema():
    for year, schedule_type in SUPPORTED_FORMS:
        _, batch_model, _ = get_year_models(year, schedule_type)
        assert not _contains_additional_properties(batch_model.model_json_schema())


def test_1920_uses_sex_and_1950_uses_birth_place():
    assert "Sex" in get_census_schema(1920).columns
    assert "Gender" not in get_census_schema(1920).columns
    assert "Birth Place" in get_census_schema(1950).columns


def test_1860_keeps_internal_line_number_out_of_ground_truth_output():
    record_model, _, _ = get_year_models(1860)
    record = record_model(
        line_number=1,
        surname="Smith",
        dwelling_number=3,
        family_number=4,
    )
    output = to_year_gt_record(record, 1860)
    assert "Line Number" not in output
    assert output["_line_number"] == 1
    assert output["Surname"] == "Smith"

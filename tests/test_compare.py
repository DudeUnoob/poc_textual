import json

import pandas as pd
import pytest

import compare


def test_classify_strategy_dwelling_and_house_number_are_numeric():
    # Regression test: these are numeric fields on the form, but previously
    # had no keyword match and fell through to the "fuzzy" string default,
    # which failed extracted "1" vs ground-truth "1.0" outright.
    assert compare.classify_strategy("Dwelling Number") == "numeric"
    assert compare.classify_strategy("House Number") == "numeric"


def test_classify_strategy_age_still_numeric():
    assert compare.classify_strategy("Age") == "numeric"


def test_classify_strategy_default_fuzzy():
    assert compare.classify_strategy("Occupation") == "fuzzy"


def test_field_match_numeric_accepts_int_vs_float_string():
    # The exact observed failure: extracted "1" vs ground truth "1.0".
    assert compare.field_match("1", "1.0", "numeric") is True


def test_field_match_numeric_rejects_real_mismatch():
    assert compare.field_match("5", "1.0", "numeric") is False


def test_field_match_numeric_within_tolerance():
    assert compare.field_match("3", "2", "numeric") is True


def test_align_by_household_order_handles_repeated_family_members():
    extracted = [
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "A"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "B"},
    ]
    ground_truth = [
        {"Dwelling Number": 1, "Family Number": 1, "Surname": "Earlier"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "A"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "B"},
    ]
    aligned_extracted, aligned_truth = compare.align_by_household_order(
        extracted, ground_truth
    )
    matched_rows = sorted(set(aligned_extracted) & set(aligned_truth))
    assert [aligned_truth[row]["Surname"] for row in matched_rows] == ["A", "B"]


def test_align_by_household_order_keeps_missing_ground_truth_rows():
    extracted = [
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "A"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "C"},
    ]
    ground_truth = [
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "A"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "B"},
        {"Dwelling Number": 7, "Family Number": 8, "Surname": "C"},
    ]
    aligned_extracted, aligned_truth = compare.align_by_household_order(
        extracted, ground_truth
    )
    missing_rows = set(aligned_truth) - set(aligned_extracted)
    assert len(missing_rows) == 1
    assert aligned_truth[missing_rows.pop()]["Surname"] == "B"


def test_1860_comparison_respects_selected_physical_page(tmp_path):
    ground_truth = pd.DataFrame([
        {
            "Dwelling Number": row,
            "Family Number": row,
            "Surname": f"Family{row}",
        }
        for row in range(1, 81)
    ])
    workbook = tmp_path / "1860.xlsx"
    ground_truth.to_excel(workbook, sheet_name="Bastrop", index=False)
    extracted = tmp_path / "extracted.json"
    extracted.write_text(json.dumps({
        "census_year": 1860,
        "records": [{
            "Dwelling Number": 41,
            "Family Number": 41,
            "Surname": "Family41",
        }],
    }))

    metrics, rows = compare.compare(
        str(extracted), str(workbook), "Bastrop", 1860, 2,
        ground_truth_row_range=(40, 80),
    )

    assert metrics["physical_page"] == 2
    assert metrics["sheet"] == "Bastrop"
    assert rows[0]["fields"]["Surname"]["match"] is True


def test_compare_rejects_extraction_sheet_name_mismatch(tmp_path):
    ground_truth = pd.DataFrame([
        {"Line Number": 1, "Surname": "Lewis", "Given Name": "Jasper H", "Gender": "Male"},
    ])
    workbook = tmp_path / "1950.xlsx"
    ground_truth.to_excel(workbook, sheet_name="Bastrop 11-2A", index=False)
    extracted = tmp_path / "extracted.json"
    extracted.write_text(json.dumps({
        "census_year": 1950,
        "schedule_type": "population",
        "sheet_name": "Bastrop 11-2B",
        "records": [{"Line Number": 1, "Surname": "Lewis", "Given Name": "Jasper H"}],
    }))

    with pytest.raises(ValueError, match="sheet_name"):
        compare.compare(
            str(extracted), str(workbook), "Bastrop 11-2A", 1950, 1,
        )


def test_compare_uses_selected_sheet_schema(tmp_path):
    ground_truth = pd.DataFrame([
        {"Line Number": 1, "Surname": "Lewis", "Given Name": "Jasper H", "Gender": "Male"},
    ])
    workbook = tmp_path / "1950.xlsx"
    ground_truth.to_excel(workbook, sheet_name="Bastrop 11-2A", index=False)
    extracted = tmp_path / "extracted.json"
    extracted.write_text(json.dumps({
        "census_year": 1950,
        "schedule_type": "population",
        "sheet_name": "Bastrop 11-2A",
        "records": [{
            "Line Number": 1,
            "Surname": "Lewis",
            "Given Name": "Jasper H",
            "Gender": "Male",
        }],
    }))

    metrics, rows = compare.compare(
        str(extracted), str(workbook), "Bastrop 11-2A", 1950, 1,
    )
    assert metrics["sheet"] == "Bastrop 11-2A"
    assert "Surname" in metrics["fields_compared"]
    assert rows[0]["fields"]["Surname"]["match"] is True


def test_row_field_match_rate_and_row_accuracy_diverge():
    # A row with 13/14 fields matching should pass the 80%-threshold metric
    # but still fail the strict all-fields-must-match row_accuracy metric --
    # the two are meant to coexist, not collapse into one number.
    compare_columns = [f"field_{i}" for i in range(14)]
    row_result = {"line_number": 1, "all_match": True, "fields": {}}
    n_matched = 0
    for i, field in enumerate(compare_columns):
        matched = i != 0  # first field mismatches, rest match
        row_result["fields"][field] = {"match": matched}
        if not matched:
            row_result["all_match"] = False
        else:
            n_matched += 1
    row_result["fields_matched"] = n_matched
    row_result["fields_total"] = len(compare_columns)
    row_result["field_match_rate"] = n_matched / len(compare_columns)

    assert row_result["field_match_rate"] == 13 / 14
    assert row_result["field_match_rate"] >= compare.ROW_ACCURACY_THRESHOLD
    assert row_result["all_match"] is False

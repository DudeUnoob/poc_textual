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

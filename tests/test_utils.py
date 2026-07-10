import utils


def test_normalize_race_per_decade():
    assert utils.normalize_race("W", 1950) == "White"
    assert utils.normalize_race("Neg", 1950) == "Negro (Black)"
    assert utils.normalize_race("Mul", 1900) == "Mulatto"
    # Unknown mark passes through unchanged (flag, don't invent).
    assert utils.normalize_race("Zz", 1950) == "Zz"


def test_normalize_gender():
    assert utils.normalize_gender("m") == "Male"
    assert utils.normalize_gender("F") == "Female"
    assert utils.normalize_gender("Male") == "Male"


def test_normalize_marital_status_1950():
    assert utils.normalize_marital_status("M", 1950) == "Married"
    assert utils.normalize_marital_status("Wd", 1950) == "Widowed"
    assert utils.normalize_marital_status("S", 1950) == "Never Married (Single)"


def test_propagate_dittos():
    recs = [
        {"Surname": "Smith", "Birth Place": "Texas"},
        {"Surname": '"', "Birth Place": "ditto"},
        {"Surname": "Jones", "Birth Place": None},
    ]
    out = utils.propagate_dittos(recs, birthplace_field="Birth Place")
    assert out[1]["Surname"] == "Smith"
    assert out[1]["Birth Place"] == "Texas"
    assert out[2]["Surname"] == "Jones"

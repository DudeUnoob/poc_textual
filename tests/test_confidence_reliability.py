"""Tests for confidence_reliability_test.py (Jayden's binomial test).
Expected values are computed independently with math.comb, not with scipy,
so the tests check the statistics and not just that the code runs."""
import random
import sys
from math import comb
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from confidence_reliability_test import run_confidence_test, scenario_breakdown  # noqa: E402


def exact_upper_tail(k, n, p):
    """P(K >= k) for K ~ Binomial(n, p), summed by hand."""
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def make_cells(n_bad, n_total):
    """n_bad cells that are wrong AND confident, the rest correct AND confident."""
    return [(True, True)] * n_bad + [(False, True)] * (n_total - n_bad)


def test_no_bad_cells_gives_p_value_one():
    r = run_confidence_test(make_cells(0, 200), tolerance=0.02)
    assert r.n_bad_scenarios == 0
    assert r.p_value == pytest.approx(1.0)
    assert not r.reject_null


def test_all_bad_cells_rejects():
    r = run_confidence_test(make_cells(50, 50), tolerance=0.02)
    assert r.p_value < 1e-10
    assert r.reject_null


def test_p_value_matches_hand_calculation():
    # 13 bad out of 500 at 2% tolerance (the example from the demo run)
    r = run_confidence_test(make_cells(13, 500), tolerance=0.02)
    assert r.p_value == pytest.approx(exact_upper_tail(13, 500, 0.02), rel=1e-6)
    assert r.p_value == pytest.approx(0.2065, abs=1e-3)
    assert not r.reject_null


def test_clearly_too_many_bad_cells_rejects():
    # 30 bad out of 500 is 6%, well above a 2% tolerance
    r = run_confidence_test(make_cells(30, 500), tolerance=0.02)
    assert r.p_value == pytest.approx(exact_upper_tail(30, 500, 0.02), rel=1e-6)
    assert r.reject_null


def test_only_wrong_and_confident_counts_as_bad():
    cells = (
        [(True, False)] * 40    # wrong but flagged for review: fine
        + [(False, False)] * 40  # correct, flagged anyway: fine
        + [(False, True)] * 100  # correct and confident: fine
        + [(True, True)] * 3     # the only bad ones
    )
    r = run_confidence_test(cells, tolerance=0.02)
    assert r.n_bad_scenarios == 3
    assert r.n_cells == 183
    assert r.observed_rate == pytest.approx(3 / 183)


def test_empty_input_raises():
    with pytest.raises(ValueError):
        run_confidence_test([], tolerance=0.02)


def test_alpha_controls_the_decision():
    cells = make_cells(8, 200)  # p-value lands between 0.01 and 0.05
    p = exact_upper_tail(8, 200, 0.02)
    assert 0.01 < p < 0.05
    assert run_confidence_test(cells, 0.02, alpha=0.05).reject_null
    assert not run_confidence_test(cells, 0.02, alpha=0.01).reject_null


def test_higher_tolerance_is_more_forgiving():
    cells = make_cells(12, 300)
    strict = run_confidence_test(cells, tolerance=0.01)
    lenient = run_confidence_test(cells, tolerance=0.05)
    assert strict.p_value < lenient.p_value


def test_scenario_breakdown_counts_each_quadrant():
    cells = [(True, True)] * 2 + [(False, True)] * 5 + [(True, False)] * 3 + [(False, False)] * 4
    counts = list(scenario_breakdown(cells).values())
    assert counts == [2, 5, 3, 4]
    assert sum(counts) == len(cells)


def test_false_alarm_rate_is_at_most_alpha():
    """If the true bad rate equals the tolerance, the test should wrongly reject
    no more than about 5% of the time (an exact binomial test is conservative)."""
    rng = random.Random(0)
    trials, n, p0 = 2000, 500, 0.02
    rejections = 0
    for _ in range(trials):
        bad = sum(rng.random() < p0 for _ in range(n))
        rejections += run_confidence_test(make_cells(bad, n), p0).reject_null
    assert rejections / trials <= 0.06


def test_catches_a_real_problem_most_of_the_time():
    """If the true bad rate is 5% against a 2% tolerance, the test should usually reject."""
    rng = random.Random(1)
    trials, n = 500, 500
    rejections = 0
    for _ in range(trials):
        bad = sum(rng.random() < 0.05 for _ in range(n))
        rejections += run_confidence_test(make_cells(bad, n), 0.02).reject_null
    assert rejections / trials > 0.9

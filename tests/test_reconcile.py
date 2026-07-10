from models import Legibility, PersonRecord1950
from reconcile import reconcile_page


def _rec(ln, **kw):
    return PersonRecord1950(line_number=ln, **kw)


def test_merges_and_dedups_by_line():
    fp = [_rec(1, surname="A"), _rec(2, surname="B")]
    c1 = [_rec(2, surname="B", given_name="Bob"), _rec(3, surname="C")]
    merged, diag = reconcile_page({"full_page": fp, "crop_1": c1}, expected_lines=3)
    assert [r.line_number for r in merged] == [1, 2, 3]
    # Line 2 present in two passes -> flagged duplicate, keeps more complete copy.
    assert 2 in diag.duplicate_lines
    assert next(r for r in merged if r.line_number == 2).given_name == "Bob"


def test_prefers_clear_over_partial():
    a = [_rec(1, surname="X", legibility=Legibility.partial)]
    b = [_rec(1, surname="Y", legibility=Legibility.clear)]
    merged, _ = reconcile_page({"full_page": a, "crop_1": b}, expected_lines=1)
    assert merged[0].surname == "Y"


def test_missing_lines_detected():
    merged, diag = reconcile_page({"full_page": [_rec(1)]}, expected_lines=3)
    assert diag.missing_lines == [2, 3]
    assert abs(diag.coverage - 1 / 3) < 1e-9


def test_targeted_retry_recovers_missing():
    def retry(missing):
        return [_rec(m, surname="R", legibility=Legibility.clear) for m in missing]
    merged, diag = reconcile_page({"full_page": [_rec(1)]}, expected_lines=3, retry_fn=retry)
    assert diag.retry_attempted is True
    assert diag.missing_lines == []
    assert sorted(diag.retry_recovered_lines) == [2, 3]


def test_out_of_range_lines_flagged():
    merged, diag = reconcile_page({"full_page": [_rec(1), _rec(99)]}, expected_lines=30)
    assert 99 in diag.out_of_range_lines
    assert all(r.line_number <= 30 for r in merged)


def test_retry_failure_is_swallowed():
    def boom(missing):
        raise RuntimeError("api down")
    merged, diag = reconcile_page({"full_page": [_rec(1)]}, expected_lines=2, retry_fn=boom)
    assert diag.retry_attempted is True
    assert diag.missing_lines == [2]

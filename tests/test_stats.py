"""Exact-statistics tests. Values are the standard ABX published tables."""
import math

import pytest

from core import stats as s


# (n, k, p) rows from published ABX significance tables
TABLE = [
    (8, 7, 0.03515625),
    (8, 8, 0.00390625),
    (10, 8, 0.0546875),
    (10, 9, 0.0107421875),
    (13, 10, 0.046142578125),
    (16, 12, 0.0384063720703125),
    (16, 13, 0.0106353759765625),
    (20, 15, 0.020694732666015625),
    (25, 18, 0.021642625331878662),
    (10, 10, 0.0009765625),
    (1, 1, 0.5),
    (2, 2, 0.25),
]


@pytest.mark.parametrize("n,k,expected", TABLE)
def test_p_values_match_published_tables(n, k, expected):
    assert s.one_tailed_p_value(n, k) == pytest.approx(expected, abs=1e-12)


def test_p_value_matches_direct_float_computation():
    for n in (1, 3, 7, 16, 25, 40):
        for k in range(n + 1):
            direct = sum(
                math.comb(n, i) * 0.5**n for i in range(k, n + 1)
            )
            assert s.one_tailed_p_value(n, k) == pytest.approx(direct, abs=1e-12)


def test_zero_trials_is_not_significant():
    assert s.one_tailed_p_value(0, 0) == 1.0
    assert not s.is_significant(0, 0)


def test_monotone_in_k_and_n():
    for n in (10, 20, 30):
        ps = [s.one_tailed_p_value(n, k) for k in range(n + 1)]
        assert ps == sorted(ps, reverse=True)


@pytest.mark.parametrize(
    "n,expected_threshold",
    [(8, 7), (10, 9), (13, 10), (16, 12), (20, 15), (25, 18), (1, None), (2, None)],
)
def test_pass_thresholds(n, expected_threshold):
    assert s.min_correct_for_significance(n) == expected_threshold


def test_threshold_is_the_smallest_passing_k():
    for n in range(3, 60):
        t = s.min_correct_for_significance(n)
        if t is None:
            continue
        assert s.is_significant(n, t)
        assert not s.is_significant(n, t - 1)


# Clopper-Pearson lower/upper bounds, validated against Beta quantiles.
@pytest.mark.parametrize(
    "n,k,lo,hi",
    [
        (10, 7, 0.347547, 0.933260),
        (16, 13, 0.543543, 0.959526),
        (10, 10, 0.691503, 1.0),
    ],
)
def test_clopper_pearson_ci(n, k, lo, hi):
    got = s.binomial_ci(n, k)
    assert got[0] == pytest.approx(lo, abs=1e-5)
    assert got[1] == pytest.approx(hi, abs=1e-5)


def test_ci_contains_point_estimate_and_is_wide_when_small():
    for n, k in [(10, 7), (16, 13), (40, 25)]:
        lo, hi = s.binomial_ci(n, k)
        assert lo <= k / n <= hi
    lo10, hi10 = s.binomial_ci(10, 7)
    lo40, hi40 = s.binomial_ci(40, 28)
    assert (hi40 - lo40) < (hi10 - lo10)


def test_ci_bounds_are_valid_probabilities():
    for n in range(1, 40):
        for k in range(n + 1):
            lo, hi = s.binomial_ci(n, k)
            assert 0.0 <= lo <= hi <= 1.0


def test_power_requirements_match_textbook():
    # Detecting 75% ability at alpha=.05, 80% power is the classic ~23 trials.
    assert s.required_trials_for_power(0.75) == 23
    assert s.required_trials_for_power(0.70) == 37
    assert s.required_trials_for_power(0.60) > 140
    assert s.required_trials_for_power(0.90) < 20
    with pytest.raises(ValueError):
        s.required_trials_for_power(0.5)


def test_holm_bonferroni_suppresses_family_inflation():
    ps = [0.001, 0.02, 0.04, 0.5]
    out = s.holm_bonferroni(ps)
    assert [idx for idx, _, _ in out][0] == 0
    assert out[0][2] is True, "the strongest p must survive"
    rejects = sum(1 for _, _, rej in out if rej)
    assert rejects == 1, "3 of 4 must not survive a 4-member family"
    # Monotone non-decreasing adjusted p along the sorted order
    adjusted = [p for _, p, _ in out]
    assert adjusted == sorted(adjusted)


def test_holm_empty():
    assert s.holm_bonferroni([]) == []


def test_verdict_text_encodes_threshold():
    assert "significant" in s.verdict_text(16, 13)
    assert "not significant" in s.verdict_text(16, 11)
    assert s.verdict_text(0, 0) == "no trials"

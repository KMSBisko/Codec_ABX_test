"""core.alignment: the logic both shells now share."""
import numpy as np
import pytest

from core.alignment import align_pair, estimate_lag, null_depth_db


def _signal(n=48000, seed=7):
    rng = np.random.default_rng(seed)
    # broadband-ish but with a sharp transient, like real music
    t = np.arange(n) / 48000.0
    tone = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3000 * t)
    tone += rng.normal(0, 0.02, n)
    tone[2000:2100] += 1.0
    return tone


def test_recovers_known_positive_lag():
    a = _signal()
    lag = 137
    b = np.concatenate([np.zeros(lag), a[:-lag]])  # B delayed
    est, conf = estimate_lag(a, b)
    assert est == pytest.approx(lag, abs=2)
    assert conf > 0.9


def test_recovers_known_negative_lag():
    """Sign convention: positive lag => SECOND input is delayed.

    Here the first input is the delayed copy, so the second is EARLY and the
    lag must come back negative -- which makes align_pair trim the leading
    zeros off the first input.
    """
    a = _signal()
    lag = 300
    delayed = np.concatenate([np.zeros(lag), a[:-lag]])
    est, conf = estimate_lag(delayed, a)
    assert est == pytest.approx(-lag, abs=2)
    assert conf > 0.9
    out_a, out_b = align_pair(delayed, a, est)
    # after alignment the two must null against each other
    assert null_depth_db(out_a, out_b) < -40


def test_zero_lag_on_identical_signals():
    a = _signal()
    est, conf = estimate_lag(a, a.copy())
    assert est == 0
    assert conf == pytest.approx(1.0, abs=1e-6)


def test_search_is_bounded_so_periodicity_cannot_win():
    """A strongly periodic signal: an unbounded search can lock onto a
    harmonic period far from the true offset. The bounded window prevents it."""
    t = np.arange(48000) / 48000.0
    periodic = np.sin(2 * np.pi * 100 * t)  # 10 ms period => many equal peaks
    lag = 50
    b = np.concatenate([np.zeros(lag), periodic[:-lag]])
    est, _ = estimate_lag(a=periodic, b=b, max_lag_samples=2000)
    assert abs(est) <= 2000
    assert est == pytest.approx(lag, abs=3)


def test_align_pair_never_pads_and_keeps_content_aligned():
    a = np.arange(1000, dtype=float)
    b = np.arange(1000, dtype=float)
    out_a, out_b = align_pair(a, b, lag=40)
    assert len(out_a) == len(out_b) == 960
    assert out_a[0] == 0.0 and out_b[0] == 40.0
    # content matches: shifting A by 40 reproduces B
    assert np.array_equal(a[40 : 40 + len(out_b)], out_b)
    # crucially: no silence was injected at the head
    assert out_b[0] != 0.0


def test_legacy_pad_behaviour_would_misalign_content():
    """Documents what the old web code did: padding with zeros leaves A and B
    playing DIFFERENT content at the same timeline index."""
    a = np.arange(1000, dtype=float)
    b = np.arange(1000, dtype=float)
    padded_b = np.pad(b, ((40, 0)), mode="constant")
    min_len = min(len(a), len(padded_b))
    a_trim, b_trim = a[:min_len], padded_b[:min_len]
    # index 0 of A is sample 0; index 0 of B is silence, and B's sample 0
    # lands at index 40 -- content is displaced by 40.
    assert b_trim[0] == 0.0
    assert not np.array_equal(a_trim[:20], b_trim[:20])
    # the fixed version keeps them on identical content
    out_a, out_b = align_pair(a, np.concatenate([np.zeros(40), b[:-40]]), lag=40)
    assert np.allclose(out_a[:10], out_b[:10])


def test_align_pair_negative_lag():
    a = np.arange(1000, dtype=float)
    b = np.arange(1000, dtype=float)
    out_a, out_b = align_pair(a, b, lag=-25)
    assert len(out_a) == len(out_b) == 975
    assert out_a[0] == 25.0 and out_b[0] == 0.0


def test_align_pair_zero_lag_trims_to_equal_length():
    a = np.ones(100)
    b = np.ones(80)
    out_a, out_b = align_pair(a, b, 0)
    assert len(out_a) == len(out_b) == 80


def test_null_depth_is_deep_for_identical_and_shallow_for_offset():
    a = _signal()
    assert null_depth_db(a, a.copy()) < -100
    shifted = np.concatenate([np.zeros(137), a[:-137]])
    # a misaligned pair nulls only shallowly: the metric is a real detector
    assert null_depth_db(a, shifted) > -40


def test_null_depth_matches_the_analytic_level_mismatch_curve():
    """For a pure level error of x dB the residual is |10^(x/20)-1| of the
    reference, so depth = 20*log10(|10^(x/20)-1|).

    This is why null depth is a usable level-match proof: a 0.1 dB error
    shows up at ~-39 dB, and 0.01 dB at ~-59 dB -- far below what the ear
    reads as "brighter".
    """
    a = _signal()
    for offset_db, expected in [(0.5, -24.54), (0.1, -38.74), (0.01, -58.82)]:
        b = a * (10 ** (offset_db / 20))
        assert null_depth_db(a, b) == pytest.approx(expected, abs=0.6), offset_db


def test_null_depth_is_sign_agnostic_on_level_error():
    a = _signal()
    up = null_depth_db(a, a * (10 ** (0.1 / 20)))
    down = null_depth_db(a, a * (10 ** (-0.1 / 20)))
    assert up == pytest.approx(down, abs=0.3)


def test_handles_stereo_and_tiny_inputs():
    stereo = np.stack([_signal(500), _signal(500)], axis=1)
    est, conf = estimate_lag(stereo, stereo.copy())
    assert est == 0
    assert estimate_lag(np.zeros(1), np.zeros(1)) == (0, 0.0)

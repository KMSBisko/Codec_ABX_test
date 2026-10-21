"""Backend-free preparation policy and measurements."""
import numpy as np
import pytest

from core.alignment import null_depth_db
from core.preparation import (
    LoudnessReport, checked_audio, measure_delivery, normalize_pair,
    select_sample_rate,
)


def rms_meter(data):
    return float(20 * np.log10(np.sqrt(np.mean(data ** 2))))


def test_loudness_report_has_one_owner():
    from app.models import LoudnessReport as DesktopReport
    assert DesktopReport is LoudnessReport


def test_gain_is_not_clamped_and_trim_is_common():
    a = np.full((100, 2), .001)
    b = a * .1
    a[50] = .5
    b[50] = .05
    a2, b2, report = normalize_pair(a, b, rms_meter, target_lufs=-2)
    assert report.gain_a_db > 12 and report.gain_b_db > 12
    assert report.global_trim_db > 0
    assert np.max(np.abs(a2)) <= 1 - 1 / 32768 + 1e-12
    np.testing.assert_allclose(a2, b2, atol=1e-12)
    measured = measure_delivery(report, a2, b2 * .9, rms_meter)
    assert measured.residual_delta_db == pytest.approx(-20 * np.log10(.9))
    assert measured.to_dict()["residual_delta_db"] == measured.residual_delta_db
    assert measured.to_dict()["pre_delta_db"] == pytest.approx(20)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_loudness_is_refused(value):
    with pytest.raises(ValueError, match="not finite"):
        normalize_pair(np.ones((100, 1)), np.ones((100, 1)), lambda _: value)


@pytest.mark.parametrize("data", [np.empty((0, 2)), np.ones((100, 6)),
                                  np.full((100, 1), np.nan)])
def test_invalid_channel_material_is_refused(data):
    with pytest.raises(ValueError):
        checked_audio(data)


def test_channel_count_change_is_refused():
    with pytest.raises(ValueError, match="channel count"):
        checked_audio(np.ones((100, 1)), channels=2)


@pytest.mark.parametrize("mode,expected", [("native", 96000), ("force_48k", 48000)])
def test_rate_policy(mode, expected):
    assert select_sample_rate(96000, mode) == expected


@pytest.mark.parametrize("rate,mode", [(0, "native"), (48000, "typo")])
def test_invalid_rate_policy_is_refused(rate, mode):
    with pytest.raises(ValueError):
        select_sample_rate(rate, mode)


def test_null_measurement_does_not_cancel_stereo_residuals():
    t = np.linspace(-.2, .2, 1000)
    a = np.column_stack((t, -t))
    b = a * .5
    assert null_depth_db(a, a) == float("-inf")
    assert null_depth_db(a, b) == pytest.approx(20 * np.log10(.5))

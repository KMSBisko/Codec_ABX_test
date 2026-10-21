"""The null pair as an ACTIVE gate, not just a number in the report.

`null_depth_db` was already measured and printed. A number nobody can act on is
decoration: this module turns it into a decision, in core, so the desktop and
the web refuse the same pairs for the same reason.

Every threshold claim below is a measured number, not a guess -- see
`test_floor_matches_the_measured_level_error_curve`.
"""
import json

import numpy as np
import pytest

from core.abx import ABXEngine, TrialGateClosed
from core.alignment import (
    CANARY_MIN_DEGRADATION_DB,
    NULL_GATE_THRESHOLD_DB,
    canary_audio_check,
    identical_pipeline_config,
    json_safe_depth,
    null_depth_db,
    null_gate_decision,
)


def _signal(n=48000, seed=7):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 48000.0
    sig = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3000 * t)
    sig = sig + rng.normal(0, 0.02, n)
    sig[2000:2100] += 1.0
    return sig


def _degrade(x: np.ndarray, sr: int = 48000) -> np.ndarray:
    """Rough stand-in for the pipeline's calibration material: telephone band
    plus hard clipping. The real degradation is exercised in the ffmpeg tests."""
    from numpy.fft import rfft, irfft

    spec = rfft(x)
    keep = int(len(x) * 3000 / (sr / 2))
    spec[keep:] = 0
    band = irfft(spec, n=len(x))
    return np.clip(band * 10.0, -0.45, 0.45)


# ── the decision itself ──────────────────────────────────────────────────────


def test_identical_pair_that_nulls_passes_the_gate():
    a = _signal()
    decision = null_gate_decision(
        null_depth_db=null_depth_db(a, a.copy()), expected_identical=True
    )
    assert decision["status"] == "passed"
    assert decision["applicable"] is True
    assert decision["is_perfect"] is True
    assert decision["measured_db"] is None, "-inf is not JSON; the flag carries it"
    assert decision["measured_text"] == "perfect (-inf dB)"
    assert "verified" in decision["reason"]


def test_identical_pair_with_a_level_error_fails_the_gate():
    """0.1 dB of level error is inaudible as 'louder' and lands at -38.7 dB,
    so the gate fails it: that is the classic ABX trap this gate exists for."""
    a = _signal()
    b = a * (10 ** (0.1 / 20))
    decision = null_gate_decision(
        null_depth_db=null_depth_db(a, b), expected_identical=True
    )
    assert decision["status"] == "failed"
    assert decision["measured_db"] == pytest.approx(-38.73, abs=0.5)
    assert "did NOT null" in decision["reason"]


def test_misaligned_identical_pair_fails_louder():
    a = _signal()
    for lag in (1, 137):
        b = np.concatenate([np.zeros(lag), a[:-lag]])
        decision = null_gate_decision(
            null_depth_db=null_depth_db(a, b), expected_identical=True
        )
        assert decision["status"] == "failed", lag


def test_floor_matches_the_measured_level_error_curve():
    """-60 dB is a 0.0087 dB level error. Anything a listener could hear as a
    level difference fails by a wide margin."""
    a = _signal()
    assert null_depth_db(a, a * (10 ** (0.0087 / 20))) == pytest.approx(-60.0, abs=0.4)
    assert null_depth_db(a, a * (10 ** (0.01 / 20))) > NULL_GATE_THRESHOLD_DB
    assert null_depth_db(a, a * (10 ** (0.001 / 20))) < NULL_GATE_THRESHOLD_DB


def test_different_pairs_are_not_judged_by_the_gate():
    """opus64 vs lossless MUST NOT null. Blocking that pair would block every
    real comparison, so the gate abstains."""
    decision = null_gate_decision(
        null_depth_db=-8.4, expected_identical=False
    )
    assert decision["status"] == "not_applicable"
    assert decision["applicable"] is False
    assert "not expected" in decision["reason"]


def test_gate_fails_closed_when_the_residual_is_unmeasurable():
    for depth, why in [
        (None, "never measured"),
        (float("nan"), "silent reference"),
    ]:
        decision = null_gate_decision(null_depth_db=depth, expected_identical=True)
        assert decision["status"] == "failed", why
        assert "fail closed" in decision["reason"], why


def test_decision_is_json_legal():
    """Infinity is not valid JSON; the export must not be the thing that breaks."""
    a = _signal()
    decision = null_gate_decision(
        null_depth_db=null_depth_db(a, a.copy()), expected_identical=True
    )
    json.dumps(decision)
    assert json_safe_depth(float("-inf"))["is_perfect"] is True
    assert json_safe_depth(float("nan"))["is_undefined"] is True
    assert json_safe_depth(-12.5)["measured_db"] == -12.5


# ── which pairs are expected to null ─────────────────────────────────────────


class Stage:
    def __init__(self, codec_id, bitrate_kbps):
        self.codec_id = codec_id
        self.bitrate_kbps = bitrate_kbps


def test_identical_config_is_detected_across_argument_shapes():
    assert identical_pipeline_config(
        [Stage("opus", 128)], [Stage("opus", 128)]
    ) is True
    assert identical_pipeline_config(
        [("opus", 128)], [{"codec_id": "opus", "bitrate_kbps": 128}]
    ) is True
    assert identical_pipeline_config(
        [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
        [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
        bandwidth_a=(False, None),
        bandwidth_b={"enabled": False, "hz": None},
    ) is True


def test_any_real_difference_means_no_null_is_expected():
    assert identical_pipeline_config([Stage("opus", 128)], [Stage("opus", 64)]) is False
    assert identical_pipeline_config(
        [Stage("opus", 128)], [Stage("aac", 128)]
    ) is False
    assert identical_pipeline_config(
        [Stage("opus", 128), Stage("opus", 128)], [Stage("opus", 128)]
    ) is False
    assert identical_pipeline_config(
        [Stage("opus", 128), Stage("aac", 128)],
        [Stage("aac", 128), Stage("opus", 128)],
    ) is False, "stage ORDER is part of the pipeline"
    assert identical_pipeline_config(
        [Stage("opus", 128)],
        [Stage("opus", 128)],
        bandwidth_a=(True, 16000),
        bandwidth_b=(True, 14000),
    ) is False
    assert identical_pipeline_config([], []) is False, "nothing processed, nothing to judge"
    assert identical_pipeline_config(
        [Stage("opus", 128)],
        [Stage("opus", 128)],
        bandwidth_a=(False, 16000),
        bandwidth_b=(False, None),
    ) is True, "a disabled limit is not a difference"


# ── the engine refuses trials on a failed gate ───────────────────────────────


def test_engine_refuses_to_start_trials_on_a_failed_null_gate():
    engine = ABXEngine(planned_trials=16)
    engine.set_null_gate(
        null_gate_decision(null_depth_db=-12.0, expected_identical=True)
    )
    assert engine.gate_open is False
    with pytest.raises(TrialGateClosed) as exc:
        engine.new_trial()
    assert "null gate failed" in "; ".join(exc.value.reasons)
    assert engine.total_trials == 0
    assert engine.stats()["null_gate"]["status"] == "failed"


def test_engine_allows_trials_on_a_passed_or_absent_gate():
    engine = ABXEngine(planned_trials=4)
    engine.set_null_gate(
        null_gate_decision(null_depth_db=float("-inf"), expected_identical=True)
    )
    assert engine.gate_open is True
    engine.new_trial()

    other = ABXEngine(planned_trials=4)
    other.set_null_gate(
        null_gate_decision(null_depth_db=-6.0, expected_identical=False)
    )
    assert other.gate_open is True
    assert other.stats()["null_gate"]["status"] == "not_applicable"

    fresh = ABXEngine(planned_trials=4)
    assert fresh.stats()["null_gate"]["status"] == "not_measured"
    assert fresh.run_valid is True, "no measurement is not a failure, it is no pair yet"


def test_gate_survives_the_export():
    engine = ABXEngine(planned_trials=8)
    engine.set_null_gate(
        null_gate_decision(null_depth_db=-38.73, expected_identical=True)
    )
    payload = json.loads(json.dumps(engine.to_dict()))
    assert payload["gates"]["null"]["status"] == "failed"
    assert payload["stats"]["run_valid"] is False
    assert payload["gates"]["null"]["measured_db"] == pytest.approx(-38.73, abs=0.01)


# ── the calibration material must be audible, or the canary is a coin flip ───


def test_canary_material_is_audibly_different():
    pristine = _signal()
    degraded = _degrade(pristine)
    check = canary_audio_check(pristine=pristine, degraded=degraded)
    assert check["usable"] is True
    assert check["status"] == "usable"
    assert check["measured_db"] > CANARY_MIN_DEGRADATION_DB


def test_silent_canary_material_is_refused():
    """Silence degraded is still silence: answering would be a coin flip and
    would void runs at random, so the canary must be disabled instead."""
    silence = np.zeros(48000)
    check = canary_audio_check(pristine=silence, degraded=silence)
    assert check["usable"] is False
    assert check["status"] == "not_audible"
    assert check["is_undefined"] is True


def test_no_op_degradation_is_refused():
    a = _signal()
    check = canary_audio_check(pristine=a, degraded=a.copy())
    assert check["usable"] is False
    assert check["is_perfect"] is True


def test_too_subtle_degradation_is_refused():
    a = _signal()
    check = canary_audio_check(pristine=a, degraded=a * (10 ** (0.01 / 20)))
    assert check["usable"] is False
    assert "floor" in check["reason"]

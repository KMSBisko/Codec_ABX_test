"""core/report: the wording every shell must agree on."""
from core.report import null_depth_text, validity_lines


def test_perfect_null_is_not_the_largest_number():
    assert null_depth_text(float("-inf")) == "perfect (-inf dB)"
    # a deeper null must print as a MORE negative number, not a bigger one
    deep = float(null_depth_text(-90.0).split()[0])
    shallow = float(null_depth_text(-10.0).split()[0])
    assert deep < shallow
    assert null_depth_text(None) == "not measured"
    assert "undefined" in null_depth_text(float("nan"))


def test_report_states_a_codec_substitution():
    lines = validity_lines(
        null_depth_db=-19.2,
        alignment_lag_samples=0,
        alignment_confidence=0.99,
        disclosure={
            "codec_fallbacks": [
                {
                    "requested_codec_name": "LDAC",
                    "actual_codec_name": "AAC",
                    "actual_encoder": "aac",
                }
            ],
            "resample": {
                "requested_engine": "soxr",
                "engine_used": "default",
                "downgraded": True,
            },
        },
    )
    text = "\n".join(lines)
    assert "LDAC -> AAC" in text
    assert "encoder=aac" in text
    assert "downgraded=True" in text


def test_report_states_no_substitution_explicitly():
    lines = validity_lines(
        null_depth_db=None, alignment_lag_samples=0, alignment_confidence=1.0
    )
    assert any(line.endswith("none") for line in lines)


def test_verdict_requires_pre_registration():
    unregistered = validity_lines(
        null_depth_db=0.0,
        alignment_lag_samples=0,
        alignment_confidence=1.0,
        stats={"planned_trials": None, "is_final": False},
    )
    assert any("not a verdict" in line for line in unregistered)

    provisional = validity_lines(
        null_depth_db=0.0,
        alignment_lag_samples=0,
        alignment_confidence=1.0,
        stats={
            "planned_trials": 16,
            "is_final": False,
            "required_correct": 12,
            "ability_ci95": [0.5, 0.9],
        },
    )
    joined = "\n".join(provisional)
    assert "PROVISIONAL at N=16" in joined
    assert "need >=12 correct" in joined

    final = validity_lines(
        null_depth_db=0.0,
        alignment_lag_samples=0,
        alignment_confidence=1.0,
        stats={
            "planned_trials": 16,
            "is_final": True,
            "required_correct": 12,
            "ability_ci95": [0.5, 0.9],
        },
    )
    assert any("FINAL at N=16" in line for line in final)


def test_loudness_block_shows_pre_delta_not_just_residual():
    lines = validity_lines(
        null_depth_db=-8.8,
        alignment_lag_samples=219,
        alignment_confidence=0.93,
        loudness={
            "pre_delta_db": 0.578,
            "residual_delta_db": 0.0,
            "gain_a_db": -2.71,
            "gain_b_db": -3.29,
            "global_trim_db": 0.0,
        },
    )
    text = "\n".join(lines)
    assert "0.578 dB" in text, "the level work normalization did must be visible"
    assert "gain A=-2.71 / B=-3.29 dB" in text
    assert "lag=219 samples" in text


def test_notes_are_surfaced():
    lines = validity_lines(
        null_depth_db=-3.0,
        alignment_lag_samples=0,
        alignment_confidence=0.31,
        notes=["alignment estimate is weak"],
    )
    assert any("alignment estimate is weak" in line for line in lines)


def test_translation_hook_is_used():
    lines = validity_lines(
        null_depth_db=float("-inf"),
        alignment_lag_samples=0,
        alignment_confidence=1.0,
        t=lambda key: f"[{key}]",
    )
    assert any(line.startswith("[diag_null_depth]") for line in lines)


def test_commitment_is_truncated_not_omitted():
    lines = validity_lines(
        null_depth_db=0.0,
        alignment_lag_samples=0,
        alignment_confidence=1.0,
        commitment="a" * 64,
    )
    joined = "\n".join(lines)
    assert "a" * 16 + "..." in joined
    assert "a" * 64 not in joined

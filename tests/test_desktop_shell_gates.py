"""Desktop shell behaviour of the Phase 1 gates, testable without PyQt6.

PyQt6 cannot be imported in this environment (missing libGL/libportaudio), so
`app/main.py` cannot be constructed and its behaviour is NOT verified here --
only compile-checked. The engine shim, the models and the pipeline module
import cleanly, so the logic they carry is tested.
"""
import inspect
from dataclasses import fields

import pytest

from app.abx_engine import ABXEngine, ABXStats


def _answer(engine, correct):
    """Answer the staged trial using the engine's own server-side state."""
    plan = engine._core.pending_plan
    mapping = plan.display_mapping
    label = next(
        lbl for lbl, src in mapping.items()
        if (src == plan.x_source) == bool(correct)
    )
    return engine.submit_answer(label)


# ── the shim's gate surface (what main.py drives) ────────────────────────────


def test_shim_stages_the_calibration_trial_first():
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    assert engine.is_canary_trial is True
    assert engine.current_trial_kind == "canary"
    assert engine.current_x_is in ("A", "B")
    assert engine.stats().total_trials == 0
    assert engine.stats().canary_status == "pending"
    assert engine.gate_open is True


def test_shim_without_a_canary_is_unchanged():
    engine = ABXEngine(planned_trials=8)
    assert engine.is_canary_trial is False
    engine.submit_answer(engine.current_x_is)
    assert engine.stats().total_trials == 1


def test_passing_the_canary_hands_over_to_scored_trials():
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    _answer(engine, correct=True)
    assert engine.canary_block()["status"] == "passed"
    assert engine.is_canary_trial is False
    assert engine.gate_open is True
    assert engine.stats().total_trials == 0, "calibration never enters the binomial"
    assert engine.canary_block()["trial_index"] == 1


def test_a_missed_canary_blocks_the_shim_without_raising():
    """The GUI calls `submit_answer` and then reads state: a raised exception
    there would surface as a crash dialog, not as a voided run."""
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    correct = _answer(engine, correct=False)
    assert correct is False

    assert engine.gate_open is False
    assert engine.gate_closed, "plain text for a QMessageBox"
    assert "canary" in engine.gate_closed
    assert engine.stats().void is True
    assert engine.stats().run_valid is False
    assert engine.stats().verdict.startswith("VOID")
    # Staged trial: none. The UI must not offer an answer for it.
    assert engine.current_x_source == "A"
    assert engine.new_trial() == engine.current_x_is, "must not raise"


def test_void_appears_in_the_p_value_line():
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    _answer(engine, correct=False)
    assert "VOID" in engine.stats().p_value_text


def test_a_failed_null_gate_blocks_the_shim_at_once():
    from core.alignment import null_gate_decision

    engine = ABXEngine(planned_trials=8)
    engine.set_null_gate(
        null_gate_decision(null_depth_db=-38.73, expected_identical=True)
    )
    assert engine.gate_open is False
    assert "null gate failed" in engine.gate_closed
    assert engine.stats().null_gate_status == "failed"

    clean = ABXEngine(planned_trials=8)
    clean.set_null_gate(
        null_gate_decision(null_depth_db=-6.0, expected_identical=False)
    )
    assert clean.gate_open is True
    assert clean.gate_closed is None


def test_shim_exposes_every_trial_for_the_commit_reveal():
    from core.abx import verify_commitment

    engine = ABXEngine(planned_trials=4, canary_enabled=True)
    _answer(engine, correct=True)
    for _ in range(4):
        _answer(engine, correct=True)

    trials = engine.all_trials()
    assert [t.trial_index for t in trials] == [1, 2, 3, 4, 5]
    assert [t.kind for t in trials] == ["canary"] + ["normal"] * 4

    reveal = engine.reveal()
    assert reveal["trials_covered"] == 5
    assert verify_commitment(
        reveal["secret_hex"], 5, [t.x_source for t in trials]
    )
    # The scored table the CSV exports is the binomial's input: 4, not 5.
    assert engine.stats().total_trials == 4


def test_stats_defaults_keep_callers_that_predate_the_gates():
    stats = ABXStats(total_trials=0, correct_trials=0, p_value_one_tailed=1.0)
    assert stats.void is False
    assert stats.run_valid is True
    assert stats.current_trial_kind == "normal"
    assert "VOID" not in stats.p_value_text


# ── models and pipeline module surface ───────────────────────────────────────


def test_prepared_session_carries_the_gates_with_defaults():
    """Every new field defaults, so existing construction sites (and tests)
    keep working; the export reads them without shell-specific phrasing."""
    from app.models import PreparedSession

    names = {f.name: f for f in fields(PreparedSession)}
    for name in ("null_gate", "canary", "canary_pristine_path", "canary_degraded_path"):
        assert name in names
        assert names[name].default is not None or name.endswith("path"), name
    assert names["null_gate"].default_factory is not None
    assert names["canary"].default_factory is not None


def test_pipeline_accepts_the_canary_switch_and_a_public_loader():
    from app.audio_pipeline import AudioPipeline

    params = inspect.signature(AudioPipeline.prepare_session).parameters
    assert "canary_enabled" in params
    assert params["canary_enabled"].default is False, "no behaviour change by default"
    assert callable(AudioPipeline.load_audio)


def test_canary_degradation_is_the_same_in_both_shells():
    """The calibration material is defined once, in core, and the desktop uses
    it verbatim -- the same rule that stopped the two trial engines diverging."""
    import numpy as np

    from app import audio_pipeline

    assert audio_pipeline.build_canary_pair is __import__(
        "core.canary", fromlist=["build_canary_pair"]
    ).build_canary_pair

    rng = np.random.default_rng(3)
    data = (rng.normal(0, 0.1, 48000) + 0.3 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000))
    data = data.astype(np.float32)
    _, degraded, check = audio_pipeline.build_canary_pair(data, 48000)
    assert check["usable"] is True
    assert degraded.dtype == np.float32
    assert degraded.shape == data.shape


def test_sealed_desktop_export_carries_the_gates():
    """The desktop export is the citation artifact: the gates and the VOID flag
    must be in the file, not only on the panel."""
    from app.logger import ExperimentLogger

    engine = ABXEngine(planned_trials=2, canary_enabled=True)
    _answer(engine, correct=True)
    for _ in range(2):
        _answer(engine, correct=True)

    logger = ExperimentLogger()
    logger.set_session_info({"input_file": "src.wav"})
    stats = engine.stats()
    logger.seal(
        disclosure={},
        verifiability=engine.reveal(),
        statistics={
            "total_trials": stats.total_trials,
            "void": stats.void,
            "run_valid": stats.run_valid,
            "verdict": stats.verdict,
        },
        canary=engine.canary_block(),
        null_gate=engine.null_gate_block(),
        gates=engine.gates(),
        run_valid=engine.run_valid,
    )

    session = logger.to_dict()["session"]
    assert session["gates"]["canary"]["status"] == "passed"
    assert session["gates"]["canary"]["trial_index"] == 1
    assert session["canary"]["status"] == "passed"
    assert session["run_valid"] is True
    assert session["statistics"]["void"] is False
    # The calibration trial is covered by the commitment, not by the binomial.
    assert session["verifiability"]["trials_covered"] == 3
    assert session["verifiability"]["canary_included"] is True

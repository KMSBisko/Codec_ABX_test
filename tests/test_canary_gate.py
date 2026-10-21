"""The calibration (canary) trial: a known-answer trial that gates the session.

A canary is the cheapest credibility feature in this domain: one deliberately-
easy trial whose answer is known by construction. Miss it and the run is void,
because the playback chain or the attention is not working. It is recorded and
never counted, and it is inside the commit-reveal so its placement cannot be
cherry-picked after the answers are in.
"""
import json

import pytest

from core.abx import (
    ABXEngine,
    CANARY_FAILED,
    CANARY_NOT_CONFIGURED,
    CANARY_PASSED,
    CANARY_PENDING,
    CanaryFailedError,
    TRIAL_KIND_CANARY,
    TRIAL_KIND_NORMAL,
    verify_commitment,
    x_sequence,
)


def _answer_correctly(engine: ABXEngine):
    plan = engine.new_trial()
    return engine.submit_answer(plan.x_display_label())


def _answer_wrongly(engine: ABXEngine):
    plan = engine.new_trial()
    wrong = "B" if plan.x_display_label() == "A" else "A"
    return engine.submit_answer(wrong)


# ── default off must not disturb anything that existed before ────────────────


def test_canary_defaults_off_and_leaves_legacy_behaviour():
    engine = ABXEngine(planned_trials=4)
    assert engine.canary_status == CANARY_NOT_CONFIGURED
    for _ in range(4):
        _answer_correctly(engine)
    assert [t.trial_index for t in engine.trials] == [1, 2, 3, 4]
    assert engine.total_trials == 4 and engine.is_final
    assert engine.run_valid is True
    assert engine.gates()["open"] is True
    assert engine.stats()["canary"]["status"] == CANARY_NOT_CONFIGURED


# ── the canary is trial 1, and it is not scored ─────────────────────────────


def test_canary_is_trial_one_and_never_enters_the_binomial():
    engine = ABXEngine(planned_trials=10, canary_enabled=True)

    plan = engine.new_trial()
    assert plan.kind == TRIAL_KIND_CANARY
    assert plan.trial_index == 1
    assert engine.current_trial_kind == TRIAL_KIND_CANARY
    assert engine.canary_status == CANARY_PENDING

    engine.submit_answer(plan.x_display_label())
    assert engine.canary_status == CANARY_PASSED
    assert engine.canary_trial.trial_index == 1
    assert engine.total_trials == 0, "calibration must not inflate the binomial"

    for _ in range(10):
        _answer_correctly(engine)

    assert [t.trial_index for t in engine.trials] == list(range(2, 12))
    assert all(t.kind == TRIAL_KIND_NORMAL for t in engine.trials)
    assert engine.total_trials == 10
    assert engine.is_final is True
    assert engine.run_valid is True


def test_canary_plan_is_public_with_no_answer_inside():
    """The canary is announced on purpose; WHICH side X is stays secret."""
    engine = ABXEngine(planned_trials=6, canary_enabled=True, label_mode="blind_random")
    plan = engine.new_trial()
    assert set(plan.public()) == {"trial_index", "token", "display_mapping"}
    blob = json.dumps(plan.public()).lower()
    assert "canary" not in blob and "source_a" not in blob and "x_source" not in blob


def test_canary_scores_through_the_display_mapping_in_blind_mode():
    """Invariant 1 applies to the canary too: a perfect listener passes even
    when the display labels are swapped."""
    engine = ABXEngine(planned_trials=8, canary_enabled=True, label_mode="blind_random")
    for _ in range(3):
        _answer_correctly(engine)
        engine.reset()
        assert engine.canary_status == CANARY_PENDING
    _answer_correctly(engine)
    assert engine.canary_status == CANARY_PASSED
    assert engine.total_trials == 0


def test_re_issuing_the_pending_canary_keeps_its_index():
    engine = ABXEngine(planned_trials=5, canary_enabled=True)
    first = engine.new_trial()
    second = engine.new_trial()
    assert first.trial_index == second.trial_index == 1
    assert second.token != first.token
    engine.submit_answer(second.x_display_label())
    assert engine.canary_status == CANARY_PASSED


# ── failing it voids the run ────────────────────────────────────────────────


def test_failed_canary_closes_the_trial_gate():
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    _answer_wrongly(engine)

    assert engine.canary_status == CANARY_FAILED
    assert engine.run_valid is False
    with pytest.raises(CanaryFailedError) as exc:
        engine.new_trial()
    assert "void" in str(exc.value)
    assert exc.value.reasons, "the shell needs the reason to show the user"


def test_a_failed_canary_stops_the_session_before_any_scored_trial():
    """Gating means gating: after a miss, no scored trial can be issued."""
    engine = ABXEngine(planned_trials=16, canary_enabled=True)
    _answer_wrongly(engine)

    with pytest.raises(CanaryFailedError):
        engine.new_trial()
    assert engine.total_trials == 0
    stats = engine.stats()
    assert stats["void"] is True
    assert stats["run_valid"] is False
    assert stats["verdict"].startswith("VOID")


def test_a_void_gate_overrides_a_perfect_significant_run():
    """A p-value is not a verdict once a gate has failed.

    In the shells the null gate is set at preprocess time, so trials never
    start; this builds the combination on the engine to prove the verdict
    logic itself, using the measured depth of a 0.1 dB level error.
    """
    from core.alignment import null_gate_decision

    engine = ABXEngine(planned_trials=16)
    for _ in range(16):
        _answer_correctly(engine)
    assert engine.stats()["significant"] is True
    assert engine.stats()["is_final"] is True

    engine.set_null_gate(
        null_gate_decision(null_depth_db=-38.73, expected_identical=True)
    )
    stats = engine.stats()
    assert stats["significant"] is True, "the arithmetic is unaffected"
    assert stats["is_final"] is True
    assert stats["void"] is True
    assert stats["run_valid"] is False
    assert stats["verdict"].startswith("VOID")
    with pytest.raises(RuntimeError):
        engine.new_trial()


def test_canary_failure_is_reported_in_the_shared_wording():
    from core.report import validity_lines

    engine = ABXEngine(planned_trials=16, canary_enabled=True)
    _answer_wrongly(engine)
    gates = engine.gates()
    lines = validity_lines(
        null_depth_db=float("-inf"),
        alignment_lag_samples=0,
        alignment_confidence=0.99,
        stats=engine.stats(),
        canary=gates["canary"],
        null_gate=gates["null"],
    )
    text = "\n".join(lines)
    assert "FAILED at trial #1" in text
    assert "RUN VOID" in text
    assert "Verdict: VOID at N=16" in text


def test_passed_canary_is_reported_as_confirmation():
    from core.report import validity_lines

    engine = ABXEngine(planned_trials=16, canary_enabled=True)
    _answer_correctly(engine)
    gates = engine.gates()
    lines = validity_lines(
        null_depth_db=-8.0,
        alignment_lag_samples=0,
        alignment_confidence=0.99,
        stats=engine.stats(),
        canary=gates["canary"],
        null_gate=gates["null"],
    )
    text = "\n".join(lines)
    assert "PASSED at trial #1" in text
    assert "RUN VOID" not in text
    assert "Verdict: PROVISIONAL at N=16" in text


# ── verifiability covers the calibration trial ──────────────────────────────


def test_commit_reveal_covers_the_canary():
    engine = ABXEngine(planned_trials=12, canary_enabled=True)
    published = engine.session_commitment

    _answer_correctly(engine)
    for _ in range(12):
        _answer_correctly(engine)

    recorded = engine.all_trials()
    reveal = engine.reveal()
    assert [t.trial_index for t in recorded] == list(range(1, 14))
    assert reveal["trials_covered"] == 13
    assert reveal["canary_included"] is True
    assert reveal["commitment"] == published
    assert verify_commitment(
        reveal["secret_hex"],
        len(recorded),
        [t.x_source for t in recorded],
    )
    assert reveal["x_sequence"] == x_sequence(engine._secret, 13)


def test_reveal_sequence_matches_recorded_x_exactly():
    """No holes: re-issuing an unanswered trial must not shift the sequence."""
    engine = ABXEngine(planned_trials=6, canary_enabled=True)
    engine.new_trial()
    engine.new_trial()  # re-issue the canary
    _answer_correctly(engine)
    for _ in range(3):
        engine.new_trial()
        engine.new_trial()  # re-issue, unanswered
        _answer_correctly(engine)

    recorded = engine.all_trials()
    assert [t.trial_index for t in recorded] == [1, 2, 3, 4]
    reveal = engine.reveal()
    assert reveal["x_sequence"] == [t.x_source for t in recorded]


def test_reset_requires_a_fresh_canary_and_keeps_the_pair_gate():
    engine = ABXEngine(planned_trials=4, canary_enabled=True)
    _answer_correctly(engine)
    assert engine.canary_status == CANARY_PASSED

    engine.set_null_gate({"status": "failed", "reason": "did not null"})
    engine.reset()
    assert engine.canary_status == CANARY_PENDING, "a new session must recalibrate"
    assert engine.total_trials == 0
    assert engine.null_gate_block()["status"] == "failed", (
        "resetting answers does not repair a broken pair"
    )
    with pytest.raises(RuntimeError):
        engine.new_trial()


def test_to_dict_carries_gates_and_is_json_serialisable():
    engine = ABXEngine(planned_trials=4, canary_enabled=True)
    _answer_correctly(engine)
    _answer_correctly(engine)
    parsed = json.loads(json.dumps(engine.to_dict()))
    assert parsed["canary_enabled"] is True
    assert parsed["gates"]["canary"]["status"] == CANARY_PASSED
    assert parsed["stats"]["run_valid"] is True
    assert [t["kind"] for t in parsed["trials"]] == [
        TRIAL_KIND_CANARY,
        TRIAL_KIND_NORMAL,
    ]

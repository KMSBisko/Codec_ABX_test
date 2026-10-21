"""Regression documentation for the pre-Phase-0 blinding/scoring bugs.

These tests reproduce the EXACT formulas from the old web/desktop engines and
show they were wrong. They exist so the bugs can never silently return.
"""
import random

import pytest


def legacy_web_score(x_is, mapping_a_to, mapping_b_to, answer_display):
    """Verbatim from web/app.py (pre-Phase-0) submit_answer()."""
    display_x_source = mapping_a_to if x_is == "A" else mapping_b_to
    answer_source = mapping_a_to if answer_display == "A" else mapping_b_to
    is_correct = answer_display == x_is  # <-- compares DISPLAY vs SOURCE
    return is_correct, answer_source, display_x_source


def test_legacy_web_misScores_perfect_listener_when_labels_swapped():
    """mapping is display->source; X is source-level.

    Swapped: display 'A' plays source_B, display 'B' plays source_A.
    X is source_A, so a perfect listener answers display 'B'.
    The legacy comparison 'B' == 'A' scores that INCORRECT.
    """
    x_is = "A"  # X plays source_A
    mapping_a_to, mapping_b_to = "source_B", "source_A"  # swapped

    correct, answer_source, display_x_source = legacy_web_score(
        x_is, mapping_a_to, mapping_b_to, "B"
    )
    assert correct is False, "expected to reproduce the legacy bug"

    # And the audit fields were computed with a backwards mapping: X plays
    # source_A, yet the report claims the listener heard source_B.
    assert display_x_source == "source_B"


def test_new_engine_scores_perfect_listener_in_blind_mode():
    from core.abx import ABXEngine

    engine = ABXEngine(planned_trials=32, label_mode="blind_random")
    for _ in range(32):
        plan = engine.new_trial()
        engine.submit_answer(plan.x_display_label())
    assert engine.correct_trials == 32


def test_new_engine_scoring_is_identical_across_label_modes():
    """Blinding must not change the arithmetic (core invariant 1)."""
    from core.abx import ABXEngine

    fixed = ABXEngine(planned_trials=40, label_mode="fixed")
    blind = ABXEngine(planned_trials=40, label_mode="blind_random")

    # Force the same X sequence by sharing one secret.
    blind._secret = fixed._secret

    fixed_out, blind_out = [], []
    for _ in range(40):
        pf, pb = fixed.new_trial(), blind.new_trial()
        fixed_out.append(pf.x_source)
        blind_out.append(pb.x_source)
        # same answer behaviour: always pick X's display label
        fixed.submit_answer(pf.x_display_label())
        blind.submit_answer(pb.x_display_label())

    assert fixed_out == blind_out
    assert fixed.correct_trials == blind.correct_trials == 40


def test_legacy_streak_blinding_is_predictable():
    """Desktop legacy rule: 0.5 + 0.07*streak, capped 0.88.

    A swap probability above 0.5 makes the mapping anti-persistent: given the
    previous mapping, the next mapping is MORE likely to differ, so the mapping
    is predictable from history. A fair coin is not.
    """
    rng = random.Random(1234)
    mapping = {"A": "A", "B": "B"}
    streak = 0
    swaps_when_streak_ge_5 = 0
    total_when_streak_ge_5 = 0

    for _ in range(20000):
        prob = min(0.5 + 0.07 * streak, 0.88)
        swapped = rng.random() < prob
        mapping = {"A": "B", "B": "A"} if swapped else {"A": "A", "B": "B"}
        if streak >= 5:
            total_when_streak_ge_5 += 1
            swaps_when_streak_ge_5 += int(swapped)
        streak = 0 if swapped else streak + 1

    rate = swaps_when_streak_ge_5 / total_when_streak_ge_5
    assert rate > 0.6, f"expected anti-persistence, got swap rate {rate:.3f}"

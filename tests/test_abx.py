"""Engine invariants: no client leak, verifiability, pre-registration."""
import pytest

from core.abx import (
    ABXEngine,
    commitment,
    verify_commitment,
    x_sequence,
    x_source_for_index,
)


def test_public_shape_never_contains_the_answer():
    """Structural check: public() is an allow-list, not a deny-list.

    The display mapping IS legitimately public (the UI needs it to draw the
    buttons). The X source is a separate secret, so knowing the mapping must
    not identify X. Assert the key set exactly.
    """
    engine = ABXEngine(planned_trials=10, label_mode="blind_random")
    for _ in range(10):
        plan = engine.new_trial()
        pub = plan.public()
        # Allow-list: the exact public surface. Adding an X field fails here.
        assert set(pub) == {"trial_index", "token", "display_mapping"}
        assert pub["token"], "token is how the client fetches X audio"
        assert plan.x_source in ("A", "B")
        engine.submit_answer(plan.x_display_label())


def test_fixed_mode_public_payload_still_hides_x():
    engine = ABXEngine(planned_trials=6, label_mode="fixed")
    for _ in range(6):
        plan = engine.new_trial()
        assert plan.public()["display_mapping"] == {"A": "A", "B": "B"}
        assert set(plan.public()) == {"trial_index", "token", "display_mapping"}
        engine.submit_answer(plan.x_display_label())


def test_display_mapping_is_a_bijection_in_blind_mode():
    engine = ABXEngine(planned_trials=200, label_mode="blind_random")
    for _ in range(200):
        plan = engine.new_trial()
        mapping = dict(plan.display_mapping)
        assert sorted(mapping) == ["A", "B"]
        assert sorted(mapping.values()) == ["A", "B"]
        engine.submit_answer(plan.x_display_label())
    assert engine.correct_trials == 200


def test_blind_swap_rate_is_a_fair_coin():
    engine = ABXEngine(planned_trials=2000, label_mode="blind_random")
    swaps = sum(
        1
        for _ in range(2000)
        if dict(engine.new_trial().display_mapping) == {"A": "B", "B": "A"}
    )
    assert 0.45 < swaps / 2000 < 0.55


def test_commit_reveal_proves_the_sequence_was_fixed_in_advance():
    engine = ABXEngine(planned_trials=40)
    published_commitment = engine.session_commitment
    for _ in range(40):
        plan = engine.new_trial()
        engine.submit_answer("A" if plan.x_source == "A" else "B")

    reveal = engine.reveal()
    assert reveal["commitment"] == published_commitment
    assert verify_commitment(reveal["secret_hex"], 40, reveal["x_sequence"])
    recorded = [t.x_source for t in engine.trials]
    assert recorded == reveal["x_sequence"], "recorded X must equal the committed X"


def test_verification_rejects_a_tampered_sequence():
    engine = ABXEngine(planned_trials=8)
    for _ in range(8):
        engine.submit_answer("A") if engine.new_trial() else None
    reveal = engine.reveal()
    tampered = list(reveal["x_sequence"])
    tampered[3] = "B" if tampered[3] == "A" else "A"
    assert not verify_commitment(reveal["secret_hex"], 8, tampered)


def test_x_sequence_is_deterministic_from_secret():
    secret = bytes(range(32))
    a = x_sequence(secret, 50)
    b = x_sequence(secret, 50)
    assert a == b
    assert a != x_sequence(bytes(range(1, 33)), 50)
    assert all(x in ("A", "B") for x in a)


def test_pre_registration_gates_the_verdict():
    engine = ABXEngine(planned_trials=16)
    for _ in range(10):
        plan = engine.new_trial()
        engine.submit_answer(plan.x_display_label())
    assert engine.total_trials == 10
    assert engine.is_final is False
    st = engine.stats()
    assert st["provisional"] is True
    # Criterion is pinned to the pre-registered N (16), not to n=10.
    assert st["required_correct"] == 12, "must not expose a peeking threshold"

    for _ in range(6):
        plan = engine.new_trial()
        engine.submit_answer(plan.x_display_label())
    assert engine.is_final is True
    assert engine.stats()["provisional"] is False


def test_no_planned_n_is_always_provisional():
    engine = ABXEngine()
    for _ in range(40):
        engine.submit_answer(engine.new_trial().x_display_label())
    assert engine.is_final is False
    assert engine.stats()["planned_trials"] is None


def test_all_correct_at_n_is_significant():
    engine = ABXEngine(planned_trials=16)
    for _ in range(16):
        engine.submit_answer(engine.new_trial().x_display_label())
    st = engine.stats()
    assert st["significant"] is True
    assert st["p_value_one_tailed"] < 0.05
    assert st["ability_ci95"][0] > 0.7


def test_answer_requires_a_pending_trial():
    engine = ABXEngine()
    with pytest.raises(RuntimeError):
        engine.submit_answer("A")


def test_rejects_bad_answer():
    engine = ABXEngine()
    engine.new_trial()
    for bad in ("", "C", None, "  ", "yes"):
        with pytest.raises(ValueError):
            engine.submit_answer(bad)


def test_answer_consumes_the_trial():
    engine = ABXEngine()
    engine.new_trial()
    engine.submit_answer("A")
    with pytest.raises(RuntimeError):
        engine.submit_answer("A")


def test_reset_invalidates_the_old_commitment():
    engine = ABXEngine(planned_trials=8)
    first = engine.session_commitment
    for _ in range(8):
        engine.submit_answer("A") if engine.new_trial() else None
    engine.reset()
    assert engine.session_commitment != first
    assert engine.total_trials == 0


def test_to_dict_is_json_serialisable_and_carries_the_reveal():
    import json

    engine = ABXEngine(planned_trials=6, label_mode="blind_random")
    for _ in range(6):
        engine.submit_answer(engine.new_trial().x_display_label())
    blob = json.dumps(engine.to_dict())
    parsed = json.loads(blob)
    assert parsed["stats"]["total_trials"] == 6
    assert len(parsed["trials"]) == 6
    assert parsed["reveal"]["verifiable"] is True


def test_rejects_bad_config():
    with pytest.raises(ValueError):
        ABXEngine(label_mode="fixed_labels")
    with pytest.raises(ValueError):
        ABXEngine(planned_trials=0)

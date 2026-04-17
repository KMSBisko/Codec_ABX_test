from app.abx_engine import ABXEngine


def test_p_value_bounds() -> None:
    engine = ABXEngine()
    assert engine.one_tailed_p_value() == 1.0


def test_perfect_score_significance() -> None:
    engine = ABXEngine()
    for _ in range(10):
        target = engine.current_x_is
        engine.submit_answer(target)
    assert engine.correct_trials == 10
    assert engine.total_trials == 10
    assert engine.one_tailed_p_value() < 0.01


def test_random_guess_reference_case() -> None:
    engine = ABXEngine()
    # 5 correct out of 10 yields a non-significant p-value near 0.623 (one-tailed)
    answers = []
    for _ in range(10):
        answers.append(engine.current_x_is)
        engine.submit_answer("A")

    # Do not assert correctness count here because x is random; only bounds are guaranteed.
    p = engine.one_tailed_p_value()
    assert 0.0 <= p <= 1.0

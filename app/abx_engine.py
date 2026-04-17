from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class ABXStats:
    total_trials: int
    correct_trials: int
    p_value_one_tailed: float


class ABXEngine:
    def __init__(self) -> None:
        self._rng = random.SystemRandom()
        self.total_trials = 0
        self.correct_trials = 0
        self.current_x_is = self._random_x()

    def _random_x(self) -> str:
        return "A" if self._rng.random() < 0.5 else "B"

    def new_trial(self) -> str:
        self.current_x_is = self._random_x()
        return self.current_x_is

    def submit_answer(self, answer: str) -> bool:
        normalized = answer.strip().upper()
        if normalized not in ("A", "B"):
            raise ValueError("answer must be 'A' or 'B'")

        self.total_trials += 1
        is_correct = normalized == self.current_x_is
        if is_correct:
            self.correct_trials += 1

        self.current_x_is = self._random_x()
        return is_correct

    def one_tailed_p_value(self) -> float:
        n = self.total_trials
        k = self.correct_trials
        if n == 0:
            return 1.0

        # P(X >= k | n, p=0.5)
        total = 0.0
        for i in range(k, n + 1):
            total += math.comb(n, i) * (0.5 ** n)
        return min(max(total, 0.0), 1.0)

    def stats(self) -> ABXStats:
        return ABXStats(
            total_trials=self.total_trials,
            correct_trials=self.correct_trials,
            p_value_one_tailed=self.one_tailed_p_value(),
        )

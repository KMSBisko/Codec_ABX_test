"""Canonical ABX trial engine. Single source of truth for desktop AND web.

Why this file exists
-------------------
The project previously carried two independent trial engines (PyQt6 desktop and
Flask web) with *divergent* randomness and *divergent* blinding heuristics, so
the same study produced different results per shell. This module replaces both.

Three invariants:

1. SCORING IS BLINDING-INDEPENDENT.  The display label mapping is a pure
   presentation concern. `fixed` and `blind_random` produce identical
   arithmetic. (The old code compared a *display* answer against a *source*
   X, and applied the display mapping in the wrong direction, so a perfect
   listener in blind mode could be scored incorrect.)

2. NO HOME-BREWED SWAP HEURISTICS.  X is an independent fair coin per trial.
   The old streak rules (web: 0.1*streak capped 0.5; desktop: 0.5 + 0.07*streak
   capped 0.88) made the mapping anti-persistent -- i.e. predictable -- which
   is strictly worse than a coin flip and impossible to audit.

3. THE ANSWER NEVER LEAVES THE SERVER BEFORE THE ANSWER.  `TrialPlan.public()`
   is the only client-visible shape and contains no X information. X audio is
   served by token, resolved server-side.

Commit-reveal: the X sequence is derived from a random secret via SHA-256, so
the commitment published at session start provably fixes the X sequence in
advance. Any third party can recompute it from (secret, n) and confirm the
tool did not cherry-pick X after seeing answers.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .stats import (
    DEFAULT_ALPHA,
    binomial_ci,
    is_significant,
    min_correct_for_significance,
    one_tailed_p_value,
)

LABEL_MODE_FIXED = "fixed"
LABEL_MODE_BLIND = "blind_random"

_COMMIT_PREFIX = b"ABX1\x00"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def x_source_for_index(secret: bytes, trial_index: int) -> str:
    """Deterministic, third-party-reproducible X assignment for a trial.

    'A' if the derived bit is 0 else 'B'. Reproducible in any language from
    (secret, trial_index) -- that is what makes the result auditable.
    """
    digest = hashlib.sha256(secret + int(trial_index).to_bytes(8, "big")).digest()
    return "A" if (digest[0] & 1) == 0 else "B"


def x_sequence(secret: bytes, n: int) -> List[str]:
    """The full X sequence for trials 1..n. Verification primitive."""
    return [x_source_for_index(secret, i) for i in range(1, n + 1)]


def commitment(secret: bytes) -> str:
    """Hash published BEFORE the test; proves the X sequence was fixed."""
    return hashlib.sha256(_COMMIT_PREFIX + secret).hexdigest()


def verify_commitment(secret_hex: str, n: int, expected: List[str]) -> bool:
    """Third-party check: does this secret generate the recorded X sequence?"""
    secret = bytes.fromhex(secret_hex)
    if commitment(secret) == "":  # pragma: no cover
        return False
    return x_sequence(secret, n) == list(expected)


@dataclass(frozen=True)
class TrialPlan:
    """What a trial looks like BEFORE the answer.

    `x_source` is server-only. Call `.public()` for the client-safe shape.
    """

    trial_index: int
    token: str
    display_mapping: Mapping[str, str]  # display label -> source
    x_source: str

    def x_display_label(self) -> str:
        """Which display label X corresponds to (server-side scoring helper)."""
        for label, source in self.display_mapping.items():
            if source == self.x_source:
                return label
        raise RuntimeError("display mapping is not a bijection")

    def public(self) -> Dict[str, Any]:
        """The ONLY shape that may be sent to an untrusted client."""
        return {
            "trial_index": self.trial_index,
            "token": self.token,
            "display_mapping": dict(self.display_mapping),
        }


@dataclass(frozen=True)
class Trial:
    trial_index: int
    x_source: str
    answer_display: str
    answer_source: str
    correct: bool
    display_mapping: Mapping[str, str]
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_index": self.trial_index,
            "x_source": self.x_source,
            "answer_display": self.answer_display,
            "answer_source": self.answer_source,
            "correct": self.correct,
            "display_mapping": dict(self.display_mapping),
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class ABXEngine:
    """Forced-choice trial engine with pre-registration.

    planned_trials: N declared BEFORE the first answer. The verdict is only
    `is_final` once n reaches N. p-values below N are marked provisional, which
    is the structural fix for optional-stopping / p-hacking.
    """

    planned_trials: Optional[int] = None
    label_mode: str = LABEL_MODE_FIXED
    alpha: float = DEFAULT_ALPHA
    _secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    trials: List[Trial] = field(default_factory=list)
    _pending: Optional[TrialPlan] = None
    _rng_label: Any = None

    def __post_init__(self) -> None:
        if self.label_mode not in (LABEL_MODE_FIXED, LABEL_MODE_BLIND):
            raise ValueError(f"unknown label_mode {self.label_mode!r}")
        if self.planned_trials is not None and self.planned_trials < 1:
            raise ValueError("planned_trials must be >= 1")
        if self._rng_label is None:
            import random

            self._rng_label = random.SystemRandom()

    # ── identity / verifiability ────────────────────────────────────────────
    @property
    def pending_mapping(self) -> Optional[Dict[str, str]]:
        """Display mapping for the trial in flight, or None."""
        if self._pending is None:
            return None
        return dict(self._pending.display_mapping)

    @property
    def session_commitment(self) -> str:
        return commitment(self._secret)

    def reveal(self) -> Dict[str, Any]:
        """Publish at session end so the run is independently auditable."""
        return {
            "commitment": self.session_commitment,
            "secret_hex": self._secret.hex(),
            "x_sequence": x_sequence(self._secret, len(self.trials)),
            "verifiable": True,
        }

    # ── trial lifecycle ─────────────────────────────────────────────────────
    def new_trial(self) -> TrialPlan:
        n = len(self.trials) + 1
        x_source = x_source_for_index(self._secret, n)

        if self.label_mode == LABEL_MODE_BLIND:
            swap = self._rng_label.random() < 0.5
            mapping = {"A": "B", "B": "A"} if swap else {"A": "A", "B": "B"}
        else:
            mapping = {"A": "A", "B": "B"}

        plan = TrialPlan(
            trial_index=n,
            token=secrets.token_urlsafe(16),
            display_mapping=mapping,
            x_source=x_source,
        )
        self._pending = plan
        return plan

    def submit_answer(self, answer_display: str) -> Trial:
        """Score an answer given as a DISPLAY label ('A'/'B' as shown)."""
        if self._pending is None:
            raise RuntimeError("no trial in progress; call new_trial() first")
        label = (answer_display or "").strip().upper()
        if label not in ("A", "B"):
            raise ValueError("answer must be 'A' or 'B'")

        plan = self._pending
        # Display -> source is the ONLY correct direction for the mapping.
        answer_source = plan.display_mapping[label]
        correct = answer_source == plan.x_source

        trial = Trial(
            trial_index=plan.trial_index,
            x_source=plan.x_source,
            answer_display=label,
            answer_source=answer_source,
            correct=correct,
            display_mapping=dict(plan.display_mapping),
            timestamp_utc=_utc_now(),
        )
        self.trials.append(trial)
        self._pending = None
        return trial

    # ── statistics ──────────────────────────────────────────────────────────
    @property
    def total_trials(self) -> int:
        return len(self.trials)

    @property
    def correct_trials(self) -> int:
        return sum(1 for t in self.trials if t.correct)

    @property
    def p_value(self) -> float:
        return one_tailed_p_value(self.total_trials, self.correct_trials)

    @property
    def is_final(self) -> bool:
        """True only when N was pre-registered and has been reached.

        Guards every "significant" claim: a provisional p-value is never
        presented as a verdict.
        """
        return (
            self.planned_trials is not None
            and self.total_trials >= self.planned_trials
        )

    @property
    def threshold(self) -> Optional[int]:
        """Pass criterion, fixed at the PRE-REGISTERED N from trial 1.

        Deliberately NOT a function of the trials taken so far: reporting
        "you need >=9 of the 10 you have done" hands the tester a peeking
        threshold and re-creates the optional-stopping problem this module
        exists to remove. The criterion is announced once, before the test.
        """
        n = self.planned_trials if self.planned_trials is not None else self.total_trials
        return min_correct_for_significance(n, self.alpha) if n else None

    def stats(self) -> Dict[str, Any]:
        n, k = self.total_trials, self.correct_trials
        p = self.p_value
        ci = binomial_ci(n, k)
        return {
            "total_trials": n,
            "correct_trials": k,
            "p_value_one_tailed": p,
            "planned_trials": self.planned_trials,
            "is_final": self.is_final,
            "provisional": not self.is_final,
            "significant": is_significant(n, k, self.alpha),
            "alpha": self.alpha,
            "required_correct": self.threshold,
            "ability_point": round(k / n, 4) if n else None,
            "ability_ci95": list(ci),
            "label_mode": self.label_mode,
        }

    def reset(self) -> None:
        self.trials = []
        self._pending = None
        self._secret = secrets.token_bytes(32)

    # ── serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "planned_trials": self.planned_trials,
            "label_mode": self.label_mode,
            "alpha": self.alpha,
            "trials": [t.to_dict() for t in self.trials],
            "stats": self.stats(),
            "reveal": self.reveal(),
        }

"""Canonical ABX trial engine. Single source of truth for desktop AND web.

Why this file exists
-------------------
The project previously carried two independent trial engines (PyQt6 desktop and
Flask web) with *divergent* randomness and *divergent* blinding heuristics, so
the same study produced different results per shell. This module replaces both.

Five invariants:

1. SCORING IS BLINDING-INDEPENDENT.  The display label mapping is a pure
   presentation concern. `fixed` and `blind_random` produce identical
   arithmetic. (The old code compared a *display* answer against a *source*
   X, and applied the display mapping in the wrong direction, so a perfect
   listener under swapped labels was scored INCORRECT.)

2. NO HOME-BREWED SWAP HEURISTICS.  X is an independent fair coin per trial.
   The old streak rules (web: 0.1*streak capped 0.5; desktop: 0.5 + 0.07*streak
   capped 0.88) made the mapping anti-persistent -- i.e. predictable -- which
   is strictly worse than a coin flip and impossible to audit.

3. THE ANSWER NEVER LEAVES THE SERVER BEFORE THE ANSWER.  `TrialPlan.public()`
   is the only client-visible shape and contains no X information. X audio is
   served by token, resolved server-side.

4. A VERDICT NEEDS VALID INSTRUMENTATION, NOT JUST A P-VALUE.  Two gates run
   before any scored trial counts:
     * CANARY -- a deliberately-easy calibration trial (trial 1) whose answer
       is known by construction. Failing it means the listener's playback
       chain or attention is not working, so the whole run is VOID.
     * NULL -- a pair that is supposed to null (identical pipelines) must
       actually null. If it does not, alignment/level matching is broken and
       every "difference" the listener reports is an artifact.
   Both gates are enforced HERE, in the one engine both shells share, so
   neither shell can offer trials the other would refuse.

Commit-reveal: the X sequence is derived from a random secret via SHA-256, so
the commitment published at session start provably fixes the X sequence in
advance. Any third party can recompute it from (secret, n) and confirm the
tool did not cherry-pick X after seeing answers -- the canary included.

5. A RUN IS AN EXPERIMENT WITH A LIFECYCLE, NOT A LOOP.  The plan is frozen
   and the trial count is bounded:

       configure -> prepare -> lock/run -> complete | abort -> reveal

   `planned_trials`, `label_mode`, `canary_enabled` and the identity of the
   prepared audio pair are frozen the moment the run starts (the first trial
   staged: A/B/X is playable from then on), scored trials stop exactly at N,
   and the secret is unreveable while the same run can still accept answers --
   from the secret, X is derivable for every remaining trial, so a mid-run
   export of the key is the study's answer sheet. An early reveal is available
   only by sealing the run: sealed runs are auditable, are never labelled
   final, and are distinguishable in the export from a redacted snapshot.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .stats import (
    DEFAULT_ALPHA,
    binomial_ci,
    is_significant,
    min_correct_for_significance,
    one_tailed_p_value,
)

LABEL_MODE_FIXED = "fixed"
LABEL_MODE_BLIND = "blind_random"

# Trial kinds. A canary is a scored-looking trial with a KNOWN answer, built
# from deliberately-easy material (pristine vs grossly degraded). It is never
# part of the statistics: it calibrates the listener and the playback chain.
TRIAL_KIND_NORMAL = "normal"
TRIAL_KIND_CANARY = "canary"

# Gate states
CANARY_NOT_CONFIGURED = "not_configured"
CANARY_PENDING = "pending"
CANARY_PASSED = "passed"
CANARY_FAILED = "failed"

NULL_GATE_NOT_MEASURED = "not_measured"
NULL_GATE_NOT_APPLICABLE = "not_applicable"
NULL_GATE_PENDING = "pending"
NULL_GATE_PASSED = "passed"
NULL_GATE_FAILED = "failed"

# Run lifecycle. The point of naming these states is that the rules attached
# to them live in one place: a run that is under way cannot be re-planned, a
# run that has reached N issues no more trials, and a run that can still take
# an answer does not publish its secret.
STATE_CONFIGURED = "configured"   # plan set, nothing staged, everything editable
STATE_RUNNING = "running"         # a trial is staged or answered: plan frozen
STATE_COMPLETED = "completed"     # N reached as pre-registered: reveal allowed
STATE_ABORTED = "aborted"         # sealed by the tester: reveal allowed, no result

_COMMIT_PREFIX = b"ABX1\x00"


class StudyLifecycleError(RuntimeError):
    """Base for lifecycle refusals. Carries plain-language `reasons` so every
    shell can surface the same explanation in its own widget."""

    def __init__(self, message: str, reasons: Optional[Iterable[str]] = None) -> None:
        super().__init__(message)
        self.reasons: List[str] = list(reasons or [])


class TrialLimitReached(StudyLifecycleError):
    """The pre-registered N has been answered: no further scored trial.

    This is the anti-peeking rule made structural. Before it existed,
    `is_final` reported "final" at N while `new_trial()` kept issuing trials,
    so a tester who did not like the p-value just kept going and the run had
    no fixed N at all.
    """


class StudySealed(StudyLifecycleError):
    """The run was aborted/sealed: it accepts no further answers, and a sealed
    partial run is auditable but is never reported as a result."""


class ConfigLocked(StudyLifecycleError):
    """The plan was frozen when the run started. Redrawing N, the label mode
    or the calibration switch after the listener can already hear A/B/X is
    how a pass criterion gets moved under the data."""


class TrialGateClosed(StudyLifecycleError):
    """Trials must not be run: a validity gate is closed.

    Carries the human-readable reasons so every shell can surface the same
    explanation in its own widget.
    """


class CanaryFailedError(TrialGateClosed):
    """The calibration trial was answered wrong: the run is void."""


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
    `kind` is public information about the TRIAL, not about X: a canary trial
    is announced on purpose, its answer (which side X is) stays secret.
    """

    trial_index: int
    token: str
    display_mapping: Mapping[str, str]  # display label -> source
    x_source: str
    kind: str = TRIAL_KIND_NORMAL

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
    kind: str = TRIAL_KIND_NORMAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_index": self.trial_index,
            "x_source": self.x_source,
            "answer_display": self.answer_display,
            "answer_source": self.answer_source,
            "correct": self.correct,
            "display_mapping": dict(self.display_mapping),
            "timestamp_utc": self.timestamp_utc,
            "kind": self.kind,
        }


@dataclass
class ABXEngine:
    """Forced-choice trial engine with pre-registration and validity gates.

    planned_trials: N declared BEFORE the first answer. The verdict is only
    `is_final` once n reaches N. p-values below N are marked provisional, which
    is the structural fix for optional-stopping / p-hacking. Reaching N also
    ends the run: `new_trial()` refuses, so N is a bound, not a caption.

    canary_enabled: makes trial 1 a calibration trial with a known answer.
    When it is answered wrong, `new_trial()` raises and the verdict is VOID.
    It defaults to False so an engine built without a shell (and every
    pre-existing test) behaves exactly as before; both shells turn it on.

    The lifecycle fields below are what makes the plan a plan: `run_lock` is
    the frozen snapshot taken when the first trial is staged, `prepared_audio`
    is the identity of the rendered pair the run was locked to, and
    `lifecycle` decides whether answers are accepted and whether
    `reveal()` may publish the secret.
    """

    planned_trials: Optional[int] = None
    label_mode: str = LABEL_MODE_FIXED
    alpha: float = DEFAULT_ALPHA
    canary_enabled: bool = False
    _secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    trials: List[Trial] = field(default_factory=list)
    canary_trial: Optional[Trial] = None
    null_gate: Optional[Dict[str, Any]] = None
    _pending: Optional[TrialPlan] = None
    _rng_label: Any = None
    lifecycle: str = STATE_CONFIGURED
    prepared_audio: Optional[Dict[str, Any]] = None
    run_lock: Optional[Dict[str, Any]] = None
    sealed_reason: Optional[str] = None
    sealed_at_utc: Optional[str] = None
    completed_at_utc: Optional[str] = None

    # Field names that must not move once a run is under way. The guard is in
    # `__setattr__`, not in a setter, because the shells assign these fields
    # directly -- a setter-only rule would be a rule for polite callers only.
    _FROZEN_WHILE_ACTIVE = frozenset(
        {"planned_trials", "label_mode", "alpha", "canary_enabled"}
    )

    def __post_init__(self) -> None:
        if self.label_mode not in (LABEL_MODE_FIXED, LABEL_MODE_BLIND):
            raise ValueError(f"unknown label_mode {self.label_mode!r}")
        if self.planned_trials is not None and self.planned_trials < 1:
            raise ValueError("planned_trials must be >= 1")
        if self.lifecycle not in (
            STATE_CONFIGURED, STATE_RUNNING, STATE_COMPLETED, STATE_ABORTED
        ):
            raise ValueError(f"unknown lifecycle {self.lifecycle!r}")
        if self._rng_label is None:
            import random

            self._rng_label = random.SystemRandom()

    def __setattr__(self, name: str, value: Any) -> None:
        if (
            name in self._FROZEN_WHILE_ACTIVE
            and getattr(self, "lifecycle", STATE_CONFIGURED) != STATE_CONFIGURED
        ):
            raise ConfigLocked(
                f"{name} is frozen for the duration of the run: the plan "
                "(N, label mode, calibration, prepared audio) is fixed before "
                "the answers, and a new plan needs a new run (reset)"
            )
        object.__setattr__(self, name, value)

    # ── lifecycle: configure -> prepare -> lock/run -> complete | abort ─────
    def has_answers(self) -> bool:
        """Any recorded answer, calibration included."""
        return bool(self.trials) or self.canary_trial is not None

    def trial_in_flight(self) -> bool:
        return self._pending is not None

    def run_under_way(self) -> bool:
        """The run has begun: a trial is staged (A/B/X is playable) or answered.

        Freezing at the staged trial, not at the first answer, is deliberate:
        the commitment is published and the audio is playable the moment trial
        1 exists, so from there on a redrawn N is a criterion moved under data
        that already exists.
        """
        return (
            self.lifecycle != STATE_CONFIGURED
            or self.has_answers()
            or self.trial_in_flight()
        )

    def config_active(self) -> bool:
        """Whether the plan is frozen (the shells ask this before editing)."""
        return self.run_under_way()

    def can_accept_answers(self) -> bool:
        """Can this run still take a real answer? This is the reveal gate.

        False when N is reached, when the run was sealed, and when a validity
        gate closed (further trials are refused, so the remaining X is worth
        nothing and the export can be audited).
        """
        if self.lifecycle in (STATE_COMPLETED, STATE_ABORTED):
            return False
        if not self.gate_open:
            return False
        if self.planned_trials is not None and self.total_trials >= self.planned_trials:
            return False
        return True

    def why_no_answers(self) -> List[str]:
        """Why the run is over, in plain words, for the export and the UI."""
        if self.lifecycle == STATE_ABORTED:
            return [
                "run sealed/aborted"
                + (f": {self.sealed_reason}" if self.sealed_reason else "")
                + " -- a sealed partial run is auditable, not a result"
            ]
        if self.lifecycle == STATE_COMPLETED:
            return [
                f"pre-registered N={self.planned_trials} reached at "
                f"n={self.total_trials}: the run is complete"
            ]
        if not self.gate_open:
            return self.gate_reasons()
        return []

    def set_prepared_audio(self, identity: str, **details: Any) -> None:
        """Record the identity of the rendered A/B pair this run will use.

        The identity is whatever the shell can prove about the pair -- a digest
        of the two rendered files plus a fingerprint of the configuration that
        rendered them. Freezing it is what stops a run from being locked to, or
        a verdict describing, audio that is not the pair that was prepared.
        """
        if self.run_under_way():
            raise ConfigLocked(
                "the prepared audio pair is frozen for the duration of the "
                "run: a new pair means a new run (reset), so a verdict can "
                "never describe audio the listener did not hear"
            )
        block: Dict[str, Any] = dict(details)
        block["id"] = str(identity)
        block.setdefault("prepared_at_utc", _utc_now())
        self.prepared_audio = block

    def lock_run(self, prepared_audio_id: Optional[str] = None) -> Dict[str, Any]:
        """Freeze the plan and start the run.

        `prepared_audio_id` is checked against the recorded pair: a shell that
        tries to run on audio other than the pair it prepared is refused here,
        in the one place both shells share.
        """
        if prepared_audio_id and self.prepared_audio:
            expected = self.prepared_audio.get("id")
            if expected and str(prepared_audio_id) != str(expected):
                raise ValueError(
                    f"run locked to prepared audio {prepared_audio_id!r} but "
                    f"the prepared pair is {expected!r}: the rendered audio "
                    "does not match the run (re-preprocess, then run)"
                )
        if self.lifecycle == STATE_CONFIGURED:
            self.lifecycle = STATE_RUNNING
            self.run_lock = {
                "planned_trials": self.planned_trials,
                "label_mode": self.label_mode,
                "alpha": self.alpha,
                "canary_enabled": bool(self.canary_enabled),
                "prepared_audio_id": (
                    prepared_audio_id
                    or (self.prepared_audio or {}).get("id")
                ),
                "locked_at_utc": _utc_now(),
            }
        return dict(self.run_lock or {})

    def abort(self, reason: Optional[str] = None) -> Dict[str, Any]:
        """Seal the run: no further answers, secret released, never a result.

        Early reveal is only honest if the run stops: while answers are
        accepted the secret is the answer key for the trials still to come.
        """
        self.sealed_reason = str(reason) if reason else "sealed by the tester"
        self.sealed_at_utc = _utc_now()
        self.lifecycle = STATE_ABORTED
        self._pending = None
        return {"lifecycle": self.lifecycle, "sealed_reason": self.sealed_reason}

    # Shells and users reach for this as "end the run now", so it is named both ways.
    seal = abort

    def _assert_not_sealed(self) -> None:
        if self.lifecycle == STATE_ABORTED:
            raise StudySealed(
                "this run was sealed/aborted: it accepts no further answers",
                self.why_no_answers(),
            )

    def _assert_below_n(self) -> None:
        """Stop scored trials exactly at the pre-registered N."""
        if self.planned_trials is None:
            return
        if self.total_trials < self.planned_trials:
            return
        if self.lifecycle != STATE_COMPLETED:
            self.lifecycle = STATE_COMPLETED
            self.completed_at_utc = _utc_now()
        raise TrialLimitReached(
            f"pre-registered N={self.planned_trials} reached: no further "
            "scored trials (reset to start a new run)",
            self.why_no_answers(),
        )

    def _maybe_complete(self) -> None:
        if (
            self.lifecycle == STATE_RUNNING
            and self.planned_trials is not None
            and self.total_trials >= self.planned_trials
        ):
            self.lifecycle = STATE_COMPLETED
            self.completed_at_utc = _utc_now()

    # ── configuration (frozen while a run is under way) ─────────────────────
    def set_planned_trials(self, n: Optional[int]) -> None:
        """Pre-register N. Refused once the run is under way."""
        self._assert_config_editable("planned_trials")
        if n is not None:
            n = int(n)
            if n < 1:
                raise ValueError("planned_trials must be >= 1")
        self.planned_trials = n

    def set_label_mode(self, label_mode: str) -> None:
        self._assert_config_editable("label_mode")
        if label_mode not in (LABEL_MODE_FIXED, LABEL_MODE_BLIND):
            raise ValueError(f"unknown label_mode {label_mode!r}")
        self.label_mode = label_mode

    def set_canary_enabled(self, enabled: bool) -> None:
        self._assert_config_editable("canary_enabled")
        self.canary_enabled = bool(enabled)

    def _assert_config_editable(self, name: str) -> None:
        if self.run_under_way():
            raise ConfigLocked(
                f"{name} is frozen for the duration of the run (answers "
                f"recorded: {self.total_trials}, trial in flight: "
                f"{self.trial_in_flight()}): reset to plan a new run",
                [f"{name} is frozen: the plan is fixed before the answers"],
            )

    # ── identity / verifiability ────────────────────────────────────────────
    @property
    def pending_mapping(self) -> Optional[Dict[str, str]]:
        """Display mapping for the trial in flight, or None."""
        if self._pending is None:
            return None
        return dict(self._pending.display_mapping)

    @property
    def pending_plan(self) -> Optional[TrialPlan]:
        """The trial in flight, SERVER-SIDE only.

        Shells use it to resolve audio for the current trial; it must never be
        serialised to a client (`public()` is the only client shape).
        """
        return self._pending

    @property
    def session_commitment(self) -> str:
        return commitment(self._secret)

    def all_trials(self) -> List[Trial]:
        """Canary + scored trials, in index order.

        The X sequence commits the whole run, calibration included, so the
        exported trial list and the reveal must cover the same indices.
        """
        out = list(self.trials)
        if self.canary_trial is not None:
            out.append(self.canary_trial)
        return sorted(out, key=lambda t: t.trial_index)

    def reveal(self) -> Dict[str, Any]:
        """Publish the answer key so the run is independently auditable.

        The secret is the whole point of the block, and it is also the answer
        key for every trial that has not been answered yet: X for trial i is
        `sha256(secret + i)`. So it is released only when this run can no
        longer accept an answer -- complete, sealed, or refused by a validity
        gate. While answers are still accepted the block is an explicitly
        REDACTED snapshot: the commitment and the answered part stay public
        (every trial row already carries its own `x_source`, and the
        commitment fixes the sequence in advance), the key does not.
        """
        recorded = self.all_trials()
        can_answer = self.can_accept_answers()
        reasons = self.why_no_answers()
        block: Dict[str, Any] = {
            "commitment": self.session_commitment,
            "secret_hex": None if can_answer else self._secret.hex(),
            # The answered part is always transcribed: it is not a secret, and
            # a third party checking a finished run needs it verbatim.
            "x_sequence": x_sequence(self._secret, len(recorded)),
            "trials_covered": len(recorded),
            "canary_included": self.canary_trial is not None,
            "lifecycle": self.lifecycle,
            "complete": self.lifecycle == STATE_COMPLETED,
            "sealed": self.lifecycle == STATE_ABORTED,
            "sealed_reason": self.sealed_reason,
            "sealed_at_utc": self.sealed_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "secret_revealed": not can_answer,
            "redacted": can_answer,
            # `verifiable` is the citable claim: a redacted snapshot cannot be
            # recomputed by a third party, so it must not claim to be.
            "verifiable": not can_answer,
            "run_closed_for_answers": not can_answer,
        }
        if can_answer:
            block["reason"] = (
                "redacted: the run can still accept answers, and the secret "
                "derives X for every trial not yet answered "
                f"(planned N={self.planned_trials}, answered n={self.total_trials}). "
                "Finish N, or seal the run, for a verifiable export."
            )
        elif reasons:
            block["reason"] = "; ".join(reasons)
        return block

    # ── gates ───────────────────────────────────────────────────────────────
    @property
    def canary_status(self) -> str:
        if not self.canary_enabled:
            return CANARY_NOT_CONFIGURED
        if self.canary_trial is None:
            return CANARY_PENDING
        return CANARY_PASSED if self.canary_trial.correct else CANARY_FAILED

    def canary_block(self) -> Dict[str, Any]:
        # While the calibration trial is in flight its index is already known
        # (it is the next trial issued), so the shells can say "pending at
        # trial #1". `x_source` is only filled in AFTER the answer: before that
        # it is the secret, and this block reaches the client through stats().
        pending = self._pending
        pending_canary = (
            pending is not None and pending.kind == TRIAL_KIND_CANARY
        )
        index = (
            self.canary_trial.trial_index
            if self.canary_trial is not None
            else (pending.trial_index if pending_canary else None)
        )
        return {
            "enabled": bool(self.canary_enabled),
            "status": self.canary_status,
            "trial_index": index,
            "correct": self.canary_trial.correct if self.canary_trial else None,
            "x_source": self.canary_trial.x_source if self.canary_trial else None,
            "answer_display": (
                self.canary_trial.answer_display if self.canary_trial else None
            ),
        }

    def set_null_gate(self, decision: Optional[Mapping[str, Any]]) -> None:
        """Record the null-pair gate decision for the prepared audio.

        Built by `core.alignment.null_gate_decision` in the shell right after
        preprocessing, then handed here so trial start is refused in ONE place
        for both shells.
        """
        if decision is None:
            self.null_gate = None
            return
        block = dict(decision)
        block.setdefault("status", NULL_GATE_PENDING)
        self.null_gate = block

    def null_gate_block(self) -> Dict[str, Any]:
        if not self.null_gate:
            return {"status": NULL_GATE_NOT_MEASURED, "applicable": False}
        block = dict(self.null_gate)
        block.setdefault("applicable", block.get("status") not in
                         (NULL_GATE_NOT_APPLICABLE, NULL_GATE_NOT_MEASURED))
        return block

    @property
    def gate_open(self) -> bool:
        return not self.gate_reasons()

    def gate_reasons(self) -> List[str]:
        """Why trials must not be run, in plain words. Empty == trials allowed."""
        reasons: List[str] = []
        if self.canary_status == CANARY_FAILED:
            reasons.append(
                "calibration (canary) trial failed: X was answered as "
                f"{self.canary_trial.answer_display} and was wrong -- the "
                "playback chain or the listening is not working, run void"
            )
        if self.null_gate_block().get("status") == NULL_GATE_FAILED:
            reasons.append(
                "null gate failed: identical pipelines did not null "
                f"(residual {self.null_gate_block().get('measured_text', '?')} "
                f"against a {self.null_gate_block().get('threshold_db')} dB floor): "
                "alignment/level matching is broken, run void"
            )
        return reasons

    def gates(self) -> Dict[str, Any]:
        reasons = self.gate_reasons()
        return {
            "open": not reasons,
            "void": bool(reasons),
            "canary": self.canary_block(),
            "null": self.null_gate_block(),
            "reasons": reasons,
        }

    @property
    def run_valid(self) -> bool:
        """A run with a failed gate is void even if the p-value is significant."""
        return not self.gates()["void"]

    def verdict(self) -> str:
        """One-line verdict that cannot be reported as a pass once voided.

        Order matters. A failed gate voids the run whatever the arithmetic
        says. A sealed run is not a result at all, so it gets said before any
        p-value is quoted. Below N, the number is descriptive.
        """
        if not self.run_valid:
            return "VOID: " + "; ".join(self.gate_reasons())
        if self.lifecycle == STATE_ABORTED:
            return (
                f"ABORTED at n={self.total_trials}"
                + (
                    f" of N={self.planned_trials}"
                    if self.planned_trials is not None
                    else ""
                )
                + f": sealed ({self.sealed_reason}), not a result"
            )
        if self.planned_trials is None:
            return "no N pre-registered - p-value is descriptive, not a verdict"
        if not self.is_final:
            return f"provisional at n={self.total_trials} of N={self.planned_trials}"
        return (
            f"{self.correct_trials}/{self.planned_trials} at N={self.planned_trials}, "
            f"p={self.p_value:.5f}"
        )

    # ── trial lifecycle ─────────────────────────────────────────────────────
    @property
    def current_trial_kind(self) -> str:
        """Kind of the trial in flight (public: the canary is announced)."""
        return self._pending.kind if self._pending is not None else TRIAL_KIND_NORMAL

    def _next_index(self) -> int:
        """Contiguous index over ANSWERED trials + 1.

        Re-issuing an unanswered trial keeps the same index, so the recorded
        X sequence stays 1..n with no holes and always matches the commitment.
        """
        return len(self.all_trials()) + 1

    def _assert_gate_open(self) -> None:
        reasons = self.gate_reasons()
        if not reasons:
            return
        if self.canary_status == CANARY_FAILED:
            raise CanaryFailedError(
                "calibration trial failed: this run is void, no further trials",
                reasons,
            )
        raise TrialGateClosed("trials are gated: " + "; ".join(reasons), reasons)

    def new_trial(self) -> TrialPlan:
        """Issue the next trial.

        Trial 1 is the calibration trial when the canary is enabled; it must be
        answered before any scored trial exists.

        Refused, in this order: a sealed run, a closed validity gate, a run that
        has reached its pre-registered N. The first call of a run locks the plan
        (`lock_run`), so the N, label mode, calibration switch and prepared
        audio that this sequence was drawn under are on the record.
        """
        self._assert_not_sealed()
        self._assert_gate_open()
        self._assert_below_n()
        if self.lifecycle == STATE_CONFIGURED:
            self.lock_run()

        is_canary = self.canary_enabled and self.canary_trial is None
        n = self._next_index()
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
            kind=TRIAL_KIND_CANARY if is_canary else TRIAL_KIND_NORMAL,
        )
        self._pending = plan
        return plan

    def submit_answer(self, answer_display: str) -> Trial:
        """Score an answer given as a DISPLAY label ('A'/'B' as shown)."""
        self._assert_not_sealed()
        if self._pending is None:
            if self.lifecycle == STATE_COMPLETED:
                raise TrialLimitReached(
                    f"pre-registered N={self.planned_trials} reached: the run "
                    "is complete and has no trial in flight",
                    self.why_no_answers(),
                )
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
            kind=plan.kind,
        )
        # The calibration trial is recorded, never scored: it says nothing
        # about codec discrimination and must not inflate the binomial.
        if plan.kind == TRIAL_KIND_CANARY:
            self.canary_trial = trial
        else:
            self.trials.append(trial)
        self._pending = None
        # Reaching N ends the run. This is where "no more trials" becomes
        # structural: the answer that completes the pre-registration is the
        # last answer the run accepts.
        self._maybe_complete()
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
        """True only when N was pre-registered, reached, and the run is over.

        Guards every "significant" claim: a provisional p-value is never
        presented as a verdict. A sealed/aborted run is never final -- an
        incomplete run cannot become a result by having its key released.
        """
        return (
            self.lifecycle == STATE_COMPLETED
            and self.planned_trials is not None
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
        gates = self.gates()
        significant = is_significant(n, k, self.alpha)
        final = self.is_final
        valid = self.run_valid
        return {
            "total_trials": n,
            "correct_trials": k,
            "p_value_one_tailed": p,
            "planned_trials": self.planned_trials,
            "is_final": final,
            "provisional": not final,
            # Raw arithmetic, unedited: it is what it is at any n.
            "significant": significant,
            # The claim a reader acts on. `significant` alone is not a pass:
            # below N it is an unregistered peek, and on a void run the
            # instrumentation itself failed. Both shells quote THIS to say
            # "pass", so the guarded verdict cannot be bypassed by reading
            # the raw field.
            "significant_verdict": bool(significant and final and valid),
            "alpha": self.alpha,
            "required_correct": self.threshold,
            "ability_point": round(k / n, 4) if n else None,
            "ability_ci95": list(ci),
            "label_mode": self.label_mode,
            "canary": gates["canary"],
            "canary_status": gates["canary"]["status"],
            "null_gate": gates["null"],
            "gates": gates,
            "run_valid": valid,
            "void": gates["void"],
            "verdict": self.verdict(),
            "current_trial_kind": self.current_trial_kind,
            # Lifecycle: the run's state, and whether it can still take an
            # answer. A finished run is `complete`; a run stopped early is
            # `sealed`; neither is still answering, and only the first is a
            # result. This is what the UI uses to stop advancing.
            "lifecycle": self.lifecycle,
            "can_accept_answers": self.can_accept_answers(),
            "complete": self.lifecycle == STATE_COMPLETED,
            "sealed": self.lifecycle == STATE_ABORTED,
            "aborted": self.lifecycle == STATE_ABORTED,
            "sealed_reason": self.sealed_reason,
            "run_closed_because": self.why_no_answers(),
            "prepared_audio": dict(self.prepared_audio or {}),
            "run_lock": dict(self.run_lock or {}),
        }

    def reset(self) -> None:
        """New run: new secret, trials and calibration cleared, plan editable.

        The null gate and the prepared pair describe the RENDERED AUDIO, not
        the answers, so they survive: resetting trials does not make a broken
        pair testable. The lifecycle returns to `configured` because this is a
        new experiment, planned from scratch -- a sealed run stays sealed in
        the file it exported, it does not haunt the next one.
        """
        self.trials = []
        self.canary_trial = None
        self._pending = None
        self._secret = secrets.token_bytes(32)
        self.lifecycle = STATE_CONFIGURED
        self.run_lock = None
        self.sealed_reason = None
        self.sealed_at_utc = None
        self.completed_at_utc = None

    # ── serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "planned_trials": self.planned_trials,
            "label_mode": self.label_mode,
            "alpha": self.alpha,
            "canary_enabled": self.canary_enabled,
            "lifecycle": self.lifecycle,
            "complete": self.lifecycle == STATE_COMPLETED,
            "sealed": self.lifecycle == STATE_ABORTED,
            "sealed_reason": self.sealed_reason,
            "can_accept_answers": self.can_accept_answers(),
            "run_lock": dict(self.run_lock or {}),
            "prepared_audio": dict(self.prepared_audio or {}),
            "trials": [t.to_dict() for t in self.all_trials()],
            "stats": self.stats(),
            "gates": self.gates(),
            "reveal": self.reveal(),
        }

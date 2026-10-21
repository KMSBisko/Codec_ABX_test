"""Desktop trial engine: thin shim over the canonical `core.abx` engine.

Kept as a compatibility layer so `main.py` keeps its existing surface:
  * `current_x_is`            -> X as a DISPLAY label (what the UI shows)
  * `display_mapping`         -> {"A": <source>, "B": <source>}
  * `submit_answer(label) -> bool`
  * `stats() -> ABXStats`

The pre-Phase-0 implementation here kept its own `random.SystemRandom()` X
draw and its own streak-based label swapping in main.py, i.e. a second
independent trial engine that diverged from the web one. Both now share
`core.abx`, so identical studies yield identical statistics.

Note on what was NOT broken here: the legacy desktop scoring was internally
consistent -- `current_x_is` is a display label and answers arrive as display
labels, so `answer == current_x_is` was a valid comparison. The legacy *web*
engine compared a display answer against a source-level X, which was not.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.abx import (  # noqa: E402
    LABEL_MODE_BLIND,
    LABEL_MODE_FIXED,
    TRIAL_KIND_CANARY,
    ABXEngine as CoreEngine,
    TrialGateClosed,
)
from core.stats import min_correct_for_significance  # noqa: E402


@dataclass
class ABXStats:
    total_trials: int
    correct_trials: int
    p_value_one_tailed: float
    planned_trials: Optional[int] = None
    is_final: bool = False
    provisional: bool = True
    required_correct: Optional[int] = None
    significant: bool = False
    ability_ci95: Optional[list] = None
    # Phase 1 validity gates. The p-value answers "did the listener beat
    # chance"; these answer "was the run allowed to be run at all", and a
    # failed gate voids the result no matter how good the p-value looks.
    void: bool = False
    run_valid: bool = True
    verdict: str = ""
    canary_status: str = "not_configured"
    null_gate_status: str = "not_measured"
    current_trial_kind: str = "normal"

    @property
    def p_value_text(self) -> str:
        """p-value phrased for display, honouring the pre-registration gate."""
        if self.void:
            # Significance is not a verdict once a validity gate has failed.
            return f"p: {self.p_value_one_tailed:.4f} (VOID: run invalid)"
        if self.total_trials == 0:
            return "p: -"
        if self.provisional:
            need = self.planned_trials or "?"
            return f"p: {self.p_value_one_tailed:.4f} (provisional, N={need})"
        return f"p: {self.p_value_one_tailed:.6f}"


class ABXEngine:
    """Shim: canonical engine + the display-label surface the GUI expects.

    The GUI expects `new_trial()` to always stage a playable trial. Validity
    gates break that expectation on purpose, so the shim absorbs the
    difference: a closed gate is recorded in `gate_closed` (plain text, ready
    for a dialog) instead of propagating, and the caller checks `gate_open`.
    One rule, so the desktop cannot drift into offering trials the web refuses.
    """

    def __init__(
        self,
        planned_trials: Optional[int] = None,
        label_mode: str = LABEL_MODE_FIXED,
        canary_enabled: bool = False,
    ) -> None:
        self._core = CoreEngine(
            planned_trials=planned_trials,
            label_mode=label_mode,
            canary_enabled=canary_enabled,
        )
        self._pending_source: Optional[str] = None
        self._mapping: Dict[str, str] = {"A": "A", "B": "B"}
        self._kind: str = "normal"
        self._last_answer = None
        self.gate_closed: Optional[str] = None
        self.current_x_is: str = "A"
        self.new_trial()

    # ── label mapping (presentation only) ───────────────────────────────────
    @property
    def display_mapping(self) -> Dict[str, str]:
        return dict(self._mapping)

    @property
    def label_mode(self) -> str:
        return self._core.label_mode

    def set_label_mode(self, label_mode: str) -> None:
        self._core.label_mode = LABEL_MODE_BLIND if label_mode == "blind_random" else LABEL_MODE_FIXED

    @property
    def planned_trials(self) -> Optional[int]:
        return self._core.planned_trials

    def set_planned_trials(self, n: Optional[int]) -> None:
        self._core.planned_trials = n

    @property
    def threshold(self) -> Optional[int]:
        n = self._core.planned_trials
        return min_correct_for_significance(n, self._core.alpha) if n else None

    # ── validity gates ──────────────────────────────────────────────────────
    @property
    def canary_enabled(self) -> bool:
        return bool(self._core.canary_enabled)

    @property
    def current_trial_kind(self) -> str:
        """'canary' while the calibration trial is the one in flight."""
        return self._kind

    @property
    def is_canary_trial(self) -> bool:
        return self._kind == TRIAL_KIND_CANARY

    @property
    def gate_open(self) -> bool:
        return self.gate_closed is None

    def gate_reasons(self) -> list:
        return self._core.gate_reasons()

    def gates(self) -> dict:
        return self._core.gates()

    @property
    def run_valid(self) -> bool:
        return self._core.run_valid

    def canary_block(self) -> dict:
        return self._core.canary_block()

    def null_gate_block(self) -> dict:
        return self._core.null_gate_block()

    def set_null_gate(self, decision) -> None:
        """Record the prepared pair's null-pair gate (core.alignment)."""
        self._core.set_null_gate(decision)
        if decision is not None and decision.get("status") == "failed":
            self.gate_closed = "; ".join(self.gate_reasons())

    # ── trial lifecycle ─────────────────────────────────────────────────────
    def new_trial(self) -> str:
        """Stage the next trial. Never raises: a closed gate is recorded."""
        try:
            plan = self._core.new_trial()
        except TrialGateClosed as exc:
            self.gate_closed = "; ".join(exc.reasons)
            self._pending_source = None
            self._mapping = {"A": "A", "B": "B"}
            self._kind = "normal"
            self.current_x_is = "A"
            return self.current_x_is

        self.gate_closed = None
        self._mapping = dict(plan.display_mapping)
        self._pending_source = plan.x_source
        self._kind = plan.kind
        self.current_x_is = plan.x_display_label()
        return self.current_x_is

    def submit_answer(self, answer: str) -> bool:
        trial = self._core.submit_answer(answer)
        self._last_answer = trial
        self.new_trial()  # stage the next trial, as the legacy engine did
        return trial.correct

    @property
    def last_trial(self):
        """The trial just answered (core.abx.Trial) or None."""
        return self._last_answer

    def all_trials(self) -> list:
        """Scored trials plus the calibration trial, in index order."""
        return self._core.all_trials()

    def reset(self) -> None:
        self._core.reset()
        self._last_answer = None
        self.new_trial()

    # ── audit / verifiability ───────────────────────────────────────────────
    @property
    def current_x_source(self) -> str:
        """Source of X for the staged trial. 'A' when the gate is closed.

        Check `gate_open` before using it: with no staged trial there is no X.
        """
        return self._pending_source or "A"

    def reveal(self) -> dict:
        return self._core.reveal()

    @property
    def session_commitment(self) -> str:
        return self._core.session_commitment

    # ── statistics ──────────────────────────────────────────────────────────
    def stats(self) -> ABXStats:
        raw = self._core.stats()
        return ABXStats(
            total_trials=raw["total_trials"],
            correct_trials=raw["correct_trials"],
            p_value_one_tailed=raw["p_value_one_tailed"],
            planned_trials=raw["planned_trials"],
            is_final=raw["is_final"],
            provisional=raw["provisional"],
            required_correct=raw["required_correct"],
            significant=raw["significant"],
            ability_ci95=raw["ability_ci95"],
            void=bool(raw["void"]),
            run_valid=bool(raw["run_valid"]),
            verdict=str(raw["verdict"]),
            canary_status=str(raw["canary_status"]),
            null_gate_status=str(raw["null_gate"]["status"]),
            current_trial_kind=str(raw["current_trial_kind"]),
        )

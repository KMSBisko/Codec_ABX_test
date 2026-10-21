"""Shell-independent core for the codec ABX tool.

Import discipline: `core/` must not import PyQt6, Flask, numpy, sounddevice or
any audio backend. Desktop and web are thin shells over this package, so the
same study produces the same statistics in both.
"""
from .report import null_depth_text, validity_lines
from .alignment import align_pair, estimate_lag, null_depth_db
from .abx import (
    LABEL_MODE_BLIND,
    LABEL_MODE_FIXED,
    ABXEngine,
    Trial,
    TrialPlan,
    commitment,
    verify_commitment,
    x_sequence,
)

__all__ = [
    "ABXEngine",
    "align_pair",
    "null_depth_text",
    "validity_lines",
    "estimate_lag",
    "null_depth_db",
    "Trial",
    "TrialPlan",
    "LABEL_MODE_FIXED",
    "LABEL_MODE_BLIND",
    "commitment",
    "verify_commitment",
    "x_sequence",
]

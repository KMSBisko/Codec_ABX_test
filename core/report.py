"""Report content that must be identical in every shell.

The desktop panel, the JSON export and the web diagnostics all describe the
same run. Keeping the wording in `core/` means a reader sees the same evidence
in all three, and it keeps the logic testable without Qt or Flask.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

T = Callable[[str], str]

DEFAULT_LABELS: Dict[str, str] = {
    "diag_validity_block": "── Test validity evidence ──",
    "diag_null_depth": "Null depth (A-B residual)",
    "diag_alignment": "Alignment",
    "diag_loudness_pre": "Loudness delta BEFORE normalization",
    "diag_loudness_residual": "Loudness residual after matching",
    "diag_codec_substituted": "Codec substituted",
    "diag_resample_engine": "Resample engine",
    "diag_note": "NOTE",
    "diag_verdict": "Verdict",
    "diag_no_planned_n": (
        "Verdict: no N pre-registered - p-value is descriptive, not a verdict"
    ),
    "diag_commitment": "X-sequence commitment",
}


def null_depth_text(null_depth_db: Optional[float]) -> str:
    """A perfect null is -inf, which must not print as the biggest number."""
    if null_depth_db is None:
        return "not measured"
    if null_depth_db == float("-inf"):
        return "perfect (-inf dB)"
    if null_depth_db == float("inf") or null_depth_db != null_depth_db:  # nan
        return "undefined (silence in reference)"
    return f"{null_depth_db:.2f} dB"


def validity_lines(
    *,
    null_depth_db: Optional[float],
    alignment_lag_samples: int,
    alignment_confidence: float,
    loudness: Optional[Dict[str, Any]] = None,
    disclosure: Optional[Dict[str, Any]] = None,
    notes: Optional[List[str]] = None,
    stats: Optional[Dict[str, Any]] = None,
    commitment: Optional[str] = None,
    t: Optional[T] = None,
) -> List[str]:
    """The evidence a reader needs to judge a run, not just its outcome.

    Null depth and alignment confidence are measurements of claims the
    pipeline makes about itself. The disclosure block states anything that
    differs from what the user asked for.
    """
    tr: T = t if t is not None else (lambda key: DEFAULT_LABELS.get(key, key))
    lines: List[str] = []

    lines.append(
        f"{tr('diag_null_depth')}: {null_depth_text(null_depth_db)}"
    )
    lines.append(
        f"{tr('diag_alignment')}: lag={alignment_lag_samples} samples, "
        f"confidence={alignment_confidence:.4f}"
    )

    if loudness:
        lines.append(
            f"{tr('diag_loudness_pre')}: "
            f"{loudness['pre_delta_db']:.3f} dB "
            f"(gain A={loudness['gain_a_db']:+.2f} / "
            f"B={loudness['gain_b_db']:+.2f} dB, "
            f"trim={loudness['global_trim_db']:.2f} dB)"
        )
        lines.append(
            f"{tr('diag_loudness_residual')}: "
            f"{loudness['residual_delta_db']:.4f} dB"
        )

    disclosure = disclosure or {}
    fallbacks = disclosure.get("codec_fallbacks") or []
    if fallbacks:
        for fb in fallbacks:
            lines.append(
                f"{tr('diag_codec_substituted')}: "
                f"{fb.get('requested_codec_name')} -> "
                f"{fb.get('actual_codec_name')} "
                f"(encoder={fb.get('actual_encoder')})"
            )
    else:
        lines.append(f"{tr('diag_codec_substituted')}: none")

    resample = disclosure.get("resample")
    if resample:
        lines.append(
            f"{tr('diag_resample_engine')}: requested="
            f"{resample.get('requested_engine')} "
            f"used={resample.get('engine_used')} "
            f"downgraded={resample.get('downgraded')}"
        )

    for note in notes or []:
        lines.append(f"{tr('diag_note')}: {note}")

    if stats:
        if stats.get("planned_trials"):
            state = "FINAL" if stats.get("is_final") else "PROVISIONAL"
            ci = stats.get("ability_ci95") or [0.0, 1.0]
            lines.append(
                f"{tr('diag_verdict')}: {state} at N={stats['planned_trials']}, "
                f"need >={stats.get('required_correct')} correct "
                f"(ability 95% CI [{ci[0]:.2f}, {ci[1]:.2f}])"
            )
        else:
            lines.append(tr("diag_no_planned_n"))

    if commitment:
        lines.append(f"{tr('diag_commitment')}: {commitment[:16]}...")

    return lines

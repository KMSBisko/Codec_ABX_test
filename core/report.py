"""Report content that must be identical in every shell.

The desktop panel, the JSON export and the web diagnostics all describe the
same run. Keeping the wording in `core/` means a reader sees the same evidence
in all three, and it keeps the logic testable without Qt or Flask.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from .alignment import describe_depth

T = Callable[[str], str]

DEFAULT_LABELS: Dict[str, str] = {
    "diag_validity_block": "── Test validity evidence ──",
    "diag_null_depth": "Null depth (A-B residual)",
    "diag_null_gate": "Null gate (identical-pair check)",
    "diag_alignment": "Alignment",
    "diag_loudness_pre": "Loudness delta BEFORE normalization",
    "diag_loudness_residual": "Loudness residual after matching",
    "diag_codec_substituted": "Codec substituted",
    "diag_resample_engine": "Resample engine",
    "diag_canary": "Calibration (canary) trial",
    "diag_run_void": "RUN VOID",
    "diag_run_sealed": "RUN SEALED (stopped early)",
    "diag_note": "NOTE",
    "diag_verdict": "Verdict",
    "diag_no_planned_n": (
        "Verdict: no N pre-registered - p-value is descriptive, not a verdict"
    ),
    "diag_commitment": "X-sequence commitment",
}


def null_depth_text(null_depth_db: Optional[float]) -> str:
    """A perfect null is -inf, which must not print as the biggest number."""
    return describe_depth(null_depth_db)


def canary_report_block(
    engine_block: Optional[Mapping[str, Any]],
    material: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The engine's canary state, annotated with the measured material.

    The engine knows what the LISTENER did with the calibration trial; only the
    preparation step knows how audible the material itself was (silent input
    degrades to silence, so the gate gets disabled instead of voiding runs at
    random). Both shells merge the two here, so the desktop panel, the web
    diagnostics and the JSON export cannot end up phrasing it differently.
    """
    block = dict(engine_block or {})
    check = dict(material or {})
    if check.get("measured_text"):
        block["residual_text"] = check["measured_text"]
        block["residual_db"] = check.get("measured_db")
    if check.get("status") == "not_audible" and block.get("status") == "not_configured":
        block["status"] = "unavailable"
        block["reason"] = check.get("reason", "calibration material is not audible")
    block["material"] = check
    return block


def _canary_line(canary: Dict[str, Any], tr: T) -> str:
    """One line saying what the calibration trial proved, or that it proved
    nothing because it was not configured."""
    status = canary.get("status")
    index = canary.get("trial_index")
    residual = canary.get("residual_text")
    tail = f" (calibration material residual {residual})" if residual else ""

    if status == "not_configured":
        detail = "not configured: this run has no calibration trial"
    elif status == "pending":
        detail = f"PENDING at trial #{index or 1}: must be passed before scored trials"
    elif status == "passed":
        detail = f"PASSED at trial #{index}: playback chain and attention confirmed"
    elif status == "failed":
        detail = (
            f"FAILED at trial #{index}: the deliberately-easy trial was missed, "
            f"so this run is void"
        )
    elif status == "unavailable":
        detail = f"unavailable: {canary.get('reason', 'calibration material is not audible')}"
    else:
        detail = str(status)
    return f"{tr('diag_canary')}: {detail}{tail}"


def _null_gate_line(gate: Dict[str, Any], tr: T) -> str:
    status = gate.get("status")
    measured = gate.get("measured_text", "not measured")
    threshold = gate.get("threshold_db")
    floor = f"{threshold:.1f} dB" if isinstance(threshold, (int, float)) else "?"

    if status == "not_measured":
        detail = "not measured (no pair processed yet)"
    elif status == "not_applicable":
        detail = "not applicable: sides are configured differently, no null expected"
    elif status == "passed":
        detail = f"PASSED: {measured} at or below the {floor} floor"
    elif status == "failed":
        detail = (
            f"FAILED: {measured} is above the {floor} floor - identical "
            f"pipelines are not level/phase matched, this run is void"
        )
    else:
        detail = f"{status}: {measured}"
    return f"{tr('diag_null_gate')}: {detail}"


def _void_reason(canary: Optional[Dict[str, Any]], gate: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why the run is void, if a gate voids it. The p-value does not get a vote."""
    reasons = []
    if canary and canary.get("status") == "failed":
        reasons.append("calibration (canary) trial failed")
    if gate and gate.get("status") == "failed":
        reasons.append("null gate failed (identical pair did not null)")
    return "; ".join(reasons) if reasons else None


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
    canary: Optional[Dict[str, Any]] = None,
    null_gate: Optional[Dict[str, Any]] = None,
    t: Optional[T] = None,
) -> List[str]:
    """The evidence a reader needs to judge a run, not just its outcome.

    Null depth and alignment confidence are measurements of claims the
    pipeline makes about itself. The disclosure block states anything that
    differs from what the user asked for. The two gates -- calibration trial
    and identical-pair null -- state whether the run was allowed to happen at
    all: a failed gate voids the verdict no matter what the p-value says.
    """
    tr: T = t if t is not None else (lambda key: DEFAULT_LABELS.get(key, key))
    lines: List[str] = []

    lines.append(
        f"{tr('diag_null_depth')}: {null_depth_text(null_depth_db)}"
    )
    if null_gate:
        lines.append(_null_gate_line(null_gate, tr))
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

    if canary:
        lines.append(_canary_line(canary, tr))

    void_reason = _void_reason(canary, null_gate)

    if stats:
        if stats.get("planned_trials"):
            # Lifecycle beats arithmetic: a run stopped early is SEALED, not
            # FINAL, whatever its p-value looks like at the partial n.
            if void_reason:
                state = "VOID"
            elif stats.get("sealed"):
                state = "SEALED"
            elif stats.get("is_final"):
                state = "FINAL"
            else:
                state = "PROVISIONAL"
            ci = stats.get("ability_ci95") or [0.0, 1.0]
            lines.append(
                f"{tr('diag_verdict')}: {state} at N={stats['planned_trials']}, "
                f"need >={stats.get('required_correct')} correct "
                f"(ability 95% CI [{ci[0]:.2f}, {ci[1]:.2f}])"
            )
        else:
            lines.append(tr("diag_no_planned_n"))
        if stats.get("sealed"):
            lines.append(
                f"{tr('diag_run_sealed')}: stopped at n="
                f"{stats.get('total_trials', '?')} of the pre-registered "
                "plan: auditable, not a result"
            )
        if void_reason:
            lines.append(f"{tr('diag_run_void')}: {void_reason}")

    if commitment:
        lines.append(f"{tr('diag_commitment')}: {commitment[:16]}...")

    return lines

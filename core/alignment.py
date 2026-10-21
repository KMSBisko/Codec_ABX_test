"""Sample-level alignment of two processed sides.

Extracted into `core/` because the desktop and web builds had INDEPENDENT
alignment implementations that had already diverged:

  * desktop bounded the lag search and trimmed from the front (correct)
  * web searched every lag out to +/-5 s (locks onto periodicity/transient
    spurious peaks) and PADDED the delayed side with zeros at the head, which
    shifts musical content apart -- at the same timeline position the listener
    heard different music, silently invalidating the comparison.

Both shells now call these two functions.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# Codec/decoder latency is bounded well inside 2 s (Opus pre-skip, AAC priming,
# Vorbis lookahead). Searching further only finds spurious peaks.
DEFAULT_MAX_LAG_SAMPLES = 96000  # 2 s @ 48 kHz

# A pair built from identical pipelines MUST null, and "identical" here means
# bit-identical render, so the residual is -inf in the clean case. The floor
# below is what still counts as a null, and it is not loose. Depth for a pure
# level error of x dB is 20*log10(|10^(x/20)-1|), measured on real material:
#   0.001 dB -> -78.8 dB   (passes with 19 dB to spare)
#   0.0087 dB -> -60.0 dB  (the floor itself)
#   0.01  dB -> -58.8 dB   (fails)
#   0.1   dB -> -38.7 dB   (fails)
# Misalignment fails far louder: a 1-sample shift measures -13.9 dB here and
# 137 samples measures +3.8 dB. So -60 dB tolerates float dust in the writer
# and nothing audible.
NULL_GATE_THRESHOLD_DB = -60.0

# A calibration trial is only a calibration trial if the degradation it is
# built from is actually audible. `core.canary` (band-limit to 3 kHz + hard
# clipping) measures -2.7 dB residual on real material -- a gross difference.
# Silent input measures NaN, and identical material measures -inf: both must
# disable the canary rather than hand the listener a coin flip that can void a
# run at random. The floor below is where "too subtle to gate a run" begins.
CANARY_MIN_DEGRADATION_DB = -30.0


def _mono(x: np.ndarray) -> np.ndarray:
    if x.ndim > 1:
        return np.mean(x, axis=1)
    return x


def estimate_lag(
    a: np.ndarray,
    b: np.ndarray,
    max_lag_samples: int = DEFAULT_MAX_LAG_SAMPLES,
    probe_samples: int = 240000,
) -> Tuple[int, float]:
    """Return (lag, confidence).

    Positive lag => B is delayed relative to A.
    Confidence is the normalized cross-correlation peak in [0, 1]; a low value
    means the estimate is not trustworthy and the caller should warn.
    """
    a_m = np.asarray(_mono(a), dtype=np.float64)
    b_m = np.asarray(_mono(b), dtype=np.float64)

    n = min(len(a_m), len(b_m), int(probe_samples))
    if n < 2:
        return 0, 0.0

    a_seg = a_m[:n] - np.mean(a_m[:n])
    b_seg = b_m[:n] - np.mean(b_m[:n])

    corr = np.correlate(b_seg, a_seg, mode="full")
    # mode="full" yields len(b)+len(a)-1 coefficients; the lag axis runs from
    # -(len(a)-1) to +(len(b)-1). Assuming equal lengths breaks whenever the
    # two sides differ by codec delay padding, which is exactly the case here.
    lags = np.arange(len(corr)) - (len(a_seg) - 1)
    window = np.abs(lags) <= int(max_lag_samples)
    if not np.any(window):
        return 0, 0.0

    corr_w = corr[window]
    best = int(np.argmax(corr_w))
    lag = int(lags[window][best])

    norm = float(np.sqrt(np.sum(a_seg ** 2) * np.sum(b_seg ** 2)))
    confidence = float(corr_w[best] / norm) if norm > 0 else 0.0
    return lag, confidence


def align_pair(
    a: np.ndarray, b: np.ndarray, lag: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Shift-then-crop. Never pads with silence.

    Padding the delayed side at the head inserts silence and moves content out
    of register between A and B. Trimming the leading samples of the side that
    starts later keeps identical content at identical timeline positions.
    """
    if lag > 0:
        b = b[lag:]
    elif lag < 0:
        a = a[-lag:]
    min_len = min(len(a), len(b))
    return a[:min_len], b[:min_len]


def null_depth_db(a: np.ndarray, b: np.ndarray) -> float:
    """Residual level, in dB, after subtracting B from A.

    The cheapest hard proof that two sides are level- and phase-matched:
    a deep null is a measurement, not a claim. More negative = better matched;
    a perfect null is -inf, silence is NaN. Kept in core so the desktop and
    web builds report the same number.

    For a pure level error of x dB the depth is 20*log10(|10^(x/20)-1|):
    0.5 dB -> -24.5, 0.1 dB -> -38.7, 0.01 dB -> -58.8.
    """
    a_m = np.asarray(_mono(a), dtype=np.float64)
    b_m = np.asarray(_mono(b), dtype=np.float64)
    n = min(len(a_m), len(b_m))
    if n == 0:
        return float("-inf")
    rms_ref = float(np.sqrt(np.mean(a_m[:n] ** 2)))
    rms_res = float(np.sqrt(np.mean((a_m[:n] - b_m[:n]) ** 2)))
    # Depth is the residual RELATIVE to the reference, so a perfect null is
    # -inf (residual infinitely far below reference), never +inf: a metric
    # where the deepest null prints as the largest number is a trap.
    if rms_ref <= 0.0:
        return float("nan")  # silent reference: ratio undefined
    if rms_res <= 0.0:
        return float("-inf")
    return 20.0 * float(np.log10(rms_res / rms_ref))


# ── gate primitives shared by both shells ────────────────────────────────────
#
# The measured depth above is only a number; these functions turn it into a
# decision, in ONE place, so the desktop and the web cannot disagree about when
# a pair is allowed to be tested.


def describe_depth(depth: Optional[float]) -> str:
    """Canonical wording for a null depth. `core.report` uses this verbatim.

    A perfect null is -inf and must not print as the biggest number there is.
    """
    if depth is None:
        return "not measured"
    if depth == float("-inf"):
        return "perfect (-inf dB)"
    if depth == float("inf") or depth != depth:  # nan
        return "undefined (silence in reference)"
    return f"{depth:.2f} dB"


def json_safe_depth(depth: Optional[float]) -> Dict[str, Any]:
    """Split a depth into JSON-legal fields (Infinity is not valid JSON)."""
    perfect = depth is not None and depth == float("-inf")
    undefined = depth is None or depth == float("inf") or depth != depth
    return {
        "measured_db": None if (perfect or undefined) else float(depth),
        "is_perfect": bool(perfect),
        "is_undefined": bool(undefined),
        "measured_text": describe_depth(depth),
    }


def _stage_pair(stage: Any) -> Tuple[str, int]:
    """One stage as a comparable (codec_id, bitrate) pair.

    Duck-typed on purpose: `core/` must not import the shells' dataclasses, and
    the shells hand over objects, tuples or dicts.
    """
    if isinstance(stage, (tuple, list)):
        codec_id, bitrate = stage[0], (stage[1] if len(stage) > 1 else 0)
    elif isinstance(stage, dict):
        codec_id, bitrate = stage.get("codec_id"), stage.get("bitrate_kbps", 0)
    else:
        codec_id, bitrate = getattr(stage, "codec_id", None), getattr(
            stage, "bitrate_kbps", 0
        )
    return (str(codec_id), int(bitrate or 0))


def _stages_signature(stages: Optional[Sequence[Any]]) -> List[Tuple[str, int]]:
    return [_stage_pair(s) for s in (stages or [])]


def _bandwidth_signature(bandwidth: Any) -> Tuple[bool, Optional[int]]:
    """(enabled, cutoff_hz) normalised so 16000 and 16000 compare equal."""
    if bandwidth is None:
        return (False, None)
    if isinstance(bandwidth, dict):
        enabled = bool(bandwidth.get("enabled", bandwidth.get("enabled", False)))
        hz = bandwidth.get("hz", bandwidth.get("cutoff_hz"))
    elif isinstance(bandwidth, (tuple, list)):
        enabled = bool(bandwidth[0])
        hz = bandwidth[1] if len(bandwidth) > 1 else None
    else:  # pragma: no cover - defensive
        return (False, None)
    return (enabled, int(hz) if (enabled and hz) else None)


def identical_pipeline_config(
    stages_a: Optional[Sequence[Any]],
    stages_b: Optional[Sequence[Any]],
    bandwidth_a: Any = None,
    bandwidth_b: Any = None,
) -> bool:
    """True when both sides run the same chain, i.e. the pair MUST null.

    This is the precondition of the null gate. It compares what the pipeline
    was told to do (codec ids, bitrates, stage order, bandwidth limits) -- not
    the audio, which the gate measures separately.
    """
    sig_a, sig_b = _stages_signature(stages_a), _stages_signature(stages_b)
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return False
    if sig_a != sig_b:
        return False
    return _bandwidth_signature(bandwidth_a) == _bandwidth_signature(bandwidth_b)


def null_gate_decision(
    *,
    null_depth_db: Optional[float],
    expected_identical: bool,
    threshold_db: float = NULL_GATE_THRESHOLD_DB,
) -> Dict[str, Any]:
    """Decide whether the measured residual permits trials to start.

    * sides configured differently -> `not_applicable`: a null is not expected,
      so a shallow residual says nothing and must NOT block a real comparison.
    * sides configured identically -> the residual must be at or below the
      floor. Fails closed: unmeasured/undefined (silent) residuals are `failed`,
      because a pair that must null and cannot be shown to null is not evidence.
    """
    block: Dict[str, Any] = {
        "expected_identical": bool(expected_identical),
        "applicable": bool(expected_identical),
        "threshold_db": float(threshold_db),
    }
    block.update(json_safe_depth(null_depth_db))

    if not expected_identical:
        block["status"] = "not_applicable"
        block["reason"] = (
            "sides are configured differently, so a null is not expected: the "
            "gate cannot judge this pair and does not block it"
        )
        return block

    depth = null_depth_db
    if depth is None or depth == float("inf") or depth != depth:
        block["status"] = "failed"
        block["reason"] = (
            f"a pair built from identical pipelines must null, and the residual "
            f"could not be measured ({block['measured_text']}): refusing to "
            f"start trials (fail closed)"
        )
        return block

    if depth <= float(threshold_db):
        block["status"] = "passed"
        block["reason"] = (
            f"identical pipelines null to {block['measured_text']}, at or below "
            f"the {threshold_db:.1f} dB floor: level and phase matching verified"
        )
        return block

    block["status"] = "failed"
    block["reason"] = (
        f"identical pipelines did NOT null: residual {block['measured_text']} "
        f"is above the {threshold_db:.1f} dB floor, so A and B are not "
        f"level/phase matched at the sample level: refusing to start trials"
    )
    return block


def canary_audio_check(
    *,
    pristine: np.ndarray,
    degraded: np.ndarray,
    min_audible_db: float = CANARY_MIN_DEGRADATION_DB,
) -> Dict[str, Any]:
    """Prove the calibration material is a real, audible difference.

    A canary is only a gate if a listener with a working chain must pass it. If
    the degradation is inaudible (silent input, no-op filter) the two samples
    are a coin flip and would void valid runs, so the canary must be refused
    instead of run.
    """
    depth = null_depth_db(pristine, degraded)
    block: Dict[str, Any] = {"min_audible_db": float(min_audible_db)}
    block.update(json_safe_depth(depth))

    if depth == float("-inf") or depth == float("inf") or depth != depth:
        block["status"] = "not_audible"
        block["usable"] = False
        block["reason"] = (
            f"canary degradation is not measurable ({block['measured_text']}): "
            "the material is silent or the degradation is a no-op, so the "
            "calibration trial would be a coin flip"
        )
        return block

    if depth > float(min_audible_db):
        block["status"] = "usable"
        block["usable"] = True
        block["reason"] = (
            f"calibration pair differs by {block['measured_text']} residual: "
            "a gross, deliberately-easy difference"
        )
        return block

    block["status"] = "not_audible"
    block["usable"] = False
    block["reason"] = (
        f"canary degradation is only {block['measured_text']} residual, deeper "
        f"than the {min_audible_db:.1f} dB audibility floor: too subtle to gate a run"
    )
    return block

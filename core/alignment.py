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

from typing import Tuple

import numpy as np

# Codec/decoder latency is bounded well inside 2 s (Opus pre-skip, AAC priming,
# Vorbis lookahead). Searching further only finds spurious peaks.
DEFAULT_MAX_LAG_SAMPLES = 96000  # 2 s @ 48 kHz


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

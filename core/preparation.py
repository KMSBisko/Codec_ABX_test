"""Shared audio preparation policy. Audio backends belong in the shells.

Channel convention: preserve source mono/stereo, never downmix implicitly.
Unsupported source/decoded channel counts fail rather than changing the test.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

import numpy as np

# No amplitude gate: it changes quiet codec artifacts and is not gain matching.
PREPARATION_ORDER = (
    "probe_source", "select_sample_rate", "working_pcm16", "codec_chains",
    "align_and_crop", "bandwidth_limit", "measure_pre_lufs", "gain_match",
    "common_peak_trim", "write_pcm16", "measure_delivered", "validity_gates",
    "canary_from_delivered",
)

CHANNEL_CONVENTION = "preserve_source_mono_or_stereo"


def checked_audio(data: np.ndarray, channels: int | None = None) -> np.ndarray:
    audio = np.asarray(data)
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise ValueError("Audio preparation supports mono or stereo only")
    if channels is not None and audio.shape[1] != channels:
        raise ValueError("Decoded audio changed the source channel count")
    if not len(audio) or not np.all(np.isfinite(audio)):
        raise ValueError("Audio must contain finite samples")
    return audio


@dataclass
class LoudnessReport:
    """Pre levels, actual gain/common trim, and remeasured delivery levels."""
    pre_lufs_a: float
    pre_lufs_b: float
    post_lufs_a: float
    post_lufs_b: float
    gain_a_db: float
    gain_b_db: float
    global_trim_db: float
    target_lufs: float

    @property
    def pre_normalization_delta_db(self) -> float:
        return abs(self.pre_lufs_a - self.pre_lufs_b)

    @property
    def residual_delta_db(self) -> float:
        return abs(self.post_lufs_a - self.post_lufs_b)

    def to_dict(self) -> dict:
        return {**asdict(self), "pre_delta_db": self.pre_normalization_delta_db,
                "residual_delta_db": self.residual_delta_db}


def select_sample_rate(original: int, mode: str) -> int:
    if original <= 0:
        raise ValueError("Unable to detect input sample rate")
    if mode not in ("native", "force_48k"):
        raise ValueError("Unknown sample-rate mode")
    return original if mode == "native" else 48000


def _measured(data: np.ndarray, measure: Callable[[np.ndarray], float]) -> float:
    value = float(measure(data))
    if not np.isfinite(value):
        raise ValueError("Integrated loudness is not finite (silent or below-gate audio)")
    return value


def normalize_pair(a: np.ndarray, b: np.ndarray, measure: Callable,
                   target_lufs: float = -16.0):
    """One gain policy for both shells; meter supplied by the audio backend.

    Independent constant gains, then a COMMON peak trim. No gain clamp, limiter,
    noise gate or per-side peak trim that could change relative delivered levels.
    Post values must be refreshed after serialization with measure_delivery().
    """
    a = checked_audio(a).astype(np.float64)
    b = checked_audio(b, a.shape[1]).astype(np.float64)
    if not np.isfinite(target_lufs):
        raise ValueError("Loudness target must be finite")
    pre_a, pre_b = _measured(a, measure), _measured(b, measure)
    gain_a, gain_b = target_lufs - pre_a, target_lufs - pre_b
    a = a * 10 ** (gain_a / 20)
    b = b * 10 ** (gain_b / 20)
    # Leave one PCM16 quantum of positive headroom, avoiding writer clipping.
    peak = max(float(np.max(np.abs(a))), float(np.max(np.abs(b))))
    trim_db = max(0.0, float(20 * np.log10(peak / (1 - 1 / 32768))))
    trim = 10 ** (-trim_db / 20)
    a, b = a * trim, b * trim
    report = LoudnessReport(pre_a, pre_b, _measured(a, measure), _measured(b, measure),
                            gain_a, gain_b, trim_db, target_lufs)
    return a, b, report


def measure_delivery(report: LoudnessReport, a: np.ndarray, b: np.ndarray,
                     measure: Callable) -> LoudnessReport:
    return replace(report, post_lufs_a=_measured(a, measure),
                   post_lufs_b=_measured(b, measure))


def sample_peak_dbfs(data: np.ndarray) -> float:
    peak = float(np.max(np.abs(data)))
    return float(20 * np.log10(peak)) if peak else -999.0

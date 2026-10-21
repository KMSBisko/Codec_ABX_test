"""The calibration (canary) material, defined once for both shells.

A canary trial is only a gate if a listener with a working chain MUST pass it,
and it is only citable if the two shells build it the same way. So the
degradation lives here (stdlib + numpy), not in the desktop pipeline or the web
route, where it would drift apart exactly like the trial engines did before
Phase 0.

What the degradation is, and why:

* band-limit to 3 kHz  -- the codec under test is being judged on exactly the
  region this removes, so a listener who can hear it is hearing through the
  chain, not guessing.
* hard clipping at a 10x drive -- gross harmonic distortion, audible on any
  output device that produces sound at all, including a laptop speaker.

Both are deliberate: the trial must be trivially easy, because its job is to
detect a muted/mis-routed output, a broken driver, or a listener who is not
listening. It is measured (`core.alignment.canary_audio_check`) and the measured
residual goes into the export; if the degradation is not audible (silent input)
the canary is disabled rather than left to void runs at random.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

from .alignment import canary_audio_check

CANARY_CUTOFF_HZ = 3000.0
CANARY_DRIVE = 10.0
CANARY_CLIP_LEVEL = 0.45


def _bandlimit(data: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    """FFT low-pass, applied per channel, deterministic.

    Deliberately not the ffmpeg lowpass: the desktop and the web build must
    produce bit-identical calibration material from the same input, and an
    ffmpeg filter chain is one more thing that can differ between installs.
    """
    x = np.asarray(data, dtype=np.float64)
    mono = x.ndim == 1
    if mono:
        x = x[:, None]

    n = x.shape[0]
    spec = np.fft.rfft(x, axis=0)
    freqs = np.fft.rfftfreq(n, d=1.0 / float(sample_rate))
    spec[freqs > float(cutoff_hz)] = 0.0
    out = np.fft.irfft(spec, n=n, axis=0)
    return out[:, 0] if mono else out


def degrade_for_canary(
    data: np.ndarray,
    sample_rate: int,
    cutoff_hz: float = CANARY_CUTOFF_HZ,
    drive: float = CANARY_DRIVE,
    clip_level: float = CANARY_CLIP_LEVEL,
) -> np.ndarray:
    """The deliberately-easy version of a sample: telephone band + hard clip."""
    band = _bandlimit(data, sample_rate, cutoff_hz)
    clipped = np.clip(np.asarray(band, dtype=np.float64) * float(drive),
                      -float(clip_level), float(clip_level))
    src = np.asarray(data)
    return clipped.astype(src.dtype if np.issubdtype(src.dtype, np.floating) else np.float64)


def build_canary_pair(
    pristine: np.ndarray,
    sample_rate: int,
    **kwargs: Any,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """(pristine, degraded, check) -- the pair plus its audibility measurement."""
    degraded = degrade_for_canary(pristine, sample_rate, **kwargs)
    check = canary_audio_check(pristine=pristine, degraded=degraded)
    return pristine, degraded, check

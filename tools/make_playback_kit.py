"""Playback acceptance kit: deterministic test material for a manual ABX run.

Real playback/listening validation is the open product gate. This tool emits
small, fixed, human-inspectable WAVs for every observation the acceptance
checklist (PLAYBACK_ACCEPTANCE.md) needs, from pure numpy -- no ffmpeg, no
network, no randomness beyond one pinned seed.

Design rules:

* Deterministic: the same bytes on every run, so an accepted session is
  reproducible and the file hashes in `manifest.txt` are citable.
* No X leakage by filename: `pair_a.wav` / `pair_b.wav` are neutral. The one
  secret (`ANSWER_KEY.md`) says which side the pre-picked X is and must be
  opened only after sealing or completing a run -- the same rule the app's
  redacted mid-run export enforces.
* The canary degraded file is built with `core.canary.degrade_for_canary`, the
  app's own recipe, so the manual calibration check exercises the real gate.

Usage:  python tools/make_playback_kit.py [out_dir]     (default: playback_kit/)
"""
from __future__ import annotations

import hashlib
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: `import core`
from core.canary import CANARY_CUTOFF_HZ, CANARY_DRIVE, CANARY_CLIP_LEVEL, degrade_for_canary

RATE = 48000
SEED = 20240508  # pinned: the kit, the seed and the key are one artifact


def _tone(freq_hz: float, n: int, amp: float = 0.35) -> np.ndarray:
    t = np.arange(n) / RATE
    return amp * np.sin(2.0 * math.pi * freq_hz * t)


def _write_wav(path: Path, data: np.ndarray) -> None:
    """Float [-1, 1] -> PCM16. `data` is (n,) mono or (n, ch) interleaved-free."""
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    clipped = np.clip(np.rint(x * 32767.0), -32767, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(clipped.tobytes())


def stereo_routing(seconds: float = 2.0) -> np.ndarray:
    """Left ONLY for the first half (440 Hz), right ONLY the second (880 Hz).

    A listener with an inverted/mis-routed chain hears the tone on the wrong
    side. A 3 ms click on both channels at t=0 marks the start, so the channel
    that starts WITH the click is unambiguously both.
    """
    n = int(seconds * RATE)
    half = n // 2
    left = np.zeros(n)
    right = np.zeros(n)
    left[:half] = _tone(440.0, half, 0.35)
    right[half:] = _tone(880.0, n - half, 0.35)
    click = np.zeros(n)
    click[: int(0.003 * RATE)] = 0.8
    left[:] += click
    right[:] += click
    return np.column_stack((left, right))


def mono_identify(seconds: float = 2.0) -> np.ndarray:
    """Four ascending notes (C5 E5 G5 C6) with decay edges and a tick per note.

    A trivially recognisable melody: the "same file in both slots" sanity check
    and the mono -> both-ears routing check.
    """
    notes = [523.25, 659.25, 784.0, 1046.5]
    n = int(seconds * RATE)
    out = np.zeros(n)
    step = n // len(notes)
    for i, f in enumerate(notes):
        seg = _tone(f, step, 0.35)
        env = np.minimum(1.0, np.linspace(0, 40, step))  # fast attack
        env[-int(0.04 * RATE):] *= np.linspace(1, 0, int(0.04 * RATE))
        seg *= env
        seg[: int(0.002 * RATE)] += 0.5  # note-start tick
        out[i * step:(i + 1) * step] += seg
    return out


def _pair_base(n: int) -> np.ndarray:
    """Shared body of the A/B pair: chord + periodic transient, in stereo
    with identical channels (phase-coherent for the in-phase null demo)."""
    t = np.arange(n) / RATE
    body = (
        0.25 * np.sin(2 * math.pi * 220.0 * t)
        + 0.12 * np.sin(2 * math.pi * 440.0 * t)
        + 0.06 * np.sin(2 * math.pi * 110.0 * t)
    )
    for k in range(int(n / RATE * 10) // 12 + 1):  # transient every 1.2 s
        s = int(k * 1.2 * RATE)
        e = min(n, s + int(0.004 * RATE))
        if s < n:
            body[s:e] += 0.30 * np.linspace(1, 0, e - s) * ((np.arange(e - s) % 2) * 2 - 1)
    return np.column_stack((body, body))


def pair(a_side: bool, seconds: float = 3.0) -> np.ndarray:
    """A/B pair: byte-identical base, plus a quiet 12 kHz shimmer (side A) or
    6 kHz shimmer (side B). Audibly distinct brightness; channel-preserving."""
    n = int(seconds * RATE)
    out = _pair_base(n)
    t = np.arange(n) / RATE
    shimmer = 0.02 * np.sin(2 * math.pi * (12000.0 if a_side else 6000.0) * t)
    return out + shimmer[:, None]


def identical_pair_source(seconds: float = 1.5) -> np.ndarray:
    """Source for the null-gate run: both uploaded inputs are copies of this,
    and identical pipelines must deliver a perfect (<= -60 dB) null."""
    n = int(seconds * RATE)
    body = _pair_base(n)
    t = np.arange(n) / RATE
    body += 0.02 * np.sin(2 * math.pi * 9000.0 * t)[:, None]
    return body


def canary_material(seconds: float = 1.5) -> tuple[np.ndarray, np.ndarray]:
    """(source, degraded) with the app's OWN recipe: 3 kHz band-limit, 10x
    drive, hard clip at 0.45 -- a gross difference any working chain reveals."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    body = (
        0.25 * np.sin(2 * math.pi * 330.0 * t)
        + 0.10 * np.sin(2 * math.pi * 1760.0 * t)
        + 0.05 * np.sin(2 * math.pi * 8800.0 * t)
    )
    for k in range(2):  # two broadband transients
        s = int((0.4 + 0.8 * k) * RATE)
        e = min(n, s + int(0.004 * RATE))
        body[s:e] += 0.30 * np.linspace(1, 0, e - s) * ((np.arange(e - s) % 2) * 2 - 1)
    stereo = np.column_stack((body, body))
    degraded = degrade_for_canary(stereo, RATE)
    return stereo, degraded


def loop_seam(seconds: float = 1.2) -> np.ndarray:
    """Click at sample 0, decaying 440 Hz body, last 50 ms faded to silence:
    a loop over the whole file must click exactly once per lap, with no seam
    double-click. A 1.2 s lap makes 5 s easy to count."""
    n = int(seconds * RATE)
    out = _tone(440.0, n, 0.30)
    decay = np.exp(-np.arange(n) / (0.25 * RATE))
    out *= decay
    out[: int(0.003 * RATE)] += 0.8 * np.linspace(1, 0.2, int(0.003 * RATE))
    fade = int(0.05 * RATE)
    out[-fade:] *= np.linspace(1, 0, fade)
    return np.column_stack((out, out))


def manifest_bytes(files: list[Path]) -> str:
    lines = []
    for f in sorted(files):
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{h}  {f.name}")
    return "\n".join(lines) + "\n"


def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    x_side = "a" if np.random.default_rng(SEED).random() < 0.5 else "b"

    written: list[Path] = []

    def emit(name: str, data: np.ndarray) -> Path:
        p = out_dir / name
        _write_wav(p, data)
        written.append(p)
        return p

    emit("stereo_routing.wav", stereo_routing())
    emit("mono_identify.wav", mono_identify())
    emit("pair_a.wav", pair(a_side=True))
    emit("pair_b.wav", pair(a_side=False))
    source = identical_pair_source()
    emit("identical_a.wav", source)
    emit("identical_b.wav", source)  # byte-identical on purpose
    src, deg = canary_material()
    emit("canary_source.wav", src)
    emit("canary_degraded.wav", deg)
    emit("loop_seam.wav", loop_seam())

    (out_dir / "manifest.txt").write_text(
        manifest_bytes(written) + "excluded: ANSWER_KEY.md (seal first)\n", encoding="utf-8"
    )
    (out_dir / "ANSWER_KEY.md").write_text(
        "# Answer key — open ONLY after sealing or completing the run\n\n"
        f"Pre-picked X for the offline triangle (items 1-2 of the checklist): "
        f"**pair_{x_side}.wav** is X.\n\n"
        f"Seed: {SEED} (kit is fully deterministic; regenerating reproduces "
        f"these bytes and this key).\n\n"
        "Why sealed-first: the app's own mid-run export is redacted for the "
        "same reason (core/abx.py invariant 7). Inspecting this key before "
        "finishing the run voids the blind the kit exists to verify.\n",
        encoding="utf-8",
    )
    return out_dir


def main() -> None:
    import sys

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("playback_kit")
    build(out_dir)
    names = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    print(f"Wrote {len(names)} files to {out_dir}:")
    for n in names:
        print(f"  {n}")


if __name__ == "__main__":
    main()

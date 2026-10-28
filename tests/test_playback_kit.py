"""Playback acceptance kit: the material must be deterministic and provably
say what the checklist claims -- judged on the written PCM16 bytes, never on
the generator's floats.

The kit exists because real listening has never been performed; if its inputs
were not deterministic and channel-checked, an accepted session would not be
reproducible. These tests are the automated half of that promise; the ear half
lives in PLAYBACK_ACCEPTANCE.md.
"""
import hashlib
import wave

import numpy as np
import pytest

from tools import make_playback_kit as kit


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return kit.build(tmp_path_factory.mktemp("kit"))


def _read(path):
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        ch = w.getnchannels()
        return data.reshape(-1, ch) / 32767.0


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def _tone_energy(x, freq_hz):
    """Goertzel: energy at one frequency. stdlib-of-numpy, no scipy."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    w = 2.0 * np.pi * freq_hz / kit.RATE
    coeff = 2.0 * np.cos(w)
    s1 = s2 = 0.0
    for v in x:  # kit files are short; clarity over speed
        s0 = v + coeff * s1 - s2
        s2, s1 = s1, s0
    return (s1 * s1 + s2 * s2 - coeff * s1 * s2) / (n * n)


def test_every_wav_is_48k_pcm16_with_the_declared_channels(built):
    expected = {
        "stereo_routing.wav": 2, "mono_identify.wav": 1,
        "pair_a.wav": 2, "pair_b.wav": 2,
        "identical_a.wav": 2, "identical_b.wav": 2,
        "canary_source.wav": 2, "canary_degraded.wav": 2,
        "loop_seam.wav": 2,
    }
    for name, channels in expected.items():
        p = built / name
        assert p.exists(), name
        with wave.open(str(p), "rb") as w:
            assert w.getnchannels() == channels, name


def test_kit_is_byte_deterministic(tmp_path, built):
    again = kit.build(tmp_path / "again")
    first = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in built.iterdir() if p.is_file()}
    second = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in again.iterdir() if p.is_file()}
    assert first == second, "regenerating the kit must reproduce every byte"


def test_stereo_routing_is_left_then_right_with_a_shared_click(built):
    data = _read(built / "stereo_routing.wav")
    half = len(data) // 2
    after = int(0.01 * kit.RATE)  # past the 3 ms shared click
    assert _rms(data[after:half, 0]) > 10 * (_rms(data[after:half, 1]) + 1e-9)
    assert _rms(data[half:, 1]) > 10 * (_rms(data[half:, 0]) + 1e-9)
    start = data[: int(0.002 * kit.RATE)]
    assert _rms(start[:, 0]) > 0.1 and _rms(start[:, 1]) > 0.1, (
        "t=0 click must hit BOTH channels, else the side of the click is a"
        " routing artifact, not a marker"
    )


def test_pair_sides_are_audibly_distinct_only_in_the_shimmer_band(built):
    a = _read(built / "pair_a.wav")
    b = _read(built / "pair_b.wav")
    assert a.shape == b.shape
    assert _tone_energy(a[:, 0], 12000.0) > 100 * _tone_energy(b[:, 0], 12000.0)
    assert _tone_energy(b[:, 0], 6000.0) > 100 * _tone_energy(a[:, 0], 6000.0)
    # The 220 Hz body is identical up to PCM16 dither: the difference is
    # brightness, not level or timing, so A/B/X switching isolates exactly one
    # audible attribute.
    ea, eb = _tone_energy(a[:, 0], 220.0), _tone_energy(b[:, 0], 220.0)
    assert abs(ea - eb) < 1e-5 * ea
    # channels are in-phase copies: the pair doubles as an in-phase check
    assert np.max(np.abs(a[:, 0] - a[:, 1])) < 1e-9


def test_identical_pair_is_byte_identical(built):
    ha = hashlib.sha256((built / "identical_a.wav").read_bytes()).hexdigest()
    hb = hashlib.sha256((built / "identical_b.wav").read_bytes()).hexdigest()
    assert ha == hb, "the null-gate input pair must be byte-identical"


def test_canary_degraded_uses_the_app_recipe(built):
    src = _read(built / "canary_source.wav")
    deg = _read(built / "canary_degraded.wav")
    assert src.shape == deg.shape
    assert not np.array_equal(src, deg)
    # band-limited to 3 kHz: 8.8 kHz content is (near) gone
    assert _tone_energy(deg[:, 0], 8800.0) < 0.01 * _tone_energy(src[:, 0], 8800.0)
    # hard clip at 0.45
    assert np.max(np.abs(deg)) <= 0.45 + 1e-3
    # gross, not subtle: residual-scale difference against the source
    assert _rms(src - deg) > 0.1 * _rms(src)


def test_loop_file_starts_with_a_click_and_ends_silent(built):
    data = _read(built / "loop_seam.wav")
    assert abs(data[0, 0]) > 0.5, "loop start must be an unmistakable click"
    assert abs(data[-1, 0]) < 0.01 * np.max(np.abs(data)), (
        "loop end must fade to silence, so a seam shows up as ONE click/lap"
    )


def test_answer_key_exists_and_is_gated_by_sealing(built):
    key = (built / "ANSWER_KEY.md").read_text(encoding="utf-8")
    assert "ONLY after sealing" in key
    assert "**pair_a.wav** is X." in key or "**pair_b.wav** is X." in key
    manifest = (built / "manifest.txt").read_text(encoding="utf-8")
    wavs = sorted(p.name for p in built.iterdir() if p.suffix == ".wav")
    for name in wavs:
        assert name in manifest, name
    assert "ANSWER_KEY.md" not in manifest.split("excluded")[0], (
        "the manifest must not hash the key next to the material it guards"
    )

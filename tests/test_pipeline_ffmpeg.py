"""Real codec pipeline. Requires ffmpeg; skipped cleanly when absent.

The headline Phase 0 behaviour under test: a requested codec that this ffmpeg
build cannot encode is a HARD ERROR, never a silent AAC stand-in -- and every
substitution is present in the exported disclosure block.
"""
import math
import shutil
import wave

import pytest

ffmpeg = shutil.which("ffmpeg")

# app.audio_pipeline pulls in pyloudnorm/soundfile, which are desktop-only
# deps. Skip the whole module rather than erroring at collection time on a
# machine (like CI's core job) that only installed requirements-dev.txt.
_deps = pytest.importorskip("pyloudnorm", reason="desktop audio deps required")
pytest.importorskip("soundfile")
pytest.importorskip("numpy")

_HAS_FFMPEG = ffmpeg is not None
pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed"),
]


def _pipeline_cls():
    from app.audio_pipeline import AudioPipeline, CodecUnavailableError

    return AudioPipeline, CodecUnavailableError


def _tone(path, seconds=2.0, rate=48000, freq=440.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            v = int(0.25 * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += v.to_bytes(2, "little", signed=True) * 2
        w.writeframes(bytes(frames))


@pytest.fixture()
def source(tmp_path):
    wav = tmp_path / "src.wav"
    _tone(wav)
    return wav


@pytest.fixture()
def pipeline(tmp_path):
    AudioPipeline, _ = _pipeline_cls()
    return AudioPipeline(
        ffmpeg_bin=ffmpeg or "ffmpeg",
        ffprobe_bin=shutil.which("ffprobe") or "ffprobe",
    )


def _noop(pipeline):
    from app.models import PipelineStageConfig

    return [PipelineStageConfig("noop_passthrough", 0)]


def test_lossless_vs_lossless_nulls_deeply(pipeline, source, tmp_path):
    """A/B with identical processing must null: proves the pipeline is
    level- and sample-matched, as a measurement rather than a claim."""
    from app.models import PipelineStageConfig  # noqa: F401

    session = pipeline.prepare_session(
        input_file=str(source),
        work_dir=str(tmp_path / "wd"),
        stages_a=_noop(pipeline),
        stages_b=_noop(pipeline),
    )
    assert session.validation.null_depth_db < -80.0
    assert session.validation.alignment_confidence > 0.9
    assert session.validation.loudness_diff_db < 0.01


def test_disclosure_block_is_present_and_honest(pipeline, source, tmp_path):
    session = pipeline.prepare_session(
        input_file=str(source),
        work_dir=str(tmp_path / "wd2"),
        stages_a=_noop(pipeline),
        stages_b=_noop(pipeline),
    )
    d = session.disclosure
    assert "codec_fallbacks" in d and "resample" in d
    assert d["codec_fallbacks"] == [], "noop requested, nothing substituted"
    assert d["resample"]["engine_used"] in ("soxr", "default")


def test_strict_mode_refuses_silent_codec_substitution(pipeline, source, tmp_path):
    """aptX/LDAC substitution must raise, not quietly encode AAC."""
    from app.models import PipelineStageConfig

    _, CodecUnavailableError = _pipeline_cls()
    encoders = subprocess_encoders(pipeline)
    missing = [
        codec
        for codec, enc in (("aptx", "aptx"), ("ldac", "libldac"))
        if enc not in encoders
    ]
    if not missing:
        pytest.skip("this ffmpeg build has every real codec encoder")

    codec_id = missing[0]
    stages = [PipelineStageConfig(codec_id, 352 if codec_id == "aptx" else 660)]
    with pytest.raises(CodecUnavailableError) as exc:
        pipeline.prepare_session(
            input_file=str(source),
            work_dir=str(tmp_path / "wd3"),
            stages_a=stages,
            stages_b=_noop(pipeline),
        )
    msg = str(exc.value)
    assert "Refusing to substitute" in msg
    assert "ffmpeg" in msg


def test_opt_in_substitution_is_recorded(pipeline, source, tmp_path):
    from app.models import PipelineStageConfig

    encoders = subprocess_encoders(pipeline)
    if "libldac" in encoders:
        pytest.skip("libldac present; substitution path not reachable")

    AudioPipeline, _ = _pipeline_cls()
    permissive = AudioPipeline(
        ffmpeg_bin=ffmpeg or "ffmpeg",
        ffprobe_bin=shutil.which("ffprobe") or "ffprobe",
        strict_codecs=True,
        allow_codec_substitution=True,
    )
    session = permissive.prepare_session(
        input_file=str(source),
        work_dir=str(tmp_path / "wd4"),
        stages_a=[PipelineStageConfig("ldac", 660)],
        stages_b=_noop(permissive),
    )
    fb = session.disclosure["codec_fallbacks"]
    assert fb, "substitution happened, so it must be disclosed"
    assert fb[0]["requested_codec_id"] == "ldac"
    assert fb[0]["actual_encoder"], "the report must say what really encoded"


def subprocess_encoders(pipeline):
    import subprocess

    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True
    ).stdout
    return {tok for line in out.splitlines() for tok in line.split()}

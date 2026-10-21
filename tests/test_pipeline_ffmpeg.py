"""Real codec pipeline against a real ffmpeg. Skips cleanly when absent.

The headline Phase 0 behaviour: a requested codec this ffmpeg build cannot
encode is a HARD ERROR, never a silent AAC stand-in -- and any opt-in
substitution is present in the exported disclosure block.
"""
import json
import math
import shutil
import wave

import pytest

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")

# app.audio_pipeline pulls desktop-only deps; skip rather than error at
# collection on a machine that only installed requirements-dev.txt.
pytest.importorskip("pyloudnorm", reason="desktop audio deps required")
pytest.importorskip("soundfile")
numpy = pytest.importorskip("numpy")

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(not (ffmpeg and ffprobe), reason="ffmpeg/ffprobe not installed"),
]


def _pipeline_cls():
    from app.audio_pipeline import AudioPipeline, CodecUnavailableError

    return AudioPipeline, CodecUnavailableError


def _tone(path, seconds=3.0, rate=48000, freq=440.0):
    """Two channels, a sustained tone plus a sharp broadband transient so the
    alignment stage has real material to lock onto."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        n = int(seconds * rate)
        for i in range(n):
            v = int(
                0.22
                * 32767
                * (
                    math.sin(2 * math.pi * freq * i / rate)
                    + 0.35 * math.sin(2 * math.pi * 5200 * i / rate)
                )
            )
            v = max(-32767, min(32767, v))
            if 4000 <= i < 4060:  # transient
                v = 30000 if i % 2 else -30000
            b = int(v).to_bytes(2, "little", signed=True)
            frames += b * 2
        w.writeframes(bytes(frames))


@pytest.fixture()
def source(tmp_path):
    wav = tmp_path / "src.wav"
    _tone(wav)
    return wav


@pytest.fixture()
def make_pipeline():
    AudioPipeline, _ = _pipeline_cls()

    def _make(**kw):
        return AudioPipeline(
            ffmpeg_bin=ffmpeg, ffprobe_bin=ffprobe, **kw
        )

    return _make


def _run(pipeline, source, tmp_path, a, b, **kw):
    from app.models import ProcessingMode, SampleRateMode

    return pipeline.prepare_session(
        input_path=str(source),
        codec_a_id=a[0],
        bitrate_a_kbps=a[1],
        codec_b_id=b[0],
        bitrate_b_kbps=b[1],
        mode=SampleRateMode.NATIVE,
        work_dir=str(tmp_path / f"wd_{id(pipeline) % 99999}_{abs(hash((a, b, tuple(sorted(kw))))) % 9999}"),
        processing_mode=ProcessingMode.SINGLE_STAGE,
        **kw,
    )


def test_lossless_vs_lossless_nulls_deeply(make_pipeline, source, tmp_path):
    """Identical processing must null. This is the measurement that the
    pipeline is level- and sample-matched -- not a claim about it."""
    session, arr_a, arr_b = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
    )
    v = session.validation
    assert v.sample_rate_equal and v.lengths_equal
    assert v.alignment_lag_samples == 0
    assert v.alignment_confidence > 0.9
    assert v.loudness_diff_db < 0.01
    # identical signal -> -inf; guard against float dust in the writer
    assert v.null_depth_db == float("-inf") or v.null_depth_db < -140.0


def test_bandwidth_limit_both_sides_equal_still_nulls(make_pipeline, source, tmp_path):
    """Applying the same filter to both sides must not break the null:
    a sanity check that the shared filter path is deterministic."""
    session, _, _ = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
        bandwidth_limit_a_enabled=True, bandwidth_limit_a_hz=16000,
        bandwidth_limit_b_enabled=True, bandwidth_limit_b_hz=16000,
    )
    assert session.validation.null_depth_db == float("-inf") or (
        session.validation.null_depth_db < -140.0
    )


def test_opus_vs_lossless_produces_a_shallow_null(make_pipeline, source, tmp_path):
    """Lossy vs lossless must NOT null: proves the null-depth metric is a
    real detector and not a metric that always reports a deep null."""
    session, _, _ = _run(
        make_pipeline(), source, tmp_path,
        (_lossy_codec_id(), 128), ("noop_passthrough", 0),
    )
    depth = session.validation.null_depth_db
    assert depth > -60.0, f"lossy vs lossless nulled too deep: {depth}"
    assert session.validation.loudness_diff_db < 0.1, "loudness match must hold"
    assert session.validation.alignment_confidence > 0.5


def test_disclosure_block_is_present_and_honest(make_pipeline, source, tmp_path):
    session, _, _ = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
    )
    d = session.disclosure
    assert set(d) >= {"codec_fallbacks", "resample", "codec_substitution_allowed"}
    assert d["codec_fallbacks"] == [], "noop requested, nothing substituted"
    assert d["resample"]["engine_used"] in ("soxr", "default")
    assert d["codec_substitution_allowed"] is False


def test_strict_mode_refuses_silent_codec_substitution(make_pipeline, source, tmp_path):
    """The #1 credibility fix: requesting a codec this build cannot encode
    must raise, not quietly encode AAC."""
    _, CodecUnavailableError = _pipeline_cls()
    if "libldac" in _encoders():
        pytest.skip("this ffmpeg build has libldac; substitution unreachable")

    with pytest.raises(CodecUnavailableError) as exc:
        _run(make_pipeline(), source, tmp_path, ("ldac", 660), ("noop_passthrough", 0))

    msg = str(exc.value)
    assert "Refusing to substitute" in msg
    assert "libldac" in msg, "must name the encoder that is missing"
    assert "ldac" in msg.lower()


def test_opt_in_substitution_is_recorded_in_disclosure(make_pipeline, source, tmp_path):
    """If the user explicitly opts into a proxy, the exported report must say
    what really encoded -- the old code showed it in the UI only."""
    if "libldac" in _encoders():
        pytest.skip("libldac present; substitution path not reachable")

    permissive = make_pipeline(allow_codec_substitution=True)
    session, _, _ = _run(
        permissive, source, tmp_path, ("ldac", 660), ("noop_passthrough", 0)
    )
    fb = session.disclosure["codec_fallbacks"]
    assert fb, "a substitution happened, so it must be disclosed"
    assert fb[0]["requested_codec_id"] == "ldac"
    assert fb[0]["actual_encoder"], "the report must name the real encoder"
    assert fb[0]["reason"] == "encoder_unavailable_in_ffmpeg_build"


def test_cascaded_pipeline_runs_and_reports(make_pipeline, source, tmp_path):
    """Multi-stage chaining is the app's headline feature; it must survive
    the refactor and report its disclosure."""
    from app.models import PipelineStageConfig, ProcessingMode

    codec = _lossy_codec_id()
    pipeline = make_pipeline()
    session, _, _ = pipeline.prepare_session(
        input_path=str(source),
        codec_a_id=codec,
        bitrate_a_kbps=128,
        codec_b_id=codec,
        bitrate_b_kbps=64,
        mode=__import__("app.models", fromlist=["SampleRateMode"]).SampleRateMode.NATIVE,
        work_dir=str(tmp_path / "wd_cascade"),
        processing_mode=ProcessingMode.CASCADED_PIPELINE,
        pipeline_stages_a=[
            PipelineStageConfig(codec, 128),
            PipelineStageConfig(codec, 128),
        ],
        pipeline_stages_b=[
            PipelineStageConfig(codec, 64),
            PipelineStageConfig(codec, 64),
        ],
    )
    assert session.pipeline_stage_count_a == 2
    assert session.pipeline_stage_count_b == 2
    assert session.validation.loudness_diff_db < 0.1


def _lossy_codec_id():
    """Catalog codec_id (not the ffmpeg encoder name) for a lossy codec this
    build can encode."""
    return "opus" if "libopus" in _encoders() else "aac"


def _encoders():
    import subprocess

    if not ffmpeg:
        return set()
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True
    ).stdout
    return {line.split()[1] for line in out.splitlines() if line.startswith(" A")}


# ── web shell, same pipeline, same evidence ────────────────────────────────


def test_web_pipeline_reports_the_same_evidence(tmp_path):
    """The web build must report the same validity facts as the desktop:
    perfect null for identical processing, confidence, honest loudness."""
    import io
    import time

    import web.app as m

    src = tmp_path / "web_src.wav"
    _tone(src, seconds=2.0)

    client = m.app.test_client()
    with open(src, "rb") as fh:
        resp = client.post(
            "/api/upload",
            data={"file": (io.BytesIO(fh.read()), "src.wav")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200, resp.data

    client.post(
        "/api/config",
        json={
            "sample_rate_mode": "native",
            "label_mapping_mode": "fixed",
            "planned_trials": 16,
            "stages_a": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
            "stages_b": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
            "bandwidth_limit_a_enabled": False,
            "bandwidth_limit_b_enabled": False,
        },
    )
    assert client.post("/api/preprocess", json={}).status_code == 200

    for _ in range(120):
        status = json.loads(client.get("/api/preprocess/status").data)
        if status.get("ready") or status.get("phase") == "error":
            break
        time.sleep(0.25)
    assert status.get("ready"), f"preprocess did not finish: {status}"

    meta = json.loads(client.get("/api/diagnostics").data)
    md = meta["metadata"]
    assert md["alignment_confidence"] > 0.9
    assert md["null_depth_is_perfect"] is True, "identical sides must null"
    assert md["loudness_is_pre_normalization"] is True

    text = "\n".join(meta["validity_lines"])
    assert "perfect (-inf dB)" in text
    assert "PROVISIONAL at N=16" in text
    assert "Codec substituted: none" in text


def test_web_pipeline_reports_a_real_codec(tmp_path):
    """Lossy vs lossless through the web build: shallow null, level matched."""
    import io
    import time

    import web.app as m

    src = tmp_path / "web_src2.wav"
    _tone(src, seconds=2.0)
    client = m.app.test_client()
    with open(src, "rb") as fh:
        client.post(
            "/api/upload",
            data={"file": (io.BytesIO(fh.read()), "src.wav")},
            content_type="multipart/form-data",
        )
    codec = _lossy_codec_id()
    client.post(
        "/api/config",
        json={
            "sample_rate_mode": "native",
            "label_mapping_mode": "blind_random",
            "planned_trials": 8,
            "stages_a": [{"codec_id": codec, "bitrate_kbps": 64}],
            "stages_b": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
            "bandwidth_limit_a_enabled": False,
            "bandwidth_limit_b_enabled": False,
        },
    )
    client.post("/api/preprocess", json={})
    for _ in range(120):
        status = json.loads(client.get("/api/preprocess/status").data)
        if status.get("ready") or status.get("phase") == "error":
            break
        time.sleep(0.25)
    assert status.get("ready"), f"preprocess did not finish: {status}"

    meta = json.loads(client.get("/api/diagnostics").data)
    md = meta["metadata"]
    assert md["null_depth_is_perfect"] is False, "lossy vs lossless must not null"
    assert md["null_depth_db"] is not None and md["null_depth_db"] > -60.0
    assert md["alignment_confidence"] > 0.5

    export = json.loads(client.get("/api/export/json").data)
    assert export["session"]["verifiability"]["verifiable"] is True
    assert len(export["validity_lines"]) >= 4

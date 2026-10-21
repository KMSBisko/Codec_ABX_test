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


# ── Phase 1: the two validity gates on real audio ────────────────────────────


def _wav_bytes_to_array(data: bytes):
    """Decode served audio and downmix to mono: the same convention the web
    pipeline uses for every measurement it reports."""
    import io

    import numpy as np
    import soundfile as sf

    arr, sr = sf.read(io.BytesIO(data), dtype="float32")
    if arr.ndim > 1:
        arr = np.mean(arr, axis=-1)
    return arr, sr


def test_canary_pair_is_real_audio_and_grossly_degraded(make_pipeline, source, tmp_path):
    """The gate is only a gate if the material is audible and is the delivered
    Side A: measured on the files that will actually be played."""
    from app.audio_pipeline import AudioPipeline
    from core.alignment import CANARY_MIN_DEGRADATION_DB, null_depth_db

    session, arr_a, _ = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
        canary_enabled=True,
    )

    check = session.canary
    assert check["usable"] is True, check
    assert check["measured_db"] > CANARY_MIN_DEGRADATION_DB, check

    pristine, sr = AudioPipeline.load_audio(session.canary_pristine_path)
    degraded, _ = AudioPipeline.load_audio(session.canary_degraded_path)
    assert sr == session.target_sample_rate

    # Pristine must BE the delivered Side A, not a second normalized copy the
    # listener never hears. The desktop writes PCM_16, so "the same signal" is
    # bounded by 16-bit quantization: measured -76.1 dB residual here, against
    # the -30 dB audibility floor this gate uses.
    n = min(len(pristine), len(arr_a))
    to_delivered = null_depth_db(pristine[:n], arr_a[:n])
    assert to_delivered < -60.0, f"pristine is not the delivered A: {to_delivered}"

    # The pair is audibly different, by a wide margin.
    m = min(len(pristine), len(degraded))
    on_disk = null_depth_db(pristine[:m], degraded[:m])
    assert on_disk > CANARY_MIN_DEGRADATION_DB, on_disk

    # And the codec pair itself is untouched by building the calibration audio.
    depth = session.validation.null_depth_db
    assert depth == float("-inf") or depth < -140.0, depth


def test_null_gate_passes_identical_and_abstains_on_a_real_codec(
    make_pipeline, source, tmp_path
):
    """The gate's two honest outcomes on measured audio: identical pipelines
    PASS (perfect null), a real comparison is NOT_APPLICABLE - never blocked."""
    from app.abx_engine import ABXEngine

    same, _, _ = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
    )
    assert same.null_gate["status"] == "passed", same.null_gate
    assert same.null_gate["applicable"] is True
    assert same.null_gate["is_perfect"] is True

    engine = ABXEngine(planned_trials=8)
    engine.set_null_gate(same.null_gate)
    assert engine.gate_open is True

    pair, _, _ = _run(
        make_pipeline(), source, tmp_path,
        (_lossy_codec_id(), 128), ("noop_passthrough", 0),
    )
    assert pair.null_gate["status"] == "not_applicable", pair.null_gate
    other = ABXEngine(planned_trials=8)
    other.set_null_gate(pair.null_gate)
    assert other.gate_open is True, "a real comparison must never be blocked"
    assert "did NOT null" not in " | ".join(pair.validation.notes)


def test_canary_off_by_default_leaves_the_desktop_session_unchanged(
    make_pipeline, source, tmp_path
):
    """No behaviour change for anyone who does not turn the gate on: the fields
    exist and say 'not requested', and no calibration files are written."""
    import os

    session, _, _ = _run(
        make_pipeline(), source, tmp_path,
        ("noop_passthrough", 0), ("noop_passthrough", 0),
    )
    assert session.canary["status"] == "not_configured"
    assert session.canary_pristine_path is None
    assert session.canary_degraded_path is None
    assert session.null_gate["status"] == "passed"
    assert not [p for p in os.listdir(os.path.dirname(session.track_a.pcm_path))
                if p.startswith("canary_")]


def test_web_pipeline_gates_the_session_with_real_audio(tmp_path):
    """The web build: preprocessing measures both gates, trial 1 is the
    calibration trial, and the audio it plays is the calibration pair."""
    import io
    import time

    import web.app as m

    src = tmp_path / "web_canary.wav"
    _tone(src, seconds=2.0)
    client = m.app.test_client()
    try:
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
                "planned_trials": 4,
                "canary_enabled": True,
                "stages_a": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
                "stages_b": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
                "bandwidth_limit_a_enabled": False,
                "bandwidth_limit_b_enabled": False,
            },
        )
        assert client.post("/api/preprocess", json={}).status_code == 200

        status = {}
        for _ in range(160):
            status = json.loads(client.get("/api/preprocess/status").data)
            if status.get("ready") or status.get("phase") == "error":
                break
            time.sleep(0.25)
        assert status.get("ready"), f"preprocess did not finish: {status}"

        diag = json.loads(client.get("/api/diagnostics").data)
        md = diag["metadata"]
        assert md["canary_requested"] is True
        assert md["canary"]["usable"] is True, md["canary"]
        assert md["null_gate"]["status"] == "passed", md["null_gate"]
        assert diag["gates"]["open"] is True
        assert diag["canary"]["status"] == "pending"
        text = "\n".join(diag["validity_lines"])
        assert "calibration material residual" in text, text
        assert "PENDING at trial #1" in text

        start = json.loads(client.post("/api/trial/start").data)
        stats = json.loads(client.get("/api/trial/stats").data)
        assert stats["current_trial_kind"] == "canary"

        a_bytes = client.get("/api/audio/a").data
        b_bytes = client.get("/api/audio/b").data
        x_bytes = client.get(f"/api/audio/x?token={start['token']}").data
        assert a_bytes and b_bytes and x_bytes in (a_bytes, b_bytes)
        assert a_bytes != b_bytes, "a calibration pair with identical sides is a coin flip"

        from core.alignment import CANARY_MIN_DEGRADATION_DB, null_depth_db

        arr_a, _ = _wav_bytes_to_array(a_bytes)
        arr_b, _ = _wav_bytes_to_array(b_bytes)
        n = min(len(arr_a), len(arr_b))
        heard = null_depth_db(arr_a[:n], arr_b[:n])
        assert heard > CANARY_MIN_DEGRADATION_DB, f"calibration pair too subtle: {heard}"

        with m.session.lock:
            x_source = m.session.engine._pending.x_source
            mapping = m.session.engine._pending.display_mapping
        label = next(lbl for lbl, src_side in mapping.items() if src_side == x_source)
        result = json.loads(
            client.post("/api/trial/answer", json={"answer": label}).data
        )
        assert result["trial"]["kind"] == "canary"
        assert result["stats"]["canary_status"] == "passed"

        # After calibration the browser hears the codec pair again.
        with m.session.lock:
            delivered_a = open(m.session.audio_a_path, "rb").read()
        assert client.get("/api/audio/a").data == delivered_a

        export = json.loads(client.get("/api/export/json").data)
        assert export["session"]["canary"]["status"] == "passed"
        assert export["session"]["null_gate"]["status"] == "passed"
        assert export["session"]["run_valid"] is True
        assert export["trials"][0]["kind"] == "canary"
    finally:
        with m.session.lock:
            m.session.canary_enabled = False
            m.session.canary_available = True
            m.session.canary_check = {}
            m.session.canary_pristine_path = None
            m.session.canary_degraded_path = None
            m.session.null_gate = {}
            m.session.reset_trials()

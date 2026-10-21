"""Preparation contracts judged on delivered PCM, not UI/backend claims."""
import io
import shutil

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")
pyln = pytest.importorskip("pyloudnorm")
pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                       reason="ffmpeg/ffprobe required"),
]


@pytest.fixture
def web(monkeypatch):
    import web.app as m

    # Isolate ALL singleton state (paths, gates, frozen plans, identity included).
    monkeypatch.setattr(m, "session", m.SessionState())
    return m


def source(tmp_path, channels=2, rate=48000, suffix="wav", quiet=False):
    t = np.arange(int(rate * 0.6)) / rate
    rng = np.random.default_rng(73)
    left = .08 * np.sin(2 * np.pi * 997 * t) + .015 * rng.normal(size=len(t))
    right = .05 * np.sin(2 * np.pi * 5300 * t) - .4 * left
    data = left[:, None] if channels == 1 else np.column_stack((left, right))
    if quiet:
        data[-rate // 5:] *= .001
    path = tmp_path / f"source.{suffix}"
    sf.write(path, data, rate, subtype="PCM_16")
    return path


def configure_web(m, path, **config):
    client = m.app.test_client()
    assert client.post("/api/trial/reset", json={}).status_code == 200
    response = client.post("/api/upload", data={
        "file": (io.BytesIO(path.read_bytes()), path.name)
    }, content_type="multipart/form-data")
    assert response.status_code == 200, response.data
    settings = {
        "planned_trials": 4, "sample_rate_mode": "native",
        "label_mapping_mode": "fixed", "canary_enabled": True,
        "stages_a": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
        "stages_b": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
        "bandwidth_limit_a_enabled": False, "bandwidth_limit_b_enabled": False,
    }
    settings.update(config)
    response = client.post("/api/config", json=settings)
    assert response.status_code == 200, response.data
    return client


def prepare_web(m, path, **config):
    client = configure_web(m, path, **config)
    # Exercise the real worker synchronously: no server, threads or polling.
    m._run_preprocess()
    assert m.session.preprocess_ready, m.session.preprocess_error
    return client


def prepare_desktop(path, tmp_path, **kwargs):
    from app.audio_pipeline import AudioPipeline
    from app.models import SampleRateMode

    return AudioPipeline().prepare_session(
        str(path), "noop_passthrough", 0, "noop_passthrough", 0,
        SampleRateMode(kwargs.pop("mode", "native")), str(tmp_path / "desktop"),
        canary_enabled=True, **kwargs,
    )


@pytest.mark.parametrize("channels", [1, 2])
def test_channels_and_canary_preserve_source(web, tmp_path, channels):
    path = source(tmp_path, channels=channels)
    client = prepare_web(web, path)
    desktop, _, _ = prepare_desktop(path, tmp_path)
    for label, disk_path in (
        ("a", desktop.track_a.pcm_path), ("b", desktop.track_b.pcm_path),
    ):
        delivered, sr = sf.read(io.BytesIO(client.get(f"/api/audio/{label}").data),
                                always_2d=True)
        reference, desktop_sr = sf.read(disk_path, always_2d=True)
        assert delivered.shape == reference.shape, "web changed the channel convention"
        assert delivered.shape[1] == channels and sr == desktop_sr
    for p in (web.session.canary_pristine_path, web.session.canary_degraded_path):
        assert sf.info(p).channels == channels
    meta = client.get("/api/export/json").get_json()["session"]
    assert meta["channels"] == channels
    assert meta["channel_convention"] == "preserve_source_mono_or_stereo"
    assert desktop.disclosure["channel_convention"] == meta["channel_convention"]


def perturb_delivery(monkeypatch):
    """A non-zero delivered residual proves we remeasure, not stamp a target."""
    original = sf.write

    def write(path, data, *args, **kwargs):
        if str(path).endswith(("b_normalized.wav", "track_b_normalized.wav")):
            data = data * (10 ** (-.125 / 20))
        return original(path, data, *args, **kwargs)

    monkeypatch.setattr(sf, "write", write)


def test_web_loudness_report_measures_delivered_pcm(web, tmp_path, monkeypatch):
    path = source(tmp_path)
    perturb_delivery(monkeypatch)
    client = prepare_web(web, path)
    meta = client.get("/api/export/json").get_json()["session"]
    report = meta["loudness"]
    assert report == web.session.session_metadata["loudness"]
    meter = pyln.Meter(48000)
    for side in ("a", "b"):
        data, _ = sf.read(getattr(web.session, f"audio_{side}_path"),
                          dtype="float32", always_2d=True)
        assert report[f"post_lufs_{side}"] == pytest.approx(
            meter.integrated_loudness(data), abs=1e-7)
    assert report["residual_delta_db"] == pytest.approx(.125, abs=.002)
    assert report["pre_delta_db"] < 1e-8
    assert report["gain_a_db"] == pytest.approx(-16 - report["pre_lufs_a"])
    assert report["global_trim_db"] >= 0
    text = "\n".join(client.get("/api/diagnostics").get_json()["validity_lines"])
    assert f'{report["residual_delta_db"]:.4f} dB' in text


def test_desktop_loudness_report_measures_delivered_pcm(tmp_path, monkeypatch):
    path = source(tmp_path)
    perturb_delivery(monkeypatch)
    prepared, a, b = prepare_desktop(path, tmp_path)
    meter = pyln.Meter(48000)
    report = prepared.validation.loudness
    for data, track, measured in ((a, prepared.track_a, report.post_lufs_a),
                                  (b, prepared.track_b, report.post_lufs_b)):
        disk, _ = sf.read(track.pcm_path, dtype="float32", always_2d=True)
        np.testing.assert_array_equal(data, disk)
        assert measured == pytest.approx(meter.integrated_loudness(disk), abs=1e-7)
        assert track.loudness_lufs == measured
    assert report.residual_delta_db == pytest.approx(.125, abs=.002)
    assert prepared.null_gate["status"] == "failed", "gate must measure delivered PCM"


def test_shared_order_preserves_quiet_tails_and_bandwidth(web, tmp_path):
    path = source(tmp_path, quiet=True)
    client = prepare_web(web, path, bandwidth_limit_a_enabled=True,
                         bandwidth_limit_a_hz=16000)
    desktop, a, b = prepare_desktop(path, tmp_path,
        bandwidth_limit_a_enabled=True, bandwidth_limit_a_hz=16000)
    for label, reference in (("a", a), ("b", b)):
        delivered, _ = sf.read(io.BytesIO(client.get(f"/api/audio/{label}").data),
                               always_2d=True, dtype="float32")
        # Includes samples below -60 dB: no web-only amplitude gate or filter.
        np.testing.assert_allclose(delivered, reference, atol=1 / 32768, rtol=0)
    meta = client.get("/api/export/json").get_json()["session"]
    assert meta["preparation_order"] == desktop.disclosure["preparation_order"]
    assert meta["amplitude_gate"] == "none"


@pytest.mark.parametrize("mode,target", [("native", 96000), ("force_48k", 48000)])
def test_flac_probe_precedes_rate_selection(web, tmp_path, mode, target):
    path = source(tmp_path, rate=96000, suffix="flac")
    client = prepare_web(web, path, sample_rate_mode=mode)
    desktop, a, _ = prepare_desktop(path, tmp_path, mode=mode)
    meta = client.get("/api/export/json").get_json()["session"]
    assert meta["original_sample_rate"] == desktop.original_sample_rate == 96000
    assert meta["target_sample_rate"] == desktop.target_sample_rate == target
    delivered, sr = sf.read(io.BytesIO(client.get("/api/audio/a").data),
                            always_2d=True, dtype="float32")
    assert sr == target and sf.info(desktop.track_a.pcm_path).samplerate == target
    np.testing.assert_array_equal(delivered, a)


@pytest.mark.parametrize("failure", ["early_cancel", "late_cancel", "stale_config"])
def test_failed_preparation_never_publishes_or_leaks(web, tmp_path, monkeypatch, failure):
    from app.audio_pipeline import AudioPipeline
    from pathlib import Path
    path = source(tmp_path)
    client = configure_web(web, path)
    created = []
    original_mkdtemp = web.tempfile.mkdtemp
    original_prepare = AudioPipeline.prepare_session

    def mkdtemp(*args, **kwargs):
        result = original_mkdtemp(*args, **kwargs)
        created.append(Path(result))
        return result

    def prepare(self, *args, **kwargs):
        result = original_prepare(self, *args, **kwargs)
        if failure == "late_cancel":
            client.post("/api/preprocess/cancel", json={})
        elif failure == "stale_config":
            web.session.bandwidth_limit_a_enabled = True
        return result

    monkeypatch.setattr(web.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(AudioPipeline, "prepare_session", prepare)
    if failure == "early_cancel":
        client.post("/api/preprocess/cancel", json={})
    web._run_preprocess()
    assert not web.session.preprocess_ready
    assert not web.session.prepared_pair_ready()
    assert web.session.prepared_audio == {}
    assert web.session.active_pipeline is None
    assert client.post("/api/trial/start").status_code == 409
    assert all(not p.exists() for p in created), "unpublished work directories leaked"


@pytest.mark.parametrize("codec,encoder,bitrate", [("opus", "libopus", 64),
                                                    ("aptx", "aptx", 352)])
def test_native_44100_codec_roundtrip_is_shared(web, tmp_path, codec, encoder, bitrate):
    from app.audio_pipeline import AudioPipeline
    from app.models import PipelineStageConfig as Stage
    pipeline = AudioPipeline()
    if not pipeline._is_encoder_available(encoder):
        pytest.skip(f"{encoder} unavailable")
    path = source(tmp_path, rate=44100)
    stages = [{"codec_id": codec, "bitrate_kbps": bitrate}]
    client = prepare_web(web, path, stages_a=stages)
    desktop, a, b = prepare_desktop(path, tmp_path,
        pipeline_stages_a=[Stage(codec, bitrate)],
        pipeline_stages_b=[Stage("noop_passthrough", 0)])
    for label, reference in (("a", a), ("b", b)):
        delivered, sr = sf.read(io.BytesIO(client.get(f"/api/audio/{label}").data),
                               always_2d=True, dtype="float32")
        assert sr == 44100
        np.testing.assert_array_equal(delivered, reference)
    meta = client.get("/api/export/json").get_json()["session"]
    assert meta["loudness"] == pytest.approx(desktop.validation.loudness.to_dict())
    assert meta["disclosure"] == desktop.disclosure
    assert meta["null_gate"]["status"] == "not_applicable"


def test_web_missing_codec_refuses_instead_of_substituting(web, tmp_path):
    from app.audio_pipeline import AudioPipeline
    if AudioPipeline()._is_encoder_available("libldac"):
        pytest.skip("libldac present; strict-refusal path unreachable")
    client = configure_web(web, source(tmp_path),
        stages_a=[{"codec_id": "ldac", "bitrate_kbps": 660}])
    web._run_preprocess()
    assert not web.session.preprocess_ready
    assert not web.session.prepared_pair_ready()
    assert "Refusing to substitute" in web.session.preprocess_error
    assert "libldac" in web.session.preprocess_error
    assert client.post("/api/trial/start").status_code == 409


@pytest.mark.parametrize("kind", ["silent", "short"])
def test_unmeasurable_input_refuses_without_fabricated_loudness(web, tmp_path, kind):
    path = tmp_path / "unmeasurable.wav"
    sf.write(path, np.zeros((28800, 2)) if kind == "silent" else
             np.ones((4800, 2)) * .05, 48000)
    client = configure_web(web, path)
    web._run_preprocess()
    assert not web.session.preprocess_ready
    assert web.session.preprocess_error
    assert "loudness" not in client.get("/api/export/json").get_json()["session"]
    with pytest.raises(ValueError):
        prepare_desktop(path, tmp_path)


def test_peak_guard_exports_real_common_trim(web, tmp_path):
    path = source(tmp_path)
    data, sr = sf.read(path, always_2d=True)
    data *= .005
    data[len(data) // 2, 0] = .95
    sf.write(path, data, sr, subtype="PCM_16")
    client = prepare_web(web, path)
    report = client.get("/api/export/json").get_json()["session"]["loudness"]
    assert report["global_trim_db"] > 1
    for side in ("a", "b"):
        delivered, _ = sf.read(getattr(web.session, f"audio_{side}_path"), always_2d=True)
        assert np.max(np.abs(delivered)) < 1
        assert report[f"post_lufs_{side}"] < report["target_lufs"] - 1
        assert report[f"post_lufs_{side}"] == pytest.approx(
            pyln.Meter(sr).integrated_loudness(delivered), abs=1e-5)

"""Preparation may not bypass the shared run lifecycle (no audio deps)."""
import pytest


@pytest.fixture
def web(monkeypatch):
    import web.app as m
    monkeypatch.setattr(m, "session", m.SessionState())
    # No real threads for route contract tests.
    class Thread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
    monkeypatch.setattr(m.threading, "Thread", Thread)
    return m


@pytest.mark.parametrize("state", ["running", "completed", "aborted"])
def test_preprocess_refuses_frozen_run(web, state):
    s = web.session
    s.input_file_path = "source.wav"
    s.audio_a_path = "a.wav"
    s.audio_b_path = "b.wav"
    s.planned_trials = 1
    s.configure_engine()
    s.new_trial()
    if state == "completed":
        s.submit_answer("A")
    elif state == "aborted":
        s.seal_run()
    commitment = s.engine.session_commitment
    response = web.app.test_client().post("/api/preprocess", json={})
    assert response.status_code == 409
    assert s.engine.lifecycle == state
    assert s.engine.session_commitment == commitment
    assert s.audio_a_path == "a.wav"


def test_preprocess_drops_old_audio_before_worker_starts(web):
    s = web.session
    s.input_file_path = "source.wav"
    s.audio_a_path, s.audio_b_path = "a.wav", "b.wav"
    s.prepared_audio = {"id": "old"}
    s.session_metadata = {"loudness": {"residual_delta_db": 0.0}}
    response = web.app.test_client().post("/api/preprocess", json={})
    assert response.status_code == 200
    assert not s.prepared_pair_ready()
    assert s.prepared_audio == {} and s.session_metadata == {}
    assert web.app.test_client().post("/api/trial/start").status_code == 409


def test_config_is_locked_during_preparation(web):
    web.session.preprocess_running = True
    old = web.session.config_fingerprint()
    response = web.app.test_client().post("/api/config", json={"sample_rate_mode": "force_48k"})
    assert response.status_code == 409
    assert web.session.config_fingerprint() == old


@pytest.mark.parametrize("busy", ["preparing", "running"])
def test_upload_cannot_overwrite_input_in_use(web, tmp_path, busy):
    import io
    path = tmp_path / "source.wav"
    path.write_bytes(b"original")
    s = web.session
    s.input_file_path = str(path)
    s.audio_a_path, s.audio_b_path = "a.wav", "b.wav"
    if busy == "preparing":
        s.preprocess_running = True
    else:
        s.new_trial()
    response = web.app.test_client().post("/api/upload", data={
        "file": (io.BytesIO(b"replacement"), "source.wav")
    }, content_type="multipart/form-data")
    assert response.status_code == 409
    assert path.read_bytes() == b"original"
    assert s.input_file_path == str(path)

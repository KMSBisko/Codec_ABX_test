"""End-to-end API behaviour of the web shell (no ffmpeg required).

The codec pipeline needs ffmpeg and is validated on a machine that has it;
these tests cover the trial/blinding/export contract, which is ffmpeg-free.
"""
import io
import json
import math
import wave

import pytest


def _wav(seconds=0.05, rate=48000, amp=0.2, freq=440):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        n = int(seconds * rate)
        for i in range(n):
            v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += v.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import web.app as m

    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(_wav(freq=440))
    b.write_bytes(_wav(freq=660))

    def _reset():
        with m.session.lock:
            m.session.reset_trials()
            # The Phase 1 gates are session-level too. A leftover canary switch
            # or null-gate verdict from another test file would quietly change
            # what trial 1 is here, so the state is cleared both ways.
            m.session.canary_enabled = False
            m.session.canary_available = True
            m.session.canary_check = {}
            m.session.canary_pristine_path = None
            m.session.canary_degraded_path = None
            m.session.null_gate = {}
            m.session.audio_a_path = str(a)
            m.session.audio_b_path = str(b)
            m.session.planned_trials = None
            m.session.session_metadata = {}
            m.session.configure_engine()

    _reset()
    yield m.app.test_client()
    _reset()


def test_trial_start_leaks_no_answer(client):
    body = json.loads(client.post("/api/trial/start").data)
    assert set(body) == {"trial_index", "token", "display_mapping"}
    assert "x_is" not in body
    assert "x_source" not in body
    blob = json.dumps(body).lower()
    assert "source_a" not in blob and "source_b" not in blob


def test_x_audio_served_by_token_without_revealing_identity(client, tmp_path):
    body = json.loads(client.post("/api/trial/start").data)
    resp = client.get(f"/api/audio/x?token={body['token']}")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/wav"
    assert len(resp.data) > 44


def test_x_audio_without_token_falls_back_to_current_plan(client):
    body = json.loads(client.post("/api/trial/start").data)
    assert client.get("/api/audio/x").status_code == 200
    assert client.get("/api/audio/x?token=nonsense").status_code == 200
    assert body["token"]


def test_unknown_side_rejected(client):
    assert client.get("/api/audio/c").status_code == 400


def test_perfect_listener_scores_full_in_blind_mode(client):
    """The core regression: the legacy web engine scored a perfect listener
    INCORRECT when display labels were swapped."""
    client.post(
        "/api/config",
        json={"label_mapping_mode": "blind_random", "planned_trials": 20},
    )
    client.post("/api/trial/start")
    import web.app as m

    hits = 0
    for _ in range(20):
        plan = json.loads(client.post("/api/trial/start").data)
        mapping = plan["display_mapping"]
        with m.session.lock:
            x_source = m.session.engine._pending.x_source
        # answer the display label whose source is X
        label = next(l for l, src in mapping.items() if src == x_source)
        result = json.loads(
            client.post("/api/trial/answer", json={"answer": label}).data
        )
        hits += int(result["trial"]["correct"])

    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["correct_trials"] == 20
    assert hits == 20


def test_pre_registration_gates_the_verdict(client):
    client.post("/api/config", json={"planned_trials": 16})
    for _ in range(8):
        client.post("/api/trial/start")
        client.post("/api/trial/answer", json={"answer": "A"})
    mid = json.loads(client.get("/api/trial/stats").data)
    assert mid["provisional"] is True
    assert mid["is_final"] is False
    assert mid["required_correct"] == 12, "criterion pinned to planned N"

    for _ in range(8):
        client.post("/api/trial/start")
        client.post("/api/trial/answer", json={"answer": "A"})
    final = json.loads(client.get("/api/trial/stats").data)
    assert final["is_final"] is True


def test_config_accepts_and_reports_planned_trials(client):
    client.post("/api/config", json={"planned_trials": 25})
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["planned_trials"] == 25
    assert stats["required_correct"] == 18


def test_planned_trials_zero_means_no_registered_verdict(client):
    client.post("/api/config", json={"planned_trials": 0})
    client.post("/api/trial/start")
    client.post("/api/trial/answer", json={"answer": "A"})
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["planned_trials"] is None
    assert stats["is_final"] is False


def test_bad_answer_is_rejected(client):
    client.post("/api/trial/start")
    assert client.post("/api/trial/answer", json={"answer": "C"}).status_code == 400
    assert client.post("/api/trial/answer", json={}).status_code == 400


def test_export_carries_commit_reveal_and_verifies(client):
    client.post("/api/config", json={"planned_trials": 12})
    for _ in range(12):
        client.post("/api/trial/start")
        client.post("/api/trial/answer", json={"answer": "A"})

    payload = json.loads(client.get("/api/export/json").data)
    reveal = payload["session"]["verifiability"]
    assert reveal["verifiable"] is True
    assert payload["session"]["export_schema"] == "abx-session/1"
    assert "disclosure" in payload["session"]

    from core.abx import verify_commitment

    recorded = [t["x_source"] for t in payload["trials"]]
    assert verify_commitment(reveal["secret_hex"], len(recorded), recorded)


def test_diagnostics_reports_pending_mapping(client):
    client.post("/api/trial/start")
    diag = json.loads(client.get("/api/diagnostics").data)
    mapping = diag["display_mapping"]
    assert mapping is None or sorted(mapping.values()) == ["A", "B"]
    assert "stats" in diag


def test_stats_shape_is_stable_for_the_ui(client):
    client.post("/api/config", json={"planned_trials": 16})
    client.post("/api/trial/start")
    client.post("/api/trial/answer", json={"answer": "A"})
    s = json.loads(client.get("/api/trial/stats").data)
    for key in (
        "total_trials", "correct_trials", "p_value_one_tailed",
        "planned_trials", "is_final", "provisional", "required_correct",
        "significant", "ability_ci95", "label_mode",
    ):
        assert key in s, key
    assert json.loads(client.get("/api/health").data)["status"] == "ok"


def test_reset_clears_state(client):
    client.post("/api/trial/start")
    client.post("/api/trial/answer", json={"answer": "A"})
    client.post("/api/trial/reset")
    s = json.loads(client.get("/api/trial/stats").data)
    assert s["total_trials"] == 0

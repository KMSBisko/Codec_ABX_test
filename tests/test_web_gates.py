"""Web-shell behaviour of the Phase 1 validity gates.

No ffmpeg: these cover the HTTP contract -- what the browser may learn, when a
trial start is refused, and what the export says. The measured audio side of
the same features is in tests/test_pipeline_ffmpeg.py.
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
    """Full gate reset: the module-level session is shared by every web test."""
    import web.app as m

    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    pristine = tmp_path / "canary_pristine.wav"
    degraded = tmp_path / "canary_degraded.wav"
    a.write_bytes(_wav(freq=440))
    b.write_bytes(_wav(freq=660))
    pristine.write_bytes(_wav(freq=1000, amp=0.3))
    degraded.write_bytes(_wav(freq=90, amp=0.05))

    with m.session.lock:
        m.session.canary_enabled = False
        m.session.canary_available = True
        m.session.canary_check = {}
        m.session.canary_pristine_path = str(pristine)
        m.session.canary_degraded_path = str(degraded)
        m.session.null_gate = {}
        m.session.reset_trials()
        m.session.audio_a_path = str(a)
        m.session.audio_b_path = str(b)
        m.session.planned_trials = None
        m.session.session_metadata = {}
        m.session.configure_engine()
    yield m.app.test_client()
    with m.session.lock:
        m.session.canary_enabled = False
        m.session.canary_available = True
        m.session.canary_check = {}
        m.session.canary_pristine_path = None
        m.session.canary_degraded_path = None
        m.session.null_gate = {}
        m.session.reset_trials()


def _answer(client, label):
    return json.loads(client.post("/api/trial/answer", json={"answer": label}).data)


def _answer_correctly(client):
    """Answer using the mapping the server published, without reading X."""
    import web.app as m

    plan = json.loads(client.post("/api/trial/start").data)
    with m.session.lock:
        x_source = m.session.engine._pending.x_source
        mapping = m.session.engine._pending.display_mapping
    label = next(l for l, src in mapping.items() if src == x_source)
    return _answer(client, label), plan


def _answer_wrongly(client):
    """Deterministically wrong: the display label whose SOURCE is not X.

    Guessing is a coin flip, and these tests must not depend on a coin.
    """
    import web.app as m

    plan = json.loads(client.post("/api/trial/start").data)
    with m.session.lock:
        x_source = m.session.engine._pending.x_source
        mapping = m.session.engine._pending.display_mapping
    label = next(l for l, src in mapping.items() if src != x_source)
    return _answer(client, label), plan


# ── configuration ────────────────────────────────────────────────────────────


def test_canary_defaults_off_so_existing_sessions_are_unchanged(client):
    config = json.loads(client.get("/api/config").data)
    assert config["canary_enabled"] is False
    body = json.loads(client.post("/api/trial/start").data)
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["canary_status"] == "not_configured"
    assert stats["current_trial_kind"] == "normal"
    assert set(body) == {"trial_index", "token", "display_mapping"}


def test_config_turns_the_canary_on(client):
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    config = json.loads(client.get("/api/config").data)
    assert config["canary_enabled"] is True

    client.post("/api/trial/start")
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["current_trial_kind"] == "canary"
    assert stats["canary_status"] == "pending"
    assert stats["canary"]["trial_index"] == 1
    assert stats["total_trials"] == 0, "calibration is not counted in the statistics"


def test_an_unusable_calibration_material_disables_the_gate(client):
    import web.app as m

    with m.session.lock:
        m.session.canary_enabled = True
        m.session.canary_available = False
        m.session.canary_check = {
            "status": "not_audible",
            "usable": False,
            "reason": "degradation is not measurable (silent input)",
        }
        m.session.configure_engine()
    client.post("/api/trial/start")
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["current_trial_kind"] == "normal", "no coin-flip calibration"
    diag = json.loads(client.get("/api/diagnostics").data)
    assert diag["canary"]["status"] == "unavailable"
    assert "silent input" in diag["canary"]["reason"]


# ── the calibration pair is what the browser hears ───────────────────────────


def test_canary_trial_serves_the_calibration_pair(client):
    import web.app as m

    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    plan = json.loads(client.post("/api/trial/start").data)

    pristine = open(m.session.canary_pristine_path, "rb").read()
    degraded = open(m.session.canary_degraded_path, "rb").read()
    assert client.get("/api/audio/a").data == pristine
    assert client.get("/api/audio/b").data == degraded
    with m.session.lock:
        x_source = m.session.engine._pending.x_source
    expected = pristine if x_source == "A" else degraded
    assert client.get(f"/api/audio/x?token={plan['token']}").data == expected
    # The codec pair is still what a normal trial plays.
    assert client.get("/api/audio/a").data != degraded


def test_canary_answer_routes_to_the_canary_not_the_scored_trials(client):
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    result, _ = _answer_correctly(client)
    assert result["trial"]["kind"] == "canary"
    assert result["trial"]["trial_index"] == 1
    assert result["stats"]["total_trials"] == 0
    assert result["stats"]["canary_status"] == "passed"

    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["current_trial_kind"] == "normal"


# ── a failed gate shuts trial start (409) ────────────────────────────────────


def test_a_failed_canary_closes_the_gate_with_409(client):
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    _answer_wrongly(client)

    resp = client.post("/api/trial/start")
    assert resp.status_code == 409
    body = json.loads(resp.data)
    assert body["reasons"], "the shell needs the reason to show the user"
    assert "canary" in body["reasons"][0]
    assert body["gates"]["open"] is False
    assert body["gates"]["void"] is True


def test_a_failed_null_gate_closes_the_gate_with_409(client):
    from core.alignment import null_gate_decision

    import web.app as m

    with m.session.lock:
        m.session.null_gate = null_gate_decision(
            null_depth_db=-38.73, expected_identical=True
        )
        m.session.configure_engine()

    resp = client.post("/api/trial/start")
    assert resp.status_code == 409
    body = json.loads(resp.data)
    assert "null gate failed" in "; ".join(body["reasons"])
    assert "-38.73" in body["reasons"][0], "the measurement is quoted, not hidden"

    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["null_gate"]["status"] == "failed"
    assert stats["void"] is True
    assert stats["run_valid"] is False


def test_a_passed_null_gate_and_a_different_pair_stay_open(client):
    from core.alignment import null_gate_decision

    import web.app as m

    with m.session.lock:
        m.session.null_gate = null_gate_decision(
            null_depth_db=-6.0, expected_identical=False
        )
        m.session.configure_engine()
    assert client.post("/api/trial/start").status_code == 200

    with m.session.lock:
        m.session.null_gate = null_gate_decision(
            null_depth_db=float("-inf"), expected_identical=True
        )
        m.session.configure_engine()
    assert client.post("/api/trial/start").status_code == 200


# ── export and diagnostics say the same thing ────────────────────────────────


def test_export_records_the_gates_and_the_canary_row(client):
    from core.abx import verify_commitment

    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    _answer_correctly(client)
    for _ in range(4):
        _answer_correctly(client)

    payload = json.loads(client.get("/api/export/json").data)
    assert payload["session"]["canary"]["status"] == "passed"
    assert payload["session"]["canary"]["trial_index"] == 1
    assert payload["session"]["gates"]["open"] is True
    assert payload["session"]["run_valid"] is True
    assert payload["stats"]["canary_status"] == "passed"
    assert payload["stats"]["void"] is False

    kinds = [t["kind"] for t in payload["trials"]]
    assert kinds == ["canary", "normal", "normal", "normal", "normal"]
    assert [t["trial_index"] for t in payload["trials"]] == [1, 2, 3, 4, 5]
    assert payload["stats"]["total_trials"] == 4, "the binomial sees 4, not 5"

    reveal = payload["session"]["verifiability"]
    assert reveal["trials_covered"] == 5
    assert reveal["canary_included"] is True
    recorded = [t["x_source"] for t in payload["trials"]]
    assert verify_commitment(reveal["secret_hex"], len(recorded), recorded)


def test_a_void_run_is_void_everywhere(client):
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    _answer_wrongly(client)

    payload = json.loads(client.get("/api/export/json").data)
    assert payload["session"]["run_valid"] is False
    assert payload["session"]["gates"]["void"] is True
    assert payload["session"]["canary"]["status"] == "failed"
    text = "\n".join(payload["validity_lines"])
    assert "RUN VOID" in text
    assert "Calibration" in text and "FAILED at trial #1" in text

    diag = json.loads(client.get("/api/diagnostics").data)
    assert diag["run_valid"] is False
    assert diag["canary"]["status"] == "failed"
    assert diag["gates"]["void"] is True
    assert any("RUN VOID" in ln for ln in diag["validity_lines"])

    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["verdict"].startswith("VOID")


def test_validity_lines_quote_the_null_gate_measurement(client):
    from core.alignment import null_gate_decision

    import web.app as m

    with m.session.lock:
        m.session.session_metadata = {
            "null_depth_db": -38.73,
            "alignment_lag_samples": 0,
            "alignment_confidence": 0.99,
        }
        m.session.null_gate = null_gate_decision(
            null_depth_db=-38.73, expected_identical=True
        )
        m.session.configure_engine()
    text = "\n".join(json.loads(client.get("/api/diagnostics").data)["validity_lines"])
    assert "Null gate" in text
    assert "FAILED" in text
    assert "-38.73 dB" in text
    assert "-60.0 dB floor" in text


def test_csv_export_survives_real_rows(client):
    """The CSV export hardcoded 9 fieldnames while writing dataclass rows, and
    raised on every session. The header must follow the row shape."""
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    _answer_correctly(client)
    _answer_correctly(client)

    resp = client.get("/api/export/csv")
    assert resp.status_code == 200, "CSV export used to raise ValueError"
    lines = resp.data.decode("utf-8").strip().splitlines()
    header = lines[0].split(",")
    assert "kind" in header
    assert len(lines) == 3, "header + calibration + one scored trial"
    kinds = [dict(zip(header, line.split(",")))["kind"] for line in lines[1:]]
    assert kinds == ["canary", "normal"]


def test_csv_export_of_an_empty_session_still_has_a_header(client):
    resp = client.get("/api/export/csv")
    assert resp.status_code == 200
    assert resp.data.decode("utf-8").strip().splitlines()[0].startswith("trial_index")


def test_trials_endpoint_lists_the_canary(client):
    client.post("/api/config", json={"canary_enabled": True, "planned_trials": 4})
    _answer_correctly(client)
    body = json.loads(client.get("/api/trials").data)
    assert [t["kind"] for t in body["trials"]] == ["canary"]
    assert body["stats"]["total_trials"] == 0

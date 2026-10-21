"""Study lifecycle: configure -> prepare -> lock/run -> complete/abort -> reveal.

Everything here is an experimental-correctness invariant, not a UI preference.
A run is an experiment: the plan (N, input, processing, the rendered pair) is
fixed before the answers, the trial count stops exactly at N, and the
randomization secret stays unreveable while the same run can still accept
answers -- revealing it would hand the tester the X of every remaining trial.

The bugs these tests were written against, reproduced in memory before any fix:

* `ABXEngine(planned_trials=1)` answered one trial and then happily issued
  another: `is_final` said "final" while `new_trial()` kept working, so N was a
  label, not a bound.
* `GET /api/export/json` returned the secret mid-run and the run continued, so
  a tester could download the answer key, finish the study "perfectly", and
  export a run that looks complete and verifiable.
* The web `POST /api/config` rebuilt the engine during a run (discarding the
  recorded answers, re-drawing the secret) and kept serving the audio rendered
  from the OLD configuration.
* `stats["significant"]` was raw arithmetic: it read as a pass below N and on a
  void run.
"""
import io
import json
import math
import wave

import pytest

from core.abx import (
    STATE_ABORTED,
    STATE_COMPLETED,
    STATE_CONFIGURED,
    STATE_RUNNING,
    ABXEngine,
    ConfigLocked,
    StudySealed,
    TrialGateClosed,
    TrialLimitReached,
    verify_commitment,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(engine: ABXEngine, n: int, correct: bool = True) -> None:
    """Answer `n` scored trials (calibration first when it is enabled)."""
    for _ in range(n):
        plan = engine.new_trial()
        label = plan.x_display_label()
        engine.submit_answer(label if correct else ("B" if label == "A" else "A"))


def _wav(seconds=0.05, rate=48000, amp=0.2, freq=440):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += v.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path):
    """Web client with a session whose state is fully reset in both directions.

    The web session is module-level, so a sealed run, a prepared-audio identity
    or a locked N leaking out of this file would quietly change what the other
    web test files see.
    """
    import web.app as m

    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(_wav(freq=440))
    b.write_bytes(_wav(freq=660))

    def _reset():
        with m.session.lock:
            m.session.reset_trials()
            m.session.canary_enabled = False
            m.session.canary_available = True
            m.session.canary_check = {}
            m.session.canary_pristine_path = None
            m.session.canary_degraded_path = None
            m.session.null_gate = {}
            m.session.preprocess_ready = False
            m.session.prepared_config_fingerprint = None
            m.session.prepared_audio = {}
            m.session.stale_audio_reason = None
            m.session.audio_a_path = str(a)
            m.session.audio_b_path = str(b)
            m.session.planned_trials = None
            m.session.stages_a = []
            m.session.stages_b = []
            m.session.session_metadata = {}
            m.session.configure_engine()

    _reset()
    yield m.app.test_client()
    _reset()


def _start(client):
    return json.loads(client.post("/api/trial/start").data)


def _answer(client, label):
    return json.loads(client.post("/api/trial/answer", json={"answer": label}).data)


# ── core: the trial count stops at N ─────────────────────────────────────────


def test_scored_trials_stop_exactly_at_n():
    """The confirmed reproduction: N was a label, not a bound."""
    engine = ABXEngine(planned_trials=3)
    _run(engine, 3)
    assert engine.total_trials == 3
    with pytest.raises(TrialLimitReached):
        engine.new_trial()
    assert engine.total_trials == 3, "refused trial must not be recorded"
    with pytest.raises(RuntimeError):
        engine.submit_answer("A")


def test_n_equals_one_cannot_run_a_fourth_trial():
    engine = ABXEngine(planned_trials=1)
    _run(engine, 1)
    assert engine.lifecycle == STATE_COMPLETED
    with pytest.raises(TrialLimitReached):
        engine.new_trial()


def test_lifecycle_walks_configured_running_completed():
    engine = ABXEngine(planned_trials=2)
    assert engine.lifecycle == STATE_CONFIGURED
    engine.new_trial()
    assert engine.lifecycle == STATE_RUNNING
    engine.submit_answer("A")
    assert engine.lifecycle == STATE_RUNNING
    _run(engine, 1)
    assert engine.lifecycle == STATE_COMPLETED
    assert engine.can_accept_answers() is False


def test_calibration_does_not_count_toward_n():
    """The canary gates the run and is in the commitment, but N counts scored
    trials only: counting the free answer would inflate p."""
    engine = ABXEngine(planned_trials=2, canary_enabled=True)
    _run(engine, 3)  # trial 1 is the calibration trial, then two scored ones
    assert engine.total_trials == 2
    assert engine.canary_trial is not None
    assert engine.lifecycle == STATE_COMPLETED
    with pytest.raises(TrialLimitReached):
        engine.new_trial()


def test_a_passed_canary_does_not_complete_the_run():
    engine = ABXEngine(planned_trials=2, canary_enabled=True)
    _run(engine, 1)  # the calibration trial
    assert engine.lifecycle == STATE_RUNNING
    assert engine.can_accept_answers() is True
    assert engine.stats()["complete"] is False


def test_run_without_registered_n_never_completes():
    engine = ABXEngine()
    _run(engine, 8)
    assert engine.lifecycle == STATE_RUNNING
    assert engine.can_accept_answers() is True
    assert engine.is_final is False
    assert engine.stats()["complete"] is False


# ── core: the secret is the answer key for the trials still to come ──────────


def test_secret_is_not_exposable_while_answers_are_accepted():
    """Mid-run reveal must not contain the secret: X for trials n+1..N is
    derivable from it, so exporting mid-run is exporting the rest of the key."""
    engine = ABXEngine(planned_trials=10)
    _run(engine, 3)
    reveal = engine.reveal()
    assert engine.can_accept_answers() is True
    assert reveal["secret_hex"] is None, "mid-run export must not ship the key"
    assert reveal["redacted"] is True
    assert reveal["verifiable"] is False
    assert reveal["secret_revealed"] is False
    # Commitment and the answered part stay public: past answers were already
    # exported with each trial row, and the commitment fixes the sequence.
    assert reveal["commitment"] == engine.session_commitment
    assert reveal["x_sequence"] == [t.x_source for t in engine.all_trials()]
    assert reveal["reason"], "the file has to say why it is not verifiable"


def test_completion_reveals_the_secret_and_verifies():
    engine = ABXEngine(planned_trials=6)
    published = engine.session_commitment
    _run(engine, 6)
    reveal = engine.reveal()
    assert reveal["secret_hex"], "a finished run is auditable"
    assert reveal["redacted"] is False
    assert reveal["verifiable"] is True
    assert reveal["complete"] is True
    assert reveal["commitment"] == published
    recorded = [t.x_source for t in engine.all_trials()]
    assert verify_commitment(reveal["secret_hex"], len(recorded), recorded)


def test_a_sealed_run_is_distinguishable_from_a_redacted_snapshot():
    """Early reveal is allowed only by sealing the run: after that it accepts
    no answers, so revealing is safe, and the file says it is not a result."""
    engine = ABXEngine(planned_trials=10)
    _run(engine, 3)
    engine.abort(reason="listener had to leave")
    assert engine.lifecycle == STATE_ABORTED
    assert engine.can_accept_answers() is False
    reveal = engine.reveal()
    assert reveal["sealed"] is True
    assert reveal["complete"] is False
    assert reveal["redacted"] is False
    assert reveal["secret_hex"]
    assert "listener had to leave" in json.dumps(reveal)
    assert engine.is_final is False, "a sealed partial run is not a final result"
    verdict = engine.stats()["verdict"]
    assert "ABORTED" in verdict.upper()
    assert engine.stats()["complete"] is False
    assert engine.stats()["sealed"] is True


def test_a_sealed_run_accepts_no_further_answers():
    engine = ABXEngine(planned_trials=5)
    _run(engine, 2)
    engine.abort()
    with pytest.raises(StudySealed):
        engine.new_trial()
    with pytest.raises(RuntimeError):
        engine.submit_answer("A")
    assert engine.total_trials == 2


def test_reset_opens_a_new_run_and_re_redacts():
    engine = ABXEngine(planned_trials=3)
    _run(engine, 3)
    first_commitment = engine.session_commitment
    engine.reset()
    assert engine.lifecycle == STATE_CONFIGURED
    assert engine.session_commitment != first_commitment
    assert engine.can_accept_answers() is True
    assert engine.reveal()["redacted"] is True
    _run(engine, 1)


def test_a_failed_gate_export_is_not_redacted():
    """A run whose gate closed can no longer accept answers, so its export is
    auditable even below N -- and it must stay labelled VOID, never a result."""
    from core.abx import CanaryFailedError

    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    plan = engine.new_trial()
    label = plan.x_display_label()
    engine.submit_answer("B" if label == "A" else "A")
    with pytest.raises(CanaryFailedError):
        engine.new_trial()
    assert engine.can_accept_answers() is False
    reveal = engine.reveal()
    assert reveal["secret_hex"] is not None
    assert reveal["verifiable"] is True
    stats = engine.stats()
    assert stats["void"] is True
    assert stats["complete"] is False
    assert stats["is_final"] is False
    assert stats["verdict"].startswith("VOID")


# ── core: the plan is frozen once the run is under way ──────────────────────


def test_n_labels_and_canary_are_frozen_during_a_run():
    engine = ABXEngine(planned_trials=5)
    engine.new_trial()  # a trial in flight: A/B/X is already playable
    with pytest.raises(ConfigLocked):
        engine.set_planned_trials(50)
    with pytest.raises(ConfigLocked):
        engine.set_label_mode("blind_random")
    with pytest.raises(ConfigLocked):
        engine.set_canary_enabled(False)
    assert engine.planned_trials == 5
    assert engine.label_mode == "fixed"
    assert engine.canary_enabled is False


def test_direct_attribute_writes_are_refused_too():
    """Freezing only a setter is not freezing: the shells assign the fields."""
    engine = ABXEngine(planned_trials=5)
    engine.new_trial()
    with pytest.raises(ConfigLocked):
        engine.planned_trials = 500
    assert engine.planned_trials == 5


def test_config_is_editable_before_the_run_and_after_a_reset():
    engine = ABXEngine(planned_trials=5)
    engine.set_planned_trials(20)
    assert engine.planned_trials == 20
    engine.set_planned_trials(5)
    engine.new_trial()
    engine.submit_answer("A")
    with pytest.raises(ConfigLocked):
        engine.set_planned_trials(9)
    engine.reset()
    engine.set_planned_trials(9)
    assert engine.planned_trials == 9


def test_lock_freezes_the_prepared_audio_identity():
    """The rendered pair the run was locked to is recorded and checked; the
    shells pass the identity they rendered, so a run cannot be locked onto a
    pair that was not the one prepared for it."""
    engine = ABXEngine(planned_trials=4)
    engine.set_prepared_audio("sha256:aaaa", a_sha256="aaaa", b_sha256="bbbb")
    engine.lock_run(prepared_audio_id="sha256:aaaa")
    assert engine.lifecycle == STATE_RUNNING
    snapshot = engine.run_lock
    assert snapshot["prepared_audio_id"] == "sha256:aaaa"
    assert snapshot["planned_trials"] == 4
    assert snapshot["label_mode"] == "fixed"


def test_lock_refuses_a_mismatched_audio_identity():
    engine = ABXEngine(planned_trials=4)
    engine.set_prepared_audio("sha256:aaaa")
    with pytest.raises(ValueError):
        engine.lock_run(prepared_audio_id="sha256:zzzz")
    assert engine.lifecycle == STATE_CONFIGURED


def test_prepared_audio_cannot_change_mid_run():
    engine = ABXEngine(planned_trials=4)
    engine.set_prepared_audio("sha256:aaaa")
    engine.new_trial()
    with pytest.raises(ConfigLocked):
        engine.set_prepared_audio("sha256:bbbb")
    assert engine.prepared_audio["id"] == "sha256:aaaa"


def test_reset_keeps_the_prepared_pair_and_its_gate():
    """Answers are per run; the pair measurement describes the audio, which
    survives a reset (a broken pair is not repaired by re-answering)."""
    engine = ABXEngine(planned_trials=4)
    engine.set_prepared_audio("sha256:aaaa")
    engine.set_null_gate({"status": "passed", "threshold_db": -60.0})
    _run(engine, 4)
    engine.reset()
    assert engine.prepared_audio["id"] == "sha256:aaaa"
    assert engine.null_gate_block()["status"] == "passed"


# ── core: significance is a verdict only when it is allowed to be one ────────


def test_raw_significance_is_reported_separately_from_the_verdict():
    below = ABXEngine(planned_trials=20)
    _run(below, 8)
    stats = below.stats()
    assert stats["significant"] is True, "arithmetic is not edited"
    assert stats["significant_verdict"] is False, "not at N, so not a pass"
    assert stats["is_final"] is False

    final = ABXEngine(planned_trials=8)
    _run(final, 8)
    assert final.stats()["significant_verdict"] is True
    assert final.stats()["is_final"] is True


def test_a_void_run_is_never_a_significant_verdict():
    engine = ABXEngine(planned_trials=8, canary_enabled=True)
    plan = engine.new_trial()
    label = plan.x_display_label()
    engine.submit_answer("B" if label == "A" else "A")  # fail the canary
    _run_for_void(engine, 8)
    stats = engine.stats()
    assert stats["significant"] is True
    assert stats["void"] is True
    assert stats["significant_verdict"] is False
    assert "VOID" in stats["verdict"]


def _run_for_void(engine: ABXEngine, n: int) -> None:
    """Answer scored trials past a closed gate: the gate refuses, which is the
    point, so this drives the statistics directly."""
    from core.abx import Trial, _utc_now

    for i in range(1, n + 1):
        engine.trials.append(
            Trial(
                trial_index=i + 1,
                x_source="A",
                answer_display="A",
                answer_source="A",
                correct=True,
                display_mapping={"A": "A", "B": "B"},
                timestamp_utc=_utc_now(),
            )
        )


def test_stats_and_to_dict_publish_the_lifecycle():
    engine = ABXEngine(planned_trials=2)
    _run(engine, 2)
    stats = engine.stats()
    for key in (
        "lifecycle", "can_accept_answers", "complete", "sealed", "aborted",
        "significant_verdict",
    ):
        assert key in stats, key
    assert stats["lifecycle"] == STATE_COMPLETED
    parsed = json.loads(json.dumps(engine.to_dict()))
    assert parsed["lifecycle"] == STATE_COMPLETED
    assert parsed["reveal"]["verifiable"] is True


# ── web: the same rules at the HTTP boundary ─────────────────────────────────


def test_web_export_mid_run_is_redacted_and_labelled(client):
    client.post("/api/config", json={"planned_trials": 8})
    for _ in range(3):
        _start(client)
        _answer(client, "A")

    payload = json.loads(client.get("/api/export/json").data)
    reveal = payload["session"]["verifiability"]
    assert reveal["secret_hex"] is None, "mid-run export shipped the answer key"
    assert reveal["redacted"] is True
    assert reveal["verifiable"] is False
    assert payload["stats"]["complete"] is False
    assert payload["stats"]["can_accept_answers"] is True
    assert payload["session"]["lifecycle"] == STATE_RUNNING
    assert payload["stats"]["verdict"].startswith("provisional")


def test_web_export_after_completion_is_verifiable(client):
    client.post("/api/config", json={"planned_trials": 4})
    for _ in range(4):
        _start(client)
        _answer(client, "A")
    payload = json.loads(client.get("/api/export/json").data)
    reveal = payload["session"]["verifiability"]
    assert reveal["secret_hex"]
    assert reveal["verifiable"] is True
    recorded = [t["x_source"] for t in payload["trials"]]
    assert verify_commitment(reveal["secret_hex"], len(recorded), recorded)
    assert payload["stats"]["complete"] is True


def test_web_seal_makes_an_early_run_auditable_without_faking_a_result(client):
    client.post("/api/config", json={"planned_trials": 10})
    for _ in range(3):
        _start(client)
        _answer(client, "A")

    sealed = json.loads(client.post("/api/trial/seal", json={"reason": "time out"}).data)
    assert sealed["lifecycle"] == STATE_ABORTED
    assert sealed["can_accept_answers"] is False

    # No more trials: enforced by the backend, not by a disabled button.
    assert client.post("/api/trial/start").status_code == 409
    assert client.post("/api/trial/answer", json={"answer": "A"}).status_code == 409

    payload = json.loads(client.get("/api/export/json").data)
    reveal = payload["session"]["verifiability"]
    assert reveal["sealed"] is True
    assert reveal["complete"] is False
    assert reveal["secret_hex"]
    recorded = [t["x_source"] for t in payload["trials"]]
    assert verify_commitment(reveal["secret_hex"], len(recorded), recorded)
    assert payload["stats"]["is_final"] is False
    assert payload["session"]["incomplete"] is True
    assert "time out" in json.dumps(payload["session"])


def test_web_start_is_refused_once_n_is_reached(client):
    client.post("/api/config", json={"planned_trials": 2})
    for _ in range(2):
        _start(client)
        _answer(client, "A")

    resp = client.post("/api/trial/start")
    assert resp.status_code == 409
    body = json.loads(resp.data)
    assert body["lifecycle"] == STATE_COMPLETED
    assert any("N" in r or "complete" in r for r in body["reasons"])
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["total_trials"] == 2
    assert stats["can_accept_answers"] is False


def test_web_config_refuses_active_run_edits(client):
    client.post("/api/config", json={"planned_trials": 8})
    _start(client)
    _answer(client, "A")

    resp = client.post("/api/config", json={"planned_trials": 60})
    assert resp.status_code == 409, "N was redrawn mid-run"
    body = json.loads(resp.data)
    assert body["error"], "the user is told why the edit was refused"
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["planned_trials"] == 8
    assert stats["total_trials"] == 1, "the recorded answers survived the refused edit"


def test_web_config_refuses_label_mode_edits_mid_run(client):
    client.post("/api/config", json={"planned_trials": 8, "label_mapping_mode": "fixed"})
    _start(client)
    resp = client.post("/api/config", json={"label_mapping_mode": "blind_random"})
    assert resp.status_code == 409
    config = json.loads(client.get("/api/config").data)
    assert config["label_mapping_mode"] == "fixed"


def test_web_config_change_outside_a_run_invalidates_the_rendered_audio(client):
    """A config change must not keep serving the audio rendered from the old
    pipeline: the null gate and the canary pair describe the old pair."""
    import web.app as m

    # Rendered pair for THIS config, as a completed preprocess would leave it.
    client.post("/api/config", json={"planned_trials": 8})
    with m.session.lock:
        m.session.preprocess_ready = True
        m.session.prepared_config_fingerprint = m.session.config_fingerprint()
        m.session.prepared_audio = {"id": "sha256:old", "a_sha256": "old"}
        m.session.canary_pristine_path = "/tmp/pristine.wav"
        m.session.canary_degraded_path = "/tmp/degraded.wav"
        m.session.null_gate = {"status": "passed", "threshold_db": -60.0}
        m.session.engine.set_null_gate(m.session.null_gate)

    # A different pipeline: the rendered pair no longer matches the config.
    client.post(
        "/api/config",
        json={
            "planned_trials": 8,
            "stages_a": [{"codec_id": "opus", "bitrate_kbps": 128}],
            "stages_b": [{"codec_id": "aac", "bitrate_kbps": 128}],
        },
    )

    with m.session.lock:
        assert m.session.audio_a_path is None, "stale rendered audio was kept"
        assert m.session.audio_b_path is None
        assert m.session.preprocess_ready is False
        assert m.session.null_gate == {}
        assert m.session.prepared_audio == {}
        assert m.session.canary_pristine_path is None
        assert m.session.engine.null_gate_block()["status"] == "not_measured"
    assert client.get("/api/audio/a").status_code == 404
    assert client.post("/api/trial/start").status_code == 409, (
        "trials may not start on a pair that was never rendered for this config"
    )


def test_web_identical_config_posting_does_not_wipe_the_pair(client):
    """The UI posts the same config on every preprocess click; only a material
    change may invalidate the prepared pair."""
    import web.app as m

    client.post("/api/config", json={"planned_trials": 8})
    with m.session.lock:
        m.session.preprocess_ready = True
        m.session.prepared_config_fingerprint = m.session.config_fingerprint()

    client.post("/api/config", json={"planned_trials": 8})
    with m.session.lock:
        assert m.session.audio_a_path is not None
        assert m.session.preprocess_ready is True
    assert client.post("/api/trial/start").status_code == 200


def test_web_reset_opens_a_new_run_after_sealing(client):
    client.post("/api/config", json={"planned_trials": 3})
    _start(client)
    _answer(client, "A")
    client.post("/api/trial/seal", json={})
    assert client.post("/api/trial/start").status_code == 409

    # A sealed run is history: its plan is not editable, because its answers
    # were collected under that plan. The refusal names the action that IS
    # available, and the UI offers exactly that (start a new run).
    frozen = client.post("/api/config", json={"planned_trials": 99})
    assert frozen.status_code == 409
    payload = json.loads(frozen.data)
    assert payload["sealed"] is True
    assert payload["can_accept_answers"] is False
    assert any("seal" in r or "reset" in r for r in payload["reasons"])
    assert json.loads(client.get("/api/config").data)["planned_trials"] == 3

    client.post("/api/trial/reset")
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["lifecycle"] == STATE_CONFIGURED
    assert stats["total_trials"] == 0
    assert stats["can_accept_answers"] is True
    # The new run may register a new N: the freeze belonged to the sealed run.
    assert client.post("/api/config", json={"planned_trials": 5}).status_code == 200
    assert json.loads(client.get("/api/config").data)["planned_trials"] == 5
    assert client.post("/api/trial/start").status_code == 200


def test_web_gates_and_lifecycle_are_reported_together(client):
    diag = json.loads(client.get("/api/diagnostics").data)
    assert diag["lifecycle"] == STATE_CONFIGURED
    assert diag["can_accept_answers"] is True
    _start(client)
    diag = json.loads(client.get("/api/diagnostics").data)
    assert diag["lifecycle"] == STATE_RUNNING
    info = json.loads(client.get("/api/session/info").data)
    assert info["lifecycle"] == STATE_RUNNING
    assert info["can_accept_answers"] is True


# ── desktop shim: same rules, GUI-safe (no raising into a Qt slot) ──────────


def test_shim_stops_staging_trials_at_n_without_raising():
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=2)
    for _ in range(2):
        engine.submit_answer(engine.current_x_is)
    assert engine.stats().total_trials == 2
    assert engine.stats().can_accept_answers is False
    assert engine.run_complete, "plain text for a status bar, not an exception"
    assert engine.new_trial() == engine.current_x_is, "must not raise"
    assert engine.stats().complete is True


def test_shim_records_a_closed_gate_as_text_as_before():
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=4, canary_enabled=True)
    plan_x = engine.current_x_is
    engine.submit_answer("B" if plan_x == "A" else "A")
    assert engine.gate_open is False
    assert engine.gate_closed and "canary" in engine.gate_closed
    assert engine.stats().can_accept_answers is False


def test_shim_refuses_a_mid_run_planned_n_change_and_says_so():
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=5)
    engine.submit_answer(engine.current_x_is)
    engine.set_planned_trials(50)
    assert engine.planned_trials == 5
    assert engine.config_locked, "the GUI shows this text and snaps the spin back"


def test_shim_seal_is_available_to_the_desktop():
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=6)
    engine.submit_answer(engine.current_x_is)
    engine.seal("listener left")
    stats = engine.stats()
    assert stats.sealed is True
    assert stats.complete is False
    assert stats.can_accept_answers is False
    reveal = engine.reveal()
    assert reveal["sealed"] is True and reveal["secret_hex"]


def test_shim_a_failed_calibration_still_leaves_a_record_to_export():
    """The desktop refuses an export when it has recorded nothing.

    Calibration is not a scored trial, so a run that died at the calibration
    trial has ZERO scored rows -- and that is precisely the run that must stay
    exportable. `all_trials()` is what the export guard reads, so a void run is
    never trapped inside the tool, and its export is not redacted: the gate
    closed, the remaining X is worth nothing, and a failed chain is a result.
    """
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=4, canary_enabled=True)
    assert engine.current_trial_kind == "canary"
    engine.submit_answer("B" if engine.current_x_is == "A" else "A")

    stats = engine.stats()
    assert stats.total_trials == 0, "calibration is not a scored trial"
    assert engine.all_trials(), (
        "the calibration trial IS a record: a run with no scored rows still has "
        "an exportable result"
    )
    assert stats.void is True
    assert engine.export_is_redacted is False, (
        "a gate-closed run's export reveals the secret: the run cannot continue, "
        "so there is nothing left to protect"
    )


def test_shim_stats_carry_the_lifecycle_words_the_gui_reads():
    """The desktop reads `sealed_reason` and `run_closed_because` straight off
    the shim's stats (status bar, diagnostics panel, export). A field the shim
    does not map is an AttributeError in the middle of an export, so the shape
    is asserted here -- the GUI cannot be launched in this environment, so the
    contract is what is testable."""
    from app.abx_engine import ABXEngine as Shim

    engine = Shim(planned_trials=3)
    engine.submit_answer(engine.current_x_is)
    engine.seal("time out")
    stats = engine.stats()
    assert stats.sealed_reason == "time out"
    assert stats.run_closed_because
    assert any("seal" in reason for reason in stats.run_closed_because)
    assert stats.p_value_text.startswith("p:")

    fresh = Shim(planned_trials=3).stats()
    assert fresh.sealed_reason is None
    assert fresh.run_closed_because == []

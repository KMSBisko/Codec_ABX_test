"""The rendered-label contract: what the browser HEARS under a display label
must be the audio the scorer resolves that label to.

Core scores an answer by resolving the display label through the trial's
`display_mapping` (display -> source). That only means anything if the audio
served for the displayed label IS that source's audio. Three layers have to
agree, and the browser cannot apply the mapping itself (it never learns X, and
applying it in more than one layer inverts it):

    core.abx          scores  answer_display -> display_mapping -> source
    web/app.py        serves  /api/audio/{a,b} resolved through the plan
    app.js            plays  the bytes it was given, keyed per trial token

The static finding this test was written against: `side_audio_path()` returned
the physical A/B paths unchanged, and the browser's `getBufferForSide()` played
the raw A/B buffers, so on a swapped trial the listener heard source A under
the label the scorer reads as source B. In blind mode that inverts ~50% of the
trials: a perfect listener scores ~50% and a citable study reports "not
significant" about somebody who can hear the difference clearly.

Every test here judges the trial the way a listener does -- by comparing the
bytes the browser would decode -- never by reading `x_source`.
"""
import io
import json
import math
import random
import wave

import pytest

from core.abx import LABEL_MODE_BLIND, LABEL_MODE_FIXED, TRIAL_KIND_CANARY


def _wav(seconds=0.05, rate=48000, amp=0.2, freq=440):
    """A tiny sine WAV. The two sides are genuinely different material:
    a mapping bug cannot hide behind identical audio."""
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
def session_client(tmp_path):
    import web.app as m

    a = tmp_path / "phys_a.wav"
    b = tmp_path / "phys_b.wav"
    pristine = tmp_path / "canary_pristine.wav"
    degraded = tmp_path / "canary_degraded.wav"
    a.write_bytes(_wav(freq=440, amp=0.2))
    b.write_bytes(_wav(freq=880, amp=0.3))
    pristine.write_bytes(_wav(freq=1000, amp=0.3))
    degraded.write_bytes(_wav(freq=90, amp=0.05))

    def _reset():
        with m.session.lock:
            m.session.reset_trials()
            m.session.canary_enabled = False
            m.session.canary_available = True
            m.session.canary_check = {}
            m.session.canary_pristine_path = str(pristine)
            m.session.canary_degraded_path = str(degraded)
            m.session.null_gate = {}
            m.session.preprocess_ready = True
            # A rendered pair, as /api/preprocess would leave it: the pair the
            # browser plays, plus the identity its buffer cache is keyed on.
            fingerprint = m.session.config_fingerprint()
            m.session.prepared_config_fingerprint = fingerprint
            m.session.prepared_audio = {
                "id": m._prepared_audio_id(str(a), str(b), fingerprint),
                "a_sha256": m._file_sha256(str(a)),
                "b_sha256": m._file_sha256(str(b)),
                "config_fingerprint": fingerprint,
                "target_sample_rate": 48000,
            }
            m.session.stale_audio_reason = None
            m.session.audio_a_path = str(a)
            m.session.audio_b_path = str(b)
            m.session.planned_trials = None
            m.session.session_metadata = {}
            m.session.configure_engine()

    _reset()
    yield m.app.test_client()
    _reset()


def _paths():
    import web.app as m

    with m.session.lock:
        return (
            open(m.session.audio_a_path, "rb").read(),
            open(m.session.audio_b_path, "rb").read(),
            m.session.canary_pristine_path,
            m.session.canary_degraded_path,
        )


def _serve(client, side, token):
    resp = client.get(f"/api/audio/{side}?token={token}")
    assert resp.status_code == 200, f"/api/audio/{side} refused: {resp.data}"
    return resp.data


def _which_label_does_x_sound_like(client, token):
    """The listener's judgement: compare the audio served for the DISPLAYED
    labels against the audio served for X. No access to `x_source`."""
    x_bytes = _serve(client, "x", token)
    for label in ("a", "b"):
        if _serve(client, label, token) == x_bytes:
            return label.upper()
    return None


def _swap_seed(swap: bool) -> int:
    """A seed whose first label draw is a swap (or not), so the mapping of
    trial 1 is deterministic instead of a coin flip."""
    for seed in range(200):
        if (random.Random(seed).random() < 0.5) is swap:
            return seed
    raise AssertionError("no seed produced the requested first draw")  # pragma: no cover


def _set_label_rng(seed: int) -> None:
    import web.app as m

    with m.session.lock:
        m.session.engine._rng_label = random.Random(seed)


# ── the browser's A/B must follow the mapping ────────────────────────────────


def test_fixed_mode_serves_the_physical_paths(session_client):
    client = session_client
    phys_a, phys_b, _, _ = _paths()
    client.post("/api/config", json={"label_mapping_mode": "fixed"})
    plan = json.loads(client.post("/api/trial/start").data)
    assert plan["display_mapping"] == {"A": "A", "B": "B"}
    token = plan["token"]
    assert _serve(client, "a", token) == phys_a
    assert _serve(client, "b", token) == phys_b


def test_a_swapped_mapping_moves_the_audio_not_just_the_label(session_client):
    """The regression: with display A -> source B, the bytes served for the
    displayed label A must be source B's file. Serving the physical A under a
    label the scorer reads as B is what inverted the scoring."""
    import web.app as m

    client = session_client
    phys_a, phys_b, _, _ = _paths()
    client.post("/api/config", json={"label_mapping_mode": "blind_random"})
    _set_label_rng(_swap_seed(swap=True))

    plan = json.loads(client.post("/api/trial/start").data)
    assert plan["display_mapping"] == {"A": "B", "B": "A"}, "seed must give a swap"
    token = plan["token"]
    assert _serve(client, "a", token) == phys_b
    assert _serve(client, "b", token) == phys_a
    # X is a physical side: it is resolved from x_source, never re-mapped.
    with m.session.lock:
        x_source = m.session.engine.pending_plan.x_source
    expected = phys_a if x_source == "A" else phys_b
    assert _serve(client, "x", token) == expected


def test_a_perfect_listener_scores_full_by_ear_in_blind_mode(session_client):
    """The end-to-end contract, judged from the bytes the browser decodes.

    For each trial: fetch displayed A, displayed B and X, answer the label
    whose audio IS X, and the scorer must call that correct. Fails on the
    pre-fix server for every swapped trial.
    """
    client = session_client
    n = 16
    client.post(
        "/api/config",
        json={"label_mapping_mode": "blind_random", "planned_trials": n},
    )
    swaps = 0
    for _ in range(n):
        plan = json.loads(client.post("/api/trial/start").data)
        if plan["display_mapping"] == {"A": "B", "B": "A"}:
            swaps += 1
        label = _which_label_does_x_sound_like(client, plan["token"])
        assert label is not None, "X must sound like one of the displayed sides"
        result = json.loads(
            client.post("/api/trial/answer", json={"answer": label}).data
        )
        assert result["trial"]["correct"] is True, (
            f"trial {plan['trial_index']} mapping {plan['display_mapping']}: "
            "the listener identified X by ear from the served audio and was "
            "scored wrong"
        )
    stats = json.loads(client.get("/api/trial/stats").data)
    assert stats["correct_trials"] == n
    assert swaps > 0, "the sample must contain swapped trials to prove anything"


def test_the_calibration_pair_follows_the_mapping_too(session_client):
    """The canary sides are served under the same contract: displayed A is
    whatever source the plan maps A to, so a swapped calibration trial is not
    scored inverted."""
    import web.app as m

    client = session_client
    client.post(
        "/api/config",
        json={"canary_enabled": True, "label_mapping_mode": "blind_random",
              "planned_trials": 4},
    )
    with m.session.lock:
        m.session.canary_available = True
    pristine_path, degraded_path = _paths()[2], _paths()[3]
    pristine = open(pristine_path, "rb").read()
    degraded = open(degraded_path, "rb").read()

    _set_label_rng(_swap_seed(swap=True))
    plan = json.loads(client.post("/api/trial/start").data)
    assert plan["display_mapping"] == {"A": "B", "B": "A"}

    label = _which_label_does_x_sound_like(client, plan["token"])
    result = json.loads(
        client.post("/api/trial/answer", json={"answer": label}).data
    )
    assert result["trial"]["kind"] == TRIAL_KIND_CANARY
    assert result["trial"]["correct"] is True
    assert result["stats"]["canary_status"] == "passed"
    assert _serve(client, "a", plan["token"]) == degraded
    assert _serve(client, "b", plan["token"]) == pristine


def test_unmapped_a_b_requests_use_the_trial_in_flight(session_client):
    """The client caches buffers, so no-token requests resolve against the
    trial in flight; the answer it plays must match the answer it scores."""
    client = session_client
    client.post("/api/config", json={"label_mapping_mode": "blind_random"})
    _set_label_rng(_swap_seed(swap=True))
    plan = json.loads(client.post("/api/trial/start").data)
    phys_a, phys_b, _, _ = _paths()
    assert client.get("/api/audio/a").data == phys_b
    assert client.get("/api/audio/b").data == phys_a


def test_audio_info_publishes_the_identity_the_client_caches_on(session_client):
    """The browser keeps decoded AudioBuffers between trials. It can only do
    that safely if it can see when the mapping or the rendered pair changed:
    the plan carries the mapping, and /api/audio/info carries the rendered
    pair's identity."""
    client = session_client
    client.post("/api/config", json={"label_mapping_mode": "blind_random"})
    _set_label_rng(_swap_seed(swap=True))
    plan = json.loads(client.post("/api/trial/start").data)

    info = json.loads(client.get("/api/audio/info").data)
    assert info["display_mapping"] == {"A": "B", "B": "A"}
    assert info["trial_index"] == plan["trial_index"]
    assert info["prepared_audio_id"], "buffer cache needs an audio identity"
    import web.app as m
    with m.session.lock:
        assert info["prepared_audio_id"] == m.session.prepared_audio["id"]
    assert info["buffers_identical"] is False


def test_mapping_is_applied_once_in_the_serving_layer(session_client):
    """Ownership: the server resolves display -> source. The client applies no
    mapping, so the inversion cannot reappear by being applied twice."""
    import pathlib

    import web.app as m

    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    serving = src.split("def side_audio_path")[1].split("def resolve_x_path")[0]
    resolver = src.split("def _display_source")[1].split("def side_audio_path")[0]
    assert "display_mapping" in resolver, "the serving layer owns the mapping"
    assert "_display_source" in serving, (
        "a/b must be resolved through that one mapping helper"
    )
    assert serving.count("display_mapping") == 0 or "_display_source" in serving

    js = pathlib.Path(m.__file__).parent / "static" / "js" / "app.js"
    js_src = js.read_text(encoding="utf-8")
    body = js_src.split("function getBufferForSide")[1].split("function ")[0]
    assert "display_mapping" not in body and "displayMapping" not in body, (
        "the client must not map: it plays the bytes it is given"
    )

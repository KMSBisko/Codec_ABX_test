"""The browser client's lifecycle and playback contract, checked statically.

No browser was launched: nothing here proves that audio is heard, that a button
is clickable, or that the page looks right. What IS checkable without a display
is the ownership boundary this design depends on -- which layer decides, and
which layer merely obeys. Each test names the inversion it closes.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
JS = (WEB / "static" / "js" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "templates" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """Source of a top-level `function name(...)` in app.js."""
    start = JS.index(f"function {name}(")
    tail = JS[start:]
    end = re.search(r"\n\}", tail)
    assert end, f"unterminated function {name}"
    return tail[: end.end()]


def test_buffers_are_cached_per_trial_mapping_not_per_label():
    """The bug: A/B buffers were loaded once after preprocessing and reused for
    every trial, while the server serves the audio of a DISPLAY label through
    that trial's mapping. In blind mode a swapped trial is a different pair of
    bytes under the same button names, so the cached pair played the wrong
    audio -- and the listener scored a trial they never heard.
    """
    body = _fn("ensureTrialBuffers")
    assert "bufferCacheKey" in body
    key = _fn("bufferCacheKey")
    assert "preparedAudioId" in key, "a re-render must invalidate the cache"
    assert "canaryTrial" in key, "the calibration pair is a different pair"
    assert "mapping" in key, "a swapped mapping must miss the cache"

    start = _fn("startNewTrial")
    assert "ensureTrialBuffers(" in start
    assert "display_mapping" in start, "the plan's mapping keys the cache"


def test_the_client_never_applies_the_display_mapping_itself():
    """The client plays the bytes it is given. Applying the mapping again would
    invert it, which is exactly the class of bug that made blind scoring
    unreliable; the server owns the mapping, once.
    """
    for name in ("getBufferForSide", "ensureTrialBuffers", "preloadBuffer"):
        body = _fn(name)
        assert "display_mapping" not in body, f"{name} must not map"
        assert "displayMapping" not in body, f"{name} must not map"


def test_a_trial_is_requested_with_its_own_token():
    """`/api/audio/a` without a token resolves to the trial in flight; when the
    client fetches buffers for a SPECIFIC trial, the request has to name it,
    or a fetch racing a new trial reads the other trial's mapping.
    """
    body = _fn("preloadBuffer")
    assert "token" in body
    assert re.search(r"/api/audio/\$\{side\}\$\{tokenQuery\}", body), (
        "A/B must carry the token, not only X"
    )


def test_the_client_stops_advancing_when_the_run_is_over():
    """Auto-advance used to continue past the pre-registered N, and the refusal
    only appeared as a console error. `can_accept_answers` is core's answer, so
    the client obeys it; the backend refuses the trial too, so a hidden button
    is a courtesy, never the rule.
    """
    submit = _fn("submitAnswer")
    assert "can_accept_answers" in submit
    assert re.search(r"if \(state\.autoAdvanceEnabled && !runOver\)", submit)

    apply = _fn("applyRunState")
    assert "can_accept_answers" in apply
    assert "btnAnswerA" in apply and "btnAnswerB" in apply
    assert "showRunBanner" in apply

    banner = _fn("showRunBanner")
    for key in ("run_sealed_banner", "run_complete_banner", "run_gate_banner"):
        assert f't("{key}"' in banner, f"three endings, three sentences: {key}"


def test_the_early_export_path_is_the_seal():
    """Sealing is the honest way to export an unfinished run: the session stops,
    the key is released, and the file is labelled a sealed partial run.
    """
    seal = _fn("sealRun")
    assert "/api/trial/seal" in seal
    assert "applyRunState" in seal, "the panel must reflect the sealed run"

    assert 'onclick="sealRun()"' in HTML
    assert 'id="runBanner"' in HTML, "the run's ending is stated on the panel"
    assert 'data-i18n="export_redacted_note"' in HTML, (
        "the export area must say that a mid-run export is redacted"
    )


def test_a_closed_run_is_not_editable_and_says_what_to_do_instead():
    """A closed run's configuration is history. The client must not present it
    as an ordinary edit error: the available action is a new run.
    """
    start = _fn("startPreprocess")
    assert "/api/trial/reset" in start
    assert "start_new_run_confirm" in start
    assert "config_frozen_alert" in start

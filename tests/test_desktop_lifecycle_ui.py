"""The desktop shell's lifecycle wiring, checked where it can be checked.

The Qt app cannot be launched in a headless environment (PyQt6 needs a display
and libGL), so its lifecycle rules would otherwise be untested: the engine and
the shim are covered elsewhere, and this module asserts the parts of `app/main.py`
that decide what the listener is allowed to do -- from the syntax tree, not from
formatting. Each check names the bug it closes.

These are contract checks on ONE layer. They do not prove that the GUI behaves:
no window was opened, no playback was performed, and no listening test was run.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
TREE = ast.parse(MAIN.read_text(encoding="utf-8"))


def _source(fragment: str) -> str:
    """Source of every function/method named `fragment`, concatenated."""
    chunks = []
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = ast.get_source_segment(MAIN.read_text(encoding="utf-8"), node)
            if node.name == fragment and body:
                chunks.append(body)
    assert chunks, f"no function named {fragment!r} in app/main.py"
    return "\n".join(chunks)


def _call_names(fragment: str) -> set[str]:
    """Every attribute name called inside the named function."""
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fragment:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    if isinstance(func, ast.Attribute):
                        names.add(func.attr)
                    elif isinstance(func, ast.Name):
                        names.add(func.id)
    return names


def _statistics_keys() -> set[str]:
    """Keys of the `statistics={...}` dict passed to the logger at export."""
    keys = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.keyword) and node.arg == "statistics":
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
    assert keys, "no statistics block is stamped at export"
    return keys


def _seal_kwargs() -> set[str]:
    """Keyword arguments handed to `logger.seal(...)`."""
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "seal":
                for kw in node.keywords:
                    if kw.arg:
                        names.add(kw.arg)
    return names


def test_the_run_is_frozen_by_core_not_by_a_disabled_widget():
    """N used to be locked by a local `total_trials > 0` test in the handler.

    The rule belongs to the engine, which names the frozen fields and raises
    `ConfigLocked`; the handler delegates and shows the engine's words, so the
    web and the desktop cannot drift into different definitions of 'mid-run'.
    """
    body = _source("_on_planned_trials_changed")
    assert "set_planned_trials" in body, "must delegate the rule to the engine"
    assert "config_locked" in body, "must surface the engine's refusal"
    assert "total_trials > 0" not in body, (
        "the local test is the bug: a STAGED trial already published a "
        "commitment and playable audio, and was editable under the old rule"
    )


def test_the_calibration_switch_cannot_be_flipped_under_a_staged_trial():
    """Toggling the gate used to be refused only once an answer existed.

    A staged trial has a published commitment; rebuilding the engine under it
    drops the commitment the answers are scored against. The checkbox also
    snaps back, so the panel never shows a gate the session does not have.
    """
    body = _source("_on_canary_toggled")
    assert "run_under_way" in body, "the freeze is core's, not an answer count"
    assert "canary_check" in body, "the widget must snap back to the real state"


def test_the_score_panel_stops_when_the_run_stops():
    """Answer buttons follow `can_accept_answers`, and a sealed run is shown as
    sealed: never provisional, never final."""
    body = _source("_update_score_ui")
    assert "can_accept_answers" in body
    assert "setEnabled" in body, "the buttons stop matching the run"
    assert "run_sealed_status" in body and "run_complete_status" in body
    assert "sealed" in body, "a sealed run is labelled, not called provisional"


def test_an_early_export_goes_through_the_seal_and_not_through_a_lie():
    """The desktop gets the same honest early-export path as the web: seal the
    run, which releases the key and labels the run; no seal, no secret.
    """
    body = _source("on_seal_run")
    assert "self.engine.seal(" in body
    assert "_update_score_ui" in body and "_refresh_gate_status" in body
    assert "QInputDialog" in body, "the reason is recorded, not invented"

    ui = _source("_build_playback_group") + _source("_apply_language")
    assert "seal_run_btn" in ui, "the control exists, in both languages"


def test_the_export_names_the_lifecycle_and_the_guarded_significance():
    """The exported file is the citation artifact. It carries the raw
    arithmetic AND the claimable form of it, the lifecycle of the run, and the
    identity of the audio the verdict describes.
    """
    stats = _statistics_keys()
    for key in (
        "significant",
        "significant_verdict",
        "lifecycle",
        "complete",
        "sealed",
        "sealed_reason",
        "incomplete",
        "can_accept_answers",
        "run_closed_because",
        "is_final",
        "void",
        "verdict",
    ):
        assert key in stats, f"export statistics must carry {key}"

    sealed = _seal_kwargs()
    for block in (
        "verifiability",
        "statistics",
        "canary",
        "null_gate",
        "gates",
        "run_valid",
        "prepared_audio",
        "run_lock",
        "export_is_redacted",
    ):
        assert block in sealed, f"the export must stamp {block}"


def test_a_calibration_only_run_is_still_exportable():
    """A run that died at the calibration trial has no SCORED rows, and hiding
    it is how failed runs get quietly dropped. The export guard reads
    `all_trials()`, which includes the calibration trial.
    """
    guard = _source("_has_any_record")
    assert "all_trials" in guard, "the calibration trial is a record"
    for name in ("on_export_json", "on_export_csv"):
        body = _source(name)
        assert "_has_any_record" in body, f"{name} must not block a void run"
        assert "if not self.logger.trials" not in body


def test_the_prepared_pair_is_hashed_and_frozen_at_prepare():
    """The run is locked to the audio the listener heard: the identity of the
    delivered buffers is recorded on the engine at prepare time, where core
    freezes it for the duration of the run.
    """
    body = _source("_on_prepare_done")
    assert "prepared_audio_identity" in body
    assert "set_prepared_audio" in body, "core freezes the identity from here"
    assert '"prepared_audio"' in body, "the export names the audio it describes"

    fingerprint = _source("_audio_config_fingerprint")
    assert "canary_enabled" in fingerprint
    assert "planned_trials" not in fingerprint, (
        "N is the trial plan, not the audio: registering a different count "
        "must not make a rendered pair stale"
    )
    assert "codec_a" in fingerprint and "bandwidth_limit_a_hz" in fingerprint


def test_the_desktop_status_bar_speaks_every_lifecycle_state():
    """Three endings, three sentences: complete, sealed, gate closed. A run
    stopped early must never be able to read as a finished study."""
    calls = _call_names("_update_score_ui")
    assert "_set_status" in calls
    translations = MAIN.read_text(encoding="utf-8")
    for key in ("run_sealed_status", "run_complete_status", "run_gate_status"):
        assert f'"{key}"' in translations
    # i18n parity for these keys is enforced by tests/test_i18n_keys.py.

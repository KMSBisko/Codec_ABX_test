"""Flask application for the web ABX tester.

Provides REST API endpoints for:
- Serving the frontend
- Audio file upload
- Codec catalog retrieval
- Preprocessing orchestration
- Trial management (ABX engine)
- Results export (JSON/CSV)
- Audio streaming for playback
"""
from __future__ import annotations

import csv
import io
import json
import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
)

from .models import (
    LabelMappingMode,
    PipelineStageConfig,
    ProcessingMode,
    SampleRateMode,
    TrialResult,
    codec_catalog,
    codec_catalog_to_json,
    trial_result_to_dict,
)

from core.abx import (
    LABEL_MODE_BLIND,
    LABEL_MODE_FIXED,
    TRIAL_KIND_CANARY,
    ABXEngine as CoreABXEngine,
    StudyLifecycleError,
    TrialGateClosed,
)
from core.report import canary_report_block, validity_lines

# ── Flask App Setup ──────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Upload configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
MAX_UPLOAD_SIZE_MB = 200
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# ── Session State (in-memory, single-user) ───────────────────────────────────

class SessionState:
    """Holds all mutable state for a single ABX test session."""

    def __init__(self):
        self.lock = threading.Lock()
        # Upload
        self.input_file_path: Optional[str] = None
        self.input_file_name: Optional[str] = None
        # Configuration
        self.sample_rate_mode: SampleRateMode = SampleRateMode.NATIVE
        self.label_mapping_mode: LabelMappingMode = LabelMappingMode.FIXED
        self.stages_a: List[PipelineStageConfig] = []
        self.stages_b: List[PipelineStageConfig] = []
        self.bandwidth_limit_a_enabled: bool = False
        self.bandwidth_limit_a_hz: Optional[int] = None
        self.bandwidth_limit_b_enabled: bool = False
        self.bandwidth_limit_b_hz: Optional[int] = None
        # Pre-registered trial count (fixed BEFORE the first answer)
        # Calibration (canary) trial: trial 1, known answer, gates the session.
        # Off by default at the API level so an existing scripted session
        # behaves exactly as before; the web UI turns it on (it sends
        # canary_enabled=true) because a run nobody calibrated is not citable.
        self.canary_enabled: bool = False
        self.canary_available: bool = True
        self.canary_check: Dict[str, Any] = {}
        self.canary_pristine_path: Optional[str] = None
        self.canary_degraded_path: Optional[str] = None
        # Null pair gate decision for the prepared pair (core.alignment)
        self.null_gate: Dict[str, Any] = {}
        # Preprocessing
        self.active_pipeline = None
        self.preprocess_running = False
        self.preprocess_cancelled = False
        self.preprocess_phase = ""
        self.preprocess_progress = 0.0
        self.preprocess_message = ""
        self.preprocess_ready = False
        self.preprocess_error: Optional[str] = None
        # Processed audio paths (final aligned, normalized A and B WAVs)
        self.audio_a_path: Optional[str] = None
        self.audio_b_path: Optional[str] = None
        # Identity of the rendered pair: a digest of the two delivered WAVs plus
        # the fingerprint of the configuration that rendered them. A run is
        # locked to it, the browser caches its decoded buffers against it, and
        # a configuration change that no longer matches it invalidates the pair
        # -- so a verdict can never be printed over audio the listener did not
        # hear. `prepared_config_fingerprint` is what the next config POST is
        # compared against.
        self.prepared_audio: Dict[str, Any] = {}
        self.prepared_config_fingerprint: Optional[str] = None
        self.stale_audio_reason: Optional[str] = None
        # Session metadata for diagnostics
        self.session_metadata: Dict[str, Any] = {}
        # ABX Engine state (canonical engine from core.abx -- shared with the
        # desktop app so identical studies yield identical statistics)
        self.planned_trials: Optional[int] = None
        self.engine = CoreABXEngine(
            planned_trials=None,
            label_mode=LABEL_MODE_FIXED,
        )
        # token -> TrialPlan for the trial in flight. X audio is served by
        # token so the client can play X without ever learning whether X is
        # A or B. The pre-Phase-0 /api/trial/start shipped `x_is` and the
        # label mapping to the browser, so the answer sat in the client
        # before the answer was made.
        self.pending_plans: Dict[str, Any] = {}

    # ── configuration: fingerprint, freeze, invalidation ────────────────────

    def config_fingerprint(self) -> str:
        """Fingerprint of everything that changes the RENDERED pair.

        Plan-only settings (N, label mode) are deliberately not in here:
        changing them does not make the rendered audio stale. Neither is the
        calibration switch: it selects an EXTRA pair (the pristine sample and
        its degraded copy) and does not alter the delivered A/B bytes, so
        flipping it must not force a re-render of a long pipeline. What the
        switch needs is a measured calibration pair, which is tracked on
        `canary_available` and reported by the gate, not on this fingerprint.
        """
        payload = {
            "input_file_path": self.input_file_path,
            "sample_rate_mode": self.sample_rate_mode.value,
            "stages_a": [
                {"codec_id": s.codec_id, "bitrate_kbps": s.bitrate_kbps}
                for s in self.stages_a
            ],
            "stages_b": [
                {"codec_id": s.codec_id, "bitrate_kbps": s.bitrate_kbps}
                for s in self.stages_b
            ],
            "bandwidth_limit_a_enabled": self.bandwidth_limit_a_enabled,
            "bandwidth_limit_a_hz": self.bandwidth_limit_a_hz,
            "bandwidth_limit_b_enabled": self.bandwidth_limit_b_enabled,
            "bandwidth_limit_b_hz": self.bandwidth_limit_b_hz,
        }
        blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def prepared_pair_ready(self) -> bool:
        """Is there a rendered pair at all? `prepare` comes before `run`."""
        return bool(self.audio_a_path and self.audio_b_path)

    def invalidate_prepared_audio(self, reason: str) -> None:
        """Drop the rendered pair, its gates and the run built on it.

        Called when the configuration changes under a pair that was rendered
        from the old one. The gates describe the PAIR, so they go with it:
        keeping a passed null gate from the old pipelines is exactly how a
        broken pair slips through. The run goes too, because its verdict would
        describe audio the listener is no longer hearing.
        """
        self.audio_a_path = None
        self.audio_b_path = None
        self.preprocess_ready = False
        self.prepared_audio = {}
        self.prepared_config_fingerprint = None
        self.stale_audio_reason = reason
        self.null_gate = {}
        self.canary_check = {}
        self.canary_available = True
        self.canary_pristine_path = None
        self.canary_degraded_path = None
        self.session_metadata = {}
        self.reset_trials()

    def configure_engine(self) -> None:
        """(Re)build the engine from the current session configuration."""
        self.engine = CoreABXEngine(
            planned_trials=self.planned_trials,
            label_mode=(
                LABEL_MODE_BLIND
                if self.label_mapping_mode == LabelMappingMode.BLIND_RANDOM
                else LABEL_MODE_FIXED
            ),
            # A canary whose material is not audible is a coin flip, so an
            # unusable calibration pair disables the gate rather than voiding
            # runs at random (and says so in the report).
            canary_enabled=bool(self.canary_enabled and self.canary_available),
        )
        self.engine.set_null_gate(self.null_gate or None)
        # The prepared pair belongs to the session, not to an engine instance:
        # a rebuild keeps the identity the run gets locked to.
        if self.prepared_audio:
            self.engine.prepared_audio = dict(self.prepared_audio)
        self.pending_plans.clear()

    def reset_trials(self):
        """Reset ABX trial state: a new run, planned from scratch."""
        self.engine.reset()
        self.configure_engine()

    def seal_run(self, reason: Optional[str] = None) -> Dict[str, Any]:
        """End the run now: the export becomes auditable, the run is not a result."""
        result = self.engine.abort(reason)
        # The trial in flight is void: its audio may have been played and its
        # answer will never arrive, so it must stop being fetchable.
        self.pending_plans.clear()
        stats = self.stats()
        result["stats"] = stats
        result["can_accept_answers"] = stats["can_accept_answers"]
        result["sealed_reason"] = stats["sealed_reason"]
        result["complete"] = stats["complete"]
        result["export_is_redacted"] = False  # the key is released with the seal
        return result

    def new_trial(self) -> Dict[str, Any]:
        """Start a new trial. Returns the CLIENT-SAFE plan only.

        Raises core.abx.TrialGateClosed when a validity gate is shut, and
        core.abx.StudyLifecycleError when the run has reached N or was sealed;
        the endpoint turns either into a 409, so no shell can offer trials the
        other would refuse.
        """
        if not self.prepared_pair_ready():
            raise TrialGateClosed(
                "no prepared audio pair: preprocess the session before running trials"
                + (f" ({self.stale_audio_reason})" if self.stale_audio_reason else ""),
                [
                    "the rendered A/B pair is missing (the configuration "
                    "changed, or preprocessing has not run): press "
                    "Preprocess & Validate before running trials"
                ],
            )
        plan = self.engine.new_trial()
        self.pending_plans[plan.token] = plan
        # Keep the token table small; only the newest plans are playable.
        if len(self.pending_plans) > 8:
            for key in list(self.pending_plans.keys())[:-8]:
                self.pending_plans.pop(key, None)
        return plan.public()

    def _plan_for(self, token: Optional[str] = None):
        plan = self.pending_plans.get(token) if token else self.engine.pending_plan
        if plan is None:
            plans = list(self.pending_plans.values())
            plan = plans[-1] if plans else None
        return plan

    def _display_source(self, plan, side_label: str) -> str:
        """Display label -> physical source, applied ONCE, in the serving layer.

        Core scores an answer by resolving the display label through the
        trial's `display_mapping`, so the audio served under that label has to
        BE that source's audio. The browser applies no mapping and must not:
        mapping in two layers inverts it, and the client has no business
        reasoning about sources at all. So this is the one place a label's
        audio is chosen. X is the exception by design -- X is a physical side,
        resolved from `x_source` and never re-mapped.
        """
        if plan is None:
            return side_label
        return dict(plan.display_mapping).get(side_label, side_label)

    def side_audio_path(
        self, side: str, token: Optional[str] = None
    ) -> Optional[str]:
        """Server-side resolution of a/b/x -> an audio path.

        'a' and 'b' are DISPLAY labels: they resolve through the trial's
        display mapping, the same resolution the scorer uses, so what the
        listener hears under a label is what an answer under that label means.
        In blind mode roughly half the trials are swapped, and serving the
        physical pair here inverted every swapped trial: a perfect listener
        scored ~50% and the study reported "not significant" about somebody
        who could hear the difference. See tests/test_web_playback_mapping.py.

        During a calibration trial the same three names resolve to the
        calibration pair (pristine vs grossly degraded) under the same
        mapping. The client asks for "A", "B" and "X" exactly as always, so the
        blinding is unchanged: it never learns which of the two X was.
        """
        plan = self._plan_for(token)
        if plan is not None and plan.kind == TRIAL_KIND_CANARY:
            if side in ("a", "b"):
                source = self._display_source(plan, side.upper())
                return (
                    self.canary_pristine_path
                    if source == "A"
                    else self.canary_degraded_path
                )
            if side == "x":
                return (
                    self.canary_pristine_path
                    if plan.x_source == "A"
                    else self.canary_degraded_path
                )
            return None

        if side in ("a", "b"):
            source = self._display_source(plan, side.upper())
            return self.audio_a_path if source == "A" else self.audio_b_path
        if side == "x":
            if plan is None:
                return None
            return self.audio_a_path if plan.x_source == "A" else self.audio_b_path
        return None

    def resolve_x_path(self, token: Optional[str] = None) -> Optional[str]:
        """Server-side resolution of X -> the actual audio file."""
        return self.side_audio_path("x", token)


    def submit_answer(self, answer_display: str) -> TrialResult:
        """Score an answer given as a DISPLAY label.

        The engine resolves display -> source and compares against X's
        source. The legacy code compared a display answer against a
        source-level X and applied the label mapping in the wrong direction,
        so a perfect listener under swapped labels was scored INCORRECT.
        Reproduced in tests/test_legacy_blind_bug.py.
        """
        trial = self.engine.submit_answer(answer_display)
        return TrialResult(
            trial_index=trial.trial_index,
            x_is=trial.x_source,
            answer=trial.answer_display,
            correct=trial.correct,
            timestamp_utc=trial.timestamp_utc,
            mapping_a_to=trial.display_mapping.get("A"),
            mapping_b_to=trial.display_mapping.get("B"),
            x_source=trial.x_source,
            answer_source=trial.answer_source,
            mapping_changed_for_next_trial=None,
            # "canary" for the calibration trial: the UI says so, and the row
            # is never counted in the statistics.
            kind=trial.kind,
        )

    def stats(self) -> Dict[str, Any]:
        raw = self.engine.stats()
        raw["session_commitment"] = self.engine.session_commitment
        return raw

    @property
    def trials(self) -> List[Any]:
        return self.engine.trials

    @property
    def total_trials(self) -> int:
        return self.engine.total_trials

    @property
    def correct_trials(self) -> int:
        return self.engine.correct_trials


# Global session state
session = SessionState()

# ── Helper: Find FFmpeg ──────────────────────────────────────────────────────

def find_ffmpeg() -> str:
    """Locate ffmpeg binary, checking bundled path first."""
    # Check third_party bundled location
    bundled = Path(__file__).parent.parent / "third_party" / "ffmpeg" / "bin" / "ffmpeg"
    if bundled.exists():
        return str(bundled)
    # Fall back to PATH
    return "ffmpeg"


def find_ffprobe() -> str:
    bundled = Path(__file__).parent.parent / "third_party" / "ffmpeg" / "bin" / "ffprobe"
    if bundled.exists():
        return str(bundled)
    return "ffprobe"

# ── Frontend Routes ──────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint for Docker / load balancer."""
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    """Serve the main page."""
    return render_template("index.html")


# ── API: Codec Catalog ───────────────────────────────────────────────────────

@app.route("/api/catalog", methods=["GET"])
def get_catalog():
    """Return available codec profiles as JSON."""
    catalog = codec_catalog()
    result = {}
    for cid, profile in catalog.items():
        result[cid] = {
            "codec_id": profile.codec_id,
            "display_name": profile.display_name,
            "ui_name": profile.ui_name,
            "container_ext": profile.container_ext,
            "bitrate_options_kbps": profile.bitrate_options_kbps,
            "pipeline_noop": profile.pipeline_noop,
        }
    return jsonify(result)


# ── API: File Upload ─────────────────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
def upload_file():
    """Upload an audio file (WAV or FLAC)."""
    if "file" not in request.files:
        return jsonify({"error": "no file part"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    allowed_ext = {".wav", ".flac"}
    ext = Path(f.filename).suffix.lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"unsupported format: {ext}"}), 400

    # Save file
    safe_name = Path(f.filename).stem[:60] + ext
    with session.lock:
        if session.preprocess_running or session.engine.run_under_way():
            return jsonify({"error": "input is locked during preparation or a frozen run"}), 409
        path = os.path.join(UPLOAD_FOLDER, safe_name)
        f.save(path)
        session.input_file_name = f.filename
        session.input_file_path = path
        # Re-uploading the same name changes bytes without changing the path.
        session.invalidate_prepared_audio("input audio replaced; render again")

    return jsonify({
        "filename": f.filename,
        "saved_as": safe_name,
    })


# ── API: Configuration ───────────────────────────────────────────────────────

@app.route("/api/config", methods=["POST"])
def set_config():
    """Update session configuration.

    Two rules the browser cannot opt out of, both enforced here:

    1. The plan is frozen while a run is under way. A run in progress has a
       published commitment, playable A/B/X and possibly recorded answers, so
       re-registering N, flipping the label mode, toggling the calibration
       trial or re-pointing the pipelines is redrawing the experiment under the
       data. The edit is refused with 409; the way to change the plan is to
       seal the run (auditable, not a result) or reset it.
    2. A material configuration change OUTSIDE a run invalidates the rendered
       pair. The old WAVs were rendered by the old pipeline and their null gate
       and calibration material describe that pair, so they are dropped --
       trials cannot start again until Preprocess & Validate has rendered the
       pair this configuration describes.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "no JSON body"}), 400

    with session.lock:
        if session.preprocess_running:
            return jsonify({"error": "configuration is locked during preparation"}), 409
        engine = session.engine
        if engine.run_under_way():
            reasons = [
                "the run is under way (lifecycle="
                f"{engine.lifecycle}, scored trials answered="
                f"{engine.total_trials}, trial in flight="
                f"{engine.trial_in_flight()}): N, label mapping, calibration "
                "and the pipelines are frozen until the run finishes",
                "seal the run for an auditable partial export, or reset it to "
                "plan a new one",
            ]
            return jsonify({
                "error": (
                    "configuration is frozen during a run: the plan is fixed "
                    "before the answers"
                ),
                "reasons": reasons,
                "lifecycle": engine.lifecycle,
                "sealed": engine.lifecycle == "aborted",
                "complete": engine.lifecycle == "completed",
                "can_accept_answers": engine.can_accept_answers(),
                "gates": engine.gates(),
                "config": _config_payload(),
            }), 409

        was_fingerprint = session.prepared_config_fingerprint

        # Sample rate mode
        sr_mode = data.get("sample_rate_mode")
        if sr_mode:
            session.sample_rate_mode = SampleRateMode(sr_mode)

        # Pre-registered trial count: fixed BEFORE the first answer.
        if "planned_trials" in data:
            pt = data.get("planned_trials")
            session.planned_trials = int(pt) if pt else None

        # Label mapping mode
        lm_mode = data.get("label_mapping_mode")
        if lm_mode:
            session.label_mapping_mode = LabelMappingMode(lm_mode)

        # Calibration (canary) trial gate.
        if "canary_enabled" in data:
            session.canary_enabled = bool(data.get("canary_enabled"))

        # Pipeline stages A
        stages_a_data = data.get("stages_a", [])
        session.stages_a = [
            PipelineStageConfig(s["codec_id"], s["bitrate_kbps"])
            for s in stages_a_data
        ]

        # Pipeline stages B
        stages_b_data = data.get("stages_b", [])
        session.stages_b = [
            PipelineStageConfig(s["codec_id"], s["bitrate_kbps"])
            for s in stages_b_data
        ]

        # Bandwidth limits
        session.bandwidth_limit_a_enabled = data.get("bandwidth_limit_a_enabled", False)
        session.bandwidth_limit_a_hz = data.get("bandwidth_limit_a_hz")
        session.bandwidth_limit_b_enabled = data.get("bandwidth_limit_b_enabled", False)
        session.bandwidth_limit_b_hz = data.get("bandwidth_limit_b_hz")

        # A render-affecting change makes the prepared pair stale. Only a pair
        # that was rendered from a KNOWN configuration can be judged stale, so
        # a scripted session that only set paths keeps working.
        invalidated = False
        if was_fingerprint and session.prepared_pair_ready():
            new_fingerprint = session.config_fingerprint()
            if new_fingerprint != was_fingerprint:
                session.invalidate_prepared_audio(
                    "configuration changed after the audio was rendered: the "
                    "delivered pair no longer matches the pipeline it is being "
                    "compared under"
                )
                invalidated = True

        # The calibration switch selects EXTRA material (the pristine sample
        # and its deliberately degraded copy); it does not change the delivered
        # A/B bytes, so toggling it must not force a re-render. It does need
        # that material to exist: a session rendered with calibration off has no
        # calibration pair, and switching the gate on must not silently promise
        # a gate that has nothing to measure.
        if session.canary_enabled and not (
            session.canary_pristine_path and session.canary_degraded_path
        ):
            session.canary_available = False
        elif not session.canary_enabled:
            # No gate requested: the flag means "nothing to gate".
            session.canary_available = True

        # Label mode / planned N changed under us: rebuild the engine so the
        # pre-registration and blinding reflect the new configuration.
        session.configure_engine()

        # Say what the new configuration can and cannot do with what is already
        # rendered, so the browser never starts a trial on audio that has to be
        # produced first.
        notes = []
        if invalidated:
            notes.append(
                "the rendered pair was dropped: it does not match this "
                "configuration. Run Preprocess & Validate before any trial."
            )
        if session.canary_enabled and not session.canary_available:
            notes.append(
                "calibration trial requested, but there is no usable "
                "calibration pair for this configuration: the gate reports "
                "'unavailable' and does not gate the run. Re-run Preprocess & "
                "Validate with the calibration trial enabled to measure it."
            )

    return jsonify({
        "status": "ok",
        "audio_invalidated": not session.prepared_pair_ready(),
        "canary_available": bool(session.canary_available),
        "prepared_audio_id": (session.prepared_audio or {}).get("id"),
        "config_fingerprint": session.config_fingerprint(),
        "lifecycle": session.engine.lifecycle,
        "can_accept_answers": session.engine.can_accept_answers(),
        "notes": notes,
    })


def _config_payload() -> Dict[str, Any]:
    """The session configuration, in the shape both GET and the 409 reply use."""
    return {
        "sample_rate_mode": session.sample_rate_mode.value,
        "label_mapping_mode": session.label_mapping_mode.value,
        "planned_trials": session.planned_trials,
        "canary_enabled": bool(session.canary_enabled),
        "canary_available": bool(session.canary_available),
        "stages_a": [
            {"codec_id": s.codec_id, "bitrate_kbps": s.bitrate_kbps}
            for s in session.stages_a
        ],
        "stages_b": [
            {"codec_id": s.codec_id, "bitrate_kbps": s.bitrate_kbps}
            for s in session.stages_b
        ],
        "bandwidth_limit_a_enabled": session.bandwidth_limit_a_enabled,
        "bandwidth_limit_a_hz": session.bandwidth_limit_a_hz,
        "bandwidth_limit_b_enabled": session.bandwidth_limit_b_enabled,
        "bandwidth_limit_b_hz": session.bandwidth_limit_b_hz,
    }


@app.route("/api/config", methods=["GET"])
def get_config():
    """Return current configuration, and whether it is frozen."""
    with session.lock:
        payload = _config_payload()
        payload["lifecycle"] = session.engine.lifecycle
        payload["config_frozen"] = session.engine.run_under_way()
        payload["can_accept_answers"] = session.engine.can_accept_answers()
        return jsonify(payload)


# ── API: Preprocessing ───────────────────────────────────────────────────────

@app.route("/api/preprocess", methods=["POST"])
def start_preprocess():
    """Start audio preprocessing in a background thread."""
    with session.lock:
        if not session.input_file_path:
            return jsonify({"error": "no input file uploaded"}), 400

        if session.preprocess_running:
            return jsonify({"error": "preprocessing already running"}), 409

        if session.engine.run_under_way():
            return jsonify({
                "error": "reset the frozen run explicitly before preparing new audio",
                "lifecycle": session.engine.lifecycle,
            }), 409

        session.invalidate_prepared_audio("audio preparation in progress")
        session.preprocess_running = True
        session.preprocess_cancelled = False
        session.preprocess_phase = "starting"
        session.preprocess_progress = 0.0
        session.preprocess_message = "Starting preprocessing..."
        session.preprocess_ready = False
        session.preprocess_error = None
        # A new pair invalidates the old gates: the null decision describes the
        # pair, and a stale decision must not gate a different pair.
        session.null_gate = {}
        session.canary_check = {}
        session.canary_available = True
        session.canary_pristine_path = None
        session.canary_degraded_path = None
        session.engine.set_null_gate(None)

    t = threading.Thread(target=_run_preprocess, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/preprocess/status", methods=["GET"])
def preprocess_status():
    """Return current preprocessing status."""
    with session.lock:
        return jsonify({
            "running": session.preprocess_running,
            "phase": session.preprocess_phase,
            "progress_pct": session.preprocess_progress,
            "message": session.preprocess_message,
            "ready": session.preprocess_ready,
            "error": session.preprocess_error,
        })


@app.route("/api/preprocess/cancel", methods=["POST"])
def cancel_preprocess():
    """Cancel running preprocessing."""
    with session.lock:
        session.preprocess_cancelled = True
        pipeline = session.active_pipeline
    if pipeline is not None:
        pipeline.request_cancel()
    return jsonify({"status": "cancelled"})


def _set_phase(phase: str, progress: float, message: str):
    """Thread-safe status update."""
    with session.lock:
        session.preprocess_phase = phase
        session.preprocess_progress = progress
        session.preprocess_message = message


def _is_cancelled() -> bool:
    with session.lock:
        return session.preprocess_cancelled


def _run_preprocess():
    """Adapt the shared headless preparation pipeline to the web session.

    DSP dependencies are lazy: API/statistics-only deployments can import the
    web app without Qt or the audio extras. Rendering requires the audio extras.
    """
    pipeline = None
    work_dir = None
    published = False
    try:
        from app.audio_pipeline import AudioPipeline, PipelineCancelled
        from app.models import PipelineStageConfig as AudioStage, SampleRateMode as AudioRate

        with session.lock:
            input_path = session.input_file_path
            input_name = session.input_file_name
            fingerprint = session.config_fingerprint()
            mode = AudioRate(session.sample_rate_mode.value)
            stages_a = [AudioStage(s.codec_id, s.bitrate_kbps) for s in session.stages_a]
            stages_b = [AudioStage(s.codec_id, s.bitrate_kbps) for s in session.stages_b]
            canary_enabled = session.canary_enabled
            bandwidth = {
                "bandwidth_limit_a_enabled": session.bandwidth_limit_a_enabled,
                "bandwidth_limit_a_hz": session.bandwidth_limit_a_hz,
                "bandwidth_limit_b_enabled": session.bandwidth_limit_b_enabled,
                "bandwidth_limit_b_hz": session.bandwidth_limit_b_hz,
            }
            # Neither a failed render nor cancellation may leave old playable
            # audio whose identity/gates now describe another configuration.
            session.invalidate_prepared_audio("audio preparation in progress")
        if not input_path or not Path(input_path).is_file():
            raise ValueError("Input file not found")
        if not stages_a or not stages_b:
            raise ValueError("Both pipelines require at least one stage")

        class WebPipeline(AudioPipeline):
            def _reset_cancel(self):
                super()._reset_cancel()
                if _is_cancelled():
                    raise PipelineCancelled("Operation cancelled by user")

            def _run(self, args):
                if _is_cancelled():
                    raise PipelineCancelled("Operation cancelled by user")
                return super()._run(args)

        pipeline = WebPipeline(ffmpeg_bin=find_ffmpeg(), ffprobe_bin=find_ffprobe())
        with session.lock:
            session.active_pipeline = pipeline
        _set_phase("preparation", 10, "Preparing aligned, level-matched audio...")
        work_dir = tempfile.mkdtemp(prefix="abx_web_")
        prepared, _, _ = pipeline.prepare_session(
            input_path=input_path,
            codec_a_id=stages_a[-1].codec_id, bitrate_a_kbps=stages_a[-1].bitrate_kbps,
            codec_b_id=stages_b[-1].codec_id, bitrate_b_kbps=stages_b[-1].bitrate_kbps,
            mode=mode, work_dir=work_dir,
            pipeline_stages_a=stages_a, pipeline_stages_b=stages_b,
            canary_enabled=canary_enabled, **bandwidth,
        )
        audio_a_norm, audio_b_norm = prepared.track_a.pcm_path, prepared.track_b.pcm_path
        validation = prepared.validation
        loudness = validation.loudness.to_dict()
        depth = validation.null_depth_db
        from dataclasses import asdict

        metadata = {
            "input_file": input_name or "",
            "original_sample_rate": prepared.original_sample_rate,
            "target_sample_rate": prepared.target_sample_rate,
            "sample_rate_mode": prepared.mode.value,
            "resample_engine_used": prepared.resample_engine_used,
            "duration_seconds": prepared.duration_seconds,
            "channels": prepared.channels,
            "channel_convention": prepared.disclosure["channel_convention"],
            "preparation_order": prepared.disclosure["preparation_order"],
            "amplitude_gate": prepared.disclosure["amplitude_gate"],
            "disclosure": prepared.disclosure,
            "stages_a_count": prepared.pipeline_stage_count_a,
            "stages_b_count": prepared.pipeline_stage_count_b,
            **bandwidth,
            "loudness": loudness,
            # Preserve legacy UI fields' PRE-normalization meaning. The full
            # measured report above is the citation surface (not rounded).
            "loudness_a_lufs": loudness["pre_lufs_a"],
            "loudness_b_lufs": loudness["pre_lufs_b"],
            "loudness_diff_db": loudness["pre_delta_db"],
            "loudness_gain_a_db": loudness["gain_a_db"],
            "loudness_gain_b_db": loudness["gain_b_db"],
            "loudness_is_pre_normalization": True,
            "alignment_lag_samples": validation.alignment_lag_samples,
            "alignment_confidence": validation.alignment_confidence,
            "null_depth_db_pre_loudness": (
                validation.null_depth_db_pre_loudness
                if math.isfinite(validation.null_depth_db_pre_loudness) else None
            ),
            "null_depth_db": depth if math.isfinite(depth) else None,
            "null_depth_is_perfect": depth == float("-inf"),
            "validation_notes": validation.notes,
            "stages_a_details": [asdict(s) for s in prepared.track_a.stages],
            "stages_b_details": [asdict(s) for s in prepared.track_b.stages],
            "expected_identical_pair": prepared.null_gate["applicable"],
            "null_gate": prepared.null_gate,
            "canary_requested": canary_enabled,
            "canary_available": bool(not canary_enabled or prepared.canary.get("usable")),
            "canary": prepared.canary,
            "canary_trial_index": 1 if canary_enabled else None,
        }
        with session.lock:
            if session.preprocess_cancelled:
                raise PipelineCancelled("Operation cancelled by user")
            if (session.config_fingerprint() != fingerprint
                    or session.canary_enabled != canary_enabled):
                raise ValueError("Configuration changed during preparation; render again")
            identity = {
                "id": _prepared_audio_id(audio_a_norm, audio_b_norm, fingerprint),
                "a_sha256": _file_sha256(audio_a_norm),
                "b_sha256": _file_sha256(audio_b_norm),
                "config_fingerprint": fingerprint,
                "target_sample_rate": prepared.target_sample_rate,
                "channels": prepared.channels,
                "channel_convention": prepared.disclosure["channel_convention"],
            }
            session.audio_a_path, session.audio_b_path = audio_a_norm, audio_b_norm
            session.null_gate = prepared.null_gate
            session.canary_check = prepared.canary
            session.canary_pristine_path = prepared.canary_pristine_path
            session.canary_degraded_path = prepared.canary_degraded_path
            session.canary_available = metadata["canary_available"]
            session.prepared_audio = identity
            session.prepared_config_fingerprint = fingerprint
            session.stale_audio_reason = None
            metadata["prepared_audio"] = identity
            session.session_metadata = metadata
            session.configure_engine()
            session.preprocess_ready = True
            published = True
        _set_phase("complete", 100, "Preprocessing complete!")
    except Exception as e:
        with session.lock:
            session.preprocess_ready = False
            session.preprocess_error = str(e)
            session.preprocess_phase = "cancelled" if session.preprocess_cancelled else "error"
            session.preprocess_message = str(e)
    finally:
        if work_dir and not published:
            # Delete only the exact unpublished directory allocated above,
            # resolved under the platform temp root with our owned prefix.
            target = Path(work_dir).resolve()
            if (target.parent == Path(tempfile.gettempdir()).resolve()
                    and target.name.startswith("abx_web_")):
                shutil.rmtree(target, ignore_errors=True)
        with session.lock:
            session.active_pipeline = None
            session.preprocess_running = False


# ── FFmpeg Helper Functions ──────────────────────────────────────────────────

def _file_sha256(path: str) -> str:
    """Digest of a rendered file, so a run can name the bytes it used."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _prepared_audio_id(a_path: str, b_path: str, fingerprint: str) -> str:
    """Stable identity of the rendered pair: both digests + the config that
    produced them. Changes when the audio changes, and when the configuration
    that produced it changes -- which is exactly when the browser's decoded
    buffers and any run built on them stop being valid."""
    digest = hashlib.sha256(
        f"{_file_sha256(a_path)}|{_file_sha256(b_path)}|{fingerprint}".encode()
    )
    return "sha256:" + digest.hexdigest()[:32]


def probe_sample_rate(path: str) -> int:
    cmd = [find_ffprobe(), "-v", "quiet", "-print_format", "json",
           "-show_streams", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(r.stdout)
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return int(s.get("sample_rate", 48000))
    return 48000


def probe_duration(path: str) -> float:
    cmd = [find_ffprobe(), "-v", "quiet", "-print_format", "json",
           "-show_streams", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(r.stdout)
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            try:
                return float(s.get("duration", 0))
            except (ValueError, TypeError):
                pass
    return 0.0


# ── API: Audio Streaming for Playback ────────────────────────────────────────

@app.route("/api/audio/<side>", methods=["GET"])
def stream_audio(side: str):
    """Stream processed audio A, B, or X for playback.

    'x' is resolved SERVER-SIDE from the trial token. The client asks for
    "the X audio" and receives bytes; it never learns which side that was.

    During a calibration (canary) trial the same three names resolve to the
    calibration pair (pristine / degraded / coin-selected) -- the client's
    request and the blinding are unchanged.
    """
    with session.lock:
        if side not in ("a", "b", "x"):
            return jsonify({"error": "side must be 'a', 'b' or 'x'"}), 400
        path = session.side_audio_path(
            side, request.args.get("token", type=str)
        )
        if not path or not Path(path).exists():
            return jsonify({"error": f"audio {side} not found"}), 404

    # Convert to WAV for streaming (already is WAV, but ensure)
    return send_file(
        path,
        mimetype="audio/wav",
        as_attachment=False,
        download_name=f"abx_{side}.wav",
    )


@app.route("/api/audio/<side>/segment", methods=["GET"])
def stream_audio_segment(side: str):
    """Stream a segment of audio for loop playback."""
    start = request.args.get("start", type=float, default=0)
    end = request.args.get("end", type=float, default=None)

    with session.lock:
        if side not in ("a", "b", "x"):
            return jsonify({"error": "side must be 'a', 'b' or 'x'"}), 400
        path = session.side_audio_path(
            side, request.args.get("token", type=str)
        )
        if not path or not Path(path).exists():
            return jsonify({"error": f"audio {side} not found"}), 404

    # Use ffmpeg to extract segment
    cmd = [find_ffmpeg(), "-y", "-i", path,
           "-ss", str(start)]
    if end:
        cmd.extend(["-to", str(end)])
    cmd.extend(["-acodec", "pcm_s16le", "-f", "wav", "-"])

    r = subprocess.run(cmd, capture_output=True)
    return send_file(
        io.BytesIO(r.stdout),
        mimetype="audio/wav",
    )


@app.route("/api/audio/info", methods=["GET"])
def audio_info():
    """Return audio metadata (duration, sample rate, buffer identity)."""
    with session.lock:
        if not session.audio_a_path:
            return jsonify({"error": "no audio processed"}), 404

        sr = probe_sample_rate(session.audio_a_path)
        dur = probe_duration(session.audio_a_path)

        # Check if A and B are identical (same codec config → same processing pipeline)
        # This happens when both sides use noop passthrough, or identical codec chains
        # with the same bandwidth limit settings. When identical, the frontend can share
        # a single AudioBuffer for all three sides (A, B, X) for zero-perception difference.
        buffers_identical = False
        meta = session.session_metadata
        cat = codec_catalog()
        if meta:
            stages_a_details = meta.get("stages_a_details", [])
            stages_b_details = meta.get("stages_b_details", [])
            # If both pipelines have identical stage results, the audio is identical
            if stages_a_details and stages_b_details:
                a_codecs = [s["codec_id"] for s in stages_a_details]
                b_codecs = [s["codec_id"] for s in stages_b_details]
                a_bitrates = [s.get("bitrate_kbps", 0) for s in stages_a_details]
                b_bitrates = [s.get("bitrate_kbps", 0) for s in stages_b_details]
                if a_codecs == b_codecs and a_bitrates == b_bitrates:
                    # Check bandwidth limits are also identical
                    bw_a_enabled = meta.get("bandwidth_limit_a_enabled", False)
                    bw_b_enabled = meta.get("bandwidth_limit_b_enabled", False)
                    bw_a_hz = meta.get("bandwidth_limit_a_hz")
                    bw_b_hz = meta.get("bandwidth_limit_b_hz")
                    bw_identical = (bw_a_enabled == bw_b_enabled and
                                    (not bw_a_enabled or bw_a_hz == bw_b_hz))
                    if bw_identical:
                        buffers_identical = True

    return jsonify({
        "sample_rate": sr,
        "duration_seconds": dur,
        "buffers_identical": buffers_identical,
        # What the browser's decoded-buffer cache is keyed on. A/B are served
        # per trial through that trial's display mapping, so the client can
        # reuse buffers only while BOTH the mapping and the rendered pair are
        # unchanged; these two fields are how it can tell.
        "prepared_audio_id": (session.prepared_audio or {}).get("id"),
        "display_mapping": session.engine.pending_mapping,
        "trial_index": (
            session.engine.pending_plan.trial_index
            if session.engine.pending_plan is not None
            else None
        ),
        "mapping_applied_server_side": True,
    })


# ── API: ABX Trials ──────────────────────────────────────────────────────────

def _canary_block() -> Dict[str, Any]:
    """Engine's canary state + the measured calibration material (core.report)."""
    return canary_report_block(session.engine.gates()["canary"], session.canary_check)


def _validity_lines() -> List[str]:
    """Same evidence block as the desktop panel, same wording (core.report)."""
    meta = session.session_metadata or {}
    stats = session.engine.stats()
    return validity_lines(
        null_depth_db=(
            float("-inf")
            if meta.get("null_depth_is_perfect")
            else meta.get("null_depth_db")
        ),
        alignment_lag_samples=int(meta.get("alignment_lag_samples", 0)),
        alignment_confidence=float(meta.get("alignment_confidence", 0.0)),
        loudness=meta.get("loudness"),
        disclosure=meta.get("disclosure", {}),
        notes=list(meta.get("validation_notes", [])),
        stats={
            "planned_trials": stats["planned_trials"],
            "is_final": stats["is_final"],
            "required_correct": stats["required_correct"],
            "ability_ci95": stats["ability_ci95"],
            # Lifecycle reaches the shared report wording, so the panel, the
            # export and the desktop all say SEALED, never FINAL, for a run
            # that was stopped early.
            "lifecycle": stats["lifecycle"],
            "sealed": stats["sealed"],
            "complete": stats["complete"],
            "total_trials": stats["total_trials"],
        },
        commitment=session.engine.session_commitment,
        canary=_canary_block(),
        null_gate=session.engine.null_gate_block(),
    )


def _trial_dict(trial) -> Dict[str, Any]:
    """core.abx Trial -> the legacy dict shape the UI/export already expects.

    `kind` separates the calibration trial from the scored ones: the canary is
    exported (it is part of the committed sequence and of the validity
    evidence) but it is never counted in the statistics.
    """
    return {
        "trial_index": trial.trial_index,
        "x_is": trial.x_source,
        "answer": trial.answer_display,
        "correct": trial.correct,
        "timestamp_utc": trial.timestamp_utc,
        "mapping_a_to": trial.display_mapping.get("A"),
        "mapping_b_to": trial.display_mapping.get("B"),
        "x_source": trial.x_source,
        "answer_source": trial.answer_source,
        "mapping_changed_for_next_trial": None,
        "kind": trial.kind,
    }


def _lifecycle_replay_payload() -> Dict[str, Any]:
    """What to tell a client that asked for a trial the run can no longer give.

    One shape for all three refusals (gate closed, N reached, sealed) so the UI
    shows the engine's own reasons and knows which action it is being offered:
    fix the chain, export a finished run, or start a new one.
    """
    engine = session.engine
    return {
        "error": "this run accepts no further trials",
        "reasons": engine.why_no_answers() or engine.gate_reasons(),
        "lifecycle": engine.lifecycle,
        "complete": engine.lifecycle == "completed",
        "sealed": engine.lifecycle == "aborted",
        "can_accept_answers": engine.can_accept_answers(),
        "gates": engine.gates(),
        "stats": session.stats(),
    }


@app.route("/api/trial/start", methods=["POST"])
def start_trial():
    """Start a new ABX trial.

    409 when the engine refuses a trial: a validity gate is shut, the
    pre-registered N has been answered, the run was sealed, or there is no
    prepared pair to run on. The engine (not this route) owns that decision, so
    the desktop and the web refuse identically, and a client cannot answer its
    way past N by scripting the API.
    """
    with session.lock:
        try:
            info = session.new_trial()
        except TrialGateClosed as exc:
            payload = _lifecycle_replay_payload()
            payload["error"] = str(exc)
            payload["reasons"] = list(exc.reasons)
            return jsonify(payload), 409
        except StudyLifecycleError as exc:
            payload = _lifecycle_replay_payload()
            payload["error"] = str(exc)
            payload["reasons"] = list(exc.reasons) or payload["reasons"]
            return jsonify(payload), 409
    return jsonify(info)


@app.route("/api/trial/answer", methods=["POST"])
def submit_answer():
    """Submit answer for current trial."""
    data = request.get_json()
    if not data or "answer" not in data:
        return jsonify({"error": "missing 'answer' field"}), 400

    with session.lock:
        try:
            trial = session.submit_answer(data["answer"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except StudyLifecycleError as exc:
            # No trial in flight because the run is over: say so, in the
            # engine's words, rather than pretending an answer is possible.
            payload = _lifecycle_replay_payload()
            payload["error"] = str(exc)
            payload["reasons"] = list(exc.reasons) or payload["reasons"]
            return jsonify(payload), 409
        except RuntimeError as exc:
            # Core contract: answering with no trial in flight (e.g. after a
            # gated 409 on start) is a lifecycle refusal, not a crash. A
            # scripted client must never see an unstructured 500 here.
            payload = _lifecycle_replay_payload()
            payload["error"] = str(exc)
            payload["reasons"] = payload["reasons"] or ["no trial in progress"]
            return jsonify(payload), 409

    return jsonify({
        # Legacy row shape plus `kind`: the answer feedback names the trial as
        # calibration when it was one, and the run's void state is in `stats`.
        "trial": trial_result_to_dict(trial),
        "stats": session.stats(),
    })


@app.route("/api/trial/seal", methods=["POST"])
def seal_run():
    """End the run now, on purpose.

    An early export has to stop the run to be honest: the secret derives X for
    every trial that has not been answered, so while answers are due the export
    is REDACTED. Sealing closes the run, releases the key, and labels the run
    sealed -- a partial run that is auditable and explicitly not a result. The
    other way to get a verifiable export is to finish the pre-registered N.
    """
    data = request.get_json(silent=True) or {}
    reason = data.get("reason") or "sealed by the tester"
    with session.lock:
        result = session.seal_run(reason)
    return jsonify(result)


@app.route("/api/trial/stats", methods=["GET"])
def get_stats():
    """Return current trial statistics."""
    with session.lock:
        return jsonify(session.stats())


@app.route("/api/trial/reset", methods=["POST"])
def reset_trials():
    """Reset all trials and score."""
    with session.lock:
        session.reset_trials()
    return jsonify({"status": "reset"})


@app.route("/api/trials", methods=["GET"])
def get_trials():
    """Return all trials (calibration included, flagged by `kind`)."""
    with session.lock:
        return jsonify({
            "trials": [_trial_dict(t) for t in session.engine.all_trials()],
            "stats": session.stats(),
        })


# ── API: Diagnostics ─────────────────────────────────────────────────────────

@app.route("/api/diagnostics", methods=["GET"])
def get_diagnostics():
    """Return full diagnostics data."""
    with session.lock:
        gates = session.engine.gates()
        return jsonify({
            "metadata": session.session_metadata,
            "trials": [_trial_dict(t) for t in session.engine.all_trials()],
            "stats": session.stats(),
            "display_mapping": session.engine.pending_mapping,
            "validity_lines": _validity_lines(),
            # Phase 1 gates: the desktop panel, this payload and the JSON
            # export all read the same blocks from core.
            "canary": _canary_block(),
            "null_gate": session.engine.null_gate_block(),
            "gates": gates,
            "run_valid": session.engine.run_valid,
            "current_trial_kind": session.engine.current_trial_kind,
            # Lifecycle of the run, plus what it means for the client: no
            # further trials, redacted export, sealed-not-final.
            "lifecycle": session.engine.lifecycle,
            "can_accept_answers": session.engine.can_accept_answers(),
            "run_closed_because": session.engine.why_no_answers(),
            "config_frozen": session.engine.run_under_way(),
            "prepared_audio": session.prepared_audio,
            "run_lock": session.engine.run_lock,
            # legacy keys the existing UI reads
            "mapping_a_to": (
                session.engine.pending_mapping or {}).get("A"),
            "mapping_b_to": (
                session.engine.pending_mapping or {}).get("B"),
        })


# ── API: Export ───────────────────────────────────────────────────────────────

@app.route("/api/export/json", methods=["GET"])
def export_json():
    """Export results as JSON file.

    Always available, including for a void run and a run that stopped early --
    a failed calibration is a result worth publishing, and hiding it is how
    failed runs get quietly dropped. What the export may NOT do is present a
    run that is not over as if it were:

      * while answers are still due, `verifiability` is a REDACTED snapshot:
        the commitment and the answered rows are there, the secret is not,
        because it derives X for every trial still to come.
      * a sealed run says so (`sealed`, `incomplete`) and is never `complete`.
      * only a run that reached its pre-registered N is `complete`, and only
        then is a pass claimable (`stats.significant_verdict`).
    """
    with session.lock:
        gates = session.engine.gates()
        stats = session.stats()
        reveal = session.engine.reveal()
        data = {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "session": {
                **session.session_metadata,
                "export_schema": "abx-session/1",
                "tool": "codec-abx-tester/web",
                "disclosure": session.session_metadata.get("disclosure", {}),
                "verifiability": reveal,
                "canary": _canary_block(),
                "null_gate": session.engine.null_gate_block(),
                "gates": gates,
                "run_valid": session.engine.run_valid,
                # Lifecycle of the run this file describes.
                "lifecycle": session.engine.lifecycle,
                "complete": stats["complete"],
                "sealed": stats["sealed"],
                "sealed_reason": stats["sealed_reason"],
                "incomplete": not stats["complete"],
                "can_accept_answers": stats["can_accept_answers"],
                "export_is_redacted": bool(reveal.get("redacted")),
                "run_closed_because": stats["run_closed_because"],
                "prepared_audio": session.prepared_audio,
                "run_lock": session.engine.run_lock,
                "audio_stale_reason": session.stale_audio_reason,
            },
            # Calibration included: it is part of the committed X sequence, so
            # a third party verifying this file sees the same indices.
            "trials": [_trial_dict(t) for t in session.engine.all_trials()],
            "stats": stats,
            "validity_lines": _validity_lines(),
        }

    return jsonify(data), 200, {"Content-Disposition": "attachment; filename=abx_results.json"}


@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    """Export trial results as CSV file.

    The rows go through `_trial_dict`, and the header is derived from its keys.
    The previous version passed `asdict(core.Trial)` to a hardcoded 9-column
    header, which raised `dict contains fields not in fieldnames` on every
    export (same class of bug as the desktop CSV crash Phase 0 fixed).
    """
    with session.lock:
        rows = [_trial_dict(t) for t in session.engine.all_trials()]

    # Header derived from the row shape, with a fixed fallback so an empty
    # session still exports a readable header.
    fieldnames = list(rows[0].keys()) if rows else [
        "trial_index", "x_is", "answer", "correct", "timestamp_utc",
        "mapping_a_to", "mapping_b_to", "x_source", "answer_source",
        "mapping_changed_for_next_trial", "kind",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: ("" if row.get(name) is None else row.get(name))
                         for name in fieldnames})

    csv_data = output.getvalue().encode("utf-8")
    return send_file(
        io.BytesIO(csv_data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="abx_trials.csv",
    )


# ── API: Session Info ────────────────────────────────────────────────────────

@app.route("/api/session/info", methods=["GET"])
def session_info():
    """Return basic session information, including the state of both gates."""
    with session.lock:
        gates = session.engine.gates()
        return jsonify({
            "has_input": session.input_file_path is not None,
            "input_filename": session.input_file_name,
            "preprocess_ready": session.preprocess_ready,
            "prepared_pair_ready": session.prepared_pair_ready(),
            "prepared_audio_id": (session.prepared_audio or {}).get("id"),
            "audio_stale_reason": session.stale_audio_reason,
            "label_mapping_mode": session.label_mapping_mode.value,
            "canary_requested": bool(session.canary_enabled),
            "canary_available": bool(session.canary_available),
            "canary": _canary_block(),
            "null_gate": session.engine.null_gate_block(),
            "gates_open": gates["open"],
            "run_valid": session.engine.run_valid,
            # Lifecycle: the UI reads this to stop advancing, to offer "seal",
            # and to know when an export is redacted rather than verifiable.
            "lifecycle": session.engine.lifecycle,
            "can_accept_answers": session.engine.can_accept_answers(),
            "run_closed_because": session.engine.why_no_answers(),
            "config_frozen": session.engine.run_under_way(),
        })


# ── Development Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug,
    )
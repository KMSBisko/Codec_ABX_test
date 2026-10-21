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
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
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
    TrialGateClosed,
)
from core.alignment import (
    align_pair,
    canary_audio_check,
    estimate_lag,
    identical_pipeline_config,
    null_depth_db,
    null_gate_decision,
)
from core.canary import degrade_for_canary
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
        self.pending_plans.clear()

    def reset_trials(self):
        """Reset ABX trial state."""
        self.engine.reset()
        self.configure_engine()

    def new_trial(self) -> Dict[str, Any]:
        """Start a new trial. Returns the CLIENT-SAFE plan only.

        Raises core.abx.TrialGateClosed when a validity gate is shut; the
        endpoint turns that into a 409 so no shell can offer trials the other
        would refuse.
        """
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

    def side_audio_path(
        self, side: str, token: Optional[str] = None
    ) -> Optional[str]:
        """Server-side resolution of a/b/x -> an audio path.

        During a calibration trial the same three names resolve to the
        calibration pair (pristine vs grossly degraded) instead of the codec
        pair. The client asks for "A", "B" and "X" exactly as always, so the
        blinding is unchanged: it never learns which of the two X was.
        """
        plan = self._plan_for(token)
        if plan is not None and plan.kind == TRIAL_KIND_CANARY:
            if side == "a":
                return self.canary_pristine_path
            if side == "b":
                return self.canary_degraded_path
            if side == "x":
                return (
                    self.canary_pristine_path
                    if plan.x_source == "A"
                    else self.canary_degraded_path
                )
            return None

        if side == "a":
            return self.audio_a_path
        if side == "b":
            return self.audio_b_path
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
    session.input_file_name = f.filename
    session.input_file_path = os.path.join(UPLOAD_FOLDER, safe_name)
    f.save(session.input_file_path)

    return jsonify({
        "filename": f.filename,
        "saved_as": safe_name,
    })


# ── API: Configuration ───────────────────────────────────────────────────────

@app.route("/api/config", methods=["POST"])
def set_config():
    """Update session configuration."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "no JSON body"}), 400

    with session.lock:
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

        # Label mode / planned N changed under us: rebuild the engine so the
        # pre-registration and blinding reflect the new configuration.
        session.configure_engine()

        # Bandwidth limits
        session.bandwidth_limit_a_enabled = data.get("bandwidth_limit_a_enabled", False)
        session.bandwidth_limit_a_hz = data.get("bandwidth_limit_a_hz")
        session.bandwidth_limit_b_enabled = data.get("bandwidth_limit_b_enabled", False)
        session.bandwidth_limit_b_hz = data.get("bandwidth_limit_b_hz")

    return jsonify({"status": "ok"})


@app.route("/api/config", methods=["GET"])
def get_config():
    """Return current configuration."""
    with session.lock:
        return jsonify({
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
        })


# ── API: Preprocessing ───────────────────────────────────────────────────────

@app.route("/api/preprocess", methods=["POST"])
def start_preprocess():
    """Start audio preprocessing in a background thread."""
    with session.lock:
        if not session.input_file_path:
            return jsonify({"error": "no input file uploaded"}), 400

        if session.preprocess_running:
            return jsonify({"error": "preprocessing already running"}), 409

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
    """Background preprocessing pipeline."""
    try:
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        work_dir = tempfile.mkdtemp(prefix="abx_web_")

        input_path = session.input_file_path
        if not input_path or not Path(input_path).exists():
            raise ValueError("Input file not found")

        # ── Phase 1: Input Prep (FLAC -> WAV if needed) ────────────────────
        _set_phase("input_prep", 5, "Preparing input audio...")
        working_wav = os.path.join(work_dir, "input.wav")
        ext = Path(input_path).suffix.lower()

        if ext == ".flac":
            cmd = [ffmpeg, "-y", "-i", input_path, "-acodec", "pcm_s16le", "-ar", "48000", working_wav]
        else:
            # WAV: normalize to pcm_s16le
            cmd = [ffmpeg, "-y", "-i", input_path, "-acodec", "pcm_s16le", working_wav]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and not _is_cancelled():
            raise RuntimeError(f"Input prep failed: {r.stderr[:200]}")

        # ── Phase 1.5: Probe input for sample rate ─────────────────────────
        _set_phase("probe", 10, "Probing audio properties...")
        probe_cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", working_wav]
        r = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_info = json.loads(r.stdout)
        audio_stream = None
        for stream in probe_info.get("streams", []):
            if stream.get("codec_type") == "audio":
                audio_stream = stream
                break

        original_sr = int(audio_stream.get("sample_rate", 48000))
        duration_str = audio_stream.get("duration", "0")
        try:
            duration = float(duration_str)
        except (ValueError, TypeError):
            duration = 0.0
        channels = int(audio_stream.get("channels", 2))

        # Determine target sample rate
        if session.sample_rate_mode == SampleRateMode.FORCE_48K:
            target_sr = 48000
        else:
            target_sr = original_sr

        resample_engine_used = "none"

        # ── Phase 2: Resample (if FORCE_48K and needed) ────────────────────
        if session.sample_rate_mode == SampleRateMode.FORCE_48K and original_sr != 48000:
            _set_phase("resample", 15, f"Resampling {original_sr} -> 48000 Hz...")
            resampled_wav = os.path.join(work_dir, "input_resampled.wav")
            cmd = [ffmpeg, "-y", "-i", working_wav, "-acodec", "pcm_s16le",
                   "-ar", "48000", "-af", "aresample=resampler=soxr", resampled_wav]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 and not _is_cancelled():
                # Fallback without soxr
                cmd = [ffmpeg, "-y", "-i", working_wav, "-acodec", "pcm_s16le", "-ar", "48000", resampled_wav]
                r = subprocess.run(cmd, capture_output=True, text=True)
            working_wav = resampled_wav
            target_sr = 48000
            resample_engine_used = "soxr"

        # ── Phase 3: Codec Pipeline (Side A & B) ───────────────────────────
        catalog = codec_catalog()

        def run_pipeline(stages, side_label, base_progress):
            _set_phase(f"codec_{side_label}", base_progress, f"Processing Side {side_label} pipeline...")
            current_input = working_wav
            stage_results = []

            for idx, stage_cfg in enumerate(stages):
                if _is_cancelled():
                    break

                profile = catalog.get(stage_cfg.codec_id)
                if not profile:
                    raise ValueError(f"Unknown codec: {stage_cfg.codec_id}")

                # No-op passthrough
                if profile.pipeline_noop:
                    stage_results.append({
                        "stage_index": idx,
                        "codec_id": profile.codec_id,
                        "codec_name": profile.display_name,
                        "bitrate_kbps": 0,
                    })
                    continue

                encoded_path = os.path.join(work_dir, f"{side_label}_stage{idx + 1}_enc.{profile.container_ext}")
                decoded_path = os.path.join(work_dir, f"{side_label}_stage{idx + 1}_dec.wav")

                # Encode
                enc_cmd = [ffmpeg, "-y", "-i", current_input]
                if profile.ffmpeg_extra_args:
                    enc_cmd.extend(profile.ffmpeg_extra_args)
                enc_cmd.extend([
                    "-acodec", profile.ffmpeg_encoder,
                    "-ar", str(target_sr),
                ])

                # Add bitrate argument
                if stage_cfg.bitrate_kbps > 0 and not profile.pipeline_noop:
                    bps = stage_cfg.bitrate_kbps * 1000
                    enc_cmd.extend(["-b:a", str(bps)])

                enc_cmd.append(encoded_path)
                r = subprocess.run(enc_cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"Encode failed for {side_label} stage {idx + 1}: {r.stderr[:200]}")

                # Decode to WAV
                dec_args = []
                if profile.ffmpeg_decode_input_format:
                    dec_args.extend(["-f", profile.ffmpeg_decode_input_format])
                dec_cmd = [ffmpeg, "-y"] + dec_args + ["-i", encoded_path,
                            "-acodec", "pcm_s16le", "-ar", str(target_sr), decoded_path]
                r = subprocess.run(dec_cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"Decode failed for {side_label} stage {idx + 1}: {r.stderr[:200]}")

                stage_results.append({
                    "stage_index": idx,
                    "codec_id": profile.codec_id,
                    "codec_name": profile.display_name,
                    "bitrate_kbps": stage_cfg.bitrate_kbps,
                })
                current_input = decoded_path

            return current_input, stage_results

        # Process Side A
        audio_a_final, stages_a_results = run_pipeline(session.stages_a, "A", 30)
        if _is_cancelled():
            audio_a_final = os.path.join(work_dir, "a_final.wav")
            # Copy last stage output as final
            src = session.stages_b[-1] if session.stages_b else None
            # Just use the pipeline output we already have
            pass

        # Process Side B
        audio_b_final, stages_b_results = run_pipeline(session.stages_b, "B", 55)

        # ── Phase 4: Bandwidth Limit (optional) ────────────────────────────
        if session.bandwidth_limit_a_enabled and session.bandwidth_limit_a_hz:
            _set_phase("bw_limit_a", 70, f"Applying {session.bandwidth_limit_a_hz}Hz low-pass to Side A...")
            bw_out = os.path.join(work_dir, "a_bw_limited.wav")
            cmd = [ffmpeg, "-y", "-i", audio_a_final,
                   "-af", f"lowpass=f={session.bandwidth_limit_a_hz}",
                   "-acodec", "pcm_s16le", bw_out]
            subprocess.run(cmd, capture_output=True)
            audio_a_final = bw_out

        if session.bandwidth_limit_b_enabled and session.bandwidth_limit_b_hz:
            _set_phase("bw_limit_b", 73, f"Applying {session.bandwidth_limit_b_hz}Hz low-pass to Side B...")
            bw_out = os.path.join(work_dir, "b_bw_limited.wav")
            cmd = [ffmpeg, "-y", "-i", audio_b_final,
                   "-af", f"lowpass=f={session.bandwidth_limit_b_hz}",
                   "-acodec", "pcm_s16le", bw_out]
            subprocess.run(cmd, capture_output=True)
            audio_b_final = bw_out

        # ── Phase 5: Alignment (cross-correlation) ─────────────────────────
        _set_phase("alignment", 78, "Aligning Side A and Side B...")
        a_data = load_wav_float(audio_a_final)
        b_data = load_wav_float(audio_b_final)

        # Shared with the desktop build via core.alignment. The pre-Phase-0 web
        # code searched every lag out to +/-5 s (locks onto periodicity/transient
        # spurious peaks) and PADDED the delayed side with zeros at the head,
        # which shifts musical content APART -- at the same timeline position
        # the listener heard different music, silently invalidating the test.
        alignment_lag, alignment_confidence = estimate_lag(a_data, b_data)
        a_data, b_data = align_pair(a_data, b_data, alignment_lag)
        pre_norm_null_db = null_depth_db(a_data, b_data)

        # ── Phase 6: Length Trim ────────────────────────────────────────────
        _set_phase("length_trim", 82, "Trimming to equal length...")
        min_len = min(len(a_data), len(b_data))
        a_data = a_data[:min_len]
        b_data = b_data[:min_len]

        # Save aligned audio
        audio_a_aligned = os.path.join(work_dir, "a_aligned.wav")
        audio_b_aligned = os.path.join(work_dir, "b_aligned.wav")
        save_wav_float(audio_a_aligned, a_data, target_sr)
        save_wav_float(audio_b_aligned, b_data, target_sr)

        # ── Phase 7: Loudness Normalization ─────────────────────────────────
        _set_phase("loudness", 88, "Normalizing loudness to -16 LUFS...")
        audio_a_norm = os.path.join(work_dir, "a_normalized.wav")
        audio_b_norm = os.path.join(work_dir, "b_normalized.wav")

        # Simple EBU R128 approximation using ffmpeg ebur128 filter
        loudness_a = measure_loudness_ffmpeg(audio_a_aligned)
        loudness_b = measure_loudness_ffmpeg(audio_b_aligned)

        target_lufs = -16.0
        gain_a = target_lufs - loudness_a
        gain_b = target_lufs - loudness_b

        # Apply gain with no-clipping guard
        apply_gain_ffmpeg(audio_a_aligned, audio_a_norm, gain_a, target_sr)
        apply_gain_ffmpeg(audio_b_aligned, audio_b_norm, gain_b, target_sr)

        # Measured BEFORE gain is applied: this is the level work
        # normalization had to do, which is the disclosure that matters.
        # (The desktop build compared post-normalization LUFS, a check that
        # can never fail; both now report pre-delta and residual.)
        loudness_diff = abs(loudness_a - loudness_b)

        # Null depth on the DELIVERED pair, after level matching: the hard
        # proof that A and B are level- and phase-aligned.
        null_depth_final = null_depth_db(
            load_wav_float(audio_a_norm), load_wav_float(audio_b_norm)
        )
        if loudness_diff > 0.5:
            validation_notes_early = (
                f"pipelines differed by {loudness_diff:.2f} dB before "
                f"normalization (gain A={gain_a:+.2f} dB, B={gain_b:+.2f} dB): "
                f"compare only the level-matched render"
            )
        else:
            validation_notes_early = None

        # ── Phase 8: Validation ─────────────────────────────────────────────
        _set_phase("validation", 95, "Validating session...")
        sr_a = probe_sample_rate(audio_a_norm)
        sr_b = probe_sample_rate(audio_b_norm)
        dur_a = probe_duration(audio_a_norm)
        dur_b = probe_duration(audio_b_norm)

        validation_notes = []
        if validation_notes_early:
            validation_notes.append(validation_notes_early)
        if alignment_confidence < 0.5:
            validation_notes.append(
                f"alignment estimate is weak (normalized correlation "
                f"{alignment_confidence:.2f}): treat this run as suspect"
            )
        if sr_a != sr_b:
            validation_notes.append(f"Sample rate mismatch: A={sr_a}, B={sr_b}")
        if abs(dur_a - dur_b) > 0.01:
            validation_notes.append(f"Duration mismatch: A={dur_a:.3f}s, B={dur_b:.3f}s")

        # ── Phase 9: validity gates ─────────────────────────────────────────
        # Two measurements decide whether trials may start at all. Both are
        # computed in core/ so the desktop refuses the same pairs the web does.
        _set_phase("gates", 97, "Checking validity gates...")

        # (a) Null gate: a pair built from identical pipelines has to null.
        expected_identical = identical_pipeline_config(
            stages_a_results,
            stages_b_results,
            bandwidth_a=(
                session.bandwidth_limit_a_enabled,
                session.bandwidth_limit_a_hz,
            ),
            bandwidth_b=(
                session.bandwidth_limit_b_enabled,
                session.bandwidth_limit_b_hz,
            ),
        )
        null_gate = null_gate_decision(
            null_depth_db=null_depth_final,
            expected_identical=expected_identical,
        )
        if null_gate["status"] == "failed":
            validation_notes.append(null_gate["reason"])

        # (b) Calibration (canary) pair: the delivered Side A against the same
        # material band-limited and hard clipped. Easy by construction.
        # The pristine side is served as a byte copy of the delivered Side A, so
        # the reference the listener hears IS Side A; the audibility measurement
        # uses the same mono render every other web measurement uses.
        canary_check: Dict[str, Any] = {
            "status": "not_configured",
            "usable": False,
            "reason": "calibration trial not requested for this session",
        }
        canary_pristine_path: Optional[str] = None
        canary_degraded_path: Optional[str] = None
        if session.canary_enabled:
            pristine_arr = load_wav_float(audio_a_norm)
            degraded_arr = degrade_for_canary(pristine_arr, target_sr)
            canary_check = canary_audio_check(
                pristine=pristine_arr, degraded=degraded_arr
            )
            if canary_check.get("usable"):
                canary_pristine_path = os.path.join(work_dir, "canary_pristine.wav")
                canary_degraded_path = os.path.join(work_dir, "canary_degraded.wav")
                shutil.copyfile(audio_a_norm, canary_pristine_path)
                save_wav_float(
                    canary_degraded_path,
                    np.asarray(degraded_arr, dtype=np.float32),
                    target_sr,
                )
                canary_check["pristine_path"] = canary_pristine_path
                canary_check["degraded_path"] = canary_degraded_path
            else:
                # A coin-flip calibration is worse than none: it voids valid
                # runs at random, so refuse the gate and say why.
                validation_notes.append(
                    f"calibration trial disabled: {canary_check['reason']}"
                )

        # Store session metadata
        with session.lock:
            session.audio_a_path = audio_a_norm
            session.audio_b_path = audio_b_norm
            session.preprocess_ready = True
            session.null_gate = null_gate
            session.canary_check = canary_check
            session.canary_pristine_path = canary_pristine_path
            session.canary_degraded_path = canary_degraded_path
            session.canary_available = bool(
                (not session.canary_enabled) or canary_check.get("usable", False)
            )
            # The engine is rebuilt from the new pair: the null gate now guards
            # /api/trial/start, and an unusable calibration pair turns the
            # canary off. New audio means a new session, so old trials do not
            # survive it.
            session.configure_engine()
            session.session_metadata = {
                "input_file": session.input_file_name or "",
                "original_sample_rate": original_sr,
                "target_sample_rate": target_sr,
                "sample_rate_mode": session.sample_rate_mode.value,
                "resample_engine_used": resample_engine_used,
                "duration_seconds": dur_a,
                "channels": channels,
                "stages_a_count": len(session.stages_a),
                "stages_b_count": len(session.stages_b),
                "bandwidth_limit_a_enabled": session.bandwidth_limit_a_enabled,
                "bandwidth_limit_a_hz": session.bandwidth_limit_a_hz,
                "bandwidth_limit_b_enabled": session.bandwidth_limit_b_enabled,
                "bandwidth_limit_b_hz": session.bandwidth_limit_b_hz,
                "loudness_a_lufs": round(loudness_a, 2),
                "loudness_b_lufs": round(loudness_b, 2),
                "loudness_diff_db": round(loudness_diff, 3),
                "alignment_lag_samples": alignment_lag,
                "alignment_confidence": round(float(alignment_confidence), 5),
                "null_depth_db_pre_loudness": round(float(pre_norm_null_db), 3),
                "null_depth_db": (
                    None
                    if null_depth_final == float("-inf")
                    else round(float(null_depth_final), 3)
                ),
                "null_depth_is_perfect": null_depth_final == float("-inf"),
                "loudness_gain_a_db": round(float(gain_a), 3),
                "loudness_gain_b_db": round(float(gain_b), 3),
                "loudness_is_pre_normalization": True,
                "validation_notes": validation_notes,
                "stages_a_details": stages_a_results,
                "stages_b_details": stages_b_results,
                # Phase 1 gates: the two measurements that decide whether this
                # session was allowed to run, in the export, not just the UI.
                "expected_identical_pair": expected_identical,
                "null_gate": null_gate,
                "canary_requested": bool(session.canary_enabled),
                "canary_available": bool(session.canary_available),
                "canary": canary_check,
                "canary_trial_index": 1 if session.canary_enabled else None,
            }

        _set_phase("complete", 100, "Preprocessing complete!")

    except Exception as e:
        with session.lock:
            session.preprocess_error = str(e)
            session.preprocess_phase = "error"
            session.preprocess_message = f"Error: {str(e)}"
    finally:
        with session.lock:
            session.preprocess_running = False


# ── FFmpeg Helper Functions ──────────────────────────────────────────────────

def load_wav_float(path: str) -> np.ndarray:
    """Load a WAV file as float32 mono array."""
    ffprobe = find_ffprobe()
    probe_cmd = [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_streams", path]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    info = json.loads(r.stdout)
    sr = 48000
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            sr = int(s.get("sample_rate", 48000))

    cmd = [find_ffmpeg(), "-y", "-i", path,
           "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def save_wav_float(path: str, data: np.ndarray, sample_rate: int):
    """Save float32 array as WAV."""
    import struct
    with open(path, "wb") as f:
        # Write WAV header for 32-bit float mono
        num_samples = len(data)
        byte_rate = sample_rate * 4
        block_align = 4
        data_size = num_samples * 4
        file_size = 36 + data_size

        f.write(b"RIFF")
        f.write(struct.pack("<I", file_size - 8))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 3))  # IEEE float
        f.write(struct.pack("<H", 1))  # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", 32))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(data.tobytes())


def measure_loudness_ffmpeg(path: str) -> float:
    """Measure integrated loudness using ffmpeg ebur128 filter."""
    cmd = [find_ffmpeg(), "-i", path, "-af", "ebur128=peak=true",
           "-f", "null", "-", "-nostdin"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # Parse integrated loudness from stderr
    for line in r.stderr.split("\n"):
        if "I:" in line and "Integrated" not in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == "I:" or (p.startswith("I") and "+" in p):
                    try:
                        return float(p.replace("I:", "").replace("+", ""))
                    except ValueError:
                        continue
    # Fallback: simple RMS-based estimate
    data = load_wav_float(path)
    if len(data) > 0:
        rms = np.sqrt(np.mean(data ** 2))
        if rms > 0:
            return 20 * math.log10(rms) - 3.5  # rough LUFS approximation
    return -20.0


def apply_gain_ffmpeg(input_path: str, output_path: str, gain_db: float, sample_rate: int):
    """Apply gain to audio with no-clipping guard."""
    vol = max(min(gain_db, 12), -30)  # clamp gain
    cmd = [find_ffmpeg(), "-y", "-i", input_path,
           "-af", f"volume={vol}dB,agate=threshold=-60dB:attack=10:release=100",
           "-acodec", "pcm_s16le", "-ar", str(sample_rate), output_path]
    subprocess.run(cmd, capture_output=True)


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
    })


# ── API: ABX Trials ──────────────────────────────────────────────────────────

def _canary_block() -> Dict[str, Any]:
    """Engine's canary state + the measured calibration material (core.report)."""
    return canary_report_block(session.engine.gates()["canary"], session.canary_check)


def _validity_lines() -> List[str]:
    """Same evidence block as the desktop panel, same wording (core.report)."""
    meta = session.session_metadata or {}
    stats = session.engine.stats()
    has_loudness = "loudness_diff_db" in meta
    return validity_lines(
        null_depth_db=(
            float("-inf")
            if meta.get("null_depth_is_perfect")
            else meta.get("null_depth_db")
        ),
        alignment_lag_samples=int(meta.get("alignment_lag_samples", 0)),
        alignment_confidence=float(meta.get("alignment_confidence", 0.0)),
        loudness=(
            None
            if not has_loudness
            else {
                "pre_delta_db": float(meta["loudness_diff_db"]),
                "residual_delta_db": 0.0,
                "gain_a_db": float(meta.get("loudness_gain_a_db", 0.0)),
                "gain_b_db": float(meta.get("loudness_gain_b_db", 0.0)),
                "global_trim_db": 0.0,
            }
        ),
        disclosure=meta.get("disclosure", {}),
        notes=list(meta.get("validation_notes", [])),
        stats={
            "planned_trials": stats["planned_trials"],
            "is_final": stats["is_final"],
            "required_correct": stats["required_correct"],
            "ability_ci95": stats["ability_ci95"],
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


@app.route("/api/trial/start", methods=["POST"])
def start_trial():
    """Start a new ABX trial.

    409 while a validity gate is shut: a session that failed its calibration
    trial, or a pair that should null and does not, cannot be offered trials.
    The engine (not this route) owns that decision, so the desktop and the web
    refuse identically.
    """
    with session.lock:
        try:
            info = session.new_trial()
        except TrialGateClosed as exc:
            return jsonify({
                "error": str(exc),
                "reasons": list(exc.reasons),
                "gates": session.engine.gates(),
            }), 409
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

    return jsonify({
        # Legacy row shape plus `kind`: the answer feedback names the trial as
        # calibration when it was one, and the run's void state is in `stats`.
        "trial": trial_result_to_dict(trial),
        "stats": session.stats(),
    })


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
            # legacy keys the existing UI reads
            "mapping_a_to": (
                session.engine.pending_mapping or {}).get("A"),
            "mapping_b_to": (
                session.engine.pending_mapping or {}).get("B"),
        })


# ── API: Export ───────────────────────────────────────────────────────────────

@app.route("/api/export/json", methods=["GET"])
def export_json():
    """Export results as JSON file."""
    with session.lock:
        gates = session.engine.gates()
        data = {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "session": {
                **session.session_metadata,
                "export_schema": "abx-session/1",
                "tool": "codec-abx-tester/web",
                "disclosure": session.session_metadata.get("disclosure", {}),
                "verifiability": session.engine.reveal(),
                "canary": _canary_block(),
                "null_gate": session.engine.null_gate_block(),
                "gates": gates,
                "run_valid": session.engine.run_valid,
            },
            # Calibration included: it is part of the committed X sequence, so
            # a third party verifying this file sees the same indices.
            "trials": [_trial_dict(t) for t in session.engine.all_trials()],
            "stats": session.stats(),
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
            "label_mapping_mode": session.label_mapping_mode.value,
            "canary_requested": bool(session.canary_enabled),
            "canary_available": bool(session.canary_available),
            "canary": _canary_block(),
            "null_gate": session.engine.null_gate_block(),
            "gates_open": gates["open"],
            "run_valid": session.engine.run_valid,
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
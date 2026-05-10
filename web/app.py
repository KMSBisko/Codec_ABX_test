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
        # ABX Engine state
        self.trials: List[TrialResult] = []
        self.total_trials = 0
        self.correct_trials = 0
        self.current_x_is: str = "A"
        # Label mapping for blind mode
        self.mapping_a_to: str = "source_A"
        self.mapping_b_to: str = "source_B"
        self._blind_no_change_streak = 0

    def reset_trials(self):
        """Reset ABX trial state."""
        self.trials.clear()
        self.total_trials = 0
        self.correct_trials = 0
        self.current_x_is = self._random_x()
        self.mapping_a_to = "source_A"
        self.mapping_b_to = "source_B"
        self._blind_no_change_streak = 0

    def _random_x(self) -> str:
        return "A" if random.random() < 0.5 else "B"

    def new_trial(self) -> Dict[str, Any]:
        """Start a new trial. Returns trial info with label mapping."""
        self.current_x_is = self._random_x()

        # Handle blind random mode: possibly swap labels
        if self.label_mapping_mode == LabelMappingMode.BLIND_RANDOM:
            changed = False
            # Increase swap probability with streak length
            swap_prob = min(0.1 * self._blind_no_change_streak, 0.5)
            if random.random() < swap_prob:
                self.mapping_a_to, self.mapping_b_to = (
                    self.mapping_b_to,
                    self.mapping_a_to,
                )
                changed = True
                self._blind_no_change_streak = 0
            else:
                self._blind_no_change_streak += 1

        return {
            "trial_index": self.total_trials + 1,
            "x_is": self.current_x_is,
            "mapping_a_to": self.mapping_a_to,
            "mapping_b_to": self.mapping_b_to,
        }

    def submit_answer(self, answer: str) -> TrialResult:
        """Submit user's answer for current trial."""
        normalized = answer.strip().upper()
        if normalized not in ("A", "B"):
            raise ValueError("answer must be 'A' or 'B'")

        self.total_trials += 1
        # Resolve what the user actually selected (source level)
        display_x_source = (
            self.mapping_a_to if self.current_x_is == "A" else self.mapping_b_to
        )
        answer_source = (
            self.mapping_a_to if normalized == "A" else self.mapping_b_to
        )
        is_correct = normalized == self.current_x_is
        if is_correct:
            self.correct_trials += 1

        trial = TrialResult(
            trial_index=self.total_trials,
            x_is=self.current_x_is,
            answer=normalized,
            correct=is_correct,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            mapping_a_to=self.mapping_a_to,
            mapping_b_to=self.mapping_b_to,
            x_source=display_x_source,
            answer_source=answer_source,
        )
        self.trials.append(trial)

        # Prepare next trial
        self.current_x_is = self._random_x()
        changed_for_next = False
        if self.label_mapping_mode == LabelMappingMode.BLIND_RANDOM:
            swap_prob = min(0.1 * self._blind_no_change_streak, 0.5)
            if random.random() < swap_prob:
                changed_for_next = True

        trial.mapping_changed_for_next_trial = changed_for_next

        return trial

    def one_tailed_p_value(self) -> float:
        n = self.total_trials
        k = self.correct_trials
        if n == 0:
            return 1.0
        total = 0.0
        for i in range(k, n + 1):
            total += math.comb(n, i) * (0.5 ** n)
        return min(max(total, 0.0), 1.0)

    def stats(self) -> Dict[str, Any]:
        return {
            "total_trials": self.total_trials,
            "correct_trials": self.correct_trials,
            "p_value_one_tailed": self.one_tailed_p_value(),
        }


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

        # Label mapping mode
        lm_mode = data.get("label_mapping_mode")
        if lm_mode:
            session.label_mapping_mode = LabelMappingMode(lm_mode)

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

    return jsonify({"status": "ok"})


@app.route("/api/config", methods=["GET"])
def get_config():
    """Return current configuration."""
    with session.lock:
        return jsonify({
            "sample_rate_mode": session.sample_rate_mode.value,
            "label_mapping_mode": session.label_mapping_mode.value,
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
            import shutil
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

        # Use shorter segment for cross-correlation to save memory
        seg_len = min(len(a_data), len(b_data), 48000 * 5)  # max 5 seconds
        a_seg = a_data[:seg_len]
        b_seg = b_data[:seg_len]

        if len(a_seg) > 1 and len(b_seg) > 1:
            # Use full-mode correlation to get lag at all offsets.
            # Default 'valid' mode returns a single scalar when inputs are equal-length,
            # which causes incorrect large negative lag values (~5s delay on Side A).
            corr_full = np.correlate(a_seg - np.mean(a_seg), b_seg - np.mean(b_seg), mode="full")
            max_lag_idx = np.argmax(corr_full)
            alignment_lag = int(max_lag_idx - len(a_seg) + 1)
        else:
            alignment_lag = 0

        # Apply alignment (pad or trim)
        if abs(alignment_lag) > 0:
            if alignment_lag > 0:
                b_data = np.pad(b_data, ((alignment_lag, 0)), mode="constant")
            else:
                a_data = np.pad(a_data, ((abs(alignment_lag), 0)), mode="constant")

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

        loudness_diff = abs(loudness_a - loudness_b)

        # ── Phase 8: Validation ─────────────────────────────────────────────
        _set_phase("validation", 95, "Validating session...")
        sr_a = probe_sample_rate(audio_a_norm)
        sr_b = probe_sample_rate(audio_b_norm)
        dur_a = probe_duration(audio_a_norm)
        dur_b = probe_duration(audio_b_norm)

        validation_notes = []
        if sr_a != sr_b:
            validation_notes.append(f"Sample rate mismatch: A={sr_a}, B={sr_b}")
        if abs(dur_a - dur_b) > 0.01:
            validation_notes.append(f"Duration mismatch: A={dur_a:.3f}s, B={dur_b:.3f}s")

        # Store session metadata
        with session.lock:
            session.audio_a_path = audio_a_norm
            session.audio_b_path = audio_b_norm
            session.preprocess_ready = True
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
                "validation_notes": validation_notes,
                "stages_a_details": stages_a_results,
                "stages_b_details": stages_b_results,
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
    """Stream processed audio A or B for playback."""
    with session.lock:
        if side == "a":
            path = session.audio_a_path
        elif side == "b":
            path = session.audio_b_path
        else:
            return jsonify({"error": "side must be 'a' or 'b'"}), 400

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
        if side == "a":
            path = session.audio_a_path
        elif side == "b":
            path = session.audio_b_path
        else:
            return jsonify({"error": "side must be 'a' or 'b'"}), 400

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

@app.route("/api/trial/start", methods=["POST"])
def start_trial():
    """Start a new ABX trial."""
    with session.lock:
        info = session.new_trial()
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
    """Return all trial results."""
    with session.lock:
        return jsonify({
            "trials": [trial_result_to_dict(t) for t in session.trials],
            "stats": session.stats(),
        })


# ── API: Diagnostics ─────────────────────────────────────────────────────────

@app.route("/api/diagnostics", methods=["GET"])
def get_diagnostics():
    """Return full diagnostics data."""
    with session.lock:
        return jsonify({
            "metadata": session.session_metadata,
            "trials": [trial_result_to_dict(t) for t in session.trials],
            "stats": session.stats(),
            "mapping_a_to": session.mapping_a_to,
            "mapping_b_to": session.mapping_b_to,
        })


# ── API: Export ───────────────────────────────────────────────────────────────

@app.route("/api/export/json", methods=["GET"])
def export_json():
    """Export results as JSON file."""
    with session.lock:
        data = {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "session": session.session_metadata,
            "trials": [trial_result_to_dict(t) for t in session.trials],
            "stats": session.stats(),
        }

    return jsonify(data), 200, {"Content-Disposition": "attachment; filename=abx_results.json"}


@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    """Export trial results as CSV file."""
    with session.lock:
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "trial_index", "x_is", "answer", "correct", "timestamp_utc",
                "mapping_a_to", "mapping_b_to", "x_source", "answer_source",
            ],
        )
        writer.writeheader()
        for trial in session.trials:
            from dataclasses import asdict
            writer.writerow(asdict(trial))

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
    """Return basic session information."""
    with session.lock:
        return jsonify({
            "has_input": session.input_file_path is not None,
            "input_filename": session.input_file_name,
            "preprocess_ready": session.preprocess_ready,
            "label_mapping_mode": session.label_mapping_mode.value,
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
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal

from .models import (
    CodecProfile,
    PreparedSession,
    PreparedTrack,
    SampleRateMode,
    SessionValidation,
    codec_catalog,
)


class PipelineError(RuntimeError):
    pass


class PipelineCancelled(PipelineError):
    pass


class AudioPipeline:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe") -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.catalog = codec_catalog()
        self._cancel_requested = False
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._proc_lock = threading.Lock()

    def request_cancel(self) -> None:
        self._cancel_requested = True
        with self._proc_lock:
            proc = self._active_process
            if proc is not None and proc.poll() is None:
                proc.terminate()

    def _reset_cancel(self) -> None:
        self._cancel_requested = False

    def check_binaries(self) -> None:
        if shutil.which(self.ffmpeg_bin) is None:
            raise PipelineError("ffmpeg is not available on PATH")
        if shutil.which(self.ffprobe_bin) is None:
            raise PipelineError("ffprobe is not available on PATH")

    def _run(self, args: List[str]) -> None:
        with self._proc_lock:
            self._active_process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        stdout = ""
        stderr = ""
        try:
            while True:
                if self._cancel_requested:
                    raise PipelineCancelled("Operation cancelled by user")

                with self._proc_lock:
                    proc = self._active_process

                if proc is None:
                    raise PipelineError("Internal process state error")

                try:
                    stdout, stderr = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except PipelineCancelled:
            with self._proc_lock:
                proc = self._active_process
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    stdout, stderr = proc.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
            raise
        finally:
            with self._proc_lock:
                self._active_process = None

        if proc.returncode != 0:
            raise PipelineError(
                "Command failed:\n"
                + " ".join(args)
                + "\n\nstdout:\n"
                + stdout
                + "\n\nstderr:\n"
                + stderr
            )

    def _probe(self, input_path: str) -> Dict[str, object]:
        args = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a:0",
            input_path,
        ]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise PipelineError(proc.stderr or "ffprobe failed")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise PipelineError("unable to parse ffprobe output") from exc
        streams = payload.get("streams", [])
        if not streams:
            raise PipelineError("no audio stream detected")
        return streams[0]

    def _to_working_wav(
        self,
        source_path: str,
        out_wav: str,
        target_sr: int,
    ) -> None:
        # Enforce explicit resample path. If source and target match this is a no-op.
        args = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            source_path,
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-af",
            "aresample=resampler=soxr:precision=33",
            "-ar",
            str(target_sr),
            out_wav,
        ]
        self._run(args)

    @staticmethod
    def _select_encoder_sample_rate(profile: CodecProfile, target_sr: int) -> int:
        # libopus only accepts a fixed family of sample rates.
        if profile.ffmpeg_encoder == "libopus":
            supported = (48000, 24000, 16000, 12000, 8000)
            if target_sr in supported:
                return target_sr
            return 48000
        return target_sr

    def _encode_decode(
        self,
        wav_in: str,
        work_dir: Path,
        profile: CodecProfile,
        bitrate_kbps: int,
        target_sr: int,
        label: str,
    ) -> Tuple[str, str]:
        encoded_path = work_dir / f"{label}.{profile.container_ext}"
        decoded_path = work_dir / f"{label}_decoded.wav"

        if profile.passthrough_unprocessed:
            # Keep a deterministic PCM render for reference-track ABX comparisons.
            passthrough_args = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                wav_in,
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-af",
                "aresample=resampler=soxr:precision=33",
                "-ar",
                str(target_sr),
                str(encoded_path),
            ]
            self._run(passthrough_args)
            return str(encoded_path), str(encoded_path)

        encode_sr = self._select_encoder_sample_rate(profile, target_sr)

        encode_args = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            wav_in,
            "-vn",
            "-c:a",
            profile.ffmpeg_encoder,
            "-b:a",
            f"{bitrate_kbps}k",
            "-af",
            "aresample=resampler=soxr:precision=33",
            "-ar",
            str(encode_sr),
        ]
        encode_args.extend(profile.ffmpeg_extra_args)
        encode_args.append(str(encoded_path))
        self._run(encode_args)

        # Decode through an explicit soxr path to avoid hidden quality variance.
        decode_args = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(encoded_path),
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-af",
            "aresample=resampler=soxr:precision=33",
            "-ar",
            str(target_sr),
            str(decoded_path),
        ]
        self._run(decode_args)
        return str(encoded_path), str(decoded_path)

    @staticmethod
    def _load_audio(path: str) -> Tuple[np.ndarray, int]:
        data, sr = sf.read(path, always_2d=True, dtype="float32")
        return data, int(sr)

    @staticmethod
    def _estimate_lag_samples(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
        a_mono = np.mean(a, axis=1)
        b_mono = np.mean(b, axis=1)

        corr = signal.correlate(b_mono, a_mono, mode="full", method="fft")
        lags = signal.correlation_lags(len(b_mono), len(a_mono), mode="full")
        mask = np.abs(lags) <= max_lag
        if not np.any(mask):
            return 0
        selected = np.argmax(corr[mask])
        return int(lags[mask][selected])

    @staticmethod
    def _apply_alignment(a: np.ndarray, b: np.ndarray, lag: int) -> Tuple[np.ndarray, np.ndarray]:
        # Positive lag means B is delayed and must be shifted earlier.
        if lag > 0:
            b = b[lag:]
        elif lag < 0:
            a = a[-lag:]
        min_len = min(len(a), len(b))
        return a[:min_len], b[:min_len]

    @staticmethod
    def _normalize_pair_to_target(
        a: np.ndarray,
        b: np.ndarray,
        sample_rate: int,
        target_lufs: float,
    ) -> Tuple[np.ndarray, np.ndarray, float, float, float, float]:
        meter = pyln.Meter(sample_rate)

        lufs_a = meter.integrated_loudness(a)
        lufs_b = meter.integrated_loudness(b)

        gain_a_db = target_lufs - lufs_a
        gain_b_db = target_lufs - lufs_b

        a2 = a * float(10 ** (gain_a_db / 20.0))
        b2 = b * float(10 ** (gain_b_db / 20.0))

        peak_a = float(np.max(np.abs(a2)))
        peak_b = float(np.max(np.abs(b2)))

        overflow_a_db = 20.0 * math.log10(peak_a) if peak_a > 1.0 else 0.0
        overflow_b_db = 20.0 * math.log10(peak_b) if peak_b > 1.0 else 0.0
        global_trim_db = max(0.0, overflow_a_db, overflow_b_db)

        if global_trim_db > 0.0:
            trim = float(10 ** (-global_trim_db / 20.0))
            a2 *= trim
            b2 *= trim

        final_lufs_a = meter.integrated_loudness(a2)
        final_lufs_b = meter.integrated_loudness(b2)

        peak_a_lin = float(np.max(np.abs(a2)))
        peak_b_lin = float(np.max(np.abs(b2)))
        peak_a_dbfs = -999.0 if peak_a_lin <= 0 else 20.0 * math.log10(peak_a_lin)
        peak_b_dbfs = -999.0 if peak_b_lin <= 0 else 20.0 * math.log10(peak_b_lin)

        return a2, b2, float(final_lufs_a), float(final_lufs_b), peak_a_dbfs, peak_b_dbfs

    @staticmethod
    def _write_float_wav(path: str, data: np.ndarray, sample_rate: int) -> None:
        sf.write(path, data, samplerate=sample_rate, subtype="PCM_16")

    def prepare_session(
        self,
        input_path: str,
        codec_a_id: str,
        bitrate_a_kbps: int,
        codec_b_id: str,
        bitrate_b_kbps: int,
        mode: SampleRateMode,
        work_dir: str,
    ) -> Tuple[PreparedSession, np.ndarray, np.ndarray]:
        self._reset_cancel()
        self.check_binaries()
        p_in = Path(input_path)
        if p_in.suffix.lower() not in (".wav", ".flac"):
            raise PipelineError("Input must be WAV or FLAC")
        if codec_a_id not in self.catalog or codec_b_id not in self.catalog:
            raise PipelineError("Unknown codec profile")

        meta = self._probe(str(p_in))
        original_sr = int(meta.get("sample_rate", 0) or 0)
        if original_sr <= 0:
            raise PipelineError("Unable to detect input sample rate")

        target_sr = original_sr if mode == SampleRateMode.NATIVE else 48000

        wd = Path(work_dir)
        wd.mkdir(parents=True, exist_ok=True)

        working_wav = wd / "working_input.wav"
        self._to_working_wav(str(p_in), str(working_wav), target_sr)

        profile_a = self.catalog[codec_a_id]
        profile_b = self.catalog[codec_b_id]

        enc_a, dec_a = self._encode_decode(
            str(working_wav), wd, profile_a, bitrate_a_kbps, target_sr, "track_a"
        )
        enc_b, dec_b = self._encode_decode(
            str(working_wav), wd, profile_b, bitrate_b_kbps, target_sr, "track_b"
        )

        arr_a, sr_a = self._load_audio(dec_a)
        arr_b, sr_b = self._load_audio(dec_b)

        if sr_a != target_sr or sr_b != target_sr:
            raise PipelineError("Decoded outputs did not preserve target sample rate")

        max_lag = int(0.100 * target_sr)
        lag = self._estimate_lag_samples(arr_a, arr_b, max_lag=max_lag)
        arr_a, arr_b = self._apply_alignment(arr_a, arr_b, lag)

        arr_a, arr_b, lufs_a, lufs_b, peak_a_dbfs, peak_b_dbfs = self._normalize_pair_to_target(
            arr_a,
            arr_b,
            target_sr,
            target_lufs=-16.0,
        )

        norm_a_path = wd / "track_a_normalized.wav"
        norm_b_path = wd / "track_b_normalized.wav"
        self._write_float_wav(str(norm_a_path), arr_a, target_sr)
        self._write_float_wav(str(norm_b_path), arr_b, target_sr)

        min_len = min(len(arr_a), len(arr_b))
        arr_a = arr_a[:min_len]
        arr_b = arr_b[:min_len]

        loudness_diff = abs(lufs_a - lufs_b)
        notes: List[str] = []
        if loudness_diff >= 0.1:
            notes.append("Loudness difference exceeds 0.1 dB target")

        validation = SessionValidation(
            sample_rate_equal=(sr_a == sr_b == target_sr),
            lengths_equal=(len(arr_a) == len(arr_b)),
            loudness_diff_db=float(loudness_diff),
            alignment_lag_samples=int(lag),
            has_switch_guard=True,
            notes=notes,
        )

        duration_seconds = min_len / float(target_sr)

        track_a = PreparedTrack(
            label="A",
            codec_id=profile_a.codec_id,
            codec_name=profile_a.ui_name,
            bitrate_kbps=bitrate_a_kbps,
            sample_rate=target_sr,
            pcm_path=str(norm_a_path),
            encoded_path=enc_a,
            loudness_lufs=float(lufs_a),
            true_peak_dbfs=float(peak_a_dbfs),
        )
        track_b = PreparedTrack(
            label="B",
            codec_id=profile_b.codec_id,
            codec_name=profile_b.ui_name,
            bitrate_kbps=bitrate_b_kbps,
            sample_rate=target_sr,
            pcm_path=str(norm_b_path),
            encoded_path=enc_b,
            loudness_lufs=float(lufs_b),
            true_peak_dbfs=float(peak_b_dbfs),
        )

        session = PreparedSession(
            input_file=str(p_in),
            working_input_wav=str(working_wav),
            original_sample_rate=original_sr,
            target_sample_rate=target_sr,
            mode=mode,
            duration_seconds=duration_seconds,
            channels=int(arr_a.shape[1]),
            track_a=track_a,
            track_b=track_b,
            validation=validation,
        )

        return session, arr_a, arr_b

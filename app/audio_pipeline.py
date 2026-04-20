from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal

from .models import (
    CodecProfile,
    PipelineStageConfig,
    PipelineStageResult,
    PreparedSession,
    PreparedTrack,
    ProcessingMode,
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
        self._resample_filter = "aresample=resampler=soxr:precision=33"
        self._available_encoders: Optional[Set[str]] = None
        self._cancel_requested = False
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._proc_lock = threading.Lock()
        self._codec_fallback_map: Dict[str, str] = {
            "aptx": "sim_aptx",
            "aptx_hd": "sim_aptx_hd",
            "ldac": "sim_ldac",
        }

    def request_cancel(self) -> None:
        self._cancel_requested = True
        with self._proc_lock:
            proc = self._active_process
            if proc is not None and proc.poll() is None:
                proc.terminate()

    def _reset_cancel(self) -> None:
        self._cancel_requested = False

    @staticmethod
    def _packaged_root() -> Optional[Path]:
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            return Path(str(frozen_root))
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return None

    def _resolve_binary(self, configured: str, candidates: List[str]) -> Optional[str]:
        configured_path = Path(configured)
        if configured_path.is_file():
            return str(configured_path)

        by_path = shutil.which(configured)
        if by_path:
            return by_path

        roots: List[Path] = []
        packaged_root = self._packaged_root()
        if packaged_root is not None:
            roots.append(packaged_root)

        cwd = Path(os.getcwd())
        roots.append(cwd)
        roots.append(cwd / "bin")

        for root in roots:
            for name in candidates:
                candidate = root / name
                if candidate.is_file():
                    return str(candidate)

        return None

    def check_binaries(self) -> None:
        ffmpeg_resolved = self._resolve_binary(self.ffmpeg_bin, ["ffmpeg.exe", "ffmpeg"])
        ffprobe_resolved = self._resolve_binary(self.ffprobe_bin, ["ffprobe.exe", "ffprobe"])

        if ffmpeg_resolved is None:
            raise PipelineError("ffmpeg not found (PATH or bundled app binary)")
        if ffprobe_resolved is None:
            raise PipelineError("ffprobe not found (PATH or bundled app binary)")

        self.ffmpeg_bin = ffmpeg_resolved
        self.ffprobe_bin = ffprobe_resolved
        self._refresh_available_encoders()

    def _refresh_available_encoders(self) -> None:
        args = [self.ffmpeg_bin, "-hide_banner", "-encoders"]
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **self._windows_no_window_flags(),
        )
        if proc.returncode != 0:
            self._available_encoders = None
            return

        encoders: Set[str] = set()
        for line in proc.stdout.splitlines():
            match = re.match(r"^\s*[VASFS.]\S*\s+([a-zA-Z0-9_]+)\s+", line)
            if match:
                encoders.add(match.group(1).lower())
        self._available_encoders = encoders

    def _require_encoder(self, profile: CodecProfile) -> None:
        available = self._is_encoder_available(profile.ffmpeg_encoder)
        if available is False:
            raise PipelineError(
                f"This ffmpeg build does not provide encoder '{profile.ffmpeg_encoder}' for "
                f"profile '{profile.ui_name}'."
            )

    def _is_encoder_available(self, encoder_name: str) -> Optional[bool]:
        if self._available_encoders is None:
            self._refresh_available_encoders()
        if self._available_encoders is None:
            return None
        return encoder_name.lower() in self._available_encoders

    def _resolve_runtime_profile(self, requested_profile: CodecProfile) -> CodecProfile:
        if requested_profile.passthrough_unprocessed or requested_profile.pipeline_noop:
            return requested_profile

        available = self._is_encoder_available(requested_profile.ffmpeg_encoder)
        if available is not False:
            return requested_profile

        fallback_id = self._codec_fallback_map.get(requested_profile.codec_id)
        if fallback_id is None:
            return requested_profile

        fallback_profile = self.catalog.get(fallback_id)
        if fallback_profile is None:
            return requested_profile

        return fallback_profile

    @staticmethod
    def _windows_no_window_flags() -> Dict[str, object]:
        if os.name != "nt":
            return {}
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            return {"creationflags": int(create_no_window)}
        return {}

    def _run(self, args: List[str]) -> None:
        proc_flags = self._windows_no_window_flags()
        with self._proc_lock:
            self._active_process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **proc_flags,
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

    def _run_with_resample_fallback(self, args: List[str]) -> None:
        previous_filter = self._resample_filter
        try:
            self._run(args)
            return
        except PipelineError as exc:
            error_text = str(exc)
            if (
                previous_filter == "aresample=resampler=soxr:precision=33"
                and "Requested resampling engine is unavailable" in error_text
            ):
                self._resample_filter = "aresample"
                retry_args = [self._resample_filter if arg == previous_filter else arg for arg in args]
                self._run(retry_args)
                return
            raise

    def _resample_engine_name(self) -> str:
        if "soxr" in self._resample_filter:
            return "soxr"
        return "default"

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
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **self._windows_no_window_flags(),
        )
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
        # Enforce an explicit resample path. Fallback to default engine if soxr is unavailable.
        args = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            source_path,
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-af",
            "__RESAMPLE_FILTER__",
            "-ar",
            str(target_sr),
            out_wav,
        ]
        args = [(self._resample_filter if arg == "__RESAMPLE_FILTER__" else arg) for arg in args]
        self._run_with_resample_fallback(args)

    @staticmethod
    def _select_encoder_sample_rate(profile: CodecProfile, target_sr: int) -> int:
        # libopus only accepts a fixed family of sample rates.
        if profile.ffmpeg_encoder == "libopus":
            supported = (48000, 24000, 16000, 12000, 8000)
            if target_sr in supported:
                return target_sr
            return 48000

        # aptX/aptX HD in some ffmpeg builds can produce pitch/timing drift at 44.1 kHz.
        # Keep internal codec processing fixed at 48 kHz, then resample decode output to target_sr.
        if profile.ffmpeg_encoder in ("aptx", "aptx_hd"):
            return 48000

        # Keep LDAC bitrates aligned to the common 48 kHz profile set.
        if profile.ffmpeg_encoder == "libldac":
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
    ) -> Tuple[str, str, CodecProfile]:
        runtime_profile = self._resolve_runtime_profile(profile)
        encoded_path = work_dir / f"{label}.{runtime_profile.container_ext}"
        decoded_path = work_dir / f"{label}_decoded.wav"

        if runtime_profile.passthrough_unprocessed:
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
                "__RESAMPLE_FILTER__",
                "-ar",
                str(target_sr),
                str(encoded_path),
            ]
            passthrough_args = [
                (self._resample_filter if arg == "__RESAMPLE_FILTER__" else arg)
                for arg in passthrough_args
            ]
            self._run_with_resample_fallback(passthrough_args)
            return str(encoded_path), str(encoded_path), runtime_profile

        self._require_encoder(runtime_profile)

        encode_sr = self._select_encoder_sample_rate(runtime_profile, target_sr)

        encode_args = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            wav_in,
            "-vn",
            "-c:a",
            runtime_profile.ffmpeg_encoder,
            "-b:a",
            f"{bitrate_kbps}k",
            "-af",
            "__RESAMPLE_FILTER__",
            "-ar",
            str(encode_sr),
        ]
        encode_args.extend(runtime_profile.ffmpeg_extra_args)
        encode_args.append(str(encoded_path))
        encode_args = [(self._resample_filter if arg == "__RESAMPLE_FILTER__" else arg) for arg in encode_args]
        self._run_with_resample_fallback(encode_args)

        # Decode through the same explicit resample path used for encode.
        decode_args = [self.ffmpeg_bin, "-y"]
        if runtime_profile.ffmpeg_decode_input_format:
            decode_args.extend(["-f", runtime_profile.ffmpeg_decode_input_format])
        decode_args.extend(
            [
                "-i",
                str(encoded_path),
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-af",
                "__RESAMPLE_FILTER__",
                "-ar",
                str(target_sr),
                str(decoded_path),
            ]
        )
        decode_args = [(self._resample_filter if arg == "__RESAMPLE_FILTER__" else arg) for arg in decode_args]
        self._run_with_resample_fallback(decode_args)
        return str(encoded_path), str(decoded_path), runtime_profile

    def _run_stage(
        self,
        wav_in: str,
        work_dir: Path,
        stage_cfg: PipelineStageConfig,
        target_sr: int,
        side_label: str,
        stage_index: int,
    ) -> PipelineStageResult:
        if stage_cfg.codec_id not in self.catalog:
            raise PipelineError(f"Unknown codec profile in stage {stage_index}: {stage_cfg.codec_id}")

        profile = self.catalog[stage_cfg.codec_id]
        if stage_cfg.bitrate_kbps < 0:
            raise PipelineError(f"Invalid bitrate in stage {stage_index}")

        if profile.pipeline_noop:
            input_sr = self._probe(wav_in).get("sample_rate", 0)
            in_sr = int(input_sr or 0)
            if in_sr <= 0:
                raise PipelineError(f"Unable to detect sample-rate at stage {stage_index}")
            return PipelineStageResult(
                stage_index=stage_index,
                codec_id=profile.codec_id,
                codec_name=profile.ui_name,
                bitrate_kbps=0,
                sample_rate_in=in_sr,
                sample_rate_out=in_sr,
                encoded_path=wav_in,
                decoded_path=wav_in,
            )

        encoded_path, decoded_path, runtime_profile = self._encode_decode(
            wav_in=wav_in,
            work_dir=work_dir,
            profile=profile,
            bitrate_kbps=stage_cfg.bitrate_kbps,
            target_sr=target_sr,
            label=f"{side_label}_stage{stage_index}",
        )

        input_sr = self._probe(wav_in).get("sample_rate", 0)
        output_sr = self._probe(decoded_path).get("sample_rate", 0)
        in_sr = int(input_sr or 0)
        out_sr = int(output_sr or 0)
        if in_sr <= 0 or out_sr <= 0:
            raise PipelineError(f"Unable to detect sample-rate in stage {stage_index}")

        return PipelineStageResult(
            stage_index=stage_index,
            codec_id=runtime_profile.codec_id,
            codec_name=runtime_profile.ui_name,
            bitrate_kbps=stage_cfg.bitrate_kbps,
            sample_rate_in=in_sr,
            sample_rate_out=out_sr,
            encoded_path=encoded_path,
            decoded_path=decoded_path,
        )

    def _run_pipeline_for_side(
        self,
        wav_in: str,
        work_dir: Path,
        stages: List[PipelineStageConfig],
        target_sr: int,
        side_label: str,
    ) -> Tuple[str, List[PipelineStageResult]]:
        current = wav_in
        stage_results: List[PipelineStageResult] = []
        for idx, stage_cfg in enumerate(stages, start=1):
            stage_result = self._run_stage(
                wav_in=current,
                work_dir=work_dir,
                stage_cfg=stage_cfg,
                target_sr=target_sr,
                side_label=side_label,
                stage_index=idx,
            )
            stage_results.append(stage_result)
            current = stage_result.decoded_path

            if stage_result.sample_rate_out != target_sr:
                raise PipelineError(
                    f"Stage {idx} on side {side_label} changed sample-rate unexpectedly "
                    f"({stage_result.sample_rate_out} vs target {target_sr})"
                )

        return current, stage_results

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
    def _apply_lowpass(data: np.ndarray, sample_rate: int, cutoff_hz: int) -> np.ndarray:
        nyquist = 0.5 * float(sample_rate)
        if cutoff_hz <= 0 or cutoff_hz >= nyquist:
            return data

        normalized_cutoff = float(cutoff_hz) / nyquist
        sos = signal.butter(N=8, Wn=normalized_cutoff, btype="lowpass", output="sos")
        return signal.sosfiltfilt(sos, data, axis=0).astype(np.float32, copy=False)

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
        processing_mode: ProcessingMode = ProcessingMode.SINGLE_STAGE,
        pipeline_stages_a: Optional[List[PipelineStageConfig]] = None,
        pipeline_stages_b: Optional[List[PipelineStageConfig]] = None,
        bandwidth_limit_a_enabled: bool = False,
        bandwidth_limit_a_hz: Optional[int] = None,
        bandwidth_limit_b_enabled: bool = False,
        bandwidth_limit_b_hz: Optional[int] = None,
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

        use_pipeline_stages = bool(pipeline_stages_a) and bool(pipeline_stages_b)

        if use_pipeline_stages:
            if len(pipeline_stages_a) < 1 or len(pipeline_stages_a) > 4:
                raise PipelineError("Pipeline A supports 1 to 4 stages")
            if len(pipeline_stages_b) < 1 or len(pipeline_stages_b) > 4:
                raise PipelineError("Pipeline B supports 1 to 4 stages")

            dec_a, stage_results_a = self._run_pipeline_for_side(
                wav_in=str(working_wav),
                work_dir=wd,
                stages=pipeline_stages_a,
                target_sr=target_sr,
                side_label="track_a",
            )
            dec_b, stage_results_b = self._run_pipeline_for_side(
                wav_in=str(working_wav),
                work_dir=wd,
                stages=pipeline_stages_b,
                target_sr=target_sr,
                side_label="track_b",
            )

            enc_a = stage_results_a[-1].encoded_path
            enc_b = stage_results_b[-1].encoded_path
            requested_codec_a_id = pipeline_stages_a[-1].codec_id
            requested_codec_b_id = pipeline_stages_b[-1].codec_id
            requested_profile_a = self.catalog[requested_codec_a_id]
            requested_profile_b = self.catalog[requested_codec_b_id]
            profile_a = self.catalog[stage_results_a[-1].codec_id]
            profile_b = self.catalog[stage_results_b[-1].codec_id]
            effective_bitrate_a = int(pipeline_stages_a[-1].bitrate_kbps)
            effective_bitrate_b = int(pipeline_stages_b[-1].bitrate_kbps)
            stage_count_a = len(stage_results_a)
            stage_count_b = len(stage_results_b)
            processing_mode_effective = (
                ProcessingMode.SINGLE_STAGE
                if stage_count_a == 1 and stage_count_b == 1
                else ProcessingMode.CASCADED_PIPELINE
            )
        else:
            requested_codec_a_id = codec_a_id
            requested_codec_b_id = codec_b_id
            requested_profile_a = self.catalog[requested_codec_a_id]
            requested_profile_b = self.catalog[requested_codec_b_id]
            profile_a = self.catalog[codec_a_id]
            profile_b = self.catalog[codec_b_id]

            enc_a, dec_a, profile_a_runtime = self._encode_decode(
                str(working_wav), wd, profile_a, bitrate_a_kbps, target_sr, "track_a"
            )
            enc_b, dec_b, profile_b_runtime = self._encode_decode(
                str(working_wav), wd, profile_b, bitrate_b_kbps, target_sr, "track_b"
            )
            stage_results_a = [
                PipelineStageResult(
                    stage_index=1,
                    codec_id=profile_a_runtime.codec_id,
                    codec_name=profile_a_runtime.ui_name,
                    bitrate_kbps=bitrate_a_kbps,
                    sample_rate_in=target_sr,
                    sample_rate_out=target_sr,
                    encoded_path=enc_a,
                    decoded_path=dec_a,
                )
            ]
            stage_results_b = [
                PipelineStageResult(
                    stage_index=1,
                    codec_id=profile_b_runtime.codec_id,
                    codec_name=profile_b_runtime.ui_name,
                    bitrate_kbps=bitrate_b_kbps,
                    sample_rate_in=target_sr,
                    sample_rate_out=target_sr,
                    encoded_path=enc_b,
                    decoded_path=dec_b,
                )
            ]
            profile_a = profile_a_runtime
            profile_b = profile_b_runtime
            effective_bitrate_a = bitrate_a_kbps
            effective_bitrate_b = bitrate_b_kbps
            stage_count_a = 1
            stage_count_b = 1
            processing_mode_effective = ProcessingMode.SINGLE_STAGE

        arr_a, sr_a = self._load_audio(dec_a)
        arr_b, sr_b = self._load_audio(dec_b)

        if sr_a != target_sr or sr_b != target_sr:
            raise PipelineError("Decoded outputs did not preserve target sample rate")

        max_lag = int(0.100 * target_sr)
        lag = self._estimate_lag_samples(arr_a, arr_b, max_lag=max_lag)
        arr_a, arr_b = self._apply_alignment(arr_a, arr_b, lag)

        if bandwidth_limit_a_enabled:
            if bandwidth_limit_a_hz not in (14000, 16000, 18000):
                raise PipelineError("Bandwidth limit cutoff A must be 14 kHz, 16 kHz, or 18 kHz")
            arr_a = self._apply_lowpass(arr_a, target_sr, int(bandwidth_limit_a_hz))

        if bandwidth_limit_b_enabled:
            if bandwidth_limit_b_hz not in (14000, 16000, 18000):
                raise PipelineError("Bandwidth limit cutoff B must be 14 kHz, 16 kHz, or 18 kHz")
            arr_b = self._apply_lowpass(arr_b, target_sr, int(bandwidth_limit_b_hz))

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
            bitrate_kbps=effective_bitrate_a,
            sample_rate=target_sr,
            pcm_path=str(norm_a_path),
            encoded_path=enc_a,
            loudness_lufs=float(lufs_a),
            true_peak_dbfs=float(peak_a_dbfs),
            stages=stage_results_a,
        )
        track_b = PreparedTrack(
            label="B",
            codec_id=profile_b.codec_id,
            codec_name=profile_b.ui_name,
            bitrate_kbps=effective_bitrate_b,
            sample_rate=target_sr,
            pcm_path=str(norm_b_path),
            encoded_path=enc_b,
            loudness_lufs=float(lufs_b),
            true_peak_dbfs=float(peak_b_dbfs),
            stages=stage_results_b,
        )

        session = PreparedSession(
            input_file=str(p_in),
            working_input_wav=str(working_wav),
            original_sample_rate=original_sr,
            target_sample_rate=target_sr,
            mode=mode,
            processing_mode=processing_mode_effective,
            resample_engine_used=self._resample_engine_name(),
            duration_seconds=duration_seconds,
            channels=int(arr_a.shape[1]),
            pipeline_stage_count_a=stage_count_a,
            pipeline_stage_count_b=stage_count_b,
            requested_codec_a_id=requested_codec_a_id,
            requested_codec_a_name=requested_profile_a.ui_name,
            requested_codec_b_id=requested_codec_b_id,
            requested_codec_b_name=requested_profile_b.ui_name,
            bandwidth_limit_a_enabled=bandwidth_limit_a_enabled,
            bandwidth_limit_a_hz=bandwidth_limit_a_hz,
            bandwidth_limit_b_enabled=bandwidth_limit_b_enabled,
            bandwidth_limit_b_hz=bandwidth_limit_b_hz,
            track_a=track_a,
            track_b=track_b,
            validation=validation,
        )

        return session, arr_a, arr_b

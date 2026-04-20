from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class SampleRateMode(str, Enum):
    NATIVE = "native"
    FORCE_48K = "force_48k"


class ProcessingMode(str, Enum):
    SINGLE_STAGE = "single_stage"
    CASCADED_PIPELINE = "cascaded_pipeline"


@dataclass(frozen=True)
class CodecProfile:
    codec_id: str
    display_name: str
    ffmpeg_encoder: str
    container_ext: str
    bitrate_options_kbps: List[int]
    simulated_label: Optional[str] = None
    ffmpeg_extra_args: List[str] = field(default_factory=list)
    ffmpeg_decode_input_format: Optional[str] = None
    passthrough_unprocessed: bool = False
    pipeline_noop: bool = False

    @property
    def ui_name(self) -> str:
        if self.simulated_label:
            return f"{self.display_name} ({self.simulated_label})"
        return self.display_name


@dataclass
class PreparedTrack:
    label: str
    codec_id: str
    codec_name: str
    bitrate_kbps: int
    sample_rate: int
    pcm_path: str
    encoded_path: str
    loudness_lufs: float
    true_peak_dbfs: float
    stages: List["PipelineStageResult"] = field(default_factory=list)


@dataclass
class PipelineStageConfig:
    codec_id: str
    bitrate_kbps: int


@dataclass
class PipelineStageResult:
    stage_index: int
    codec_id: str
    codec_name: str
    bitrate_kbps: int
    sample_rate_in: int
    sample_rate_out: int
    encoded_path: str
    decoded_path: str


@dataclass
class SessionValidation:
    sample_rate_equal: bool
    lengths_equal: bool
    loudness_diff_db: float
    alignment_lag_samples: int
    has_switch_guard: bool
    notes: List[str]


@dataclass
class PreparedSession:
    input_file: str
    working_input_wav: str
    original_sample_rate: int
    target_sample_rate: int
    mode: SampleRateMode
    processing_mode: ProcessingMode
    resample_engine_used: str
    duration_seconds: float
    channels: int
    pipeline_stage_count_a: int
    pipeline_stage_count_b: int
    requested_codec_a_id: str
    requested_codec_a_name: str
    requested_codec_b_id: str
    requested_codec_b_name: str
    track_a: PreparedTrack
    track_b: PreparedTrack
    validation: SessionValidation
    bandwidth_limit_a_enabled: bool = False
    bandwidth_limit_a_hz: Optional[int] = None
    bandwidth_limit_b_enabled: bool = False
    bandwidth_limit_b_hz: Optional[int] = None


@dataclass
class TrialResult:
    trial_index: int
    x_is: str
    answer: str
    correct: bool
    timestamp_utc: str
    mapping_a_to: Optional[str] = None
    mapping_b_to: Optional[str] = None
    x_source: Optional[str] = None
    answer_source: Optional[str] = None
    mapping_changed_for_next_trial: Optional[bool] = None


def codec_catalog() -> Dict[str, CodecProfile]:
    return {
        "lossless_unprocessed": CodecProfile(
            codec_id="lossless_unprocessed",
            display_name="Lossless (Unprocessed Reference)",
            ffmpeg_encoder="pcm_s16le",
            container_ext="wav",
            bitrate_options_kbps=[0],
            passthrough_unprocessed=True,
        ),
        "noop_passthrough": CodecProfile(
            codec_id="noop_passthrough",
            display_name="No-op / Lossless passthrough",
            ffmpeg_encoder="pcm_s16le",
            container_ext="wav",
            bitrate_options_kbps=[0],
            pipeline_noop=True,
        ),
        "opus": CodecProfile(
            codec_id="opus",
            display_name="Opus",
            ffmpeg_encoder="libopus",
            container_ext="opus",
            bitrate_options_kbps=[16, 24, 32, 48, 64, 96, 128, 160, 192, 256, 320],
            ffmpeg_extra_args=["-vbr", "off"],
        ),
        "aac": CodecProfile(
            codec_id="aac",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[48, 64, 80, 96, 128, 160, 192, 256, 320],
        ),
        "ogg_vorbis": CodecProfile(
            codec_id="ogg_vorbis",
            display_name="Ogg Vorbis",
            ffmpeg_encoder="libvorbis",
            container_ext="ogg",
            bitrate_options_kbps=[64, 96, 128, 160, 192, 256, 320],
        ),
        "sbc": CodecProfile(
            codec_id="sbc",
            display_name="SBC",
            ffmpeg_encoder="sbc",
            container_ext="sbc",
            bitrate_options_kbps=[96, 128, 160, 192, 256, 320],
            ffmpeg_extra_args=["-f", "sbc"],
            ffmpeg_decode_input_format="sbc",
        ),
        "aptx": CodecProfile(
            codec_id="aptx",
            display_name="aptX",
            ffmpeg_encoder="aptx",
            container_ext="aptx",
            bitrate_options_kbps=[352],
            ffmpeg_extra_args=["-f", "aptx"],
            ffmpeg_decode_input_format="aptx",
        ),
        "aptx_hd": CodecProfile(
            codec_id="aptx_hd",
            display_name="aptX HD",
            ffmpeg_encoder="aptx_hd",
            container_ext="aptx_hd",
            bitrate_options_kbps=[576],
            ffmpeg_extra_args=["-f", "aptx_hd"],
            ffmpeg_decode_input_format="aptx_hd",
        ),
        "ldac": CodecProfile(
            codec_id="ldac",
            display_name="LDAC",
            ffmpeg_encoder="libldac",
            container_ext="ldac",
            bitrate_options_kbps=[330, 660, 990],
            ffmpeg_extra_args=["-f", "ldac"],
            ffmpeg_decode_input_format="ldac",
        ),
        "sim_aptx": CodecProfile(
            codec_id="sim_aptx",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[256, 320, 352],
            simulated_label="Simulated aptX",
        ),
        "sim_aptx_hd": CodecProfile(
            codec_id="sim_aptx_hd",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[384, 512, 576],
            simulated_label="Simulated aptX HD",
        ),
        "sim_ldac": CodecProfile(
            codec_id="sim_ldac",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[330, 660, 990],
            simulated_label="Simulated LDAC",
        ),
    }

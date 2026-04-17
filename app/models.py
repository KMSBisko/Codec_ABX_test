from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class SampleRateMode(str, Enum):
    NATIVE = "native"
    FORCE_48K = "force_48k"


@dataclass(frozen=True)
class CodecProfile:
    codec_id: str
    display_name: str
    ffmpeg_encoder: str
    container_ext: str
    bitrate_options_kbps: List[int]
    simulated_label: Optional[str] = None
    ffmpeg_extra_args: List[str] = field(default_factory=list)
    passthrough_unprocessed: bool = False

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
    duration_seconds: float
    channels: int
    track_a: PreparedTrack
    track_b: PreparedTrack
    validation: SessionValidation


@dataclass
class TrialResult:
    trial_index: int
    x_is: str
    answer: str
    correct: bool
    timestamp_utc: str


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
        "opus": CodecProfile(
            codec_id="opus",
            display_name="Opus",
            ffmpeg_encoder="libopus",
            container_ext="opus",
            bitrate_options_kbps=[64, 96, 128, 160, 192, 256, 320],
            ffmpeg_extra_args=["-vbr", "off"],
        ),
        "aac": CodecProfile(
            codec_id="aac",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[96, 128, 160, 192, 256, 320],
        ),
        "sbc": CodecProfile(
            codec_id="sbc",
            display_name="SBC",
            ffmpeg_encoder="sbc",
            container_ext="sbc",
            bitrate_options_kbps=[192, 256, 320],
            ffmpeg_extra_args=["-f", "sbc"],
        ),
        "sim_aptx": CodecProfile(
            codec_id="sim_aptx",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[320, 352],
            simulated_label="Simulated aptX",
        ),
        "sim_aptx_hd": CodecProfile(
            codec_id="sim_aptx_hd",
            display_name="AAC",
            ffmpeg_encoder="aac",
            container_ext="m4a",
            bitrate_options_kbps=[512, 576],
            simulated_label="Simulated aptX HD",
        ),
        "sim_ldac": CodecProfile(
            codec_id="sim_ldac",
            display_name="Opus",
            ffmpeg_encoder="libopus",
            container_ext="opus",
            bitrate_options_kbps=[660, 990],
            simulated_label="Simulated LDAC",
            ffmpeg_extra_args=["-vbr", "off"],
        ),
    }

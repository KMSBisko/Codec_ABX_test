from __future__ import annotations

import platform
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd


@dataclass
class OutputDevice:
    device_index: int
    name: str
    host_api: str
    max_output_channels: int


class SynchronizedABXPlayer:
    def __init__(self) -> None:
        self._buffers: Dict[str, np.ndarray] = {}
        self._sample_rate: int = 48000
        self._channels: int = 2
        self._length_samples: int = 0

        self._stream: Optional[sd.OutputStream] = None
        self._lock = threading.RLock()

        self._position_samples: int = 0
        self._is_playing = False

        self._active_source: str = "A"
        self._x_maps_to: str = "A"

        self._prev_source: str = "A"
        self._switch_fade_total = 256
        self._switch_fade_remaining = 0

        self._loop_enabled = False
        self._loop_start = 0
        self._loop_end = 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def length_seconds(self) -> float:
        if self._sample_rate <= 0:
            return 0.0
        return self._length_samples / float(self._sample_rate)

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @staticmethod
    def list_output_devices() -> List[OutputDevice]:
        host_apis = sd.query_hostapis()
        devices = sd.query_devices()
        out: List[OutputDevice] = []
        for idx, d in enumerate(devices):
            if d["max_output_channels"] <= 0:
                continue
            host_name = host_apis[d["hostapi"]]["name"]
            out.append(
                OutputDevice(
                    device_index=idx,
                    name=d["name"],
                    host_api=host_name,
                    max_output_channels=int(d["max_output_channels"]),
                )
            )
        return out

    def load_buffers(self, a: np.ndarray, b: np.ndarray, sample_rate: int) -> None:
        if a.ndim != 2 or b.ndim != 2:
            raise ValueError("buffers must be 2D arrays [samples, channels]")
        if a.shape[1] != b.shape[1]:
            raise ValueError("channel mismatch between A and B")

        min_len = min(len(a), len(b))
        a = np.asarray(a[:min_len], dtype=np.float32)
        b = np.asarray(b[:min_len], dtype=np.float32)

        self.stop()
        with self._lock:
            self._buffers = {"A": a, "B": b}
            self._sample_rate = int(sample_rate)
            self._channels = int(a.shape[1])
            self._length_samples = int(min_len)
            self._position_samples = 0
            self._active_source = "A"
            self._x_maps_to = "A"
            self._prev_source = "A"
            self._switch_fade_remaining = 0
            self._loop_start = 0
            self._loop_end = self._length_samples

    def set_x_mapping(self, x_maps_to: str) -> None:
        v = x_maps_to.strip().upper()
        if v not in ("A", "B"):
            raise ValueError("X must map to A or B")
        with self._lock:
            self._x_maps_to = v

    def set_active_source(self, source: str) -> None:
        v = source.strip().upper()
        if v not in ("A", "B", "X"):
            raise ValueError("source must be A/B/X")

        with self._lock:
            resolved = self._x_maps_to if v == "X" else v
            if resolved != self._active_source:
                self._prev_source = self._active_source
                self._active_source = resolved
                self._switch_fade_remaining = self._switch_fade_total

    def set_position_seconds(self, seconds: float) -> None:
        with self._lock:
            pos = int(max(0.0, seconds) * self._sample_rate)
            self._position_samples = min(pos, max(0, self._length_samples - 1))

    def get_position_seconds(self) -> float:
        with self._lock:
            if self._sample_rate <= 0:
                return 0.0
            return self._position_samples / float(self._sample_rate)

    def set_loop(self, enabled: bool, start_seconds: float, end_seconds: float) -> None:
        with self._lock:
            self._loop_enabled = bool(enabled)
            s = int(max(0.0, start_seconds) * self._sample_rate)
            e = int(max(0.0, end_seconds) * self._sample_rate)
            self._loop_start = min(max(0, s), max(0, self._length_samples - 1))
            self._loop_end = min(max(self._loop_start + 1, e), self._length_samples)

    def _resolve_extra_settings(self, exclusive: bool):
        if not exclusive:
            return None

        os_name = platform.system().lower()
        if "windows" in os_name and hasattr(sd, "WasapiSettings"):
            return sd.WasapiSettings(exclusive=True)

        # PortAudio bindings expose reliable exclusive mode mainly through WASAPI.
        return None

    def start(self, device_index: Optional[int] = None, exclusive: bool = False) -> None:
        with self._lock:
            if not self._buffers:
                raise RuntimeError("no audio loaded")
            if self._stream is not None and self._stream.active:
                self._is_playing = True
                return

            extra_settings = self._resolve_extra_settings(exclusive)

            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                blocksize=1024,
                channels=self._channels,
                dtype="float32",
                callback=self._callback,
                device=device_index,
                extra_settings=extra_settings,
                latency="low",
            )
            self._stream.start()
            self._is_playing = True

    def stop(self) -> None:
        with self._lock:
            self._is_playing = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

    def close(self) -> None:
        self.stop()

    def _read_chunk(self, source: str, start: int, frames: int) -> np.ndarray:
        data = self._buffers[source]
        end = start + frames
        if end <= len(data):
            return data[start:end]

        out = np.zeros((frames, self._channels), dtype=np.float32)
        valid = max(0, len(data) - start)
        if valid > 0:
            out[:valid] = data[start:start + valid]
        return out

    def _advance_position(self, frames: int) -> None:
        self._position_samples += frames
        if self._loop_enabled and self._position_samples >= self._loop_end:
            self._position_samples = self._loop_start
        elif self._position_samples >= self._length_samples:
            self._position_samples = self._length_samples
            self._is_playing = False

    def _callback(self, outdata, frames, time_info, status) -> None:
        del time_info, status
        with self._lock:
            if not self._is_playing or self._position_samples >= self._length_samples:
                outdata.fill(0)
                return

            pos = self._position_samples
            new_chunk = self._read_chunk(self._active_source, pos, frames)

            if self._switch_fade_remaining > 0:
                old_chunk = self._read_chunk(self._prev_source, pos, frames)
                n = min(frames, self._switch_fade_remaining)
                ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
                mixed = new_chunk.copy()
                mixed[:n] = old_chunk[:n] * (1.0 - ramp) + new_chunk[:n] * ramp
                out_chunk = mixed
                self._switch_fade_remaining -= n
            else:
                out_chunk = new_chunk

            outdata[:] = out_chunk
            self._advance_position(frames)


def format_device_label(device: OutputDevice) -> str:
    return f"{device.device_index}: {device.name} [{device.host_api}]"

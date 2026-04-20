from __future__ import annotations

import os
import sys
import random
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .abx_engine import ABXEngine
from .audio_pipeline import AudioPipeline, PipelineCancelled, PipelineError
from .logger import ExperimentLogger
from .models import PipelineStageConfig, ProcessingMode, SampleRateMode, TrialResult, codec_catalog
from .player import SynchronizedABXPlayer, format_device_label


TRANSLATIONS = {
    "en": {
        "window_title": "Codec ABX Tester",
        "lang_english": "English",
        "lang_vietnamese": "Tiếng Việt",
        "status_idle": "Status: idle",
        "status_prefix": "Status: ",
        "group_input": "Input",
        "input_placeholder": "Select WAV or FLAC",
        "browse": "Browse",
        "audio_file": "Audio file",
        "sample_rate_mode": "Sample-rate mode",
        "processing_mode": "Mode",
        "mode_single": "Single-stage ABX",
        "mode_cascaded": "Cascaded Pipeline ABX",
        "sr_native": "Native sample rate (default)",
        "sr_force_48k": "Force 48 kHz (Bluetooth simulation)",
        "prepare": "Preprocess A/B",
        "cancel_prepare": "Cancel Preprocess",
        "group_codec": "Codec Profiles",
        "pipeline_stages": "Pipeline stages",
        "stage_count": "Stage count",
        "stage_count_a": "Stage count A",
        "stage_count_b": "Stage count B",
        "stage_codec": "Codec",
        "stage_bitrate": "Bitrate",
        "side_a": "Side A",
        "side_b": "Side B",
        "stage_label": "Stage",
        "bandwidth_limit_a": "Enable bandwidth limit A",
        "bandwidth_limit_b": "Enable bandwidth limit B",
        "bandwidth_cutoff_a": "Cutoff A",
        "bandwidth_cutoff_b": "Cutoff B",
        "codec_a": "Codec A",
        "codec_b": "Codec B",
        "bitrate_a": "Bitrate A",
        "bitrate_b": "Bitrate B",
        "mapping_mode": "A/B label mapping",
        "map_fixed": "Fixed labels (Play A=Codec A, Play B=Codec B)",
        "map_blind": "Blinded labels (Play A/B randomized per session)",
        "group_output": "3) Output Device",
        "device": "Device",
        "refresh_devices": "Refresh devices",
        "exclusive": "Request exclusive mode when available",
        "group_playback": "ABX tools",
        "group_abx_tools": "ABX tools",
        "play_a": "Play A",
        "play_b": "Play B",
        "play_x": "Play X",
        "pause": "Pause",
        "stop": "Stop",
        "loop": "Loop",
        "start_sec": "Start (s)",
        "end_sec": "End (s)",
        "group_abx": "5) ABX Trials",
        "trial": "Trial",
        "score": "Score",
        "pvalue": "p-value (one-tailed)",
        "x_eq_a": "X = A",
        "x_eq_b": "X = B",
        "export_json": "Export JSON",
        "export_csv": "Export CSV",
        "cancel_abx": "Cancel ABX Session",
        "group_diag": "Post-Session Diagnostics",
        "refresh_diag": "Show/Refresh Diagnostics",
        "diag_placeholder": "Diagnostics will appear here after preprocessing/trials.",
        "na_unprocessed": "N/A (unprocessed)",
        "already_running_title": "Already running",
        "already_running_text": "Preprocessing is already running.",
        "input_required_title": "Input required",
        "input_required_text": "Please select a WAV or FLAC file.",
        "status_preparing": "preprocessing and validating A/B...",
        "status_cancelling_prepare": "cancelling preprocessing...",
        "status_prepare_failed": "preprocessing failed",
        "status_prepare_cancelled": "preprocessing cancelled",
        "prepare_failed_title": "Preparation failed",
        "prepare_first_title": "Prepare first",
        "prepare_first_text": "Please preprocess A/B first.",
        "playback_error_title": "Playback error",
        "status_session_cancelled": "ABX session cancelled and reset",
        "diag_none": "No prepared session yet.",
        "nothing_export_title": "Nothing to export",
        "nothing_export_text": "No trial results yet.",
        "export_failed_title": "Export failed",
        "json_filter": "JSON (*.json)",
        "csv_filter": "CSV (*.csv)",
        "save_json": "Export JSON",
        "save_csv": "Export CSV",
        "status_ready": "ready",
        "labels_fixed": "labels=fixed",
        "labels_blinded": "labels=blinded",
        "diag_session_summary": "Session Summary",
        "diag_input": "Input",
        "diag_mode": "Mode",
        "diag_target_sr": "Target SR",
        "diag_resample_engine": "Resample engine",
        "diag_trials": "Trials",
        "diag_correct": "Correct",
        "diag_trial_details": "Trial Details",
        "diag_processing_mode": "Processing mode",
        "diag_pipeline_stages_a": "Pipeline stages A",
        "diag_pipeline_stages_b": "Pipeline stages B",
        "diag_bandwidth_limit_a": "Bandwidth limit A",
        "diag_bandwidth_cutoff_a": "Bandwidth cutoff A",
        "diag_bandwidth_limit_b": "Bandwidth limit B",
        "diag_bandwidth_cutoff_b": "Bandwidth cutoff B",
        "diag_pipeline_a_stages": "Pipeline A stages",
        "diag_pipeline_b_stages": "Pipeline B stages",
        "diag_encode_engine": "Encode",
        "diag_decode_engine": "Decode",
        "diag_requested": "Requested",
        "diag_effective": "Effective",
        "diag_no_trials": "(no trial answers submitted yet)",
        "diag_mapping_audit": "--- Mapping Audit ---",
        "diag_current_mapping": "Current Mapping",
        "diag_no_mapping_entries": "(no mapping audit entries yet)",
        "zoom": "Zoom",
        "zoom_out": "A-",
        "zoom_reset": "100%",
        "zoom_in": "A+",
        "dark_mode": "Dark mode (default)",
        "oled_mode": "OLED mode",
        "fullscreen_enter": "Fullscreen",
        "fullscreen_exit": "Windowed",
    },
    "vi": {
        "window_title": "Trình kiểm tra ABX Codec",
        "lang_english": "English",
        "lang_vietnamese": "Tiếng Việt",
        "status_idle": "Trạng thái: chờ",
        "status_prefix": "Trạng thái: ",
        "group_input": "Đầu vào",
        "input_placeholder": "Chọn WAV hoặc FLAC",
        "browse": "Chọn file",
        "audio_file": "Tệp âm thanh",
        "sample_rate_mode": "Chế độ tần số lấy mẫu",
        "processing_mode": "Chế độ",
        "mode_single": "ABX một tầng",
        "mode_cascaded": "ABX pipeline nhiều tầng",
        "sr_native": "Giữ tần số gốc (mặc định)",
        "sr_force_48k": "Ép 48 kHz (mô phỏng Bluetooth)",
        "prepare": "Tiền xử lý A/B",
        "cancel_prepare": "Hủy tiền xử lý",
        "group_codec": "Cấu hình Codec",
        "pipeline_stages": "Các tầng pipeline",
        "stage_count": "Số tầng",
        "stage_count_a": "Số tầng A",
        "stage_count_b": "Số tầng B",
        "stage_codec": "Codec",
        "stage_bitrate": "Bitrate",
        "side_a": "Nhánh A",
        "side_b": "Nhánh B",
        "stage_label": "Tầng",
        "bandwidth_limit_a": "Bật giới hạn băng thông A",
        "bandwidth_limit_b": "Bật giới hạn băng thông B",
        "bandwidth_cutoff_a": "Điểm cắt A",
        "bandwidth_cutoff_b": "Điểm cắt B",
        "codec_a": "Codec A",
        "codec_b": "Codec B",
        "bitrate_a": "Bitrate A",
        "bitrate_b": "Bitrate B",
        "mapping_mode": "Ánh xạ nhãn A/B",
        "map_fixed": "Nhãn cố định (Play A=Codec A, Play B=Codec B)",
        "map_blind": "Nhãn ẩn (Play A/B được xáo trộn theo phiên)",
        "group_output": "3) Thiết bị phát",
        "device": "Thiết bị",
        "refresh_devices": "Làm mới thiết bị",
        "exclusive": "Yêu cầu chế độ độc quyền nếu hỗ trợ",
        "group_playback": "Công cụ ABX",
        "group_abx_tools": "Công cụ ABX",
        "play_a": "Phát A",
        "play_b": "Phát B",
        "play_x": "Phát X",
        "pause": "Tạm dừng",
        "stop": "Dừng",
        "loop": "Lặp",
        "start_sec": "Bắt đầu (s)",
        "end_sec": "Kết thúc (s)",
        "group_abx": "5) Lượt ABX",
        "trial": "Lượt",
        "score": "Điểm",
        "pvalue": "p-value (một phía)",
        "x_eq_a": "X = A",
        "x_eq_b": "X = B",
        "export_json": "Xuất JSON",
        "export_csv": "Xuất CSV",
        "cancel_abx": "Hủy phiên ABX",
        "group_diag": "Chẩn đoán sau phiên",
        "refresh_diag": "Hiện/Làm mới chẩn đoán",
        "diag_placeholder": "Thông tin chẩn đoán sẽ hiển thị ở đây sau khi tiền xử lý/làm bài.",
        "na_unprocessed": "Không áp dụng (gốc)",
        "already_running_title": "Đang chạy",
        "already_running_text": "Tiền xử lý đang chạy.",
        "input_required_title": "Thiếu đầu vào",
        "input_required_text": "Vui lòng chọn tệp WAV hoặc FLAC.",
        "status_preparing": "đang tiền xử lý và kiểm tra A/B...",
        "status_cancelling_prepare": "đang hủy tiền xử lý...",
        "status_prepare_failed": "tiền xử lý thất bại",
        "status_prepare_cancelled": "đã hủy tiền xử lý",
        "prepare_failed_title": "Tiền xử lý thất bại",
        "prepare_first_title": "Hãy tiền xử lý trước",
        "prepare_first_text": "Vui lòng tiền xử lý A/B trước.",
        "playback_error_title": "Lỗi phát",
        "status_session_cancelled": "Đã hủy và đặt lại phiên ABX",
        "diag_none": "Chưa có phiên nào được tiền xử lý.",
        "nothing_export_title": "Không có dữ liệu để xuất",
        "nothing_export_text": "Chưa có kết quả lượt nào.",
        "export_failed_title": "Xuất thất bại",
        "json_filter": "JSON (*.json)",
        "csv_filter": "CSV (*.csv)",
        "save_json": "Xuất JSON",
        "save_csv": "Xuất CSV",
        "status_ready": "sẵn sàng",
        "labels_fixed": "nhãn=cố định",
        "labels_blinded": "nhãn=ẩn",
        "diag_session_summary": "Tóm tắt phiên",
        "diag_input": "Đầu vào",
        "diag_mode": "Chế độ",
        "diag_target_sr": "Tần số mục tiêu",
        "diag_resample_engine": "Engine resample",
        "diag_trials": "Số lượt",
        "diag_correct": "Đúng",
        "diag_trial_details": "Chi tiết lượt",
        "diag_processing_mode": "Chế độ xử lý",
        "diag_pipeline_stages_a": "Số tầng pipeline A",
        "diag_pipeline_stages_b": "Số tầng pipeline B",
        "diag_bandwidth_limit_a": "Giới hạn băng thông A",
        "diag_bandwidth_cutoff_a": "Điểm cắt A",
        "diag_bandwidth_limit_b": "Giới hạn băng thông B",
        "diag_bandwidth_cutoff_b": "Điểm cắt B",
        "diag_pipeline_a_stages": "Các tầng pipeline A",
        "diag_pipeline_b_stages": "Các tầng pipeline B",
        "diag_encode_engine": "Mã hóa",
        "diag_decode_engine": "Giải mã",
        "diag_requested": "Yêu cầu",
        "diag_effective": "Thực tế",
        "diag_no_trials": "(chưa có lượt trả lời nào)",
        "diag_mapping_audit": "--- Kiểm tra ánh xạ ---",
        "diag_current_mapping": "Ánh xạ hiện tại",
        "diag_no_mapping_entries": "(chưa có dữ liệu ánh xạ)",
        "zoom": "Thu phóng",
        "zoom_out": "A-",
        "zoom_reset": "100%",
        "zoom_in": "A+",
        "dark_mode": "Chế độ tối (mặc định)",
        "oled_mode": "Chế độ OLED",
        "fullscreen_enter": "Toàn màn hình",
        "fullscreen_exit": "Cửa sổ",
    },
}


class PrepareWorker(QObject):
    finished = pyqtSignal(object, object, object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        pipeline: AudioPipeline,
        input_path: str,
        codec_a: str,
        br_a: int,
        codec_b: str,
        br_b: int,
        mode: SampleRateMode,
        work_dir: str,
        pipeline_stages_a: list[PipelineStageConfig],
        pipeline_stages_b: list[PipelineStageConfig],
        bandwidth_limit_a_enabled: bool,
        bandwidth_limit_a_hz: Optional[int],
        bandwidth_limit_b_enabled: bool,
        bandwidth_limit_b_hz: Optional[int],
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.input_path = input_path
        self.codec_a = codec_a
        self.br_a = br_a
        self.codec_b = codec_b
        self.br_b = br_b
        self.mode = mode
        self.work_dir = work_dir
        self.pipeline_stages_a = list(pipeline_stages_a)
        self.pipeline_stages_b = list(pipeline_stages_b)
        self.bandwidth_limit_a_enabled = bandwidth_limit_a_enabled
        self.bandwidth_limit_a_hz = bandwidth_limit_a_hz
        self.bandwidth_limit_b_enabled = bandwidth_limit_b_enabled
        self.bandwidth_limit_b_hz = bandwidth_limit_b_hz

    def run(self) -> None:
        try:
            prepared, arr_a, arr_b = self.pipeline.prepare_session(
                input_path=self.input_path,
                codec_a_id=self.codec_a,
                bitrate_a_kbps=self.br_a,
                codec_b_id=self.codec_b,
                bitrate_b_kbps=self.br_b,
                mode=self.mode,
                work_dir=self.work_dir,
                pipeline_stages_a=self.pipeline_stages_a,
                pipeline_stages_b=self.pipeline_stages_b,
                bandwidth_limit_a_enabled=self.bandwidth_limit_a_enabled,
                bandwidth_limit_a_hz=self.bandwidth_limit_a_hz,
                bandwidth_limit_b_enabled=self.bandwidth_limit_b_enabled,
                bandwidth_limit_b_hz=self.bandwidth_limit_b_hz,
            )
            self.finished.emit(prepared, arr_a, arr_b)
        except PipelineCancelled:
            self.cancelled.emit()
        except (PipelineError, Exception) as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_language = "en"
        self._last_status_raw = "idle"
        self.setWindowTitle(self._t("window_title"))

        self._base_font_size = 11
        self._zoom_percent = 100
        self._zoom_min = 80
        self._zoom_max = 170
        self._shortcuts: list[QShortcut] = []

        self.pipeline = AudioPipeline()
        self.player = SynchronizedABXPlayer()
        self.engine = ABXEngine()
        self.logger = ExperimentLogger()

        self.catalog = codec_catalog()
        self._hidden_select_codec_ids = {"sim_aptx", "sim_aptx_hd", "sim_ldac"}
        self.codec_ids = [cid for cid in self.catalog.keys() if cid not in self._hidden_select_codec_ids]
        self.pipeline_codec_ids = [cid for cid in self.codec_ids if cid != "lossless_unprocessed"]

        self.pipeline_codec_a: list[QComboBox] = []
        self.pipeline_codec_b: list[QComboBox] = []
        self.pipeline_br_a: list[QComboBox] = []
        self.pipeline_br_b: list[QComboBox] = []
        self.stage_rows: list[tuple[QLabel, QComboBox, QComboBox, QComboBox, QComboBox]] = []

        self.prepared_session = None
        self._user_scrubbing = False
        self._resume_after_scrub = False
        self.prepare_thread: Optional[QThread] = None
        self.prepare_worker: Optional[PrepareWorker] = None
        self._active_stamp: Optional[str] = None
        self._mapping_mode_pending: str = "fixed"
        self._display_to_source = {"A": "A", "B": "B"}
        self._blind_same_mapping_streak = 0
        self._blind_base_swap_probability = 0.5
        self._blind_streak_probability_step = 0.07
        self._blind_max_swap_probability = 0.88
        self._active_work_dir: Optional[Path] = None

        self._build_ui()
        self._apply_startup_geometry()
        self._apply_visual_theme()
        self._apply_zoom()
        self._install_shortcuts()

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh_transport)
        self.timer.start()

        self._load_devices()

    def _apply_startup_geometry(self) -> None:
        # Prefer a tall launch ratio, but adapt to each monitor's available work area.
        min_w, min_h = 980, 760
        self.setMinimumSize(min_w, min_h)

        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 1000)
            return

        available = screen.availableGeometry()
        max_w = max(min_w, int(available.width() * 0.95))
        max_h = max(min_h, int(available.height() * 0.95))

        preferred_ratio_h_per_w = 1.5
        preferred_h = int(available.height() * 0.9)
        preferred_w = int(preferred_h / preferred_ratio_h_per_w)

        width = max(min_w, min(preferred_w, max_w))
        height = int(width * preferred_ratio_h_per_w)

        if height > max_h:
            height = max_h
            width = int(height / preferred_ratio_h_per_w)

        if width < min_w or height < min_h:
            width = max(min_w, min(max_w, int(available.width() * 0.85)))
            height = max(min_h, min(max_h, int(available.height() * 0.9)))

        width = int(width * 0.8)
        height = int(height * 0.86)
        width = max(min_w, min(width, max_w))
        height = max(min_h, min(height, max_h))

        self.resize(width, height)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("rootPanel")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("contentPanel")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(24, 20, 24, 24)
        main_layout.setSpacing(14)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        self.btn_lang_en = QPushButton(self._t("lang_english"))
        self.btn_lang_vi = QPushButton(self._t("lang_vietnamese"))
        self.btn_lang_en.setObjectName("langButton")
        self.btn_lang_vi.setObjectName("langButton")
        self.btn_lang_en.clicked.connect(lambda: self._set_language("en"))
        self.btn_lang_vi.clicked.connect(lambda: self._set_language("vi"))
        lang_row.addWidget(self.btn_lang_en)
        lang_row.addWidget(self.btn_lang_vi)
        lang_row.addStretch(1)

        self.lbl_zoom = QLabel(self._t("zoom"))
        self.btn_zoom_out = QPushButton(self._t("zoom_out"))
        self.btn_zoom_reset = QPushButton(self._t("zoom_reset"))
        self.btn_zoom_in = QPushButton(self._t("zoom_in"))
        self.btn_zoom_out.clicked.connect(lambda: self._set_zoom_percent(self._zoom_percent - 10))
        self.btn_zoom_reset.clicked.connect(lambda: self._set_zoom_percent(100))
        self.btn_zoom_in.clicked.connect(lambda: self._set_zoom_percent(self._zoom_percent + 10))

        self.dark_mode_toggle = QCheckBox(self._t("dark_mode"))
        self.dark_mode_toggle.setChecked(True)
        self.dark_mode_toggle.stateChanged.connect(self._on_theme_mode_changed)
        self.oled_mode_toggle = QCheckBox(self._t("oled_mode"))
        self.oled_mode_toggle.setChecked(False)
        self.oled_mode_toggle.stateChanged.connect(self._on_theme_mode_changed)

        self.btn_fullscreen = QPushButton(self._t("fullscreen_enter"))
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)

        lang_row.addWidget(self.lbl_zoom)
        lang_row.addWidget(self.btn_zoom_out)
        lang_row.addWidget(self.btn_zoom_reset)
        lang_row.addWidget(self.btn_zoom_in)
        lang_row.addWidget(self.dark_mode_toggle)
        lang_row.addWidget(self.oled_mode_toggle)
        lang_row.addWidget(self.btn_fullscreen)
        main_layout.addLayout(lang_row)

        main_layout.addWidget(self._build_input_group())
        main_layout.addWidget(self._build_codec_group())
        main_layout.addWidget(self._build_playback_group())
        main_layout.addWidget(self._build_diagnostics_group())

        self.status_label = QLabel(self._t("status_idle"))
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusBarLabel")
        main_layout.addWidget(self.status_label)
        main_layout.addStretch(1)

        scroll.setWidget(container)
        root_layout.addWidget(scroll)
        self.setCentralWidget(root)

    def _install_shortcuts(self) -> None:
        def add_shortcut(seq: str, handler) -> None:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

        add_shortcut("Ctrl++", lambda: self._set_zoom_percent(self._zoom_percent + 10))
        add_shortcut("Ctrl+=", lambda: self._set_zoom_percent(self._zoom_percent + 10))
        add_shortcut("Ctrl+Plus", lambda: self._set_zoom_percent(self._zoom_percent + 10))
        add_shortcut("Ctrl+-", lambda: self._set_zoom_percent(self._zoom_percent - 10))
        add_shortcut("Ctrl+_", lambda: self._set_zoom_percent(self._zoom_percent - 10))
        add_shortcut("Ctrl+Minus", lambda: self._set_zoom_percent(self._zoom_percent - 10))
        add_shortcut("Ctrl+0", lambda: self._set_zoom_percent(100))
        add_shortcut("F11", self._toggle_fullscreen)

    def _on_theme_mode_changed(self, _state: int) -> None:
        self._apply_visual_theme()

    def _apply_visual_theme(self) -> None:
        dark_mode_enabled = bool(getattr(self, "dark_mode_toggle", None) and self.dark_mode_toggle.isChecked())
        oled_mode_enabled = bool(getattr(self, "oled_mode_toggle", None) and self.oled_mode_toggle.isChecked())
        if dark_mode_enabled:
            if oled_mode_enabled:
                stylesheet = """
                QWidget {
                    background-color: #000000;
                    color: #ffffff;
                    font-family: "Segoe UI Variable", "Segoe UI", "Trebuchet MS";
                }
                QWidget#contentPanel {
                    background: #000000;
                }
                QGroupBox {
                    border: 1px solid #2b2b2b;
                    border-radius: 12px;
                    margin-top: 10px;
                    padding: 14px 12px 12px 12px;
                    background-color: #050505;
                    font-weight: 600;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                    color: #ffffff;
                    background-color: rgba(0, 0, 0, 0);
                }
                QLabel {
                    background: transparent;
                }
                QLabel#statusBarLabel {
                    border: 1px solid #2f2f2f;
                    border-radius: 8px;
                    background-color: #0d0d0d;
                    padding: 8px 10px;
                    font-weight: 500;
                }
                QPushButton {
                    background-color: #5865F2;
                    color: #ffffff;
                    border: none;
                    border-radius: 9px;
                    padding: 8px 12px;
                    font-weight: 600;
                    min-height: 30px;
                }
                QPushButton#langButton {
                    background-color: #3a3f4b;
                }
                QPushButton:hover {
                    background-color: #4752c4;
                }
                QPushButton:pressed {
                    background-color: #3c45a5;
                }
                QPushButton:disabled {
                    background-color: #2f3240;
                    color: #9ca1b2;
                }
                QToolButton {
                    background-color: #5865F2;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    min-width: 28px;
                    min-height: 26px;
                    font-weight: 700;
                }
                QToolButton:hover {
                    background-color: #4752c4;
                }
                QToolButton:pressed {
                    background-color: #3c45a5;
                }
                QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                    border: 1px solid #2f3136;
                    border-radius: 8px;
                    padding: 6px 8px;
                    background-color: #111214;
                    color: #ffffff;
                    selection-background-color: #5865F2;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 24px;
                }
                QSpinBox {
                    padding-right: 24px;
                }
                QSpinBox::up-button {
                    subcontrol-origin: border;
                    subcontrol-position: top right;
                    width: 18px;
                    border-left: 1px solid #2f3136;
                    border-bottom: 1px solid #2f3136;
                    background: #18191c;
                }
                QSpinBox::down-button {
                    subcontrol-origin: border;
                    subcontrol-position: bottom right;
                    width: 18px;
                    border-left: 1px solid #2f3136;
                    background: #18191c;
                }
                QSpinBox::up-button:hover,
                QSpinBox::down-button:hover {
                    background: #23252b;
                }
                QSpinBox::up-arrow {
                    image: none;
                    width: 0px;
                    height: 0px;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-bottom: 7px solid #ffffff;
                }
                QSpinBox::down-arrow {
                    image: none;
                    width: 0px;
                    height: 0px;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-top: 7px solid #ffffff;
                }
                QCheckBox {
                    spacing: 8px;
                    color: #ffffff;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 2px solid #8e9297;
                    background: #000000;
                }
                QCheckBox::indicator:checked {
                    background: #5865F2;
                    border: 2px solid #ffffff;
                }
                QCheckBox#exclusiveModeCheck {
                    border: 1px solid #3b3f45;
                    border-radius: 8px;
                    background-color: #0d0e10;
                    padding: 8px 10px;
                    font-weight: 600;
                }
                QSlider::groove:horizontal {
                    border: none;
                    background: #2f3136;
                    height: 8px;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #5865F2;
                    width: 16px;
                    margin: -4px 0;
                    border-radius: 8px;
                }
                QScrollBar:vertical {
                    width: 12px;
                    background: transparent;
                }
                QScrollBar::handle:vertical {
                    border-radius: 6px;
                    background: #3d4047;
                    min-height: 28px;
                }
                """
            else:
                stylesheet = """
                QWidget {
                    background-color: #1e1f22;
                    color: #dbdee1;
                    font-family: "Segoe UI Variable", "Segoe UI", "Trebuchet MS";
                }
                QWidget#contentPanel {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                                stop:0 #1f2023, stop:1 #2b2d31);
                }
                QGroupBox {
                    border: 1px solid #3f4147;
                    border-radius: 12px;
                    margin-top: 10px;
                    padding: 14px 12px 12px 12px;
                    background-color: #2b2d31;
                    font-weight: 600;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                    color: #f2f3f5;
                    background-color: rgba(0, 0, 0, 0);
                }
                QLabel {
                    background: transparent;
                }
                QLabel#statusBarLabel {
                    border: 1px solid #43464d;
                    border-radius: 8px;
                    background-color: #232428;
                    padding: 8px 10px;
                    font-weight: 500;
                }
                QPushButton {
                    background-color: #5865F2;
                    color: #ffffff;
                    border: none;
                    border-radius: 9px;
                    padding: 8px 12px;
                    font-weight: 600;
                    min-height: 30px;
                }
                QPushButton#langButton {
                    background-color: #4a4d55;
                }
                QPushButton:hover {
                    background-color: #4752c4;
                }
                QPushButton:pressed {
                    background-color: #3c45a5;
                }
                QPushButton:disabled {
                    background-color: #3e4048;
                    color: #a6abbb;
                }
                QToolButton {
                    background-color: #5865F2;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    min-width: 28px;
                    min-height: 26px;
                    font-weight: 700;
                }
                QToolButton:hover {
                    background-color: #4752c4;
                }
                QToolButton:pressed {
                    background-color: #3c45a5;
                }
                QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                    border: 1px solid #4a4d55;
                    border-radius: 8px;
                    padding: 6px 8px;
                    background-color: #1e1f22;
                    color: #f2f3f5;
                    selection-background-color: #5865F2;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 24px;
                }
                QSpinBox {
                    padding-right: 24px;
                }
                QSpinBox::up-button {
                    subcontrol-origin: border;
                    subcontrol-position: top right;
                    width: 18px;
                    border-left: 1px solid #4a4d55;
                    border-bottom: 1px solid #4a4d55;
                    background: #17181b;
                }
                QSpinBox::down-button {
                    subcontrol-origin: border;
                    subcontrol-position: bottom right;
                    width: 18px;
                    border-left: 1px solid #4a4d55;
                    background: #17181b;
                }
                QSpinBox::up-button:hover,
                QSpinBox::down-button:hover {
                    background: #232428;
                }
                QSpinBox::up-arrow {
                    image: none;
                    width: 0px;
                    height: 0px;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-bottom: 7px solid #f2f3f5;
                }
                QSpinBox::down-arrow {
                    image: none;
                    width: 0px;
                    height: 0px;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-top: 7px solid #f2f3f5;
                }
                QCheckBox {
                    spacing: 8px;
                    color: #f2f3f5;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 2px solid #9da1a8;
                    background: #1b1c20;
                }
                QCheckBox::indicator:checked {
                    background: #5865F2;
                    border: 2px solid #f2f3f5;
                }
                QCheckBox#exclusiveModeCheck {
                    border: 1px solid #4b4f56;
                    border-radius: 8px;
                    background-color: #232428;
                    padding: 8px 10px;
                    font-weight: 600;
                }
                QSlider::groove:horizontal {
                    border: none;
                    background: #3f4147;
                    height: 8px;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #5865F2;
                    width: 16px;
                    margin: -4px 0;
                    border-radius: 8px;
                }
                QScrollBar:vertical {
                    width: 12px;
                    background: transparent;
                }
                QScrollBar::handle:vertical {
                    border-radius: 6px;
                    background: #4a4d55;
                    min-height: 28px;
                }
                """
        else:
            stylesheet = """
            QWidget {
                background-color: #e9edf2;
                color: #1f2a3a;
                font-family: "Segoe UI Variable", "Segoe UI", "Trebuchet MS";
            }
            QWidget#contentPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 #f7fbff, stop:1 #eef2ff);
            }
            QGroupBox {
                border: 1px solid #cfd8e8;
                border-radius: 12px;
                margin-top: 10px;
                padding: 14px 12px 12px 12px;
                background-color: rgba(255, 255, 255, 0.93);
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #203354;
                background-color: rgba(255, 255, 255, 0);
            }
            QLabel {
                background: transparent;
            }
            QLabel#statusBarLabel {
                border: 1px solid #c7d1e3;
                border-radius: 8px;
                background-color: rgba(255, 255, 255, 0.86);
                padding: 8px 10px;
                font-weight: 500;
            }
            QPushButton {
                background-color: #2f6db2;
                color: white;
                border: none;
                border-radius: 9px;
                padding: 8px 12px;
                font-weight: 600;
                min-height: 30px;
            }
            QPushButton#langButton {
                background-color: #3f4f66;
            }
            QPushButton:hover {
                background-color: #265c97;
            }
            QPushButton:pressed {
                background-color: #1f4f82;
            }
            QPushButton:disabled {
                background-color: #8d9bb0;
                color: #e6ebf3;
            }
            QToolButton {
                background-color: #2f6db2;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                min-width: 28px;
                min-height: 26px;
                font-weight: 700;
            }
            QToolButton:hover {
                background-color: #265c97;
            }
            QToolButton:pressed {
                background-color: #1f4f82;
            }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                border: 1px solid #bcc8dd;
                border-radius: 8px;
                padding: 6px 8px;
                background-color: #ffffff;
                selection-background-color: #2f6db2;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QSpinBox {
                padding-right: 24px;
            }
            QSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 18px;
                border-left: 1px solid #bcc8dd;
                border-bottom: 1px solid #bcc8dd;
                background: #edf2fb;
            }
            QSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 18px;
                border-left: 1px solid #bcc8dd;
                background: #edf2fb;
            }
            QSpinBox::up-button:hover,
            QSpinBox::down-button:hover {
                background: #e4ecf9;
            }
            QSpinBox::up-arrow {
                image: none;
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-bottom: 7px solid #1f2a3a;
            }
            QSpinBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 7px solid #1f2a3a;
            }
            QCheckBox {
                spacing: 8px;
                color: #1f2a3a;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #5b7698;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #2f6db2;
                border: 2px solid #1f4f82;
            }
            QCheckBox#exclusiveModeCheck {
                border: 1px solid #c4d1e5;
                border-radius: 8px;
                background-color: rgba(255, 255, 255, 0.92);
                padding: 8px 10px;
                font-weight: 600;
            }
            QSlider::groove:horizontal {
                border: none;
                background: #d3dbe8;
                height: 8px;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #2f6db2;
                width: 16px;
                margin: -4px 0;
                border-radius: 8px;
            }
            QScrollBar:vertical {
                width: 12px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                border-radius: 6px;
                background: #a8b6cd;
                min-height: 28px;
            }
            """
        self.setStyleSheet(stylesheet)

    def _set_zoom_percent(self, percent: int) -> None:
        clamped = max(self._zoom_min, min(self._zoom_max, int(percent)))
        if clamped == self._zoom_percent:
            return
        self._zoom_percent = clamped
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        scaled = round(self._base_font_size * self._zoom_percent / 100.0, 1)
        app = QApplication.instance()
        if app is not None:
            font = QFont("Segoe UI Variable")
            font.setPointSizeF(scaled)
            app.setFont(font)
            app.setStyleSheet("")
        # Re-apply theme after changing app font to ensure style consistency.
        self._apply_visual_theme()
        if hasattr(self, "btn_zoom_reset"):
            self.btn_zoom_reset.setText(f"{self._zoom_percent}%")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._update_fullscreen_button_text()

    def _update_fullscreen_button_text(self) -> None:
        if not hasattr(self, "btn_fullscreen"):
            return
        key = "fullscreen_exit" if self.isFullScreen() else "fullscreen_enter"
        self.btn_fullscreen.setText(self._t(key))

    def _t(self, key: str) -> str:
        return TRANSLATIONS.get(self.current_language, TRANSLATIONS["en"]).get(
            key,
            TRANSLATIONS["en"].get(key, key),
        )

    def _set_language(self, lang: str) -> None:
        if lang not in TRANSLATIONS:
            return
        self.current_language = lang
        self._apply_language()

    def _populate_sr_mode_items(self) -> None:
        current_mode = self.sr_mode.currentData()
        self.sr_mode.blockSignals(True)
        self.sr_mode.clear()
        self.sr_mode.addItem(self._t("sr_native"), SampleRateMode.NATIVE)
        self.sr_mode.addItem(self._t("sr_force_48k"), SampleRateMode.FORCE_48K)
        if current_mode is not None:
            idx = self.sr_mode.findData(current_mode)
            if idx >= 0:
                self.sr_mode.setCurrentIndex(idx)
        self.sr_mode.blockSignals(False)

    def _populate_mapping_mode_items(self) -> None:
        current_mode = self.ab_mapping_mode.currentData()
        self.ab_mapping_mode.blockSignals(True)
        self.ab_mapping_mode.clear()
        self.ab_mapping_mode.addItem(self._t("map_fixed"), "fixed")
        self.ab_mapping_mode.addItem(self._t("map_blind"), "blind_random")
        if current_mode is not None:
            idx = self.ab_mapping_mode.findData(current_mode)
            if idx >= 0:
                self.ab_mapping_mode.setCurrentIndex(idx)
        self.ab_mapping_mode.blockSignals(False)

    def _apply_language(self) -> None:
        self.setWindowTitle(self._t("window_title"))
        self.btn_lang_en.setText(self._t("lang_english"))
        self.btn_lang_vi.setText(self._t("lang_vietnamese"))

        self.group_input.setTitle(self._t("group_input"))
        self.input_path.setPlaceholderText(self._t("input_placeholder"))
        self.btn_browse.setText(self._t("browse"))
        self.lbl_audio_file.setText(self._t("audio_file"))
        self.lbl_sample_rate_mode.setText(self._t("sample_rate_mode"))
        self.lbl_device.setText(self._t("device"))
        self.btn_refresh_devices.setText(self._t("refresh_devices"))
        self.exclusive_mode.setText(self._t("exclusive"))
        self.prepare_btn.setText(self._t("prepare"))
        self.cancel_prepare_btn.setText(self._t("cancel_prepare"))
        self.export_json_btn.setText(self._t("export_json"))
        self.export_csv_btn.setText(self._t("export_csv"))
        self._populate_sr_mode_items()

        self.group_codec.setTitle(self._t("group_codec"))
        self.lbl_codec_a.setText(self._t("codec_a"))
        self.lbl_codec_b.setText(self._t("codec_b"))
        self.lbl_bitrate_a.setText(self._t("bitrate_a"))
        self.lbl_bitrate_b.setText(self._t("bitrate_b"))
        self.lbl_mapping_mode.setText(self._t("mapping_mode"))
        self.lbl_stage_count_a.setText(self._t("stage_count_a"))
        self.lbl_stage_count_b.setText(self._t("stage_count_b"))
        self.bandwidth_limit_a_enabled.setText(self._t("bandwidth_limit_a"))
        self.bandwidth_limit_b_enabled.setText(self._t("bandwidth_limit_b"))
        self.lbl_bandwidth_cutoff_a.setText(self._t("bandwidth_cutoff_a"))
        self.lbl_bandwidth_cutoff_b.setText(self._t("bandwidth_cutoff_b"))
        self.lbl_pipeline_stages.setText(self._t("pipeline_stages"))
        self.lbl_side_a.setText(self._t("side_a"))
        self.lbl_side_b.setText(self._t("side_b"))
        self.lbl_stage_codec_a.setText(self._t("stage_codec"))
        self.lbl_stage_bitrate_a.setText(self._t("stage_bitrate"))
        self.lbl_stage_codec_b.setText(self._t("stage_codec"))
        self.lbl_stage_bitrate_b.setText(self._t("stage_bitrate"))
        for idx, row in enumerate(self.stage_rows):
            row[0].setText(f"{self._t('stage_label')} {idx + 1}")
            self._refresh_pipeline_bitrate("A", idx)
            self._refresh_pipeline_bitrate("B", idx)
        self._populate_mapping_mode_items()
        self._refresh_bitrate_a()
        self._refresh_bitrate_b()
        self._refresh_pipeline_controls()

        self.group_playback.setTitle(self._t("group_abx_tools"))
        self.btn_play_a.setText(self._t("play_a"))
        self.btn_play_b.setText(self._t("play_b"))
        self.btn_play_x.setText(self._t("play_x"))
        self.btn_pause.setText(self._t("pause"))
        self.btn_stop.setText(self._t("stop"))
        self.loop_enabled.setText(self._t("loop"))
        self.lbl_loop_start.setText(self._t("start_sec"))
        self.lbl_loop_end.setText(self._t("end_sec"))

        self.answer_a.setText(self._t("x_eq_a"))
        self.answer_b.setText(self._t("x_eq_b"))
        self.cancel_session_btn.setText(self._t("cancel_abx"))
        self._update_score_ui()

        self.group_diag.setTitle(self._t("group_diag"))
        self.diagnostics_view.setPlaceholderText(self._t("diag_placeholder"))
        self.lbl_zoom.setText(self._t("zoom"))
        self.btn_zoom_out.setText(self._t("zoom_out"))
        self.btn_zoom_in.setText(self._t("zoom_in"))
        self.dark_mode_toggle.setText(self._t("dark_mode"))
        self.oled_mode_toggle.setText(self._t("oled_mode"))
        self._update_fullscreen_button_text()

        self._set_status(self._last_status_raw)
        self._refresh_diagnostics_panel()

    def _build_input_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_input"))
        self.group_input = g
        layout = QGridLayout(g)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText(self._t("input_placeholder"))
        self.input_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_browse = QPushButton(self._t("browse"))
        self.btn_browse.clicked.connect(self.on_browse)

        self.sr_mode = QComboBox()
        self._populate_sr_mode_items()
        self.sr_mode.setMinimumWidth(240)
        self.sr_mode.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.prepare_btn = QPushButton(self._t("prepare"))
        self.prepare_btn.clicked.connect(self.on_prepare)
        self.cancel_prepare_btn = QPushButton(self._t("cancel_prepare"))
        self.cancel_prepare_btn.setEnabled(False)
        self.cancel_prepare_btn.clicked.connect(self.on_cancel_prepare)
        self.export_json_btn = QPushButton(self._t("export_json"))
        self.export_csv_btn = QPushButton(self._t("export_csv"))
        self.export_json_btn.clicked.connect(self.on_export_json)
        self.export_csv_btn.clicked.connect(self.on_export_csv)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(280)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_refresh_devices = QPushButton(self._t("refresh_devices"))
        self.btn_refresh_devices.clicked.connect(self._load_devices)
        self.exclusive_mode = QCheckBox(self._t("exclusive"))
        self.exclusive_mode.setObjectName("exclusiveModeCheck")

        self.lbl_audio_file = QLabel(self._t("audio_file"))
        self.lbl_sample_rate_mode = QLabel(self._t("sample_rate_mode"))
        self.lbl_device = QLabel(self._t("device"))

        row_top = QHBoxLayout()
        row_top.setSpacing(10)
        row_top.addWidget(self.lbl_audio_file)
        row_top.addWidget(self.input_path, 1)
        row_top.addWidget(self.btn_browse)
        row_top.addWidget(self.lbl_sample_rate_mode)
        row_top.addWidget(self.sr_mode)

        row_actions = QHBoxLayout()
        row_actions.setSpacing(10)
        row_actions.addWidget(self.prepare_btn)
        row_actions.addWidget(self.cancel_prepare_btn)
        row_actions.addWidget(self.export_json_btn)
        row_actions.addWidget(self.export_csv_btn)

        row_output = QHBoxLayout()
        row_output.setSpacing(10)
        row_output.addWidget(self.lbl_device)
        row_output.addWidget(self.device_combo, 1)
        row_output.addWidget(self.btn_refresh_devices)
        row_output.addWidget(self.exclusive_mode)

        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(14)
        row_bottom.addLayout(row_actions)
        row_bottom.addStretch(1)
        row_bottom.addLayout(row_output, 1)

        layout.addLayout(row_top, 0, 0)
        layout.addLayout(row_bottom, 1, 0)
        return g

    def _build_codec_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_codec"))
        self.group_codec = g
        layout = QGridLayout(g)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.codec_a = QComboBox()
        self.codec_b = QComboBox()
        for cid in self.codec_ids:
            profile = self.catalog[cid]
            self.codec_a.addItem(profile.ui_name, cid)
            self.codec_b.addItem(profile.ui_name, cid)

        self.codec_b.setCurrentIndex(1 if self.codec_b.count() > 1 else 0)

        self.br_a = QComboBox()
        self.br_b = QComboBox()
        self.codec_a.currentIndexChanged.connect(self._refresh_bitrate_a)
        self.codec_b.currentIndexChanged.connect(self._refresh_bitrate_b)
        self._refresh_bitrate_a()
        self._refresh_bitrate_b()

        self.lbl_codec_a = QLabel(self._t("codec_a"))
        self.lbl_codec_b = QLabel(self._t("codec_b"))
        self.lbl_bitrate_a = QLabel(self._t("bitrate_a"))
        self.lbl_bitrate_b = QLabel(self._t("bitrate_b"))
        self.lbl_mapping_mode = QLabel(self._t("mapping_mode"))

        layout.addWidget(self.lbl_codec_a, 0, 0)
        layout.addWidget(self.codec_a, 0, 1)
        layout.addWidget(self.lbl_bitrate_a, 0, 2)
        layout.addWidget(self.br_a, 0, 3)

        layout.addWidget(self.lbl_codec_b, 1, 0)
        layout.addWidget(self.codec_b, 1, 1)
        layout.addWidget(self.lbl_bitrate_b, 1, 2)
        layout.addWidget(self.br_b, 1, 3)

        self.ab_mapping_mode = QComboBox()
        self._populate_mapping_mode_items()

        layout.addWidget(self.lbl_mapping_mode, 2, 0)
        layout.addWidget(self.ab_mapping_mode, 2, 1, 1, 3)

        self.lbl_stage_count_a = QLabel(self._t("stage_count_a"))
        self.stage_count_a = QSpinBox()
        self.stage_count_a.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.stage_count_a.setRange(1, 4)
        self.stage_count_a.setValue(1)
        self.stage_count_a.valueChanged.connect(self._on_stage_count_changed)
        self.stage_count_a_dec = QToolButton()
        self.stage_count_a_dec.setText("-")
        self.stage_count_a_dec.clicked.connect(lambda: self.stage_count_a.stepBy(-1))
        self.stage_count_a_inc = QToolButton()
        self.stage_count_a_inc.setText("+")
        self.stage_count_a_inc.clicked.connect(lambda: self.stage_count_a.stepBy(1))
        for btn in (self.stage_count_a_dec, self.stage_count_a_inc):
            btn.setAutoRaise(False)
            btn.setFixedWidth(28)

        self.lbl_stage_count_b = QLabel(self._t("stage_count_b"))
        self.stage_count_b = QSpinBox()
        self.stage_count_b.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.stage_count_b.setRange(1, 4)
        self.stage_count_b.setValue(1)
        self.stage_count_b.valueChanged.connect(self._on_stage_count_changed)
        self.stage_count_b_dec = QToolButton()
        self.stage_count_b_dec.setText("-")
        self.stage_count_b_dec.clicked.connect(lambda: self.stage_count_b.stepBy(-1))
        self.stage_count_b_inc = QToolButton()
        self.stage_count_b_inc.setText("+")
        self.stage_count_b_inc.clicked.connect(lambda: self.stage_count_b.stepBy(1))
        for btn in (self.stage_count_b_dec, self.stage_count_b_inc):
            btn.setAutoRaise(False)
            btn.setFixedWidth(28)

        self.bandwidth_limit_a_enabled = QCheckBox(self._t("bandwidth_limit_a"))
        self.bandwidth_limit_b_enabled = QCheckBox(self._t("bandwidth_limit_b"))
        self.bandwidth_limit_a_enabled.stateChanged.connect(self._refresh_pipeline_controls)
        self.bandwidth_limit_b_enabled.stateChanged.connect(self._refresh_pipeline_controls)

        self.lbl_bandwidth_cutoff_a = QLabel(self._t("bandwidth_cutoff_a"))
        self.bandwidth_cutoff_a = QComboBox()
        self.bandwidth_cutoff_a.addItem("14 kHz", 14000)
        self.bandwidth_cutoff_a.addItem("16 kHz", 16000)
        self.bandwidth_cutoff_a.addItem("18 kHz", 18000)

        self.lbl_bandwidth_cutoff_b = QLabel(self._t("bandwidth_cutoff_b"))
        self.bandwidth_cutoff_b = QComboBox()
        self.bandwidth_cutoff_b.addItem("14 kHz", 14000)
        self.bandwidth_cutoff_b.addItem("16 kHz", 16000)
        self.bandwidth_cutoff_b.addItem("18 kHz", 18000)

        stage_count_a_box = QHBoxLayout()
        stage_count_a_box.setSpacing(6)
        stage_count_a_box.addWidget(self.stage_count_a)
        stage_count_a_box.addWidget(self.stage_count_a_dec)
        stage_count_a_box.addWidget(self.stage_count_a_inc)

        stage_count_b_box = QHBoxLayout()
        stage_count_b_box.setSpacing(6)
        stage_count_b_box.addWidget(self.stage_count_b)
        stage_count_b_box.addWidget(self.stage_count_b_dec)
        stage_count_b_box.addWidget(self.stage_count_b_inc)

        stage_controls_row = QHBoxLayout()
        stage_controls_row.setSpacing(12)

        side_a_controls = QHBoxLayout()
        side_a_controls.setSpacing(8)
        side_a_controls.addWidget(self.lbl_stage_count_a)
        side_a_controls.addLayout(stage_count_a_box)
        side_a_controls.addWidget(self.bandwidth_limit_a_enabled)
        side_a_controls.addWidget(self.lbl_bandwidth_cutoff_a)
        side_a_controls.addWidget(self.bandwidth_cutoff_a)

        side_b_controls = QHBoxLayout()
        side_b_controls.setSpacing(8)
        side_b_controls.addWidget(self.lbl_stage_count_b)
        side_b_controls.addLayout(stage_count_b_box)
        side_b_controls.addWidget(self.bandwidth_limit_b_enabled)
        side_b_controls.addWidget(self.lbl_bandwidth_cutoff_b)
        side_b_controls.addWidget(self.bandwidth_cutoff_b)

        stage_controls_row.addLayout(side_a_controls, 1)
        stage_controls_row.addLayout(side_b_controls, 1)

        layout.addLayout(stage_controls_row, 3, 0, 1, 5)

        self.lbl_pipeline_stages = QLabel(self._t("pipeline_stages"))
        self.lbl_side_a = QLabel(self._t("side_a"))
        self.lbl_side_b = QLabel(self._t("side_b"))
        self.lbl_stage_codec_a = QLabel(self._t("stage_codec"))
        self.lbl_stage_bitrate_a = QLabel(self._t("stage_bitrate"))
        self.lbl_stage_codec_b = QLabel(self._t("stage_codec"))
        self.lbl_stage_bitrate_b = QLabel(self._t("stage_bitrate"))
        layout.addWidget(self.lbl_pipeline_stages, 4, 0)
        layout.addWidget(self.lbl_side_a, 4, 1)
        layout.addWidget(self.lbl_side_b, 4, 3)
        layout.addWidget(self.lbl_stage_codec_a, 5, 1)
        layout.addWidget(self.lbl_stage_bitrate_a, 5, 2)
        layout.addWidget(self.lbl_stage_codec_b, 5, 3)
        layout.addWidget(self.lbl_stage_bitrate_b, 5, 4)

        for idx in range(4):
            stage_label = QLabel(f"{self._t('stage_label')} {idx + 1}")

            codec_a = QComboBox()
            codec_b = QComboBox()
            for cid in self.pipeline_codec_ids:
                profile = self.catalog[cid]
                codec_a.addItem(profile.ui_name, cid)
                codec_b.addItem(profile.ui_name, cid)

            br_a = QComboBox()
            br_b = QComboBox()
            codec_a.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            codec_b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            codec_a.setMinimumWidth(240)
            codec_b.setMinimumWidth(240)
            br_a.setMinimumWidth(160)
            br_b.setMinimumWidth(160)

            codec_a.currentIndexChanged.connect(lambda _=None, i=idx: self._refresh_pipeline_bitrate("A", i))
            codec_b.currentIndexChanged.connect(lambda _=None, i=idx: self._refresh_pipeline_bitrate("B", i))

            self.pipeline_codec_a.append(codec_a)
            self.pipeline_codec_b.append(codec_b)
            self.pipeline_br_a.append(br_a)
            self.pipeline_br_b.append(br_b)
            self.stage_rows.append((stage_label, codec_a, br_a, codec_b, br_b))

            self._refresh_pipeline_bitrate("A", idx)
            self._refresh_pipeline_bitrate("B", idx)

            row = 6 + idx
            layout.addWidget(stage_label, row, 0)
            layout.addWidget(codec_a, row, 1)
            layout.addWidget(br_a, row, 2)
            layout.addWidget(codec_b, row, 3)
            layout.addWidget(br_b, row, 4)

        layout.setColumnStretch(1, 4)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 4)
        layout.setColumnStretch(4, 2)

        self._refresh_pipeline_controls()
        return g

    def _build_output_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_output"))
        self.group_output = g
        layout = QGridLayout(g)

        self.device_combo = QComboBox()
        self.btn_refresh_devices = QPushButton(self._t("refresh_devices"))
        self.btn_refresh_devices.clicked.connect(self._load_devices)

        self.exclusive_mode = QCheckBox(self._t("exclusive"))
        self.exclusive_mode.setObjectName("exclusiveModeCheck")
        self.lbl_device = QLabel(self._t("device"))

        layout.addWidget(self.lbl_device, 0, 0)
        layout.addWidget(self.device_combo, 0, 1)
        layout.addWidget(self.btn_refresh_devices, 0, 2)
        layout.addWidget(self.exclusive_mode, 1, 0, 1, 3)
        return g

    def _build_playback_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_abx_tools"))
        self.group_playback = g
        layout = QVBoxLayout(g)

        row1 = QHBoxLayout()
        self.btn_play_a = QPushButton(self._t("play_a"))
        self.btn_play_b = QPushButton(self._t("play_b"))
        self.btn_play_x = QPushButton(self._t("play_x"))
        self.btn_pause = QPushButton(self._t("pause"))
        self.btn_stop = QPushButton(self._t("stop"))

        self.btn_play_a.clicked.connect(lambda: self.on_play_source("A"))
        self.btn_play_b.clicked.connect(lambda: self.on_play_source("B"))
        self.btn_play_x.clicked.connect(lambda: self.on_play_source("X"))
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)

        for w in [self.btn_play_a, self.btn_play_b, self.btn_play_x, self.btn_pause, self.btn_stop]:
            row1.addWidget(w)
        layout.addLayout(row1)

        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 10000)
        self.timeline.sliderPressed.connect(self.on_scrub_start)
        self.timeline.sliderReleased.connect(self.on_scrub_end)

        self.time_label = QLabel("00:00.000 / 00:00.000")

        transport_row = QHBoxLayout()
        transport_row.setSpacing(10)
        self.loop_enabled = QCheckBox(self._t("loop"))
        self.loop_start = QSpinBox()
        self.loop_end = QSpinBox()
        self.loop_start.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.loop_end.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.loop_start.setRange(0, 36000)
        self.loop_end.setRange(0, 36000)
        self.loop_end.setValue(30)
        self.loop_enabled.stateChanged.connect(self.on_loop_changed)
        self.loop_start.valueChanged.connect(self.on_loop_changed)
        self.loop_end.valueChanged.connect(self.on_loop_changed)

        self.loop_start_dec = QToolButton()
        self.loop_start_dec.setText("-")
        self.loop_start_dec.clicked.connect(lambda: self.loop_start.stepBy(-1))
        self.loop_start_inc = QToolButton()
        self.loop_start_inc.setText("+")
        self.loop_start_inc.clicked.connect(lambda: self.loop_start.stepBy(1))

        self.loop_end_dec = QToolButton()
        self.loop_end_dec.setText("-")
        self.loop_end_dec.clicked.connect(lambda: self.loop_end.stepBy(-1))
        self.loop_end_inc = QToolButton()
        self.loop_end_inc.setText("+")
        self.loop_end_inc.clicked.connect(lambda: self.loop_end.stepBy(1))

        for btn in (
            self.loop_start_dec,
            self.loop_start_inc,
            self.loop_end_dec,
            self.loop_end_inc,
        ):
            btn.setAutoRaise(False)
            btn.setFixedWidth(26)

        self.lbl_loop_start = QLabel(self._t("start_sec"))
        self.lbl_loop_end = QLabel(self._t("end_sec"))

        transport_row.addWidget(self.timeline, 1)
        transport_row.addWidget(self.time_label)
        transport_row.addWidget(self.loop_enabled)
        transport_row.addWidget(self.lbl_loop_start)
        transport_row.addWidget(self.loop_start)
        transport_row.addWidget(self.loop_start_dec)
        transport_row.addWidget(self.loop_start_inc)
        transport_row.addWidget(self.lbl_loop_end)
        transport_row.addWidget(self.loop_end)
        transport_row.addWidget(self.loop_end_dec)
        transport_row.addWidget(self.loop_end_inc)

        layout.addLayout(transport_row)

        abx_row = QGridLayout()
        self.trial_label = QLabel(f"{self._t('trial')}: 0")
        self.score_label = QLabel(f"{self._t('score')}: 0/0")
        self.pvalue_label = QLabel(f"{self._t('pvalue')}: 1.0000")

        self.answer_a = QPushButton(self._t("x_eq_a"))
        self.answer_b = QPushButton(self._t("x_eq_b"))
        self.answer_a.clicked.connect(lambda: self.on_answer("A"))
        self.answer_b.clicked.connect(lambda: self.on_answer("B"))

        self.cancel_session_btn = QPushButton(self._t("cancel_abx"))
        self.cancel_session_btn.clicked.connect(self.on_cancel_session)

        abx_row.addWidget(self.trial_label, 0, 0)
        abx_row.addWidget(self.score_label, 0, 1)
        abx_row.addWidget(self.pvalue_label, 0, 2)
        abx_row.addWidget(self.answer_a, 1, 0)
        abx_row.addWidget(self.answer_b, 1, 1)
        abx_row.addWidget(self.cancel_session_btn, 1, 2)
        layout.addLayout(abx_row)
        return g

    def _build_diagnostics_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_diag"))
        self.group_diag = g
        layout = QVBoxLayout(g)

        self.diagnostics_view = QPlainTextEdit()
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setPlaceholderText(self._t("diag_placeholder"))
        self.diagnostics_view.setMinimumHeight(170)

        layout.addWidget(self.diagnostics_view)
        return g

    def _refresh_bitrate_a(self) -> None:
        cid = self.codec_a.currentData()
        self.br_a.clear()
        for br in self.catalog[cid].bitrate_options_kbps:
            if br <= 0:
                self.br_a.addItem(self._t("na_unprocessed"), br)
            else:
                self.br_a.addItem(f"{br} kbps", br)

    def _refresh_bitrate_b(self) -> None:
        cid = self.codec_b.currentData()
        self.br_b.clear()
        for br in self.catalog[cid].bitrate_options_kbps:
            if br <= 0:
                self.br_b.addItem(self._t("na_unprocessed"), br)
            else:
                self.br_b.addItem(f"{br} kbps", br)

    def _refresh_pipeline_bitrate(self, side: str, stage_index: int) -> None:
        codec_box = self.pipeline_codec_a[stage_index] if side == "A" else self.pipeline_codec_b[stage_index]
        bitrate_box = self.pipeline_br_a[stage_index] if side == "A" else self.pipeline_br_b[stage_index]
        cid = codec_box.currentData()
        if cid is None:
            return
        bitrate_box.clear()
        for br in self.catalog[cid].bitrate_options_kbps:
            if br <= 0:
                bitrate_box.addItem(self._t("na_unprocessed"), br)
            else:
                bitrate_box.addItem(f"{br} kbps", br)

    def _on_stage_count_changed(self, *_args) -> None:
        self._refresh_pipeline_controls()

    def _refresh_pipeline_controls(self, *_args) -> None:
        # Unified pipeline UI: hide legacy single-stage controls and always show stage controls.
        self.codec_a.setVisible(False)
        self.codec_b.setVisible(False)
        self.br_a.setVisible(False)
        self.br_b.setVisible(False)
        self.lbl_codec_a.setVisible(False)
        self.lbl_codec_b.setVisible(False)
        self.lbl_bitrate_a.setVisible(False)
        self.lbl_bitrate_b.setVisible(False)

        self.stage_count_a.setEnabled(True)
        self.stage_count_b.setEnabled(True)
        self.bandwidth_limit_a_enabled.setEnabled(True)
        self.bandwidth_limit_b_enabled.setEnabled(True)
        self.bandwidth_cutoff_a.setEnabled(self.bandwidth_limit_a_enabled.isChecked())
        self.bandwidth_cutoff_b.setEnabled(self.bandwidth_limit_b_enabled.isChecked())
        self.lbl_stage_count_a.setEnabled(True)
        self.lbl_stage_count_b.setEnabled(True)
        self.lbl_bandwidth_cutoff_a.setEnabled(self.bandwidth_limit_a_enabled.isChecked())
        self.lbl_bandwidth_cutoff_b.setEnabled(self.bandwidth_limit_b_enabled.isChecked())
        self.lbl_pipeline_stages.setEnabled(True)
        self.lbl_side_a.setVisible(True)
        self.lbl_side_b.setVisible(True)
        self.lbl_stage_codec_a.setVisible(True)
        self.lbl_stage_bitrate_a.setVisible(True)
        self.lbl_stage_codec_b.setVisible(True)
        self.lbl_stage_bitrate_b.setVisible(True)
        self.lbl_pipeline_stages.setVisible(True)

        active_stage_count_a = int(self.stage_count_a.value())
        active_stage_count_b = int(self.stage_count_b.value())
        for idx, row in enumerate(self.stage_rows):
            stage_label, codec_a, br_a, codec_b, br_b = row
            show_a = idx < active_stage_count_a
            show_b = idx < active_stage_count_b
            stage_label.setVisible(show_a or show_b)
            codec_a.setVisible(show_a)
            br_a.setVisible(show_a)
            codec_b.setVisible(show_b)
            br_b.setVisible(show_b)

    def _collect_pipeline_stages(self, side: str) -> list[PipelineStageConfig]:
        stage_count = int(self.stage_count_a.value()) if side == "A" else int(self.stage_count_b.value())
        stages: list[PipelineStageConfig] = []
        for idx in range(stage_count):
            if side == "A":
                cid = str(self.pipeline_codec_a[idx].currentData())
                br = int(self.pipeline_br_a[idx].currentData())
            else:
                cid = str(self.pipeline_codec_b[idx].currentData())
                br = int(self.pipeline_br_b[idx].currentData())
            stages.append(PipelineStageConfig(codec_id=cid, bitrate_kbps=br))
        return stages

    def _load_devices(self) -> None:
        self.device_combo.clear()
        for dev in self.player.list_output_devices():
            self.device_combo.addItem(format_device_label(dev), dev.device_index)

    def _get_selected_device(self) -> Optional[int]:
        if self.device_combo.currentIndex() < 0:
            return None
        return int(self.device_combo.currentData())

    def on_browse(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._t("input_placeholder"),
            "",
            "Audio Files (*.wav *.flac)",
        )
        if file_path:
            self.input_path.setText(file_path)

    def _set_status(self, text: str) -> None:
        self._last_status_raw = text
        self.status_label.setText(f"{self._t('status_prefix')}{text}")

    def _cleanup_work_dir(self) -> None:
        if self._active_work_dir is None:
            return
        shutil.rmtree(self._active_work_dir, ignore_errors=True)
        self._active_work_dir = None

    def _create_work_dir(self) -> Path:
        work_dir = Path(tempfile.mkdtemp(prefix="codec_abx_"))
        self._active_work_dir = work_dir
        return work_dir

    def on_prepare(self) -> None:
        if self.prepare_thread is not None:
            QMessageBox.information(self, self._t("already_running_title"), self._t("already_running_text"))
            return

        src = self.input_path.text().strip()
        if not src:
            QMessageBox.warning(self, self._t("input_required_title"), self._t("input_required_text"))
            return

        mode = self.sr_mode.currentData()
        pipeline_stages_a = self._collect_pipeline_stages("A")
        pipeline_stages_b = self._collect_pipeline_stages("B")
        codec_a = pipeline_stages_a[0].codec_id
        codec_b = pipeline_stages_b[0].codec_id
        br_a = int(pipeline_stages_a[0].bitrate_kbps)
        br_b = int(pipeline_stages_b[0].bitrate_kbps)
        bandwidth_a_enabled = self.bandwidth_limit_a_enabled.isChecked()
        bandwidth_b_enabled = self.bandwidth_limit_b_enabled.isChecked()
        bandwidth_a_hz = int(self.bandwidth_cutoff_a.currentData()) if bandwidth_a_enabled else None
        bandwidth_b_hz = int(self.bandwidth_cutoff_b.currentData()) if bandwidth_b_enabled else None

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._active_stamp = stamp
        self._mapping_mode_pending = str(self.ab_mapping_mode.currentData())
        self._cleanup_work_dir()
        work_dir = self._create_work_dir()

        self._set_status(self._t("status_preparing"))
        self.prepare_btn.setEnabled(False)
        self.cancel_prepare_btn.setEnabled(True)

        self.prepare_thread = QThread(self)
        self.prepare_worker = PrepareWorker(
            pipeline=self.pipeline,
            input_path=src,
            codec_a=codec_a,
            br_a=br_a,
            codec_b=codec_b,
            br_b=br_b,
            mode=mode,
            work_dir=str(work_dir),
            pipeline_stages_a=pipeline_stages_a,
            pipeline_stages_b=pipeline_stages_b,
            bandwidth_limit_a_enabled=bandwidth_a_enabled,
            bandwidth_limit_a_hz=bandwidth_a_hz,
            bandwidth_limit_b_enabled=bandwidth_b_enabled,
            bandwidth_limit_b_hz=bandwidth_b_hz,
        )
        self.prepare_worker.moveToThread(self.prepare_thread)
        self.prepare_thread.started.connect(self.prepare_worker.run)
        self.prepare_worker.finished.connect(self._on_prepare_done)
        self.prepare_worker.failed.connect(self._on_prepare_failed)
        self.prepare_worker.cancelled.connect(self._on_prepare_cancelled)
        self.prepare_worker.finished.connect(self.prepare_thread.quit)
        self.prepare_worker.failed.connect(self.prepare_thread.quit)
        self.prepare_worker.cancelled.connect(self.prepare_thread.quit)
        self.prepare_thread.finished.connect(self._cleanup_prepare_thread)
        self.prepare_thread.start()

    def on_cancel_prepare(self) -> None:
        if self.prepare_thread is None:
            return
        self.cancel_prepare_btn.setEnabled(False)
        self._set_status(self._t("status_cancelling_prepare"))
        self.pipeline.request_cancel()

    def _on_prepare_done(self, prepared, arr_a, arr_b) -> None:
        stamp = self._active_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")

        self.prepared_session = prepared
        self.player.load_buffers(arr_a, arr_b, prepared.target_sample_rate)

        mapping_mode = self._mapping_mode_pending
        self._configure_display_mapping(mapping_mode)

        self.engine = ABXEngine()
        self._sync_player_x_mapping()

        self.logger = ExperimentLogger()
        self.logger.set_session_info(
            {
                "timestamp_local": stamp,
                "input_file": prepared.input_file,
                "original_sample_rate": prepared.original_sample_rate,
                "target_sample_rate": prepared.target_sample_rate,
                "mode": prepared.mode.value,
                "processing_mode": prepared.processing_mode.value,
                "resample_engine_used": prepared.resample_engine_used,
                "pipeline_stage_count_a": prepared.pipeline_stage_count_a,
                "pipeline_stage_count_b": prepared.pipeline_stage_count_b,
                "bandwidth_limit_a_enabled": prepared.bandwidth_limit_a_enabled,
                "bandwidth_limit_a_hz": prepared.bandwidth_limit_a_hz,
                "bandwidth_limit_b_enabled": prepared.bandwidth_limit_b_enabled,
                "bandwidth_limit_b_hz": prepared.bandwidth_limit_b_hz,
                "codec_a": prepared.track_a.codec_name,
                "codec_b": prepared.track_b.codec_name,
                "bitrate_a_kbps": prepared.track_a.bitrate_kbps,
                "bitrate_b_kbps": prepared.track_b.bitrate_kbps,
                "ab_label_mapping_mode": mapping_mode,
                "ab_label_mapping": dict(self._display_to_source),
                "validation": asdict(prepared.validation),
                "track_a": asdict(prepared.track_a),
                "track_b": asdict(prepared.track_b),
            }
        )

        self.loop_end.setValue(int(prepared.duration_seconds))
        self._update_score_ui()

        validation = prepared.validation
        msg = (
            f"{self._t('status_ready')} | sr={prepared.target_sample_rate} Hz | duration={prepared.duration_seconds:.2f}s | "
            f"loudness_diff={validation.loudness_diff_db:.3f} dB | lag={validation.alignment_lag_samples} samples"
        )
        msg += f" | stagesA={prepared.pipeline_stage_count_a} | stagesB={prepared.pipeline_stage_count_b}"
        if prepared.bandwidth_limit_a_enabled and prepared.bandwidth_limit_a_hz is not None:
            msg += f" | lowpassA={prepared.bandwidth_limit_a_hz}Hz"
        if prepared.bandwidth_limit_b_enabled and prepared.bandwidth_limit_b_hz is not None:
            msg += f" | lowpassB={prepared.bandwidth_limit_b_hz}Hz"
        if mapping_mode == "fixed":
            msg += f" | {self._t('labels_fixed')}"
        else:
            msg += f" | {self._t('labels_blinded')}"
        self._set_status(msg)
        self._refresh_diagnostics_panel()

    def _configure_display_mapping(self, mapping_mode: str) -> None:
        if mapping_mode == "blind_random":
            if random.SystemRandom().random() < 0.5:
                self._display_to_source = {"A": "A", "B": "B"}
            else:
                self._display_to_source = {"A": "B", "B": "A"}
            self._blind_same_mapping_streak = 0
            return
        self._display_to_source = {"A": "A", "B": "B"}
        self._blind_same_mapping_streak = 0

    def _advance_blind_mapping_if_needed(self) -> bool:
        # In blind mode, randomize label mapping after each completed trial.
        if self._mapping_mode_pending != "blind_random":
            return False

        prev = dict(self._display_to_source)

        # Start near true-random 50/50 and only bias toward swap gradually
        # as the no-change streak grows.
        swap_probability = min(
            self._blind_base_swap_probability
            + (self._blind_streak_probability_step * self._blind_same_mapping_streak),
            self._blind_max_swap_probability,
        )
        swap = random.SystemRandom().random() < swap_probability
        if swap:
            self._display_to_source = {"A": prev["B"], "B": prev["A"]}
        else:
            self._display_to_source = dict(prev)

        changed = self._display_to_source != prev
        if changed:
            self._blind_same_mapping_streak = 0
        else:
            self._blind_same_mapping_streak += 1
        return changed

    def _resolve_display_source(self, display_label: str) -> str:
        label = display_label.strip().upper()
        if label not in ("A", "B"):
            return label
        return self._display_to_source[label]

    def _sync_player_x_mapping(self) -> None:
        x_display = self.engine.current_x_is
        x_source = self._resolve_display_source(x_display)
        self.player.set_x_mapping(x_source)

    def _on_prepare_failed(self, error_text: str) -> None:
        QMessageBox.critical(self, self._t("prepare_failed_title"), error_text)
        self._set_status(self._t("status_prepare_failed"))
        self._cleanup_work_dir()

    def _on_prepare_cancelled(self) -> None:
        self._set_status(self._t("status_prepare_cancelled"))
        self._cleanup_work_dir()

    def _cleanup_prepare_thread(self) -> None:
        if self.prepare_worker is not None:
            self.prepare_worker.deleteLater()
        if self.prepare_thread is not None:
            self.prepare_thread.deleteLater()
        self.prepare_worker = None
        self.prepare_thread = None
        self.prepare_btn.setEnabled(True)
        self.cancel_prepare_btn.setEnabled(False)

    def _start_stream_if_needed(self) -> None:
        if self.prepared_session is None:
            raise RuntimeError("No prepared session")
        if self.player.is_playing:
            return

        self.player.start(
            device_index=self._get_selected_device(),
            exclusive=self.exclusive_mode.isChecked(),
        )

    def on_play_source(self, source: str) -> None:
        if self.prepared_session is None:
            QMessageBox.warning(self, self._t("prepare_first_title"), self._t("prepare_first_text"))
            return

        try:
            resolved = self._resolve_display_source(source)
            self.player.set_active_source(resolved)
            self._start_stream_if_needed()
        except Exception as exc:
            QMessageBox.critical(self, self._t("playback_error_title"), str(exc))

    def on_pause(self) -> None:
        self.player.stop()

    def on_stop(self) -> None:
        self.player.stop()
        self.player.set_position_seconds(0.0)

    def on_scrub_start(self) -> None:
        self._user_scrubbing = True
        self._resume_after_scrub = self.player.is_playing
        if self.player.is_playing:
            self.player.stop()

    def on_scrub_end(self) -> None:
        if self.prepared_session is None:
            self._user_scrubbing = False
            return

        ratio = self.timeline.value() / 10000.0
        pos = ratio * self.player.length_seconds
        self.player.set_position_seconds(pos)

        if self._resume_after_scrub:
            try:
                self._start_stream_if_needed()
            except Exception:
                pass

        self._resume_after_scrub = False
        self._user_scrubbing = False

    def on_loop_changed(self) -> None:
        if self.prepared_session is None:
            return
        start = float(self.loop_start.value())
        end = float(self.loop_end.value())
        self.player.set_loop(self.loop_enabled.isChecked(), start, end)

    def on_answer(self, answer: str) -> None:
        if self.prepared_session is None:
            QMessageBox.warning(self, self._t("prepare_first_title"), self._t("prepare_first_text"))
            return

        x_is_before = self.engine.current_x_is
        mapping_before = dict(self._display_to_source)
        x_source_before = self._resolve_display_source(x_is_before)
        answer_source_before = self._resolve_display_source(answer)
        correct = self.engine.submit_answer(answer)

        mapping_changed = self._advance_blind_mapping_if_needed()

        stats = self.engine.stats()
        self.logger.add_trial(
            TrialResult(
                trial_index=stats.total_trials,
                x_is=x_is_before,
                answer=answer,
                correct=correct,
                timestamp_utc=self.logger.utc_now_iso(),
                mapping_a_to=mapping_before.get("A"),
                mapping_b_to=mapping_before.get("B"),
                x_source=x_source_before,
                answer_source=answer_source_before,
                mapping_changed_for_next_trial=mapping_changed,
            )
        )

        self._sync_player_x_mapping()
        self._update_score_ui()
        self._refresh_diagnostics_panel()

    def on_cancel_session(self) -> None:
        self.player.stop()
        self.player.set_position_seconds(0.0)

        self.engine = ABXEngine()
        self._sync_player_x_mapping()

        self.logger = ExperimentLogger()
        if self.prepared_session is not None:
            self.logger.set_session_info(
                {
                    "input_file": self.prepared_session.input_file,
                    "original_sample_rate": self.prepared_session.original_sample_rate,
                    "target_sample_rate": self.prepared_session.target_sample_rate,
                    "mode": self.prepared_session.mode.value,
                    "processing_mode": self.prepared_session.processing_mode.value,
                    "resample_engine_used": self.prepared_session.resample_engine_used,
                    "pipeline_stage_count_a": self.prepared_session.pipeline_stage_count_a,
                    "pipeline_stage_count_b": self.prepared_session.pipeline_stage_count_b,
                    "bandwidth_limit_a_enabled": self.prepared_session.bandwidth_limit_a_enabled,
                    "bandwidth_limit_a_hz": self.prepared_session.bandwidth_limit_a_hz,
                    "bandwidth_limit_b_enabled": self.prepared_session.bandwidth_limit_b_enabled,
                    "bandwidth_limit_b_hz": self.prepared_session.bandwidth_limit_b_hz,
                    "codec_a": self.prepared_session.track_a.codec_name,
                    "codec_b": self.prepared_session.track_b.codec_name,
                    "bitrate_a_kbps": self.prepared_session.track_a.bitrate_kbps,
                    "bitrate_b_kbps": self.prepared_session.track_b.bitrate_kbps,
                    "ab_label_mapping_mode": self._mapping_mode_pending,
                    "ab_label_mapping": dict(self._display_to_source),
                    "validation": asdict(self.prepared_session.validation),
                    "track_a": asdict(self.prepared_session.track_a),
                    "track_b": asdict(self.prepared_session.track_b),
                }
            )

        self._update_score_ui()
        self._set_status(self._t("status_session_cancelled"))
        self._refresh_diagnostics_panel()

    def _refresh_diagnostics_panel(self) -> None:
        if self.prepared_session is None:
            self.diagnostics_view.setPlainText(self._t("diag_none"))
            return

        stats = self.engine.stats()
        mapping_mode = "fixed" if self._mapping_mode_pending == "fixed" else "blind_random"
        track_a_encode, track_a_decode = self._codec_engine_labels(self.prepared_session.track_a.codec_id)
        track_b_encode, track_b_decode = self._codec_engine_labels(self.prepared_session.track_b.codec_id)

        lines = [
            self._t("diag_session_summary"),
            f"{self._t('diag_input')}: {self.prepared_session.input_file}",
            f"{self._t('diag_mode')}: {self.prepared_session.mode.value}",
            f"{self._t('diag_processing_mode')}: {self.prepared_session.processing_mode.value}",
            f"{self._t('diag_pipeline_stages_a')}: {self.prepared_session.pipeline_stage_count_a}",
            f"{self._t('diag_pipeline_stages_b')}: {self.prepared_session.pipeline_stage_count_b}",
            f"{self._t('diag_bandwidth_limit_a')}: {self.prepared_session.bandwidth_limit_a_enabled}",
            f"{self._t('diag_bandwidth_cutoff_a')}: {self.prepared_session.bandwidth_limit_a_hz}",
            f"{self._t('diag_bandwidth_limit_b')}: {self.prepared_session.bandwidth_limit_b_enabled}",
            f"{self._t('diag_bandwidth_cutoff_b')}: {self.prepared_session.bandwidth_limit_b_hz}",
            f"{self._t('diag_target_sr')}: {self.prepared_session.target_sample_rate} Hz",
            f"{self._t('diag_resample_engine')}: {self.prepared_session.resample_engine_used}",
            f"{self._t('mapping_mode')}: {mapping_mode}",
            f"{self._t('diag_trials')}: {stats.total_trials}",
            f"{self._t('diag_correct')}: {stats.correct_trials}",
            f"{self._t('pvalue')}: {stats.p_value_one_tailed:.6f}",
            "",
            f"{self._t('diag_pipeline_a_stages')}:",
        ]

        for stage in self.prepared_session.track_a.stages:
            stage_encode, stage_decode = self._codec_engine_labels(stage.codec_id)
            lines.append(
                f"A{stage.stage_index}: {stage.codec_name} @ {stage.bitrate_kbps} kbps "
                f"(sr {stage.sample_rate_in}->{stage.sample_rate_out})"
            )
            lines.append(
                f"  {self._t('diag_encode_engine')}={stage_encode} | {self._t('diag_decode_engine')}={stage_decode}"
            )

        lines.append(f"{self._t('diag_pipeline_b_stages')}:")
        for stage in self.prepared_session.track_b.stages:
            stage_encode, stage_decode = self._codec_engine_labels(stage.codec_id)
            lines.append(
                f"B{stage.stage_index}: {stage.codec_name} @ {stage.bitrate_kbps} kbps "
                f"(sr {stage.sample_rate_in}->{stage.sample_rate_out})"
            )
            lines.append(
                f"  {self._t('diag_encode_engine')}={stage_encode} | {self._t('diag_decode_engine')}={stage_decode}"
            )

        lines.extend([
            "",
            self._t("diag_trial_details"),
        ])

        if not self.logger.trials:
            lines.append(self._t("diag_no_trials"))
        else:
            for trial in self.logger.trials:
                lines.append(
                    "#"
                    + str(trial.trial_index)
                    + f" | X label={trial.x_is}"
                    + f" | Answer={trial.answer}"
                    + f" | Correct={trial.correct}"
                )

        lines.append("")
        lines.append(self._t("diag_mapping_audit"))
        lines.append(
            f"{self._t('diag_current_mapping')}: A->{self._display_to_source['A']} | B->{self._display_to_source['B']}"
        )
        if not self.logger.trials:
            lines.append(self._t("diag_no_mapping_entries"))
        else:
            for trial in self.logger.trials:
                x_source = trial.x_source if trial.x_source is not None else self._resolve_display_source(trial.x_is)
                answer_source = (
                    trial.answer_source
                    if trial.answer_source is not None
                    else self._resolve_display_source(trial.answer)
                )
                mapping_a_to = trial.mapping_a_to if trial.mapping_a_to is not None else "?"
                mapping_b_to = trial.mapping_b_to if trial.mapping_b_to is not None else "?"
                next_trial_changed = "?"
                if trial.mapping_changed_for_next_trial is not None:
                    next_trial_changed = "Yes" if trial.mapping_changed_for_next_trial else "No"

                lines.append(
                    "#"
                    + str(trial.trial_index)
                    + f" | X label={trial.x_is} (source={x_source})"
                    + f" | Answer={trial.answer} (source={answer_source})"
                    + f" | Mapping(A->{mapping_a_to}, B->{mapping_b_to})"
                    + f" | NextTrialMappingChanged={next_trial_changed}"
                )

        self.diagnostics_view.setPlainText("\n".join(lines))

    def _codec_engine_labels(self, codec_id: str) -> tuple[str, str]:
        profile = self.catalog.get(codec_id)
        if profile is None:
            return "unknown", "auto"

        if profile.passthrough_unprocessed or profile.pipeline_noop:
            return "none (passthrough)", "none (passthrough)"

        encode_engine = profile.ffmpeg_encoder
        decode_engine = profile.ffmpeg_decode_input_format or "auto"
        return encode_engine, decode_engine

    def _update_score_ui(self) -> None:
        s = self.engine.stats()
        self.trial_label.setText(f"{self._t('trial')}: {s.total_trials + 1}")
        self.score_label.setText(f"{self._t('score')}: {s.correct_trials}/{s.total_trials}")
        self.pvalue_label.setText(f"{self._t('pvalue')}: {s.p_value_one_tailed:.6f}")

    def _refresh_transport(self) -> None:
        if self.prepared_session is None:
            return

        pos = self.player.get_position_seconds()
        total = self.player.length_seconds

        if not self._user_scrubbing and total > 0:
            v = int((pos / total) * 10000)
            self.timeline.setValue(max(0, min(10000, v)))

        self.time_label.setText(f"{self._fmt_time(pos)} / {self._fmt_time(total)}")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        ms = int(seconds * 1000)
        m = ms // 60000
        s = (ms % 60000) // 1000
        r = ms % 1000
        return f"{m:02d}:{s:02d}.{r:03d}"

    def on_export_json(self) -> None:
        if not self.logger.trials:
            QMessageBox.information(self, self._t("nothing_export_title"), self._t("nothing_export_text"))
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            self._t("save_json"),
            "abx_results.json",
            self._t("json_filter"),
        )
        if not path:
            return
        try:
            self.logger.export_json(path)
            self._set_status(f"exported JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, self._t("export_failed_title"), str(exc))

    def on_export_csv(self) -> None:
        if not self.logger.trials:
            QMessageBox.information(self, self._t("nothing_export_title"), self._t("nothing_export_text"))
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            self._t("save_csv"),
            "abx_results.csv",
            self._t("csv_filter"),
        )
        if not path:
            return
        try:
            self.logger.export_csv(path)
            self._set_status(f"exported CSV: {path}")
        except Exception as exc:
            QMessageBox.critical(self, self._t("export_failed_title"), str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.prepare_thread is not None:
            self.pipeline.request_cancel()
            self.prepare_thread.quit()
            self.prepare_thread.wait(1500)
        self.player.close()
        self._cleanup_work_dir()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == event.Type.WindowStateChange:
            self._update_fullscreen_button_text()
        super().changeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

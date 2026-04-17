from __future__ import annotations

import sys
import random
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .abx_engine import ABXEngine
from .audio_pipeline import AudioPipeline, PipelineCancelled, PipelineError
from .logger import ExperimentLogger
from .models import PipelineStageConfig, ProcessingMode, SampleRateMode, TrialResult, codec_catalog
from .player import SynchronizedABXPlayer, format_device_label


# Session retention knobs (easy to tune):
# - keep at most N newest session folders
# - delete any session folder older than SESSION_MAX_AGE
SESSION_ROOT = Path("sessions")
SESSION_MAX_KEEP = 8
SESSION_MAX_AGE = timedelta(days=1)

TRANSLATIONS = {
    "en": {
        "window_title": "Codec ABX Tester",
        "lang_english": "English",
        "lang_vietnamese": "Tiếng Việt",
        "status_idle": "Status: idle",
        "status_prefix": "Status: ",
        "group_input": "1) Input",
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
        "group_codec": "2) Codec Profiles",
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
        "group_playback": "4) Playback + Shared Timeline",
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
        "group_diag": "6) Post-Session Diagnostics",
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
        "diag_no_trials": "(no trial answers submitted yet)",
        "diag_mapping_audit": "--- Mapping Audit (Scroll Down To Reveal) ---",
        "diag_current_mapping": "Current Mapping",
        "diag_no_mapping_entries": "(no mapping audit entries yet)",
    },
    "vi": {
        "window_title": "Trình kiểm tra ABX Codec",
        "lang_english": "English",
        "lang_vietnamese": "Tiếng Việt",
        "status_idle": "Trạng thái: chờ",
        "status_prefix": "Trạng thái: ",
        "group_input": "1) Đầu vào",
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
        "group_codec": "2) Cấu hình Codec",
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
        "group_playback": "4) Phát + Timeline chung",
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
        "group_diag": "6) Chẩn đoán sau phiên",
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
        "diag_no_trials": "(chưa có lượt trả lời nào)",
        "diag_mapping_audit": "--- Kiểm tra ánh xạ (cuộn xuống để xem) ---",
        "diag_current_mapping": "Ánh xạ hiện tại",
        "diag_no_mapping_entries": "(chưa có dữ liệu ánh xạ)",
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
        self.resize(1050, 700)

        self.pipeline = AudioPipeline()
        self.player = SynchronizedABXPlayer()
        self.engine = ABXEngine()
        self.logger = ExperimentLogger()

        self.catalog = codec_catalog()
        self.codec_ids = list(self.catalog.keys())
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

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh_transport)
        self.timer.start()

        self._prune_session_folders()
        self._load_devices()

    def _build_ui(self) -> None:
        root = QWidget(self)
        main_layout = QVBoxLayout(root)

        lang_row = QHBoxLayout()
        self.btn_lang_en = QPushButton(self._t("lang_english"))
        self.btn_lang_vi = QPushButton(self._t("lang_vietnamese"))
        self.btn_lang_en.clicked.connect(lambda: self._set_language("en"))
        self.btn_lang_vi.clicked.connect(lambda: self._set_language("vi"))
        lang_row.addWidget(self.btn_lang_en)
        lang_row.addWidget(self.btn_lang_vi)
        lang_row.addStretch(1)
        main_layout.addLayout(lang_row)

        main_layout.addWidget(self._build_input_group())
        main_layout.addWidget(self._build_codec_group())
        main_layout.addWidget(self._build_output_group())
        main_layout.addWidget(self._build_playback_group())
        main_layout.addWidget(self._build_abx_group())
        main_layout.addWidget(self._build_diagnostics_group())

        self.status_label = QLabel(self._t("status_idle"))
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.setCentralWidget(root)

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
        self.prepare_btn.setText(self._t("prepare"))
        self.cancel_prepare_btn.setText(self._t("cancel_prepare"))
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

        self.group_output.setTitle(self._t("group_output"))
        self.lbl_device.setText(self._t("device"))
        self.btn_refresh_devices.setText(self._t("refresh_devices"))
        self.exclusive_mode.setText(self._t("exclusive"))

        self.group_playback.setTitle(self._t("group_playback"))
        self.btn_play_a.setText(self._t("play_a"))
        self.btn_play_b.setText(self._t("play_b"))
        self.btn_play_x.setText(self._t("play_x"))
        self.btn_pause.setText(self._t("pause"))
        self.btn_stop.setText(self._t("stop"))
        self.loop_enabled.setText(self._t("loop"))
        self.lbl_loop_start.setText(self._t("start_sec"))
        self.lbl_loop_end.setText(self._t("end_sec"))

        self.group_abx.setTitle(self._t("group_abx"))
        self.answer_a.setText(self._t("x_eq_a"))
        self.answer_b.setText(self._t("x_eq_b"))
        self.export_json_btn.setText(self._t("export_json"))
        self.export_csv_btn.setText(self._t("export_csv"))
        self.cancel_session_btn.setText(self._t("cancel_abx"))
        self._update_score_ui()

        self.group_diag.setTitle(self._t("group_diag"))
        self.refresh_diag_btn.setText(self._t("refresh_diag"))
        self.diagnostics_view.setPlaceholderText(self._t("diag_placeholder"))

        self._set_status(self._last_status_raw)
        self._refresh_diagnostics_panel()

    def _build_input_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_input"))
        self.group_input = g
        layout = QGridLayout(g)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText(self._t("input_placeholder"))
        self.btn_browse = QPushButton(self._t("browse"))
        self.btn_browse.clicked.connect(self.on_browse)

        self.sr_mode = QComboBox()
        self._populate_sr_mode_items()

        self.prepare_btn = QPushButton(self._t("prepare"))
        self.prepare_btn.clicked.connect(self.on_prepare)
        self.cancel_prepare_btn = QPushButton(self._t("cancel_prepare"))
        self.cancel_prepare_btn.setEnabled(False)
        self.cancel_prepare_btn.clicked.connect(self.on_cancel_prepare)

        self.lbl_audio_file = QLabel(self._t("audio_file"))
        self.lbl_sample_rate_mode = QLabel(self._t("sample_rate_mode"))

        layout.addWidget(self.lbl_audio_file, 0, 0)
        layout.addWidget(self.input_path, 0, 1)
        layout.addWidget(self.btn_browse, 0, 2)
        layout.addWidget(self.lbl_sample_rate_mode, 1, 0)
        layout.addWidget(self.sr_mode, 1, 1, 1, 2)
        layout.addWidget(self.prepare_btn, 2, 0, 1, 3)
        layout.addWidget(self.cancel_prepare_btn, 3, 0, 1, 3)
        return g

    def _build_codec_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_codec"))
        self.group_codec = g
        layout = QGridLayout(g)

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
        self.stage_count_a.setRange(1, 4)
        self.stage_count_a.setValue(1)
        self.stage_count_a.valueChanged.connect(self._on_stage_count_changed)

        self.lbl_stage_count_b = QLabel(self._t("stage_count_b"))
        self.stage_count_b = QSpinBox()
        self.stage_count_b.setRange(1, 4)
        self.stage_count_b.setValue(1)
        self.stage_count_b.valueChanged.connect(self._on_stage_count_changed)

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

        layout.addWidget(self.lbl_stage_count_a, 3, 0)
        layout.addWidget(self.stage_count_a, 3, 1)
        layout.addWidget(self.bandwidth_limit_a_enabled, 3, 2)
        layout.addWidget(self.lbl_bandwidth_cutoff_a, 3, 3)
        layout.addWidget(self.bandwidth_cutoff_a, 3, 4)

        layout.addWidget(self.lbl_stage_count_b, 4, 0)
        layout.addWidget(self.stage_count_b, 4, 1)
        layout.addWidget(self.bandwidth_limit_b_enabled, 4, 2)
        layout.addWidget(self.lbl_bandwidth_cutoff_b, 4, 3)
        layout.addWidget(self.bandwidth_cutoff_b, 4, 4)

        self.lbl_pipeline_stages = QLabel(self._t("pipeline_stages"))
        self.lbl_side_a = QLabel(self._t("side_a"))
        self.lbl_side_b = QLabel(self._t("side_b"))
        self.lbl_stage_codec_a = QLabel(self._t("stage_codec"))
        self.lbl_stage_bitrate_a = QLabel(self._t("stage_bitrate"))
        self.lbl_stage_codec_b = QLabel(self._t("stage_codec"))
        self.lbl_stage_bitrate_b = QLabel(self._t("stage_bitrate"))
        layout.addWidget(self.lbl_pipeline_stages, 5, 0)
        layout.addWidget(self.lbl_side_a, 5, 1)
        layout.addWidget(self.lbl_side_b, 5, 3)
        layout.addWidget(self.lbl_stage_codec_a, 6, 1)
        layout.addWidget(self.lbl_stage_bitrate_a, 6, 2)
        layout.addWidget(self.lbl_stage_codec_b, 6, 3)
        layout.addWidget(self.lbl_stage_bitrate_b, 6, 4)

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

            codec_a.currentIndexChanged.connect(lambda _=None, i=idx: self._refresh_pipeline_bitrate("A", i))
            codec_b.currentIndexChanged.connect(lambda _=None, i=idx: self._refresh_pipeline_bitrate("B", i))

            self.pipeline_codec_a.append(codec_a)
            self.pipeline_codec_b.append(codec_b)
            self.pipeline_br_a.append(br_a)
            self.pipeline_br_b.append(br_b)
            self.stage_rows.append((stage_label, codec_a, br_a, codec_b, br_b))

            self._refresh_pipeline_bitrate("A", idx)
            self._refresh_pipeline_bitrate("B", idx)

            row = 7 + idx
            layout.addWidget(stage_label, row, 0)
            layout.addWidget(codec_a, row, 1)
            layout.addWidget(br_a, row, 2)
            layout.addWidget(codec_b, row, 3)
            layout.addWidget(br_b, row, 4)

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
        self.lbl_device = QLabel(self._t("device"))

        layout.addWidget(self.lbl_device, 0, 0)
        layout.addWidget(self.device_combo, 0, 1)
        layout.addWidget(self.btn_refresh_devices, 0, 2)
        layout.addWidget(self.exclusive_mode, 1, 0, 1, 3)
        return g

    def _build_playback_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_playback"))
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

        loop_row = QHBoxLayout()
        self.loop_enabled = QCheckBox(self._t("loop"))
        self.loop_start = QSpinBox()
        self.loop_end = QSpinBox()
        self.loop_start.setRange(0, 36000)
        self.loop_end.setRange(0, 36000)
        self.loop_end.setValue(30)
        self.loop_enabled.stateChanged.connect(self.on_loop_changed)
        self.loop_start.valueChanged.connect(self.on_loop_changed)
        self.loop_end.valueChanged.connect(self.on_loop_changed)

        loop_row.addWidget(self.loop_enabled)
        self.lbl_loop_start = QLabel(self._t("start_sec"))
        self.lbl_loop_end = QLabel(self._t("end_sec"))
        loop_row.addWidget(self.lbl_loop_start)
        loop_row.addWidget(self.loop_start)
        loop_row.addWidget(self.lbl_loop_end)
        loop_row.addWidget(self.loop_end)
        loop_row.addStretch(1)

        layout.addWidget(self.timeline)
        layout.addWidget(self.time_label)
        layout.addLayout(loop_row)
        return g

    def _build_abx_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_abx"))
        self.group_abx = g
        layout = QGridLayout(g)

        self.trial_label = QLabel(f"{self._t('trial')}: 0")
        self.score_label = QLabel(f"{self._t('score')}: 0/0")
        self.pvalue_label = QLabel(f"{self._t('pvalue')}: 1.0000")

        self.answer_a = QPushButton(self._t("x_eq_a"))
        self.answer_b = QPushButton(self._t("x_eq_b"))
        self.answer_a.clicked.connect(lambda: self.on_answer("A"))
        self.answer_b.clicked.connect(lambda: self.on_answer("B"))

        self.export_json_btn = QPushButton(self._t("export_json"))
        self.export_csv_btn = QPushButton(self._t("export_csv"))
        self.cancel_session_btn = QPushButton(self._t("cancel_abx"))
        self.export_json_btn.clicked.connect(self.on_export_json)
        self.export_csv_btn.clicked.connect(self.on_export_csv)
        self.cancel_session_btn.clicked.connect(self.on_cancel_session)

        layout.addWidget(self.trial_label, 0, 0)
        layout.addWidget(self.score_label, 0, 1)
        layout.addWidget(self.pvalue_label, 0, 2)
        layout.addWidget(self.answer_a, 1, 0)
        layout.addWidget(self.answer_b, 1, 1)
        layout.addWidget(self.export_json_btn, 2, 0)
        layout.addWidget(self.export_csv_btn, 2, 1)
        layout.addWidget(self.cancel_session_btn, 2, 2)
        return g

    def _build_diagnostics_group(self) -> QGroupBox:
        g = QGroupBox(self._t("group_diag"))
        self.group_diag = g
        layout = QVBoxLayout(g)

        row = QHBoxLayout()
        self.refresh_diag_btn = QPushButton(self._t("refresh_diag"))
        self.refresh_diag_btn.clicked.connect(self._refresh_diagnostics_panel)
        row.addWidget(self.refresh_diag_btn)
        row.addStretch(1)

        self.diagnostics_view = QPlainTextEdit()
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setPlaceholderText(self._t("diag_placeholder"))
        self.diagnostics_view.setMinimumHeight(170)

        layout.addLayout(row)
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

    def _prune_session_folders(self) -> None:
        if not SESSION_ROOT.exists():
            return

        now = datetime.now(timezone.utc)
        dirs = [d for d in SESSION_ROOT.iterdir() if d.is_dir()]

        # Remove expired sessions first.
        for d in dirs:
            try:
                mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if now - mtime > SESSION_MAX_AGE:
                shutil.rmtree(d, ignore_errors=True)

        # Keep only the newest N sessions.
        remaining = [d for d in SESSION_ROOT.iterdir() if d.is_dir()]
        remaining.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old_dir in remaining[SESSION_MAX_KEEP:]:
            shutil.rmtree(old_dir, ignore_errors=True)

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
        self._prune_session_folders()
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        work_dir = SESSION_ROOT / f"session_{stamp}"

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

    def _on_prepare_cancelled(self) -> None:
        self._set_status(self._t("status_prepare_cancelled"))

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
            f"{self._t('codec_a')}: {self.prepared_session.track_a.codec_name} @ {self.prepared_session.track_a.bitrate_kbps} kbps",
            f"{self._t('codec_b')}: {self.prepared_session.track_b.codec_name} @ {self.prepared_session.track_b.bitrate_kbps} kbps",
            f"{self._t('mapping_mode')}: {mapping_mode}",
            f"{self._t('diag_trials')}: {stats.total_trials}",
            f"{self._t('diag_correct')}: {stats.correct_trials}",
            f"{self._t('pvalue')}: {stats.p_value_one_tailed:.6f}",
            "",
            f"{self._t('diag_pipeline_a_stages')}:",
        ]

        for stage in self.prepared_session.track_a.stages:
            lines.append(
                f"A{stage.stage_index}: {stage.codec_name} @ {stage.bitrate_kbps} kbps "
                f"(sr {stage.sample_rate_in}->{stage.sample_rate_out})"
            )

        lines.append(f"{self._t('diag_pipeline_b_stages')}:")
        for stage in self.prepared_session.track_b.stages:
            lines.append(
                f"B{stage.stage_index}: {stage.codec_name} @ {stage.bitrate_kbps} kbps "
                f"(sr {stage.sample_rate_in}->{stage.sample_rate_out})"
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

        # Keep mapping-revealing details far below so users do not see them accidentally.
        lines.extend([""] * 28)
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
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

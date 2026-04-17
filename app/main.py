from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime
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
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .abx_engine import ABXEngine
from .audio_pipeline import AudioPipeline, PipelineCancelled, PipelineError
from .logger import ExperimentLogger
from .models import SampleRateMode, TrialResult, codec_catalog
from .player import SynchronizedABXPlayer, format_device_label


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
            )
            self.finished.emit(prepared, arr_a, arr_b)
        except PipelineCancelled:
            self.cancelled.emit()
        except (PipelineError, Exception) as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Codec ABX Tester")
        self.resize(1050, 700)

        self.pipeline = AudioPipeline()
        self.player = SynchronizedABXPlayer()
        self.engine = ABXEngine()
        self.logger = ExperimentLogger()

        self.catalog = codec_catalog()
        self.codec_ids = list(self.catalog.keys())

        self.prepared_session = None
        self._user_scrubbing = False
        self._resume_after_scrub = False
        self.prepare_thread: Optional[QThread] = None
        self.prepare_worker: Optional[PrepareWorker] = None
        self._active_stamp: Optional[str] = None

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh_transport)
        self.timer.start()

        self._load_devices()

    def _build_ui(self) -> None:
        root = QWidget(self)
        main_layout = QVBoxLayout(root)

        main_layout.addWidget(self._build_input_group())
        main_layout.addWidget(self._build_codec_group())
        main_layout.addWidget(self._build_output_group())
        main_layout.addWidget(self._build_playback_group())
        main_layout.addWidget(self._build_abx_group())

        self.status_label = QLabel("Status: idle")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.setCentralWidget(root)

    def _build_input_group(self) -> QGroupBox:
        g = QGroupBox("1) Input")
        layout = QGridLayout(g)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Select WAV or FLAC")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.on_browse)

        self.sr_mode = QComboBox()
        self.sr_mode.addItem("Native sample rate (default)", SampleRateMode.NATIVE)
        self.sr_mode.addItem("Force 48 kHz (Bluetooth simulation)", SampleRateMode.FORCE_48K)

        self.prepare_btn = QPushButton("Preprocess A/B")
        self.prepare_btn.clicked.connect(self.on_prepare)
        self.cancel_prepare_btn = QPushButton("Cancel Preprocess")
        self.cancel_prepare_btn.setEnabled(False)
        self.cancel_prepare_btn.clicked.connect(self.on_cancel_prepare)

        layout.addWidget(QLabel("Audio file"), 0, 0)
        layout.addWidget(self.input_path, 0, 1)
        layout.addWidget(browse_btn, 0, 2)
        layout.addWidget(QLabel("Sample-rate mode"), 1, 0)
        layout.addWidget(self.sr_mode, 1, 1, 1, 2)
        layout.addWidget(self.prepare_btn, 2, 0, 1, 3)
        layout.addWidget(self.cancel_prepare_btn, 3, 0, 1, 3)
        return g

    def _build_codec_group(self) -> QGroupBox:
        g = QGroupBox("2) Codec Profiles")
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

        layout.addWidget(QLabel("Codec A"), 0, 0)
        layout.addWidget(self.codec_a, 0, 1)
        layout.addWidget(QLabel("Bitrate A"), 0, 2)
        layout.addWidget(self.br_a, 0, 3)

        layout.addWidget(QLabel("Codec B"), 1, 0)
        layout.addWidget(self.codec_b, 1, 1)
        layout.addWidget(QLabel("Bitrate B"), 1, 2)
        layout.addWidget(self.br_b, 1, 3)
        return g

    def _build_output_group(self) -> QGroupBox:
        g = QGroupBox("3) Output Device")
        layout = QGridLayout(g)

        self.device_combo = QComboBox()
        refresh_btn = QPushButton("Refresh devices")
        refresh_btn.clicked.connect(self._load_devices)

        self.exclusive_mode = QCheckBox("Request exclusive mode when available")

        layout.addWidget(QLabel("Device"), 0, 0)
        layout.addWidget(self.device_combo, 0, 1)
        layout.addWidget(refresh_btn, 0, 2)
        layout.addWidget(self.exclusive_mode, 1, 0, 1, 3)
        return g

    def _build_playback_group(self) -> QGroupBox:
        g = QGroupBox("4) Playback + Shared Timeline")
        layout = QVBoxLayout(g)

        row1 = QHBoxLayout()
        self.btn_play_a = QPushButton("Play A")
        self.btn_play_b = QPushButton("Play B")
        self.btn_play_x = QPushButton("Play X")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")

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
        self.loop_enabled = QCheckBox("Loop")
        self.loop_start = QSpinBox()
        self.loop_end = QSpinBox()
        self.loop_start.setRange(0, 36000)
        self.loop_end.setRange(0, 36000)
        self.loop_end.setValue(30)
        self.loop_enabled.stateChanged.connect(self.on_loop_changed)
        self.loop_start.valueChanged.connect(self.on_loop_changed)
        self.loop_end.valueChanged.connect(self.on_loop_changed)

        loop_row.addWidget(self.loop_enabled)
        loop_row.addWidget(QLabel("Start (s)"))
        loop_row.addWidget(self.loop_start)
        loop_row.addWidget(QLabel("End (s)"))
        loop_row.addWidget(self.loop_end)
        loop_row.addStretch(1)

        layout.addWidget(self.timeline)
        layout.addWidget(self.time_label)
        layout.addLayout(loop_row)
        return g

    def _build_abx_group(self) -> QGroupBox:
        g = QGroupBox("5) ABX Trials")
        layout = QGridLayout(g)

        self.trial_label = QLabel("Trial: 0")
        self.score_label = QLabel("Score: 0/0")
        self.pvalue_label = QLabel("p-value (one-tailed): 1.0000")

        self.answer_a = QPushButton("X = A")
        self.answer_b = QPushButton("X = B")
        self.answer_a.clicked.connect(lambda: self.on_answer("A"))
        self.answer_b.clicked.connect(lambda: self.on_answer("B"))

        self.export_json_btn = QPushButton("Export JSON")
        self.export_csv_btn = QPushButton("Export CSV")
        self.cancel_session_btn = QPushButton("Cancel ABX Session")
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

    def _refresh_bitrate_a(self) -> None:
        cid = self.codec_a.currentData()
        self.br_a.clear()
        for br in self.catalog[cid].bitrate_options_kbps:
            if br <= 0:
                self.br_a.addItem("N/A (unprocessed)", br)
            else:
                self.br_a.addItem(f"{br} kbps", br)

    def _refresh_bitrate_b(self) -> None:
        cid = self.codec_b.currentData()
        self.br_b.clear()
        for br in self.catalog[cid].bitrate_options_kbps:
            if br <= 0:
                self.br_b.addItem("N/A (unprocessed)", br)
            else:
                self.br_b.addItem(f"{br} kbps", br)

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
            "Select WAV/FLAC",
            "",
            "Audio Files (*.wav *.flac)",
        )
        if file_path:
            self.input_path.setText(file_path)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")

    def on_prepare(self) -> None:
        if self.prepare_thread is not None:
            QMessageBox.information(self, "Already running", "Preprocessing is already running.")
            return

        src = self.input_path.text().strip()
        if not src:
            QMessageBox.warning(self, "Input required", "Please select a WAV or FLAC file.")
            return

        mode = self.sr_mode.currentData()
        codec_a = self.codec_a.currentData()
        codec_b = self.codec_b.currentData()
        br_a = int(self.br_a.currentData())
        br_b = int(self.br_b.currentData())

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._active_stamp = stamp
        work_dir = Path("sessions") / f"session_{stamp}"

        self._set_status("preprocessing and validating A/B...")
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
        self._set_status("cancelling preprocessing...")
        self.pipeline.request_cancel()

    def _on_prepare_done(self, prepared, arr_a, arr_b) -> None:
        stamp = self._active_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")

        self.prepared_session = prepared
        self.player.load_buffers(arr_a, arr_b, prepared.target_sample_rate)

        self.engine = ABXEngine()
        self.player.set_x_mapping(self.engine.current_x_is)

        self.logger = ExperimentLogger()
        self.logger.set_session_info(
            {
                "timestamp_local": stamp,
                "input_file": prepared.input_file,
                "original_sample_rate": prepared.original_sample_rate,
                "target_sample_rate": prepared.target_sample_rate,
                "mode": prepared.mode.value,
                "codec_a": prepared.track_a.codec_name,
                "codec_b": prepared.track_b.codec_name,
                "bitrate_a_kbps": prepared.track_a.bitrate_kbps,
                "bitrate_b_kbps": prepared.track_b.bitrate_kbps,
                "validation": asdict(prepared.validation),
                "track_a": asdict(prepared.track_a),
                "track_b": asdict(prepared.track_b),
            }
        )

        self.loop_end.setValue(int(prepared.duration_seconds))
        self._update_score_ui()

        validation = prepared.validation
        msg = (
            f"ready | sr={prepared.target_sample_rate} Hz | duration={prepared.duration_seconds:.2f}s | "
            f"loudness_diff={validation.loudness_diff_db:.3f} dB | lag={validation.alignment_lag_samples} samples"
        )
        self._set_status(msg)

    def _on_prepare_failed(self, error_text: str) -> None:
        QMessageBox.critical(self, "Preparation failed", error_text)
        self._set_status("preprocessing failed")

    def _on_prepare_cancelled(self) -> None:
        self._set_status("preprocessing cancelled")

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
            QMessageBox.warning(self, "Prepare first", "Please preprocess A/B first.")
            return

        try:
            self.player.set_active_source(source)
            self._start_stream_if_needed()
        except Exception as exc:
            QMessageBox.critical(self, "Playback error", str(exc))

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
            QMessageBox.warning(self, "Prepare first", "Please preprocess A/B first.")
            return

        x_is_before = self.engine.current_x_is
        correct = self.engine.submit_answer(answer)

        stats = self.engine.stats()
        self.logger.add_trial(
            TrialResult(
                trial_index=stats.total_trials,
                x_is=x_is_before,
                answer=answer,
                correct=correct,
                timestamp_utc=self.logger.utc_now_iso(),
            )
        )

        self.player.set_x_mapping(self.engine.current_x_is)
        self._update_score_ui()

    def on_cancel_session(self) -> None:
        self.player.stop()
        self.player.set_position_seconds(0.0)

        self.engine = ABXEngine()
        self.player.set_x_mapping(self.engine.current_x_is)

        self.logger = ExperimentLogger()
        if self.prepared_session is not None:
            self.logger.set_session_info(
                {
                    "input_file": self.prepared_session.input_file,
                    "original_sample_rate": self.prepared_session.original_sample_rate,
                    "target_sample_rate": self.prepared_session.target_sample_rate,
                    "mode": self.prepared_session.mode.value,
                    "codec_a": self.prepared_session.track_a.codec_name,
                    "codec_b": self.prepared_session.track_b.codec_name,
                    "bitrate_a_kbps": self.prepared_session.track_a.bitrate_kbps,
                    "bitrate_b_kbps": self.prepared_session.track_b.bitrate_kbps,
                    "validation": asdict(self.prepared_session.validation),
                    "track_a": asdict(self.prepared_session.track_a),
                    "track_b": asdict(self.prepared_session.track_b),
                }
            )

        self._update_score_ui()
        self._set_status("ABX session cancelled and reset")

    def _update_score_ui(self) -> None:
        s = self.engine.stats()
        self.trial_label.setText(f"Trial: {s.total_trials + 1}")
        self.score_label.setText(f"Score: {s.correct_trials}/{s.total_trials}")
        self.pvalue_label.setText(f"p-value (one-tailed): {s.p_value_one_tailed:.6f}")

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
            QMessageBox.information(self, "Nothing to export", "No trial results yet.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", "abx_results.json", "JSON (*.json)")
        if not path:
            return
        try:
            self.logger.export_json(path)
            self._set_status(f"exported JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def on_export_csv(self) -> None:
        if not self.logger.trials:
            QMessageBox.information(self, "Nothing to export", "No trial results yet.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "abx_results.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            self.logger.export_csv(path)
            self._set_status(f"exported CSV: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

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

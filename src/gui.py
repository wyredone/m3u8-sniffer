from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QComboBox,
    QProgressBar,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .browser_worker import BrowserThread
from .command_utils import build_ffmpeg_command, build_vlc_command, build_ytdlp_command
from .export_utils import export_csv, export_json
from .stream_tester import test_hls_record
from .download_engine import download_record

APP_NAME = "M3U8 Sniffer TV"
APP_VERSION = "1.2.0"

TABLE_COLUMNS = [
    "ID",
    "Time",
    "Score",
    "Type",
    "Status",
    "Resolution",
    "Bandwidth",
    "Host",
    "M3U8 URL",
    "Page URL",
    "Referer",
    "Validation",
    "FPS",
    "Codec",
    "Audio",
]


class BatchTestThread(QThread):
    record_tested = Signal(dict, dict)  # (original_record, test_result)
    finished_all = Signal(int)

    def __init__(self, records: list[dict[str, Any]], parent: Any = None) -> None:
        super().__init__(parent)
        self.records = [dict(record) for record in records]

    def run(self) -> None:
        tested_count = 0
        for record in self.records:
            if self.isInterruptionRequested():
                break
            if not record.get("m3u8_url"):
                continue
            res = test_hls_record(record, timeout=8)
            self.record_tested.emit(record, res)
            tested_count += 1
        self.finished_all.emit(tested_count)


class DownloadThread(QThread):
    progress_signal = Signal(str)
    metrics_signal = Signal(dict)
    finished_signal = Signal(bool, str)

    def __init__(self, record: dict[str, Any], output_path: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.record = record
        self.output_path = output_path

    def run(self) -> None:
        try:
            download_record(self.record, self.output_path, self.progress_signal.emit, self.isInterruptionRequested, self.metrics_signal.emit)
            self.finished_signal.emit(True, "Download completed successfully!")
        except Exception as exc:
            self.finished_signal.emit(False, f"Download stopped: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1450, 850)

        self.browser_thread: BrowserThread | None = None
        self.batch_thread: BatchTestThread | None = None
        self.download_thread: DownloadThread | None = None
        self.records: list[dict[str, Any]] = []
        self.record_index: dict[str, int] = {}
        self.capture_enabled = True
        self.auto_thread = None
        self.validation_pending = {}
        self.validation_epoch = 0
        self.last_download_folder = None

        data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "M3U8SnifferTV"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("m3u8_sniffer")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = RotatingFileHandler(data_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            self.logger.addHandler(handler)
        self._build_ui()
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.start_auto_validation)
        self.auto_timer.start(500)
        self._build_menu()
        self._apply_style()
        self._log("Ready. Paste a video page URL and click Open Browser.")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")

        export_json_action = QAction("Export JSON", self)
        export_json_action.triggered.connect(self.export_json_clicked)
        file_menu.addAction(export_json_action)

        export_csv_action = QAction("Export CSV", self)
        export_csv_action.triggered.connect(self.export_csv_clicked)
        file_menu.addAction(export_csv_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = self.menuBar().addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        self.setCentralWidget(root)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste page/video URL here")
        self.url_input.returnPressed.connect(self.open_browser_clicked)

        self.open_button = QPushButton("Open Browser")
        self.open_button.clicked.connect(self.open_browser_clicked)

        self.start_capture_button = QPushButton("Start Capture")
        self.start_capture_button.clicked.connect(lambda: self.set_capture(True))

        self.pause_capture_button = QPushButton("Pause Capture")
        self.pause_capture_button.clicked.connect(lambda: self.set_capture(False))

        self.close_browser_button = QPushButton("Close Browser")
        self.close_browser_button.clicked.connect(self.close_browser_clicked)

        url_row.addWidget(QLabel("URL:"))
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.open_button)
        url_row.addWidget(self.start_capture_button)
        url_row.addWidget(self.pause_capture_button)
        url_row.addWidget(self.close_browser_button)
        root_layout.addLayout(url_row)

        action_row = QHBoxLayout()
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Best quality", "Up to 1080p", "Up to 720p", "Up to 480p"])
        action_row.addWidget(self.quality_combo)
        self.download_button = QPushButton("Download Video")
        self.download_button.clicked.connect(self.download_video_clicked)

        self.copy_url_button = QPushButton("Copy URL")
        self.copy_url_button.clicked.connect(self.copy_url_clicked)

        self.copy_ytdlp_button = QPushButton("Copy yt-dlp")
        self.copy_ytdlp_button.clicked.connect(self.copy_ytdlp_clicked)

        self.copy_ffmpeg_button = QPushButton("Copy FFmpeg")
        self.copy_ffmpeg_button.clicked.connect(self.copy_ffmpeg_clicked)

        self.copy_vlc_button = QPushButton("Copy VLC")
        self.copy_vlc_button.clicked.connect(self.copy_vlc_clicked)

        self.test_button = QPushButton("Test Playlist")
        self.test_button.clicked.connect(self.test_playlist_clicked)

        self.test_all_button = QPushButton("Test All")
        self.test_all_button.clicked.connect(self.test_all_playlists_clicked)

        self.open_url_button = QPushButton("Open M3U8")
        self.open_url_button.clicked.connect(self.open_selected_url_clicked)

        self.export_json_button = QPushButton("Export JSON")
        self.export_json_button.clicked.connect(self.export_json_clicked)

        self.export_csv_button = QPushButton("Export CSV")
        self.export_csv_button.clicked.connect(self.export_csv_clicked)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_clicked)

        for button in [
            self.download_button,
            self.copy_url_button,
            self.copy_ytdlp_button,
            self.copy_ffmpeg_button,
            self.copy_vlc_button,
            self.test_button,
            self.test_all_button,
            self.open_url_button,
            self.export_json_button,
            self.export_csv_button,
            self.clear_button,
        ]:
            action_row.addWidget(button)
        self.cancel_button = QPushButton("Cancel Download")
        self.cancel_button.clicked.connect(self.cancel_download)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        root_layout.addLayout(action_row)

        choices = QHBoxLayout()
        self.page_filter = QComboBox()
        self.page_filter.addItem("All source pages", "")
        self.page_filter.currentIndexChanged.connect(self.apply_page_filter)
        choices.addWidget(QLabel("Video/page group:"))
        choices.addWidget(self.page_filter, 1)
        self.refresh_button = QPushButton("Refresh Stream")
        self.refresh_button.clicked.connect(self.refresh_stream)
        choices.addWidget(self.refresh_button)
        self.output_combo = QComboBox()
        self.output_combo.addItems(["Video MP4", "Audio only MP3"])
        choices.addWidget(self.output_combo)
        self.folder_button = QPushButton("Open Download Folder")
        self.folder_button.clicked.connect(self.open_download_folder)
        choices.addWidget(self.folder_button)
        root_layout.addLayout(choices)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        root_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("No download running")
        root_layout.addWidget(self.progress_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter, 1)

        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(1, 145)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 85)
        self.table.setColumnWidth(5, 105)
        self.table.setColumnWidth(6, 105)
        self.table.setColumnWidth(7, 180)
        self.table.setColumnWidth(8, 520)
        self.table.setColumnWidth(9, 360)
        self.table.setColumnWidth(10, 360)
        splitter.addWidget(self.table)

        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(bottom_splitter)

        details_group = QGroupBox("Selected Capture Details")
        details_layout = QVBoxLayout(details_group)
        self.details_box = QTextEdit()
        self.details_box.setReadOnly(True)
        self.details_box.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        details_layout.addWidget(self.details_box)
        bottom_splitter.addWidget(details_group)

        right_group = QGroupBox("Test Results / Log")
        right_layout = QGridLayout(right_group)

        test_result_group = QGroupBox("Last Playlist Test")
        test_form = QFormLayout(test_result_group)
        self.test_status_label = QLabel("Not tested")
        self.test_http_label = QLabel("")
        self.test_type_label = QLabel("")
        self.test_resolution_label = QLabel("")
        self.test_message_box = QPlainTextEdit()
        self.test_message_box.setReadOnly(True)
        self.test_message_box.setMaximumBlockCount(500)
        test_form.addRow("Result:", self.test_status_label)
        test_form.addRow("HTTP:", self.test_http_label)
        test_form.addRow("Type:", self.test_type_label)
        test_form.addRow("Resolution:", self.test_resolution_label)
        test_form.addRow("Message:", self.test_message_box)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(1000)

        right_layout.addWidget(test_result_group, 0, 0)
        right_layout.addWidget(QLabel("Log:"), 1, 0)
        right_layout.addWidget(self.log_box, 2, 0)
        bottom_splitter.addWidget(right_group)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        bottom_splitter.setStretchFactor(0, 3)
        bottom_splitter.setStretchFactor(1, 2)

        self.statusBar().showMessage("Ready")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #202124; color: #f1f3f4; }
            QWidget { font-size: 10pt; }
            QLabel, QGroupBox { color: #f1f3f4; }
            QGroupBox { border: 1px solid #5f6368; border-radius: 6px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QLineEdit, QTextEdit, QPlainTextEdit, QTableWidget {
                background: #111315;
                color: #f1f3f4;
                border: 1px solid #5f6368;
                border-radius: 4px;
                selection-background-color: #3c4043;
            }
            QHeaderView::section {
                background: #303134;
                color: #f1f3f4;
                border: 1px solid #5f6368;
                padding: 4px;
            }
            QPushButton {
                background: #303134;
                color: #f1f3f4;
                border: 1px solid #5f6368;
                border-radius: 4px;
                padding: 6px 10px;
            }
            QPushButton:hover { background: #3c4043; }
            QPushButton:pressed { background: #4a4d51; }
            QMenuBar, QMenu { background: #202124; color: #f1f3f4; }
            QMenu::item:selected { background: #3c4043; }
            """
        )

    def open_browser_clicked(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Paste a page/video URL first.")
            return

        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.open_url(url)
            self._log(f"Opening URL in existing browser: {url}")
            return

        profile_dir = str(Path.cwd() / "browser_profile")
        self.browser_thread = BrowserThread(start_url=url, profile_dir=profile_dir)
        self.browser_thread.capture_enabled = self.capture_enabled
        self.browser_thread.stream_detected.connect(self.add_or_update_record)
        self.browser_thread.status_changed.connect(self.on_status)
        self.browser_thread.error_reported.connect(self.on_error)
        self.browser_thread.page_changed.connect(self.on_page_changed)
        self.browser_thread.finished.connect(lambda: self._log("Browser worker stopped."))
        self.browser_thread.start()
        self._log("Browser worker started.")

    def set_capture(self, enabled: bool) -> None:
        self.capture_enabled = enabled
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.set_capture_enabled(enabled)
        self.statusBar().showMessage("Capture ON" if enabled else "Capture PAUSED")
        self._log("Capture ON" if enabled else "Capture PAUSED")

    def close_browser_clicked(self) -> None:
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.close_browser()
            self._log("Close browser requested.")
        else:
            self._log("No active browser to close.")

    def on_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._log(message)

    def on_error(self, message: str) -> None:
        self._log(f"ERROR: {message}")
        self.statusBar().showMessage("Error. Check log.")

    def on_page_changed(self, url: str) -> None:
        if url:
            self.statusBar().showMessage(f"Current page: {url}")

    def add_or_update_record(self, record: dict[str, Any]) -> None:
        record_id = record.get("id") or record.get("m3u8_url")
        if not record_id:
            return

        self.table.setSortingEnabled(False)
        if record_id in self.record_index:
            index = self.record_index[record_id]
            existing = self.records[index]
            merged = self._merge_records(existing, record)
            if existing.get("validation_state") == "Awaiting recapture":
                merged["validation_state"] = "Untested"
            self.records[index] = merged
            row = self._find_row_by_id(record_id)
            if row is not None:
                self._populate_row(row, merged)
        else:
            self.record_index[record_id] = len(self.records)
            self.records.append(record)
            self.table.setSortingEnabled(False)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._populate_row(row, record)
            self.table.setSortingEnabled(True)
            self._log(f"Captured HLS: {record.get('playlist_type', '')} | {record.get('m3u8_url', '')}")

        self.table.setSortingEnabled(True)
        self._sort_best_first()
        page = record.get("source_page", "")
        if page and self.page_filter.findData(page) < 0:
            self.page_filter.addItem(page, page)
        self.apply_page_filter()
        current = self.records[self.record_index[record_id]]
        if current.get("validation_state", "Untested") == "Untested":
            self.validation_pending[record_id] = dict(current)

    def variant_summary(self, record, key):
        values = list(dict.fromkeys(str(v[key]) for v in record.get("variants", []) if v.get(key)))
        return ", ".join(values) or "Unknown"

    def apply_page_filter(self):
        page = self.page_filter.currentData()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 9)
            self.table.setRowHidden(row, bool(page and item and item.text() != page))

    def start_auto_validation(self):
        if self.auto_thread and self.auto_thread.isRunning():
            return
        if not self.validation_pending:
            return
        records = list(self.validation_pending.values())
        self.validation_pending.clear()
        epoch = self.validation_epoch
        self.auto_thread = BatchTestThread(records, parent=self)
        self.auto_thread.record_tested.connect(lambda record, result: self.apply_validation(record, result, epoch))
        self.auto_thread.start()

    def apply_validation(self, record, result, epoch=None):
        if epoch is not None and epoch != self.validation_epoch:
            return
        index = self.record_index.get(record.get("id"))
        if index is None:
            return
        current = self.records[index]
        if current.get("validation_revision", 0) != record.get("validation_revision", 0):
            return
        state = "Validated" if result.get("ok") else "Expired / denied" if result.get("status_code") in {401, 403, 410} else "Failed"
        current["validation_state"] = state
        current["validation_result"] = result
        for key in ("variants", "audio", "resolution", "bandwidth", "playlist_type"):
            if result.get(key):
                current[key] = result[key]
        from .hls_utils import rank_capture
        current["score"] = rank_capture(current) + (200 if result.get("ok") else -200)
        self.table.setSortingEnabled(False)
        row = self._find_row_by_id(current["id"])
        if row is not None:
            self._populate_row(row, current)
        self.table.setSortingEnabled(True)
        self._sort_best_first()
        self.selection_changed()

    def refresh_stream(self):
        record = self.selected_record()
        page = (record or {}).get("source_page") or self.page_filter.currentData() or self.url_input.text().strip()
        if not page:
            QMessageBox.warning(self, "Missing source page", "Select a capture or enter its source page URL.")
            return
        for current in self.records:
            if current.get("source_page") == page:
                self.validation_pending.pop(current["id"], None)
                current["validation_state"] = "Awaiting recapture"
                current["score"] = -200
                current["validation_revision"] = current.get("validation_revision", 0) + 1
                row = self._find_row_by_id(current["id"])
                if row is not None:
                    self.table.setSortingEnabled(False)
                    self._populate_row(row, current)
                    self.table.setSortingEnabled(True)
        self.url_input.setText(page)
        self.set_capture(True)
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.reset_captures()
        self.open_browser_clicked()
        self._log("Revisiting source page. Start playback or sign in manually if needed; replacement streams appear as new captures.")

    def open_download_folder(self):
        if self.last_download_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_download_folder)))
        else:
            self._log("No download folder selected yet.")

    def show_download_progress(self, metrics):
        downloaded = metrics.get("downloaded_bytes") or 0
        total = metrics.get("total_bytes") or metrics.get("total_bytes_estimate") or 0
        self.progress_bar.setRange(0, 100 if total else 0)
        if total:
            self.progress_bar.setValue(min(100, int(downloaded * 100 / total)))
        speed = metrics.get("speed") or 0
        eta = metrics.get("eta")
        self.progress_label.setText(f"{downloaded / 1048576:.1f} MB / {total / 1048576:.1f} MB | {speed / 1048576:.2f} MB/s | ETA {eta if eta is not None else '?'} s")
        if metrics.get("status") == "finished":
            self.progress_label.setText("Processing output...")

    def _merge_records(self, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        merged = dict(old)
        for key, value in new.items():
            if new.get("event_source") == "request" and key in {"playlist_type", "content_type", "resolution", "bandwidth"} and old.get("event_source") == "response":
                continue
            if value not in (None, "", [], {}):
                if key == "score":
                    merged[key] = max(int(old.get("score") or 0), int(value or 0))
                elif key == "playlist_sample" and len(str(value)) > len(str(old.get(key, ""))):
                    merged[key] = value
                elif not old.get(key) or key in {"status_code", "content_type", "playlist_type", "resolution", "bandwidth", "response_headers", "request_headers", "headers_needed", "variants", "event_source", "captured_at"}:
                    merged[key] = value
        if merged.get("validation_result") and merged.get("validation_state") != "Untested":
            from .hls_utils import rank_capture
            merged["score"] = rank_capture(merged) + (200 if merged["validation_result"].get("ok") else -200)
        return merged

    def _find_row_by_id(self, record_id: str) -> int | None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == record_id:
                return row
        return None

    def _populate_row(self, row: int, record: dict[str, Any]) -> None:
        values = [
            record.get("id", ""),
            record.get("captured_at", ""),
            str(record.get("score", "")),
            record.get("playlist_type", ""),
            str(record.get("status_code", "")),
            record.get("resolution", ""),
            record.get("bandwidth", ""),
            record.get("host", ""),
            record.get("m3u8_url", ""),
            record.get("source_page", ""),
            record.get("referer", ""),
            record.get("validation_state", "Untested"),
            self.variant_summary(record, "fps"),
            self.variant_summary(record, "codecs"),
            record.get("audio", "Unknown"),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 2:
                item.setData(Qt.ItemDataRole.DisplayRole, int(record.get("score") or 0))
            item.setToolTip(str(value))
            self.table.setItem(row, column, item)

    def _sort_best_first(self) -> None:
        self.table.sortItems(2, Qt.SortOrder.DescendingOrder)

    def selection_changed(self) -> None:
        record = self.selected_record()
        if record:
            variants = record.get("variants", [])
            summary = [f"Source page: {record.get('source_page', '')}", f"Stream: {record.get('playlist_type', '')} | {record.get('validation_state', 'Untested')}"]
            for variant in variants:
                summary.append(f"• {variant.get('resolution') or 'Unknown resolution'} | {variant.get('fps') or '?'} FPS | {variant.get('codecs') or 'Unknown codec'} | audio group: {variant.get('audio_group') or 'Unknown'}")
            self.details_box.setPlainText("\n".join(summary) + "\n\n" + json.dumps(record, indent=2, ensure_ascii=False))

    def selected_record(self) -> dict[str, Any] | None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self.table.item(row, 0)
        if not id_item:
            return None
        record_id = id_item.text()
        index = self.record_index.get(record_id)
        if index is None:
            return None
        return self.records[index]

    def copy_to_clipboard(self, text: str, label: str) -> None:
        if not text:
            QMessageBox.warning(self, "Nothing to copy", f"No {label} available for the selected row.")
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied {label}.")
        self._log(f"Copied {label}.")

    def copy_url_clicked(self) -> None:
        record = self.selected_record()
        self.copy_to_clipboard(record.get("m3u8_url", "") if record else "", "M3U8 URL")

    def copy_ytdlp_clicked(self) -> None:
        record = self.selected_record()
        self.copy_to_clipboard(build_ytdlp_command(record) if record else "", "yt-dlp command")

    def copy_ffmpeg_clicked(self) -> None:
        record = self.selected_record()
        self.copy_to_clipboard(build_ffmpeg_command(record) if record else "", "PowerShell FFmpeg command")

    def copy_vlc_clicked(self) -> None:
        record = self.selected_record()
        self.copy_to_clipboard(build_vlc_command(record) if record else "", "VLC command")

    def download_video_clicked(self) -> None:
        record = self.selected_record()
        if not record:
            QMessageBox.warning(self, "No selection", "Select a captured M3U8 row to download.")
            return

        if self.download_thread and self.download_thread.isRunning():
            QMessageBox.warning(self, "Download in progress", "A video download is currently running. Please wait for it to finish.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video File",
            "audio.mp3" if self.output_combo.currentIndex() else "video.mp4",
            "MP3 Files (*.mp3)" if self.output_combo.currentIndex() else "MP4 Files (*.mp4)"
        )
        if not save_path:
            return

        self._log(f"Starting download to: {save_path}")
        self.download_button.setEnabled(False)
        self.statusBar().showMessage("Downloading video with yt-dlp...")

        self.last_download_folder = Path(save_path).parent
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        download_record_data = dict(record)
        download_record_data["audio_only"] = bool(self.output_combo.currentIndex())
        download_record_data["max_height"] = [None, 1080, 720, 480][self.quality_combo.currentIndex()]
        self.download_thread = DownloadThread(download_record_data, save_path, parent=self)
        self.download_thread.metrics_signal.connect(self.show_download_progress)
        self.download_thread.progress_signal.connect(self._log)
        self.download_thread.finished_signal.connect(self._on_download_finished)
        self.download_thread.start()

    def cancel_download(self) -> None:
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.requestInterruption()
            self._log("Download cancellation requested.")

    def _on_download_finished(self, success: bool, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        if success:
            self.progress_bar.setValue(100)
        self.progress_label.setText(message)
        self.download_button.setEnabled(True)
        self.statusBar().showMessage(message)
        self._log(message)
        if success:
            QMessageBox.information(self, "Download Complete", message)
        else:
            QMessageBox.critical(self, "Download Failed", message)

    def test_playlist_clicked(self) -> None:
        record = self.selected_record()
        if not record:
            QMessageBox.warning(self, "No selection", "Select a captured M3U8 row first.")
            return

        if self.batch_thread and self.batch_thread.isRunning():
            return
        self.test_button.setEnabled(False)
        self.test_all_button.setEnabled(False)
        self.batch_thread = BatchTestThread([record], parent=self)
        self.batch_thread.record_tested.connect(lambda rec, result: (self.apply_validation(rec, result), self.show_test_result(result)))
        self.batch_thread.finished_all.connect(self._on_batch_finished)
        self.batch_thread.start()

    def show_test_result(self, result: dict[str, Any]) -> None:
        self.test_status_label.setText("OK" if result.get("ok") else "FAILED / NEEDS SESSION")
        self.test_http_label.setText(str(result.get("status_code") or ""))
        self.test_type_label.setText(result.get("playlist_type") or "")
        resolution_bits = []
        if result.get("resolution"):
            resolution_bits.append(result["resolution"])
        if result.get("bandwidth"):
            resolution_bits.append(result["bandwidth"])
        self.test_resolution_label.setText(" | ".join(resolution_bits))
        self.test_message_box.setPlainText(
            f"{result.get('message', '')}\n\nContent-Type: {result.get('content_type', '')}\n\nSample:\n{result.get('sample', '')}"
        )
        self._log(result.get("message", "Test complete."))

    def test_all_playlists_clicked(self) -> None:
        if self.batch_thread and self.batch_thread.isRunning():
            return
        if not self.records:
            QMessageBox.information(self, "No records", "No captured streams to test.")
            return

        self._log(f"Starting batch validation for {len(self.records)} streams...")
        self.test_all_button.setEnabled(False)
        self.statusBar().showMessage("Validating streams in background...")

        self.batch_thread = BatchTestThread(self.records, parent=self)
        self.batch_thread.record_tested.connect(self._on_record_tested)
        self.batch_thread.finished_all.connect(self._on_batch_finished)
        self.batch_thread.start()

    def _on_record_tested(self, record: dict[str, Any], result: dict[str, Any]) -> None:
        self.apply_validation(record, result)
        record_id = record.get("id") or record.get("m3u8_url")
        row = self._find_row_by_id(record_id) if record_id else None

        status_str = "VALID" if result.get("ok") else f"FAILED ({result.get('status_code', 'ERR')})"
        self._log(f"Stream {record.get('m3u8_url', '')[:40]}... -> {status_str}")

        if row is not None:
            status_item = self.table.item(row, 4)
            if status_item:
                status_item.setText(f"{result.get('status_code', '')} ({'✓' if result.get('ok') else '✗'})")

    def _on_batch_finished(self, count: int) -> None:
        self.test_button.setEnabled(True)
        self.test_all_button.setEnabled(True)
        self.statusBar().showMessage(f"Batch validation complete. Tested {count} streams.")
        self._log(f"Batch validation finished ({count} streams checked).")

    def open_selected_url_clicked(self) -> None:
        record = self.selected_record()
        if not record or not record.get("m3u8_url"):
            QMessageBox.warning(self, "No URL", "Select a captured M3U8 row first.")
            return
        webbrowser.open(record["m3u8_url"])

    def export_json_clicked(self) -> None:
        if not self.records:
            QMessageBox.information(self, "No records", "No captured streams to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", "m3u8_captures.json", "JSON Files (*.json)")
        if path:
            export_json(self.records, path)
            self._log(f"Exported JSON: {path}")

    def export_csv_clicked(self) -> None:
        if not self.records:
            QMessageBox.information(self, "No records", "No captured streams to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "m3u8_captures.csv", "CSV Files (*.csv)")
        if path:
            export_csv(self.records, path)
            self._log(f"Exported CSV: {path}")

    def clear_clicked(self) -> None:
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.reset_captures()
        self.validation_epoch += 1
        self.validation_pending.clear()
        self.page_filter.clear()
        self.page_filter.addItem("All source pages", "")
        self.records.clear()
        self.record_index.clear()
        self.table.setRowCount(0)
        self.details_box.clear()
        self._log("Cleared captured stream list.")

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About M3U8 Sniffer TV",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "Windows desktop HLS/M3U8 sniffer using a visible Playwright Chromium browser.\n\n"
            "Use only for streams you own, control, or are authorized to inspect. "
            "This app does not bypass DRM, paywalls, logins, or access controls.",
        )

    def _log(self, message: str) -> None:
        safe_message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", message)
        self.log_box.appendPlainText(safe_message)
        self.logger.info(safe_message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.auto_timer.stop()
        workers = [self.browser_thread, self.batch_thread, self.download_thread, self.auto_thread]
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.close_browser()
        for worker in workers:
            if worker and worker.isRunning():
                worker.requestInterruption()
        if any(worker and worker.isRunning() for worker in workers):
            from PySide6.QtCore import QTimer
            event.ignore()
            self.statusBar().showMessage("Stopping background work before closing...")
            QTimer.singleShot(250, self.close)
            return
        event.accept()


def run_gui() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
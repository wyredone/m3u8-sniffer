from __future__ import annotations

import json
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
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

APP_NAME = "M3U8 Sniffer TV"
APP_VERSION = "1.0.0"

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
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1450, 850)

        self.browser_thread: BrowserThread | None = None
        self.records: list[dict[str, Any]] = []
        self.record_index: dict[str, int] = {}
        self.capture_enabled = True

        self._build_ui()
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

        self.open_url_button = QPushButton("Open M3U8")
        self.open_url_button.clicked.connect(self.open_selected_url_clicked)

        self.export_json_button = QPushButton("Export JSON")
        self.export_json_button.clicked.connect(self.export_json_clicked)

        self.export_csv_button = QPushButton("Export CSV")
        self.export_csv_button.clicked.connect(self.export_csv_clicked)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_clicked)

        for button in [
            self.copy_url_button,
            self.copy_ytdlp_button,
            self.copy_ffmpeg_button,
            self.copy_vlc_button,
            self.test_button,
            self.open_url_button,
            self.export_json_button,
            self.export_csv_button,
            self.clear_button,
        ]:
            action_row.addWidget(button)
        action_row.addStretch(1)
        root_layout.addLayout(action_row)

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
        self.table.setColumnWidth(4, 75)
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

        if record_id in self.record_index:
            index = self.record_index[record_id]
            existing = self.records[index]
            merged = self._merge_records(existing, record)
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

        self._sort_best_first()

    def _merge_records(self, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        merged = dict(old)
        for key, value in new.items():
            if value not in (None, "", [], {}):
                if key == "score":
                    merged[key] = max(int(old.get("score") or 0), int(value or 0))
                elif key == "playlist_sample" and len(str(value)) > len(str(old.get(key, ""))):
                    merged[key] = value
                elif not old.get(key) or key in {"status_code", "content_type", "playlist_type", "resolution", "bandwidth", "response_headers"}:
                    merged[key] = value
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
            self.details_box.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))

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
        self.copy_to_clipboard(build_ffmpeg_command(record) if record else "", "FFmpeg command")

    def copy_vlc_clicked(self) -> None:
        record = self.selected_record()
        self.copy_to_clipboard(build_vlc_command(record) if record else "", "VLC command")

    def test_playlist_clicked(self) -> None:
        record = self.selected_record()
        if not record:
            QMessageBox.warning(self, "No selection", "Select a captured M3U8 row first.")
            return

        self._log("Testing selected playlist...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = test_hls_record(record)
        finally:
            QApplication.restoreOverrideCursor()

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
        self.log_box.appendPlainText(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.browser_thread and self.browser_thread.isRunning():
            self.browser_thread.close_browser()
            self.browser_thread.wait(3000)
        event.accept()


def run_gui() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.exec()

"""Dialog showing batch transcode results."""

from __future__ import annotations

import csv
import logging
from typing import TYPE_CHECKING, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QAbstractItemView,
)

from usdb_syncer.gui import icons

if TYPE_CHECKING:
    from .batch_orchestrator import BatchTranscodeCandidate, BatchTranscodeSummary

_logger = logging.getLogger(__name__)


class BatchResultsDialog(QDialog):
    """Dialog showing batch transcode results."""

    def __init__(
        self,
        parent: QtWidgets.QMainWindow,
        candidates: list[BatchTranscodeCandidate],
        summary: BatchTranscodeSummary,
        aborted: bool = False
    ):
        super().__init__(parent)
        self.candidates = candidates
        # Only show media files that were actually selected for transcoding
        self.processed_candidates = [c for c in candidates if c.selected]
        self.summary = summary
        self.aborted = aborted
        self._setup_ui()
        self._load_results()

    def _setup_ui(self) -> None:
        """Build UI with summary and expandable details."""
        title = "Batch Transcode Results"
        if self.aborted:
            title += " (Aborted)"
        self.setWindowTitle(title)
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(800, 180)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(4)

        # 1. Summary Group
        summary_group = QtWidgets.QGroupBox("Summary")
        summary_layout = QtWidgets.QGridLayout(summary_group)
        summary_layout.setContentsMargins(10, 4, 10, 8)
        summary_layout.setVerticalSpacing(4)
        summary_layout.setHorizontalSpacing(10)
        
        success_count = sum(1 for c in self.processed_candidates if c.status == "success")
        failed_count = sum(1 for c in self.processed_candidates if c.status == "failed")
        skipped_count = sum(1 for c in self.processed_candidates if c.status == "skipped")
        aborted_count = sum(1 for c in self.processed_candidates if c.status == "aborted")
        rolled_back_count = sum(1 for c in self.processed_candidates if c.status == "rolled_back")
        
        lbl_success = QLabel("✓ Success:")
        lbl_success.setToolTip("Number of media files successfully transcoded.")
        summary_layout.addWidget(lbl_success, 0, 0)
        summary_layout.addWidget(QLabel(f"<b>{success_count}</b>"), 0, 1)
        
        lbl_failed = QLabel("✗ Failed:")
        lbl_failed.setToolTip("Number of media files that encountered an error during transcoding.")
        summary_layout.addWidget(lbl_failed, 0, 2)
        summary_layout.addWidget(QLabel(f"<b>{failed_count}</b>"), 0, 3)
        
        lbl_skipped = QLabel("⊙ Skipped:")
        lbl_skipped.setToolTip("Number of media files that were not selected for transcoding.")
        summary_layout.addWidget(lbl_skipped, 0, 4)
        summary_layout.addWidget(QLabel(f"<b>{skipped_count}</b>"), 0, 5)
        
        lbl_aborted = QLabel("⊘ Aborted:")
        lbl_aborted.setToolTip("Number of media files that were cancelled by the user.")
        summary_layout.addWidget(lbl_aborted, 0, 6)
        summary_layout.addWidget(QLabel(f"<b>{aborted_count}</b>"), 0, 7)

        lbl_rolled_back = QLabel("↺ Rolled Back:")
        lbl_rolled_back.setToolTip("Number of media files that were transcoded but then reverted due to abort.")
        summary_layout.addWidget(lbl_rolled_back, 0, 8)
        summary_layout.addWidget(QLabel(f"<b>{rolled_back_count}</b>"), 0, 9)
        
        total_time = sum(c.actual_time_seconds or 0 for c in self.candidates)
        summary_layout.addWidget(QLabel("Total Time:"), 1, 0)
        summary_layout.addWidget(QLabel(f"<b>{self._format_duration(total_time)}</b>"), 1, 1, 1, 3)
        
        # Space saved calculation
        space_saved = 0.0
        for c in self.processed_candidates:
            if c.status == "success" and c.result:
                if c.result.output_path and c.result.output_path.exists():
                    new_size = c.result.output_path.stat().st_size / (1024 * 1024)
                    space_saved += (c.current_size_mb - new_size)
        
        if space_saved >= 0:
            space_label_text = "Net Space Saved:"
            space_tooltip = "Total disk space saved across all successful transcodes (Original Size - New Size)."
            space_value = f"<b>{space_saved:.1f} MB</b>"
        else:
            space_label_text = "Net Additional Space Used:"
            space_tooltip = "Total additional disk space used across all successful transcodes (New Size - Original Size)."
            space_value = f"<b>{abs(space_saved):.1f} MB</b>"
            
        lbl_space = QLabel(space_label_text)
        lbl_space.setToolTip(space_tooltip)
        summary_layout.addWidget(lbl_space, 1, 4)
        summary_layout.addWidget(QLabel(space_value), 1, 5, 1, 3)
        
        self.layout.addWidget(summary_group)

        # 2. Details Toggle
        self.btn_show_details = QPushButton("Show Detailed Report")
        self.btn_show_details.setCheckable(True)
        self.btn_show_details.clicked.connect(self._on_toggle_details)
        self.layout.addWidget(self.btn_show_details)

        # 3. Detailed Results Table (hidden by default)
        self.details_widget = QWidget()
        details_layout = QVBoxLayout(self.details_widget)
        details_layout.setContentsMargins(0, 2, 0, 0)
        details_layout.setSpacing(2)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Status", "Title", "Artist", "Change", "Error"])
        # Configure column sizing for optimal space distribution
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Status (single char)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Title
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Artist
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Change
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # Error
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        details_layout.addWidget(self.table)
        
        self.details_widget.setVisible(False)
        self.layout.addWidget(self.details_widget)

        # 4. Action Buttons
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(2)
        
        self.btn_export = QPushButton("Export to CSV")
        self.btn_export.clicked.connect(self._export_to_csv)
        action_layout.addWidget(self.btn_export)
        
        self.btn_copy = QPushButton("Copy to Clipboard")
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        action_layout.addWidget(self.btn_copy)
        
        action_layout.addStretch()
        
        self.btn_ok = QPushButton("OK")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        action_layout.addWidget(self.btn_ok)
        
        self.layout.addLayout(action_layout)

    def _load_results(self) -> None:
        """Populate table with results."""
        self.table.setRowCount(len(self.processed_candidates))
        
        for i, c in enumerate(self.processed_candidates):
            # Status icon
            status_item = QTableWidgetItem()
            if c.status == "success":
                status_item.setText("✓")
                status_item.setForeground(QtGui.QColor("green"))
            elif c.status == "failed":
                status_item.setText("✗")
                status_item.setForeground(QtGui.QColor("red"))
            elif c.status == "aborted":
                status_item.setText("⊘")
                status_item.setForeground(QtGui.QColor("orange"))
            elif c.status == "rolled_back":
                status_item.setText("↺")
                status_item.setForeground(QtGui.QColor("blue"))
            else:
                status_item.setText("⊙")
                status_item.setForeground(QtGui.QColor("gray"))
            self.table.setItem(i, 0, status_item)
            
            self.table.setItem(i, 1, QTableWidgetItem(c.song_title))
            self.table.setItem(i, 2, QTableWidgetItem(c.artist))
            
            # Change info
            change = "-"
            if c.status == "success" and c.result and c.result.output_path and c.result.output_path.exists():
                new_size = c.result.output_path.stat().st_size / (1024 * 1024)
                target_codec = self.summary.target_codec
                if getattr(c, "media_type", "video") == "audio":
                    target_codec = self.summary.target_audio_codec
                change = f"{c.current_codec} → {target_codec}, {c.current_size_mb:.1f}MB → {new_size:.1f}MB"
            self.table.setItem(i, 3, QTableWidgetItem(change))
            
            # Error message
            self.table.setItem(i, 4, QTableWidgetItem(c.error_message or "—"))

    def _on_toggle_details(self, checked: bool) -> None:
        """Show/hide detailed report."""
        self.details_widget.setVisible(checked)
        self.btn_show_details.setText("Hide Detailed Report" if checked else "Show Detailed Report")
        if checked:
            self.resize(800, 600)
        else:
            self.resize(800, 180)

    def _export_to_csv(self) -> None:
        """Export results to CSV file."""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Results", "", "CSV Files (*.csv)"
        )
        if not path:
            return
            
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Status", "Title", "Artist", "Current Codec", "Target Codec", "Original Size (MB)", "New Size (MB)", "Time (s)", "Error"])
                for c in self.processed_candidates:
                    new_size = ""
                    if c.status == "success" and c.result and c.result.output_path and c.result.output_path.exists():
                        new_size = f"{c.result.output_path.stat().st_size / (1024 * 1024):.2f}"
                    
                    writer.writerow([
                        c.status,
                        c.song_title,
                        c.artist,
                        c.current_codec,
                        (
                            (self.summary.target_audio_codec if getattr(c, "media_type", "video") == "audio" else self.summary.target_codec)
                            if c.status == "success"
                            else ""
                        ),
                        f"{c.current_size_mb:.2f}",
                        new_size,
                        f"{c.actual_time_seconds or 0:.1f}",
                        c.error_message or ""
                    ])
            QtWidgets.QMessageBox.information(self, "Export Successful", f"Results exported to {path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Export Failed", f"Failed to export results: {e}")

    def _copy_to_clipboard(self) -> None:
        """Copy results to clipboard."""
        lines = ["Status\tTitle\tArtist\tChange\tError"]
        for c in self.processed_candidates:
            change = "-"
            if c.status == "success" and c.result and c.result.output_path and c.result.output_path.exists():
                new_size = c.result.output_path.stat().st_size / (1024 * 1024)
                target_codec = self.summary.target_codec
                if getattr(c, "media_type", "video") == "audio":
                    target_codec = self.summary.target_audio_codec
                change = f"{c.current_codec} → {target_codec}, {c.current_size_mb:.1f}MB → {new_size:.1f}MB"
            
            lines.append(f"{c.status}\t{c.song_title}\t{c.artist}\t{change}\t{c.error_message or ''}")
            
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        QtWidgets.QMessageBox.information(self, "Copied", "Results copied to clipboard.")

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

"""Progress dialog for rollback backup creation."""

from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QGridLayout,
)

from usdb_syncer.gui import icons

_logger = logging.getLogger(__name__)


class RollbackBackupProgressDialog(QDialog):
    """Progress dialog to show backup creation progress."""

    # Signals
    abort_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget, total_files: int):
        super().__init__(parent)
        self.total_files = total_files
        self.start_time = time.time()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build UI with progress bars and statistics."""
        self.setWindowTitle("Creating Rollback Backups")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.setMinimumWidth(500)
        self.setModal(True)
        
        # Remove close button from title bar
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        # 1. Title
        title_label = QLabel("Creating Rollback Backups")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title_label)
        
        layout.addSpacing(10)

        # 2. Overall Progress
        self.lbl_overall = QLabel(f"Copying 0 of {self.total_files} files")
        layout.addWidget(self.lbl_overall)
        
        self.pb_overall = QProgressBar()
        self.pb_overall.setRange(0, self.total_files)
        self.pb_overall.setValue(0)
        layout.addWidget(self.pb_overall)

        layout.addSpacing(20)

        # 3. Current File Info
        self.lbl_current_file_title = QLabel("Current file:")
        self.lbl_current_file_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.lbl_current_file_title)
        
        self.lbl_filename = QLabel("-")
        self.lbl_filename.setWordWrap(True)
        layout.addWidget(self.lbl_filename)

        layout.addSpacing(20)

        # 4. Statistics Panel
        stats_frame = QFrame()
        stats_frame.setFrameShape(QFrame.Shape.StyledPanel)
        stats_layout = QGridLayout(stats_frame)
        
        stats_layout.addWidget(QLabel("Bytes Copied:"), 0, 0)
        self.lbl_bytes = QLabel("0 B")
        stats_layout.addWidget(self.lbl_bytes, 0, 1)
        
        stats_layout.addWidget(QLabel("Elapsed Time:"), 0, 2)
        self.lbl_elapsed = QLabel("00:00:00")
        stats_layout.addWidget(self.lbl_elapsed, 0, 3)
        
        stats_layout.addWidget(QLabel("ETA:"), 1, 0)
        self.lbl_eta = QLabel("-")
        stats_layout.addWidget(self.lbl_eta, 1, 1)
        
        layout.addWidget(stats_frame)

        layout.addSpacing(30)

        # 5. Cancel Button
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)

    def update_progress(self, current: int, total: int, filename: str, bytes_copied: float) -> None:
        """Update progress display."""
        self.pb_overall.setValue(current)
        self.lbl_overall.setText(f"Copying {min(current + 1, total)} of {total} files")
        self.lbl_filename.setText(filename)
        
        # Update stats
        self.lbl_bytes.setText(self._format_bytes(bytes_copied))
        
        elapsed = time.time() - self.start_time
        self.lbl_elapsed.setText(self._format_duration(elapsed))
        
        if current > 0:
            avg_time_per_file = elapsed / current
            remaining_files = total - current
            eta = avg_time_per_file * remaining_files
            self.lbl_eta.setText(self._format_duration(eta))

    def _on_cancel_clicked(self) -> None:
        """Handle cancel button click."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Cancel Backup",
            "Are you sure you want to cancel creating rollback backups? This will abort the entire batch operation.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("Canceling...")
            self.abort_requested.emit()

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        if seconds < 0:
            return "-"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _format_bytes(self, bytes_val: float) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PB"

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Prevent dialog from being closed via Alt+F4 or other means."""
        event.ignore()

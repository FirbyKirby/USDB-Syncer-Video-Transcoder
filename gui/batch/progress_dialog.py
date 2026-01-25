"""Modal dialog showing batch transcode progress."""

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
)

from usdb_syncer.gui import icons

_logger = logging.getLogger(__name__)


class BatchProgressDialog(QDialog):
    """Modal dialog showing batch transcode progress."""

    # Signals
    abort_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QMainWindow, total_videos: int):
        super().__init__(parent)
        self.total_videos = total_videos
        self.current_video_idx = 0
        self.start_time = time.time()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build UI with progress bars and statistics."""
        self.setWindowTitle("Batch Media Transcode In Progress")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.setMinimumWidth(500)
        self.setModal(True)
        
        # Remove close button from title bar
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        # 1. Overall Progress
        self.lbl_overall = QLabel(f"Overall Progress: Transcoding 0 of {self.total_videos} media files")
        layout.addWidget(self.lbl_overall)
        
        self.pb_overall = QProgressBar()
        self.pb_overall.setRange(0, self.total_videos)
        self.pb_overall.setValue(0)
        layout.addWidget(self.pb_overall)

        layout.addSpacing(20)

        # 2. Current Media Info
        self.lbl_current_title = QLabel("Currently transcoding:")
        self.lbl_current_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.lbl_current_title)
        
        self.lbl_video_info = QLabel("-")
        self.lbl_video_info.setWordWrap(True)
        layout.addWidget(self.lbl_video_info)

        layout.addSpacing(10)

        # 3. Media Progress
        layout.addWidget(QLabel("Progress:"))
        self.pb_video = QProgressBar()
        self.pb_video.setRange(0, 100)
        self.pb_video.setValue(0)
        layout.addWidget(self.pb_video)

        layout.addSpacing(20)

        # 4. Statistics Panel
        stats_frame = QFrame()
        stats_frame.setFrameShape(QFrame.Shape.StyledPanel)
        stats_layout = QtWidgets.QGridLayout(stats_frame)
        
        stats_layout.addWidget(QLabel("FPS:"), 0, 0)
        self.lbl_fps = QLabel("-")
        stats_layout.addWidget(self.lbl_fps, 0, 1)
        
        stats_layout.addWidget(QLabel("Speed:"), 0, 2)
        self.lbl_speed = QLabel("-")
        stats_layout.addWidget(self.lbl_speed, 0, 3)
        
        stats_layout.addWidget(QLabel("Elapsed:"), 1, 0)
        self.lbl_elapsed = QLabel("00:00:00")
        stats_layout.addWidget(self.lbl_elapsed, 1, 1)
        
        stats_layout.addWidget(QLabel("ETA (current):"), 1, 2)
        self.lbl_eta = QLabel("-")
        stats_layout.addWidget(self.lbl_eta, 1, 3)
        
        stats_layout.addWidget(QLabel("Overall Elapsed:"), 2, 0)
        self.lbl_overall_elapsed = QLabel("00:00:00")
        stats_layout.addWidget(self.lbl_overall_elapsed, 2, 1)
        
        stats_layout.addWidget(QLabel("Overall ETA:"), 2, 2)
        self.lbl_overall_eta = QLabel("-")
        stats_layout.addWidget(self.lbl_overall_eta, 2, 3)
        
        layout.addWidget(stats_frame)

        layout.addSpacing(30)

        # 5. Abort Button
        self.btn_abort = QPushButton("🛑 Abort Batch Transcode")
        self.btn_abort.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f;
                color: white;
                font-weight: bold;
                padding: 10px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #b71c1c;
            }
        """)
        self.btn_abort.clicked.connect(self._on_abort_clicked)
        layout.addWidget(self.btn_abort)

    def update_overall_progress(self, completed: int) -> None:
        """Update overall progress (X of Y media files)."""
        self.current_video_idx = completed
        if completed < self.total_videos:
            self.lbl_overall.setText(f"Overall Progress: Transcoding {completed + 1} of {self.total_videos} media files")
        else:
            self.lbl_overall.setText(f"Overall Progress: Completed {self.total_videos} of {self.total_videos} media files")
        self.pb_overall.setValue(completed)
        
        # Update overall elapsed time
        elapsed = time.time() - self.start_time
        self.lbl_overall_elapsed.setText(self._format_duration(elapsed))
        
        # Update overall ETA
        if completed > 0:
            avg_time_per_video = elapsed / completed
            remaining_videos = self.total_videos - completed
            overall_eta = avg_time_per_video * remaining_videos
            self.lbl_overall_eta.setText(self._format_duration(overall_eta))

    def update_current_video(self, title: str, artist: str) -> None:
        """Update which video is currently being transcoded."""
        self.lbl_video_info.setText(f"<b>{artist} - {title}</b>")
        self.pb_video.setValue(0)
        self.lbl_fps.setText("-")
        self.lbl_speed.setText("-")
        self.lbl_eta.setText("-")

    def update_video_progress(
        self,
        percent: float,
        fps: float,
        speed: str,
        elapsed: float,
        eta: float
    ) -> None:
        """Update current video progress and stats."""
        self.pb_video.setValue(int(percent))
        self.lbl_fps.setText(f"{fps:.1f}")
        self.lbl_speed.setText(speed)
        self.lbl_elapsed.setText(self._format_duration(elapsed))
        self.lbl_eta.setText(self._format_duration(eta))
        
        # Also update overall elapsed
        overall_elapsed = time.time() - self.start_time
        self.lbl_overall_elapsed.setText(self._format_duration(overall_elapsed))

    def _on_abort_clicked(self) -> None:
        """Handle abort button click."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Abort Batch",
            "Are you sure you want to abort the entire batch operation?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.btn_abort.setEnabled(False)
            self.btn_abort.setText("Aborting...")
            self.abort_requested.emit()

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        if seconds < 0:
            return "-"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Prevent dialog from being closed via Alt+F4 or other means."""
        # Only allow closing if we are not in progress (though we remove the close button)
        event.ignore()

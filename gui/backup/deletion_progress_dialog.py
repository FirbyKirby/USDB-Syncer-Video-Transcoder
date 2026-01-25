from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtWidgets
from PySide6.QtCore import Qt, Signal

from usdb_syncer.gui import icons

from .backup_manager import BackupInfo

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class BackupDeletionProgressDialog(QtWidgets.QDialog):
    """Dialog showing progress of backup deletion."""
    canceled = Signal()

    def __init__(self, total: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.total = total
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Deleting Media Backups")
        self.setWindowIcon(icons.Icon.DELETE.icon())
        self.setFixedSize(400, 250)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        
        layout = QtWidgets.QVBoxLayout(self)

        self.lbl_status = QtWidgets.QLabel(f"Deleting backup 0 of {self.total}...")
        layout.addWidget(self.lbl_status)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, self.total)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.details_group = QtWidgets.QGroupBox("Currently deleting")
        details_layout = QtWidgets.QVBoxLayout(self.details_group)
        self.lbl_current_song = QtWidgets.QLabel("...")
        self.lbl_current_file = QtWidgets.QLabel("...")
        details_layout.addWidget(self.lbl_current_song)
        details_layout.addWidget(self.lbl_current_file)
        layout.addWidget(self.details_group)

        self.lbl_stats = QtWidgets.QLabel("Deleted: 0 | Failed: 0 | Space Freed: 0.0 MB")
        layout.addWidget(self.lbl_stats)

        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self.btn_cancel, 0, Qt.AlignmentFlag.AlignCenter)

        self._deleted = 0
        self._failed = 0
        self._freed_mb = 0.0

        self._is_finished = False

    def update_progress(self, current: int, total: int, backup: BackupInfo) -> None:
        """Update the progress display."""
        media_label = getattr(backup, "media_type", "media")
        self.lbl_status.setText(f"Deleting {media_label} backup {current + 1} of {total}...")
        self.progress_bar.setValue(current + 1)
        self.lbl_current_song.setText(f"{backup.artist} - {backup.song_title}")
        self.lbl_current_file.setText(f"{backup.backup_path.name} ({backup.size_mb:.1f} MB)")
        
        if backup.deletion_status == "deleted":
            self._deleted += 1
            self._freed_mb += backup.size_mb
        elif backup.deletion_status == "failed":
            self._failed += 1
            
        self.lbl_stats.setText(
            f"Deleted: {self._deleted} | Failed: {self._failed} | Space Freed: {self._freed_mb:.1f} MB"
        )

    def mark_finished(self) -> None:
        """Mark the operation as finished to prevent cancellation on close."""
        self._is_finished = True

    def _on_cancel(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelling...")
        self.canceled.emit()

    def closeEvent(self, event) -> None:
        if not self._is_finished:
            self._on_cancel()
        super().closeEvent(event)

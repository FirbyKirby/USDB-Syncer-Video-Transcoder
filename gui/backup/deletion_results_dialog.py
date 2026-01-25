from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtWidgets
from PySide6.QtCore import Qt

from usdb_syncer.gui import icons

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from .backup_manager import BackupDeletionResult


class BackupDeletionResultsDialog(QtWidgets.QDialog):
    """Dialog showing results of backup deletion."""

    def __init__(self, result: BackupDeletionResult, parent: QWidget | None = None):
        super().__init__(parent)
        self.result = result
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Backup Deletion Complete")
        self.setWindowIcon(icons.Icon.DELETE.icon())
        self.setMinimumWidth(400)
        layout = QtWidgets.QVBoxLayout(self)

        summary_group = QtWidgets.QGroupBox("Summary")
        summary_layout = QtWidgets.QVBoxLayout(summary_group)
        
        success_lbl = QtWidgets.QLabel(f"✓ Successfully deleted: {self.result.backups_deleted} backups")
        summary_layout.addWidget(success_lbl)
        
        failed_lbl = QtWidgets.QLabel(f"✗ Failed: {self.result.backups_failed}")
        if self.result.backups_failed > 0:
            failed_lbl.setStyleSheet("color: red; font-weight: bold;")
        summary_layout.addWidget(failed_lbl)
        
        space_lbl = QtWidgets.QLabel(f"Total Space Freed: {self.result.total_space_freed_mb:.1f} MB")
        summary_layout.addWidget(space_lbl)
        
        layout.addWidget(summary_group)

        if self.result.errors:
            error_group = QtWidgets.QGroupBox("Errors")
            error_layout = QtWidgets.QVBoxLayout(error_group)
            error_list = QtWidgets.QListWidget()
            for song_id, error in self.result.errors:
                error_list.addItem(f"Song {song_id}: {error}")
            error_layout.addWidget(error_list)
            layout.addWidget(error_group)

        btn_ok = QtWidgets.QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        layout.addWidget(btn_ok, 0, Qt.AlignmentFlag.AlignCenter)

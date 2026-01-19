from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Callable

from PySide6 import QtWidgets
from PySide6.QtCore import Qt, QThread, Signal

from usdb_syncer import db
from usdb_syncer.gui import icons
from usdb_syncer.utils import AppPaths
from .backup_manager import (
    BackupInfo,
    discover_backups,
    delete_backups_batch,
    restore_backups_batch,
)
from .config import load_config

import logging
_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow


class ScanWorker(QThread):
    """Worker thread for discovering backups."""
    finished = Signal(list)

    def __init__(self, cfg, cancel_check: Optional[Callable[[], bool]] = None):
        super().__init__()
        self.cfg = cfg
        self.cancel_check = cancel_check

    def run(self):
        db.connect(AppPaths.db)
        backups = discover_backups(self.cfg, cancel_check=self.cancel_check)
        self.finished.emit(backups)


class DeletionWorker(QThread):
    """Worker thread for deleting backups."""
    progress = Signal(int, int, BackupInfo)
    finished = Signal(object)

    def __init__(self, backups):
        super().__init__()
        self.backups = backups
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        db.connect(AppPaths.db)
        result = delete_backups_batch(
            self.backups,
            progress_callback=self.progress.emit,
            cancel_check=lambda: self._is_cancelled
        )
        self.finished.emit(result)


class RestoreWorker(QThread):
    """Worker thread for restoring backups."""
    progress = Signal(int, int, BackupInfo)
    finished = Signal(object)

    def __init__(self, backups: list[BackupInfo]):
        super().__init__()
        self.backups = backups
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        db.connect(AppPaths.db)
        result = restore_backups_batch(
            self.backups,
            progress_callback=self.progress.emit,
            cancel_check=lambda: self._is_cancelled
        )
        self.finished.emit(result)


class BackupDialogOrchestrator:
    """Orchestrates the backup management workflow."""

    def __init__(self, parent: QMainWindow):
        self.parent = parent
        self.cfg = load_config()
        self.backups: list[BackupInfo] = []
        self._scan_dialog: Optional[QtWidgets.QProgressDialog] = None
        self._worker: Optional[QThread] = None
        self._cancel_scan = False

    def start_workflow(self) -> None:
        """Entry point for backup management."""
        self._start_scan()

    def _start_scan(self) -> None:
        """Phase 1: Discover backups with a progress dialog."""
        self._scan_dialog = QtWidgets.QProgressDialog(
            "Scanning song library for backups...",
            "Cancel",
            0, 0,
            self.parent
        )
        self._scan_dialog.setWindowTitle("Scanning...")
        self._scan_dialog.setWindowIcon(icons.Icon.CHANGES.icon())
        self._scan_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        
        self._cancel_scan = False
        self._worker = ScanWorker(self.cfg, cancel_check=lambda: self._cancel_scan)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.start()
        
        self._scan_dialog.exec()
        if self._scan_dialog.wasCanceled():
            self._cancel_scan = True
            self._worker.quit()
            self._worker.wait()

    def _on_scan_finished(self, backups: list[BackupInfo]) -> None:
        """Callback when scan completes."""
        if self._scan_dialog:
            self._scan_dialog.close()
        
        if self._cancel_scan:
            return

        self.backups = backups
        
        if not self.backups:
            QtWidgets.QMessageBox.information(
                self.parent,
                "No Backups Found",
                "No persistent media backups (video/audio) were found in your song library."
            )
            return

        self._show_selection_dialog()

    def _show_selection_dialog(self) -> None:
        """Phase 2: Show selection dialog."""
        from .backup_selection_dialog import BackupSelectionDialog
        dialog = BackupSelectionDialog(self.backups, self.parent)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            selected = [b for b in self.backups if b.selected]
            if selected:
                if dialog.chosen_action == "restore":
                    self._confirm_restore(selected)
                elif dialog.chosen_action == "delete":
                    self._confirm_deletion(selected)
                else:
                    _logger.error(f"Unexpected backup action: {dialog.chosen_action}")
                    QtWidgets.QMessageBox.critical(
                        self.parent,
                        "Error",
                        f"An unexpected action was requested: {dialog.chosen_action}"
                    )

    def _confirm_deletion(self, selected: list[BackupInfo]) -> None:
        """Phase 3: Confirm deletion."""
        total_size = sum(b.size_mb for b in selected)
        
        message = (
            f"You are about to PERMANENTLY DELETE {len(selected)} backup files.\n\n"
            f"This will free {total_size:.1f} MB of disk space.\n\n"
            "⚠ THIS ACTION CANNOT BE UNDONE! ⚠\n\n"
            "Deleted backups cannot be recovered. Your transcoded media files will remain intact, "
            "but you will lose the ability to restore from these backups.\n\n"
            "Are you absolutely sure you want to continue?"
        )
        
        reply = QtWidgets.QMessageBox.warning(
            self.parent,
            "Confirm Backup Deletion",
            message,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._execute_deletion(selected)

    def _execute_deletion(self, selected: list[BackupInfo]) -> None:
        """Phase 4: Execute deletion with progress dialog."""
        from .backup_deletion_progress_dialog import BackupDeletionProgressDialog
        
        progress_dialog = BackupDeletionProgressDialog(len(selected), self.parent)
        
        worker = DeletionWorker(selected)
        worker.progress.connect(progress_dialog.update_progress)
        worker.finished.connect(lambda result: self._on_deletion_finished(result, progress_dialog))
        
        progress_dialog.canceled.connect(worker.cancel)
        
        worker.start()
        progress_dialog.exec()

    def _on_deletion_finished(self, result, progress_dialog) -> None:
        """Phase 5: Show results."""
        progress_dialog.mark_finished()
        progress_dialog.close()
        from .backup_deletion_results_dialog import BackupDeletionResultsDialog
        results_dialog = BackupDeletionResultsDialog(result, self.parent)
        results_dialog.exec()

    def _confirm_restore(self, selected: list[BackupInfo]) -> None:
        """Phase 3: Confirm restoration."""
        message = (
            f"You are about to RESTORE {len(selected)} backup files.\n\n"
            "⚠ THIS WILL OVERWRITE YOUR ACTIVE TRANSCODED MEDIA FILES! ⚠\n\n"
            "The current active media files will be replaced by their original backup versions. "
            "A safety backup of the current media files will be created before replacement.\n\n"
            "Are you sure you want to continue?"
        )
        
        reply = QtWidgets.QMessageBox.warning(
            self.parent,
            "Confirm Backup Restoration",
            message,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._execute_restore(selected)

    def _execute_restore(self, selected: list[BackupInfo]) -> None:
        """Phase 4: Execute restoration with progress dialog."""
        from .backup_restore_progress_dialog import BackupRestoreProgressDialog
        
        progress_dialog = BackupRestoreProgressDialog(len(selected), self.parent)
        
        worker = RestoreWorker(selected)
        worker.progress.connect(progress_dialog.update_progress)
        worker.finished.connect(lambda result: self._on_restore_finished(result, progress_dialog))
        
        progress_dialog.canceled.connect(worker.cancel)
        
        worker.start()
        progress_dialog.exec()

    def _on_restore_finished(self, result, progress_dialog) -> None:
        """Phase 5: Show results."""
        progress_dialog.mark_finished()
        progress_dialog.close()
        from .backup_restore_results_dialog import BackupRestoreResultsDialog
        results_dialog = BackupRestoreResultsDialog(result, self.parent)
        results_dialog.exec()

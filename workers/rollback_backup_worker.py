"""Worker thread for creating rollback backups in the background."""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from PySide6 import QtCore

if TYPE_CHECKING:
    from .batch_orchestrator import BatchTranscodeCandidate
    from .rollback import RollbackManager

_logger = logging.getLogger(__name__)


class RollbackBackupWorker(QtCore.QThread):
    """Worker thread for background file copying of rollback backups."""

    # Signals
    progress = QtCore.Signal(int, int, str, float)  # current, total, filename, bytes_copied
    finished = QtCore.Signal()
    error = QtCore.Signal(str, str)  # filename, error_message
    aborted = QtCore.Signal()

    def __init__(
        self,
        candidates: list[BatchTranscodeCandidate],
        rollback_manager: RollbackManager
    ):
        super().__init__()
        self.candidates = candidates
        self.rollback_manager = rollback_manager
        self._abort_requested = False

    def abort(self) -> None:
        """Request abort of the backup operation."""
        self._abort_requested = True

    def run(self) -> None:
        """Execute background backup creation."""
        total = len(self.candidates)
        bytes_copied_total = 0.0

        for i, candidate in enumerate(self.candidates):
            if self._abort_requested:
                _logger.info("Rollback backup creation aborted by user")
                self.aborted.emit()
                return

            filename = candidate.video_path.name
            self.progress.emit(i, total, filename, bytes_copied_total)

            rollback_backup_path = self.rollback_manager.get_rollback_backup_path(
                candidate.song_id,
                candidate.video_path,
                media_type=getattr(candidate, "media_type", "video"),
            )

            try:
                # Ensure parent directory exists (RollbackManager should handle this, but being safe)
                rollback_backup_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Perform the copy in chunks to allow for responsive abort
                source_size = candidate.video_path.stat().st_size
                with open(candidate.video_path, "rb") as fsrc:
                    with open(rollback_backup_path, "wb") as fdst:
                        chunk_size = 1024 * 1024  # 1MB chunks
                        bytes_copied_file = 0
                        while True:
                            if self._abort_requested:
                                _logger.info("Rollback backup creation aborted by user during file copy")
                                # Clean up partial file
                                fdst.close()
                                rollback_backup_path.unlink(missing_ok=True)
                                self.aborted.emit()
                                return
                            
                            chunk = fsrc.read(chunk_size)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            bytes_copied_file += len(chunk)
                            self.progress.emit(i, total, filename, bytes_copied_total + bytes_copied_file)

                # Update total bytes copied
                # We'll use bytes for precision and format in the dialog
                bytes_copied_total += candidate.video_path.stat().st_size
                
                _logger.debug(f"Created rollback backup: {rollback_backup_path}")
            except Exception as e:
                _logger.error(f"Failed to create rollback backup for {candidate.song_title}: {e}")
                self.error.emit(filename, str(e))
                # We don't return here, the orchestrator will decide whether to continue or abort
                # based on the error signal and user input.
                # However, the design says "Handles exceptions gracefully and emits error signal".
                # If we emit error, we should probably wait for orchestrator response or just continue.
                # The orchestrator will likely show a message box.
                # To keep it simple and non-blocking, we'll stop this worker on error and let orchestrator handle it.
                return

        self.progress.emit(total, total, "Complete", bytes_copied_total)
        self.finished.emit()

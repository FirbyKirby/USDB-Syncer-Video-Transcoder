"""Progress dialog for library scan phase in batch wizard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from PySide6 import QtCore, QtWidgets
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)
from usdb_syncer.gui import icons

from ...batch.wizard.scan_worker import ScanWorker

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from ...batch.wizard.state import BatchWizardState, ScanResult
    from ...core.config import TranscoderConfig

_logger = logging.getLogger(__name__)


class ScanProgressDialog(QDialog):
    """Modal dialog showing scan progress."""

    def __init__(
        self, cfg: TranscoderConfig, state: BatchWizardState, parent: Optional[QMainWindow] = None
    ):
        """Initialize scan progress dialog.
        
        Args:
            cfg: Transcoder configuration
            state: Wizard state with scan parameters
            parent: Parent window
        """
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.scan_results: List[ScanResult] = []
        self._scan_aborted = False
        self._worker: Optional[ScanWorker] = None
        self._setup_ui()
        self._start_scan()

    def _setup_ui(self) -> None:
        """Build UI."""
        self.setWindowTitle("Batch Wizard - Scanning Library")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.setMinimumWidth(500)
        self.setModal(True)

        # Remove close button
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        # Header
        header = QLabel("<h2>Scanning Library</h2>")
        layout.addWidget(header)

        # Description
        desc = QLabel("Analyzing library for media files that need processing...")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(20)

        # Progress info
        self.lbl_progress = QLabel("Initializing...")
        layout.addWidget(self.lbl_progress)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        layout.addSpacing(10)

        # Current file
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setWordWrap(True)
        layout.addWidget(self.lbl_current_file)

        layout.addStretch()

        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _start_scan(self) -> None:
        """Start the scan worker."""
        self._worker = ScanWorker(self.cfg, self.state)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.aborted.connect(self._on_aborted)
        self._worker.start()

    def _on_progress(self, current: int, total: int, filename: str) -> None:
        """Handle progress update from worker."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.lbl_progress.setText(f"Scanning {current + 1} of {total} songs")
        self.lbl_current_file.setText(f"<i>{filename}</i>")

    def _on_finished(self, results: List[ScanResult]) -> None:
        """Handle scan completion."""
        self.scan_results = results
        _logger.info(f"Scan completed: {len(results)} files found")
        self.accept()

    def _on_error(self, error_msg: str) -> None:
        """Handle scan error."""
        _logger.error(f"Scan error: {error_msg}")
        QtWidgets.QMessageBox.critical(
            self,
            "Scan Error",
            f"An error occurred during library scan:\n\n{error_msg}\n\nThe wizard will be cancelled.",
        )
        self.reject()

    def _on_aborted(self) -> None:
        """Handle scan abort."""
        self._scan_aborted = True
        self.reject()

    def _on_cancel(self) -> None:
        """Handle cancel button click."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Cancel Scan",
            "Are you sure you want to cancel the scan?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self._worker:
                self._worker.abort()

    def get_results(self) -> List[ScanResult]:
        """Get scan results.
        
        Returns:
            List of ScanResult objects from the scan
        """
        return self.scan_results

    def was_aborted(self) -> bool:
        """Check if scan was aborted.
        
        Returns:
            True if scan was aborted by user
        """
        return self._scan_aborted

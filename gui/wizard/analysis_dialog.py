"""Progress dialog for loudness analysis phase in batch wizard.

This dialog shows analysis progress with running summaries of verification results.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6 import QtCore, QtWidgets
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)
from usdb_syncer.gui import icons

from ...batch.wizard.analysis_worker import AnalysisWorker
from ...core.loudness_cache import LoudnessCache, get_cache_path

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from ...batch.wizard.state import BatchWizardState, ScanResult
    from ...core.config import TranscoderConfig
    from ...core.loudness_verifier import VerificationResult

_logger = logging.getLogger(__name__)


class AnalysisProgressDialog(QDialog):
    """Modal dialog showing analysis progress with running summaries."""

    def __init__(
        self,
        cfg: TranscoderConfig,
        state: BatchWizardState,
        audio_files: List[ScanResult],
        parent: Optional[QMainWindow] = None,
    ):
        """Initialize analysis progress dialog.
        
        Args:
            cfg: Transcoder configuration
            state: Wizard state with verification settings
            audio_files: List of audio files to analyze
            parent: Parent window
        """
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.audio_files = audio_files
        self.analysis_results: Dict[str, VerificationResult] = {}
        self._analysis_aborted = False
        self._worker: Optional[AnalysisWorker] = None
        
        # Running counters
        self._within_tolerance = 0
        self._out_of_tolerance = 0
        self._errors = 0
        
        self._setup_ui()
        self._start_analysis()

    def _setup_ui(self) -> None:
        """Build UI."""
        self.setWindowTitle("Batch Wizard - Loudness Analysis")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.setMinimumWidth(600)
        self.setModal(True)

        # Remove close button
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        # Header
        header = QLabel("<h2>Loudness Analysis</h2>")
        layout.addWidget(header)

        # Description
        desc = QLabel(
            f"Analyzing {len(self.audio_files)} audio files for loudness verification. "
            "This may take a while..."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(20)

        # Progress info
        self.lbl_progress = QLabel("Initializing...")
        layout.addWidget(self.lbl_progress)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, len(self.audio_files))
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        layout.addSpacing(10)

        # Current file
        self.lbl_current_file = QLabel("")
        self.lbl_current_file.setWordWrap(True)
        layout.addWidget(self.lbl_current_file)

        layout.addSpacing(20)

        # Time stats frame
        time_frame = QFrame()
        time_frame.setFrameShape(QFrame.Shape.StyledPanel)
        time_layout = QtWidgets.QGridLayout(time_frame)

        time_layout.addWidget(QLabel("Elapsed:"), 0, 0)
        self.lbl_elapsed = QLabel("00:00:00")
        time_layout.addWidget(self.lbl_elapsed, 0, 1)

        time_layout.addWidget(QLabel("Estimated Remaining:"), 0, 2)
        self.lbl_eta = QLabel("-")
        time_layout.addWidget(self.lbl_eta, 0, 3)

        layout.addWidget(time_frame)

        layout.addSpacing(10)

        # Running summary frame
        summary_frame = QFrame()
        summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        summary_layout = QVBoxLayout(summary_frame)

        summary_layout.addWidget(QLabel("<b>Analysis Summary:</b>"))

        self.lbl_summary = QLabel("Processed 0 files")
        summary_layout.addWidget(self.lbl_summary)

        layout.addWidget(summary_frame)

        layout.addStretch()

        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel Analysis")
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f;
                color: white;
                font-weight: bold;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #b71c1c;
            }
        """)
        self.btn_cancel.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _start_analysis(self) -> None:
        """Start the analysis worker."""
        self._worker = AnalysisWorker(self.cfg, self.state, self.audio_files)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_completed.connect(self._on_file_completed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.aborted.connect(self._on_aborted)
        self._worker.start()

    def _on_progress(
        self, current: int, total: int, filename: str, elapsed: float, eta: float
    ) -> None:
        """Handle progress update from worker."""
        self.progress_bar.setValue(current)
        self.lbl_progress.setText(f"Analyzing file {current + 1} of {total}")
        self.lbl_current_file.setText(f"<i>{filename}</i>")
        self.lbl_elapsed.setText(self._format_duration(elapsed))
        self.lbl_eta.setText(self._format_duration(eta) if eta > 0 else "Calculating...")

    def _on_file_completed(self, filename: str, result: VerificationResult) -> None:
        """Handle file completion."""
        # Update running counters
        if result.within_tolerance:
            self._within_tolerance += 1
        elif result.reasons:  # Has reasons means out of tolerance
            self._out_of_tolerance += 1
        else:  # No reasons, likely error
            self._errors += 1

        # Update summary
        total_processed = self._within_tolerance + self._out_of_tolerance + self._errors
        summary_parts = [f"Processed {total_processed} of {len(self.audio_files)} files:"]
        
        if self._within_tolerance > 0:
            summary_parts.append(f"✓ {self._within_tolerance} within tolerance")
        
        if self._out_of_tolerance > 0:
            summary_parts.append(f"⚠ {self._out_of_tolerance} need normalization")
        
        if self._errors > 0:
            summary_parts.append(f"✗ {self._errors} errors")

        self.lbl_summary.setText("\n".join(summary_parts))

    def _on_finished(self, results: Dict[str, VerificationResult]) -> None:
        """Handle analysis completion."""
        self.analysis_results = results
        _logger.info(
            f"Analysis completed: {len(results)} files analyzed "
            f"({self._within_tolerance} OK, {self._out_of_tolerance} needs work, {self._errors} errors)"
        )
        self.accept()

    def _on_error(self, filename: str, error_msg: str) -> None:
        """Handle analysis error for a file."""
        _logger.warning(f"Analysis error for {filename}: {error_msg}")
        self._errors += 1
        # Continue analysis, don't stop on individual file errors

    def _on_aborted(self) -> None:
        """Handle analysis abort."""
        self._analysis_aborted = True
        _logger.info("Analysis aborted by user")
        self.reject()

    def _on_cancel(self) -> None:
        """Handle cancel button click."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Cancel Analysis",
            "Are you sure you want to cancel the analysis?\n\n"
            "Analysis progress up to this point has been saved to the cache.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self._worker:
                self._worker.abort()

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        if seconds < 0:
            return "-"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def get_results(self) -> Dict[str, VerificationResult]:
        """Get analysis results.
        
        Returns:
            Dictionary mapping file paths to VerificationResult
        """
        return self.analysis_results

    def was_aborted(self) -> bool:
        """Check if analysis was aborted.
        
        Returns:
            True if analysis was aborted by user
        """
        return self._analysis_aborted

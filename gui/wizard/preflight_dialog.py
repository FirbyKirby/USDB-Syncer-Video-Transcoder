"""Preflight dialog for batch wizard - Review estimates and confirm.

This is a stub implementation for Phase 3. Full UI will be implemented in Phase 4.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QGroupBox,
)
from usdb_syncer.gui import icons

from ...core.loudness_cache import LoudnessCache, get_cache_path

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow
    from ...batch.wizard.state import BatchWizardState

_logger = logging.getLogger(__name__)


def _format_time_estimate(seconds: float) -> str:
    """Format time estimate in user-friendly units."""
    if seconds < 60:
        return f"{int(seconds)} seconds"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        hours = seconds / 3600
        return f"{hours:.1f} hours"


class BatchWizardPreflightDialog(QDialog):
    """Dialog for reviewing estimates and confirming batch operation.
    
    Step 3 of the wizard: Shows library size, estimated processing time,
    disk space requirements, and allows user to review settings before proceeding.
    """
    
    def __init__(self, state: BatchWizardState, parent: Optional[QMainWindow] = None):
        """Initialize the Preflight dialog.
        
        Args:
            state: Current wizard state
            parent: Parent window
        """
        super().__init__(parent)
        self.state = state
        self.went_back = False
        self._setup_ui()
        
    def _setup_ui(self) -> None:
        """Build the UI (stub for Phase 3)."""
        self.setWindowTitle("Batch Wizard - Step 3: Preflight Check")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(600, 450)
        
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("<h2>Preflight Check</h2>")
        layout.addWidget(header)
        
        # Description
        desc = QLabel(
            "Review the batch operation settings and estimated resource requirements "
            "before proceeding."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        layout.addSpacing(20)
        
        # Settings summary
        settings_group = QGroupBox("Settings Summary")
        settings_layout = QVBoxLayout(settings_group)
        
        summary_parts = []
        if self.state.process_audio:
            summary_parts.append("• Process audio files")
            if self.state.force_audio_transcode:
                summary_parts.append("  - Force transcode all audio")
            if self.state.verify_normalization:
                summary_parts.append(f"  - Verify normalization ({self.state.verification_tolerance_preset} preset)")
        
        if self.state.process_video:
            summary_parts.append("• Process video files")
            if self.state.force_video_transcode:
                summary_parts.append("  - Force transcode all video")
        
        summary_label = QLabel("\n".join(summary_parts))
        settings_layout.addWidget(summary_label)
        layout.addWidget(settings_group)
        
        # Estimates
        estimates_group = QGroupBox("Estimated Requirements")
        estimates_layout = QVBoxLayout(estimates_group)
        
        estimate_parts = []
        
        # Get library size estimate from syncer if possible
        try:
            from usdb_syncer import settings
            from usdb_syncer.sync_meta import SyncMeta
            from usdb_syncer.utils import AppPaths
            from usdb_syncer import db
            
            db.connect(AppPaths.db)
            song_dir = settings.get_song_dir()
            metas = list(SyncMeta.get_in_folder(song_dir))
            total_songs = len(metas)

            estimate_parts.append(f"<b>Library size:</b> ~{total_songs} songs")

            # Scan estimate (~1-2 sec per song)
            scan_time_sec = total_songs * 1.5  # Conservative estimate
            scan_time_min = int(scan_time_sec // 60)
            estimate_parts.append(f"<b>Fast scan:</b> ~{scan_time_min if scan_time_min > 0 else 1} minute(s)")

            # Get cache for analysis estimates
            cache = LoudnessCache(get_cache_path())

            # Analysis estimate (if enabled)
            if self.state.verify_normalization and self.state.process_audio:
                # Assume average song is 3 minutes
                avg_duration_min = 3.0
                audio_count = total_songs if self.state.process_audio else 0
                total_duration_seconds = audio_count * avg_duration_min * 60

                # Get learned performance estimate
                speed_x, count, confidence = cache.get_analysis_speed_estimate()
                _logger.debug(f"Analysis speed estimate: speed_x={speed_x}, count={count}, confidence={confidence}")

                if confidence == 'learned' and speed_x is not None:
                    eta_seconds = total_duration_seconds / speed_x
                    time_str = _format_time_estimate(eta_seconds)
                    estimate_parts.append(
                        f"<b>Loudness analysis:</b> ~{time_str}<br/>"
                        f"<i>Estimated analysis time: {time_str} (based on your machine's previous analysis speed, {count} analyses)</i>"
                    )
                elif confidence == 'insufficient_data':
                    eta_seconds = total_duration_seconds / 1.0
                    time_str = _format_time_estimate(eta_seconds)
                    estimate_parts.append(
                        f"<b>Loudness analysis:</b> ~{time_str}<br/>"
                        f"<i>Estimated analysis time: {time_str} (default estimate; will improve after some processing, {count} analyses)</i>"
                    )
                else:
                    # Fallback to default
                    eta_seconds = total_duration_seconds / 1.0
                    time_str = _format_time_estimate(eta_seconds)
                    estimate_parts.append(
                        f"<b>Loudness analysis:</b> ~{time_str}<br/>"
                        "<i>Note: First-time analysis is slow; cached results will be used in future runs.</i>"
                    )
        except Exception:
            # Fallback if we can't query the database
            estimate_parts.append("<i>Library size unknown - estimates will be shown after scan.</i>")
        
        estimate_parts.append(
            "<br/><b>Note:</b> These are conservative estimates. "
            "Actual time may vary based on your system performance."
        )
        
        estimates_label = QLabel("<br/>".join(estimate_parts))
        estimates_label.setWordWrap(True)
        estimates_layout.addWidget(estimates_label)
        layout.addWidget(estimates_group)
        
        # What happens next
        next_group = QGroupBox("What Happens Next")
        next_layout = QVBoxLayout(next_group)
        
        next_steps = [
            "1. Fast scan of your library (metadata only)",
        ]
        
        if self.state.verify_normalization:
            next_steps.append("2. Optional loudness analysis (can be slow)")
            next_steps.append("3. Review and select files to process")
        else:
            next_steps.append("2. Review and select files to process")
        
        next_label = QLabel("\n".join(next_steps))
        next_layout.addWidget(next_label)
        layout.addWidget(next_group)
        
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._on_back)
        button_layout.addWidget(self.btn_back)
        
        button_layout.addStretch()
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)
        
        self.btn_next = QPushButton("Start Scan")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        button_layout.addWidget(self.btn_next)
        
        layout.addLayout(button_layout)
        
    def _on_back(self) -> None:
        """Handle Back button click."""
        self.went_back = True
        self.reject()
        
    def _on_next(self) -> None:
        """Handle Next button click."""
        _logger.debug("Preflight confirmed, proceeding to scan phase")
        self.accept()
        
    def get_state(self) -> BatchWizardState:
        """Return the updated wizard state.
        
        Returns:
            Updated BatchWizardState
        """
        return self.state

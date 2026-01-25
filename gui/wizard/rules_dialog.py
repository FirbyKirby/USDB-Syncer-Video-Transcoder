"""Rules dialog for batch wizard - Configure transcode and verification rules.

This is a stub implementation for Phase 3. Full UI will be implemented in Phase 4.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QGroupBox,
)
from usdb_syncer.gui import icons

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow
    from ...batch.wizard.state import BatchWizardState

_logger = logging.getLogger(__name__)


class BatchWizardRulesDialog(QDialog):
    """Dialog for configuring batch processing rules.
    
    Step 2 of the wizard: User configures force transcode options and
    verification settings (whether to analyze, tolerance preset).
    """
    
    def __init__(self, state: BatchWizardState, parent: Optional[QMainWindow] = None):
        """Initialize the Rules dialog.
        
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
        self.setWindowTitle("Batch Wizard - Step 2: Configure Rules")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(550, 400)
        
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("<h2>Configure Processing Rules</h2>")
        layout.addWidget(header)
        
        # Description
        desc = QLabel(
            "Set the rules for this batch operation, including whether to force "
            "transcoding and whether to verify loudness normalization."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        layout.addSpacing(20)
        
        # Force transcode options
        force_group = QGroupBox("Force Transcode")
        force_layout = QVBoxLayout(force_group)
        
        if self.state.process_audio:
            self.cb_force_audio = QCheckBox("Force audio transcode even if format matches")
            self.cb_force_audio.setChecked(self.state.force_audio_transcode)
            self.cb_force_audio.setToolTip(
                "Re-transcode all audio files regardless of current codec/container"
            )
            force_layout.addWidget(self.cb_force_audio)
        
        if self.state.process_video:
            self.cb_force_video = QCheckBox("Force video transcode even if format matches")
            self.cb_force_video.setChecked(self.state.force_video_transcode)
            self.cb_force_video.setToolTip(
                "Re-transcode all video files regardless of current codec/container"
            )
            force_layout.addWidget(self.cb_force_video)
        
        layout.addWidget(force_group)
        
        # Verification options (only shown if processing audio)
        if self.state.process_audio:
            verify_group = QGroupBox("Loudness Verification")
            verify_layout = QVBoxLayout(verify_group)
            
            self.cb_verify = QCheckBox("Run optional loudness analysis phase")
            self.cb_verify.setChecked(self.state.verify_normalization)
            self.cb_verify.setToolTip(
                "<b>Optional loudness analysis</b><br/>"
                "Analyze audio files to verify they meet normalization targets.<br/>"
                "This is a separate phase and will take additional time."
            )
            verify_layout.addWidget(self.cb_verify)
            
            # Tolerance preset
            preset_layout = QHBoxLayout()
            preset_layout.addWidget(QLabel("Tolerance preset:"))
            
            self.combo_preset = QComboBox()
            self.combo_preset.addItems(["strict", "balanced", "relaxed"])
            self.combo_preset.setCurrentText(self.state.verification_tolerance_preset)
            self.combo_preset.setToolTip(
                "<b>Tolerance preset</b><br/>"
                "<b>Strict:</b> Closest match to target loudness<br/>"
                "<b>Balanced:</b> Recommended; differences rarely noticeable<br/>"
                "<b>Relaxed:</b> Fastest and least picky"
            )
            preset_layout.addWidget(self.combo_preset)
            preset_layout.addStretch()
            
            verify_layout.addLayout(preset_layout)
            layout.addWidget(verify_group)
        
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
        
        self.btn_next = QPushButton("Next")
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
        # Update state
        if self.state.process_audio:
            self.state.force_audio_transcode = self.cb_force_audio.isChecked()
            self.state.verify_normalization = self.cb_verify.isChecked()
            self.state.verification_tolerance_preset = self.combo_preset.currentText()
        
        if self.state.process_video:
            self.state.force_video_transcode = self.cb_force_video.isChecked()
        
        _logger.debug(
            f"Rules configured: force_audio={self.state.force_audio_transcode}, "
            f"force_video={self.state.force_video_transcode}, "
            f"verify={self.state.verify_normalization}, "
            f"preset={self.state.verification_tolerance_preset}"
        )
        self.accept()
        
    def get_state(self) -> BatchWizardState:
        """Return the updated wizard state.
        
        Returns:
            Updated BatchWizardState
        """
        return self.state

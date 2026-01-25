"""Goals dialog for batch wizard - Choose what to process.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from usdb_syncer.gui import icons

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow
    from ...batch.wizard.state import BatchWizardState

_logger = logging.getLogger(__name__)


class BatchWizardGoalsDialog(QDialog):
    """Dialog for choosing batch processing goals.
    
    Step 1 of the wizard: User selects whether to process audio, video, or both.
    This is the entry point to the wizard, so there is no Back button.
    """
    
    def __init__(self, state: BatchWizardState, parent: Optional[QMainWindow] = None):
        """Initialize the Goals dialog.
        
        Args:
            state: Current wizard state
            parent: Parent window
        """
        super().__init__(parent)
        self.state = state
        self._setup_ui()
        
    def _setup_ui(self) -> None:
        """Build the UI."""
        self.setWindowTitle("Batch Wizard - Step 1: Choose Goals")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(500, 300)
        
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("<h2>What would you like to process?</h2>")
        layout.addWidget(header)
        
        # Description
        desc = QLabel(
            "Select which media types to include in this batch operation.\n"
            "You can process audio files, video files, or both."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        layout.addSpacing(20)
        
        # Checkboxes
        self.cb_process_audio = QCheckBox("Process Audio Files")
        self.cb_process_audio.setChecked(self.state.process_audio)
        self.cb_process_audio.setToolTip("Include audio files in batch processing")
        layout.addWidget(self.cb_process_audio)
        
        self.cb_process_video = QCheckBox("Process Video Files")
        self.cb_process_video.setChecked(self.state.process_video)
        self.cb_process_video.setToolTip("Include video files in batch processing")
        layout.addWidget(self.cb_process_video)
        
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # No Back button on first step
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)
        
        self.btn_next = QPushButton("Next")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        button_layout.addWidget(self.btn_next)
        
        layout.addLayout(button_layout)
        
    def _on_next(self) -> None:
        """Handle Next button click."""
        # Validate at least one option is selected
        if not self.cb_process_audio.isChecked() and not self.cb_process_video.isChecked():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "No Options Selected",
                "Please select at least one media type to process."
            )
            return
        
        # Update state
        self.state.process_audio = self.cb_process_audio.isChecked()
        self.state.process_video = self.cb_process_video.isChecked()
        
        _logger.debug(f"Goals selected: audio={self.state.process_audio}, video={self.state.process_video}")
        self.accept()
        
    def get_state(self) -> BatchWizardState:
        """Return the updated wizard state.
        
        Returns:
            Updated BatchWizardState
        """
        return self.state

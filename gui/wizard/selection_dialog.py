"""Selection dialog for batch wizard with tree view grouped by song.

This dialog presents scan results in a hierarchical tree view where each song
is a parent node and audio/video are child nodes. Users can select which 
songs/media to transcode.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from usdb_syncer.gui import icons

from ...batch.wizard.state import SongSelection

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from ...batch.wizard.state import BatchWizardState, ScanResult

_logger = logging.getLogger(__name__)


class FilterMode(Enum):
    """Filter modes for the selection dialog."""
    
    ALL = "All Files"
    NEEDS_PROCESSING = "Needs Processing"
    ALREADY_OK = "Already Compatible"
    ERRORS = "Errors Only"


class BatchWizardSelectionDialog(QDialog):
    """Selection dialog with tree view grouped by song."""

    def __init__(self, state: BatchWizardState, parent: Optional[QMainWindow] = None):
        """Initialize selection dialog.
        
        Args:
            state: Wizard state with scan results
            parent: Parent window
        """
        super().__init__(parent)
        self.state = state
        self.went_back = False
        self._filter_mode = FilterMode.NEEDS_PROCESSING
        self._song_items: dict = {}  # song_id -> QTreeWidgetItem
        self._setup_ui()
        self._populate_tree()

    def _setup_ui(self) -> None:
        """Build UI."""
        self.setWindowTitle("Batch Wizard - Select Files to Process")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(900,600)

        layout = QVBoxLayout(self)

        # Header
        header = QtWidgets.QLabel("<h2>Select Files to Process</h2>")
        layout.addWidget(header)

        # Description
        desc = QtWidgets.QLabel(
            "Choose which songs and media files to transcode. "
            f"Found {len(self.state.scan_results)} candidates."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(10)

        # Filter row
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel("Filter:"))
        
        self.filter_combo = QComboBox()
        for mode in FilterMode:
            self.filter_combo.addItem(mode.value, mode)
        self.filter_combo.setCurrentIndex(1)  # Default to "Needs Processing"
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_combo)
        
        filter_layout.addStretch()
        
        # Selection buttons
        btn_select_all = QPushButton("Select All")
        btn_select_all.clicked.connect(self._on_select_all)
        filter_layout.addWidget(btn_select_all)
        
        btn_deselect_all = QPushButton("Deselect All")
        btn_deselect_all.clicked.connect(self._on_deselect_all)
        filter_layout.addWidget(btn_deselect_all)
        
        btn_invert = QPushButton("Invert Selection")
        btn_invert.clicked.connect(self._on_invert_selection)
        filter_layout.addWidget(btn_invert)
        
        layout.addLayout(filter_layout)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Song / Type", "Status", "Details", "Reason"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 200)
        self.tree.setColumnWidth(3, 200)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree)

        # Navigation buttons
        button_layout = QHBoxLayout()

        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._on_back)
        button_layout.addWidget(self.btn_back)

        button_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        self.btn_next = QPushButton("Start Transcode")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        button_layout.addWidget(self.btn_next)

        layout.addLayout(button_layout)

    def _populate_tree(self) -> None:
        """Populate tree view from scan results."""
        self.tree.blockSignals(True)
        self.tree.clear()
        self._song_items.clear()

        # Group scan results by song
        results_by_song = self.state.get_scan_results_by_song()

        for song_id, (audio_result, video_result) in results_by_song.items():
            # Get song info from first available result
            result = audio_result or video_result
            if not result:
                continue

            # Create parent item for song
            song_item = QTreeWidgetItem(self.tree)
            song_item.setText(0, f"{result.artist} - {result.song_title}")
            song_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, song_id)
            song_item.setFlags(
                song_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsAutoTristate
            )
            song_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
            self._song_items[song_id] = song_item

            # Add audio child if present
            if audio_result:
                self._add_media_child(song_item, audio_result, "Audio")

            # Add video child if present
            if video_result:
                self._add_media_child(song_item, video_result, "Video")

        self.tree.expandAll()
        self.tree.blockSignals(False)
        
        # Apply default filter
        self._apply_filter()

    def _add_media_child(self, parent_item: QTreeWidgetItem, scan_result: ScanResult, media_label: str) -> None:
        """Add a child item for audio or video."""
        child = QTreeWidgetItem(parent_item)
        child.setText(0, media_label)
        child.setData(0, QtCore.Qt.ItemDataRole.UserRole, scan_result)
        child.setFlags(child.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        
        # Determine status and details based on verification results if available
        status = ""
        details = ""
        reason = ""
        
        if scan_result.media_type == "audio" and scan_result.verification_result:
            # Has verification result
            result = scan_result.verification_result
            if result.within_tolerance:
                status = "✓ Within tolerance"
                child.setForeground(1, QtGui.QBrush(QtGui.QColor("green")))
                details = f"I={result.measurements.measured_I:.1f} LUFS"
            else:
                status = "⚠ Out of tolerance"
                child.setForeground(1, QtGui.QBrush(QtGui.QColor("orange")))
                if result.reasons:
                    reason = result.reasons[0]  # Show first reason
                    details = reason
        elif scan_result.needs_processing:
            status = "Needs processing"
            if scan_result.processing_reasons:
                reason = ", ".join(scan_result.processing_reasons)
                details = reason[:50]  # Truncate if long
        else:
            status = "Already compatible"
            child.setForeground(1, QtGui.QBrush(QtGui.QColor("gray")))
        
        child.setText(1, status)
        child.setText(2, details)
        child.setText(3, reason if len(reason) <= 50 else reason[:50] + "...")
        
        # Default check state: check if needs processing
        check_state = QtCore.Qt.CheckState.Checked if scan_result.needs_processing else QtCore.Qt.CheckState.Unchecked
        child.setCheckState(0, check_state)

    def _on_filter_changed(self, index: int) -> None:
        """Handle filter combo change."""
        self._filter_mode = self.filter_combo.itemData(index)
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Apply current filter to tree."""
        for i in range(self.tree.topLevelItemCount()):
            song_item = self.tree.topLevelItem(i)
            if not song_item:
                continue
            
            # Check if any children match filter
            has_visible_child = False
            for j in range(song_item.childCount()):
                child = song_item.child(j)
                if not child:
                    continue
                
                scan_result: ScanResult = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                visible = self._matches_filter(scan_result)
                child.setHidden(not visible)
                if visible:
                    has_visible_child = True
            
            # Hide parent if no children match
            song_item.setHidden(not has_visible_child)

    def _matches_filter(self, scan_result: ScanResult) -> bool:
        """Check if scan result matches current filter."""
        if self._filter_mode == FilterMode.ALL:
            return True
        elif self._filter_mode == FilterMode.NEEDS_PROCESSING:
            return scan_result.needs_processing
        elif self._filter_mode == FilterMode.ALREADY_OK:
            return not scan_result.needs_processing
        elif self._filter_mode == FilterMode.ERRORS:
            # Check if verification had errors
            if scan_result.verification_result:
                return not scan_result.verification_result.within_tolerance and "failed" in str(scan_result.verification_result.reasons).lower()
            return False
        return True

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle item check state change."""
        if column != 0:
            return
        
        # If parent item changed, propagate to children
        if item.childCount() > 0:
            check_state = item.checkState(0)
            for i in range(item.childCount()):
                child = item.child(i)
                if child and not child.isHidden():
                    child.setCheckState(0, check_state)

    def _on_select_all(self) -> None:
        """Select all visible items."""
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            song_item = self.tree.topLevelItem(i)
            if song_item and not song_item.isHidden():
                song_item.setCheckState(0, QtCore.Qt.CheckState.Checked)
        self.tree.blockSignals(False)

    def _on_deselect_all(self) -> None:
        """Deselect all items."""
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            song_item = self.tree.topLevelItem(i)
            if song_item:
                song_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def _on_invert_selection(self) -> None:
        """Invert selection of visible items."""
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            song_item = self.tree.topLevelItem(i)
            if not song_item or song_item.isHidden():
                continue
            
            for j in range(song_item.childCount()):
                child = song_item.child(j)
                if child and not child.isHidden():
                    current = child.checkState(0)
                    new_state = QtCore.Qt.CheckState.Checked if current == QtCore.Qt.CheckState.Unchecked else QtCore.Qt.CheckState.Unchecked
                    child.setCheckState(0, new_state)
        self.tree.blockSignals(False)

    def _on_back(self) -> None:
        """Handle Back button click."""
        self.went_back = True
        self.reject()

    def _on_next(self) -> None:
        """Handle Next button click."""
        # Collect selected items
        selections = self._collect_selections()
        
        if not selections:
            QtWidgets.QMessageBox.warning(
                self,
                "No Selection",
                "Please select at least one file to transcode."
            )
            return
        
        self.state.selected_songs = selections
        _logger.debug(f"Selection complete: {len(selections)} songs selected")
        self.accept()

    def _collect_selections(self) -> list[SongSelection]:
        """Collect user selections from tree."""
        selections: list[SongSelection] = []
        results_by_song = self.state.get_scan_results_by_song()
        
        for song_id, song_item in self._song_items.items():
            audio_result, video_result = results_by_song.get(song_id, (None, None))
            
            # Check which children are selected
            process_audio = False
            process_video = False
            
            for i in range(song_item.childCount()):
                child = song_item.child(i)
                if not child or child.checkState(0) != QtCore.Qt.CheckState.Checked:
                    continue
                
                scan_result: ScanResult = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if scan_result.media_type == "audio":
                    process_audio = True
                else:
                    process_video = True
            
            # Only add if something is selected
            if process_audio or process_video:
                result = audio_result or video_result
                if result:
                    selections.append(SongSelection(
                        song_id=song_id,
                        song_title=result.song_title,
                        artist=result.artist,
                        process_audio=process_audio,
                        process_video=process_video,
                        audio_scan_result=audio_result,
                        video_scan_result=video_result,
                    ))
        
        return selections

    def get_state(self) -> BatchWizardState:
        """Return updated wizard state.
        
        Returns:
            Updated BatchWizardState with selections
        """
        return self.state

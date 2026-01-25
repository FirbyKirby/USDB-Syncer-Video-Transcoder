"""Dialog for selecting videos and reviewing batch estimates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QHeaderView,
    QAbstractItemView,
)

from usdb_syncer.gui import icons

if TYPE_CHECKING:
    from .batch_orchestrator import BatchTranscodeCandidate, BatchTranscodeSummary

_logger = logging.getLogger(__name__)


class BatchPreviewDialog(QDialog):
    """Dialog for selecting videos and reviewing batch estimates."""

    def __init__(
        self,
        parent: QtWidgets.QMainWindow,
        candidates: list[BatchTranscodeCandidate],
        summary: BatchTranscodeSummary
    ):
        super().__init__(parent)
        self.candidates = candidates
        self.summary = summary
        self._setup_ui()
        self._load_data()
        self._update_statistics()

    def _setup_ui(self) -> None:
        """Build UI with summary panel, table, and statistics."""
        self.setWindowTitle("Batch Media Transcode")
        self.setWindowIcon(icons.Icon.FFMPEG.icon())
        self.resize(1000, 700)
        layout = QVBoxLayout(self)

        # 1. Target Settings Panel
        settings_group = QtWidgets.QGroupBox("Target Settings")
        settings_layout = QtWidgets.QGridLayout(settings_group)
        
        settings_layout.addWidget(QLabel("Target Codec:"), 0, 0)
        settings_layout.addWidget(QLabel(f"<b>{self.summary.target_codec}</b>"), 0, 1)
        
        settings_layout.addWidget(QLabel("Target Container:"), 0, 2)
        settings_layout.addWidget(QLabel(f"<b>{self.summary.target_container}</b>"), 0, 3)

        # Audio target summary (always show; mixed batches are supported)
        settings_layout.addWidget(QLabel("Target Audio:"), 1, 0)
        settings_layout.addWidget(
            QLabel(f"<b>{self.summary.target_audio_codec} ({self.summary.target_audio_container})</b>"),
            1,
            1,
        )
        
        settings_layout.addWidget(QLabel("Resolution:"), 2, 0)
        settings_layout.addWidget(QLabel(f"<b>{self.summary.resolution_display}</b>"), 2, 1)

        settings_layout.addWidget(QLabel("FPS:"), 2, 2)
        settings_layout.addWidget(QLabel(f"<b>{self.summary.fps_display}</b>"), 2, 3)
        
        row = 3
        if self.summary.target_codec in ("h264", "hevc"):
            settings_layout.addWidget(QLabel("Profile:"), row, 0)
            settings_layout.addWidget(QLabel(f"<b>{self.summary.target_profile}</b>"), row, 1)
            
            settings_layout.addWidget(QLabel("Pixel Format:"), row, 2)
            settings_layout.addWidget(QLabel(f"<b>{self.summary.target_pixel_format}</b>"), row, 3)
            row += 1
            
        if self.summary.target_bitrate_kbps:
            settings_layout.addWidget(QLabel("Max Bitrate:"), row, 0)
            settings_layout.addWidget(QLabel(f"<b>{self.summary.target_bitrate_kbps} kbps</b>"), row, 1, 1, 3)
            row += 1
        
        layout.addWidget(settings_group)

        # 2. Filter and Selection Buttons
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search by title, artist, codec...")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_edit)
        
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self._on_select_all)
        filter_layout.addWidget(self.btn_select_all)
        
        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.clicked.connect(self._on_deselect_all)
        filter_layout.addWidget(self.btn_deselect_all)
        
        layout.addLayout(filter_layout)

        # 3. Table
        self.table = QTableWidget()
        headers = ["", "Type", "Title", "Artist", "Codec"]
        if self.summary.target_codec in ("h264", "hevc"):
            headers.extend(["Profile", "PixFmt"])
        headers.extend(["Resolution", "FPS", "Container"])
        if self.summary.target_bitrate_kbps:
            headers.append("Bitrate")
        headers.extend(["Size", "Est. Output"])
        
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        # 4. Statistics Panel
        stats_group = QtWidgets.QGroupBox("Statistics")
        self.stats_layout = QVBoxLayout(stats_group)
        
        self.lbl_selected_count = QLabel()
        self.stats_layout.addWidget(self.lbl_selected_count)
        
        self.lbl_est_time = QLabel()
        self.stats_layout.addWidget(self.lbl_est_time)
        
        self.lbl_disk_space = QLabel()
        self.stats_layout.addWidget(self.lbl_disk_space)
        
        self.lbl_free_space = QLabel()
        self.stats_layout.addWidget(self.lbl_free_space)
        
        layout.addWidget(stats_group)

        # 5. Rollback Checkbox
        self.cb_rollback = QCheckBox("Enable rollback protection for this batch")
        self.cb_rollback.setToolTip("Creates temporary backups; can restore on abort")
        self.cb_rollback.setChecked(self.summary.rollback_enabled)
        self.cb_rollback.stateChanged.connect(self._update_statistics)
        layout.addWidget(self.cb_rollback)

        # 6. Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.btn_start = QPushButton("Start Transcoding")
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.accept)
        button_layout.addWidget(self.btn_start)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)
        
        layout.addLayout(button_layout)

    def _load_data(self) -> None:
        """Populate table with candidate videos."""
        self.table.setRowCount(len(self.candidates))
        self.table.blockSignals(True)
        
        for i, c in enumerate(self.candidates):
            # Checkbox
            cb_item = QTableWidgetItem()
            cb_item.setCheckState(QtCore.Qt.CheckState.Checked if c.selected else QtCore.Qt.CheckState.Unchecked)
            cb_item.setData(QtCore.Qt.ItemDataRole.UserRole, i)
            self.table.setItem(i, 0, cb_item)

            media_type = getattr(c, "media_type", "video")
            self.table.setItem(i, 1, QTableWidgetItem("Audio" if media_type == "audio" else "Video"))
            self.table.setItem(i, 2, QTableWidgetItem(c.song_title))
            self.table.setItem(i, 3, QTableWidgetItem(c.artist))
            self.table.setItem(i, 4, QTableWidgetItem(c.current_codec))

            col = 5
            if self.summary.target_codec in ("h264", "hevc"):
                # For audio candidates, profile/pixfmt are always blank.
                profile = "—"
                pixfmt = "—"
                if media_type == "video":
                    profile = c.current_profile or "—"
                    if c.current_codec.lower() not in ("h264", "avc", "hevc", "h265"):
                        profile = "—"
                    pixfmt = c.current_pixel_format or "-"
                self.table.setItem(i, col, QTableWidgetItem(profile))
                col += 1
                self.table.setItem(i, col, QTableWidgetItem(pixfmt))
                col += 1
            
            self.table.setItem(i, col, QTableWidgetItem(c.current_resolution))
            col += 1
            self.table.setItem(i, col, QTableWidgetItem(f"{c.current_fps:.1f}"))
            col += 1
            self.table.setItem(i, col, QTableWidgetItem(c.current_container))
            col += 1
            
            if self.summary.target_bitrate_kbps:
                bitrate_str = f"{c.current_bitrate_kbps}k" if c.current_bitrate_kbps else "-"
                self.table.setItem(i, col, QTableWidgetItem(bitrate_str))
                col += 1
                
            self.table.setItem(i, col, QTableWidgetItem(self._format_size(c.current_size_mb)))
            col += 1
            self.table.setItem(i, col, QTableWidgetItem(self._format_size(c.estimated_output_size_mb)))
            
        self.table.blockSignals(False)

    def _update_statistics(self) -> None:
        """Recalculate statistics based on current selection."""
        visible_count = 0
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                visible_count += 1

        selected_candidates = [c for c in self.candidates if c.selected]
        selected_count = len(selected_candidates)
        
        total_time = sum(c.estimated_time_seconds for c in selected_candidates)
        
        # Disk space calculation
        from .batch_estimator import BatchEstimator
        required_space = BatchEstimator.calculate_disk_space_required(
            self.candidates,
            self.cb_rollback.isChecked(),
            self.summary.rollback_enabled # This is a bit redundant but follows architecture
        )
        
        self.lbl_selected_count.setText(f"Selected: <b>{selected_count}</b> of {visible_count} media files")
        self.lbl_est_time.setText(f"Estimated Time: <b>{self._format_duration(total_time)}</b>")
        self.lbl_disk_space.setText(f"Disk Space Required: <b>{self._format_size(required_space)}</b>")
        
        free_space = self.summary.current_free_space_mb
        space_ok = free_space >= required_space
        status_icon = "✓" if space_ok else "✗"
        color = "green" if space_ok else "red"
        self.lbl_free_space.setText(
            f"Current Free Space: <span style='color: {color}'><b>{self._format_size(free_space)} {status_icon}</b></span>"
        )
        
        self.btn_start.setEnabled(selected_count > 0 and space_ok)

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _format_size(self, size_mb: float) -> str:
        """Format size in MB, GB, or kB."""
        if size_mb >= 1024:
            return f"{size_mb / 1024:.2f} GB"
        if size_mb < 1:
            return f"{size_mb * 1024:.2f} kB"
        return f"{size_mb:.2f} MB"

    def _on_filter_changed(self, text: str) -> None:
        """Filter table rows based on search text."""
        text = text.lower()
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            match = False
            for j in range(1, self.table.columnCount()):
                item = self.table.item(i, j)
                if item and text in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(i, not match)
            if not match:
                item = self.table.item(i, 0)
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
                self.candidates[idx].selected = False
        self.table.blockSignals(False)
        self._update_statistics()

    def _on_select_all(self) -> None:
        """Select all visible rows."""
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                item = self.table.item(i, 0)
                item.setCheckState(QtCore.Qt.CheckState.Checked)
                idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
                self.candidates[idx].selected = True
        self.table.blockSignals(False)
        self._update_statistics()

    def _on_deselect_all(self) -> None:
        """Deselect all visible rows."""
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                item = self.table.item(i, 0)
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
                self.candidates[idx].selected = False
        self.table.blockSignals(False)
        self._update_statistics()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Handle checkbox state change."""
        if item.column() == 0:
            idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
            self.candidates[idx].selected = (item.checkState() == QtCore.Qt.CheckState.Checked)
            self._update_statistics()

    def get_selected_candidates(self) -> list[BatchTranscodeCandidate]:
        """Return list of selected candidates."""
        return [c for c in self.candidates if c.selected]

    def is_rollback_enabled(self) -> bool:
        """Return rollback checkbox state."""
        return self.cb_rollback.isChecked()

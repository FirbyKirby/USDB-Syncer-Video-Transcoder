from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt

from usdb_syncer.gui import icons

from .backup_manager import BackupInfo

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class BackupSelectionDialog(QtWidgets.QDialog):
    """Dialog for selecting backups to delete or restore."""

    chosen_action: str | None = None

    def __init__(
        self,
        backups: list[BackupInfo],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.backups = backups
        self._setup_ui()
        self._populate_table()
        self._update_stats()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Manage Media Backups")
        self.setWindowIcon(icons.Icon.CHANGES.icon())
        self.resize(800, 500)
        layout = QtWidgets.QVBoxLayout(self)

        # Filter and selection buttons
        top_layout = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter backups...")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        top_layout.addWidget(self.filter_edit)

        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_select_all.clicked.connect(lambda: self._set_all_selected(True))
        top_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QtWidgets.QPushButton("Deselect All")
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_selected(False))
        top_layout.addWidget(self.btn_deselect_all)
        
        layout.addLayout(top_layout)

        # Table
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Select", "Title", "Artist", "Backup File", "Size", "Date"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Stats
        self.stats_group = QtWidgets.QGroupBox("Statistics")
        stats_layout = QtWidgets.QVBoxLayout(self.stats_group)
        self.lbl_selected_count = QtWidgets.QLabel("Selected: 0 of 0 backups")
        self.lbl_total_size = QtWidgets.QLabel("Total Size: 0.0 MB")
        stats_layout.addWidget(self.lbl_selected_count)
        stats_layout.addWidget(self.lbl_total_size)
        layout.addWidget(self.stats_group)

        # Warning
        warning_text = (
            "You can delete backups to reclaim disk space, or restore them to replace active transcoded media files. "
            "Both operations are destructive and require confirmation."
        )
            
        warning_label = QtWidgets.QLabel(warning_text)
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(warning_label)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)
        
        button_layout.addStretch()

        self.btn_restore = QtWidgets.QPushButton("Restore Selected")
        self.btn_restore.setIcon(icons.Icon.CHECK_FOR_UPDATE.icon())
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        button_layout.addWidget(self.btn_restore)

        self.btn_delete = QtWidgets.QPushButton("Delete Selected")
        self.btn_delete.setIcon(icons.Icon.DELETE.icon())
        self.btn_delete.setStyleSheet("QPushButton { color: #ff4444; font-weight: bold; }")
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        button_layout.addWidget(self.btn_delete)
        
        layout.addLayout(button_layout)

    def _on_restore_clicked(self) -> None:
        self.chosen_action = "restore"
        self.accept()

    def _on_delete_clicked(self) -> None:
        self.chosen_action = "delete"
        self.accept()

    def accept(self) -> None:
        """Defensive check to ensure an action was chosen."""
        if not self.chosen_action:
            return
        super().accept()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self.backups))
        for i, backup in enumerate(self.backups):
            # Checkbox
            cb_item = QtWidgets.QTableWidgetItem()
            cb_item.setCheckState(Qt.CheckState.Checked if backup.selected else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, cb_item)

            # Data
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(backup.song_title))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(backup.artist))
            self.table.setItem(i, 3, QtWidgets.QTableWidgetItem(backup.backup_path.name))
            
            size_item = QtWidgets.QTableWidgetItem(f"{backup.size_mb:.1f} MB")
            size_item.setData(Qt.ItemDataRole.UserRole, backup.size_mb)
            self.table.setItem(i, 4, size_item)

            date_str = QtCore.QDateTime.fromSecsSinceEpoch(int(backup.backup_date)).toString("yyyy-MM-dd HH:mm") if backup.backup_date else "Unknown"
            self.table.setItem(i, 5, QtWidgets.QTableWidgetItem(date_str))

        self.table.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() == 0:
            row = item.row()
            self.backups[row].selected = (item.checkState() == Qt.CheckState.Checked)
            self._update_stats()

    def _on_filter_changed(self, text: str) -> None:
        text = text.lower()
        for i in range(self.table.rowCount()):
            match = any(text in (self.table.item(i, j).text().lower()) for j in range(1, 6))
            self.table.setRowHidden(i, not match)

    def _set_all_selected(self, selected: bool) -> None:
        self.table.blockSignals(True)
        state = Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                self.table.item(i, 0).setCheckState(state)
                self.backups[i].selected = selected
        self.table.blockSignals(False)
        self._update_stats()

    def _update_stats(self) -> None:
        selected = [b for b in self.backups if b.selected]
        count = len(selected)
        total_size = sum(b.size_mb for b in selected)
        
        self.lbl_selected_count.setText(f"Selected: {count} of {len(self.backups)} backups")
        self.lbl_total_size.setText(f"Total Size: {total_size:.1f} MB")
        
        has_selection = count > 0
        self.btn_restore.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)

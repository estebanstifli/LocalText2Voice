from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.bulk_audiobook_store import BulkAudiobookBatch, BulkAudiobookItem
from app.core.book_metadata import default_cover_image

from .icons import ui_icon


class BulkAudiobooksPage(QWidget):
    createRequested = Signal(object)
    batchSelectionChanged = Signal(int)
    startRequested = Signal(int)
    pauseRequested = Signal(int)
    cancelRequested = Signal(int)
    retryRequested = Signal(int)
    refreshRequested = Signal()
    openProjectRequested = Signal(int)
    openFolderRequested = Signal(str)

    SOURCE_PATH_ROLE = Qt.ItemDataRole.UserRole
    DEFAULT_MUSIC_SENTINEL = "__bulk_default_music__"

    def __init__(self, translate: Callable[..., str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = translate
        self.music_files: list[Path] = []
        self.current_default_music: Path | None = None
        self._batch_items: dict[int, list[BulkAudiobookItem]] = {}
        self._review_settings: dict[str, object] = {}
        self._whisper_available = False
        self._flow_context_initialized = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QLabel(
            self.tr_text(
                "bulk_intro",
                "Create many independent, editable audiobook projects and render "
                "them sequentially without supervision.",
            )
        )
        intro.setWordWrap(True)
        intro.setObjectName("helperLabel")
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_new_task_tab(), self.tr_text("bulk_new_task", "New task"))
        self.tabs.addTab(self._build_monitor_tab(), self.tr_text("bulk_monitor", "Monitor"))
        layout.addWidget(self.tabs, 1)

    def _build_new_task_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        source_card = QFrame()
        source_card.setObjectName("card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(14, 14, 14, 14)
        source_layout.setSpacing(10)
        source_title = QLabel(self.tr_text("bulk_sources", "1. Source books"))
        source_title.setObjectName("sectionLabel")
        source_help = QLabel(
            self.tr_text(
                "bulk_sources_help",
                "Each TXT file becomes a separate LocalText2Voice project. Titles "
                "and background music can be adjusted before creation. Existing "
                "markup and effects are copied unchanged.",
            )
        )
        source_help.setWordWrap(True)
        source_help.setObjectName("helperLabel")
        source_actions = QHBoxLayout()
        add_files = QPushButton(self.tr_text("bulk_add_txt", "Add TXT files"))
        add_files.setIcon(ui_icon("file"))
        add_files.clicked.connect(self._choose_source_files)
        add_folder = QPushButton(self.tr_text("bulk_add_folder", "Add folder"))
        add_folder.setIcon(ui_icon("folder"))
        add_folder.clicked.connect(self._choose_source_folder)
        clear = QPushButton(self.tr_text("clear", "Clear"))
        clear.setIcon(ui_icon("delete"))
        clear.clicked.connect(self.clear_sources)
        self.recursive_checkbox = QCheckBox(
            self.tr_text("bulk_include_subfolders", "Include subfolders")
        )
        source_actions.addWidget(add_files)
        source_actions.addWidget(add_folder)
        source_actions.addWidget(clear)
        source_actions.addStretch(1)
        source_actions.addWidget(self.recursive_checkbox)

        self.sources_table = QTableWidget(0, 5)
        self.sources_table.setHorizontalHeaderLabels(
            [
                self.tr_text("source_file", "Source file"),
                self.tr_text("project_title", "Project title"),
                self.tr_text("cover", "Cover"),
                self.tr_text("background_music", "Background music"),
                self.tr_text("actions", "Actions"),
            ]
        )
        self.sources_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.sources_table.setAlternatingRowColors(True)
        self.sources_table.horizontalHeader().setStretchLastSection(False)
        self.sources_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.sources_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.sources_table.setColumnWidth(2, 74)
        self.sources_table.setColumnWidth(3, 250)
        self.sources_table.setColumnWidth(4, 96)
        self.sources_table.itemChanged.connect(self._on_source_item_changed)
        source_layout.addWidget(source_title)
        source_layout.addWidget(source_help)
        source_layout.addLayout(source_actions)
        source_layout.addWidget(self.sources_table, 1)
        root.addWidget(source_card, 1)

        config_card = QFrame()
        config_card.setObjectName("card")
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(14, 14, 14, 14)
        config_layout.setSpacing(10)
        config_title = QLabel(self.tr_text("bulk_configuration", "2. Task configuration"))
        config_title.setObjectName("sectionLabel")
        config_layout.addWidget(config_title)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(self.tr_text("bulk_task_name", "Task name")))
        self.task_name_edit = QLineEdit(
            self.tr_text(
                "bulk_default_task_name",
                "Bulk Audiobooks {date}",
                date=time.strftime("%Y-%m-%d"),
            )
        )
        name_row.addWidget(self.task_name_edit, 1)
        config_layout.addLayout(name_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel(self.tr_text("project_parent", "Project parent folder")))
        self.output_parent_edit = QLineEdit()
        output_browse = QPushButton(self.tr_text("browse", "Browse"))
        output_browse.setIcon(ui_icon("folder"))
        output_browse.clicked.connect(self._choose_output_parent)
        output_row.addWidget(self.output_parent_edit, 1)
        output_row.addWidget(output_browse)
        config_layout.addLayout(output_row)

        music_row = QHBoxLayout()
        music_row.addWidget(QLabel(self.tr_text("bulk_default_music", "Default music")))
        self.default_music_combo = QComboBox()
        music_row.addWidget(self.default_music_combo, 1)
        config_layout.addLayout(music_row)

        flow_row = QHBoxLayout()
        flow_row.addWidget(QLabel(self.tr_text("bulk_creation_flow", "Creation flow")))
        self.creation_flow_combo = QComboBox()
        self.creation_flow_combo.addItem(
            self.tr_text(
                "bulk_flow_settings",
                "Follow current Settings (Recommended)",
            ),
            "settings",
        )
        self.creation_flow_combo.addItem(
            self.tr_text(
                "bulk_flow_auto_review",
                "Generation → Review → Retry failed segments (Auto)",
            ),
            "auto_review",
        )
        self.creation_flow_combo.addItem(
            self.tr_text(
                "bulk_flow_review_only",
                "Generation → Review only",
            ),
            "review_only",
        )
        self.creation_flow_combo.addItem(
            self.tr_text("bulk_flow_generation_only", "Generation only"),
            "generation_only",
        )
        self.creation_flow_combo.currentIndexChanged.connect(
            self._update_creation_flow_help
        )
        flow_row.addWidget(self.creation_flow_combo, 1)
        self.flow_retry_label = QLabel(
            self.tr_text("bulk_flow_retries", "Retries")
        )
        self.flow_retry_spin = QSpinBox()
        self.flow_retry_spin.setRange(1, 5)
        self.flow_retry_spin.setValue(1)
        self.flow_retry_spin.setToolTip(
            self.tr_text(
                "bulk_flow_retries_help",
                "Maximum automatic TTS retries for each segment that does not pass review.",
            )
        )
        self.flow_retry_spin.valueChanged.connect(self._update_creation_flow_help)
        flow_row.addWidget(self.flow_retry_label)
        flow_row.addWidget(self.flow_retry_spin)
        config_layout.addLayout(flow_row)

        self.creation_flow_help = QLabel()
        self.creation_flow_help.setObjectName("helperLabel")
        self.creation_flow_help.setWordWrap(True)
        config_layout.addWidget(self.creation_flow_help)

        self.create_only_checkbox = QCheckBox(
            self.tr_text(
                "bulk_create_only",
                "Create editable projects only; do not generate audio yet",
            )
        )
        config_layout.addWidget(self.create_only_checkbox)

        self.frozen_profile_label = QLabel()
        self.frozen_profile_label.setObjectName("helperLabel")
        self.frozen_profile_label.setWordWrap(True)
        config_layout.addWidget(self.frozen_profile_label)

        start_row = QHBoxLayout()
        self.create_button = QPushButton(
            self.tr_text("bulk_create_generate", "Create projects and generate")
        )
        self.create_button.setIcon(ui_icon("generate"))
        self.create_button.clicked.connect(self._emit_create_request)
        self.create_only_checkbox.toggled.connect(self._update_create_button)
        start_row.addStretch(1)
        start_row.addWidget(self.create_button)
        config_layout.addLayout(start_row)
        root.addWidget(config_card)
        return tab

    def _build_monitor_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(self.tr_text("bulk_task", "Task")))
        self.batch_combo = QComboBox()
        self.batch_combo.currentIndexChanged.connect(self._on_batch_changed)
        toolbar.addWidget(self.batch_combo, 1)
        refresh = QPushButton(self.tr_text("refresh", "Refresh"))
        refresh.setIcon(ui_icon("refresh"))
        refresh.clicked.connect(self.refreshRequested.emit)
        self.start_button = QPushButton(self.tr_text("resume", "Start / Resume"))
        self.start_button.setIcon(ui_icon("play"))
        self.start_button.clicked.connect(self._emit_start)
        self.pause_button = QPushButton(self.tr_text("pause_after_current", "Pause after current"))
        self.pause_button.setIcon(ui_icon("pause"))
        self.pause_button.clicked.connect(self._emit_pause)
        self.cancel_button = QPushButton(self.tr_text("cancel_pending", "Cancel"))
        self.cancel_button.setIcon(ui_icon("stop"))
        self.cancel_button.clicked.connect(self._emit_cancel)
        self.retry_button = QPushButton(self.tr_text("retry_failed", "Retry failed"))
        self.retry_button.setIcon(ui_icon("regenerate"))
        self.retry_button.clicked.connect(self._emit_retry)
        for widget in (
            refresh,
            self.start_button,
            self.pause_button,
            self.cancel_button,
            self.retry_button,
        ):
            toolbar.addWidget(widget)
        root.addLayout(toolbar)

        self.batch_summary_label = QLabel(self.tr_text("no_bulk_tasks", "No bulk tasks yet."))
        self.batch_summary_label.setWordWrap(True)
        self.batch_summary_label.setObjectName("helperLabel")
        root.addWidget(self.batch_summary_label)

        self.monitor_progress = QProgressBar()
        self.monitor_progress.setRange(0, 100)
        self.monitor_progress.setValue(0)
        root.addWidget(self.monitor_progress)

        self.items_table = QTableWidget(0, 7)
        self.items_table.setHorizontalHeaderLabels(
            [
                "#",
                self.tr_text("project_title", "Project title"),
                self.tr_text("background_music", "Background music"),
                self.tr_text("status", "Status"),
                self.tr_text("progress", "Progress"),
                self.tr_text("message", "Message"),
                self.tr_text("actions", "Actions"),
            ]
        )
        self.items_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.verticalHeader().setVisible(False)
        header = self.items_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.items_table.setColumnWidth(0, 48)
        self.items_table.setColumnWidth(2, 190)
        self.items_table.setColumnWidth(3, 105)
        self.items_table.setColumnWidth(4, 90)
        self.items_table.setColumnWidth(6, 190)
        root.addWidget(self.items_table, 1)
        return tab

    def set_output_parent(self, path: Path) -> None:
        if not self.output_parent_edit.text().strip():
            self.output_parent_edit.setText(str(path))

    def set_frozen_profile_summary(self, summary: str) -> None:
        self.frozen_profile_label.setText(summary)

    def set_creation_flow_context(
        self,
        review_settings: dict[str, object],
        whisper_available: bool,
    ) -> None:
        self._review_settings = dict(review_settings)
        self._whisper_available = bool(whisper_available)
        if not self._flow_context_initialized:
            retries = max(1, min(5, int(review_settings.get("max_retries", 0) or 1)))
            blocked = self.flow_retry_spin.blockSignals(True)
            self.flow_retry_spin.setValue(retries)
            self.flow_retry_spin.blockSignals(blocked)
            self._flow_context_initialized = True
        self._update_creation_flow_help()

    def set_music_files(
        self,
        files: Iterable[Path],
        current_default: Path | None,
    ) -> None:
        self.music_files = sorted(
            {Path(path).resolve() for path in files if Path(path).is_file()},
            key=lambda path: path.name.casefold(),
        )
        self.current_default_music = (
            current_default.resolve()
            if current_default is not None and current_default.is_file()
            else None
        )
        previous = self.default_music_combo.currentData()
        self.default_music_combo.clear()
        if self.current_default_music is not None:
            self.default_music_combo.addItem(
                self.tr_text(
                    "bulk_current_default_music",
                    "Current default — {name}",
                    name=self.current_default_music.name,
                ),
                str(self.current_default_music),
            )
        self.default_music_combo.addItem(self.tr_text("no_music", "No music"), "")
        for path in self.music_files:
            if path == self.current_default_music:
                continue
            self.default_music_combo.addItem(path.name, str(path))
        index = self.default_music_combo.findData(previous)
        if index >= 0:
            self.default_music_combo.setCurrentIndex(index)
        for row in range(self.sources_table.rowCount()):
            combo = self.sources_table.cellWidget(row, 3)
            if isinstance(combo, QComboBox):
                selected = combo.currentData()
                self._populate_item_music_combo(combo)
                selected_index = combo.findData(selected)
                if selected_index >= 0:
                    combo.setCurrentIndex(selected_index)

    def add_source_paths(self, paths: Iterable[Path]) -> None:
        existing = {
            str(self.sources_table.item(row, 0).data(self.SOURCE_PATH_ROLE)).casefold()
            for row in range(self.sources_table.rowCount())
            if self.sources_table.item(row, 0) is not None
        }
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            key = str(resolved).casefold()
            if resolved.suffix.casefold() != ".txt" or key in existing:
                continue
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            source_item = QTableWidgetItem(resolved.name)
            source_item.setToolTip(str(resolved))
            source_item.setData(self.SOURCE_PATH_ROLE, str(resolved))
            source_item.setFlags(source_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            title_item = QTableWidgetItem(resolved.stem)
            cover_button = self._new_cover_button(resolved.stem)
            music_combo = QComboBox()
            self._populate_item_music_combo(music_combo)
            remove = QPushButton(self.tr_text("remove", "Remove"))
            remove.setIcon(ui_icon("delete"))
            remove.clicked.connect(
                lambda _checked=False, source=str(resolved): self._remove_source(source)
            )
            self.sources_table.setItem(row, 0, source_item)
            self.sources_table.setItem(row, 1, title_item)
            self.sources_table.setCellWidget(row, 2, cover_button)
            self.sources_table.setCellWidget(row, 3, music_combo)
            self.sources_table.setCellWidget(row, 4, remove)
            self.sources_table.setRowHeight(row, 58)
            existing.add(key)
        self.sources_table.resizeRowsToContents()

    def clear_sources(self) -> None:
        self.sources_table.setRowCount(0)

    def creation_payload(self) -> dict[str, object]:
        sources: list[dict[str, str]] = []
        default_music = str(self.default_music_combo.currentData() or "")
        for row in range(self.sources_table.rowCount()):
            source_item = self.sources_table.item(row, 0)
            title_item = self.sources_table.item(row, 1)
            cover_button = self.sources_table.cellWidget(row, 2)
            music_combo = self.sources_table.cellWidget(row, 3)
            if source_item is None or title_item is None:
                continue
            selected_music = (
                str(music_combo.currentData() or "")
                if isinstance(music_combo, QComboBox)
                else self.DEFAULT_MUSIC_SENTINEL
            )
            if selected_music == self.DEFAULT_MUSIC_SENTINEL:
                selected_music = default_music
            sources.append(
                {
                    "source_path": str(source_item.data(self.SOURCE_PATH_ROLE) or ""),
                    "title": title_item.text().strip(),
                    "cover_path": str(cover_button.property("cover_path") or "")
                    if isinstance(cover_button, QPushButton)
                    else "",
                    "music_path": selected_music,
                }
            )
        return {
            "title": self.task_name_edit.text().strip(),
            "output_parent": self.output_parent_edit.text().strip(),
            "create_only": self.create_only_checkbox.isChecked(),
            "creation_flow": str(self.creation_flow_combo.currentData() or "settings"),
            "flow_max_retries": self.flow_retry_spin.value(),
            "creation_flow_label": self.creation_flow_combo.currentText(),
            "creation_flow_summary": self.creation_flow_help.text(),
            "sources": sources,
        }

    def _new_cover_button(self, title: str) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(52, 52)
        button.setIconSize(QSize(46, 46))
        button.setProperty("cover_path", "")
        button.setToolTip(
            self.tr_text(
                "bulk_cover_hint",
                "Automatic cover. Click to choose an image; right-click to restore automatic.",
            )
        )
        button.clicked.connect(lambda _checked=False, target=button: self._choose_row_cover(target))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda _point, target=button: self._reset_row_cover(target)
        )
        self._set_cover_button_preview(button, title)
        return button

    def _set_cover_button_preview(self, button: QPushButton, title: str) -> None:
        cover_path = str(button.property("cover_path") or "")
        pixmap = QPixmap(cover_path) if cover_path else QPixmap.fromImage(
            default_cover_image(title or "Project", 180)
        )
        button.setIcon(QIcon(pixmap))

    def _choose_row_cover(self, button: QPushButton) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_text("choose_cover", "Choose cover image"),
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp);;All files (*.*)",
        )
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        button.setProperty("cover_path", path)
        button.setIcon(QIcon(pixmap))
        button.setToolTip(path)

    def _reset_row_cover(self, button: QPushButton) -> None:
        button.setProperty("cover_path", "")
        row = next(
            (index for index in range(self.sources_table.rowCount())
             if self.sources_table.cellWidget(index, 2) is button),
            -1,
        )
        title_item = self.sources_table.item(row, 1) if row >= 0 else None
        self._set_cover_button_preview(button, title_item.text() if title_item else "Project")

    def _on_source_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        button = self.sources_table.cellWidget(item.row(), 2)
        if isinstance(button, QPushButton) and not str(button.property("cover_path") or ""):
            self._set_cover_button_preview(button, item.text())

    def set_batches(
        self,
        batches: Iterable[BulkAudiobookBatch],
        selected_batch_id: int | None = None,
    ) -> None:
        previous = selected_batch_id or self.selected_batch_id()
        blocked = self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        for batch in batches:
            self.batch_combo.addItem(f"{batch.title} — {batch.status}", batch.id)
        index = self.batch_combo.findData(previous)
        self.batch_combo.setCurrentIndex(index if index >= 0 else 0)
        self.batch_combo.blockSignals(blocked)
        current = self.selected_batch_id()
        if current is not None:
            self.batchSelectionChanged.emit(current)
        else:
            self.set_batch_details(None, [])

    def set_batch_details(
        self,
        batch: BulkAudiobookBatch | None,
        items: list[BulkAudiobookItem],
    ) -> None:
        self.items_table.setRowCount(0)
        if batch is None:
            self.batch_summary_label.setText(self.tr_text("no_bulk_tasks", "No bulk tasks yet."))
            self.monitor_progress.setValue(0)
            for button in (
                self.start_button,
                self.pause_button,
                self.cancel_button,
                self.retry_button,
            ):
                button.setEnabled(False)
            return
        counts: dict[str, int] = {}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
            self._append_monitor_item(item)
        total = len(items)
        complete = counts.get("complete", 0)
        terminal = complete + counts.get("failed", 0) + counts.get("cancelled", 0)
        running_fraction = sum(
            min(1.0, item.progress_current / item.progress_total)
            for item in items
            if item.status == "running" and item.progress_total > 0
        )
        self.monitor_progress.setValue(
            round((terminal + running_fraction) / total * 100) if total else 0
        )
        self.batch_summary_label.setText(
            self.tr_text(
                "bulk_summary",
                "Status: {status} · {complete}/{total} complete · {failed} failed · "
                "{queued} queued",
                status=batch.status.replace("_", " ").title(),
                complete=complete,
                total=total,
                failed=counts.get("failed", 0),
                queued=counts.get("queued", 0),
            )
        )
        profile_summary = str(batch.config().get("profile_summary", "")).strip()
        if profile_summary:
            self.batch_summary_label.setText(
                self.batch_summary_label.text()
                + "\n"
                + self.tr_text(
                    "bulk_frozen_profile",
                    "Frozen profile: {profile}",
                    profile=profile_summary,
                )
            )
        flow_config = batch.config()
        flow_summary = str(
            flow_config.get("creation_flow_summary")
            or flow_config.get("creation_flow_label", "")
        ).strip()
        if flow_summary:
            self.batch_summary_label.setText(
                self.batch_summary_label.text()
                + "\n"
                + self.tr_text(
                    "bulk_monitor_creation_flow",
                    "Creation flow: {flow}",
                    flow=flow_summary,
                )
            )
        active = batch.status in {"running", "pausing"}
        self.start_button.setEnabled(not active and counts.get("queued", 0) > 0)
        self.pause_button.setEnabled(batch.status == "running")
        self.cancel_button.setEnabled(active or counts.get("queued", 0) > 0)
        self.retry_button.setEnabled(
            not active
            and (counts.get("failed", 0) + counts.get("cancelled", 0) > 0)
        )
        self.items_table.resizeRowsToContents()

    def show_monitor(self, batch_id: int) -> None:
        self.tabs.setCurrentIndex(1)
        index = self.batch_combo.findData(batch_id)
        if index >= 0:
            self.batch_combo.setCurrentIndex(index)

    def selected_batch_id(self) -> int | None:
        value = self.batch_combo.currentData()
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _append_monitor_item(self, item: BulkAudiobookItem) -> None:
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)
        progress = (
            f"{round(item.progress_current / item.progress_total * 100)}%"
            if item.progress_total
            else ("100%" if item.status == "complete" else "—")
        )
        message = (
            f"{item.error_message}\n{item.message}"
            if item.error_message and item.message
            else (item.error_message or item.message)
        )
        values = (
            str(item.position + 1),
            item.title,
            Path(item.music_path).name if item.music_path else self.tr_text("no_music", "No music"),
            self._status_label(item.status),
            progress,
            message,
        )
        for column, value in enumerate(values):
            cell = QTableWidgetItem(value)
            if column in {0, 3, 4}:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 5:
                cell.setToolTip(message)
            elif item.error_message:
                cell.setToolTip(item.error_message)
            self.items_table.setItem(row, column, cell)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(6)
        if item.audiobook_id is not None:
            open_project = QPushButton(self.tr_text("project", "Project"))
            open_project.setIcon(ui_icon("open"))
            open_project.setToolTip(self.tr_text("open_project", "Open project"))
            open_project.clicked.connect(
                lambda _checked=False, audiobook_id=item.audiobook_id: (
                    self.openProjectRequested.emit(audiobook_id)
                )
            )
            actions_layout.addWidget(open_project)
        output = item.mix_audio_path or item.clean_audio_path
        if output:
            open_folder = QPushButton(self.tr_text("folder", "Folder"))
            open_folder.setIcon(ui_icon("folder"))
            open_folder.setToolTip(self.tr_text("open_folder", "Open folder"))
            open_folder.clicked.connect(
                lambda _checked=False, path=output: self.openFolderRequested.emit(path)
            )
            actions_layout.addWidget(open_folder)
        actions_layout.addStretch(1)
        self.items_table.setCellWidget(row, 6, actions)

    def _populate_item_music_combo(self, combo: QComboBox) -> None:
        combo.clear()
        combo.addItem(
            self.tr_text("use_bulk_default", "Use task default"),
            self.DEFAULT_MUSIC_SENTINEL,
        )
        combo.addItem(self.tr_text("no_music", "No music"), "")
        for path in self.music_files:
            combo.addItem(path.name, str(path))

    def _status_label(self, status: str) -> str:
        labels = {
            "queued": self.tr_text("queued", "Queued"),
            "running": self.tr_text("running", "Running"),
            "complete": self.tr_text("complete", "Complete"),
            "failed": self.tr_text("failed", "Failed"),
            "cancelled": self.tr_text("cancelled", "Cancelled"),
        }
        return labels.get(status, status.replace("_", " ").title())

    def _choose_source_files(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            self.tr_text("bulk_add_txt", "Add TXT files"),
            "",
            "Text files (*.txt);;All files (*.*)",
        )
        self.add_source_paths(Path(path) for path in paths)

    def _choose_source_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            self.tr_text("bulk_add_folder", "Add folder"),
            "",
        )
        if not directory:
            return
        root = Path(directory)
        pattern = "**/*.txt" if self.recursive_checkbox.isChecked() else "*.txt"
        self.add_source_paths(sorted(root.glob(pattern)))

    def _choose_output_parent(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            self.tr_text("project_parent_folder_prompt", "Choose project parent folder"),
            self.output_parent_edit.text(),
        )
        if directory:
            self.output_parent_edit.setText(directory)

    def _remove_source(self, source_path: str) -> None:
        wanted = source_path.casefold()
        for row in range(self.sources_table.rowCount()):
            item = self.sources_table.item(row, 0)
            if item is None:
                continue
            if str(item.data(self.SOURCE_PATH_ROLE) or "").casefold() == wanted:
                self.sources_table.removeRow(row)
                return

    def _emit_create_request(self) -> None:
        self.createRequested.emit(self.creation_payload())

    def _update_create_button(self, create_only: bool) -> None:
        self.create_button.setText(
            self.tr_text("bulk_create_projects", "Create projects")
            if create_only
            else self.tr_text("bulk_create_generate", "Create projects and generate")
        )
        self.creation_flow_combo.setEnabled(not create_only)
        self._update_creation_flow_help()

    def _update_creation_flow_help(self, _value: int | None = None) -> None:
        flow = str(self.creation_flow_combo.currentData() or "settings")
        configured_retries = max(
            0,
            int(self._review_settings.get("max_retries", 0) or 0),
        )
        settings_review = bool(self._review_settings.get("enabled", False)) and bool(
            self._review_settings.get("auto_verify_after_generation", False)
        )
        if flow == "settings":
            if settings_review:
                description = self.tr_text(
                    "bulk_flow_settings_review_description",
                    "Uses the frozen Settings flow: text normalization when enabled → "
                    "generation → Whisper review → up to {retries} automatic segment "
                    "retries.",
                    retries=configured_retries,
                )
            else:
                description = self.tr_text(
                    "bulk_flow_settings_generation_description",
                    "Uses the frozen Settings flow. Text normalization is applied when "
                    "enabled; automatic Whisper review is currently disabled.",
                )
        elif flow == "auto_review":
            description = self.tr_text(
                "bulk_flow_auto_review_description",
                "Text normalization when enabled → generation → Whisper review → up "
                "to {retries} automatic retries for each segment that does not pass.",
                retries=self.flow_retry_spin.value(),
            )
        elif flow == "review_only":
            description = self.tr_text(
                "bulk_flow_review_only_description",
                "Text normalization when enabled → generation → Whisper review. Failed "
                "segments are reported without automatic regeneration.",
            )
        else:
            description = self.tr_text(
                "bulk_flow_generation_only_description",
                "Text normalization when enabled → generation. Whisper review and "
                "automatic segment retries are skipped.",
            )
        review_required = flow in {"auto_review", "review_only"} or (
            flow == "settings" and settings_review
        )
        if review_required and not self._whisper_available:
            description += " " + self.tr_text(
                "bulk_flow_whisper_missing",
                "Faster Whisper must be installed before this task can run.",
            )
        if self.create_only_checkbox.isChecked():
            description = self.tr_text(
                "bulk_flow_create_only_description",
                "Creation flow is not run in project-only mode.",
            )
        self.flow_retry_label.setVisible(flow == "auto_review")
        self.flow_retry_spin.setVisible(flow == "auto_review")
        self.flow_retry_spin.setEnabled(
            flow == "auto_review" and not self.create_only_checkbox.isChecked()
        )
        self.creation_flow_help.setText(description)

    def _on_batch_changed(self, _index: int) -> None:
        batch_id = self.selected_batch_id()
        if batch_id is not None:
            self.batchSelectionChanged.emit(batch_id)

    def _emit_start(self) -> None:
        batch_id = self.selected_batch_id()
        if batch_id is not None:
            self.startRequested.emit(batch_id)

    def _emit_pause(self) -> None:
        batch_id = self.selected_batch_id()
        if batch_id is not None:
            self.pauseRequested.emit(batch_id)

    def _emit_cancel(self) -> None:
        batch_id = self.selected_batch_id()
        if batch_id is not None:
            self.cancelRequested.emit(batch_id)

    def _emit_retry(self) -> None:
        batch_id = self.selected_batch_id()
        if batch_id is not None:
            self.retryRequested.emit(batch_id)

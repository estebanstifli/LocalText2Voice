from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.tts.voice_catalog import HuggingFaceVoiceCatalog, RemoteVoice
from app.utils.i18n import Translator
from app.workers.voice_catalog_worker import VoiceCatalogWorker

from .icons import ui_icon


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


class VoiceManagerDialog(QDialog):
    voices_changed = Signal()

    def __init__(
        self,
        voices_root: Path,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.voices_root = voices_root
        self.translator = translator
        self.catalog_service = HuggingFaceVoiceCatalog(voices_root)
        self.remote_voices: list[RemoteVoice] = []
        self.visible_voices: list[RemoteVoice] = []
        self.worker: VoiceCatalogWorker | None = None
        self.worker_thread: QThread | None = None
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.sample_player = QMediaPlayer(self)
        self.sample_player.setAudioOutput(self.audio_output)
        self.sample_player.playbackStateChanged.connect(
            self._on_sample_playback_state
        )
        self.sample_player.errorOccurred.connect(self._on_sample_error)

        self.setWindowTitle(self.tr("voice_manager", "Voice Manager"))
        self.setMinimumSize(850, 560)
        self.resize(980, 680)
        self._build_ui()
        self._set_busy(False)
        self._load_catalog()

    def tr(self, key: str, default: str | None = None, **values: object) -> str:
        return self.translator.text(key, default, **values)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        title = QLabel(self.tr("download_piper_voices", "Download Piper voices"))
        title.setObjectName("sectionLabel")
        description = QLabel(
            self.tr(
                "voice_manager_description",
                "Browse the public rhasspy/piper-voices repository. "
                "Models are downloaded directly to the portable voices folder.",
            )
        )
        description.setWordWrap(True)
        description.setObjectName("helperLabel")
        layout.addWidget(title)
        layout.addWidget(description)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            self.tr("search_voices", "Search language or voice...")
        )
        self.search_edit.textChanged.connect(self._apply_filters)
        self.language_filter = QComboBox()
        self.language_filter.currentIndexChanged.connect(self._apply_filters)
        self.quality_filter = QComboBox()
        self.quality_filter.currentIndexChanged.connect(self._apply_filters)
        self.installed_filter = QComboBox()
        self.installed_filter.addItem(
            ui_icon("voice"),
            self.tr("all_voices", "All voices"),
            "all",
        )
        self.installed_filter.addItem(
            ui_icon("apply"),
            self.tr("installed_only", "Installed only"),
            "installed",
        )
        self.installed_filter.addItem(
            ui_icon("save"),
            self.tr("available_only", "Available only"),
            "available",
        )
        self.installed_filter.currentIndexChanged.connect(self._apply_filters)
        self.refresh_button = QPushButton(self.tr("refresh_catalog", "Refresh catalog"))
        self.refresh_button.setIcon(ui_icon("refresh"))
        self.refresh_button.clicked.connect(self._load_catalog)
        filters.addWidget(self.search_edit, 2)
        filters.addWidget(self.language_filter, 1)
        filters.addWidget(self.quality_filter, 1)
        filters.addWidget(self.installed_filter, 1)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("status", "Status"),
                self.tr("language", "Language"),
                self.tr("voice", "Voice"),
                self.tr("preview_voice", "Preview"),
                self.tr("quality", "Quality"),
                self.tr("download_size", "Size"),
            ]
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Stretch,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            5,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._primary_action)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel(self.tr("ready", "Ready"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        self.repository_button = QPushButton(
            self.tr("open_repository", "Open repository")
        )
        self.repository_button.setIcon(ui_icon("repository"))
        self.repository_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(HuggingFaceVoiceCatalog.REPOSITORY_URL)
            )
        )
        self.cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.cancel_button.setIcon(ui_icon("cancel"))
        self.cancel_button.clicked.connect(self._cancel_operation)
        self.preview_button = QPushButton(self.tr("preview_voice", "Preview"))
        self.preview_button.setIcon(ui_icon("preview"))
        self.preview_button.clicked.connect(self._preview_selected)
        self.remove_button = QPushButton(self.tr("remove_voice", "Remove"))
        self.remove_button.setIcon(ui_icon("delete"))
        self.remove_button.clicked.connect(self._remove_selected)
        self.install_button = QPushButton(self.tr("install_voice", "Install"))
        self.install_button.setIcon(ui_icon("save"))
        self.install_button.setObjectName("primaryButton")
        self.install_button.clicked.connect(self._install_selected)
        self.close_button = QPushButton(self.tr("close", "Close"))
        self.close_button.setIcon(ui_icon("close"))
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.repository_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.install_button)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

    def _load_catalog(self) -> None:
        if self.worker_thread is not None:
            return
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(
            self.tr("loading_voice_catalog", "Loading voice catalog...")
        )
        self._start_worker("catalog")

    def _install_selected(self) -> None:
        voice = self._selected_voice()
        if voice is None or self.worker_thread is not None:
            return
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText(
            self.tr(
                "installing_voice",
                "Installing {voice}...",
                voice=voice.display_name,
            )
        )
        self._start_worker("install", voice)

    def _remove_selected(self) -> None:
        voice = self._selected_voice()
        if (
            voice is None
            or self.worker_thread is not None
            or not self.catalog_service.is_installed(voice)
        ):
            return
        choice = QMessageBox.question(
            self,
            self.tr("remove_voice", "Remove voice"),
            self.tr(
                "remove_voice_confirm",
                "Remove {voice} from this computer?",
                voice=voice.display_name,
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText(
            self.tr(
                "removing_voice",
                "Removing {voice}...",
                voice=voice.display_name,
            )
        )
        self._start_worker("remove", voice)

    def _start_worker(
        self,
        operation: str,
        voice: RemoteVoice | None = None,
    ) -> None:
        catalog = HuggingFaceVoiceCatalog(self.voices_root)
        worker = VoiceCatalogWorker(catalog, operation, voice)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.catalog_ready.connect(self._on_catalog_ready)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_operation_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.catalog_ready.connect(thread.quit)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self.catalog_service = catalog
        self.worker = worker
        self.worker_thread = thread
        self._set_busy(True)
        thread.start()

    def _on_catalog_ready(self, voices: object) -> None:
        self.remote_voices = list(voices) if isinstance(voices, list) else []
        self._populate_filters()
        self._apply_filters()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr(
                "catalog_loaded",
                "Loaded {count} downloadable voice(s).",
                count=len(self.remote_voices),
            )
        )

    def _on_progress(self, current: int, total: int, message: str) -> None:
        translated_message = {
            "Downloading voice model...": self.tr(
                "downloading_voice_model",
                "Downloading voice model...",
            ),
            "Downloading voice configuration...": self.tr(
                "downloading_voice_config",
                "Downloading voice configuration...",
            ),
            "Voice installed.": self.tr("voice_installed", "Voice installed."),
        }.get(message, message)
        if message.startswith("Loading voice catalog page "):
            page = "".join(character for character in message if character.isdigit())
            translated_message = self.tr(
                "loading_catalog_page",
                "Loading voice catalog page {page}...",
                page=page,
            )
        self.status_label.setText(translated_message)
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(min(100, int(current / total * 100)))

    def _on_operation_finished(self, voice_name: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr(
                "voice_operation_complete",
                "Voice updated: {voice}",
                voice=voice_name,
            )
        )
        self.voices_changed.emit()
        self._apply_filters()

    def _on_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText(self.tr("voice_operation_failed", "Voice operation failed"))
        QMessageBox.critical(
            self,
            self.tr("voice_operation_failed", "Voice operation failed"),
            message,
        )

    def _on_cancelled(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText(
            self.tr("voice_download_cancelled", "Voice download cancelled")
        )

    def _clear_worker(self) -> None:
        self.worker = None
        self.worker_thread = None
        self._set_busy(False)
        self._apply_filters()

    def _cancel_operation(self) -> None:
        if self.worker is not None:
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self.tr("cancelling", "Cancelling..."))
            self.worker.request_cancel()

    def _selection_changed(self) -> None:
        if self.sample_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.sample_player.stop()
        self._update_actions()

    def _preview_selected(self) -> None:
        voice = self._selected_voice()
        if voice is None or not voice.has_sample:
            return
        if (
            self.sample_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            self.sample_player.stop()
            return
        sample_url = self.catalog_service.sample_url(voice)
        self.sample_player.setSource(QUrl(sample_url))
        self.status_label.setText(
            self.tr(
                "loading_voice_sample",
                "Loading sample for {voice}...",
                voice=voice.display_name,
            )
        )
        self.sample_player.play()

    def _preview_row(self, row: int) -> None:
        if not 0 <= row < len(self.visible_voices):
            return
        self.table.selectRow(row)
        self._preview_selected()

    def _on_sample_playback_state(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.preview_button.setText(self.tr("stop_preview", "Stop preview"))
            voice = self._selected_voice()
            if voice is not None:
                self.status_label.setText(
                    self.tr(
                        "playing_voice_sample",
                        "Playing sample: {voice}",
                        voice=voice.display_name,
                    )
                )
        else:
            self.preview_button.setText(self.tr("preview_voice", "Preview"))

    def _on_sample_error(
        self,
        error: QMediaPlayer.Error,
        message: str,
    ) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.status_label.setText(
            self.tr(
                "sample_playback_failed",
                "Could not play the voice sample: {message}",
                message=message or str(error),
            )
        )

    def _populate_filters(self) -> None:
        current_language = self.language_filter.currentData()
        current_quality = self.quality_filter.currentData()
        self.language_filter.blockSignals(True)
        self.quality_filter.blockSignals(True)
        self.language_filter.clear()
        self.quality_filter.clear()
        self.language_filter.addItem(
            ui_icon("language"),
            self.tr("all_languages", "All languages"),
            "",
        )
        self.quality_filter.addItem(
            ui_icon("settings"),
            self.tr("all_qualities", "All qualities"),
            "",
        )
        for language in sorted({voice.language for voice in self.remote_voices}):
            self.language_filter.addItem(ui_icon("language"), language, language)
        for quality in sorted({voice.quality for voice in self.remote_voices}):
            self.quality_filter.addItem(
                ui_icon("settings"),
                quality.title(),
                quality,
            )
        self.language_filter.blockSignals(False)
        self.quality_filter.blockSignals(False)
        self._select_combo_data(self.language_filter, current_language)
        self._select_combo_data(self.quality_filter, current_quality)

    def _apply_filters(self) -> None:
        selected_voice = self._selected_voice()
        selected_id = selected_voice.voice_id if selected_voice else ""
        search = self.search_edit.text().strip().casefold()
        language = self.language_filter.currentData() or ""
        quality = self.quality_filter.currentData() or ""
        installed_mode = self.installed_filter.currentData() or "all"
        visible: list[RemoteVoice] = []
        for voice in self.remote_voices:
            installed = self.catalog_service.is_installed(voice)
            searchable = f"{voice.language} {voice.speaker} {voice.quality}".casefold()
            if search and search not in searchable:
                continue
            if language and voice.language != language:
                continue
            if quality and voice.quality != quality:
                continue
            if installed_mode == "installed" and not installed:
                continue
            if installed_mode == "available" and installed:
                continue
            visible.append(voice)
        self.visible_voices = visible
        self._populate_table(selected_id)

    def _populate_table(self, selected_id: str = "") -> None:
        self.table.setRowCount(len(self.visible_voices))
        selected_row = -1
        for row, voice in enumerate(self.visible_voices):
            installed = self.catalog_service.is_installed(voice)
            values = (
                self.tr("installed", "Installed")
                if installed
                else self.tr("available", "Available"),
                voice.language,
                voice.speaker,
                voice.quality.title(),
                format_bytes(voice.total_size),
            )
            for column, value in enumerate(values):
                table_column = column if column < 3 else column + 1
                item = QTableWidgetItem(value)
                if table_column in {0, 5}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, table_column, item)
            preview_button = QPushButton()
            preview_button.setIcon(ui_icon("preview"))
            preview_button.setToolTip(self.tr("preview_voice", "Preview"))
            preview_button.setEnabled(voice.has_sample)
            preview_button.clicked.connect(
                lambda checked=False, row=row: self._preview_row(row)
            )
            self.table.setCellWidget(row, 3, preview_button)
            if voice.voice_id == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self._update_actions()

    def _selected_voice(self) -> RemoteVoice | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.visible_voices):
            return self.visible_voices[row]
        return None

    def _primary_action(self) -> None:
        voice = self._selected_voice()
        if voice is None:
            return
        if self.catalog_service.is_installed(voice):
            self._remove_selected()
        else:
            self._install_selected()

    def _update_actions(self) -> None:
        voice = self._selected_voice()
        installed = (
            self.catalog_service.is_installed(voice)
            if voice is not None
            else False
        )
        idle = self.worker_thread is None
        self.install_button.setEnabled(idle and voice is not None and not installed)
        self.remove_button.setEnabled(idle and voice is not None and installed)
        self.preview_button.setEnabled(
            idle and voice is not None and voice.has_sample
        )

    def _set_busy(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)
        self.close_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.search_edit.setEnabled(not busy)
        self.language_filter.setEnabled(not busy)
        self.quality_filter.setEnabled(not busy)
        self.installed_filter.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.preview_button.setEnabled(False if busy else self.preview_button.isEnabled())
        if busy:
            self.install_button.setEnabled(False)
            self.remove_button.setEnabled(False)
        else:
            self._update_actions()

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.sample_player.stop()
        if self.worker is not None:
            self.worker.request_cancel()
            event.ignore()
            return
        event.accept()

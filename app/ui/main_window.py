from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.audio_pipeline import AudioGenerationOptions
from app.core.project_manager import DocumentImportError, ProjectManager
from app.core.settings_manager import SettingsManager
from app.tts.engine_registry import TTS_ENGINES
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.kokoro_manager import KokoroManager
from app.tts.kokoro_preview import kokoro_preview_text_for_language
from app.tts.voice_manager import VoiceInfo, VoiceManager
from app.utils.i18n import Translator
from app.utils.paths import application_root, resolve_app_path, resource_root
from app.workers.chatterbox_worker import (
    ChatterboxInstallWorker,
    ChatterboxPreviewWorker,
)
from app.workers.generation_worker import GenerationWorker
from app.workers.kokoro_worker import KokoroInstallWorker, KokoroPreviewWorker

from .icons import ui_icon
from .voice_manager_dialog import VoiceManagerDialog
from .widgets import FilePicker, LogView, PathPicker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.settings
        self.translator = Translator(str(self.settings.get("ui_language", "en")))
        self.kokoro_manager = KokoroManager()
        self.chatterbox_manager = ChatterboxManager()
        self.voices: list[VoiceInfo] = []
        self.worker: GenerationWorker | None = None
        self.worker_thread: QThread | None = None
        self.kokoro_worker: KokoroInstallWorker | None = None
        self.kokoro_thread: QThread | None = None
        self.kokoro_preview_worker: KokoroPreviewWorker | None = None
        self.kokoro_preview_thread: QThread | None = None
        self.chatterbox_worker: ChatterboxInstallWorker | None = None
        self.chatterbox_thread: QThread | None = None
        self.chatterbox_preview_worker: ChatterboxPreviewWorker | None = None
        self.chatterbox_preview_thread: QThread | None = None
        self.generation_started_at: float | None = None
        self.progress_current = 0
        self.progress_total = 0
        self.last_output_folder: Path | None = None
        self.generation_timer = QTimer(self)
        self.generation_timer.setInterval(1000)
        self.generation_timer.timeout.connect(self._update_generation_time)
        self.kokoro_audio_output = QAudioOutput(self)
        self.kokoro_sample_player = QMediaPlayer(self)
        self.kokoro_sample_player.setAudioOutput(self.kokoro_audio_output)
        self.kokoro_sample_player.playbackStateChanged.connect(
            self._on_kokoro_playback_state_changed
        )
        self.chatterbox_audio_output = QAudioOutput(self)
        self.chatterbox_sample_player = QMediaPlayer(self)
        self.chatterbox_sample_player.setAudioOutput(self.chatterbox_audio_output)
        self.chatterbox_sample_player.playbackStateChanged.connect(
            self._on_chatterbox_playback_state_changed
        )

        self.setWindowTitle(self.tr("app_title", "LocalText2Voice"))
        logo_path = resource_root() / "assets" / "logotipo.png"
        self.setWindowIcon(QIcon(str(logo_path)))
        self.setMinimumSize(960, 720)
        self.resize(1120, 820)
        self._build_ui()
        self._apply_style()
        self._load_voices()
        self._restore_settings()
        self._set_running(False)

    def tr(self, key: str, default: str | None = None, **values: object) -> str:
        return self.translator.text(key, default, **values)

    def _build_ui(self) -> None:
        central_widget = QWidget()
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        brand_widget = QWidget()
        brand_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)
        logo_label = QLabel()
        logo_label.setObjectName("logoLabel")
        logo_label.setFixedSize(64, 64)
        logo_label.setPixmap(
            QPixmap(str(resource_root() / "assets" / "logotipo.png")).scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title = QLabel(self.tr("app_title", "LocalText2Voice"))
        title.setObjectName("titleLabel")
        subtitle = QLabel(
            self.tr(
                "app_subtitle",
                "Turn long-form text into offline MP3 courses with Piper.",
            )
        )
        subtitle.setObjectName("subtitleLabel")
        author_credit = QLabel(
            'By Esteban, <a href="https://andromedanova.com">'
            "AndromedaNova.com</a>"
        )
        author_credit.setObjectName("authorCreditLabel")
        author_credit.setOpenExternalLinks(True)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_layout.addWidget(author_credit)
        brand_layout.addWidget(logo_label)
        brand_layout.addLayout(title_layout, 1)
        header_layout.addWidget(brand_widget, 1)

        self.ui_language_combo = QComboBox()
        self.ui_language_combo.setToolTip(
            self.tr("interface_language", "Interface language")
        )
        for locale in Translator.available_languages():
            self.ui_language_combo.addItem(
                ui_icon("language"),
                locale.name,
                locale.code,
            )
        self.ui_language_combo.currentIndexChanged.connect(
            self._change_ui_language
        )
        self.settings_button = QPushButton(self.tr("settings", "Settings"))
        self.settings_button.setIcon(ui_icon("settings"))
        self.settings_button.clicked.connect(self._toggle_settings)
        header_layout.addWidget(self.ui_language_combo)
        header_layout.addWidget(self.settings_button)
        root_layout.addLayout(header_layout)

        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self._build_generation_page())
        self.page_stack.addWidget(self._build_settings_page())
        root_layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(central_widget)
        self._apply_language_direction()

    def _apply_language_direction(self) -> None:
        direction = (
            Qt.LayoutDirection.RightToLeft
            if self.translator.direction == "rtl"
            else Qt.LayoutDirection.LeftToRight
        )
        self.setLayoutDirection(direction)

    def _build_generation_page(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(14)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_editor_panel())

        lower_panel = QWidget()
        lower_layout = QHBoxLayout(lower_panel)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(14)
        lower_layout.addWidget(self._build_voice_panel(), 1)
        lower_layout.addWidget(self._build_log_panel(), 2)
        splitter.addWidget(lower_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root_layout.addWidget(splitter, 1)

        status_frame = QFrame()
        status_frame.setObjectName("card")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(8)

        self.status_label = QLabel(self.tr("ready", "Ready"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        self.time_label = QLabel()
        self.time_label.setObjectName("helperLabel")
        self.time_label.setVisible(False)
        status_layout.addWidget(self.time_label)

        button_layout = QHBoxLayout()
        self.open_output_button = QPushButton(
            self.tr("open_output_folder", "Open output folder")
        )
        self.open_output_button.setIcon(ui_icon("folder"))
        self.open_output_button.clicked.connect(self._open_last_output_folder)
        self.open_output_button.setVisible(False)
        button_layout.addWidget(self.open_output_button)
        button_layout.addStretch(1)
        self.cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.cancel_button.setIcon(ui_icon("cancel"))
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.generate_button = QPushButton(
            self.tr("generate_audio", "Generate Audio")
        )
        self.generate_button.setIcon(ui_icon("generate"))
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self._start_generation)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.generate_button)
        status_layout.addLayout(button_layout)
        root_layout.addWidget(status_frame)
        return widget

    def _build_editor_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header = QLabel(self.tr("source_text", "Source text"))
        header.setObjectName("sectionLabel")
        header_layout.addWidget(header)
        header_layout.addStretch(1)

        self.import_button = QPushButton(self.tr("import_file", "Import file"))
        self.import_button.setIcon(ui_icon("file"))
        self.import_button.clicked.connect(self._import_document)
        header_layout.addWidget(self.import_button)
        layout.addLayout(header_layout)

        self.text_editor = QTextEdit()
        self.text_editor.setAcceptRichText(False)
        self.text_editor.setPlaceholderText(
            self.tr(
                "text_placeholder",
                "Paste a chapter, lesson, article, or complete course here...",
            )
        )
        layout.addWidget(self.text_editor, 1)
        return frame

    def _build_voice_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel(self.tr("voice_selection", "Voice selection"))
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        self.language_combo = QComboBox()
        self.language_combo.currentIndexChanged.connect(self._filter_voices)

        voice_row = QWidget()
        voice_layout = QHBoxLayout(voice_row)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(8)
        self.voice_combo = QComboBox()
        self.voice_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.voice_combo.setMinimumContentsLength(24)
        self.manage_voices_button = QPushButton(
            self.tr("manage_voices", "Manage voices")
        )
        self.manage_voices_button.setIcon(ui_icon("voice"))
        self.manage_voices_button.clicked.connect(self._open_voice_manager)
        voice_layout.addWidget(self.voice_combo, 1)
        voice_layout.addWidget(self.manage_voices_button)

        form.addRow(self.tr("language", "Language"), self.language_combo)
        form.addRow(self.tr("voice", "Voice"), voice_row)
        layout.addLayout(form)

        self.voice_help_label = QLabel(
            self.tr(
                "voice_help",
                "Voices are discovered from voices/**/*.onnx when the matching "
                ".onnx.json file is present.",
            )
        )
        self.voice_help_label.setWordWrap(True)
        self.voice_help_label.setObjectName("helperLabel")
        layout.addWidget(self.voice_help_label)
        layout.addStretch(1)
        return frame

    def _build_settings_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        self.back_button = QPushButton(
            self.tr("back_to_generation", "Back to generation")
        )
        self.back_button.setIcon(ui_icon("back"))
        self.back_button.clicked.connect(self._show_generation)
        title = QLabel(self.tr("settings", "Settings"))
        title.setObjectName("sectionLabel")
        header_layout.addWidget(self.back_button)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.addTab(
            self._build_general_settings(),
            ui_icon("settings"),
            self.tr("general_settings", "General"),
        )
        self.settings_tabs.addTab(
            self._build_advanced_settings(),
            ui_icon("settings"),
            self.tr("advanced_settings", "Advanced"),
        )
        layout.addWidget(self.settings_tabs, 1)
        return widget

    def _toggle_settings(self) -> None:
        if self.page_stack.currentIndex() == 0:
            self.page_stack.setCurrentIndex(1)
            self.settings_button.setText(
                self.tr("back_to_generation", "Back to generation")
            )
            self.settings_button.setIcon(ui_icon("back"))
        else:
            self._show_generation()

    def _show_generation(self) -> None:
        self.page_stack.setCurrentIndex(0)
        self.settings_button.setText(self.tr("settings", "Settings"))
        self.settings_button.setIcon(ui_icon("settings"))

    def _build_tts_engine_settings(self) -> QGroupBox:
        group = QGroupBox(
            self.tr("voice_generation_engine", "Voice Generation Engine")
        )
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self.tts_engine_combo = QComboBox()
        for engine in TTS_ENGINES:
            self.tts_engine_combo.addItem(
                ui_icon("voice"),
                self._tts_engine_label(engine.engine_id),
                engine.engine_id,
            )
        form.addRow(self.tr("tts_engine", "Generation engine"), self.tts_engine_combo)
        layout.addLayout(form)

        self.engine_settings_stack = QStackedWidget()
        self.engine_stack_indexes: dict[str, int] = {}
        for engine_id, panel in (
            ("piper", self._build_piper_engine_panel()),
            ("kokoro", self._build_kokoro_engine_panel()),
            ("chatterbox", self._build_chatterbox_engine_panel()),
            ("openai", self._build_openai_engine_panel()),
            ("elevenlabs", self._build_elevenlabs_engine_panel()),
            ("azure", self._build_azure_engine_panel()),
        ):
            self.engine_stack_indexes[engine_id] = self.engine_settings_stack.addWidget(
                panel
            )
        layout.addWidget(self.engine_settings_stack)

        note = QLabel(
            self.tr(
                "api_key_local_note",
                "API keys are stored locally in config.json. Leave API engines "
                "empty unless you want to use that provider.",
            )
        )
        note.setWordWrap(True)
        note.setObjectName("helperLabel")
        layout.addWidget(note)
        self.tts_engine_combo.currentIndexChanged.connect(
            self._on_tts_engine_changed
        )
        return group

    def _build_piper_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.piper_path_edit = QLineEdit()
        self.piper_path_edit.setPlaceholderText("engines/piper/piper.exe")
        form.addRow(
            self.tr("piper_executable", "Piper executable"),
            self.piper_path_edit,
        )
        helper = QLabel(
            self.tr(
                "piper_engine_help",
                "Free and offline. Uses local .onnx voices from the voices folder.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        form.addRow("", helper)
        return panel

    def _build_kokoro_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.kokoro_status_label = QLabel()
        self.kokoro_status_label.setObjectName("helperLabel")
        self.kokoro_path_label = QLabel()
        self.kokoro_path_label.setObjectName("helperLabel")
        self.kokoro_path_label.setWordWrap(True)
        self.kokoro_runtime_label = QLabel()
        self.kokoro_runtime_label.setObjectName("helperLabel")
        self.kokoro_runtime_label.setWordWrap(True)
        layout.addWidget(self.kokoro_status_label)
        layout.addWidget(self.kokoro_path_label)
        layout.addWidget(self.kokoro_runtime_label)

        self.kokoro_progress_bar = QProgressBar()
        self.kokoro_progress_bar.setRange(0, 100)
        self.kokoro_progress_bar.setValue(0)
        self.kokoro_progress_bar.setVisible(False)
        layout.addWidget(self.kokoro_progress_bar)

        form = QFormLayout()
        form.setSpacing(10)
        self.kokoro_voice_combo = QComboBox()
        for voice in self.kokoro_manager.list_voices():
            self.kokoro_voice_combo.addItem(voice.display_name, voice.voice_id)
        self.kokoro_provider_combo = QComboBox()
        self.kokoro_provider_combo.addItem("CPU", "cpu")
        self.kokoro_provider_combo.addItem("Auto (future)", "auto")
        self.kokoro_provider_combo.addItem("CUDA (future)", "cuda")
        self.kokoro_provider_combo.addItem("DirectML (future)", "directml")
        form.addRow(self.tr("kokoro_voice", "Kokoro voice"), self.kokoro_voice_combo)
        form.addRow(
            self.tr("kokoro_provider", "Backend provider"),
            self.kokoro_provider_combo,
        )
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.kokoro_install_button = QPushButton(self.tr("install", "Install"))
        self.kokoro_install_button.setIcon(ui_icon("apply"))
        self.kokoro_install_button.clicked.connect(self._install_kokoro)
        self.kokoro_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.kokoro_remove_button.setIcon(ui_icon("delete"))
        self.kokoro_remove_button.clicked.connect(self._remove_kokoro)
        self.kokoro_test_button = QPushButton(
            self.tr("test_voice", "Test voice")
        )
        self.kokoro_test_button.setIcon(ui_icon("preview"))
        self.kokoro_test_button.clicked.connect(self._test_kokoro_voice)
        self.kokoro_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.kokoro_cancel_button.setIcon(ui_icon("cancel"))
        self.kokoro_cancel_button.setObjectName("secondaryButton")
        self.kokoro_cancel_button.clicked.connect(self._cancel_kokoro_operation)
        actions.addWidget(self.kokoro_install_button)
        actions.addWidget(self.kokoro_remove_button)
        actions.addWidget(self.kokoro_test_button)
        actions.addWidget(self.kokoro_cancel_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.kokoro_preview_frame = QFrame()
        self.kokoro_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.kokoro_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.kokoro_preview_status_label = QLabel()
        self.kokoro_preview_status_label.setObjectName("helperLabel")
        self.kokoro_preview_bar = QProgressBar()
        self.kokoro_preview_bar.setRange(0, 0)
        self.kokoro_preview_bar.setTextVisible(False)
        self.kokoro_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.kokoro_preview_status_label, 1)
        preview_layout.addWidget(self.kokoro_preview_bar)
        self.kokoro_preview_frame.setVisible(False)
        layout.addWidget(self.kokoro_preview_frame)

        helper = QLabel(
            self.tr(
                "kokoro_help",
                "Optional local engine. Models are downloaded to your user "
                "data folder, not bundled with the main app.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addWidget(helper)
        self._refresh_kokoro_status()
        return panel

    def _refresh_kokoro_status(self) -> None:
        if not hasattr(self, "kokoro_status_label"):
            return
        installed = self.kokoro_manager.is_installed()
        runtime_ready = self.kokoro_manager.has_runtime()
        operation_running = self.kokoro_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if installed
            else self.tr("not_installed", "Not installed")
        )
        self.kokoro_status_label.setText(
            self.tr("kokoro_status", "Kokoro status: {status}", status=status_text)
        )
        self.kokoro_path_label.setText(
            self.tr(
                "kokoro_model_path",
                "Model path: {path}",
                path=str(self.kokoro_manager.install_dir),
            )
        )
        runtime_status = (
            self.tr("installed", "Installed")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        self.kokoro_runtime_label.setText(
            self.tr(
                "kokoro_runtime_status",
                "Runtime: {status} ({path})",
                status=runtime_status,
                path=str(self.kokoro_manager.runtime_path),
            )
        )
        self.kokoro_install_button.setEnabled(not installed and not operation_running)
        self.kokoro_remove_button.setEnabled(installed and not operation_running)
        self.kokoro_test_button.setEnabled(
            installed
            and runtime_ready
            and self.kokoro_preview_thread is None
            and not operation_running
        )
        self.kokoro_cancel_button.setVisible(operation_running)
        self.kokoro_cancel_button.setEnabled(operation_running)

    def _install_kokoro(self) -> None:
        self._start_kokoro_operation("install")

    def _remove_kokoro(self) -> None:
        self._start_kokoro_operation("remove")

    def _cancel_kokoro_operation(self) -> None:
        if self.kokoro_worker is None:
            return
        self.kokoro_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.kokoro_worker.request_cancel()

    def _start_kokoro_operation(self, operation: str) -> None:
        if self.kokoro_thread is not None:
            return
        self.kokoro_progress_bar.setVisible(True)
        self.kokoro_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "kokoro_installing",
                "Kokoro operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = KokoroInstallWorker(KokoroManager(), operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_kokoro_progress)
        worker.finished.connect(self._on_kokoro_finished)
        worker.failed.connect(self._on_kokoro_failed)
        worker.cancelled.connect(self._on_kokoro_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_kokoro_worker)
        self.kokoro_thread = thread
        self.kokoro_worker = worker
        self._refresh_kokoro_status()
        thread.start()

    def _on_kokoro_progress(self, current: int, total: int, message: str) -> None:
        percentage = int((current / total) * 100) if total else 0
        self.kokoro_progress_bar.setValue(max(0, min(100, percentage)))
        self.kokoro_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_kokoro_finished(self, path: str) -> None:
        self.kokoro_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("kokoro_ready", "Kokoro ready: {path}", path=path)
        )

    def _on_kokoro_failed(self, message: str) -> None:
        self.kokoro_progress_bar.setVisible(False)
        if (
            hasattr(self, "kokoro_preview_frame")
            and self.kokoro_preview_thread is not None
        ):
            self._hide_kokoro_preview_status()
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_kokoro_cancelled(self) -> None:
        self.kokoro_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("kokoro_cancelled", "Kokoro installation cancelled.")
        )

    def _clear_kokoro_worker(self) -> None:
        self.kokoro_worker = None
        self.kokoro_thread = None
        self.kokoro_progress_bar.setVisible(False)
        self.kokoro_manager = KokoroManager()
        self._refresh_kokoro_status()

    def _test_kokoro_voice(self) -> None:
        if self.kokoro_preview_thread is not None:
            return
        if not self.kokoro_manager.is_installed():
            self._show_error(
                self.tr("missing_voice", "No voice selected"),
                self.tr("kokoro_not_installed", "Kokoro is not installed yet."),
            )
            return
        if not self.kokoro_manager.has_runtime():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "kokoro_runtime_missing",
                    "kokoro_engine.exe is missing. Build it with "
                    "build_kokoro_engine.bat and place it in engines/kokoro/.",
                ),
            )
            return
        voice_id = str(self.kokoro_voice_combo.currentData() or "af_heart")
        lang = self._kokoro_language_for_voice(voice_id)
        thread = QThread(self)
        worker = KokoroPreviewWorker(
            KokoroManager(),
            voice_id,
            lang,
            self.speed_spin.value(),
            kokoro_preview_text_for_language(lang),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_kokoro_preview_ready)
        worker.failed.connect(self._on_kokoro_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_kokoro_preview_worker)
        self.kokoro_preview_thread = thread
        self.kokoro_preview_worker = worker
        self._refresh_kokoro_status()
        self._show_kokoro_preview_status(
            self.tr("kokoro_preview_generating", "Generating Kokoro preview..."),
            busy=True,
        )
        self.log_view.append_event(
            self.tr("kokoro_preview_generating", "Generating Kokoro preview...")
        )
        thread.start()

    def _on_kokoro_preview_ready(self, path: str) -> None:
        self._show_kokoro_preview_status(
            self.tr("kokoro_preview_ready", "Playing Kokoro preview."),
            busy=False,
        )
        self.kokoro_sample_player.setSource(QUrl.fromLocalFile(path))
        self.kokoro_sample_player.play()
        self.log_view.append_event(
            self.tr("kokoro_preview_ready", "Playing Kokoro preview.")
        )

    def _show_kokoro_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "kokoro_preview_frame"):
            return
        self.kokoro_preview_status_label.setText(message)
        self.kokoro_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.kokoro_preview_bar.setValue(100)
        self.kokoro_preview_frame.setVisible(True)

    def _hide_kokoro_preview_status(self) -> None:
        if not hasattr(self, "kokoro_preview_frame"):
            return
        self.kokoro_preview_frame.setVisible(False)

    def _on_kokoro_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "kokoro_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_kokoro_preview_status(
                self.tr("kokoro_preview_ready", "Playing Kokoro preview."),
                busy=False,
            )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.kokoro_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_kokoro_preview_status)

    def _clear_kokoro_preview_worker(self) -> None:
        self.kokoro_preview_worker = None
        self.kokoro_preview_thread = None
        if (
            hasattr(self, "kokoro_preview_frame")
            and self.kokoro_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_kokoro_preview_status)
        self._refresh_kokoro_status()

    def _kokoro_language_for_voice(self, voice_id: str) -> str:
        voice = next(
            (
                candidate
                for candidate in self.kokoro_manager.list_voices()
                if candidate.voice_id == voice_id
            ),
            None,
        )
        return voice.language if voice is not None else "en-us"

    def _build_chatterbox_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.chatterbox_status_label = QLabel()
        self.chatterbox_status_label.setObjectName("helperLabel")
        self.chatterbox_path_label = QLabel()
        self.chatterbox_path_label.setObjectName("helperLabel")
        self.chatterbox_path_label.setWordWrap(True)
        self.chatterbox_runtime_label = QLabel()
        self.chatterbox_runtime_label.setObjectName("helperLabel")
        self.chatterbox_runtime_label.setWordWrap(True)
        layout.addWidget(self.chatterbox_status_label)
        layout.addWidget(self.chatterbox_path_label)
        layout.addWidget(self.chatterbox_runtime_label)

        self.chatterbox_progress_bar = QProgressBar()
        self.chatterbox_progress_bar.setRange(0, 100)
        self.chatterbox_progress_bar.setValue(0)
        self.chatterbox_progress_bar.setVisible(False)
        layout.addWidget(self.chatterbox_progress_bar)

        form = QFormLayout()
        form.setSpacing(10)
        self.chatterbox_model_combo = QComboBox()
        for model in self.chatterbox_manager.list_models():
            self.chatterbox_model_combo.addItem(model.display_name, model.model_id)
        self.chatterbox_language_combo = QComboBox()
        for language in self.chatterbox_manager.list_languages():
            self.chatterbox_language_combo.addItem(
                language.display_name,
                language.language_id,
            )
        self.chatterbox_device_combo = QComboBox()
        self.chatterbox_device_combo.addItem("CUDA / NVIDIA GPU", "cuda")
        self.chatterbox_device_combo.addItem("Auto", "auto")
        self.chatterbox_device_combo.addItem("CPU fallback", "cpu")
        self.chatterbox_device_combo.addItem("Apple MPS", "mps")
        self.chatterbox_reference_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr(
                "audio_files_filter",
                "Audio files (*.wav *.mp3 *.flac *.m4a);;All files (*.*)",
            ),
        )
        self.chatterbox_consent_checkbox = QCheckBox(
            self.tr(
                "voice_clone_consent",
                "I have permission to use this reference voice.",
            )
        )
        self.chatterbox_exaggeration_spin = self._ratio_spin(0.5)
        self.chatterbox_cfg_spin = self._ratio_spin(0.5)
        form.addRow(
            self.tr("chatterbox_model", "Chatterbox model"),
            self.chatterbox_model_combo,
        )
        form.addRow(
            self.tr("chatterbox_language", "Language"),
            self.chatterbox_language_combo,
        )
        form.addRow(
            self.tr("chatterbox_device", "GPU device"),
            self.chatterbox_device_combo,
        )
        form.addRow(
            self.tr("reference_audio", "Reference audio"),
            self.chatterbox_reference_picker,
        )
        form.addRow("", self.chatterbox_consent_checkbox)
        form.addRow(
            self.tr("exaggeration", "Emotion exaggeration"),
            self.chatterbox_exaggeration_spin,
        )
        form.addRow(self.tr("cfg_weight", "CFG weight"), self.chatterbox_cfg_spin)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.chatterbox_install_button = QPushButton(self.tr("install", "Install"))
        self.chatterbox_install_button.setIcon(ui_icon("apply"))
        self.chatterbox_install_button.clicked.connect(self._install_chatterbox)
        self.chatterbox_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.chatterbox_remove_button.setIcon(ui_icon("delete"))
        self.chatterbox_remove_button.clicked.connect(self._remove_chatterbox)
        self.chatterbox_test_button = QPushButton(self.tr("test_voice", "Test voice"))
        self.chatterbox_test_button.setIcon(ui_icon("preview"))
        self.chatterbox_test_button.clicked.connect(self._test_chatterbox_voice)
        self.chatterbox_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.chatterbox_cancel_button.setIcon(ui_icon("cancel"))
        self.chatterbox_cancel_button.setObjectName("secondaryButton")
        self.chatterbox_cancel_button.clicked.connect(
            self._cancel_chatterbox_operation
        )
        actions.addWidget(self.chatterbox_install_button)
        actions.addWidget(self.chatterbox_remove_button)
        actions.addWidget(self.chatterbox_test_button)
        actions.addWidget(self.chatterbox_cancel_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.chatterbox_preview_frame = QFrame()
        self.chatterbox_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.chatterbox_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.chatterbox_preview_status_label = QLabel()
        self.chatterbox_preview_status_label.setObjectName("helperLabel")
        self.chatterbox_preview_bar = QProgressBar()
        self.chatterbox_preview_bar.setRange(0, 0)
        self.chatterbox_preview_bar.setTextVisible(False)
        self.chatterbox_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.chatterbox_preview_status_label, 1)
        preview_layout.addWidget(self.chatterbox_preview_bar)
        self.chatterbox_preview_frame.setVisible(False)
        layout.addWidget(self.chatterbox_preview_frame)

        helper = QLabel(
            self.tr(
                "chatterbox_help",
                "Advanced local GPU engine. Build or install the separate "
                "Chatterbox runtime, then download model assets on demand. "
                "CUDA/NVIDIA is recommended.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addWidget(helper)
        self._refresh_chatterbox_status()
        return panel

    def _refresh_chatterbox_status(self) -> None:
        if not hasattr(self, "chatterbox_status_label"):
            return
        installed = self.chatterbox_manager.is_installed()
        runtime_ready = self.chatterbox_manager.has_runtime()
        operation_running = self.chatterbox_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if installed
            else self.tr("not_installed", "Not installed")
        )
        runtime_status = (
            self.tr("installed", "Installed")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        self.chatterbox_status_label.setText(
            self.tr(
                "chatterbox_status",
                "Chatterbox status: {status}",
                status=status_text,
            )
        )
        self.chatterbox_path_label.setText(
            self.tr(
                "chatterbox_model_path",
                "Model cache: {path}",
                path=str(self.chatterbox_manager.cache_dir),
            )
        )
        self.chatterbox_runtime_label.setText(
            self.tr(
                "chatterbox_runtime_status",
                "Runtime: {status} ({path})",
                status=runtime_status,
                path=str(self.chatterbox_manager.runtime_path),
            )
        )
        self.chatterbox_install_button.setEnabled(not operation_running)
        self.chatterbox_remove_button.setEnabled(
            installed and not operation_running
        )
        self.chatterbox_test_button.setEnabled(
            runtime_ready
            and self.chatterbox_preview_thread is None
            and not operation_running
        )
        self.chatterbox_cancel_button.setVisible(operation_running)
        self.chatterbox_cancel_button.setEnabled(operation_running)

    def _install_chatterbox(self) -> None:
        self._start_chatterbox_operation("install")

    def _remove_chatterbox(self) -> None:
        self._start_chatterbox_operation("remove")

    def _cancel_chatterbox_operation(self) -> None:
        if self.chatterbox_worker is None:
            return
        self.chatterbox_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.chatterbox_worker.request_cancel()

    def _start_chatterbox_operation(self, operation: str) -> None:
        if self.chatterbox_thread is not None:
            return
        self.chatterbox_progress_bar.setVisible(True)
        self.chatterbox_progress_bar.setRange(0, 0 if operation == "install" else 100)
        self.chatterbox_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "chatterbox_installing",
                "Chatterbox operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = ChatterboxInstallWorker(
            ChatterboxManager(),
            operation,
            str(self.chatterbox_model_combo.currentData() or "multilingual_v3"),
            str(self.chatterbox_device_combo.currentData() or "cuda"),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_chatterbox_progress)
        worker.finished.connect(self._on_chatterbox_finished)
        worker.failed.connect(self._on_chatterbox_failed)
        worker.cancelled.connect(self._on_chatterbox_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_worker)
        self.chatterbox_thread = thread
        self.chatterbox_worker = worker
        self._refresh_chatterbox_status()
        thread.start()

    def _on_chatterbox_progress(self, current: int, total: int, message: str) -> None:
        if total:
            self.chatterbox_progress_bar.setRange(0, 100)
            percentage = int((current / total) * 100)
            self.chatterbox_progress_bar.setValue(max(0, min(100, percentage)))
        self.chatterbox_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_chatterbox_finished(self, path: str) -> None:
        self.chatterbox_progress_bar.setRange(0, 100)
        self.chatterbox_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("chatterbox_ready", "Chatterbox ready: {path}", path=path)
        )

    def _on_chatterbox_failed(self, message: str) -> None:
        self.chatterbox_progress_bar.setVisible(False)
        if (
            hasattr(self, "chatterbox_preview_frame")
            and self.chatterbox_preview_thread is not None
        ):
            self._hide_chatterbox_preview_status()
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_chatterbox_cancelled(self) -> None:
        self.chatterbox_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("chatterbox_cancelled", "Chatterbox operation cancelled.")
        )

    def _clear_chatterbox_worker(self) -> None:
        self.chatterbox_worker = None
        self.chatterbox_thread = None
        self.chatterbox_progress_bar.setVisible(False)
        self.chatterbox_manager = ChatterboxManager()
        self._refresh_chatterbox_status()

    def _test_chatterbox_voice(self) -> None:
        if self.chatterbox_preview_thread is not None:
            return
        voice_config = self._chatterbox_voice_config_for_ui()
        if voice_config is None:
            return
        thread = QThread(self)
        worker = ChatterboxPreviewWorker(
            ChatterboxManager(),
            voice_config,
            self._chatterbox_preview_text(
                str(voice_config.get("language", "en"))
            ),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_chatterbox_preview_ready)
        worker.failed.connect(self._on_chatterbox_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_preview_worker)
        self.chatterbox_preview_thread = thread
        self.chatterbox_preview_worker = worker
        self._refresh_chatterbox_status()
        self._show_chatterbox_preview_status(
            self.tr(
                "chatterbox_preview_generating",
                "Generating Chatterbox preview...",
            ),
            busy=True,
        )
        self.log_view.append_event(
            self.tr(
                "chatterbox_preview_generating",
                "Generating Chatterbox preview...",
            )
        )
        thread.start()

    def _on_chatterbox_preview_ready(self, path: str) -> None:
        self._show_chatterbox_preview_status(
            self.tr("chatterbox_preview_ready", "Playing Chatterbox preview."),
            busy=False,
        )
        self.chatterbox_sample_player.setSource(QUrl.fromLocalFile(path))
        self.chatterbox_sample_player.play()
        self.log_view.append_event(
            self.tr("chatterbox_preview_ready", "Playing Chatterbox preview.")
        )

    def _show_chatterbox_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        self.chatterbox_preview_status_label.setText(message)
        self.chatterbox_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.chatterbox_preview_bar.setValue(100)
        self.chatterbox_preview_frame.setVisible(True)

    def _hide_chatterbox_preview_status(self) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        self.chatterbox_preview_frame.setVisible(False)

    def _on_chatterbox_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_chatterbox_preview_status(
                self.tr("chatterbox_preview_ready", "Playing Chatterbox preview."),
                busy=False,
            )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.chatterbox_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_chatterbox_preview_status)

    def _clear_chatterbox_preview_worker(self) -> None:
        self.chatterbox_preview_worker = None
        self.chatterbox_preview_thread = None
        if (
            hasattr(self, "chatterbox_preview_frame")
            and self.chatterbox_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_chatterbox_preview_status)
        self._refresh_chatterbox_status()

    def _chatterbox_voice_config_for_ui(self) -> dict[str, object] | None:
        if not self.chatterbox_manager.has_runtime():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "chatterbox_runtime_missing",
                    "chatterbox_engine.exe is missing. Build it with "
                    "build_chatterbox_engine.bat or install a runtime pack.",
                ),
            )
            return None
        reference_path = self.chatterbox_reference_picker.path()
        reference_text = str(reference_path or "")
        if reference_path is not None and not reference_path.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "reference_audio_missing",
                    "Reference audio file not found: {path}",
                    path=reference_text,
                ),
            )
            return None
        if reference_path is not None and not self.chatterbox_consent_checkbox.isChecked():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "voice_clone_consent_required",
                    "Confirm that you have permission to use the reference voice.",
                ),
            )
            return None
        model = str(self.chatterbox_model_combo.currentData() or "multilingual_v3")
        if model == "turbo" and reference_path is None:
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "chatterbox_turbo_reference_required",
                    "Chatterbox Turbo requires a 5-20 second reference audio file.",
                ),
            )
            return None
        return {
            "engine": "chatterbox",
            "speed": self.speed_spin.value(),
            "model": model,
            "language": self.chatterbox_language_combo.currentData() or "en",
            "device": self.chatterbox_device_combo.currentData() or "cuda",
            "reference_audio_path": reference_text,
            "exaggeration": self.chatterbox_exaggeration_spin.value(),
            "cfg_weight": self.chatterbox_cfg_spin.value(),
            "cache_dir": str(self.chatterbox_manager.cache_dir),
        }

    @staticmethod
    def _chatterbox_preview_text(language: str) -> str:
        texts = {
            "de": "Der Mond ist heute Nacht wunderschon.",
            "en": "The moon looks beautiful tonight.",
            "es": "La luna esta preciosa esta noche.",
            "fr": "La lune est magnifique ce soir.",
            "it": "La luna e bellissima stasera.",
            "ja": "今夜の月はとてもきれいです。",
            "pt": "A lua esta linda esta noite.",
            "zh": "今晚的月亮很美。",
        }
        return texts.get(language.lower(), texts["en"])

    def _build_openai_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.openai_api_key_edit = self._password_edit()
        self.openai_model_combo = QComboBox()
        for model in ("gpt-4o-mini-tts", "tts-1", "tts-1-hd"):
            self.openai_model_combo.addItem(model, model)
        self.openai_voice_combo = QComboBox()
        for voice in (
            "marin",
            "cedar",
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "fable",
            "nova",
            "onyx",
            "sage",
            "shimmer",
            "verse",
        ):
            self.openai_voice_combo.addItem(voice, voice)
        self.openai_instructions_edit = QLineEdit()
        self.openai_instructions_edit.setPlaceholderText(
            self.tr(
                "openai_instructions_placeholder",
                "Optional: calm narrator, energetic course teacher...",
            )
        )

        form.addRow(self.tr("api_key", "API key"), self.openai_api_key_edit)
        form.addRow(self.tr("openai_model", "Model"), self.openai_model_combo)
        form.addRow(self.tr("openai_voice", "Voice"), self.openai_voice_combo)
        form.addRow(
            self.tr("openai_instructions", "Style instructions"),
            self.openai_instructions_edit,
        )
        return panel

    def _build_elevenlabs_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.elevenlabs_api_key_edit = self._password_edit()
        self.elevenlabs_voice_id_edit = QLineEdit()
        self.elevenlabs_voice_id_edit.setPlaceholderText(
            self.tr(
                "elevenlabs_voice_id_placeholder",
                "Paste a voice_id from your ElevenLabs account",
            )
        )
        self.elevenlabs_model_combo = QComboBox()
        for model in (
            "eleven_flash_v2_5",
            "eleven_multilingual_v2",
            "eleven_v3",
        ):
            self.elevenlabs_model_combo.addItem(model, model)
        self.elevenlabs_output_combo = QComboBox()
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_pcm_24000", "PCM 24 kHz (recommended)"),
            "pcm_24000",
        )
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_pcm_44100", "PCM 44.1 kHz"),
            "pcm_44100",
        )
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_wav_44100", "WAV 44.1 kHz (Pro tier)"),
            "wav_44100",
        )
        self.elevenlabs_stability_spin = self._ratio_spin(0.5)
        self.elevenlabs_similarity_spin = self._ratio_spin(0.75)
        self.elevenlabs_style_spin = self._ratio_spin(0.0)
        self.elevenlabs_speaker_boost_checkbox = QCheckBox(
            self.tr("speaker_boost", "Speaker boost")
        )

        form.addRow(self.tr("api_key", "API key"), self.elevenlabs_api_key_edit)
        form.addRow(
            self.tr("elevenlabs_voice_id", "Voice ID"),
            self.elevenlabs_voice_id_edit,
        )
        form.addRow(
            self.tr("elevenlabs_model", "Model"),
            self.elevenlabs_model_combo,
        )
        form.addRow(
            self.tr("elevenlabs_output_format", "Output format"),
            self.elevenlabs_output_combo,
        )
        form.addRow(
            self.tr("stability", "Stability"),
            self.elevenlabs_stability_spin,
        )
        form.addRow(
            self.tr("similarity", "Similarity"),
            self.elevenlabs_similarity_spin,
        )
        form.addRow(
            self.tr("style_exaggeration", "Style exaggeration"),
            self.elevenlabs_style_spin,
        )
        form.addRow("", self.elevenlabs_speaker_boost_checkbox)
        return panel

    def _build_azure_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.azure_api_key_edit = self._password_edit()
        self.azure_region_edit = QLineEdit()
        self.azure_region_edit.setPlaceholderText(
            self.tr("azure_region_placeholder", "Example: westeurope")
        )
        self.azure_voice_edit = QLineEdit()
        self.azure_voice_edit.setPlaceholderText("en-US-JennyNeural")
        self.azure_output_combo = QComboBox()
        self.azure_output_combo.addItem(
            "RIFF 24 kHz mono PCM",
            "riff-24khz-16bit-mono-pcm",
        )
        self.azure_output_combo.addItem(
            "RIFF 48 kHz mono PCM",
            "riff-48khz-16bit-mono-pcm",
        )
        self.azure_style_edit = QLineEdit()
        self.azure_style_edit.setPlaceholderText(
            self.tr("azure_style_placeholder", "Optional: cheerful, sad, calm...")
        )
        form.addRow(self.tr("api_key", "API key"), self.azure_api_key_edit)
        form.addRow(self.tr("azure_region", "Region"), self.azure_region_edit)
        form.addRow(self.tr("azure_voice", "Voice name"), self.azure_voice_edit)
        form.addRow(
            self.tr("azure_output_format", "Output format"),
            self.azure_output_combo,
        )
        form.addRow(self.tr("azure_style", "Style"), self.azure_style_edit)
        return panel

    def _on_tts_engine_changed(self) -> None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        index = self.engine_stack_indexes.get(engine_id, 0)
        self.engine_settings_stack.setCurrentIndex(index)
        self._update_voice_panel_for_engine()

    def _update_voice_panel_for_engine(self) -> None:
        if not hasattr(self, "language_combo"):
            return
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        piper_selected = engine_id == "piper"
        self.language_combo.setEnabled(piper_selected)
        self.voice_combo.setEnabled(piper_selected)
        self.manage_voices_button.setEnabled(piper_selected)
        if piper_selected:
            self.voice_help_label.setText(
                self.tr(
                    "voice_help",
                    "Voices are discovered from voices/**/*.onnx when the matching "
                    ".onnx.json file is present.",
                )
            )
        else:
            self.voice_help_label.setText(
                self.tr(
                    "api_voice_config_help",
                    "This API engine uses the voice configured in Settings > General.",
                )
            )

    @staticmethod
    def _password_edit() -> QLineEdit:
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("sk-... / API key")
        return edit

    @staticmethod
    def _ratio_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(default)
        return spin

    def _tts_engine_label(self, engine_id: str) -> str:
        labels = {
            "piper": self.tr("tts_engine_piper", "Piper Local (offline, free)"),
            "kokoro": self.tr(
                "tts_engine_kokoro",
                "Kokoro - Better local quality",
            ),
            "chatterbox": self.tr(
                "tts_engine_chatterbox",
                "Chatterbox - Advanced local GPU",
            ),
            "openai": self.tr("tts_engine_openai", "OpenAI TTS (API)"),
            "elevenlabs": self.tr("tts_engine_elevenlabs", "ElevenLabs (API)"),
            "azure": self.tr("tts_engine_azure", "Azure Speech (API)"),
        }
        return labels.get(engine_id, engine_id)

    def _build_general_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        engine_group = self._build_tts_engine_settings()
        narration_group = QGroupBox(
            self.tr("narration_settings", "Narration and export")
        )
        left_form = QFormLayout(narration_group)
        left_form.setSpacing(10)
        output_group = QGroupBox(
            self.tr("output_and_podcast", "Output and podcast")
        )
        right_form = QFormLayout(output_group)
        right_form.setSpacing(10)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix("x")

        self.split_combo = QComboBox()
        self.split_combo.addItem(
            ui_icon("file"),
            self.tr("split_safe", "Split by safe chunks"),
            "safe_chunks",
        )
        self.split_combo.addItem(
            ui_icon("file"),
            self.tr("split_chapters", "Split by chapters/headings"),
            "chapters",
        )

        self.export_combo = QComboBox()
        self.export_combo.addItem(
            ui_icon("save"),
            self.tr("export_single", "Single MP3"),
            "single",
        )
        self.export_combo.addItem(
            ui_icon("save"),
            self.tr("export_chapters", "MP3 per chapter/block"),
            "chapters",
        )

        left_form.addRow(self.tr("voice_speed", "Voice speed"), self.speed_spin)
        left_form.addRow(self.tr("split_mode", "Text splitting"), self.split_combo)
        left_form.addRow(self.tr("export_mode", "Output type"), self.export_combo)

        output_path = str(resolve_app_path(self.settings.get("output_dir", "output")))
        self.output_picker = PathPicker(
            self.tr("browse", "Browse"),
            output_path,
        )
        self.normalize_checkbox = QCheckBox(
            self.tr("normalize_clean_audio", "Normalize clean narration")
        )
        self.open_folder_checkbox = QCheckBox(
            self.tr("open_output", "Open output folder when finished")
        )
        self.podcast_enabled_checkbox = QCheckBox(
            self.tr("create_podcast_mix", "Create podcast mix")
        )
        self.background_enabled_checkbox = QCheckBox(
            self.tr("background_music", "Background music")
        )
        self.background_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )

        right_form.addRow(
            self.tr("output_folder", "Output folder"),
            self.output_picker,
        )
        right_form.addRow("", self.normalize_checkbox)
        right_form.addRow("", self.open_folder_checkbox)
        right_form.addRow("", self.podcast_enabled_checkbox)
        right_form.addRow(
            self.background_enabled_checkbox,
            self.background_picker,
        )

        grid.addWidget(engine_group, 0, 0, 1, 2)
        grid.addWidget(narration_group, 1, 0)
        grid.addWidget(output_group, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _build_advanced_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)

        pause_group = QGroupBox(
            self.tr("natural_pauses", "Natural paragraph pauses")
        )
        pause_form = QFormLayout(pause_group)
        self.adaptive_pause_checkbox = QCheckBox(
            self.tr(
                "adaptive_pause",
                "Adapt pause to paragraph length and reading rhythm",
            )
        )
        pause_form.addRow("", self.adaptive_pause_checkbox)

        paragraph_pause_row = QWidget()
        paragraph_pause_layout = QHBoxLayout(paragraph_pause_row)
        paragraph_pause_layout.setContentsMargins(0, 0, 0, 0)
        paragraph_pause_layout.setSpacing(6)
        self.paragraph_pause_min_spin = self._seconds_spin(0.45)
        self.paragraph_pause_max_spin = self._seconds_spin(0.90)
        self.paragraph_pause_min_spin.valueChanged.connect(
            self.paragraph_pause_max_spin.setMinimum
        )
        paragraph_pause_layout.addWidget(self.paragraph_pause_min_spin)
        paragraph_pause_layout.addWidget(QLabel(self.tr("to", "to")))
        paragraph_pause_layout.addWidget(self.paragraph_pause_max_spin)
        pause_form.addRow(
            self.tr("base_paragraph_pause", "Base paragraph pause"),
            paragraph_pause_row,
        )

        self.paragraph_length_reference_spin = QSpinBox()
        self.paragraph_length_reference_spin.setRange(50, 5000)
        self.paragraph_length_reference_spin.setSuffix(
            self.tr("characters_suffix", " chars")
        )
        self.paragraph_length_extra_spin = self._seconds_spin(0.65)
        pause_form.addRow(
            self.tr("long_paragraph_reference", "Long paragraph reference"),
            self.paragraph_length_reference_spin,
        )
        pause_form.addRow(
            self.tr("long_paragraph_extra", "Extra pause after long paragraph"),
            self.paragraph_length_extra_spin,
        )

        self.periodic_pause_every_spin = QSpinBox()
        self.periodic_pause_every_spin.setRange(0, 100)
        self.periodic_pause_every_spin.setSpecialValueText(
            self.tr("disabled", "Disabled")
        )
        periodic_pause_row = QWidget()
        periodic_pause_layout = QHBoxLayout(periodic_pause_row)
        periodic_pause_layout.setContentsMargins(0, 0, 0, 0)
        periodic_pause_layout.setSpacing(6)
        self.periodic_pause_min_spin = self._seconds_spin(0.35)
        self.periodic_pause_max_spin = self._seconds_spin(0.75)
        self.periodic_pause_min_spin.valueChanged.connect(
            self.periodic_pause_max_spin.setMinimum
        )
        periodic_pause_layout.addWidget(self.periodic_pause_min_spin)
        periodic_pause_layout.addWidget(QLabel(self.tr("to", "to")))
        periodic_pause_layout.addWidget(self.periodic_pause_max_spin)
        pause_form.addRow(
            self.tr("periodic_pause_every", "Extra pause every N paragraphs"),
            self.periodic_pause_every_spin,
        )
        pause_form.addRow(
            self.tr("periodic_pause_duration", "Periodic extra pause"),
            periodic_pause_row,
        )

        podcast_group = QGroupBox(
            self.tr("podcast_mix_settings", "Podcast mix")
        )
        podcast_form = QFormLayout(podcast_group)
        self.intro_enabled_checkbox = QCheckBox(
            self.tr("enable_intro", "Enable intro")
        )
        self.intro_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        self.outro_enabled_checkbox = QCheckBox(
            self.tr("enable_outro", "Enable outro")
        )
        self.outro_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        self.background_loop_checkbox = QCheckBox(
            self.tr("loop_background", "Loop background during narration")
        )
        self.background_volume_spin = QSpinBox()
        self.background_volume_spin.setRange(0, 100)
        self.background_volume_spin.setSuffix("%")
        self.fade_in_spin = self._seconds_spin(1.5)
        self.fade_out_spin = self._seconds_spin(2.0)
        self.podcast_gap_spin = self._seconds_spin(0.5)
        self.podcast_normalize_checkbox = QCheckBox(
            self.tr("normalize_podcast", "Normalize podcast output to -16 LUFS")
        )
        self.podcast_ducking_checkbox = QCheckBox(
            self.tr(
                "enable_ducking",
                "Lower background music while narration is speaking",
            )
        )
        podcast_form.addRow(self.intro_enabled_checkbox, self.intro_picker)
        podcast_form.addRow("", self.background_loop_checkbox)
        podcast_form.addRow(
            self.tr("background_volume", "Background volume"),
            self.background_volume_spin,
        )
        podcast_form.addRow(self.outro_enabled_checkbox, self.outro_picker)
        podcast_form.addRow(
            self.tr("music_fade_in", "Music fade in"),
            self.fade_in_spin,
        )
        podcast_form.addRow(
            self.tr("music_fade_out", "Music fade out"),
            self.fade_out_spin,
        )
        podcast_form.addRow(
            self.tr("podcast_gap", "Silence between sections"),
            self.podcast_gap_spin,
        )
        podcast_form.addRow("", self.podcast_normalize_checkbox)
        podcast_form.addRow("", self.podcast_ducking_checkbox)

        grid.addWidget(pause_group, 0, 0)
        grid.addWidget(podcast_group, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _seconds_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 30.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def _build_log_panel(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel(self.tr("log", "Log"))
        title.setObjectName("sectionLabel")
        layout.addWidget(title)
        self.log_view = LogView()
        layout.addWidget(self.log_view)
        return widget

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f6f9;
                color: #18212f;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QFrame#card, QTabWidget::pane {
                background: white;
                border: 1px solid #dfe4ec;
                border-radius: 10px;
            }
            QLabel#titleLabel {
                color: #162033;
                font-size: 24pt;
                font-weight: 700;
            }
            QLabel#subtitleLabel, QLabel#helperLabel {
                color: #657084;
            }
            QLabel#authorCreditLabel {
                color: #657084;
                font-size: 9pt;
            }
            QLabel#authorCreditLabel a {
                color: #3859d9;
                text-decoration: none;
            }
            QLabel#sectionLabel {
                font-size: 12pt;
                font-weight: 600;
            }
            QFrame#inlineStatusFrame {
                background: #f7f9fd;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
            QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox {
                background: #ffffff;
                border: 1px solid #ccd3df;
                border-radius: 6px;
                padding: 7px;
                selection-background-color: #4468e8;
            }
            QTextEdit:focus, QPlainTextEdit:focus, QLineEdit:focus,
            QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #4468e8;
            }
            QPushButton {
                background: #eef1f6;
                border: 1px solid #d3d9e4;
                border-radius: 6px;
                padding: 7px 12px;
            }
            QPushButton:hover {
                background: #e4e9f2;
            }
            QPushButton#primaryButton {
                background: #3859d9;
                border-color: #3859d9;
                color: white;
                font-weight: 600;
                padding: 9px 18px;
            }
            QPushButton#primaryButton:hover {
                background: #2f4fc9;
            }
            QPushButton:disabled {
                background: #e5e8ee;
                color: #9299a6;
                border-color: #dfe2e8;
            }
            QProgressBar {
                border: 1px solid #d5dae4;
                border-radius: 6px;
                background: #eef1f5;
                text-align: center;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background: #4263df;
                border-radius: 5px;
            }
            QTabBar::tab {
                background: #e9edf4;
                border: 1px solid #d9dee8;
                padding: 8px 18px;
            }
            QTabBar::tab:selected {
                background: white;
                color: #294ccf;
            }
            """
        )

    def _load_voices(self) -> None:
        self.voices = VoiceManager(application_root() / "voices").discover()
        current_language = self.language_combo.currentData()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(
            ui_icon("language"),
            self.tr("all_languages", "All languages"),
            "",
        )
        for language in sorted({voice.language for voice in self.voices}):
            self.language_combo.addItem(ui_icon("language"), language, language)
        self.language_combo.blockSignals(False)
        self._select_combo_data(self.language_combo, current_language or "")
        self._filter_voices()

    def _refresh_voices(self) -> None:
        selected_voice = self.voice_combo.currentData()
        self._load_voices()
        self._select_combo_data(self.voice_combo, selected_voice)
        self.log_view.append_event(
            self.tr(
                "voices_found",
                "Discovered {count} valid Piper voice(s).",
                count=len(self.voices),
            )
        )

    def _open_voice_manager(self) -> None:
        dialog = VoiceManagerDialog(
            application_root() / "voices",
            self.translator,
            self,
        )
        dialog.voices_changed.connect(self._refresh_voices)
        dialog.exec()
        self._refresh_voices()

    def _filter_voices(self) -> None:
        language = self.language_combo.currentData() or ""
        selected_voice = self.voice_combo.currentData()
        self.voice_combo.clear()
        matching = [
            voice for voice in self.voices if not language or voice.language == language
        ]
        for voice in matching:
            self.voice_combo.addItem(
                ui_icon("voice"),
                voice.display_name,
                voice.voice_id,
            )
        self._select_combo_data(self.voice_combo, selected_voice)
        if not matching:
            self.voice_combo.addItem(
                ui_icon("info"),
                self.tr("no_voices", "No valid Piper voices found"),
                None,
            )

    def _restore_settings(self) -> None:
        self._select_combo_data(
            self.tts_engine_combo,
            self.settings.get("tts_engine", "piper"),
        )
        self.piper_path_edit.setText(
            str(self.settings.get("piper_path", "engines/piper/piper.exe"))
        )
        api_tts = self._api_tts_settings()
        openai = api_tts.get("openai", {})
        self.openai_api_key_edit.setText(str(openai.get("api_key", "")))
        self._select_combo_data(
            self.openai_model_combo,
            openai.get("model", "gpt-4o-mini-tts"),
        )
        self._select_combo_data(self.openai_voice_combo, openai.get("voice", "marin"))
        self.openai_instructions_edit.setText(
            str(openai.get("instructions", ""))
        )

        kokoro = self.settings.get("kokoro", {})
        if not isinstance(kokoro, dict):
            kokoro = {}
        self._select_combo_data(
            self.kokoro_voice_combo,
            kokoro.get("voice", "af_heart"),
        )
        self._select_combo_data(
            self.kokoro_provider_combo,
            kokoro.get("provider", "cpu"),
        )

        chatterbox = self.settings.get("chatterbox", {})
        if not isinstance(chatterbox, dict):
            chatterbox = {}
        self._select_combo_data(
            self.chatterbox_model_combo,
            chatterbox.get("model", "multilingual_v3"),
        )
        self._select_combo_data(
            self.chatterbox_language_combo,
            chatterbox.get("language", "en"),
        )
        self._select_combo_data(
            self.chatterbox_device_combo,
            chatterbox.get("device", "cuda"),
        )
        self.chatterbox_reference_picker.set_path(
            str(chatterbox.get("reference_audio_path", ""))
        )
        self.chatterbox_consent_checkbox.setChecked(
            bool(chatterbox.get("voice_clone_consent", False))
        )
        self.chatterbox_exaggeration_spin.setValue(
            float(chatterbox.get("exaggeration", 0.5))
        )
        self.chatterbox_cfg_spin.setValue(
            float(chatterbox.get("cfg_weight", 0.5))
        )

        elevenlabs = api_tts.get("elevenlabs", {})
        self.elevenlabs_api_key_edit.setText(str(elevenlabs.get("api_key", "")))
        self.elevenlabs_voice_id_edit.setText(
            str(elevenlabs.get("voice_id", ""))
        )
        self._select_combo_data(
            self.elevenlabs_model_combo,
            elevenlabs.get("model_id", "eleven_flash_v2_5"),
        )
        self._select_combo_data(
            self.elevenlabs_output_combo,
            elevenlabs.get("output_format", "pcm_24000"),
        )
        self.elevenlabs_stability_spin.setValue(
            float(elevenlabs.get("stability", 0.5))
        )
        self.elevenlabs_similarity_spin.setValue(
            float(elevenlabs.get("similarity_boost", 0.75))
        )
        self.elevenlabs_style_spin.setValue(float(elevenlabs.get("style", 0.0)))
        self.elevenlabs_speaker_boost_checkbox.setChecked(
            bool(elevenlabs.get("use_speaker_boost", True))
        )

        azure = api_tts.get("azure", {})
        self.azure_api_key_edit.setText(str(azure.get("api_key", "")))
        self.azure_region_edit.setText(str(azure.get("region", "")))
        self.azure_voice_edit.setText(
            str(azure.get("voice", "en-US-JennyNeural"))
        )
        self._select_combo_data(
            self.azure_output_combo,
            azure.get("output_format", "riff-24khz-16bit-mono-pcm"),
        )
        self.azure_style_edit.setText(str(azure.get("style", "")))
        self._on_tts_engine_changed()

        self.speed_spin.setValue(float(self.settings.get("speed", 1.0)))
        paragraph_min = max(
            0,
            int(self.settings.get("paragraph_pause_min_ms", 450)),
        )
        paragraph_max = max(
            paragraph_min,
            int(self.settings.get("paragraph_pause_max_ms", 900)),
        )
        self.paragraph_pause_min_spin.setValue(paragraph_min / 1000)
        self.paragraph_pause_max_spin.setValue(paragraph_max / 1000)
        self.adaptive_pause_checkbox.setChecked(
            bool(self.settings.get("adaptive_paragraph_pause", True))
        )
        self.paragraph_length_reference_spin.setValue(
            int(self.settings.get("paragraph_length_reference_chars", 600))
        )
        self.paragraph_length_extra_spin.setValue(
            int(self.settings.get("paragraph_length_extra_ms", 650)) / 1000
        )
        self.periodic_pause_every_spin.setValue(
            int(self.settings.get("periodic_pause_every_paragraphs", 5))
        )
        periodic_min = max(
            0,
            int(self.settings.get("periodic_pause_min_ms", 350)),
        )
        periodic_max = max(
            periodic_min,
            int(self.settings.get("periodic_pause_max_ms", 750)),
        )
        self.periodic_pause_min_spin.setValue(periodic_min / 1000)
        self.periodic_pause_max_spin.setValue(periodic_max / 1000)
        self._select_combo_data(
            self.split_combo,
            self.settings.get("split_mode", "safe_chunks"),
        )
        self._select_combo_data(
            self.export_combo,
            self.settings.get("export_mode", "single"),
        )
        self.normalize_checkbox.setChecked(
            bool(self.settings.get("normalize_audio", False))
        )
        self.podcast_enabled_checkbox.setChecked(
            bool(self.settings.get("podcast_enabled", False))
        )
        self.intro_enabled_checkbox.setChecked(
            bool(self.settings.get("intro_enabled", False))
        )
        self.intro_picker.set_path(self.settings.get("intro_path", ""))
        self.background_enabled_checkbox.setChecked(
            bool(self.settings.get("background_enabled", False))
        )
        self.background_picker.set_path(self.settings.get("background_path", ""))
        self.background_loop_checkbox.setChecked(
            bool(self.settings.get("background_loop", True))
        )
        self.background_volume_spin.setValue(
            int(self.settings.get("background_volume_percent", 12))
        )
        self.outro_enabled_checkbox.setChecked(
            bool(self.settings.get("outro_enabled", False))
        )
        self.outro_picker.set_path(self.settings.get("outro_path", ""))
        self.fade_in_spin.setValue(
            float(self.settings.get("music_fade_in_seconds", 1.5))
        )
        self.fade_out_spin.setValue(
            float(self.settings.get("music_fade_out_seconds", 2.0))
        )
        self.podcast_gap_spin.setValue(
            int(self.settings.get("podcast_gap_ms", 500)) / 1000
        )
        self.podcast_normalize_checkbox.setChecked(
            bool(self.settings.get("podcast_normalize", True))
        )
        self.podcast_ducking_checkbox.setChecked(
            bool(self.settings.get("podcast_ducking", True))
        )
        self.open_folder_checkbox.setChecked(
            bool(self.settings.get("open_output_on_finish", True))
        )
        self.ui_language_combo.blockSignals(True)
        self._select_combo_data(
            self.ui_language_combo,
            self.settings.get("ui_language", "en"),
        )
        self.ui_language_combo.blockSignals(False)
        saved_language = str(self.settings.get("language", ""))
        self._select_combo_data(self.language_combo, saved_language)
        self._filter_voices()
        self._select_combo_data(
            self.voice_combo,
            self.settings.get("voice_id", ""),
        )
        self.log_view.append_event(
            self.tr(
                "voices_found",
                "Discovered {count} valid Piper voice(s).",
                count=len(self.voices),
            )
        )

    def _api_tts_settings(self) -> dict[str, dict[str, object]]:
        value = self.settings.get("api_tts", {})
        if not isinstance(value, dict):
            return {}
        return {
            str(key): dict(item) if isinstance(item, dict) else {}
            for key, item in value.items()
        }

    def _change_ui_language(self) -> None:
        language = str(self.ui_language_combo.currentData() or "en")
        if language == self.translator.language:
            return
        text = self.text_editor.toPlainText()
        page_index = self.page_stack.currentIndex()
        self._save_settings()
        self.settings["ui_language"] = language
        self.settings_manager.save(self.settings)
        self.translator.set_language(language)

        old_central = self.takeCentralWidget()
        self.setWindowTitle(self.tr("app_title", "LocalText2Voice"))
        self._build_ui()
        self._apply_style()
        self._load_voices()
        self._restore_settings()
        self.text_editor.setPlainText(text)
        if page_index == 1:
            self.page_stack.setCurrentIndex(1)
            self.settings_button.setText(
                self.tr("back_to_generation", "Back to generation")
            )
            self.settings_button.setIcon(ui_icon("back"))
        self._set_running(False)
        if old_central is not None:
            old_central.deleteLater()

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _import_document(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("import_file", "Import file"),
            "",
            self.tr(
                "supported_documents",
                "Supported documents (*.txt *.md *.docx);;"
                "Text files (*.txt);;Markdown files (*.md);;"
                "Word documents (*.docx)",
            ),
        )
        if not path_text:
            return
        try:
            text = ProjectManager.import_document(Path(path_text))
            self.text_editor.setPlainText(text)
            self.log_view.append_event(f"Imported: {path_text}")
        except DocumentImportError as exc:
            self._show_error(self.tr("import_failed", "Import failed"), str(exc))

    def _current_voice_config(self) -> dict[str, object] | None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        speed = self.speed_spin.value()
        if engine_id == "piper":
            voice_id = self.voice_combo.currentData()
            voice = next(
                (
                    candidate
                    for candidate in self.voices
                    if candidate.voice_id == voice_id
                ),
                None,
            )
            if voice is None:
                self._show_error(
                    self.tr("missing_voice", "No voice selected"),
                    self.tr(
                        "missing_voice_message",
                        "Add a Piper .onnx model and its .onnx.json file to the "
                        "voices folder.",
                    ),
                )
                return None
            config = voice.as_config(speed)
            config["engine"] = "piper"
            return config

        if engine_id == "openai":
            return {
                "engine": "openai",
                "speed": speed,
                "api_key": self.openai_api_key_edit.text().strip(),
                "model": self.openai_model_combo.currentData(),
                "voice": self.openai_voice_combo.currentData(),
                "instructions": self.openai_instructions_edit.text().strip(),
            }
        if engine_id == "kokoro":
            voice_id = str(self.kokoro_voice_combo.currentData() or "af_heart")
            provider = str(self.kokoro_provider_combo.currentData() or "cpu")
            if provider != "cpu":
                self._show_error(
                    self.tr("generation_failed", "Generation failed"),
                    self.tr(
                        "kokoro_cpu_only",
                        "Kokoro is currently enabled in CPU mode only.",
                    ),
                )
                return None
            return {
                "engine": "kokoro",
                "speed": speed,
                "voice": voice_id,
                "lang": self._kokoro_language_for_voice(voice_id),
                "provider": provider,
                "model_path": str(self.kokoro_manager.model_path),
            }
        if engine_id == "chatterbox":
            return self._chatterbox_voice_config_for_ui()
        if engine_id == "elevenlabs":
            return {
                "engine": "elevenlabs",
                "speed": speed,
                "api_key": self.elevenlabs_api_key_edit.text().strip(),
                "voice_id": self.elevenlabs_voice_id_edit.text().strip(),
                "model_id": self.elevenlabs_model_combo.currentData(),
                "output_format": self.elevenlabs_output_combo.currentData(),
                "stability": self.elevenlabs_stability_spin.value(),
                "similarity_boost": self.elevenlabs_similarity_spin.value(),
                "style": self.elevenlabs_style_spin.value(),
                "use_speaker_boost": (
                    self.elevenlabs_speaker_boost_checkbox.isChecked()
                ),
            }
        if engine_id == "azure":
            return {
                "engine": "azure",
                "speed": speed,
                "api_key": self.azure_api_key_edit.text().strip(),
                "region": self.azure_region_edit.text().strip(),
                "voice": self.azure_voice_edit.text().strip(),
                "output_format": self.azure_output_combo.currentData(),
                "style": self.azure_style_edit.text().strip(),
            }
        self._show_error(
            self.tr("generation_failed", "Generation failed"),
            f"Unknown TTS engine: {engine_id}",
        )
        return None

    def _start_generation(self) -> None:
        if self.worker_thread is not None:
            return

        text = self.text_editor.toPlainText().strip()
        if not text:
            self._show_error(
                self.tr("missing_text", "Missing text"),
                self.tr("missing_text_message", "Paste or import text first."),
            )
            return

        voice_config = self._current_voice_config()
        if voice_config is None:
            return

        output_dir = self.output_picker.path()
        if not output_dir.is_absolute():
            output_dir = resolve_app_path(output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error(
                self.tr("output_error", "Output folder error"),
                str(exc),
            )
            return

        options = AudioGenerationOptions(
            output_dir=output_dir,
            voice_config=voice_config,
            ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            split_mode=str(self.split_combo.currentData()),
            export_mode=str(self.export_combo.currentData()),
            chunk_size=int(self.settings.get("chunk_size", 2500)),
            pause_between_blocks_ms=int(
                self.settings.get("pause_between_blocks_ms", 350)
            ),
            pause_between_chapters_ms=int(
                self.settings.get("pause_between_chapters_ms", 900)
            ),
            paragraph_pause_min_ms=round(
                self.paragraph_pause_min_spin.value() * 1000
            ),
            paragraph_pause_max_ms=round(
                self.paragraph_pause_max_spin.value() * 1000
            ),
            adaptive_paragraph_pause=self.adaptive_pause_checkbox.isChecked(),
            paragraph_length_reference_chars=(
                self.paragraph_length_reference_spin.value()
            ),
            paragraph_length_extra_ms=round(
                self.paragraph_length_extra_spin.value() * 1000
            ),
            periodic_pause_every_paragraphs=(
                self.periodic_pause_every_spin.value()
            ),
            periodic_pause_min_ms=round(
                self.periodic_pause_min_spin.value() * 1000
            ),
            periodic_pause_max_ms=round(
                self.periodic_pause_max_spin.value() * 1000
            ),
            normalize_audio=self.normalize_checkbox.isChecked(),
            podcast_enabled=self.podcast_enabled_checkbox.isChecked(),
            intro_enabled=self.intro_enabled_checkbox.isChecked(),
            intro_path=self._resolved_audio_path(self.intro_picker.path()),
            background_enabled=self.background_enabled_checkbox.isChecked(),
            background_path=self._resolved_audio_path(
                self.background_picker.path()
            ),
            background_loop=self.background_loop_checkbox.isChecked(),
            background_volume_percent=self.background_volume_spin.value(),
            outro_enabled=self.outro_enabled_checkbox.isChecked(),
            outro_path=self._resolved_audio_path(self.outro_picker.path()),
            music_fade_in_seconds=self.fade_in_spin.value(),
            music_fade_out_seconds=self.fade_out_spin.value(),
            podcast_gap_ms=round(self.podcast_gap_spin.value() * 1000),
            podcast_normalize=self.podcast_normalize_checkbox.isChecked(),
            podcast_ducking=self.podcast_ducking_checkbox.isChecked(),
            mp3_bitrate=str(self.settings.get("mp3_bitrate", "128k")),
            metadata=dict(self.settings.get("metadata", {})),
        )
        piper_path = resolve_app_path(
            self.piper_path_edit.text().strip() or "engines/piper/piper.exe"
        )
        self._save_settings()
        self.log_view.clear()
        self.log_view.append_event(self.tr("starting", "Starting generation..."))
        self.progress_bar.setValue(0)
        self.status_label.setText(self.tr("preparing", "Preparing audio job..."))
        self.generation_started_at = time.monotonic()
        self.progress_current = 0
        self.progress_total = 0
        self.last_output_folder = None
        self.open_output_button.setVisible(False)
        self.time_label.setVisible(True)
        self._update_generation_time()
        self.generation_timer.start()
        self._set_running(True)

        thread = QThread(self)
        worker = GenerationWorker(text, options, piper_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self.worker_thread = thread
        self.worker = worker
        thread.start()

    def _cancel_generation(self) -> None:
        if self.worker is None:
            return
        self.status_label.setText(
            self.tr("cancelling", "Cancelling generation...")
        )
        self.cancel_button.setEnabled(False)
        self.worker.request_cancel()

    def _on_progress(self, current: int, total: int, status: str) -> None:
        percentage = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(min(99, percentage))
        self.status_label.setText(status)
        self.progress_current = current
        self.progress_total = total
        self._update_generation_time()

    def _on_finished(self, output_paths: list[str]) -> None:
        self.generation_timer.stop()
        self.progress_current = self.progress_total
        self._update_generation_time()
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr(
                "generation_complete",
                "Generation complete. Created {count} file(s).",
                count=len(output_paths),
            )
        )
        self.log_view.append_event(self.status_label.text())
        if output_paths:
            self.last_output_folder = Path(output_paths[0]).parent
            self.open_output_button.setVisible(True)
            if self.open_folder_checkbox.isChecked():
                self._open_last_output_folder()

    def _on_failed(self, message: str) -> None:
        self.generation_timer.stop()
        self._update_generation_time()
        self.status_label.setText(self.tr("generation_failed", "Generation failed"))
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_cancelled(self) -> None:
        self.generation_timer.stop()
        self._update_generation_time()
        self.status_label.setText(
            self.tr("generation_cancelled", "Generation cancelled")
        )
        self.log_view.append_event(
            self.tr("generation_cancelled", "Generation cancelled")
        )

    def _clear_worker(self) -> None:
        self.generation_timer.stop()
        self.worker = None
        self.worker_thread = None
        self._set_running(False)

    def _update_generation_time(self) -> None:
        if self.generation_started_at is None:
            return
        elapsed = max(0, round(time.monotonic() - self.generation_started_at))
        if self.progress_current > 0 and self.progress_total > self.progress_current:
            remaining = round(
                elapsed
                / self.progress_current
                * (self.progress_total - self.progress_current)
            )
            remaining_text = self._format_duration(remaining)
        elif self.progress_total > 0 and self.progress_current >= self.progress_total:
            remaining_text = self._format_duration(0)
        else:
            remaining_text = self.tr("calculating", "Calculating...")
        self.time_label.setText(
            self.tr(
                "generation_time_status",
                "Elapsed: {elapsed} | Estimated remaining: {remaining}",
                elapsed=self._format_duration(elapsed),
                remaining=remaining_text,
            )
        )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _open_last_output_folder(self) -> None:
        if self.last_output_folder is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.last_output_folder))
            )

    def _set_running(self, running: bool) -> None:
        self.generate_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.open_output_button.setEnabled(not running)
        self.text_editor.setReadOnly(running)
        for widget in (
            self.import_button,
            self.language_combo,
            self.voice_combo,
            self.manage_voices_button,
            self.settings_button,
            self.back_button,
            self.speed_spin,
            self.split_combo,
            self.export_combo,
            self.ui_language_combo,
            self.tts_engine_combo,
            self.piper_path_edit,
            self.kokoro_voice_combo,
            self.kokoro_provider_combo,
            self.kokoro_install_button,
            self.kokoro_remove_button,
            self.kokoro_test_button,
            self.chatterbox_model_combo,
            self.chatterbox_language_combo,
            self.chatterbox_device_combo,
            self.chatterbox_reference_picker,
            self.chatterbox_consent_checkbox,
            self.chatterbox_exaggeration_spin,
            self.chatterbox_cfg_spin,
            self.chatterbox_install_button,
            self.chatterbox_remove_button,
            self.chatterbox_test_button,
            self.openai_api_key_edit,
            self.openai_model_combo,
            self.openai_voice_combo,
            self.openai_instructions_edit,
            self.elevenlabs_api_key_edit,
            self.elevenlabs_voice_id_edit,
            self.elevenlabs_model_combo,
            self.elevenlabs_output_combo,
            self.elevenlabs_stability_spin,
            self.elevenlabs_similarity_spin,
            self.elevenlabs_style_spin,
            self.elevenlabs_speaker_boost_checkbox,
            self.azure_api_key_edit,
            self.azure_region_edit,
            self.azure_voice_edit,
            self.azure_output_combo,
            self.azure_style_edit,
            self.paragraph_pause_min_spin,
            self.paragraph_pause_max_spin,
            self.adaptive_pause_checkbox,
            self.paragraph_length_reference_spin,
            self.paragraph_length_extra_spin,
            self.periodic_pause_every_spin,
            self.periodic_pause_min_spin,
            self.periodic_pause_max_spin,
            self.output_picker,
            self.normalize_checkbox,
            self.podcast_enabled_checkbox,
            self.intro_enabled_checkbox,
            self.intro_picker,
            self.background_enabled_checkbox,
            self.background_picker,
            self.background_loop_checkbox,
            self.background_volume_spin,
            self.outro_enabled_checkbox,
            self.outro_picker,
            self.fade_in_spin,
            self.fade_out_spin,
            self.podcast_gap_spin,
            self.podcast_normalize_checkbox,
            self.podcast_ducking_checkbox,
            self.open_folder_checkbox,
        ):
            widget.setEnabled(not running)
        if not running:
            self._update_voice_panel_for_engine()
            self._refresh_kokoro_status()
            self._refresh_chatterbox_status()

    def _save_settings(self) -> None:
        output_dir = self.output_picker.path()
        self.settings.update(
            {
                "output_dir": str(output_dir),
                "ui_language": self.ui_language_combo.currentData() or "en",
                "tts_engine": self.tts_engine_combo.currentData() or "piper",
                "piper_path": self.piper_path_edit.text().strip()
                or "engines/piper/piper.exe",
                "voice_id": self.voice_combo.currentData() or "",
                "language": self.language_combo.currentData() or "",
                "speed": self.speed_spin.value(),
                "paragraph_pause_min_ms": round(
                    self.paragraph_pause_min_spin.value() * 1000
                ),
                "paragraph_pause_max_ms": round(
                    self.paragraph_pause_max_spin.value() * 1000
                ),
                "adaptive_paragraph_pause": (
                    self.adaptive_pause_checkbox.isChecked()
                ),
                "paragraph_length_reference_chars": (
                    self.paragraph_length_reference_spin.value()
                ),
                "paragraph_length_extra_ms": round(
                    self.paragraph_length_extra_spin.value() * 1000
                ),
                "periodic_pause_every_paragraphs": (
                    self.periodic_pause_every_spin.value()
                ),
                "periodic_pause_min_ms": round(
                    self.periodic_pause_min_spin.value() * 1000
                ),
                "periodic_pause_max_ms": round(
                    self.periodic_pause_max_spin.value() * 1000
                ),
                "split_mode": self.split_combo.currentData(),
                "export_mode": self.export_combo.currentData(),
                "normalize_audio": self.normalize_checkbox.isChecked(),
                "podcast_enabled": self.podcast_enabled_checkbox.isChecked(),
                "intro_enabled": self.intro_enabled_checkbox.isChecked(),
                "intro_path": str(self.intro_picker.path() or ""),
                "background_enabled": (
                    self.background_enabled_checkbox.isChecked()
                ),
                "background_path": str(self.background_picker.path() or ""),
                "background_loop": self.background_loop_checkbox.isChecked(),
                "background_volume_percent": (
                    self.background_volume_spin.value()
                ),
                "outro_enabled": self.outro_enabled_checkbox.isChecked(),
                "outro_path": str(self.outro_picker.path() or ""),
                "music_fade_in_seconds": self.fade_in_spin.value(),
                "music_fade_out_seconds": self.fade_out_spin.value(),
                "podcast_gap_ms": round(self.podcast_gap_spin.value() * 1000),
                "podcast_normalize": (
                    self.podcast_normalize_checkbox.isChecked()
                ),
                "podcast_ducking": self.podcast_ducking_checkbox.isChecked(),
                "open_output_on_finish": self.open_folder_checkbox.isChecked(),
                "kokoro": {
                    "voice": self.kokoro_voice_combo.currentData() or "af_heart",
                    "lang": self._kokoro_language_for_voice(
                        str(self.kokoro_voice_combo.currentData() or "af_heart")
                    ),
                    "provider": self.kokoro_provider_combo.currentData() or "cpu",
                },
                "chatterbox": {
                    "model": (
                        self.chatterbox_model_combo.currentData()
                        or "multilingual_v3"
                    ),
                    "language": self.chatterbox_language_combo.currentData() or "en",
                    "device": self.chatterbox_device_combo.currentData() or "cuda",
                    "reference_audio_path": str(
                        self.chatterbox_reference_picker.path() or ""
                    ),
                    "voice_clone_consent": (
                        self.chatterbox_consent_checkbox.isChecked()
                    ),
                    "exaggeration": self.chatterbox_exaggeration_spin.value(),
                    "cfg_weight": self.chatterbox_cfg_spin.value(),
                },
                "api_tts": {
                    "openai": {
                        "api_key": self.openai_api_key_edit.text().strip(),
                        "model": self.openai_model_combo.currentData(),
                        "voice": self.openai_voice_combo.currentData(),
                        "instructions": (
                            self.openai_instructions_edit.text().strip()
                        ),
                        "timeout_seconds": 120,
                    },
                    "elevenlabs": {
                        "api_key": self.elevenlabs_api_key_edit.text().strip(),
                        "voice_id": (
                            self.elevenlabs_voice_id_edit.text().strip()
                        ),
                        "model_id": self.elevenlabs_model_combo.currentData(),
                        "output_format": (
                            self.elevenlabs_output_combo.currentData()
                        ),
                        "stability": self.elevenlabs_stability_spin.value(),
                        "similarity_boost": (
                            self.elevenlabs_similarity_spin.value()
                        ),
                        "style": self.elevenlabs_style_spin.value(),
                        "use_speaker_boost": (
                            self.elevenlabs_speaker_boost_checkbox.isChecked()
                        ),
                        "timeout_seconds": 120,
                    },
                    "azure": {
                        "api_key": self.azure_api_key_edit.text().strip(),
                        "region": self.azure_region_edit.text().strip(),
                        "voice": self.azure_voice_edit.text().strip(),
                        "output_format": self.azure_output_combo.currentData(),
                        "style": self.azure_style_edit.text().strip(),
                        "timeout_seconds": 120,
                    },
                },
            }
        )
        try:
            self.settings_manager.save(self.settings)
        except OSError as exc:
            self.log_view.append_event(f"Could not save config.json: {exc}")

    @staticmethod
    def _resolved_audio_path(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path if path.is_absolute() else resolve_app_path(path)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            choice = QMessageBox.question(
                self,
                self.tr("generation_running", "Generation is running"),
                self.tr(
                    "close_running_message",
                    "Cancel the current generation and close the application?",
                ),
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_cancel()
            if self.worker_thread is not None:
                self.worker_thread.quit()
                if not self.worker_thread.wait(5000):
                    QMessageBox.warning(
                        self,
                        self.tr("still_stopping", "Still stopping"),
                        self.tr(
                            "still_stopping_message",
                            "The audio process is still stopping. Please wait a moment "
                            "and close the application again.",
                        ),
                    )
                    event.ignore()
                    return
        self._save_settings()
        event.accept()

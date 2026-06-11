from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtCore import QUrl
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
from app.tts.voice_manager import VoiceInfo, VoiceManager
from app.utils.i18n import Translator
from app.utils.paths import application_root, resolve_app_path, resource_root
from app.workers.generation_worker import GenerationWorker

from .icons import ui_icon
from .voice_manager_dialog import VoiceManagerDialog
from .widgets import FilePicker, LogView, PathPicker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.settings
        self.translator = Translator(str(self.settings.get("ui_language", "en")))
        self.voices: list[VoiceInfo] = []
        self.worker: GenerationWorker | None = None
        self.worker_thread: QThread | None = None
        self.generation_started_at: float | None = None
        self.progress_current = 0
        self.progress_total = 0
        self.last_output_folder: Path | None = None
        self.generation_timer = QTimer(self)
        self.generation_timer.setInterval(1000)
        self.generation_timer.timeout.connect(self._update_generation_time)

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

        helper = QLabel(
            self.tr(
                "voice_help",
                "Voices are discovered from voices/**/*.onnx when the matching "
                ".onnx.json file is present.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addWidget(helper)
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

    def _build_general_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

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

        grid.addWidget(narration_group, 0, 0)
        grid.addWidget(output_group, 0, 1)
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

        voice_id = self.voice_combo.currentData()
        voice = next(
            (candidate for candidate in self.voices if candidate.voice_id == voice_id),
            None,
        )
        if voice is None:
            self._show_error(
                self.tr("missing_voice", "No voice selected"),
                self.tr(
                    "missing_voice_message",
                    "Add a Piper .onnx model and its .onnx.json file to the voices folder.",
                ),
            )
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
            voice_config=voice.as_config(self.speed_spin.value()),
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
            self.settings.get("piper_path", "engines/piper/piper.exe")
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

    def _save_settings(self) -> None:
        output_dir = self.output_picker.path()
        self.settings.update(
            {
                "output_dir": str(output_dir),
                "ui_language": self.ui_language_combo.currentData() or "en",
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

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QComboBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QButtonGroup,
)

from app.ui.icons import ui_icon
from app.ui.storyboard_analysis_sidebar import StoryboardAnalysisSidebar, character_scene_counts, entity_scene_counts


Translate = Callable[..., str]


class VideoStoryboardAnalysisDialog(QDialog):
    """Live, non-blocking audit window for storyboard analysis."""

    cancelRequested = Signal()
    startRequested = Signal(object)
    referenceReady = Signal(object)

    def __init__(
        self,
        tr: Translate,
        parent: QWidget | None = None,
        *,
        request_log_path: str = "",
        provider: str = "LLM",
        model: str = "",
        analysis_process: str = "",
        max_input_characters: int = 4000,
        max_output_tokens: int = 4096,
        replaces_existing: bool = False,
        analysis_choices: dict | None = None,
        resume_available: bool = False,
        maximum_scene_seconds: int = 15,
        audiobook_era: str = "",
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.request_log_path = str(request_log_path or "")
        self.provider = str(provider or "LLM")
        self.model = str(model or "").strip()
        self.analysis_process = analysis_process
        self.replaces_existing = bool(replaces_existing)
        self.analysis_choices = analysis_choices or {}
        self.resume_available = resume_available
        self.maximum_scene_seconds = maximum_scene_seconds
        self.audiobook_era = str(audiobook_era or "")
        from app.core.storyboard_token_usage import AnalysisTokenUsage
        self.token_usage = AnalysisTokenUsage()
        self._reference_worker = None
        self._reference_queue = []
        self._reference_context = None
        self._request_count = 0
        self._running = False
        self._started = False
        self._cancel_requested = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("videoStoryboardAnalysisDialog")
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_analysis_dialog_title",
                "Analyzing audiobook",
            )
        )
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(1320, 900)
        available = self.screen().availableGeometry()
        self.resize(min(self.width(), available.width() - 40), min(self.height(), available.height() - 60))
        self._build_ui()
        self.max_input_characters_spin.setValue(
            max(1000, min(100000, int(max_input_characters or 4000)))
        )
        self.max_output_tokens_spin.setValue(
            max(512, min(131072, int(max_output_tokens or 4096)))
        )
        # Keep direct diagnostic/test construction with an already-created log
        # backwards compatible. The application itself opens this dialog with
        # no log and waits for explicit confirmation.
        if self.request_log_path:
            self._enter_running(emit_start=False)

    def _build_ui(self) -> None:
        shell = QHBoxLayout(self)
        self.sidebar = StoryboardAnalysisSidebar(self.tr_text)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setWidget(self.sidebar)
        sidebar_scroll.setMinimumWidth(290)
        sidebar_scroll.setMaximumWidth(340)
        sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left = QVBoxLayout()
        left.addWidget(sidebar_scroll, 1)
        engine_card = QGroupBox(self.tr_text("analysis_llm_engine", "LLM engine"))
        engine_layout = QVBoxLayout(engine_card)
        self.engine_label = QLabel(f"{self.provider}\n{self.model}")
        self.engine_label.setWordWrap(True)
        self.usage_label = QLabel()
        self.usage_note = QLabel(self.tr_text("analysis_usage_note", "Reported tokens · * partial total"))
        self.usage_note.setWordWrap(True)
        for widget in (self.engine_label, self.usage_label, self.usage_note):
            engine_layout.addWidget(widget)
        left.addWidget(engine_card)
        self._refresh_usage()
        shell.addLayout(left)
        main = QWidget()
        layout = QVBoxLayout(main)
        shell.addWidget(main, 1)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.setup_group = QGroupBox(
            self.tr_text(
                "video_storyboard_analysis_before_start",
                "Before analysis starts",
            )
        )
        setup_layout = QVBoxLayout(self.setup_group)
        explanation = QLabel(
            self.tr_text(
                "storyboard_plan_intro",
                "Choose continuity for this audiobook and optionally generate character reference images afterwards.",
            )
        )
        explanation.setWordWrap(True)
        setup_layout.addWidget(explanation)
        from app.core.storyboard_analysis_review import choices
        selected = choices(self.analysis_choices)
        self.selected_plan = selected["plan"]
        self.plan_buttons = {}
        self.plan_group = QButtonGroup(self)
        self.plan_group.setExclusive(True)
        cards = QHBoxLayout()
        for key, title, description in (
            ("scenes", "Scenes only", "Essays and documentaries\nIndependent illustrations"),
            ("basic", "Basic continuity", "Short stories\nStable appearances"),
            ("full", "Full continuity", "Novels and longer stories\nAppearance changes"),
        ):
            button = QPushButton(self.tr_text("analysis_plan_" + key, title) + "\n\n" +
                                 self.tr_text("analysis_plan_desc_" + key, description))
            button.setCheckable(True)
            button.setMinimumHeight(100)
            button.setStyleSheet("QPushButton:checked { border: 2px solid palette(highlight); font-weight: bold; }")
            self.plan_group.addButton(button)
            self.plan_buttons[key] = button
            button.clicked.connect(lambda _checked=False, plan=key: self._select_plan(plan))
            cards.addWidget(button, 1)
        self.plan_buttons[self.selected_plan].setChecked(True)
        setup_layout.addLayout(cards)
        self.plan_help = QLabel()
        self.plan_help.setWordWrap(True)
        setup_layout.addWidget(self.plan_help)
        entities = QHBoxLayout()
        entities.addWidget(QLabel(self.tr_text("analysis_continuity_options", "Continuity:")))
        self.entity_checks = {}
        for key, label in (("characters", "Characters"), ("locations", "Locations"), ("objects", "Important objects")):
            check = QCheckBox(self.tr_text("analysis_category_" + key, label))
            check.setChecked(selected[key])
            self.entity_checks[key] = check
            entities.addWidget(check)
        setup_layout.addLayout(entities)
        self.references_checkbox = QCheckBox(self.tr_text("analysis_auto_references", "Generate references afterwards for characters in more than one scene"))
        self.references_checkbox.setChecked(False)
        self.references_checkbox.setEnabled(self.entity_checks["characters"].isChecked())
        self.entity_checks["characters"].toggled.connect(self.references_checkbox.setEnabled)
        setup_layout.addWidget(self.references_checkbox)
        self.review_checkbox = QCheckBox(self.tr_text("storyboard_review_choice", "Revisar y editar el resumen antes de generar las escenas"))
        self.review_checkbox.setChecked(bool(self.analysis_choices.get("review")))
        setup_layout.addWidget(self.review_checkbox)
        review_help = QLabel(self.tr_text("storyboard_review_help", "El proceso se pausará para corregir el resumen. Puedes guardarlo y continuar más tarde. En Solo escenas añade un breve resumen visual."))
        review_help.setWordWrap(True)
        setup_layout.addWidget(review_help)
        self.resume_checkbox = QCheckBox(self.tr_text("storyboard_review_resume", "Retomar el borrador de revisión guardado, si coincide con este texto y configuración"))
        self.resume_checkbox.setChecked(self.resume_available)
        self.resume_checkbox.setVisible(self.resume_available)
        setup_layout.addWidget(self.resume_checkbox)
        self._update_plan_help()
        if self.replaces_existing:
            warning = QLabel(
                self.tr_text(
                    "video_storyboard_reanalyze_warning_compact",
                    "Warning: continuing will replace the current scene plan and its frame references. Existing image files will not be deleted.",
                )
            )
            warning.setObjectName("warningLabel")
            warning.setWordWrap(True)
            setup_layout.addWidget(warning)
        limits_form = QFormLayout()
        self.era_edit = QLineEdit(self.audiobook_era)
        self.era_edit.setPlaceholderText(self.tr_text("analysis_era_placeholder", "For example: 1890, Victorian era, ancient Rome"))
        self.era_automatic = QCheckBox(self.tr_text("analysis_era_auto", "Detect historical periods automatically"))
        self.era_automatic.setChecked(selected["era_mode"] != "manual")
        limits_form.addRow(self.era_automatic)
        self.era_scope = QComboBox()
        self.era_scope.addItem(self.tr_text("analysis_era_single", "One period for the whole audiobook"), "auto_single")
        self.era_scope.addItem(self.tr_text("analysis_era_multiple", "Multiple periods / follow the story"), "auto_multiple")
        self.era_scope.setCurrentIndex(1 if selected["era_mode"] == "auto_multiple" else 0)
        limits_form.addRow(self.tr_text("analysis_era_scope", "Historical setting"), self.era_scope)
        limits_form.addRow(self.tr_text("analysis_manual_era", "Manual year / era"), self.era_edit)
        self.era_hint = QLabel(self.tr_text("analysis_era_help", "Manual: leave blank for no period restriction. Automatic: detected periods can be reviewed before creating scenes, independently of character continuity."))
        self.era_hint.setWordWrap(True)
        limits_form.addRow(self.era_hint)
        self.era_automatic.toggled.connect(self._update_era_controls)
        self.era_edit.textChanged.connect(self._update_era_controls)
        for check in self.entity_checks.values():
            check.toggled.connect(self._update_era_controls)
        self._update_era_controls()
        self.maximum_scene_spin = QSpinBox()
        self.maximum_scene_spin.setRange(4, 60)
        self.maximum_scene_spin.setSuffix(" s")
        self.maximum_scene_spin.setValue(max(4, min(60, int(self.maximum_scene_seconds))))
        limits_form.addRow(self.tr_text("video_storyboard_maximum", "Maximum scene"), self.maximum_scene_spin)
        self.max_input_characters_spin = QSpinBox()
        self.max_input_characters_spin.setRange(1000, 100000)
        self.max_input_characters_spin.setSingleStep(1000)
        self.max_input_characters_spin.setSuffix(" characters")
        self.max_input_characters_spin.setToolTip(
            self.tr_text(
                "video_storyboard_analysis_input_size_help",
                "Higher values send more timed narration in each request and reduce the number of calls, but require a model with a larger context window.",
            )
        )
        self.max_output_tokens_spin = QSpinBox()
        self.max_output_tokens_spin.setRange(512, 131072)
        self.max_output_tokens_spin.setSingleStep(1000)
        self.max_output_tokens_spin.setSuffix(" tokens")
        self.max_output_tokens_spin.setToolTip(
            self.tr_text(
                "video_storyboard_analysis_output_size_help",
                "Maximum response budget for each analysis request. The model may finish before reaching it.",
            )
        )
        limits_form.addRow(
            self.tr_text(
                "video_storyboard_analysis_input_size",
                "Audiobook text per request",
            ),
            self.max_input_characters_spin,
        )
        limits_form.addRow(
            self.tr_text(
                "video_storyboard_analysis_output_size",
                "Maximum response tokens",
            ),
            self.max_output_tokens_spin,
        )
        setup_layout.addLayout(limits_form)
        advanced_note = QLabel(
            self.tr_text(
                "video_storyboard_analysis_limits_note",
                "Use larger values only with capable models such as GPT-5.5 and a sufficiently large context window. The defaults are safer for local 8B models.",
            )
        )
        advanced_note.setObjectName("helperLabel")
        advanced_note.setWordWrap(True)
        setup_layout.addWidget(advanced_note)
        setup_buttons = QHBoxLayout()
        setup_buttons.addStretch(1)
        self.continue_button = QPushButton(
            self.tr_text("continue", "Continue")
        )
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setIcon(ui_icon("apply"))
        self.initial_cancel_button = QPushButton(
            self.tr_text("cancel", "Cancel")
        )
        self.initial_cancel_button.setIcon(ui_icon("cancel"))
        setup_buttons.addWidget(self.continue_button)
        setup_buttons.addWidget(self.initial_cancel_button)
        setup_layout.addLayout(setup_buttons)
        self.setup_scroll = QScrollArea()
        self.setup_scroll.setWidgetResizable(True)
        self.setup_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setup_scroll.setWidget(self.setup_group)
        layout.addWidget(self.setup_scroll, 1)

        self.status_label = QLabel(
            self.tr_text(
                "video_storyboard_analysis_preparing",
                "Preparing semantic analysis...",
            )
        )
        self.status_label.setObjectName("sectionTitle")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("helperLabel")
        self.detail_label.setWordWrap(True)
        self.detail_label.hide()
        layout.addWidget(self.detail_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.tabs = QTabWidget()
        self.activity_view = self._log_view()
        self.request_view = self._log_view()
        self.response_view = self._log_view()
        self.tabs.addTab(
            self.activity_view,
            self.tr_text("video_storyboard_analysis_activity_tab", "Activity"),
        )
        self.tabs.addTab(
            self.request_view,
            self.tr_text("video_storyboard_analysis_request_tab", "Request"),
        )
        self.tabs.addTab(
            self.response_view,
            self.tr_text(
                "video_storyboard_analysis_response_tab",
                "Model response",
            ),
        )
        response_label = self.tabs.tabText(self.tabs.indexOf(self.response_view))
        self.tabs.removeTab(self.tabs.indexOf(self.response_view))
        self.tabs.insertTab(0, self.response_view, response_label)
        self.tabs.setCurrentWidget(self.response_view)
        self.tabs.hide()
        layout.addWidget(self.tabs, 1)

        self.recommendation_label = QLabel()
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setTextFormat(Qt.TextFormat.PlainText)
        self.recommendation_label.hide()
        layout.addWidget(self.recommendation_label)
        self.generate_references_button = QPushButton(self.tr_text("analysis_generate_references", "Generate recurring character references"))
        self.generate_references_button.hide()
        self.generate_references_button.clicked.connect(self.start_references)
        layout.addWidget(self.generate_references_button)
        self.character_summary = QTableWidget(0, 3)
        self.character_summary.setHorizontalHeaderLabels([
            self.tr_text("analysis_character", "Character"),
            self.tr_text("analysis_scene_appearances", "Scenes featuring character"),
            self.tr_text("analysis_reference", "Reference image"),
        ])
        self.character_summary.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.character_summary.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.character_summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.character_summary.verticalHeader().hide()
        self.character_summary.hide()
        self.entity_summaries = {}
        for category, label in (("locations", "Location"), ("objects", "Object"), ("eras", "Era / period")):
            table = QTableWidget(0, 2, self)
            table.setHorizontalHeaderLabels([
                self.tr_text("analysis_entity_" + category, label),
                self.tr_text("analysis_entity_scene_count", "Number of scenes"),
            ])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.verticalHeader().hide()
            table.hide()
            self.entity_summaries[category] = table
            if category == "eras":
                table.setColumnCount(3)
                table.setHorizontalHeaderLabels([
                    self.tr_text("analysis_category_eras", "Eras / periods"),
                    self.tr_text("analysis_entity_scene_count", "Number of scenes"),
                    self.tr_text("analysis_era_context", "Visual context"),
                ])
                table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
                table.cellDoubleClicked.connect(self._open_era)
                table.setToolTip(self.tr_text("analysis_era_open", "Double-click a period to review its context and scenes."))

        note = QLabel(
            self.tr_text(
                "video_storyboard_analysis_stream_note",
                "Live responses are shown when supported by the selected LLM provider. Partial plans are saved after every completed block.",
            )
        )
        note.setObjectName("helperLabel")
        note.setWordWrap(True)
        note.hide()
        self.stream_note = note
        layout.addWidget(note)

        request_log_row = QHBoxLayout()
        self.request_log_label = QLabel()
        self.request_log_label.setObjectName("helperLabel")
        self.request_log_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.request_log_label.setWordWrap(True)
        self.open_request_log_button = QPushButton(
            self.tr_text(
                "video_storyboard_analysis_open_request_log",
                "Open raw request / response log",
            )
        )
        self.open_request_log_button.clicked.connect(self._open_request_log)
        request_log_row.addWidget(self.request_log_label, 1)
        request_log_row.addWidget(self.open_request_log_button)
        layout.addLayout(request_log_row)
        self._refresh_request_log_row()
        self.request_log_label.hide()
        self.open_request_log_button.hide()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_continue_button = QPushButton(
            self.tr_text(
                "video_storyboard_analysis_close_continue",
                "Close and continue in background",
            )
        )
        self.close_continue_button.setIcon(ui_icon("close"))
        self.cancel_button = QPushButton(
            self.tr_text(
                "video_storyboard_analysis_cancel",
                "Cancel analysis",
            )
        )
        self.cancel_button.setIcon(ui_icon("stop"))
        buttons.addWidget(self.close_continue_button)
        buttons.addWidget(self.cancel_button)
        self.running_buttons = QWidget()
        self.running_buttons.setLayout(buttons)
        self.running_buttons.hide()
        layout.addWidget(self.running_buttons)

        self.close_continue_button.clicked.connect(self.hide)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.continue_button.clicked.connect(self._start_analysis)
        self.initial_cancel_button.clicked.connect(self.reject)

    def _start_analysis(self) -> None:
        if self._started:
            return
        self._enter_running(emit_start=True)

    def _update_plan_help(self):
        descriptions = {
            "scenes": "For essays and documentaries without recurring protagonists. Historical periods can be manual or automatic.",
            "basic": "Stable character and location appearances. Historical periods can be manual or automatic.",
            "full": "Analyze characters, locations and character appearance changes. Choose entities and historical periods separately.",
        }
        key = self.selected_plan
        self.plan_help.setText(self.tr_text("storyboard_content_help_v2_" + key, descriptions[key]))

    def _update_era_controls(self, *_):
        automatic = self.era_automatic.isChecked()
        self.era_edit.setEnabled(not automatic)
        self.era_scope.setEnabled(automatic)
        if not self._started:
            self.sidebar.configure({**{key: check.isChecked() for key, check in self.entity_checks.items()},
                                    "era_mode": self._era_mode(), "audiobook_era": self.era_edit.text().strip()})

    def _era_mode(self):
        return str(self.era_scope.currentData()) if self.era_automatic.isChecked() else "manual"

    def _open_era(self, row, _column):
        item = self.entity_summaries["eras"].item(row, 0)
        if item is None:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        page = getattr(self.parent(), "video_storyboard_page", None)
        if page is not None:
            page.edit_storyboard_era(identifier)
            self.update_plan({**page.project_state()["plan"], "scenes": page.scenes()}, final=True)
        else:
            from app.ui.storyboard_era_dialog import StoryboardEraDialog
            dialog = StoryboardEraDialog(self.tr_text, self._analysed_plan, identifier, self, read_only=True)
            dialog.open()

    def _select_plan(self, key):
        self.selected_plan = key
        for entity in ("characters", "locations"):
            self.entity_checks[entity].setChecked(key != "scenes")
        self._update_plan_help()

    def _enter_running(self, *, emit_start: bool) -> None:
        self._started = True
        self._running = True
        self.setup_group.hide()
        self.setup_scroll.hide()
        self.sidebar.configure({**{key: check.isChecked() for key, check in self.entity_checks.items()},
                                "era_mode": self._era_mode(), "audiobook_era": self.era_edit.text().strip()})
        for widget in (
            self.status_label,
            self.detail_label,
            self.progress_bar,
            self.tabs,
            self.stream_note,
            self.running_buttons,
        ):
            widget.show()
        self._refresh_request_log_row()
        self.append_activity(
            self.tr_text(
                "video_storyboard_analysis_started",
                "Analysis started. Releasing image-model memory before contacting the LLM provider.",
            )
        )
        if emit_start:
            self.startRequested.emit(
                {
                    "max_input_characters": self.max_input_characters_spin.value(),
                    "max_output_tokens": self.max_output_tokens_spin.value(),
                    "analysis_choices": {"plan": self.selected_plan, "review": self.review_checkbox.isChecked(),
                                         "era_mode": self._era_mode(),
                                         **{key: check.isChecked() for key, check in self.entity_checks.items()}},
                    "maximum_scene_seconds": self.maximum_scene_spin.value(),
                    "audiobook_era": self.era_edit.text().strip(),
                    "generate_character_references": self.references_checkbox.isEnabled() and self.references_checkbox.isChecked(),
                    "resume_review": self.resume_checkbox.isChecked(),
                }
            )

    def set_request_log_path(self, path: str) -> None:
        self.request_log_path = str(path or "")
        self._refresh_request_log_row()

    @staticmethod
    def _log_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setMaximumBlockCount(20000)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        return view

    def set_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        self.status_label.setText(message)
        self.detail_label.setText(
            self.tr_text(
                "video_storyboard_analysis_block_detail",
                "Block {current} of {total}",
                current=current,
                total=total,
            )
        )
        self.progress_bar.setValue(
            round(max(0, current - 1) / max(1, total) * 100)
        )
        self.append_activity(message)

    def append_trace(self, raw_event: object) -> None:
        if not isinstance(raw_event, dict):
            return
        event: dict[str, Any] = raw_event
        kind = str(event.get("kind") or "")
        if kind == "raw_response":
            self.token_usage.add(event.get("raw_response"))
            self._refresh_usage()
        if kind == "request":
            self.engine_label.setText(f"{event.get('provider', self.provider)}\n{event.get('model', self.model)}")
        if kind in {"request", "status"}:
            self.sidebar.set_stage(str(event.get("label") or event.get("message") or ""))
        if kind == "block":
            phase = str(event.get("phase") or "scenes")
            if phase == "discovery":
                phase_label = self.tr_text(
                    "video_storyboard_analysis_discovery_phase",
                    "Continuity discovery",
                )
            elif phase == "continuity":
                phase_label = self.tr_text(
                    "video_storyboard_analysis_continuity_phase",
                    "Continuity analyzer",
                )
            elif phase == "binding":
                phase_label = self.tr_text(
                    "video_storyboard_analysis_binding_phase",
                    "Unit binder",
                )
            else:
                phase_label = self.tr_text(
                    "video_storyboard_analysis_scene_phase",
                    "Scene composition pass",
                )
            self.append_activity(
                self.tr_text(
                    "video_storyboard_analysis_phase_block",
                    "{phase}: prepared block {current}/{total} ({start:.2f}s–{end:.2f}s).",
                    phase=phase_label,
                    current=event.get("current", 0),
                    total=event.get("total", 0),
                    start=float(event.get("start_seconds") or 0.0),
                    end=float(event.get("end_seconds") or 0.0),
                )
            )
        elif kind == "request":
            self._request_count += 1
            self.append_activity(
                self.tr_text(
                    "video_storyboard_analysis_request_work",
                    "Sending {label} to {model}: {items} {item_type}, up to {tokens} output tokens.",
                    label=event.get("label", ""),
                    model=event.get("model", event.get("provider", "LLM")),
                    items=event.get(
                        "work_item_count", event.get("scene_count", 0)
                    ),
                    item_type=event.get("work_item_type", "fixed scenes"),
                    tokens=event.get("output_tokens", 0),
                )
            )
            raw_request = event.get("raw_request")
            if isinstance(raw_request, dict):
                request_text = json.dumps(
                    raw_request,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                request_text = str(event.get("prompt") or "")
            endpoint = str(event.get("endpoint") or "(not reported)")
            transport = str(event.get("transport") or "http")
            request_action = "SDK CALL" if transport == "sdk" else "POST"
            request_header = (
                f"REQUEST {self._request_count}: {event.get('label', '')}\n"
                f"{request_action} {endpoint}\n"
                f"Provider: {event.get('provider', 'LLM')}\n"
                f"Attempt: {event.get('attempt', 1)}\n\n"
            )
            stream_header = f"\n\n{'=' * 24} {event.get('label', '')} {'=' * 24}\n"
            if self.response_view.toPlainText():
                self.response_view.appendPlainText(stream_header)
            if request_text:
                if self.request_view.toPlainText():
                    self.request_view.appendPlainText("\n\n" + "=" * 72 + "\n")
                self.request_view.appendPlainText(request_header + request_text)
        elif kind == "content":
            self._append_stream(self.response_view, str(event.get("text") or ""))
        elif kind == "retry":
            self.append_activity(
                self.tr_text(
                    "video_storyboard_analysis_retry",
                    "The response was incomplete ({reason}). Retrying automatically with a compact anti-repetition request...",
                    reason=event.get("reason", "invalid JSON"),
                )
            )
        elif kind == "response":
            self.append_activity(
                self.tr_text(
                    "video_storyboard_analysis_response_complete",
                    "Received and validated {characters} response characters for {label}.",
                    characters=event.get("characters", 0),
                    label=event.get("label", ""),
                )
            )
        elif kind == "warning":
            self.append_activity(
                self.tr_text(
                    "video_storyboard_analysis_warning",
                    "Warning: {message}",
                    message=str(event.get("message") or "Continuity issue"),
                )
            )
        elif kind == "error":
            message = str(event.get("message") or "LLM provider error")
            self.append_activity(message)
            raw_error = event.get("raw_error")
            self.response_view.appendPlainText(
                "\n\nPROVIDER ERROR\n"
                + (
                    json.dumps(raw_error, ensure_ascii=False, indent=2)
                    if isinstance(raw_error, (dict, list))
                    else message
                )
            )

    def append_activity(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_view.appendPlainText(f"[{timestamp}] {message}")

    def _refresh_request_log_row(self) -> None:
        visible = self._started and bool(self.request_log_path)
        self.request_log_label.setVisible(visible)
        self.open_request_log_button.setVisible(visible)
        if visible:
            self.request_log_label.setText(
                self.tr_text(
                    "video_storyboard_analysis_request_log_path",
                    "Raw request / response log: {path}",
                    path=self.request_log_path,
                )
            )

    def _open_request_log(self) -> None:
        if self.request_log_path:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(self.request_log_path).resolve()))
            )

    def mark_partial_saved(
        self,
        current: int,
        total: int,
        scenes: int,
        *,
        phase: str = "scenes",
        characters: int = 0,
        locations: int = 0,
    ) -> None:
        self.progress_bar.setValue(round(current / max(1, total) * 100))
        if phase == "discovery":
            message = self.tr_text(
                "video_storyboard_discovery_partial_saved",
                "Discovery block {current}/{total} analyzed and saved.",
                current=current,
                total=total,
            )
        elif phase == "continuity":
            message = self.tr_text(
                "video_storyboard_continuity_partial_saved",
                "Continuity block {current}/{total} validated and saved ({characters} characters, {locations} locations).",
                current=current,
                total=total,
                characters=characters,
                locations=locations,
            )
        else:
            message = self.tr_text(
                "video_storyboard_analysis_partial_saved",
                "Block {current}/{total} validated and saved ({scenes} frames currently planned).",
                current=current,
                total=total,
                scenes=scenes,
            )
        self.append_activity(message)

    def set_finished(self, success: bool, message: str) -> None:
        self._started = True
        self._running = False
        self.status_label.setText(message)
        if success:
            self.progress_bar.setValue(100)
        else:
            self.sidebar.stop()
        self.append_activity(message)
        self.cancel_button.setEnabled(False)
        self.close_continue_button.setText(self.tr_text("close", "Close"))

    def update_plan(self, plan: dict, *, final: bool = False) -> None:
        self._analysed_plan = plan
        self.sidebar.update_plan(plan, final=final)
        if not final:
            return
        continuity = plan.get("continuity") or {}
        for category, title in (("locations", "Location appearances"), ("objects", "Object appearances"), ("eras", "Era appearances")):
            table = self.entity_summaries[category]
            enabled = self._era_mode() != "manual" if category == "eras" else self.entity_checks[category].isChecked()
            # Existing records also identify analysed entities when showing a restored result.
            analysed = bool(continuity.get(category)) or (enabled and category in continuity)
            if not analysed:
                tab_index = self.tabs.indexOf(table)
                if tab_index >= 0:
                    self.tabs.removeTab(tab_index)
                table.hide()
                table.setRowCount(0)
                continue
            entity_rows = entity_scene_counts(plan, category)
            table.setRowCount(len(entity_rows))
            for index, (name, count) in enumerate(entity_rows):
                table.setItem(index, 0, QTableWidgetItem(name))
                table.setItem(index, 1, QTableWidgetItem(str(count)))
                if category == "eras":
                    record = next(r for r in continuity["eras"] if (r.get("name") or r.get("description") or r.get("id")) == name)
                    table.item(index, 0).setData(Qt.ItemDataRole.UserRole, record["id"])
                    table.setItem(index, 2, QTableWidgetItem(str(record.get("material_culture") or "")))
            if self.tabs.indexOf(table) < 0:
                self.tabs.addTab(table, self.tr_text("analysis_appearances_" + category, title))
        rows = character_scene_counts(plan)
        self.character_summary.setRowCount(len(rows))
        for index, (name, count) in enumerate(rows):
            self.character_summary.setItem(index, 0, QTableWidgetItem(name))
            self.character_summary.setItem(index, 1, QTableWidgetItem(str(count)))
        if self.tabs.indexOf(self.character_summary) < 0:
            self.tabs.insertTab(3, self.character_summary, self.tr_text("analysis_summary_tab", "Character appearances"))
        recurring = [name for name, count in rows if count > 1]
        if recurring:
            text = self.tr_text("analysis_reference_recommendation",
                "For image generation, create reference images for recurring characters first: {names}. "
                "Reuse them in supported image workflows to improve visual continuity.", names=", ".join(recurring))
        else:
            text = self.tr_text("analysis_no_recurring", "No recurring characters identified. Scene counts are available in Character appearances.")
        self.recommendation_label.setText(text)
        self.recommendation_label.show()

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _request_cancel(self) -> None:
        if self._reference_worker is not None:
            self._reference_queue.clear()
            self._reference_worker.cancelled.set()
            self.cancel_button.setEnabled(False)
            return
        if not self._running or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        message = self.tr_text(
            "video_storyboard_analysis_cancelling",
            "Cancelling analysis after the current LLM request...",
        )
        self.status_label.setText(message)
        self.append_activity(message)
        self.cancelRequested.emit()

    @staticmethod
    def _append_stream(view: QPlainTextEdit, text: str) -> None:
        if not text:
            return
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        view.setTextCursor(cursor)
        view.ensureCursorVisible()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._reference_worker is not None:
            event.ignore()
            return
        if self._running:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

    def _refresh_usage(self):
        self.usage_label.setText(self.tr_text("analysis_usage_totals", "Input: {input}\nOutput: {output}",
            input=self.token_usage.display("input"), output=self.token_usage.display("output")))

    def configure_references(self, plan, settings, project_dir):
        from copy import deepcopy
        self._reference_context = (deepcopy(plan), deepcopy(settings), Path(project_dir))
        self.generate_references_button.setVisible(any(n > 1 for _, n in character_scene_counts(plan)))
        for record in plan.get("continuity", {}).get("characters", []):
            self._show_reference(record, str(record.get("reference_image_path") or ""))

    def _show_reference(self, record, path):
        if not path or not Path(path).is_file():
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        for row in range(self.character_summary.rowCount()):
            if self.character_summary.item(row, 0).text() == str(record.get("name") or record.get("id") or "—"):
                label = QLabel()
                label.setPixmap(pixmap.scaled(144, 81, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                label.setToolTip(path)
                self.character_summary.setCellWidget(row, 2, label)
                self.character_summary.setRowHeight(row, 85)
        self.character_summary.setColumnWidth(2, 150)

    def start_references(self):
        if self._reference_worker is not None or self._reference_context is None:
            return
        if not getattr(self, "reference_start_allowed", lambda: True)():
            self.append_activity(self.tr_text("analysis_reference_unavailable", "Return to the analyzed project and wait for other image operations to finish."))
            return
        plan, _, _ = self._reference_context
        self._reference_queue = []
        for record in plan.get("continuity", {}).get("characters", []):
            count = character_scene_counts({"continuity": {"characters": [record]}, "scenes": plan.get("scenes", [])})[0][1]
            path = str(record.get("reference_image_path") or "")
            if count > 1 and not (path and Path(path).is_file()):
                self._reference_queue.append(record)
        self.tabs.setCurrentWidget(self.character_summary)
        self.show_and_raise()
        self._next_reference()

    def _next_reference(self):
        if not self._reference_queue:
            self.generate_references_button.setEnabled(True)
            self.close_continue_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self.tr_text("analysis_references_finished", "Reference generation finished. Review the images and any errors in Activity."))
            return
        import uuid
        from app.workers.character_image_worker import CharacterImageWorker
        record = self._reference_queue.pop(0)
        _, settings, directory = self._reference_context
        target = directory / "storyboard" / "references" / f"{uuid.uuid4().hex}.png"
        description = str((record.get("states") or [{}])[0].get("description") or record.get("identity_description") or "")
        prompt = str(record.get("reference_image_prompt") or (
            f"Single full-body character reference portrait of {record.get('name', '')}. {description}. "
            "Preserve the described hair and clothing colors and details. Neutral pose, face clearly visible, plain light gray background. No text, no panels, no collage."))
        self._reference_record = record
        self._reference_worker = CharacterImageWorker(prompt, settings, target, self)
        self._reference_worker.finished.connect(self._reference_finished)
        self.generate_references_button.setEnabled(False)
        self.close_continue_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        message = self.tr_text("analysis_reference_generating", "Generating reference: {name}", name=record.get("name", ""))
        self.status_label.setText(message)
        self.append_activity(message)
        self._reference_worker.start()

    def _reference_finished(self):
        worker = self._reference_worker
        record = self._reference_record
        self._reference_worker = None
        if worker.error:
            self.append_activity(f"{record.get('name', '')}: {worker.error}")
        elif not worker.cancelled.is_set() and worker.target.is_file():
            record["reference_image_path"] = str(worker.target)
            record["reference_image_prompt"] = worker.prompt
            self._show_reference(record, str(worker.target))
            self.referenceReady.emit(dict(record))
        worker.deleteLater()
        self._next_reference()

    def reject(self):
        if self._reference_worker is not None:
            return
        if self._running:
            self.hide()
            return
        super().reject()

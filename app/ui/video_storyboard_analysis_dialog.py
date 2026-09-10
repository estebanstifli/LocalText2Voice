from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QCheckBox,
)

from app.ui.icons import ui_icon


Translate = Callable[..., str]


class VideoStoryboardAnalysisDialog(QDialog):
    """Live, non-blocking audit window for storyboard analysis."""

    cancelRequested = Signal()
    startRequested = Signal(object)

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
        self.resize(980, 850)
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
        layout = QVBoxLayout(self)
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
                "Choose how much continuity this audiobook needs. This step plans scenes; it does not generate images.",
            )
        )
        explanation.setWordWrap(True)
        setup_layout.addWidget(explanation)
        self.content_plan_combo = QComboBox()
        self.plan_help = QLabel()
        self.plan_help.setWordWrap(True)
        for key, label in (("scenes", "Solo escenas"), ("basic", "Continuidad básica"), ("full", "Continuidad completa")):
            self.content_plan_combo.addItem(self.tr_text("storyboard_content_" + key, label), key)
        self.content_plan_combo.setCurrentIndex(max(0, self.content_plan_combo.findData(self.analysis_choices.get("plan", "full"))))
        self.content_plan_combo.currentIndexChanged.connect(self._update_plan_help)
        setup_layout.addWidget(self.content_plan_combo)
        setup_layout.addWidget(self.plan_help)
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
        model_label = QLabel(
            self.tr_text(
                "video_storyboard_analysis_selected_model",
                "AI model: {provider} · {model}",
                provider=self.provider,
                model=self.model or self.tr_text("unknown", "Unknown"),
            )
        )
        model_label.setObjectName("sectionTitle")
        model_label.setWordWrap(True)
        setup_layout.addWidget(model_label)
        if self.analysis_process:
            setup_layout.addWidget(QLabel(self.tr_text("continuity_selected_process", "Analysis process: {process}", process=self.analysis_process)))
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
        layout.addWidget(self.setup_group)

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
        self.tabs.hide()
        layout.addWidget(self.tabs, 1)

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
            "scenes": "Para divulgación y documentales sin protagonistas recurrentes. Crea escenas sin fichas de personajes ni lugares. Mantiene estilo y época manual.",
            "basic": "Para cuentos sencillos. Mantiene una apariencia estable de personajes y lugares, sin estados temporales ni detección de épocas.",
            "full": "Crea perfiles iniciales editables de personajes y lugares. En este flujo simplificado los cambios temporales y la época se ajustan manualmente; no se extraen durante la conversión de los resúmenes.",
        }
        key = self.content_plan_combo.currentData()
        self.plan_help.setText(self.tr_text("storyboard_content_help_" + key, descriptions[key]))

    def _enter_running(self, *, emit_start: bool) -> None:
        self._started = True
        self._running = True
        self.setup_group.hide()
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
                    "analysis_choices": {"plan": self.content_plan_combo.currentData(), "review": self.review_checkbox.isChecked()},
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
        self.append_activity(message)
        self.cancel_button.setEnabled(False)
        self.close_continue_button.setText(self.tr_text("close", "Close"))

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _request_cancel(self) -> None:
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
        if self._running:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

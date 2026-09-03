from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon


Translate = Callable[..., str]


class VideoStoryboardAnalysisDialog(QDialog):
    """Live, non-blocking audit window for storyboard analysis."""

    cancelRequested = Signal()

    def __init__(
        self,
        tr: Translate,
        parent: QWidget | None = None,
        *,
        request_log_path: str = "",
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.request_log_path = str(request_log_path or "")
        self._request_count = 0
        self._running = True
        self._cancel_requested = False
        self.setObjectName("videoStoryboardAnalysisDialog")
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_analysis_dialog_title",
                "Analyzing audiobook",
            )
        )
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(980, 700)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.status_label = QLabel(
            self.tr_text(
                "video_storyboard_analysis_preparing",
                "Preparing semantic analysis...",
            )
        )
        self.status_label.setObjectName("sectionTitle")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("helperLabel")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
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
                "Structured response",
            ),
        )
        layout.addWidget(self.tabs, 1)

        note = QLabel(
            self.tr_text(
                "video_storyboard_analysis_stream_note",
                "Live responses are shown when supported by the selected LLM provider. Partial plans are saved after every completed block.",
            )
        )
        note.setObjectName("helperLabel")
        note.setWordWrap(True)
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
        layout.addLayout(buttons)

        self.close_continue_button.clicked.connect(self.hide)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.append_activity(
            self.tr_text(
                "video_storyboard_analysis_started",
                "Analysis started. Releasing image-model memory before contacting the LLM provider.",
            )
        )

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
            if phase == "continuity":
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
        visible = bool(self.request_log_path)
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
        if phase == "continuity":
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

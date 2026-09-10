from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon

Translate = Callable[..., str]


class VideoStoryboardFrameBatchDialog(QDialog):
    """Confirmation and live progress for a batch of storyboard frames."""

    startRequested = Signal(bool)
    cancelRequested = Signal()
    settingsRequested = Signal()

    def __init__(
        self,
        tr: Translate,
        *,
        mode: str,
        total_count: int,
        existing_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.mode = "regenerate" if mode == "regenerate" else "generate"
        self.total_count = max(0, int(total_count))
        self.existing_count = max(0, int(existing_count))
        self._running = False
        self._finished = False
        self._cancel_requested = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_regenerate_all_dialog_title"
                if self.mode == "regenerate"
                else "video_storyboard_generate_frames_dialog_title",
                "Regenerate storyboard frames"
                if self.mode == "regenerate"
                else "Generate storyboard frames",
            )
        )
        self.resize(760, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(
            self.tr_text(
                "video_storyboard_regenerate_all_dialog_heading"
                if self.mode == "regenerate"
                else "video_storyboard_generate_frames_dialog_heading",
                "Regenerate the storyboard images"
                if self.mode == "regenerate"
                else "Generate the storyboard images",
            )
        )
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        if self.mode == "regenerate":
            explanation = self.tr_text(
                "video_storyboard_regenerate_all_dialog_info",
                "Images will use Project visual and video overrides. Any value not overridden by this project falls back to Video Storyboard settings.",
            )
        else:
            explanation = self.tr_text(
                "video_storyboard_generate_frames_dialog_info",
                "Images will be created for the planned scenes using the image provider and generation parameters configured in Video Storyboard settings.",
            )
        info = QLabel(explanation)
        info.setWordWrap(True)
        info.setObjectName("helperLabel")
        layout.addWidget(info)

        settings_row = QHBoxLayout()
        summary = QLabel(
            self.tr_text(
                "video_storyboard_frame_batch_summary",
                "Scenes: {total} · Existing images: {existing}",
                total=self.total_count,
                existing=self.existing_count,
            )
        )
        summary.setObjectName("helperLabel")
        self.settings_button = QPushButton(
            self.tr_text(
                "video_storyboard_open_settings",
                "Open Video Storyboard settings",
            )
        )
        self.settings_button.setIcon(ui_icon("settings"))
        self.settings_button.setFlat(True)
        settings_row.addWidget(summary, 1)
        settings_row.addWidget(self.settings_button)
        layout.addLayout(settings_row)

        self.overwrite_checkbox = QCheckBox(
            self.tr_text(
                "video_storyboard_overwrite_all_frames",
                "Overwrite all existing frames",
            )
        )
        self.overwrite_checkbox.setChecked(self.mode == "regenerate")
        self.overwrite_checkbox.setVisible(self.existing_count > 0)
        layout.addWidget(self.overwrite_checkbox)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(
            self.tr_text(
                "video_storyboard_frame_batch_ready",
                "Review the options, then start generation.",
            )
        )
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("helperLabel")
        layout.addWidget(self.status_label)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        self.log_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_frame_batch_log_placeholder",
                "Detailed generation activity will appear here.",
            )
        )
        layout.addWidget(self.log_edit, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.start_button = QPushButton(
            self.tr_text(
                "video_storyboard_start_regeneration"
                if self.mode == "regenerate"
                else "video_storyboard_start_generation",
                "Start regeneration" if self.mode == "regenerate" else "Start generation",
            )
        )
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(ui_icon("bolt"))
        self.close_button = QPushButton(
            self.tr_text(
                "video_storyboard_close_continue_background",
                "Close and continue in background",
            )
        )
        self.close_button.setIcon(ui_icon("close"))
        self.cancel_process_button = QPushButton(
            self.tr_text(
                "video_storyboard_cancel_frame_generation",
                "Cancel generation",
            )
        )
        self.cancel_process_button.setIcon(ui_icon("stop"))
        self.cancel_button = QPushButton(self.tr_text("cancel", "Cancel"))
        self.cancel_button.setIcon(ui_icon("cancel"))
        self.close_button.hide()
        self.cancel_process_button.hide()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.cancel_process_button)
        layout.addLayout(buttons)

        self.settings_button.clicked.connect(self._open_settings)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)
        self.close_button.clicked.connect(self._close_or_hide)
        self.cancel_process_button.clicked.connect(self._cancel_process)

    def _start(self) -> None:
        if self._running or self._finished:
            return
        self._running = True
        self.start_button.hide()
        self.cancel_button.hide()
        self.close_button.show()
        self.cancel_process_button.show()
        self.settings_button.setEnabled(False)
        self.overwrite_checkbox.setEnabled(False)
        message = self.tr_text(
            "video_storyboard_frame_batch_starting",
            "Starting image generation...",
        )
        self.status_label.setText(message)
        self.append_log(message)
        self.startRequested.emit(self.overwrite_checkbox.isChecked())

    def _open_settings(self) -> None:
        self.hide()
        self.settingsRequested.emit()

    def _close_or_hide(self) -> None:
        if self._running:
            self.hide()
        else:
            self.close()

    def set_progress(
        self,
        current: int,
        total: int,
        message: str,
        percentage: int | None = None,
    ) -> None:
        if self._finished:
            return
        self.status_label.setText(message)
        if percentage is None:
            percentage = (
                round(max(0, current - 1) / max(1, total) * 100)
                if current and total
                else 0
            )
        self.progress_bar.setValue(max(0, min(100, int(percentage))))
        self.append_log(message)

    def frame_ready(self, scene_id: str, completed: int, total: int) -> None:
        self.progress_bar.setValue(round(completed / max(1, total) * 100))
        self.append_log(
            self.tr_text(
                "video_storyboard_frame_batch_scene_ready",
                "Scene {scene}: image ready ({completed}/{total}).",
                scene=scene_id,
                completed=completed,
                total=total,
            )
        )

    def set_finished(self, success: bool, message: str) -> None:
        self._running = False
        self._finished = True
        self.status_label.setText(message)
        if success:
            self.progress_bar.setValue(100)
        self.append_log(message)
        self.close_button.setText(self.tr_text("close", "Close"))
        self.close_button.show()
        self.cancel_process_button.hide()
        if not self.isVisible():
            self.close()

    def append_log(self, message: str) -> None:
        if not message:
            return
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{timestamp}] {message}")

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _cancel_process(self) -> None:
        if not self._running or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_process_button.setEnabled(False)
        message = self.tr_text(
            "video_storyboard_cancelling_frame_generation",
            "Cancelling after the current model request...",
        )
        self.status_label.setText(message)
        self.append_log(message)
        self.cancelRequested.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

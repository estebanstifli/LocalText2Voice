from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QRadioButton,
    QLineEdit,
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


def parse_scene_selection(text: str, total: int) -> list[int]:
    """Return unique zero-based indices in timeline order; reject invalid ranges."""
    selected = set()
    for part in text.split(','):
        match = re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?', part)
        if not match:
            raise ValueError('Invalid scene selection')
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= total:
            raise ValueError('Scene out of range')
        selected.update(range(first - 1, last))
    return sorted(selected)


class VideoStoryboardFrameBatchDialog(QDialog):
    """Confirmation and live progress for a batch of storyboard frames."""

    startRequested = Signal(bool)
    cancelRequested = Signal()

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
        self.selected_scene_indices = list(range(self.total_count))
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
        self.heading_label = title
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        if self.mode == "regenerate":
            explanation = self.tr_text(
                "video_storyboard_regenerate_all_dialog_info",
                "Generate images using Project visual direction and each scene’s prompt and frame overrides. Missing project values use the configured defaults. Select overwrite to replace existing images in the selected scenes.",
            )
        else:
            explanation = self.tr_text(
                "video_storyboard_generate_frames_dialog_info",
                "Generate images for the planned scenes using Project visual direction: visual style, characters, locations, objects and project overrides. Each scene keeps its own prompt and frame overrides. Missing project values use the configured defaults. Existing images in the selection are kept unless overwrite is enabled.",
            )
        info = QLabel(explanation)
        self.info_label = info
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
        self.summary_label = summary
        summary.setObjectName("helperLabel")
        settings_row.addWidget(summary, 1)
        layout.addLayout(settings_row)

        selection_row = QHBoxLayout()
        self.all_radio = QRadioButton(self.tr_text("video_storyboard_frames_all", "All"))
        self.scenes_radio = QRadioButton(self.tr_text("video_storyboard_frames_scenes", "Scenes:"))
        self.scene_selection_edit = QLineEdit()
        self.scene_selection_edit.setPlaceholderText("3-10, 12, 15-18")
        self.scene_selection_edit.setAccessibleName(self.scenes_radio.text())
        self.all_radio.setChecked(True)
        self.scene_selection_edit.setEnabled(False)
        self.scenes_radio.toggled.connect(self.scene_selection_edit.setEnabled)
        selection_row.addWidget(self.all_radio)
        selection_row.addWidget(self.scenes_radio)
        selection_row.addWidget(self.scene_selection_edit, 1)
        layout.addLayout(selection_row)

        self.overwrite_checkbox = QCheckBox(
            self.tr_text(
                "video_storyboard_overwrite_selected_frames",
                "Overwrite existing frames in selection",
            )
        )
        self.overwrite_checkbox.setChecked(self.mode == "regenerate")
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

        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)
        self.close_button.clicked.connect(self._close_or_hide)
        self.cancel_process_button.clicked.connect(self._cancel_process)

    def _start(self) -> None:
        if self._running or self._finished:
            return
        try:
            self.selected_scene_indices = (list(range(self.total_count)) if self.all_radio.isChecked()
                                           else parse_scene_selection(self.scene_selection_edit.text(), self.total_count))
            if not self.selected_scene_indices:
                raise ValueError()
        except ValueError:
            self.status_label.setText(self.tr_text(
                "video_storyboard_frames_selection_error",
                "Enter scenes between 1 and {total}, for example: 3-10 or 3,7,8,10.",
                total=self.total_count))
            self.scene_selection_edit.setFocus()
            return
        self.all_radio.setEnabled(False)
        self.scenes_radio.setEnabled(False)
        self.scene_selection_edit.setEnabled(False)
        self._running = True
        self.start_button.hide()
        self.cancel_button.hide()
        self.close_button.show()
        self.cancel_process_button.show()
        self.overwrite_checkbox.setEnabled(False)
        message = self._starting_message()
        self.status_label.setText(message)
        self.append_log(message)
        self.startRequested.emit(self.overwrite_checkbox.isChecked())

    def _starting_message(self) -> str:
        return self.tr_text("video_storyboard_frame_batch_starting", "Starting image generation...")

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

    def reject(self) -> None:
        if self._running:
            self.hide()
            return
        super().reject()

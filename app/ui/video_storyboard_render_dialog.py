from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
)


class VideoStoryboardRenderDialog(QDialog):
    """Render controls and progress, retained while rendering in the background."""

    startRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, tr, video, parent=None):
        super().__init__(parent)
        self.tr_text = tr
        self._running = False
        self._finished = False
        self._last_progress = None
        self.setWindowTitle(tr("storyboard_render_options", "Final video settings"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fps = QSpinBox()
        self.fps.setRange(12, 60)
        self.fps.setValue(int(video.get("fps") or 30))
        self.threads = QSpinBox()
        self.threads.setRange(1, max(1, min(64, os.cpu_count() or 1)))
        self.threads.setValue(min(2, self.threads.maximum()))
        form.addRow(tr("storyboard_render_fps", "Frames per second"), self.fps)
        form.addRow(tr("storyboard_render_threads", "Encoding threads"), self.threads)
        layout.addLayout(form)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        layout.addWidget(self.log_edit, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.start_button = QPushButton(tr("storyboard_render_start", "Render video"))
        self.start_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton(tr("cancel", "Cancel"))
        self.close_button = QPushButton(tr("video_storyboard_close_continue_background", "Close and continue in background"))
        self.cancel_process_button = QPushButton(tr("cancel", "Cancel"))
        for button in (self.start_button, self.cancel_button, self.close_button, self.cancel_process_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        self.close_button.hide()
        self.cancel_process_button.hide()
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)
        self.close_button.clicked.connect(self.close)
        self.cancel_process_button.clicked.connect(self._cancel_process)
        layout.addLayout(buttons)

    def values(self):
        return {"fps": self.fps.value(), "threads": self.threads.value()}

    def _start(self):
        if self._running or self._finished:
            return
        self._running = True
        self.fps.setEnabled(False)
        self.threads.setEnabled(False)
        self.start_button.hide()
        self.cancel_button.hide()
        self.close_button.show()
        self.cancel_process_button.show()
        self.set_progress(self.tr_text("video_storyboard_rendering", "Rendering final video..."), 0)
        self.startRequested.emit()

    def append_log(self, message):
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{timestamp}] {message}")

    def set_progress(self, message, percentage):
        if self._finished:
            return
        self.status_label.setText(message)
        self.progress_bar.setValue(max(0, min(100, percentage)))
        progress = (message, percentage)
        if progress != self._last_progress:
            self.append_log(f"{percentage}% · {message}")
            self._last_progress = progress

    def set_finished(self, success, message):
        self._running = False
        self._finished = True
        self.status_label.setText(message)
        self.append_log(message)
        if success:
            self.progress_bar.setValue(100)
        self.cancel_process_button.hide()
        self.close_button.setText(self.tr_text("close", "Close"))

    def _cancel_process(self):
        if not self._running or not self.cancel_process_button.isEnabled():
            return
        self.cancel_process_button.setEnabled(False)
        message = self.tr_text("storyboard_render_cancelling", "Cancelling video rendering...")
        self.status_label.setText(message)
        self.append_log(message)
        self.cancelRequested.emit()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def reject(self):
        if self._running:
            self.hide()
        else:
            super().reject()

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)

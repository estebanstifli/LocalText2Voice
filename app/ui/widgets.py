from __future__ import annotations

import re
from html import escape
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from .icons import ui_icon


class PathPicker(QWidget):
    path_changed = Signal(str)

    def __init__(
        self,
        button_text: str,
        initial_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path_edit = QLineEdit(initial_path)
        self.browse_button = QPushButton(button_text)
        self.browse_button.setIcon(ui_icon("folder"))
        self.browse_button.clicked.connect(self._browse)
        self.path_edit.textChanged.connect(self.path_changed.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.path_edit, 1)
        layout.addWidget(self.browse_button)

    def path(self) -> Path:
        return Path(self.path_edit.text().strip()).expanduser()

    def set_path(self, path: str | Path) -> None:
        self.path_edit.setText(str(path))

    def _browse(self) -> None:
        initial = self.path_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            self.browse_button.text(),
            initial,
        )
        if selected:
            self.set_path(selected)


class FilePicker(QWidget):
    path_changed = Signal(str)

    def __init__(
        self,
        button_text: str,
        file_filter: str,
        initial_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.file_filter = file_filter
        self.path_edit = QLineEdit(initial_path)
        self.browse_button = QPushButton(button_text)
        self.browse_button.setIcon(ui_icon("file"))
        self.browse_button.clicked.connect(self._browse)
        self.path_edit.textChanged.connect(self.path_changed.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.path_edit, 1)
        layout.addWidget(self.browse_button)

    def path(self) -> Path | None:
        value = self.path_edit.text().strip()
        return Path(value).expanduser() if value else None

    def set_path(self, path: str | Path | None) -> None:
        self.path_edit.setText(str(path) if path else "")

    def _browse(self) -> None:
        initial = self.path_edit.text().strip()
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self.browse_button.text(),
            initial,
            self.file_filter,
        )
        if selected:
            self.set_path(selected)


class LogView(QTextEdit):
    TIMESTAMP_PATTERN = re.compile(r"^\[\d{2}:\d{2}:\d{2}(?:\.\d{3})?\]")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.document().setMaximumBlockCount(1500)

    def append_event(self, message: str) -> None:
        for line in self._timestamp_message(message).splitlines() or [""]:
            self._append_styled_line(line)
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def refresh_theme(self) -> None:
        """Recolor existing entries without discarding the current log."""

        lines = self.toPlainText().splitlines()
        self.clear()
        for line in lines:
            self._append_styled_line(line)
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _append_styled_line(self, line: str) -> None:
        self.append(
            f'<span style="color:{self._line_color(line)}">'
            f"{escape(line)}</span>"
        )

    @classmethod
    def _timestamp_message(cls, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines = str(message).splitlines() or [""]
        stamped_lines: list[str] = []
        for line in lines:
            if cls.TIMESTAMP_PATTERN.match(line):
                stamped_lines.append(line)
            else:
                stamped_lines.append(f"[{timestamp}] {line}")
        return "\n".join(stamped_lines)

    @staticmethod
    def _line_color(line: str) -> str:
        normalized = line.casefold()
        application = QApplication.instance()
        dark = bool(
            application is not None
            and application.property("uiTheme") == "dark"
        )
        error_markers = (
            " error",
            "failed",
            "failure",
            "fatal",
            "exception",
            "traceback",
            "missing",
            "could not",
            "not found",
            "invalid",
        )
        warning_markers = (
            " warning",
            "warn",
            "ignored",
            "fallback",
            "falling back",
            "skipped",
            "unknown",
            "unsupported",
            "deprecated",
        )
        if any(marker in normalized for marker in error_markers):
            return "#fca5a5" if dark else "#b91c1c"
        if any(marker in normalized for marker in warning_markers):
            return "#fdba74" if dark else "#92400e"
        return "#e5edf8" if dark else "#111827"

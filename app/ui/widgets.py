from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
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


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1500)

    def append_event(self, message: str) -> None:
        self.appendPlainText(message)
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon

Translate = Callable[..., str]


class VideoStoryboardEntityDialog(QDialog):
    """Create/edit a continuity entity and its optional visual reference."""

    def __init__(
        self,
        tr: Translate,
        entity_kind: str,
        total_seconds: float,
        record: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.entity_kind = "location" if entity_kind == "location" else "character"
        self.record = dict(record or {})
        self.reference_image_path = str(self.record.get("reference_image_path") or "")
        states = [value for value in self.record.get("states", []) if isinstance(value, dict)]
        self.state = dict(states[0]) if states else {}
        editing = bool(record)
        noun = "location" if self.entity_kind == "location" else "character"
        self.setWindowTitle(
            self.tr_text(
                f"video_storyboard_{'edit' if editing else 'new'}_{noun}",
                f"{'Edit' if editing else 'New'} {noun}",
            )
        )
        self.resize(860, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        form = QFormLayout()
        self.name_edit = QLineEdit(str(self.record.get("name") or ""))
        self.aliases_edit = QLineEdit(
            ", ".join(str(value) for value in self.record.get("aliases", []) if str(value).strip())
        )
        self.aliases_edit.setPlaceholderText("Optional, comma-separated")
        self.identity_edit = QPlainTextEdit(str(self.record.get("identity_description") or ""))
        self.identity_edit.setMinimumSize(590, 100)
        self.state_edit = QPlainTextEdit(str(self.state.get("description") or ""))
        self.state_edit.setMinimumSize(590, 130)
        self.start_spin = QDoubleSpinBox()
        self.end_spin = QDoubleSpinBox()
        maximum = max(1_000_000.0, float(total_seconds or 1.0))
        for spin in (self.start_spin, self.end_spin):
            spin.setRange(0.0, maximum)
            spin.setDecimals(3)
            spin.setSuffix(" s")
        self.start_spin.setValue(float(self.state.get("from_seconds") or 0.0))
        self.end_spin.setValue(float(self.state.get("to_seconds") or total_seconds or 1.0))
        form.addRow(self.tr_text("name", "Name"), self.name_edit)
        form.addRow(self.tr_text("video_storyboard_entity_aliases", "Aliases"), self.aliases_edit)
        form.addRow(
            self.tr_text("video_storyboard_entity_identity", "Permanent visual identity"),
            self.identity_edit,
        )
        form.addRow(
            self.tr_text("video_storyboard_entity_initial_state", "Initial visual state"),
            self.state_edit,
        )
        form.addRow(self.tr_text("video_storyboard_continuity_from", "From"), self.start_spin)
        form.addRow(self.tr_text("video_storyboard_continuity_to", "To"), self.end_spin)
        layout.addLayout(form)

        reference_row = QHBoxLayout()
        self.reference_preview = QLabel()
        self.reference_preview.setObjectName("storyboardPreview")
        self.reference_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reference_preview.setFixedSize(QSize(170, 170))
        reference_buttons = QVBoxLayout()
        reference_label = QLabel(
            self.tr_text(
                "video_storyboard_entity_reference_help",
                "Optional reference image for models that support one or more visual references.",
            )
        )
        reference_label.setObjectName("helperLabel")
        reference_label.setWordWrap(True)
        self.choose_reference_button = QPushButton(
            self.tr_text("video_storyboard_choose_reference_image", "Choose reference image")
        )
        self.choose_reference_button.setIcon(ui_icon("image"))
        self.remove_reference_button = QPushButton(
            self.tr_text("video_storyboard_remove_reference_image", "Remove reference")
        )
        self.remove_reference_button.setIcon(ui_icon("delete", danger=True))
        reference_buttons.addWidget(reference_label)
        reference_buttons.addWidget(self.choose_reference_button)
        reference_buttons.addWidget(self.remove_reference_button)
        reference_buttons.addStretch(1)
        reference_row.addWidget(self.reference_preview)
        reference_row.addLayout(reference_buttons, 1)
        layout.addLayout(reference_row)
        self._refresh_reference()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.choose_reference_button.clicked.connect(self._choose_reference)
        self.remove_reference_button.clicked.connect(self._remove_reference)

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name_edit.text().strip(),
            "aliases": [value.strip() for value in self.aliases_edit.text().split(",") if value.strip()],
            "identity_description": self.identity_edit.toPlainText().strip(),
            "state_description": self.state_edit.toPlainText().strip(),
            "from_seconds": self.start_spin.value(),
            "to_seconds": max(self.start_spin.value(), self.end_spin.value()),
            "reference_image_path": self.reference_image_path,
        }

    def _choose_reference(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            self.tr_text("video_storyboard_choose_reference_image", "Choose reference image"),
            str(Path(self.reference_image_path).parent) if self.reference_image_path else "",
            self.tr_text("video_storyboard_image_files", "Image files (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)"),
        )
        if selected:
            self.reference_image_path = selected
            self._refresh_reference()

    def _remove_reference(self) -> None:
        self.reference_image_path = ""
        self._refresh_reference()

    def _refresh_reference(self) -> None:
        path = Path(self.reference_image_path) if self.reference_image_path else None
        pixmap = QPixmap(str(path)) if path and path.is_file() else QPixmap()
        if pixmap.isNull():
            self.reference_preview.clear()
            self.reference_preview.setText(self.tr_text("video_storyboard_no_reference", "No reference"))
        else:
            self.reference_preview.setText("")
            self.reference_preview.setPixmap(
                pixmap.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        self.remove_reference_button.setEnabled(not pixmap.isNull())

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_entity_name_required", "Name required"),
                self.tr_text("video_storyboard_entity_name_required_detail", "Enter a canonical name."),
            )
            return
        if self.end_spin.value() < self.start_spin.value():
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_invalid_state_time", "Invalid time range"),
                self.tr_text("video_storyboard_invalid_state_time_detail", "The end time must be equal to or later than the start time."),
            )
            return
        self.accept()

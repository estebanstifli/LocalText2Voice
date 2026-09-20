from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import tempfile
import uuid

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
    QTabWidget,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QApplication,
    QMenu,
)

from app.ui.icons import ui_icon
from app.core.storyboard_provider_label import provider_label

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
        settings: dict[str, Any] | None = None,
        storyboard_page=None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.entity_kind = entity_kind if entity_kind in {"location", "object"} else "character"
        self.record = dict(record or {})
        self.generation_settings = dict(settings or {})
        self._image_worker = None
        self._close_when_finished = False
        self._generated_directory = tempfile.TemporaryDirectory(prefix="character-reference-")
        self.reference_image_path = str(self.record.get("reference_image_path") or "")
        states = [value for value in self.record.get("states", []) if isinstance(value, dict)]
        self.state = dict(states[0]) if states else {}
        editing = bool(record)
        noun = self.entity_kind
        self.setWindowTitle(
            self.tr_text(
                f"video_storyboard_{'edit' if editing else 'new'}_{noun}",
                f"{'Edit' if editing else 'New'} {noun}",
            )
        )
        self.resize(1160, 850)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        self.tabs = QTabWidget()
        general = QWidget()
        general_layout = QVBoxLayout(general)
        self.tabs.addTab(general, self.tr_text("character_general_tab", "General"))
        layout.addWidget(self.tabs, 1)
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
        general_layout.addLayout(form)

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
        general_layout.addLayout(reference_row)
        self.generate_image_button = QPushButton(self.tr_text("character_generate_image", "Generate image with AI"))
        general_layout.addWidget(self.generate_image_button)
        general_layout.addStretch(1)
        image_page = QWidget()
        image_layout = QVBoxLayout(image_page)
        self.image_preview = QLabel()
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumSize(320, 180)
        self.image_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        image_layout.addWidget(self.image_preview, 1)
        image_actions = QHBoxLayout()
        self.copy_image_button = QPushButton(self.tr_text("entity_copy_image", "Copy"))
        self.paste_image_button = QPushButton(self.tr_text("entity_paste_image", "Paste"))
        self.load_image_button = QPushButton(self.tr_text("entity_load_image", "Load from file"))
        for button, action in ((self.copy_image_button, self._copy_image),
                               (self.paste_image_button, self._paste_image),
                               (self.load_image_button, self._choose_reference)):
            image_actions.addWidget(button)
            button.clicked.connect(action)
        image_actions.addStretch()
        image_layout.addLayout(image_actions)
        self.image_preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_preview.customContextMenuRequested.connect(self._image_context_menu)
        image_layout.addWidget(QLabel(self.tr_text("entity_image_resolution", "Reference sheet · 1280 × 720 (720p)")))
        image_layout.addWidget(QLabel(self.tr_text("character_image_prompt", "Editable image prompt")))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setMaximumHeight(190)
        self.prompt_edit.setPlainText(str(self.record.get("reference_image_prompt") or self._default_prompt()))
        image_layout.addWidget(self.prompt_edit)
        actions = QHBoxLayout()
        self.reset_prompt_button = QPushButton(self.tr_text("entity_reset_prompt", "Rebuild prompt from description"))
        self.run_generation_button = QPushButton(self.tr_text("character_generate_image", "Generate image with AI"))
        actions.addWidget(self.reset_prompt_button)
        actions.addStretch()
        actions.addWidget(self.run_generation_button)
        image_layout.addLayout(actions)
        self.image_status = QLabel()
        self.image_status.setWordWrap(True)
        image_layout.addWidget(self.image_status)
        self.tabs.addTab(image_page, self.tr_text("character_image_tab", "Image"))
        from app.ui.storyboard_entity_scenes import EntityScenesPanel
        self.scene_appearances = EntityScenesPanel(self.tr_text, self.record, self.entity_kind, storyboard_page, self)
        self.tabs.addTab(self.scene_appearances, self.tr_text("entity_scene_appearances", "Scene appearances"))
        self.generate_image_button.clicked.connect(self._open_image_prompt)
        self.reset_prompt_button.clicked.connect(lambda: self.prompt_edit.setPlainText(self._default_prompt()))
        self.run_generation_button.clicked.connect(self._generate_image)
        self._last_default_prompt = self._default_prompt()
        for field in (self.name_edit, self.identity_edit, self.state_edit):
            field.textChanged.connect(self._sync_prompt)
        self._refresh_reference()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        self.buttons = buttons
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
            "reference_image_prompt": self.prompt_edit.toPlainText().strip(),
        }

    def _default_prompt(self) -> str:
        description = "\n".join(value for value in (
            self.identity_edit.toPlainText().strip(), self.state_edit.toPlainText().strip()
        ) if value)
        name = self.name_edit.text().strip() or f"the {self.entity_kind}"
        if self.entity_kind == "location":
            return (
                f"Create a clean location reference sheet for {name}.\nLocation description: {description}\n\n"
                "Include a large establishing view, an alternate angle of the same space, and small detail panels "
                "of distinctive architecture, materials and landmarks. Preserve the same layout, scale, "
                "architecture, colors and historical period across views. No characters or unrelated objects. "
                "Clear, even lighting and readable spatial relationships. Professional environment design sheet, "
                "neatly separated panels, consistent perspective, no text or watermarks. Landscape, 1280 × 720."
            )
        if self.entity_kind == "object":
            return (
                f"Create a clean object reference sheet for {name}.\nObject description: {description}\n\n"
                "Show a large three-quarter view, a front view, a side view and small close-ups of distinctive "
                "details. Keep the exact same shape, proportions, materials, colors and components in every view. "
                "Plain light gray studio background, even lighting, realistic construction. No people, no busy "
                "environment. Professional product model sheet with neatly separated panels and a small color "
                "palette. No text or watermarks. Landscape, 1280 × 720."
            )
        return (
            f"Create a clean character reference sheet / model sheet for {self.name_edit.text().strip() or 'the character'}.\n"
            f"Character description: {description}\n\n"
            "Show one full-body front view, one large face close-up front view, one head-and-shoulders "
            "3/4 view, and one side-profile head view in neatly separated panels.\n"
            "Keep the same face, hairstyle, clothing, proportions, and age across all views. "
            "Neutral, calm expression. Plain light gray studio background, even studio lighting, "
            "high detail and realistic anatomy appropriate to the character.\n"
            "Professional model sheet layout, minimalist design, clear readability, organized composition. "
            "No busy background or cinematic environment. Optional small clothing detail close-ups "
            "and a small color palette section. Landscape composition, 1280 × 720."
        )

    def _sync_prompt(self) -> None:
        updated = self._default_prompt()
        if self.prompt_edit.toPlainText() == self._last_default_prompt:
            self.prompt_edit.setPlainText(updated)
        self._last_default_prompt = updated

    def _open_image_prompt(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.prompt_edit.setFocus()

    def _generate_image(self) -> None:
        if self._image_worker is not None:
            return
        if not self.prompt_edit.toPlainText().strip():
            self.image_status.setText(self.tr_text("character_prompt_required", "Enter a prompt to generate the image."))
            return
        from app.workers.character_image_worker import CharacterImageWorker
        target = Path(self._generated_directory.name) / f"{uuid.uuid4().hex}.png"
        self._image_worker = CharacterImageWorker(self.prompt_edit.toPlainText().strip(), self.generation_settings, target, self)
        self._set_generating(True)
        self.image_status.setText(self.tr_text("entity_image_generating", "{provider} — Generating reference sheet…", provider=provider_label(self._image_worker.settings)))
        self._image_worker.finished.connect(self._generation_finished)
        self._image_worker.start()

    def _set_generating(self, active: bool) -> None:
        for widget in (self.generate_image_button, self.run_generation_button, self.reset_prompt_button,
                       self.choose_reference_button, self.remove_reference_button, self.prompt_edit,
                       self.paste_image_button, self.load_image_button):
            widget.setEnabled(not active)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not active)

    def _generation_finished(self) -> None:
        worker = self._image_worker
        self._image_worker = None
        self._set_generating(False)
        if self._close_when_finished:
            worker.deleteLater()
            self.reject()
            return
        if worker.error:
            error = self.tr_text("character_image_failed", "Image generation failed: {error}", error=worker.error)
            self.image_status.setText(f"{provider_label(worker.settings)} — {error}")
        else:
            self.reference_image_path = str(worker.target)
            self.image_status.setText(self.tr_text("entity_image_ready", "Image ready. Press OK to save it."))
            self.tabs.setCurrentIndex(1)
        self._refresh_reference()
        worker.deleteLater()

    def reject(self) -> None:
        if self._image_worker is not None:
            self._close_when_finished = True
            self._image_worker.cancelled.set()
            self.image_status.setText(self.tr_text("character_image_cancelling", "Cancelling generation…"))
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._image_worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "image_preview"):
            self._refresh_reference()

    def _choose_reference(self) -> None:
        if self._image_worker is not None:
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            self.tr_text("video_storyboard_choose_reference_image", "Choose reference image"),
            str(Path(self.reference_image_path).parent) if self.reference_image_path else "",
            self.tr_text("video_storyboard_image_files", "Image files (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)"),
        )
        if selected:
            if QPixmap(selected).isNull():
                self.image_status.setText(self.tr_text("entity_invalid_image", "The file does not contain a readable image."))
                return
            self.reference_image_path = selected
            self._refresh_reference()

    def _copy_image(self) -> None:
        image = QPixmap(self.reference_image_path)
        if not image.isNull():
            QApplication.clipboard().setPixmap(image)

    def _paste_image(self) -> None:
        if self._image_worker is not None:
            return
        clipboard = QApplication.clipboard()
        image = clipboard.image()
        if image.isNull():
            mime = clipboard.mimeData()
            urls = mime.urls() if mime is not None else []
            if urls and urls[0].isLocalFile():
                image = QPixmap(urls[0].toLocalFile()).toImage()
        if image.isNull():
            self.image_status.setText(self.tr_text("entity_clipboard_no_image", "The clipboard contains no readable image."))
            return
        target = Path(self._generated_directory.name) / f"{uuid.uuid4().hex}.png"
        if not image.save(str(target), "PNG"):
            self.image_status.setText(self.tr_text("entity_image_save_failed", "Could not save the pasted image."))
            return
        self.reference_image_path = str(target)
        self.image_status.clear()
        self._refresh_reference()

    def _reference_menu(self):
        menu = QMenu(self)
        for button, action in ((self.copy_image_button, self._copy_image),
                               (self.paste_image_button, self._paste_image),
                               (self.load_image_button, self._choose_reference)):
            item = menu.addAction(button.text())
            item.setEnabled(button.isEnabled())
            item.triggered.connect(action)
        return menu

    def _image_context_menu(self, position):
        menu = self._reference_menu()
        menu.exec(self.image_preview.mapToGlobal(position))
        menu.deleteLater()

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
        self.remove_reference_button.setEnabled(not pixmap.isNull() and self._image_worker is None)
        self.copy_image_button.setEnabled(not pixmap.isNull())
        if hasattr(self, "image_preview"):
            self.image_preview.setText(self.tr_text("video_storyboard_no_reference", "No reference") if pixmap.isNull() else "")
            if pixmap.isNull():
                self.image_preview.setPixmap(QPixmap())
                self.image_preview.setText(self.tr_text("video_storyboard_no_reference", "No reference"))
            else:
                self.image_preview.setPixmap(pixmap.scaled(self.image_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

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

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon
from app.core.video_storyboard_comfyui import (
    canonical_character_lines_for_prompt,
    compile_effective_scene_prompt,
    compile_reference_edit_prompt,
)
from app.core.video_storyboard_prompt_entities import (
    active_entity_state_id,
    canonical_location_state_ids_for_prompt,
    decorate_storyboard_prompt,
    storyboard_prompt_entities,
    strip_storyboard_prompt_markers,
)
from app.core.video_storyboard_styles import (
    DEFAULT_STORYBOARD_STYLE_ID,
    normalize_storyboard_style_id,
    storyboard_style,
    storyboard_style_id_from_prompt,
    storyboard_style_sample_path,
    storyboard_styles,
)
from app.ui.video_storyboard_prompt_highlighter import (
    VideoStoryboardPromptHighlighter,
)
from app.ui.video_storyboard_reference_picker_dialog import (
    VideoStoryboardReferencePickerDialog,
)


Translate = Callable[..., str]


class _ImagePreview(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._empty_text = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360, 220)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setObjectName("storyboardPreview")

    def set_image(self, path: str, empty_text: str) -> None:
        self._source = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self._empty_text = empty_text
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._source.isNull():
            self.clear()
            self.setText(self._empty_text)
            return
        self.setText("")
        self.setPixmap(
            self._source.scaled(
                max(1, self.width() - 8),
                max(1, self.height() - 8),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class VideoStoryboardRegenerationDialog(QDialog):
    """Per-frame overrides and explicit candidate comparison workflow."""

    generateRequested = Signal(object)
    candidateAccepted = Signal(object)
    candidateRejected = Signal(str, str)

    def __init__(
        self,
        tr: Translate,
        scene: dict[str, Any],
        plan: dict[str, Any],
        parent: QWidget | None = None,
        *,
        project_dir: str = "",
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.scene = dict(scene)
        self.plan = dict(plan)
        self.project_dir = str(project_dir or "")
        self.scene_id = str(scene.get("scene_id") or scene.get("id") or "")
        self._candidate_path = ""
        self._generating = False
        self._resolved = False
        self._loading_style = False
        self._loading_values = True
        self._updating_raw_prompt = False
        self._raw_prompt_customized = False
        self._explicit_character_ids: set[str] = set()
        self._explicit_location_ids: set[str] = set()
        self._explicit_location_state_ids: list[str] = []
        self._reference_images: list[dict[str, str]] = []
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_regenerate_dialog_title",
                "Regenerate storyboard frame",
            )
        )
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(1180, 900)
        self._build_ui()
        self._load_effective_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(
            self.tr_text(
                "video_storyboard_regenerate_dialog_scene",
                "Frame {scene} — temporary overrides",
                scene=self.scene_id,
            )
        )
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        previews = QHBoxLayout()
        current_group = QGroupBox(
            self.tr_text("video_storyboard_current_frame", "Current frame")
        )
        current_layout = QVBoxLayout(current_group)
        self.current_preview = _ImagePreview()
        current_layout.addWidget(self.current_preview)
        candidate_group = QGroupBox(
            self.tr_text("video_storyboard_new_candidate", "New candidate")
        )
        candidate_layout = QVBoxLayout(candidate_group)
        self.candidate_preview = _ImagePreview()
        candidate_layout.addWidget(self.candidate_preview)
        previews.addWidget(current_group, 1)
        previews.addWidget(candidate_group, 1)
        layout.addLayout(previews, 1)

        prompt_label = QLabel(
            self.tr_text(
                "video_storyboard_generation_prompt",
                "Generation prompt (English)",
            )
        )
        prompt_label.setObjectName("sectionTitle")
        self.prompt_mode_tabs = QTabWidget()
        normal_prompt_page = QWidget()
        normal_prompt_layout = QVBoxLayout(normal_prompt_page)
        normal_prompt_layout.setContentsMargins(6, 6, 6, 6)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setMaximumHeight(115)
        self._prompt_highlighter = VideoStoryboardPromptHighlighter(
            self.prompt_edit.document(), self.plan
        )
        normal_prompt_layout.addWidget(self.prompt_edit)
        reference_row = QHBoxLayout()
        self.reference_button = QPushButton(
            self.tr_text("video_storyboard_add_image_reference", "+ Img Ref")
        )
        self.reference_button.setIcon(ui_icon("replace_image"))
        self.reference_summary = QLabel(
            self.tr_text("video_storyboard_no_image_references", "No image references")
        )
        self.reference_summary.setObjectName("helperLabel")
        reference_row.addWidget(self.reference_button)
        reference_row.addWidget(self.reference_summary, 1)
        normal_prompt_layout.addLayout(reference_row)
        self.prompt_mode_tabs.addTab(
            normal_prompt_page,
            self.tr_text("video_storyboard_prompt_normal", "Normal"),
        )
        raw_prompt_page = QWidget()
        raw_prompt_layout = QVBoxLayout(raw_prompt_page)
        raw_prompt_layout.setContentsMargins(6, 6, 6, 6)
        raw_hint = QLabel(
            self.tr_text(
                "video_storyboard_prompt_raw_help",
                "Exact composed positive prompt. Editing it bypasses the fields below for this frame.",
            )
        )
        raw_hint.setObjectName("helperLabel")
        raw_hint.setWordWrap(True)
        self.raw_prompt_edit = QPlainTextEdit()
        self.raw_prompt_edit.setMaximumHeight(115)
        raw_prompt_layout.addWidget(raw_hint)
        raw_prompt_layout.addWidget(self.raw_prompt_edit)
        self.prompt_mode_tabs.addTab(
            raw_prompt_page,
            self.tr_text("video_storyboard_prompt_composed", "Composed / Raw"),
        )
        layout.addWidget(prompt_label)
        prompt_modes_row = QHBoxLayout()
        prompt_modes_row.addWidget(self.prompt_mode_tabs, 1)
        prompt_insert_buttons = QHBoxLayout()
        prompt_insert_buttons.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.insert_character_button = QPushButton(
            self.tr_text("video_storyboard_insert_character", "Character")
        )
        self.insert_character_button.setIcon(ui_icon("person"))
        self.insert_character_button.setStyleSheet(
            "QPushButton { color: #6d28d9; font-weight: 600; }"
        )
        self.insert_location_button = QPushButton(
            self.tr_text("video_storyboard_insert_location", "Location")
        )
        self.insert_location_button.setIcon(ui_icon("location"))
        self.insert_location_button.setStyleSheet(
            "QPushButton { color: #0f766e; font-weight: 600; }"
        )
        prompt_insert_buttons.addWidget(self.insert_character_button)
        prompt_insert_buttons.addWidget(self.insert_location_button)
        prompt_modes_row.addLayout(prompt_insert_buttons)
        layout.addLayout(prompt_modes_row)
        self._build_entity_menus()

        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(280)

        gallery_page = QWidget()
        gallery_layout = QVBoxLayout(gallery_page)
        gallery_layout.setContentsMargins(8, 8, 8, 8)
        gallery_hint = QLabel(
            self.tr_text(
                "video_storyboard_frame_style_gallery_help",
                "Choose a visual style override for this frame only.",
            )
        )
        gallery_hint.setObjectName("helperLabel")
        gallery_layout.addWidget(gallery_hint)
        self.style_gallery = QListWidget()
        self.style_gallery.setObjectName("storyboardFrameStyleGallery")
        self.style_gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.style_gallery.setMovement(QListWidget.Movement.Static)
        self.style_gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.style_gallery.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.style_gallery.setIconSize(QSize(120, 120))
        self.style_gallery.setGridSize(QSize(150, 158))
        self.style_gallery.setSpacing(5)
        self.style_gallery.setWordWrap(True)
        for catalog_style in storyboard_styles():
            sample_path = storyboard_style_sample_path(catalog_style.id)
            item = QListWidgetItem(
                QIcon(str(sample_path)) if sample_path is not None else QIcon(),
                catalog_style.name,
            )
            item.setData(Qt.ItemDataRole.UserRole, catalog_style.id)
            item.setToolTip(catalog_style.prompt)
            self.style_gallery.addItem(item)
        custom_item = QListWidgetItem(
            self._custom_style_icon(),
            self.tr_text("custom", "Custom"),
        )
        custom_item.setData(Qt.ItemDataRole.UserRole, "custom")
        self.style_gallery.addItem(custom_item)
        gallery_layout.addWidget(self.style_gallery, 1)
        self.tabs.addTab(
            gallery_page,
            self.tr_text("video_storyboard_styles_tab", "Styles"),
        )

        style_page = QWidget()
        style_form = QFormLayout(style_page)
        self.shot_edit = QLineEdit()
        self.medium_edit = QLineEdit()
        self.palette_edit = QLineEdit()
        self.lighting_edit = QLineEdit()
        self.negative_edit = QLineEdit()
        self.seed_edit = QLineEdit()
        style_form.addRow(self.tr_text("video_storyboard_shot_framing", "Shot / framing"), self.shot_edit)
        style_form.addRow(self.tr_text("video_storyboard_style_medium", "Visual medium"), self.medium_edit)
        style_form.addRow(self.tr_text("video_storyboard_style_palette", "Color palette"), self.palette_edit)
        style_form.addRow(self.tr_text("video_storyboard_style_lighting", "Lighting"), self.lighting_edit)
        style_form.addRow(self.tr_text("video_storyboard_style_negative", "Avoid / negative"), self.negative_edit)
        style_form.addRow(self.tr_text("video_storyboard_seed", "Seed"), self.seed_edit)
        self.tabs.addTab(
            style_page,
            self.tr_text(
                "video_storyboard_style_details_tab",
                "Style details",
            ),
        )

        narrative_page = QWidget()
        narrative_form = QFormLayout(narrative_page)
        self.era_edit = QLineEdit()
        narrative_form.addRow(self.tr_text("video_storyboard_story_era", "Historical period / era"), self.era_edit)
        self.tabs.addTab(narrative_page, self.tr_text("video_storyboard_narrative_context", "Narrative context"))

        characters_page = QWidget()
        characters_layout = QVBoxLayout(characters_page)
        characters_layout.addWidget(
            QLabel(
                self.tr_text(
                    "video_storyboard_style_characters",
                    "Character definitions (one per line)",
                )
            )
        )
        self.characters_edit = QPlainTextEdit()
        characters_layout.addWidget(self.characters_edit)
        self.tabs.addTab(characters_page, self.tr_text("video_storyboard_characters_tab", "Characters"))
        layout.addWidget(self.tabs)

        self.status_label = QLabel(
            self.tr_text(
                "video_storyboard_regenerate_ready",
                "Adjust the frame and generate a candidate. The current image is preserved until you accept.",
            )
        )
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("helperLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.generate_button = QPushButton(
            self.tr_text("video_storyboard_generate_candidate", "Generate candidate")
        )
        self.generate_button.setIcon(ui_icon("bolt"))
        self.accept_button = QPushButton(
            self.tr_text("video_storyboard_accept_candidate", "Accept and replace")
        )
        self.accept_button.setObjectName("primaryButton")
        self.accept_button.setIcon(ui_icon("apply"))
        self.accept_button.setEnabled(False)
        self.accept_button.hide()
        self.ignore_button = QPushButton(
            self.tr_text("video_storyboard_reject_candidate", "Discard")
        )
        self.ignore_button.setIcon(ui_icon("cancel"))
        self.ignore_button.hide()
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.ignore_button)
        layout.addLayout(buttons)

        self.generate_button.clicked.connect(self._request_generation)
        self.accept_button.clicked.connect(self._accept_candidate)
        self.ignore_button.clicked.connect(self._ignore_candidate)
        self.style_gallery.currentItemChanged.connect(
            self._style_gallery_changed
        )
        self.prompt_edit.textChanged.connect(self._sync_prompt_characters)
        self.prompt_edit.textChanged.connect(self._structured_field_changed)
        for editor in (
            self.shot_edit,
            self.medium_edit,
            self.palette_edit,
            self.lighting_edit,
            self.negative_edit,
            self.era_edit,
        ):
            editor.textChanged.connect(self._structured_field_changed)
        self.characters_edit.textChanged.connect(self._structured_field_changed)
        self.raw_prompt_edit.textChanged.connect(self._raw_prompt_changed)
        self.insert_character_button.clicked.connect(
            lambda: self._show_entity_menu(
                self.insert_character_button,
                "characters",
            )
        )
        self.insert_location_button.clicked.connect(
            lambda: self._show_entity_menu(
                self.insert_location_button,
                "locations",
            )
        )
        self.reference_button.clicked.connect(self._choose_reference_images)

    def _load_effective_values(self) -> None:
        style = dict(self.plan.get("style", {}))
        overrides = self.scene.get("generation_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
        override_style = overrides.get("style", {})
        if isinstance(override_style, dict):
            style.update(override_style)
        stored_references = overrides.get("reference_images", [])
        if isinstance(stored_references, list):
            self._reference_images = [
                dict(value) for value in stored_references
                if isinstance(value, dict) and value.get("path")
            ][:3]
        self._refresh_reference_summary()
        narrative = self.plan.get("narrative_context", {})
        if not isinstance(narrative, dict):
            narrative = {}
        override_narrative = overrides.get("narrative", {})
        if not isinstance(override_narrative, dict):
            override_narrative = {}

        self.current_preview.set_image(
            str(self.scene.get("image_path") or ""),
            self.tr_text("video_storyboard_no_frame", "No frame"),
        )
        self.candidate_preview.set_image(
            "",
            self.tr_text(
                "video_storyboard_candidate_not_generated",
                "Generate a candidate to compare it here",
            ),
        )
        self.prompt_edit.setPlainText(
            decorate_storyboard_prompt(
                str(self.scene.get("prompt") or ""),
                self.plan,
            )
        )
        self.shot_edit.setText(str(self.scene.get("shot") or ""))
        self.medium_edit.setText(str(style.get("medium") or ""))
        self.palette_edit.setText(str(style.get("palette") or ""))
        self.lighting_edit.setText(str(style.get("lighting") or ""))
        self.negative_edit.setText(str(style.get("negative") or ""))
        characters = style.get("characters", [])
        if not (
            isinstance(override_style, dict)
            and isinstance(override_style.get("characters"), list)
        ):
            active_names = {
                str(value).strip().casefold()
                for value in self.scene.get("characters", [])
                if str(value).strip()
            }
            characters = [
                value
                for value in characters
                if str(value).split(":", 1)[0].strip().casefold()
                in active_names
            ] if isinstance(characters, list) else []
        self.characters_edit.setPlainText(
            "\n".join(str(value) for value in characters)
            if isinstance(characters, list)
            else ""
        )
        self._sync_prompt_characters()
        self.era_edit.setText(
            str(
                override_narrative.get("era")
                or self.scene.get("era")
                or narrative.get("era")
                or ""
            )
        )
        self.seed_edit.setText(
            str(overrides.get("seed", self.plan.get("base_seed", 0)))
        )
        raw_style_mode = (
            overrides.get("style_mode")
            or self.plan.get("style_mode")
            or storyboard_style_id_from_prompt(style.get("medium"))
        )
        style_mode = normalize_storyboard_style_id(
            raw_style_mode or DEFAULT_STORYBOARD_STYLE_ID
        )
        self._loading_style = True
        try:
            self.style_gallery.setCurrentItem(
                self._style_gallery_item(style_mode)
            )
        finally:
            self._loading_style = False
        raw_prompt = str(overrides.get("raw_prompt") or "").strip()
        self._loading_values = False
        if raw_prompt:
            self._set_raw_prompt(raw_prompt)
            self._raw_prompt_customized = True
        else:
            self._refresh_composed_prompt()
        QTimer.singleShot(0, self._center_selected_style)

    def request_payload(self, *, include_raw: bool = True) -> dict[str, Any]:
        try:
            seed = max(0, min(int(self.seed_edit.text().strip()), 2**63 - 1))
        except ValueError:
            seed = max(0, int(self.plan.get("base_seed") or 0))
        current_style_item = self.style_gallery.currentItem()
        style_mode = normalize_storyboard_style_id(
            current_style_item.data(Qt.ItemDataRole.UserRole)
            if current_style_item is not None
            else self.plan.get("style_mode")
        )
        character_lines = [
            line.strip()
            for line in self.characters_edit.toPlainText().splitlines()
            if line.strip()
        ]
        known_ids = {
            line.split(":", 1)[0].strip().casefold()
            for line in character_lines
        }
        normal_prompt = strip_storyboard_prompt_markers(
            self.prompt_edit.toPlainText(),
            self.plan,
        )
        for line in canonical_character_lines_for_prompt(
            self.plan,
            self.scene,
            normal_prompt,
        ):
            state_id = line.split(":", 1)[0].strip().casefold()
            parent_id = self._character_id_for_state(state_id)
            if parent_id in self._explicit_character_ids:
                continue
            if state_id not in known_ids:
                character_lines.append(line)
                known_ids.add(state_id)
        location_state_ids = canonical_location_state_ids_for_prompt(
            self.plan,
            self.scene,
            normal_prompt,
        )
        location_state_ids = [
            state_id
            for state_id in location_state_ids
            if self._location_id_for_state(state_id)
            not in self._explicit_location_ids
        ]
        for state_id in self._explicit_location_state_ids:
            if state_id not in location_state_ids:
                location_state_ids.append(state_id)
        overrides: dict[str, Any] = {
            "seed": seed,
            "style_mode": style_mode,
            "style": {
                "medium": self.medium_edit.text().strip(),
                "palette": self.palette_edit.text().strip(),
                "lighting": self.lighting_edit.text().strip(),
                "negative": self.negative_edit.text().strip(),
                "characters": character_lines,
            },
            "narrative": {"era": self.era_edit.text().strip()},
            "location_state_ids": location_state_ids,
            "reference_images": [dict(value) for value in self._reference_images],
        }
        if include_raw and self._raw_prompt_customized:
            raw_prompt = self.raw_prompt_edit.toPlainText().strip()
            if raw_prompt:
                overrides["raw_prompt"] = raw_prompt
        return {
            "scene_id": self.scene_id,
            "prompt": normal_prompt.strip(),
            "shot": self.shot_edit.text().strip(),
            "overrides": overrides,
        }

    def _choose_reference_images(self) -> None:
        dialog = VideoStoryboardReferencePickerDialog(
            self.tr_text,
            self.plan,
            self._reference_images,
            maximum=3,
            project_dir=self.project_dir,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._reference_images = dialog.selected_references()
        self._refresh_reference_summary()
        self._structured_field_changed()

    def _refresh_reference_summary(self) -> None:
        if not hasattr(self, "reference_summary"):
            return
        labels = [str(value.get("label") or Path(str(value.get("path") or "")).stem) for value in self._reference_images]
        self.reference_summary.setText(
            ", ".join(labels)
            if labels
            else self.tr_text("video_storyboard_no_image_references", "No image references")
        )
        self.reference_summary.setToolTip(
            "\n".join(str(value.get("path") or "") for value in self._reference_images)
        )

    def _compose_structured_prompt(self) -> str:
        payload = self.request_payload(include_raw=False)
        scene = dict(self.scene)
        scene["prompt"] = payload["prompt"]
        scene["shot"] = payload["shot"]
        overrides = payload["overrides"]
        scene["generation_overrides"] = overrides
        style = overrides.get("style", {})
        character_lines = style.get("characters", []) if isinstance(style, dict) else []
        scene["characters"] = [
            str(value).split(":", 1)[0].strip()
            for value in character_lines
            if str(value).split(":", 1)[0].strip()
        ]
        scene["locations"] = list(overrides.get("location_state_ids", []))
        narrative = overrides.get("narrative", {})
        if isinstance(narrative, dict) and narrative.get("era"):
            scene["era"] = str(narrative["era"])
            scene["era_state_id"] = ""
        prompt = compile_effective_scene_prompt(self.plan, scene)
        if self._reference_images:
            prompt = compile_reference_edit_prompt(prompt, self._reference_images)
        return prompt

    def _set_raw_prompt(self, prompt: str) -> None:
        self._updating_raw_prompt = True
        try:
            self.raw_prompt_edit.setPlainText(str(prompt or ""))
        finally:
            self._updating_raw_prompt = False

    def _refresh_composed_prompt(self) -> None:
        if self._loading_values or self._raw_prompt_customized:
            return
        self._set_raw_prompt(self._compose_structured_prompt())

    def _raw_prompt_changed(self) -> None:
        if self._loading_values or self._updating_raw_prompt:
            return
        self._raw_prompt_customized = True

    def _structured_field_changed(self) -> None:
        if self._loading_values:
            return
        if self._raw_prompt_customized:
            self._raw_prompt_customized = False
            QMessageBox.warning(
                self,
                self.tr_text(
                    "video_storyboard_raw_prompt_rebuilt_title",
                    "Composed prompt updated",
                ),
                self.tr_text(
                    "video_storyboard_raw_prompt_rebuilt_message",
                    "Your manual Composed / Raw edit has been replaced because a structured prompt field changed.",
                ),
            )
        self._refresh_composed_prompt()

    def _sync_prompt_characters(self) -> None:
        additions = canonical_character_lines_for_prompt(
            self.plan,
            self.scene,
            self.prompt_edit.toPlainText(),
        )
        if not additions:
            return
        lines = [
            line.strip()
            for line in self.characters_edit.toPlainText().splitlines()
            if line.strip()
        ]
        known_ids = {
            line.split(":", 1)[0].strip().casefold()
            for line in lines
        }
        changed = False
        for line in additions:
            state_id = line.split(":", 1)[0].strip().casefold()
            if state_id in known_ids:
                continue
            lines.append(line)
            known_ids.add(state_id)
            changed = True
        if changed:
            self.characters_edit.setPlainText("\n".join(lines))

    def _build_entity_menus(self) -> None:
        self.insert_character_button.setEnabled(
            bool(storyboard_prompt_entities(self.plan, "characters"))
        )
        self.insert_location_button.setEnabled(
            bool(storyboard_prompt_entities(self.plan, "locations"))
        )

    def _show_entity_menu(self, button: QPushButton, collection: str) -> None:
        menu = QMenu(self)
        for entity in storyboard_prompt_entities(self.plan, collection):
            name = str(entity.get("name") or "").strip()
            states = [
                state
                for state in entity.get("states", [])
                if isinstance(state, dict) and str(state.get("id") or "").strip()
            ]
            if not states:
                action = menu.addAction(f"@{name}")
                action.triggered.connect(
                    lambda _checked=False, selected=entity, source=collection:
                    self._insert_entity(selected, None, source)
                )
                continue
            entity_menu = menu.addMenu(f"@{name}")
            for state in states:
                description = str(state.get("description") or "").strip()
                start = self._time_label(state.get("from_seconds"))
                end = self._time_label(state.get("to_seconds"))
                label = f"{start}–{end} · {description or state.get('id', '')}"
                action = entity_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, selected=entity, selected_state=state,
                    source=collection: self._insert_entity(
                        selected,
                        selected_state,
                        source,
                    )
                )
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        menu.deleteLater()

    def _insert_entity(
        self,
        entity: dict[str, Any],
        state: dict[str, Any] | None,
        collection: str,
    ) -> None:
        name = str(entity.get("name") or "").strip()
        if not name:
            return
        self.prompt_mode_tabs.setCurrentIndex(0)
        cursor = self.prompt_edit.textCursor()
        prompt_text = self.prompt_edit.toPlainText()
        position = cursor.position()
        prefix = (
            ""
            if position == 0 or prompt_text[position - 1].isspace()
            else " "
        )
        suffix = (
            ""
            if position >= len(prompt_text) or prompt_text[position].isspace()
            else " "
        )
        cursor.insertText(f"{prefix}@{name}{suffix}")
        self.prompt_edit.setTextCursor(cursor)
        entity_id = str(entity.get("id") or "").casefold()
        if collection == "characters":
            self._explicit_character_ids.add(entity_id)
            if state is None:
                state = self._active_state(entity)
            if isinstance(state, dict):
                state_ids = {
                    str(value.get("id") or "").casefold()
                    for value in entity.get("states", [])
                    if isinstance(value, dict)
                }
                lines = [
                    line.strip()
                    for line in self.characters_edit.toPlainText().splitlines()
                    if line.strip()
                    and line.split(":", 1)[0].strip().casefold() not in state_ids
                ]
                state_id = str(state.get("id") or "").strip()
                details = ", ".join(
                    value
                    for value in (
                        name,
                        str(entity.get("identity_description") or "").strip(),
                        str(state.get("description") or "").strip(),
                    )
                    if value
                )
                if state_id:
                    lines.append(f"{state_id}: {details}" if details else state_id)
                self.characters_edit.setPlainText("\n".join(lines))
        else:
            self._explicit_location_ids.add(entity_id)
            if state is None:
                state = self._active_state(entity)
            state_id = str(state.get("id") or "").strip() if isinstance(state, dict) else ""
            self._explicit_location_state_ids = [
                value
                for value in self._explicit_location_state_ids
                if self._location_id_for_state(value) != entity_id
            ]
            if state_id:
                self._explicit_location_state_ids.append(state_id)
        self._structured_field_changed()

    def _active_state(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        try:
            reference_time = float(self.scene.get("start_seconds") or 0.0) + (
                max(0.0, float(self.scene.get("duration_seconds") or 0.0)) / 2.0
            )
        except (TypeError, ValueError):
            reference_time = 0.0
        state_id = active_entity_state_id(entity, reference_time)
        return next(
            (
                state
                for state in entity.get("states", [])
                if isinstance(state, dict) and str(state.get("id") or "") == state_id
            ),
            None,
        )

    def _character_id_for_state(self, state_id: str) -> str:
        return self._entity_id_for_state("characters", state_id)

    def _location_id_for_state(self, state_id: str) -> str:
        return self._entity_id_for_state("locations", state_id)

    def _entity_id_for_state(self, collection: str, state_id: str) -> str:
        target = str(state_id or "").casefold()
        for entity in storyboard_prompt_entities(self.plan, collection):
            if any(
                str(state.get("id") or "").casefold() == target
                for state in entity.get("states", [])
                if isinstance(state, dict)
            ):
                return str(entity.get("id") or "").casefold()
        return ""

    @staticmethod
    def _time_label(value: object) -> str:
        try:
            seconds = max(0.0, float(value))
        except (TypeError, ValueError):
            return "—"
        minutes, remainder = divmod(seconds, 60.0)
        return f"{int(minutes):02d}:{remainder:04.1f}"

    def _custom_style_icon(self) -> QIcon:
        pixmap = QPixmap(120, 120)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self.palette().color(self.palette().ColorRole.Base))
        painter.setPen(QPen(self.palette().color(self.palette().ColorRole.Mid), 2))
        painter.drawRoundedRect(2, 2, 116, 116, 10, 10)
        painter.setPen(self.palette().color(self.palette().ColorRole.Text))
        font = painter.font()
        font.setPointSize(28)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "+")
        painter.end()
        return QIcon(pixmap)

    def _style_gallery_item(self, identifier: object) -> QListWidgetItem | None:
        normalized = normalize_storyboard_style_id(identifier)
        for index in range(self.style_gallery.count()):
            item = self.style_gallery.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == normalized:
                return item
        return self.style_gallery.item(0)

    def _style_gallery_changed(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        if self._loading_style or current is None:
            return
        selected = normalize_storyboard_style_id(
            current.data(Qt.ItemDataRole.UserRole)
        )
        selected_style = storyboard_style(selected)
        if selected_style is not None:
            self.medium_edit.setText(selected_style.prompt)
        elif previous is not None:
            previous_style = storyboard_style(
                previous.data(Qt.ItemDataRole.UserRole)
            )
            if (
                previous_style is not None
                and self.medium_edit.text().strip() == previous_style.prompt
            ):
                self.medium_edit.clear()
        QTimer.singleShot(0, self._center_selected_style)

    def _center_selected_style(self) -> None:
        selected_item = self.style_gallery.currentItem()
        if selected_item is not None:
            self.style_gallery.scrollToItem(
                selected_item,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def _request_generation(self) -> None:
        effective_prompt = (
            self.raw_prompt_edit.toPlainText().strip()
            if self._raw_prompt_customized
            else self.prompt_edit.toPlainText().strip()
        )
        if self._generating or not effective_prompt:
            return
        if self._candidate_path:
            self.candidateRejected.emit(self.scene_id, self._candidate_path)
            self._candidate_path = ""
        self._generating = True
        self.generate_button.setEnabled(False)
        self.accept_button.setEnabled(False)
        self.ignore_button.setEnabled(False)
        self.accept_button.hide()
        self.ignore_button.hide()
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(
            self.tr_text(
                "video_storyboard_generating_scene",
                "Generating frame for scene {scene}...",
                scene=self.scene_id,
            )
        )
        self.generateRequested.emit(self.request_payload())

    def set_progress(self, message: str, percentage: int) -> None:
        self.status_label.setText(message)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(0, min(100, int(percentage))))

    def set_compiled_prompt(self, prompt: str) -> None:
        if not self._raw_prompt_customized:
            self._set_raw_prompt(prompt)

    def set_candidate(self, image_path: str) -> None:
        self._generating = False
        self._candidate_path = str(image_path or "")
        self.candidate_preview.set_image(
            self._candidate_path,
            self.tr_text(
                "video_storyboard_preview_unavailable",
                "Preview unavailable",
            ),
        )
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr_text(
                "video_storyboard_candidate_ready",
                "Candidate ready for review.",
            )
        )
        self.generate_button.setEnabled(True)
        self.accept_button.setEnabled(bool(self._candidate_path))
        self.ignore_button.setEnabled(True)
        self.accept_button.setVisible(bool(self._candidate_path))
        self.ignore_button.setVisible(bool(self._candidate_path))

    def set_failed(self, error: str) -> None:
        self._generating = False
        self.progress_bar.hide()
        self.status_label.setText(str(error))
        self.generate_button.setEnabled(True)
        self.accept_button.setEnabled(bool(self._candidate_path))
        self.ignore_button.setEnabled(bool(self._candidate_path))
        self.accept_button.setVisible(bool(self._candidate_path))
        self.ignore_button.setVisible(bool(self._candidate_path))

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _accept_candidate(self) -> None:
        if not self._candidate_path:
            return
        payload = self.request_payload()
        payload["image_path"] = self._candidate_path
        self._resolved = True
        self.candidateAccepted.emit(payload)
        self.accept()

    def _ignore_candidate(self) -> None:
        if self._candidate_path:
            self.candidateRejected.emit(self.scene_id, self._candidate_path)
        self._resolved = True
        self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._generating:
            event.ignore()
            return
        if self._candidate_path and not self._resolved:
            self.candidateRejected.emit(self.scene_id, self._candidate_path)
            self._resolved = True
        super().closeEvent(event)

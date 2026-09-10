from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QRubberBand,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon
from app.core.video_storyboard_camera import runpod_camera_prompt, camera_prompt, compose_image_edit_prompt
from app.ui.video_storyboard_camera_controls import StoryboardCameraControls
from app.ui.video_storyboard_reference_picker_dialog import VideoStoryboardReferencePickerDialog
from app.utils.ffmpeg_utils import FFmpegError, find_ffmpeg

Translate = Callable[..., str]


class _EditedImagePreview(QLabel):
    cropSelected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setObjectName("storyboardPreview")
        self._crop_enabled = False
        self._crop_origin = QPoint()
        self._display_rect = QRect()
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)

    def set_image(self, image: QImage) -> None:
        self._image = image.copy()
        self._refresh()

    def set_crop_enabled(self, enabled: bool) -> None:
        self._crop_enabled = bool(enabled)
        self.setCursor(
            Qt.CursorShape.CrossCursor
            if self._crop_enabled
            else Qt.CursorShape.ArrowCursor
        )
        if not enabled:
            self._rubber_band.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        if (
            self._crop_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._display_rect.contains(point)
        ):
            self._crop_origin = point
            self._rubber_band.setGeometry(QRect(point, point))
            self._rubber_band.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._crop_enabled and self._rubber_band.isVisible():
            point = self._clamped_point(event.position().toPoint())
            self._rubber_band.setGeometry(
                QRect(self._crop_origin, point).normalized()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._crop_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._rubber_band.isVisible()
        ):
            selection = self._rubber_band.geometry().intersected(
                self._display_rect
            )
            self._rubber_band.hide()
            source = self._source_rect(selection)
            if source.width() >= 2 and source.height() >= 2:
                self.cropSelected.emit(source)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamped_point(self, point: QPoint) -> QPoint:
        return QPoint(
            min(max(point.x(), self._display_rect.left()), self._display_rect.right()),
            min(max(point.y(), self._display_rect.top()), self._display_rect.bottom()),
        )

    def _source_rect(self, selection: QRect) -> QRect:
        if self._image.isNull() or self._display_rect.isEmpty():
            return QRect()
        scale_x = self._image.width() / max(1, self._display_rect.width())
        scale_y = self._image.height() / max(1, self._display_rect.height())
        return QRect(
            round((selection.left() - self._display_rect.left()) * scale_x),
            round((selection.top() - self._display_rect.top()) * scale_y),
            max(1, round(selection.width() * scale_x)),
            max(1, round(selection.height() * scale_y)),
        ).intersected(self._image.rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._image.isNull():
            self.clear()
            self.setText("No image")
            return
        self.setText("")
        pixmap = QPixmap.fromImage(self._image).scaled(
                max(1, self.width() - 8),
                max(1, self.height() - 8),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
        )
        self._display_rect = QRect(
            (self.width() - pixmap.width()) // 2,
            (self.height() - pixmap.height()) // 2,
            pixmap.width(),
            pixmap.height(),
        )
        self.setPixmap(pixmap)


class VideoStoryboardImageEditDialog(QDialog):
    """Small non-destructive image editor for a storyboard reference frame."""

    imageAccepted = Signal(str, object)
    generateEditRequested = Signal(object)
    cancelEditRequested = Signal()

    def __init__(
        self,
        tr: Translate,
        scene_id: str,
        image_path: str,
        *,
        previous_video_path: str = "",
        ffmpeg_path: str = "ffmpeg/ffmpeg.exe",
        seed: int = 0,
        width: int = 1280,
        height: int = 720,
        plan: dict | None = None,
        configuration: dict | None = None,
        project_dir: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.scene_id = str(scene_id)
        self.image_path = str(image_path)
        self.previous_video_path = str(previous_video_path)
        self.ffmpeg_path = str(ffmpeg_path or "ffmpeg/ffmpeg.exe")
        self.seed = max(0, int(seed or 0))
        self.output_width = max(1, int(width or 1280))
        self.output_height = max(1, int(height or 720))
        self.plan = deepcopy(plan or {})
        self.configuration = deepcopy(configuration or {})
        self.project_dir = str(project_dir or "")
        self._reference_images: list[dict[str, str]] = []
        self._original = QImage(self.image_path)
        self._initial_image = self._original.copy()
        self._working = self._original.copy()
        self._before_candidate = self._working.copy()
        self._extract_process: QProcess | None = None
        self._extract_target: Path | None = None
        self._edit_reference_target: Path | None = None
        self._generating_edit = False
        self._has_ai_candidate = False
        self._ai_candidate_path = ""
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(
            self.tr_text("video_storyboard_edit_frame_title", "Edit frame")
        )
        screen = self.screen().availableGeometry()
        self.resize(min(1340, screen.width() - 40), min(870, screen.height() - 60))
        self._build_ui()
        self.finished.connect(self._cleanup_edit_files)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        heading = QLabel(
            self.tr_text(
                "video_storyboard_edit_frame_heading",
                "Edit scene {scene} reference image",
                scene=self.scene_id,
            )
        )
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        self.file_link_button = QPushButton()
        self.file_link_button.setFlat(True)
        self.file_link_button.setIcon(ui_icon("file"))
        self.file_link_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_link_button.setStyleSheet(
            "QPushButton { color: #1769ff; text-decoration: underline; border: 0; }"
        )
        header.addWidget(self.file_link_button)
        layout.addLayout(header)
        self._refresh_file_link()
        description = QLabel(
            self.tr_text(
                "video_storyboard_edit_frame_help",
                "Changes are non-destructive: a new image is saved only when you accept. The original frame remains available through Undo.",
            )
        )
        description.setObjectName("helperLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor_splitter.setChildrenCollapsible(False)
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(0, 0, 10, 0)
        self.preview = _EditedImagePreview()
        self.preview.set_image(self._working)
        image_layout.addWidget(self.preview, 1)

        transform_row = QHBoxLayout()
        self.flip_horizontal_button = self._button(
            "video_storyboard_flip_horizontal", "Flip horizontal", "redo"
        )
        self.flip_vertical_button = self._button(
            "video_storyboard_flip_vertical", "Flip vertical", "redo"
        )
        self.replace_button = self._button(
            "video_storyboard_replace_frame", "Replace image", "replace_image"
        )
        self.crop_button = self._button(
            "video_storyboard_crop_frame", "Crop", "crop"
        )
        self.crop_button.setCheckable(True)
        self.reset_button = self._button(
            "video_storyboard_reset_image", "Reset", "refresh"
        )
        for button in (
            self.replace_button,
            self.crop_button,
            self.flip_horizontal_button,
            self.flip_vertical_button,
            self.reset_button,
        ):
            transform_row.addWidget(button)
        transform_row.addStretch(1)
        image_layout.addLayout(transform_row)

        previous_row = QHBoxLayout()
        self.previous_video_button = QPushButton(
            self.tr_text(
                "video_storyboard_use_previous_video_last_frame",
                "Use final frame from previous scene video",
            )
        )
        self.previous_video_button.setIcon(ui_icon("video_track"))
        self.previous_video_button.setEnabled(
            bool(
                self.previous_video_path
                and Path(self.previous_video_path).is_file()
            )
        )
        self.previous_status = QLabel()
        self.previous_status.setObjectName("helperLabel")
        self.previous_status.setWordWrap(True)
        previous_row.addWidget(self.previous_video_button)
        previous_row.addWidget(self.previous_status, 1)
        image_layout.addLayout(previous_row)
        self.editor_splitter.addWidget(image_panel)
        self.ai_sidebar = QWidget()
        self.ai_sidebar.setMinimumWidth(380)
        ai_layout = QVBoxLayout(self.ai_sidebar)
        ai_layout.setContentsMargins(12, 0, 8, 0)
        ai_layout.setSpacing(10)
        sidebar_title = QLabel(self.tr_text("storyboard_ai_tools", "AI editing studio"))
        sidebar_title.setObjectName("sectionTitle")
        ai_layout.addWidget(sidebar_title)
        provider = str(self.configuration.get("image_edit_provider") or "disabled")
        provider_text = {
            "comfyui": "ComfyUI · Qwen Image Edit 2509",
            "runpod": "Runpod · Qwen Image Edit 2511 · ~$0.02 / image",
            "litellm_image": "LiteLLM · " + str(self.configuration.get("litellm_image_edit", {}).get("model") or ""),
        }.get(provider, self.tr_text("storyboard_edit_disabled", "Configure an image editor in Settings → Video Storyboard."))
        self.provider_label = provider_label = QLabel(provider_text)
        provider_label.setTextFormat(Qt.TextFormat.PlainText)
        provider_label.setWordWrap(True)
        provider_label.setObjectName("helperLabel")
        ai_layout.addWidget(provider_label)
        self.camera_controls = StoryboardCameraControls(self.tr_text)
        self.camera_controls.set_image(self._working)
        self.camera_controls.setEnabled(provider in {"comfyui", "runpod"})
        if provider == "runpod":
            self.camera_controls.set_runpod_mode()
        if provider not in {"comfyui", "runpod"}:
            self.camera_controls.setToolTip(self.tr_text("storyboard_camera_comfy_only", "Camera controls require Qwen Edit with ComfyUI and the Multiple-angles LoRA."))
        ai_layout.addWidget(self.camera_controls)
        self.camera_cost_label = QLabel()
        self.camera_cost_label.setWordWrap(True)
        self.camera_cost_label.setVisible(provider == "runpod")
        ai_layout.addWidget(self.camera_cost_label)

        ai_label = QLabel(
            self.tr_text(
                "video_storyboard_ai_edit_instruction",
                "AI edit / refinement instruction",
            )
        )
        ai_label.setObjectName("sectionTitle")
        ai_layout.addWidget(ai_label)
        self.prompt_examples = QComboBox()
        self.prompt_examples.addItem(self.tr_text("storyboard_edit_examples", "Try an example…"), "")
        for label, prompt in (
            (self.tr_text("storyboard_edit_remove", "Remove an object"), "Remove the hat from the woman. Keep the rest of the image unchanged."),
            (self.tr_text("storyboard_edit_add", "Add from a reference"), "Add the object from Image 2 to Image 1. Match the scene's lighting and perspective."),
            (self.tr_text("storyboard_edit_replace", "Replace an object"), "Replace the dog with a cat. Preserve the composition and lighting."),
            (self.tr_text("storyboard_edit_light", "Change the lighting"), "Change the lighting to warm sunset light. Preserve the subjects and their appearance."),
        ):
            self.prompt_examples.addItem(label, prompt)
        ai_layout.addWidget(self.prompt_examples)
        self.ai_prompt_edit = QPlainTextEdit()
        self.ai_prompt_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_ai_edit_prompt_hint",
                "Describe the change in natural language, for example: Replace the dog with a cat, keeping the composition and lighting.",
            )
        )
        self.ai_prompt_edit.setMinimumHeight(100)
        self.ai_prompt_edit.setMaximumHeight(150)
        ai_layout.addWidget(self.ai_prompt_edit)
        reference_row = QHBoxLayout()
        self.reference_button = QPushButton("+ Img Ref")
        self.reference_button.setIcon(ui_icon("replace_image"))
        self.reference_summary = QLabel()
        self.reference_summary.setObjectName("helperLabel")
        self.reference_summary.setWordWrap(True)
        reference_row.addWidget(self.reference_button)
        reference_row.addWidget(self.reference_summary, 1)
        ai_layout.addLayout(reference_row)
        self.reference_thumbnails = QHBoxLayout()
        ai_layout.addLayout(self.reference_thumbnails)
        self._refresh_references()
        self.prompt_preview_toggle = QPushButton(self.tr_text("storyboard_edit_show_prompt", "Show prompt sent to model"))
        self.prompt_preview_toggle.setCheckable(True)
        self.prompt_preview = QPlainTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.prompt_preview.setFixedHeight(95)
        self.prompt_preview.hide()
        ai_layout.addWidget(self.prompt_preview_toggle)
        ai_layout.addWidget(self.prompt_preview)
        ai_actions = QHBoxLayout()
        self.generate_ai_button = QPushButton(
            self.tr_text("video_storyboard_generate_ai_edit", "Generate edit candidate")
        )
        self.generate_ai_button.setIcon(ui_icon("bolt"))
        self.generate_ai_button.setObjectName("primaryButton")
        self.discard_ai_button = QPushButton(
            self.tr_text("video_storyboard_discard_ai_edit", "Discard candidate")
        )
        self.discard_ai_button.setIcon(ui_icon("cancel"))
        self.discard_ai_button.hide()
        self.cancel_ai_button = QPushButton(
            self.tr_text("video_storyboard_cancel_ai_edit", "Cancel generation")
        )
        self.cancel_ai_button.setIcon(ui_icon("stop"))
        self.cancel_ai_button.hide()
        self.ai_status = QLabel()
        self.ai_status.setWordWrap(True)
        self.ai_status.setTextFormat(Qt.TextFormat.PlainText)
        self.ai_status.setObjectName("helperLabel")
        self.ai_progress = QProgressBar()
        self.ai_progress.setRange(0, 100)
        self.ai_progress.hide()
        ai_actions.addWidget(self.discard_ai_button)
        ai_actions.addWidget(self.cancel_ai_button)
        ai_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.ai_sidebar)
        scroll.setMinimumWidth(410)
        ai_panel = QWidget()
        ai_panel_layout = QVBoxLayout(ai_panel)
        ai_panel_layout.setContentsMargins(0, 0, 0, 0)
        ai_panel_layout.addWidget(scroll, 1)
        # Generation actions stay visible even on a small display or when
        # references and the composed prompt expand the scrollable tools.
        ai_panel_layout.addWidget(self.generate_ai_button)
        ai_panel_layout.addLayout(ai_actions)
        ai_panel_layout.addWidget(self.ai_status)
        ai_panel_layout.addWidget(self.ai_progress)
        self.editor_splitter.addWidget(ai_panel)
        self.editor_splitter.setStretchFactor(0, 3)
        self.editor_splitter.setStretchFactor(1, 2)
        self.editor_splitter.setSizes([780, 450])
        layout.addWidget(self.editor_splitter, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.accept_button = QPushButton(
            self.tr_text("video_storyboard_apply_frame_edit", "Apply as new frame")
        )
        self.accept_button.setObjectName("primaryButton")
        self.accept_button.setIcon(ui_icon("apply"))
        self.cancel_button = QPushButton(self.tr_text("cancel", "Cancel"))
        self.cancel_button.setIcon(ui_icon("cancel"))
        self.accept_button.hide()
        footer.addWidget(self.accept_button)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)

        self.flip_horizontal_button.clicked.connect(
            lambda: self._set_working(
                self._working.flipped(Qt.Orientation.Horizontal)
            )
        )
        self.flip_vertical_button.clicked.connect(
            lambda: self._set_working(
                self._working.flipped(Qt.Orientation.Vertical)
            )
        )
        self.reset_button.clicked.connect(lambda: self._set_working(self._original))
        self.replace_button.clicked.connect(self._replace_image)
        self.crop_button.toggled.connect(self._set_crop_mode)
        self.preview.cropSelected.connect(self._apply_crop)
        self.file_link_button.clicked.connect(self._open_current_file)
        self.previous_video_button.clicked.connect(self._extract_previous_last_frame)
        self.generate_ai_button.clicked.connect(self._request_ai_edit)
        self.reference_button.clicked.connect(self._choose_references)
        self.prompt_examples.activated.connect(self._use_example)
        self.prompt_preview_toggle.toggled.connect(self.prompt_preview.setVisible)
        self.ai_prompt_edit.textChanged.connect(self._refresh_prompt)
        self.camera_controls.changed.connect(self._refresh_prompt)
        self.discard_ai_button.clicked.connect(self._discard_ai_candidate)
        self.cancel_ai_button.clicked.connect(self.cancelEditRequested.emit)
        self.accept_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self.reject)
        self._refresh_prompt()

    def _use_example(self, index: int) -> None:
        prompt = str(self.prompt_examples.itemData(index) or "")
        if prompt:
            self.ai_prompt_edit.setPlainText(prompt)
        self.prompt_examples.setCurrentIndex(0)

    def _choose_references(self) -> None:
        dialog = VideoStoryboardReferencePickerDialog(
            self.tr_text, self.plan, self._reference_images, maximum=2,
            project_dir=self.project_dir, parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reference_images = dialog.selected_references()
            self._refresh_references()
            self._refresh_prompt()

    def _refresh_references(self) -> None:
        self.reference_summary.setText(self.tr_text(
            "storyboard_edit_references_hint", "Image 1: current frame · {count}/2 extra references",
            count=len(self._reference_images)))
        while self.reference_thumbnails.count():
            item = self.reference_thumbnails.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, reference in enumerate(self._reference_images, start=2):
            button = QPushButton(f"{index} · {reference['label']}  ×")
            button.setIcon(QIcon(reference["path"]))
            button.setIconSize(QSize(44, 44))
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setToolTip(reference["label"] + "\n" + self.tr_text("storyboard_edit_remove_ref", "Click to remove this reference"))
            button.clicked.connect(lambda _checked=False, selected=index - 2: self._remove_reference(selected))
            self.reference_thumbnails.addWidget(button)

    def _remove_reference(self, index: int) -> None:
        self._reference_images.pop(index)
        self._refresh_references()
        self._refresh_prompt()

    def _compiled_prompt(self) -> str:
        return compose_image_edit_prompt(
            self.ai_prompt_edit.toPlainText(), self.camera_controls.camera(),
            [{"label": "Current frame"}, *self._reference_images],
            provider=self.configuration.get("image_edit_provider", "comfyui"),
        )

    def _refresh_prompt(self) -> None:
        self.prompt_preview.setPlainText(self._compiled_prompt())
        if self.configuration.get("image_edit_provider") == "runpod":
            self.provider_label.setText("Runpod - Qwen Image Edit 2511 LoRA" if self.camera_controls.camera()["enabled"] else "Runpod - " + str(self.configuration.get("runpod", {}).get("edit_endpoint") or "qwen-image-edit-2511"))
        self.camera_cost_label.setText(self.tr_text("runpod_camera_cost", "Change camera: Qwen 2511 Multi-Angles - approximately $0.025 per image.") if self.camera_controls.camera()["enabled"] else "")
        has_instruction = bool(self.ai_prompt_edit.toPlainText().strip() or (runpod_camera_prompt(self.camera_controls.camera()) if self.configuration.get("image_edit_provider") == "runpod" else camera_prompt(self.camera_controls.camera())))
        self.generate_ai_button.setEnabled(not self._generating_edit and has_instruction)

    def _set_edit_busy(self, busy: bool) -> None:
        for widget in (
            self.ai_prompt_edit, self.prompt_examples, self.reference_button,
            self.replace_button, self.crop_button, self.flip_horizontal_button,
            self.flip_vertical_button, self.reset_button, self.accept_button,
        ):
            widget.setEnabled(not busy)
        self.camera_controls.setEnabled(not busy and self.configuration.get("image_edit_provider") in {"comfyui", "runpod"})
        for index in range(self.reference_thumbnails.count()):
            self.reference_thumbnails.itemAt(index).widget().setEnabled(not busy)
        self.previous_video_button.setEnabled(not busy and bool(self.previous_video_path and Path(self.previous_video_path).is_file()))
        self._refresh_prompt()

    def _button(self, key: str, fallback: str, icon: str) -> QPushButton:
        button = QPushButton(self.tr_text(key, fallback))
        button.setIcon(ui_icon(icon))
        return button

    def _set_working(self, image: QImage) -> None:
        if image.isNull():
            return
        self._working = image.copy()
        self.preview.set_image(self._working)
        self.camera_controls.set_image(self._working)
        self.accept_button.setVisible(not self._working.isNull() and self._working != self._initial_image)
        self.accept_button.setEnabled(not self._generating_edit)

    def _request_ai_edit(self) -> None:
        prompt = self._compiled_prompt()
        has_instruction = bool(self.ai_prompt_edit.toPlainText().strip() or (runpod_camera_prompt(self.camera_controls.camera()) if self.configuration.get("image_edit_provider") == "runpod" else camera_prompt(self.camera_controls.camera())))
        if self._generating_edit or not has_instruction or self._working.isNull():
            return
        target = Path(tempfile.gettempdir()) / f"ltv-storyboard-edit-reference-{uuid.uuid4().hex}.png"
        if not self._working.save(str(target), "PNG"):
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_ai_edit_failed", "Image edit unavailable"),
                self.tr_text("video_storyboard_ai_edit_reference_failed", "Could not prepare the current frame as a reference."),
            )
            return
        if self._edit_reference_target is not None:
            self._edit_reference_target.unlink(missing_ok=True)
        self._edit_reference_target = target
        self._before_candidate = self._working.copy()
        self.crop_button.setChecked(False)
        self._generating_edit = True
        self._has_ai_candidate = False
        self.generate_ai_button.setEnabled(False)
        self.ai_prompt_edit.setEnabled(False)
        self._set_edit_busy(True)
        self.cancel_ai_button.show()
        self.discard_ai_button.hide()
        self.ai_progress.show()
        self.ai_progress.setRange(0, 0)
        self.ai_status.setText(
            self.tr_text("video_storyboard_ai_edit_preparing", "Preparing image editor...")
        )
        self.generateEditRequested.emit({
            "scene_id": self.scene_id,
            "prompt": prompt,
            "reference_images": [{"path": str(target), "label": "Current frame"}, *deepcopy(self._reference_images)],
            "camera": self.camera_controls.camera(),
            "seed": self.seed,
            "width": self.output_width,
            "height": self.output_height,
        })

    def set_edit_progress(self, message: str, percentage: int = -1) -> None:
        self.ai_status.setText(str(message or ""))
        self.ai_progress.show()
        if percentage < 0:
            self.ai_progress.setRange(0, 0)
        else:
            self.ai_progress.setRange(0, 100)
            self.ai_progress.setValue(max(0, min(100, int(percentage))))

    def set_edit_candidate(self, image_path: str) -> None:
        image = QImage(str(image_path or ""))
        self._generating_edit = False
        self._set_edit_busy(False)
        self.cancel_ai_button.hide()
        self.generate_ai_button.setEnabled(True)
        self.ai_prompt_edit.setEnabled(True)
        self.ai_progress.setRange(0, 100)
        self.ai_progress.setValue(100)
        if image.isNull():
            self.set_edit_failed(
                self.tr_text("video_storyboard_ai_edit_invalid_candidate", "The editing provider returned an invalid image.")
            )
            return
        self._set_working(image)
        self._has_ai_candidate = True
        self._ai_candidate_path = str(image_path or "")
        self.discard_ai_button.show()
        self.ai_status.setText(
            self.tr_text("video_storyboard_ai_edit_ready", "Edit candidate ready. Apply it or discard it.")
        )

    def set_edit_failed(self, error: str) -> None:
        self._generating_edit = False
        self._set_edit_busy(False)
        self.cancel_ai_button.hide()
        self.generate_ai_button.setEnabled(True)
        self.ai_prompt_edit.setEnabled(True)
        self.ai_progress.hide()
        self.ai_status.setText(str(error or ""))

    def _discard_ai_candidate(self) -> None:
        if not self._has_ai_candidate:
            return
        self._set_working(self._before_candidate)
        if self._ai_candidate_path:
            Path(self._ai_candidate_path).unlink(missing_ok=True)
        self._ai_candidate_path = ""
        self._has_ai_candidate = False
        self.discard_ai_button.hide()
        self.ai_status.setText(
            self.tr_text("video_storyboard_ai_edit_discarded", "Edit candidate discarded.")
        )

    def _refresh_file_link(self) -> None:
        path = Path(self.image_path) if self.image_path else None
        self.file_link_button.setText(path.name if path is not None else "")
        self.file_link_button.setToolTip(str(path or ""))
        self.file_link_button.setVisible(bool(path and path.is_file()))

    def _open_current_file(self) -> None:
        if self.image_path and Path(self.image_path).is_file():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(self.image_path).resolve()))
            )

    def _replace_image(self) -> None:
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr_text(
                "video_storyboard_replace_frame_dialog",
                "Choose replacement image",
            ),
            str(Path(self.image_path).parent) if self.image_path else "",
            self.tr_text(
                "video_storyboard_image_files",
                "Image files (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)",
            ),
        )
        if not selected:
            return
        image = QImage(selected)
        if image.isNull():
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_invalid_image", "Invalid image"),
                self.tr_text(
                    "video_storyboard_invalid_image_detail",
                    "The selected file could not be opened as an image.",
                ),
            )
            return
        self.image_path = selected
        self._original = image.copy()
        self._set_working(image)
        self._refresh_file_link()

    def _set_crop_mode(self, enabled: bool) -> None:
        self.preview.set_crop_enabled(enabled)
        self.crop_button.setText(
            self.tr_text(
                "video_storyboard_crop_drag",
                "Drag crop area",
            )
            if enabled
            else self.tr_text("video_storyboard_crop_frame", "Crop")
        )

    def _apply_crop(self, source_rect: object) -> None:
        if not isinstance(source_rect, QRect) or self._working.isNull():
            return
        source_rect = source_rect.intersected(self._working.rect())
        if source_rect.width() < 2 or source_rect.height() < 2:
            return
        output_size = self._working.size()
        cropped = self._working.copy(source_rect).scaled(
            output_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._set_working(cropped)
        self.crop_button.setChecked(False)

    def _extract_previous_last_frame(self) -> None:
        if self._extract_process is not None:
            return
        try:
            executable = find_ffmpeg(self.ffmpeg_path)
        except FFmpegError as exc:
            self._show_extraction_error(str(exc))
            return
        target = Path(tempfile.gettempdir()) / (
            f"ltv-storyboard-last-frame-{uuid.uuid4().hex}.png"
        )
        process = QProcess(self)
        process.setProgram(str(executable))
        process.setArguments(
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-sseof",
                "-0.08",
                "-i",
                self.previous_video_path,
                "-frames:v",
                "1",
                "-y",
                str(target),
            ]
        )
        process.finished.connect(self._previous_frame_extracted)
        process.errorOccurred.connect(self._previous_frame_process_error)
        self._extract_process = process
        self._extract_target = target
        self.previous_video_button.setEnabled(False)
        self.previous_status.setText(
            self.tr_text(
                "video_storyboard_extracting_previous_frame",
                "Extracting the final video frame...",
            )
        )
        process.start()

    def _previous_frame_extracted(self, exit_code: int, _status: object) -> None:
        process = self._extract_process
        target = self._extract_target
        if process is None and target is None:
            return
        error = (
            bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
            if process is not None
            else ""
        )
        self._extract_process = None
        self._extract_target = None
        if process is not None:
            process.deleteLater()
        if exit_code == 0 and target is not None:
            image = QImage(str(target))
            target.unlink(missing_ok=True)
            if not image.isNull():
                self._set_working(image)
                self.previous_status.setText(
                    self.tr_text(
                        "video_storyboard_previous_frame_loaded",
                        "Final frame loaded. Apply to use it for this scene.",
                    )
                )
                return
        if target is not None:
            target.unlink(missing_ok=True)
        self._show_extraction_error(error or "FFmpeg did not create an image.")

    def _previous_frame_process_error(self, _error: object) -> None:
        process = self._extract_process
        if process is None or process.state() != QProcess.ProcessState.NotRunning:
            return
        error = process.errorString()
        target = self._extract_target
        self._extract_process = None
        self._extract_target = None
        process.deleteLater()
        if target is not None:
            target.unlink(missing_ok=True)
        self._show_extraction_error(error)

    def _show_extraction_error(self, detail: str) -> None:
        self.previous_video_button.setEnabled(
            bool(self.previous_video_path and Path(self.previous_video_path).is_file())
        )
        self.previous_status.setText(
            self.tr_text(
                "video_storyboard_previous_frame_failed",
                "Could not extract the previous video's final frame.",
            )
        )
        QMessageBox.warning(
            self,
            self.tr_text(
                "video_storyboard_previous_frame_failed_title",
                "Previous video frame unavailable",
            ),
            detail,
        )

    def _accept(self) -> None:
        if self._working.isNull() or self._generating_edit or self._working == self._initial_image:
            return
        self.imageAccepted.emit(self.scene_id, self._working.copy())
        if self._ai_candidate_path:
            Path(self._ai_candidate_path).unlink(missing_ok=True)
            self._ai_candidate_path = ""
        self.accept()

    def reject(self) -> None:
        if self._generating_edit:
            self.cancelEditRequested.emit()
            self.ai_status.setText(self.tr_text("storyboard_edit_cancelling", "Cancelling generation…"))
            return
        super().reject()

    def _cleanup_edit_files(self, _result: int = 0) -> None:
        if self._edit_reference_target is not None:
            self._edit_reference_target.unlink(missing_ok=True)
        if self._ai_candidate_path:
            Path(self._ai_candidate_path).unlink(missing_ok=True)

    def closeEvent(self, event) -> None:
        if self._generating_edit:
            self.cancelEditRequested.emit()
            event.ignore()
            return
        if self._extract_process is not None:
            self._extract_process.kill()
            self._extract_process.waitForFinished(1000)
            if self._extract_target is not None:
                self._extract_target.unlink(missing_ok=True)
        if self._edit_reference_target is not None:
            self._edit_reference_target.unlink(missing_ok=True)
        if self._ai_candidate_path:
            Path(self._ai_candidate_path).unlink(missing_ok=True)
        super().closeEvent(event)

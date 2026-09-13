from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QDesktopServices,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon
from app.ui.storyboard_text_hover import StoryboardTextHover
from app.ui.storyboard_video_clipboard import VideoLastFrameCopy
from app.ui.storyboard_video_edit_dialog import StoryboardVideoEditDialog
from app.ui.storyboard_image_fit_dialog import fit_pasted_image
from app.ui.video_storyboard_frame_batch_dialog import (
    VideoStoryboardFrameBatchDialog,
)
from app.ui.video_storyboard_image_edit_dialog import (
    VideoStoryboardImageEditDialog,
)
from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.ui.video_storyboard_regeneration_dialog import (
    VideoStoryboardRegenerationDialog,
)
from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog
from app.ui.video_storyboard_prompt_highlighter import (
    VideoStoryboardPromptHighlighter,
)
from app.core.video_storyboard_comfyui import (
    canonical_character_lines_for_prompt,
    compile_effective_scene_prompt,
)
from app.core.video_storyboard_prompt_entities import (
    canonical_location_state_ids_for_prompt,
    decorate_storyboard_prompt,
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


Translate = Callable[..., str]

STORYBOARD_GUIDE_URL = "https://github.com/estebanstifli/LocalText2Voice/blob/main/docs/VIDEO_STORYBOARD.md"


class _ContinuityItemDelegate(QStyledItemDelegate):
    """Give inline tree editors enough vertical room for the app font."""

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802 - Qt API
        hint = super().sizeHint(option, index)
        hint.setHeight(max(38, hint.height()))
        return hint

    def createEditor(self, parent, option, index):  # noqa: ANN001, N802 - Qt API
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setStyleSheet("QLineEdit { padding: 3px 6px; border-radius: 4px; }")
            editor.setMinimumHeight(30)
        return editor

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: ANN001, N802
        editor.setGeometry(option.rect.adjusted(1, 2, -1, -2))

_TRANSITION_OPTIONS = (
    ("none", "None"),
    ("fade", "Fade"),
    ("dissolve", "Dissolve"),
    ("wipeleft", "Wipe left"),
    ("wiperight", "Wipe right"),
    ("smoothleft", "Smooth left"),
    ("smoothright", "Smooth right"),
    ("circleopen", "Circle open"),
    ("circleclose", "Circle close"),
)


def _safe_int(value: object, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@dataclass
class StoryboardScene:
    scene_id: str
    duration_seconds: float
    narration: str = ""
    prompt: str = ""
    image_path: str = ""
    video_path: str = ""
    video_prompt: str = ""
    video_frame_role: str = "start"
    video_duration_seconds: float = 0.0
    video_motion_in: str = "none"
    video_motion_out: str = "none"
    status: str = "ready"
    start_seconds: float = 0.0
    characters: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    shot: str = ""
    era: str = ""
    era_state_id: str = ""
    motion: str = "zoom_in"
    motion_in: str = ""
    motion_out: str = ""
    transition: str = "fade"
    source_unit_start: int = -1
    source_unit_end: int = -1
    source_text_start: int = -1
    source_text_end: int = -1
    aligned_start_seconds: float = 0.0
    llm_narration: str = ""
    alignment_confidence: float = 0.0
    semantic_scene_id: str = ""
    source_proposal_id: str = ""
    coverage_index: int = 1
    coverage_count: int = 1
    shot_strategy: str = "semantic_shot"
    coverage_prompt_version: int = 1
    generation_overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(
        cls,
        value: StoryboardScene | dict[str, Any],
        index: int,
    ) -> StoryboardScene:
        if isinstance(value, cls):
            return cls(**value.as_dict())
        return cls(
            scene_id=str(
                value.get("scene_id") or value.get("id") or f"{index + 1:03d}"
            ),
            duration_seconds=max(
                0.001,
                float(value.get("duration_seconds") or value.get("duration") or 6.0),
            ),
            narration=str(value.get("narration") or value.get("text") or ""),
            prompt=str(value.get("prompt") or ""),
            image_path=str(value.get("image_path") or value.get("frame") or ""),
            video_path=str(value.get("video_path") or ""),
            video_prompt=str(value.get("video_prompt") or ""),
            video_frame_role=(
                "end"
                if str(value.get("video_frame_role") or "start").casefold() == "end"
                else "start"
            ),
            video_duration_seconds=max(
                0.0, float(value.get("video_duration_seconds") or 0.0)
            ),
            video_motion_in=str(value.get("video_motion_in") or "none"),
            video_motion_out=str(value.get("video_motion_out") or "none"),
            status=str(value.get("status") or "ready"),
            start_seconds=max(0.0, float(value.get("start_seconds") or 0.0)),
            characters=[
                str(item)
                for item in value.get("characters", [])
                if str(item).strip()
            ]
            if isinstance(value.get("characters"), list)
            else [],
            locations=[
                str(item)
                for item in value.get("locations", [])
                if str(item).strip()
            ]
            if isinstance(value.get("locations"), list)
            else [],
            shot=str(value.get("shot") or ""),
            era=str(value.get("era") or ""),
            era_state_id=str(value.get("era_state_id") or ""),
            motion=str(value.get("motion") or "zoom_in"),
            motion_in=str(value.get("motion_in") or ""),
            motion_out=str(value.get("motion_out") or ""),
            transition=str(value.get("transition") or "fade"),
            source_unit_start=_safe_int(value.get("source_unit_start"), -1),
            source_unit_end=_safe_int(value.get("source_unit_end"), -1),
            source_text_start=_safe_int(value.get("source_text_start"), -1),
            source_text_end=_safe_int(value.get("source_text_end"), -1),
            aligned_start_seconds=max(
                0.0,
                float(value.get("aligned_start_seconds") or 0.0),
            ),
            llm_narration=str(value.get("llm_narration") or ""),
            alignment_confidence=max(
                0.0,
                min(1.0, float(value.get("alignment_confidence") or 0.0)),
            ),
            semantic_scene_id=str(value.get("semantic_scene_id") or ""),
            source_proposal_id=str(value.get("source_proposal_id") or ""),
            coverage_index=max(1, _safe_int(value.get("coverage_index"), 1)),
            coverage_count=max(1, _safe_int(value.get("coverage_count"), 1)),
            shot_strategy=str(value.get("shot_strategy") or "semantic_shot"),
            coverage_prompt_version=max(
                1,
                _safe_int(value.get("coverage_prompt_version"), 1),
            ),
            generation_overrides=(
                dict(value.get("generation_overrides", {}))
                if isinstance(value.get("generation_overrides"), dict)
                else {}
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "duration_seconds": self.duration_seconds,
            "narration": self.narration,
            "prompt": self.prompt,
            "image_path": self.image_path,
            "video_path": self.video_path,
            "video_prompt": self.video_prompt,
            "video_frame_role": self.video_frame_role,
            "video_duration_seconds": self.video_duration_seconds,
            "video_motion_in": self.video_motion_in,
            "video_motion_out": self.video_motion_out,
            "status": self.status,
            "start_seconds": self.start_seconds,
            "characters": list(self.characters),
            "locations": list(self.locations),
            "shot": self.shot,
            "era": self.era,
            "era_state_id": self.era_state_id,
            "motion": self.motion,
            "motion_in": self.motion_in,
            "motion_out": self.motion_out,
            "transition": self.transition,
            "source_unit_start": self.source_unit_start,
            "source_unit_end": self.source_unit_end,
            "source_text_start": self.source_text_start,
            "source_text_end": self.source_text_end,
            "aligned_start_seconds": self.aligned_start_seconds,
            "llm_narration": self.llm_narration,
            "alignment_confidence": self.alignment_confidence,
            "semantic_scene_id": self.semantic_scene_id,
            "source_proposal_id": self.source_proposal_id,
            "coverage_index": self.coverage_index,
            "coverage_count": self.coverage_count,
            "shot_strategy": self.shot_strategy,
            "coverage_prompt_version": self.coverage_prompt_version,
            "generation_overrides": dict(self.generation_overrides),
        }


@dataclass
class StoryboardNarrationCue:
    segment_id: int
    sequence_index: int
    start_seconds: float
    duration_seconds: float
    text: str
    timing_ready: bool = True

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> StoryboardNarrationCue:
        return cls(
            segment_id=int(value.get("segment_id") or 0),
            sequence_index=int(value.get("sequence_index") or 0),
            start_seconds=max(0.0, float(value.get("start_seconds") or 0.0)),
            duration_seconds=max(0.0, float(value.get("duration_seconds") or 0.0)),
            text=str(value.get("text") or ""),
            timing_ready=bool(value.get("timing_ready", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "sequence_index": self.sequence_index,
            "start_seconds": self.start_seconds,
            "end_seconds": self.start_seconds + self.duration_seconds,
            "duration_seconds": self.duration_seconds,
            "text": self.text,
            "timing_ready": self.timing_ready,
        }


class _PixmapPreview(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._empty_text = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 135)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setObjectName("storyboardPreview")

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return max(135, round(max(1, width) * 9 / 16))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(320, 180)

    def set_image(self, path: str, empty_text: str) -> None:
        self._source = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self._empty_text = empty_text
        self._refresh_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
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


class _AspectRatioVideoWidget(QVideoWidget):
    """A video surface that occupies the same 16:9 box as a frame preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 135)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return max(135, round(max(1, width) * 9 / 16))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(320, 180)


class StoryboardTimelineCanvas(QWidget):
    """Two-track timeline with draggable scene boundaries."""

    sceneSelected = Signal(str)
    timingChanged = Signal(object)
    timingEditStarted = Signal()
    timingEditFinished = Signal()
    seekRequested = Signal(float)
    transitionSelected = Signal(int)
    selectionCleared = Signal()
    frameContextMenuRequested = Signal(str, QPoint)

    RULER_HEIGHT = 30
    VIDEO_TOP = 34
    VIDEO_HEIGHT = 116
    TEXT_TOP = 158
    TEXT_HEIGHT = 66
    CANVAS_HEIGHT = 232
    HANDLE_WIDTH = 8
    MIN_SCENE_SECONDS = 1.0
    ZOOM_LEVELS = (20, 30, 45, 65, 90, 125, 170)

    def __init__(self, tr: Translate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.scenes: list[StoryboardScene] = []
        self.narration_cues: list[StoryboardNarrationCue] = []
        self.source_duration_seconds = 0.0
        self.selected_scene_id = ""
        self.zoom_index = 2
        self.viewport_width = 700
        self._drag_boundary: int | None = None
        self._drag_origin_x = 0.0
        self._drag_durations: tuple[float, float | None] = (0.0, None)
        self._boundary_drag_started = False
        self._dragging_playhead = False
        self._pixmap_cache: dict[str, QPixmap] = {}
        self.playhead_seconds: float | None = None
        self._text_hover = StoryboardTextHover(self)
        self.selected_transition_index: int | None = None
        self.setFixedHeight(self.CANVAS_HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_width()

    @property
    def pixels_per_second(self) -> int:
        return self.ZOOM_LEVELS[self.zoom_index]

    @property
    def total_seconds(self) -> float:
        scene_total = sum(scene.duration_seconds for scene in self.scenes)
        narration_total = max(
            (
                cue.start_seconds + cue.duration_seconds
                for cue in self.narration_cues
            ),
            default=0.0,
        )
        return max(scene_total, narration_total, self.source_duration_seconds)

    def set_scenes(self, scenes: list[StoryboardScene]) -> None:
        self._text_hover.hide()
        self.scenes = scenes
        self._recalculate_starts()
        if self.selected_scene_id not in {item.scene_id for item in scenes}:
            self.selected_scene_id = scenes[0].scene_id if scenes else ""
        if (
            self.selected_transition_index is not None
            and not 0 <= self.selected_transition_index < len(scenes) - 1
        ):
            self.selected_transition_index = None
        self._pixmap_cache.clear()
        self._update_width()
        self.update()

    def set_narration_cues(
        self,
        cues: list[StoryboardNarrationCue],
    ) -> None:
        self.narration_cues = cues
        self._text_hover.hide()
        self._update_width()
        self.update()

    def set_source_duration(self, seconds: float) -> None:
        self.source_duration_seconds = max(0.0, float(seconds))
        self._update_width()
        self.update()

    def set_viewport_width(self, width: int) -> None:
        self.viewport_width = max(200, width)
        self._update_width()

    def zoom_in(self) -> bool:
        if self.zoom_index >= len(self.ZOOM_LEVELS) - 1:
            return False
        self.zoom_index += 1
        self._update_width()
        self.update()
        return True

    def zoom_out(self) -> bool:
        if self.zoom_index <= 0:
            return False
        self.zoom_index -= 1
        self._update_width()
        self.update()
        return True

    def select_scene(self, scene_id: str, *, emit: bool = False) -> None:
        if scene_id not in {item.scene_id for item in self.scenes}:
            return
        self.selected_scene_id = scene_id
        self.selected_transition_index = None
        self.update()
        if emit:
            self.sceneSelected.emit(scene_id)

    def select_transition(self, boundary_index: int, *, emit: bool = False) -> None:
        if not 0 <= boundary_index < len(self.scenes) - 1:
            return
        self.selected_scene_id = ""
        self.selected_transition_index = boundary_index
        self.update()
        if emit:
            self.transitionSelected.emit(boundary_index)

    def clear_selection(self, *, emit: bool = False) -> None:
        self.selected_scene_id = ""
        self.selected_transition_index = None
        self.update()
        if emit:
            self.selectionCleared.emit()

    def set_playhead(self, seconds: float | None) -> None:
        self.playhead_seconds = (
            None if seconds is None else max(0.0, float(seconds))
        )
        self.update()

    def set_scene_duration(self, scene_id: str, duration: float) -> None:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                scene.duration_seconds = max(
                    self.MIN_SCENE_SECONDS,
                    float(duration),
                )
                self._recalculate_starts()
                self._update_width()
                self.update()
                self.timingChanged.emit(
                    [item.as_dict() for item in self.scenes]
                )
                return

    def adjust_boundary(self, boundary_index: int, delta_seconds: float) -> None:
        """Move one cut while keeping adjacent scenes contiguous."""
        if not 0 <= boundary_index < len(self.scenes):
            return
        first = self.scenes[boundary_index].duration_seconds
        second = (
            self.scenes[boundary_index + 1].duration_seconds
            if boundary_index + 1 < len(self.scenes)
            else None
        )
        self._apply_boundary_delta(
            boundary_index,
            first,
            second,
            float(delta_seconds),
        )

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        base = palette.color(palette.ColorRole.Base)
        alternate = palette.color(palette.ColorRole.AlternateBase)
        text = palette.color(palette.ColorRole.Text)
        muted = palette.color(palette.ColorRole.PlaceholderText)
        highlight = palette.color(palette.ColorRole.Highlight)

        painter.fillRect(self.rect(), base.darker(108))
        painter.fillRect(
            QRect(0, self.VIDEO_TOP, self.width(), self.VIDEO_HEIGHT),
            alternate,
        )
        painter.fillRect(
            QRect(0, self.TEXT_TOP, self.width(), self.TEXT_HEIGHT),
            base,
        )
        painter.setPen(QPen(muted, 1))
        painter.drawLine(0, self.TEXT_TOP - 4, self.width(), self.TEXT_TOP - 4)
        self._paint_ruler(painter, muted)

        self._paint_narration_track(painter, text, muted, highlight)

        if not self.scenes:
            painter.setPen(muted)
            painter.drawText(
                QRect(20, self.VIDEO_TOP, self.width() - 40, self.VIDEO_HEIGHT),
                Qt.AlignmentFlag.AlignCenter,
                self.tr_text(
                    "video_storyboard_empty_timeline",
                    "Generate or load a storyboard to display its scenes here.",
                ),
            )
            return

        for index, scene in enumerate(self.scenes):
            left = round(scene.start_seconds * self.pixels_per_second)
            width = max(2, round(scene.duration_seconds * self.pixels_per_second))
            video_rect = QRect(
                left + 1,
                self.VIDEO_TOP + 3,
                max(1, width - 2),
                self.VIDEO_HEIGHT - 6,
            )
            text_rect = QRect(
                left + 1,
                self.TEXT_TOP + 3,
                max(1, width - 2),
                self.TEXT_HEIGHT - 6,
            )
            self._paint_scene(
                painter,
                scene,
                index,
                video_rect,
                text_rect,
                scene.scene_id == self.selected_scene_id,
                text,
                muted,
                highlight,
            )
        self._paint_selected_transition(painter)
        self._paint_playhead(painter)

    def _paint_selected_transition(self, painter: QPainter) -> None:
        boundary = self.selected_transition_index
        if boundary is None or not 0 <= boundary < len(self.scenes) - 1:
            return
        scene = self.scenes[boundary]
        x = round(
            (scene.start_seconds + scene.duration_seconds)
            * self.pixels_per_second
        )
        color = QColor("#75c9ff")
        painter.save()
        painter.setBrush(color)
        painter.setPen(QPen(color.lighter(120), 1))
        painter.drawRoundedRect(
            QRect(
                x - self.HANDLE_WIDTH,
                self.VIDEO_TOP + 9,
                self.HANDLE_WIDTH,
                self.VIDEO_HEIGHT - 18,
            ),
            2,
            2,
        )
        painter.drawRoundedRect(
            QRect(
                x,
                self.VIDEO_TOP + 9,
                self.HANDLE_WIDTH,
                self.VIDEO_HEIGHT - 18,
            ),
            2,
            2,
        )
        painter.restore()

    def _paint_playhead(self, painter: QPainter) -> None:
        if self.playhead_seconds is None:
            return
        x = round(self.playhead_seconds * self.pixels_per_second)
        if not 0 <= x <= self.width():
            return
        painter.save()
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawLine(x, self.RULER_HEIGHT - 3, x, self.CANVAS_HEIGHT - 2)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(
            [
                QPoint(x - 5, self.RULER_HEIGHT - 3),
                QPoint(x + 5, self.RULER_HEIGHT - 3),
                QPoint(x, self.RULER_HEIGHT + 4),
            ]
        )
        painter.restore()

    def _paint_narration_track(
        self,
        painter: QPainter,
        text: QColor,
        muted: QColor,
        highlight: QColor,
    ) -> None:
        for cue in self.narration_cues:
            left = round(cue.start_seconds * self.pixels_per_second)
            duration = max(0.3, cue.duration_seconds)
            width = max(12, round(duration * self.pixels_per_second))
            rect = QRect(
                left + 1,
                self.TEXT_TOP + 3,
                max(1, width - 2),
                self.TEXT_HEIGHT - 6,
            )
            dark = self.palette().color(self.palette().ColorRole.Base).lightness() < 128
            if cue.timing_ready:
                fill = QColor("#102d35") if dark else QColor("#dbeafe")
                border = highlight.darker(120) if dark else QColor("#3b82f6")
                foreground = QColor("#e6f7fb") if dark else QColor("#172033")
            else:
                fill = QColor("#342710") if dark else QColor("#fef3c7")
                border = QColor("#9a7226") if dark else QColor("#d97706")
                foreground = QColor("#f6e4bd") if dark else QColor("#4a2c00")
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(foreground)
            snippet = painter.fontMetrics().elidedText(
                " ".join(cue.text.split()),
                Qt.TextElideMode.ElideRight,
                max(10, rect.width() - 14),
            )
            painter.drawText(
                rect.adjusted(7, 5, -7, -5),
                Qt.AlignmentFlag.AlignVCenter,
                snippet,
            )

    def _paint_ruler(self, painter: QPainter, muted: QColor) -> None:
        step = (
            1
            if self.pixels_per_second >= 90
            else 2
            if self.pixels_per_second >= 45
            else 5
        )
        total = max(
            self.total_seconds,
            self.width() / max(1, self.pixels_per_second),
        )
        painter.setPen(QPen(muted, 1))
        second = 0
        while second <= total + step:
            x = round(second * self.pixels_per_second)
            painter.drawLine(x, self.RULER_HEIGHT - 7, x, self.RULER_HEIGHT)
            painter.drawText(
                x + 4,
                4,
                70,
                20,
                Qt.AlignmentFlag.AlignLeft,
                self._format_time(second),
            )
            second += step

    def _paint_scene(
        self,
        painter: QPainter,
        scene: StoryboardScene,
        index: int,
        video_rect: QRect,
        text_rect: QRect,
        selected: bool,
        text: QColor,
        muted: QColor,
        highlight: QColor,
    ) -> None:
        border = highlight if selected else QColor("#526680")
        fill = QColor("#17325b") if selected else QColor("#1a2940")
        painter.setBrush(fill)
        painter.setPen(QPen(border, 2 if selected else 1))
        painter.drawRoundedRect(video_rect, 6, 6)

        image_rect = video_rect.adjusted(4, 23, -4, -22)
        pixmap = self._scene_pixmap(scene.image_path)
        if not pixmap.isNull() and image_rect.width() > 8:
            tile_width = max(24, round(image_rect.height() * 16 / 9))
            scaled = pixmap.scaled(
                tile_width,
                image_rect.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            source_x = max(0, (scaled.width() - tile_width) // 2)
            source_y = max(0, (scaled.height() - image_rect.height()) // 2)
            painter.save()
            painter.setClipRect(image_rect)
            x = image_rect.left()
            while x <= image_rect.right():
                visible_width = min(tile_width, image_rect.right() - x + 1)
                painter.drawPixmap(
                    QRect(x, image_rect.top(), visible_width, image_rect.height()),
                    scaled,
                    QRect(source_x, source_y, visible_width, image_rect.height()),
                )
                x += tile_width
                if x <= image_rect.right():
                    painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
                    painter.drawLine(x, image_rect.top(), x, image_rect.bottom())
            painter.restore()
        else:
            painter.fillRect(image_rect, QColor("#0d1727"))
            painter.setPen(muted)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, f"{index + 1}")

        painter.setPen(QColor("#f8fafc"))
        title = self.tr_text(
            "video_storyboard_scene_number",
            "Scene {number}",
            number=index + 1,
        )
        painter.drawText(
            video_rect.adjusted(7, 2, -5, 0),
            Qt.AlignmentFlag.AlignTop,
            title,
        )
        painter.drawText(
            video_rect.adjusted(7, 0, -5, -3),
            Qt.AlignmentFlag.AlignBottom,
            f"{self._format_time(scene.start_seconds)}  ·  {scene.duration_seconds:.1f} s",
        )

        if not self.narration_cues:
            dark = self.palette().color(self.palette().ColorRole.Base).lightness() < 128
            fallback_fill = (
                QColor("#14243a") if selected else QColor("#101d30")
            ) if dark else (
                QColor("#dbeafe") if selected else QColor("#edf4ff")
            )
            painter.setBrush(fallback_fill)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(text_rect, 5, 5)
            painter.setPen(QColor("#eef5ff") if dark else QColor("#172033"))
            snippet = painter.fontMetrics().elidedText(
                " ".join(scene.narration.split()),
                Qt.TextElideMode.ElideRight,
                max(10, text_rect.width() - 14),
            )
            painter.drawText(
                text_rect.adjusted(7, 5, -7, -5),
                Qt.AlignmentFlag.AlignVCenter,
                snippet,
            )

        handle_color = highlight.lighter(125) if selected else QColor("#758aa6")
        painter.fillRect(
            QRect(
                video_rect.left(),
                video_rect.top() + 6,
                self.HANDLE_WIDTH,
                video_rect.height() - 12,
            ),
            handle_color,
        )
        painter.fillRect(
            QRect(
                video_rect.right() - self.HANDLE_WIDTH + 1,
                video_rect.top() + 6,
                self.HANDLE_WIDTH,
                video_rect.height() - 12,
            ),
            handle_color,
        )
        if scene.video_path and Path(scene.video_path).is_file():
            film_color = QColor("#38bdf8")
            film_pen = QPen(film_color, 2)
            film_pen.setStyle(Qt.PenStyle.DashLine)
            film_pen.setDashPattern([5.0, 3.0])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(film_pen)
            painter.drawRoundedRect(video_rect.adjusted(2, 2, -2, -2), 5, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(film_color)
            for x in range(video_rect.left() + 7, video_rect.right() - 4, 11):
                painter.drawRect(x, video_rect.top() + 3, 5, 3)
                painter.drawRect(x, video_rect.bottom() - 5, 5, 3)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._text_hover.hide()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._scene_index_at(event.position().x(), event.position().y())
        boundary = (
            self._boundary_at(
                event.position().x(),
                event.position().y(),
                index,
            )
            if index is not None
            else None
        )
        if boundary is not None:
            self._drag_boundary = boundary
            self._boundary_drag_started = False
            self._drag_origin_x = event.position().x()
            first = self.scenes[boundary].duration_seconds
            second = (
                self.scenes[boundary + 1].duration_seconds
                if boundary + 1 < len(self.scenes)
                else None
            )
            self._drag_durations = (first, second)
            self.grabMouse()
            return
        if 0 <= event.position().y() <= self.CANVAS_HEIGHT:
            if index is not None:
                self.select_scene(self.scenes[index].scene_id, emit=True)
            else:
                self.clear_selection(emit=True)
            self._dragging_playhead = True
            self.grabMouse()
            self._request_seek(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not event.buttons():
            self._show_text_hover(event.position().toPoint())
        if (
            self._drag_boundary is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = (
                event.position().x() - self._drag_origin_x
            ) / self.pixels_per_second
            if not self._boundary_drag_started:
                if abs(event.position().x() - self._drag_origin_x) < 3:
                    return
                self._boundary_drag_started = True
                self.timingEditStarted.emit()
            boundary = self._drag_boundary
            first_original, second_original = self._drag_durations
            self._apply_boundary_delta(
                boundary,
                first_original,
                second_original,
                delta,
            )
            return
        if (
            self._dragging_playhead
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._request_seek(event.position().x())
            return
        index = self._scene_index_at(event.position().x(), event.position().y())
        boundary = (
            self._boundary_at(event.position().x(), event.position().y(), index)
            if index is not None
            else None
        )
        self.setCursor(
            Qt.CursorShape.SizeHorCursor
            if boundary is not None
            else Qt.CursorShape.ArrowCursor
        )

    def _text_block_at(self, point: QPoint):
        if not self.TEXT_TOP + 3 <= point.y() < self.TEXT_TOP + self.TEXT_HEIGHT - 3:
            return None
        blocks = (
            ((cue.start_seconds, cue.duration_seconds, cue.text) for cue in reversed(self.narration_cues))
            if self.narration_cues else
            ((scene.start_seconds, scene.duration_seconds, scene.narration) for scene in reversed(self.scenes))
        )
        for start, seconds, text in blocks:
            left = round(start * self.pixels_per_second)
            width = max(12, round(max(0.3, seconds) * self.pixels_per_second))
            rect = QRect(left + 1, self.TEXT_TOP + 3, max(1, width - 2), self.TEXT_HEIGHT - 6)
            if rect.contains(point) and text.strip():
                return text, start, seconds, rect
        return None

    def _show_text_hover(self, point: QPoint) -> None:
        block = self._text_block_at(point)
        if block is None:
            self._text_hover.schedule_hide()
            return
        text, start, seconds, rect = block
        rect = rect.intersected(self.visibleRegion().boundingRect())
        anchor = QRect(self.mapToGlobal(rect.topLeft()), rect.size())
        self._text_hover.show_text(
            text, f"{self._format_time(start)} – {self._format_time(start + seconds)}",
            anchor, self.mapToGlobal(point),
        )

    def leaveEvent(self, event) -> None:
        self._text_hover.schedule_hide()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._text_hover.hide()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_boundary is not None:
            boundary = self._drag_boundary
            self._drag_boundary = None
            self.releaseMouse()
            if self._boundary_drag_started:
                self.timingEditFinished.emit()
            elif boundary < len(self.scenes) - 1:
                self.select_transition(boundary, emit=True)
            self._boundary_drag_started = False
        if self._dragging_playhead:
            self._request_seek(event.position().x())
            self._dragging_playhead = False
            self.releaseMouse()
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        position = event.pos()
        index = self._scene_index_at(position.x(), position.y())
        if (
            index is None
            or not self.VIDEO_TOP
            <= position.y()
            <= self.VIDEO_TOP + self.VIDEO_HEIGHT
        ):
            super().contextMenuEvent(event)
            return
        scene = self.scenes[index]
        self.select_scene(scene.scene_id, emit=True)
        self.frameContextMenuRequested.emit(scene.scene_id, event.globalPos())
        event.accept()

    def _request_seek(self, x: float) -> None:
        seconds = max(
            0.0,
            min(
                self.total_seconds,
                float(x) / max(1, self.pixels_per_second),
            ),
        )
        self.set_playhead(seconds)
        self.seekRequested.emit(seconds)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        index = self._scene_index_at(event.position().x(), event.position().y())
        if index is not None:
            self.select_scene(self.scenes[index].scene_id, emit=True)

    def _scene_index_at(self, x: float, y: float) -> int | None:
        if not (self.VIDEO_TOP <= y <= self.TEXT_TOP + self.TEXT_HEIGHT):
            return None
        seconds = x / max(1, self.pixels_per_second)
        for index, scene in enumerate(self.scenes):
            if (
                scene.start_seconds
                <= seconds
                <= scene.start_seconds + scene.duration_seconds
            ):
                return index
        return None

    def _boundary_at(self, x: float, y: float, index: int) -> int | None:
        if not (self.VIDEO_TOP <= y <= self.VIDEO_TOP + self.VIDEO_HEIGHT):
            return None
        scene = self.scenes[index]
        left = scene.start_seconds * self.pixels_per_second
        right = (
            scene.start_seconds + scene.duration_seconds
        ) * self.pixels_per_second
        threshold = max(self.HANDLE_WIDTH, 7)
        if index > 0 and abs(x - left) <= threshold:
            return index - 1
        if abs(x - right) <= threshold:
            return index
        return None

    def _scene_pixmap(self, path: str) -> QPixmap:
        if not path or not Path(path).is_file():
            return QPixmap()
        if path not in self._pixmap_cache:
            self._pixmap_cache[path] = QPixmap(path)
        return self._pixmap_cache[path]

    def _apply_boundary_delta(
        self,
        boundary: int,
        first_original: float,
        second_original: float | None,
        delta: float,
    ) -> None:
        if second_original is None:
            self.scenes[boundary].duration_seconds = max(
                self.MIN_SCENE_SECONDS,
                first_original + delta,
            )
        else:
            minimum_delta = self.MIN_SCENE_SECONDS - first_original
            maximum_delta = second_original - self.MIN_SCENE_SECONDS
            delta = max(minimum_delta, min(maximum_delta, delta))
            self.scenes[boundary].duration_seconds = first_original + delta
            self.scenes[boundary + 1].duration_seconds = second_original - delta
        self._recalculate_starts()
        self._update_width()
        self.update()
        self.timingChanged.emit([item.as_dict() for item in self.scenes])

    def _recalculate_starts(self) -> None:
        cursor = 0.0
        for scene in self.scenes:
            scene.start_seconds = cursor
            cursor += scene.duration_seconds

    def _update_width(self) -> None:
        desired = round(self.total_seconds * self.pixels_per_second) + 24
        self.setFixedWidth(max(self.viewport_width, desired, 500))
        self.updateGeometry()

    @staticmethod
    def _format_time(seconds: float) -> str:
        value = max(0, round(seconds))
        return f"{value // 60:02d}:{value % 60:02d}"


class _TimelineScrollArea(QScrollArea):
    def __init__(
        self,
        canvas: StoryboardTimelineCanvas,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.setWidget(canvas)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setFixedHeight(canvas.CANVAS_HEIGHT + 18)
        self.horizontalScrollBar().valueChanged.connect(lambda value: canvas._text_hover.hide())

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self.canvas.set_viewport_width(self.viewport().width())


class _TrackLabels(QWidget):
    # The label widget is top-aligned with the scroll area, so the icons can
    # now be centered in the same coordinate system as their tracks.
    ICON_VERTICAL_OFFSET = 0

    def __init__(self, tr: Translate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.video_icon = ui_icon("video_track", color="#ffffff")
        self.audiobook_icon = ui_icon("audiobook")
        self.setFixedSize(58, StoryboardTimelineCanvas.CANVAS_HEIGHT)
        video_label = self.tr_text("video_storyboard_video_track", "Video")
        audiobook_label = self.tr_text(
            "video_storyboard_text_track", "Audiobook"
        )
        self.setToolTip(f"{video_label} / {audiobook_label}")
        self.setAccessibleName(f"{video_label} / {audiobook_label}")

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(palette.ColorRole.Base)
        painter.fillRect(self.rect(), base.darker(108))
        painter.fillRect(
            QRect(
                0,
                StoryboardTimelineCanvas.VIDEO_TOP,
                self.width(),
                StoryboardTimelineCanvas.VIDEO_HEIGHT,
            ),
            QColor("#1a2940"),
        )
        painter.fillRect(
            QRect(
                0,
                StoryboardTimelineCanvas.TEXT_TOP,
                self.width(),
                StoryboardTimelineCanvas.TEXT_HEIGHT,
            ),
            base,
        )
        self.video_icon.paint(
            painter,
            self._video_icon_rect(),
            Qt.AlignmentFlag.AlignCenter,
        )
        self.audiobook_icon.paint(
            painter,
            self._audiobook_icon_rect(),
            Qt.AlignmentFlag.AlignCenter,
        )

    @classmethod
    def _video_icon_rect(cls) -> QRect:
        track = QRect(
            13,
            StoryboardTimelineCanvas.VIDEO_TOP,
            32,
            StoryboardTimelineCanvas.VIDEO_HEIGHT,
        )
        return QRect(
            track.left(),
            track.center().y() - 16 - cls.ICON_VERTICAL_OFFSET,
            32,
            32,
        )

    @classmethod
    def _audiobook_icon_rect(cls) -> QRect:
        track = QRect(
            13,
            StoryboardTimelineCanvas.TEXT_TOP,
            32,
            StoryboardTimelineCanvas.TEXT_HEIGHT,
        )
        return QRect(
            track.left(),
            track.center().y() - 14 - cls.ICON_VERTICAL_OFFSET,
            28,
            28,
        )


class VideoStoryboardPage(QWidget):
    """Editable storyboard timeline and integration surface for workers."""

    analyzeRequested = Signal(object)
    generateFramesRequested = Signal(object)
    regenerateRequested = Signal(object)
    regenerationAccepted = Signal(str, str, str)
    regenerationRejected = Signal(str, str)
    replaceFrameRequested = Signal(str, str)
    editFrameRequested = Signal(str, object)
    imageEditRequested = Signal(object)
    cancelImageEditRequested = Signal()
    cancelFrameGenerationRequested = Signal()
    storyboardSettingsRequested = Signal()
    generateVideoRequested = Signal(object)
    importVideoRequested = Signal(object)
    generateAllVideosRequested = Signal(object)
    videoCandidateAccepted = Signal(object)
    videoCandidateRejected = Signal(str, str)
    renderRequested = Signal(object)
    openOutputFolderRequested = Signal()
    scenesChanged = Signal(object)
    projectChanged = Signal(object)

    def __init__(self, tr: Translate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self._scenes: list[StoryboardScene] = []
        self._selected_scene_id = ""
        self._candidate_scene_id = ""
        self._candidate_path = ""
        self._rendered_output_path = ""
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []
        self._last_scene_snapshot: dict[str, Any] = {
            "scenes": [],
            "selected_scene_id": "",
        }
        self._history_restoring = False
        self._timing_history_active = False
        self._prompt_history_open = False
        self._frame_generation_history_active = False
        self._frame_generation_history_recorded = False
        self._frame_generation_start_snapshot: dict[str, Any] | None = None
        self._selected_transition_index: int | None = None
        self._updating_transition_controls = False
        self._updating_frame_motion = False
        self._updating_video_motion = False
        self._inspector_scene_id = ""
        self._loaded_scene_video_path = ""
        self._scene_video_pause_on_first_frame = False
        self._audiobook_text = ""
        self._source_project_id: int | None = None
        self._source_project_dir = ""
        self._source_title = ""
        self._audio_path = ""
        self._voice_start_offset_seconds = 0.0
        self._narration_cues: list[StoryboardNarrationCue] = []
        self._source_duration_seconds = 0.0
        self._plan_metadata: dict[str, Any] = {}
        self._configuration: dict[str, Any] = {}
        self._restoring_duration = False
        self._updating_project_controls = False
        self._updating_continuity_trees = False
        self._detected_era_value = ""
        self._frame_batch_dialog: VideoStoryboardFrameBatchDialog | None = None
        self._frame_batch_mode = ""
        self._frame_batch_completed = 0
        self._image_edit_dialog: VideoStoryboardImageEditDialog | None = None
        self._ffmpeg_path = "ffmpeg/ffmpeg.exe"
        self._regeneration_dialog: VideoStoryboardRegenerationDialog | None = None
        self._video_dialog: VideoStoryboardVideoDialog | None = None
        self._video_edit_dialog: StoryboardVideoEditDialog | None = None
        self._video_copy_task: VideoLastFrameCopy | None = None
        self._build_ui()
        self._setup_preview_player()
        self.set_configuration({})
        self.set_scenes([])

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.beta_guide_link = QLabel()
        self.beta_guide_link.setTextFormat(Qt.TextFormat.RichText)
        self.beta_guide_link.setText(
            f'<a href="{STORYBOARD_GUIDE_URL}">'
            + self.tr_text("video_storyboard_beta_guide", "Guide, demos and development")
            + " ↗</a>"
        )
        self.beta_guide_link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.beta_guide_link.setOpenExternalLinks(True)
        self.beta_guide_link.setWordWrap(True)
        layout.addWidget(self.beta_guide_link)

        toolbar = QFrame()
        toolbar.setObjectName("card")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 10, 14, 10)
        self.status_label = QLabel()
        self.status_label.setObjectName("helperLabel")
        toolbar_layout.addWidget(self.status_label)
        self.rendered_video_button = QPushButton()
        self.rendered_video_button.setIcon(ui_icon("convert_video"))
        self.rendered_video_button.setFlat(True)
        self.rendered_video_button.hide()
        toolbar_layout.addWidget(self.rendered_video_button)
        self.source_summary_label = QLabel()
        self.source_summary_label.setObjectName("helperLabel")
        toolbar_layout.addWidget(self.source_summary_label)
        toolbar_layout.addStretch(1)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setIcon(ui_icon("zoom_out"))
        self.zoom_out_button.setToolTip(self.tr_text("zoom_out", "Zoom out"))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QPushButton()
        self.zoom_in_button.setIcon(ui_icon("zoom_in"))
        self.zoom_in_button.setToolTip(self.tr_text("zoom_in", "Zoom in"))
        self.analyze_button = QPushButton(
            self.tr_text(
                "video_storyboard_analyze_audiobook",
                "Analyze audiobook",
            )
        )
        self.analyze_button.setIcon(ui_icon("storyboard"))
        self.generate_frames_button = QPushButton(
            self.tr_text(
                "video_storyboard_generate_frames",
                "Generate frames",
            )
        )
        self.generate_frames_button.setIcon(ui_icon("bolt"))
        self.generate_all_videos_button = QPushButton(
            self.tr_text(
                "video_storyboard_generate_all_videos",
                "Generate all videos",
            )
        )
        self.generate_all_videos_button.setIcon(ui_icon("convert_video"))
        self.render_button = QPushButton(
            self.tr_text(
                "video_storyboard_render_final",
                "Render final video",
            )
        )
        self.render_button.setObjectName("primaryButton")
        self.render_button.setIcon(ui_icon("render"))
        self.open_output_folder_button = QPushButton(
            self.tr_text("open_output_folder", "Open output folder")
        )
        self.open_output_folder_button.setIcon(ui_icon("folder"))
        toolbar_layout.addWidget(self.analyze_button)
        toolbar_layout.addWidget(self.generate_frames_button)
        toolbar_layout.addWidget(self.generate_all_videos_button)
        toolbar_layout.addWidget(self.render_button)
        toolbar_layout.addWidget(self.open_output_folder_button)
        layout.addWidget(toolbar)

        editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        timeline_card = QFrame()
        timeline_card.setObjectName("card")
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(12, 12, 12, 12)
        heading_row = QHBoxLayout()
        heading = QLabel(
            self.tr_text(
                "video_storyboard_timeline",
                "Timeline",
            )
        )
        heading.setObjectName("sectionTitle")
        self.timeline_heading = heading
        self.copy_last_frame_button = self._timeline_action_button(
            "copy", self.tr_text("storyboard_copy_last_frame", "Copy Last Frame")
        )
        self.edit_video_button = self._timeline_action_button(
            "edit", self.tr_text("storyboard_edit_video", "Edit Video")
        )
        self.delete_video_button = self._timeline_action_button(
            "delete", self.tr_text("storyboard_delete_video", "Delete Video")
        )
        self.timeline_regenerate_video_button = self._timeline_action_button(
            "regenerate", self.tr_text("video_storyboard_regenerate_video", "Regenerate Video")
        )
        self.copy_frame_button = self._timeline_action_button(
            "copy", self.tr_text("video_storyboard_copy_frame", "Copy")
        )
        self.paste_frame_button = self._timeline_action_button(
            "paste", self.tr_text("video_storyboard_paste_frame", "Paste")
        )
        self.replace_frame_button = self._timeline_action_button(
            "replace_image",
            self.tr_text(
                "video_storyboard_replace_frame",
                "Replace frame with an image",
            ),
        )
        self.timeline_regenerate_button = self._timeline_action_button(
            "bolt",
            self.tr_text(
                "video_storyboard_regenerate_frame",
                "Regenerate frame",
            ),
        )
        self.edit_frame_button = self._timeline_action_button(
            "edit",
            self.tr_text(
                "video_storyboard_edit_frame",
                "Edit frame",
            ),
        )
        self.split_frame_button = self._timeline_action_button(
            "split",
            self.tr_text(
                "video_storyboard_split_frame",
                "Split frame into two",
            ),
        )
        self.delete_frame_button = self._timeline_action_button(
            "delete",
            self.tr_text(
                "video_storyboard_clear_frame",
                "Clear frame",
            ),
        )
        self.convert_frame_video_button = self._timeline_action_button(
            "convert_video",
            self.tr_text(
                "video_storyboard_convert_to_video",
                "Generate scene video",
            ),
        )
        self.undo_button = self._timeline_action_button(
            "undo",
            self.tr_text("video_storyboard_undo", "Undo"),
        )
        self.redo_button = self._timeline_action_button(
            "redo",
            self.tr_text("video_storyboard_redo", "Redo"),
        )
        self.transition_label = QLabel(
            self.tr_text("video_storyboard_transition", "Transition")
        )
        self.transition_label.setObjectName("sectionTitle")
        self.transition_combo = QComboBox()
        self.transition_combo.setMinimumWidth(145)
        for value, label in _TRANSITION_OPTIONS:
            self.transition_combo.addItem(
                self.tr_text(
                    f"video_storyboard_transition_{value}",
                    label,
                ),
                value,
            )
        self.transition_label.hide()
        self.transition_combo.hide()
        heading_row.addWidget(heading)
        heading_row.addSpacing(12)
        heading_row.addWidget(self.transition_label)
        heading_row.addWidget(self.transition_combo)
        heading_row.addStretch(1)
        heading_row.addWidget(self.undo_button)
        heading_row.addWidget(self.redo_button)
        heading_row.addWidget(self.copy_frame_button)
        heading_row.addWidget(self.paste_frame_button)
        heading_row.addWidget(self.replace_frame_button)
        heading_row.addWidget(self.timeline_regenerate_button)
        heading_row.addWidget(self.edit_frame_button)
        heading_row.addWidget(self.split_frame_button)
        heading_row.addWidget(self.delete_frame_button)
        heading_row.addWidget(self.convert_frame_video_button)
        heading_row.addWidget(self.copy_last_frame_button)
        heading_row.addWidget(self.edit_video_button)
        heading_row.addWidget(self.delete_video_button)
        heading_row.addWidget(self.timeline_regenerate_video_button)
        timeline_layout.addLayout(heading_row)
        tracks = QHBoxLayout()
        tracks.setSpacing(0)
        self.track_labels = _TrackLabels(self.tr_text)
        tracks.addWidget(
            self.track_labels,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        self.timeline_canvas = StoryboardTimelineCanvas(self.tr_text)
        self.timeline_scroll = _TimelineScrollArea(self.timeline_canvas)
        tracks.addWidget(self.timeline_scroll, 1)
        timeline_layout.addLayout(tracks)

        playback_row = QHBoxLayout()
        self.preview_play_button = QPushButton(
            self.tr_text("play", "Play")
        )
        self.preview_play_button.setIcon(ui_icon("play"))
        self.preview_pause_button = QPushButton(
            self.tr_text("pause", "Pause")
        )
        self.preview_pause_button.setIcon(ui_icon("pause"))
        self.preview_stop_button = QPushButton(
            self.tr_text("stop", "Stop")
        )
        self.preview_stop_button.setIcon(ui_icon("stop"))
        self.preview_time_label = QLabel("00:00 / 00:00")
        self.preview_time_label.setObjectName("helperLabel")
        playback_row.addWidget(self.preview_play_button)
        playback_row.addWidget(self.preview_pause_button)
        playback_row.addWidget(self.preview_stop_button)
        playback_row.addSpacing(8)
        playback_row.addWidget(self.preview_time_label)
        playback_row.addStretch(1)
        playback_row.addWidget(self.zoom_out_button)
        playback_row.addWidget(self.zoom_label)
        playback_row.addWidget(self.zoom_in_button)
        timeline_layout.addLayout(playback_row)
        timeline_layout.addWidget(self._build_project_controls())
        editor_splitter.addWidget(timeline_card)

        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        inspector_scroll.setMinimumWidth(320)
        inspector_scroll.setMaximumWidth(440)
        inspector_scroll.setWidget(self._build_inspector())
        editor_splitter.addWidget(inspector_scroll)
        editor_splitter.setStretchFactor(0, 1)
        editor_splitter.setStretchFactor(1, 0)
        layout.addWidget(editor_splitter, 1)

        activity = QFrame()
        activity.setObjectName("card")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(12, 10, 12, 10)
        activity_header = QHBoxLayout()
        activity_title = QLabel(
            self.tr_text("video_storyboard_activity", "Activity")
        )
        activity_title.setObjectName("sectionTitle")
        self.operation_label = QLabel(
            self.tr_text("video_storyboard_ready", "Ready")
        )
        self.operation_label.setObjectName("helperLabel")
        self.operation_progress = QProgressBar()
        self.operation_progress.setFixedWidth(180)
        self.operation_progress.hide()
        activity_header.addWidget(activity_title)
        activity_header.addWidget(self.operation_label, 1)
        activity_header.addWidget(self.operation_progress)
        activity_layout.addLayout(activity_header)
        self.activity_log = QPlainTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(92)
        self.activity_log.setPlaceholderText(
            self.tr_text(
                "video_storyboard_activity_empty",
                "Generation and rendering progress will appear here.",
            )
        )
        activity_layout.addWidget(self.activity_log)
        layout.addWidget(activity)

        self.timeline_canvas.sceneSelected.connect(self._select_scene)
        self.timeline_canvas.timingChanged.connect(self._on_timing_changed)
        self.timeline_canvas.timingEditStarted.connect(
            self._begin_timing_history
        )
        self.timeline_canvas.timingEditFinished.connect(
            self._finish_timing_history
        )
        self.timeline_canvas.transitionSelected.connect(
            self._select_transition
        )
        self.timeline_canvas.selectionCleared.connect(
            self._clear_timeline_selection
        )
        self.timeline_canvas.frameContextMenuRequested.connect(
            self._open_frame_context_menu
        )
        self.timeline_canvas.seekRequested.connect(self._seek_preview)
        self.replace_frame_button.clicked.connect(self._request_frame_replacement)
        self.copy_frame_button.clicked.connect(self._copy_selected_frame)
        self.copy_last_frame_button.clicked.connect(self._copy_video_last_frame)
        self.edit_video_button.clicked.connect(self._request_video_edit)
        self.delete_video_button.clicked.connect(self._delete_selected_video)
        self.timeline_regenerate_video_button.clicked.connect(self._request_video_generation)
        self.paste_frame_button.clicked.connect(self._paste_selected_frame)
        self.timeline_regenerate_button.clicked.connect(
            self._request_regeneration
        )
        self.edit_frame_button.clicked.connect(self._request_frame_edit)
        self.split_frame_button.clicked.connect(self._split_selected_frame)
        self.delete_frame_button.clicked.connect(self._delete_selected_frame)
        self.convert_frame_video_button.clicked.connect(
            self._request_video_generation
        )
        self.undo_button.clicked.connect(self._undo_scene_edit)
        self.redo_button.clicked.connect(self._redo_scene_edit)
        self.transition_combo.currentIndexChanged.connect(
            self._transition_changed
        )
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.analyze_button.clicked.connect(self._request_analysis)
        self.generate_frames_button.clicked.connect(
            self._request_frame_generation
        )
        self.generate_all_videos_button.clicked.connect(
            self._request_generate_all_videos
        )
        self.render_button.clicked.connect(self._request_render)
        self.open_output_folder_button.clicked.connect(
            self.openOutputFolderRequested.emit
        )
        self.rendered_video_button.clicked.connect(self._open_rendered_video)
        self.preview_play_button.clicked.connect(self._play_preview)
        self.preview_pause_button.clicked.connect(self._pause_preview)
        self.preview_stop_button.clicked.connect(self.stop_preview)

    def _timeline_action_button(self, icon: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setIcon(ui_icon(icon))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(32, 30)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    def _continuity_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setItemDelegate(_ContinuityItemDelegate(tree))
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setHeaderLabels(
            [
                self.tr_text("video_storyboard_continuity_entity", "Entity / state"),
                self.tr_text("video_storyboard_continuity_from", "From"),
                self.tr_text("video_storyboard_continuity_to", "To"),
                self.tr_text("video_storyboard_continuity_definition", "Visual definition"),
            ]
        )
        tree.setColumnWidth(0, 175)
        tree.setColumnWidth(1, 68)
        tree.setColumnWidth(2, 68)
        tree.itemChanged.connect(self._continuity_item_changed)
        return tree

    def _build_project_controls(self) -> QWidget:
        group = QGroupBox(
            self.tr_text(
                "video_storyboard_project_direction",
                "Project visual direction and video overrides",
            )
        )
        outer = QVBoxLayout(group)
        tabs = QTabWidget()
        self.project_tabs = tabs
        gallery_page = QWidget()
        gallery_layout = QVBoxLayout(gallery_page)
        gallery_layout.setContentsMargins(8, 8, 8, 8)
        gallery_hint = QLabel(
            self.tr_text(
                "video_storyboard_style_gallery_help",
                "Choose the visual look for every frame in this audiobook.",
            )
        )
        gallery_hint.setObjectName("helperLabel")
        gallery_layout.addWidget(gallery_hint)
        self.style_gallery = QListWidget()
        self.style_gallery.setObjectName("storyboardStyleGallery")
        self.style_gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.style_gallery.setMovement(QListWidget.Movement.Static)
        self.style_gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.style_gallery.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.style_gallery.setIconSize(QSize(144, 144))
        self.style_gallery.setGridSize(QSize(180, 184))
        self.style_gallery.setSpacing(6)
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
        tabs.addTab(
            gallery_page,
            self.tr_text("video_storyboard_styles_tab", "Styles"),
        )

        style_page = QWidget()
        style_page_layout = QVBoxLayout(style_page)
        style_page_layout.setContentsMargins(8, 8, 8, 8)
        style_form = QFormLayout()
        self.style_medium_edit = QLineEdit()
        self.style_palette_edit = QLineEdit()
        self.style_lighting_edit = QLineEdit()
        self.style_negative_edit = QLineEdit()
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_seed_automatic",
                "Automatic from audiobook text",
            )
        )
        self.seed_edit.setToolTip(
            self.tr_text(
                "video_storyboard_seed_help",
                "Use the same seed to reproduce the same image-generation sequence.",
            )
        )
        self.story_era_edit = QLineEdit()
        self.story_era_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_era_automatic",
                "Automatic (present day when the text gives no historical clues)",
            )
        )
        style_form.addRow(
            self.tr_text("video_storyboard_style_medium", "Visual medium"),
            self.style_medium_edit,
        )
        style_form.addRow(
            self.tr_text("video_storyboard_style_palette", "Color palette"),
            self.style_palette_edit,
        )
        style_form.addRow(
            self.tr_text("video_storyboard_style_lighting", "Lighting"),
            self.style_lighting_edit,
        )
        style_form.addRow(
            self.tr_text("video_storyboard_style_negative", "Avoid / negative"),
            self.style_negative_edit,
        )
        style_form.addRow(
            self.tr_text("video_storyboard_seed", "Seed"),
            self.seed_edit,
        )
        style_page_layout.addLayout(style_form)
        style_page_layout.addStretch(1)
        tabs.addTab(
            style_page,
            self.tr_text(
                "video_storyboard_style_details_tab",
                "Style details",
            ),
        )

        narrative_page = QWidget()
        narrative_layout = QVBoxLayout(narrative_page)
        narrative_layout.setContentsMargins(8, 8, 8, 8)
        narrative_form = QFormLayout()
        narrative_form.addRow(
            self.tr_text("video_storyboard_story_era", "Historical period / era"),
            self.story_era_edit,
        )
        narrative_layout.addLayout(narrative_form)
        self.continuity_eras_tree = self._continuity_tree()
        narrative_layout.addWidget(self.continuity_eras_tree, 1)
        tabs.addTab(
            narrative_page,
            self.tr_text(
                "video_storyboard_historical_periods_tab",
                "Historical periods",
            ),
        )

        characters_page = QWidget()
        characters_layout = QVBoxLayout(characters_page)
        characters_layout.setContentsMargins(8, 8, 8, 8)
        self.style_characters_edit = QPlainTextEdit()
        self.style_characters_edit.setPlaceholderText(
            "Mara: 30 years old, short dark hair, green coat"
        )
        self.continuity_characters_panel = QWidget()
        characters_panel_layout = QHBoxLayout(
            self.continuity_characters_panel
        )
        characters_panel_layout.setContentsMargins(0, 0, 0, 0)
        characters_panel_layout.setSpacing(8)
        self.continuity_characters_tree = self._continuity_tree()
        characters_panel_layout.addWidget(self.continuity_characters_tree, 1)
        character_actions = QVBoxLayout()
        self.new_character_button = QPushButton(
            self.tr_text("video_storyboard_new_character", "New character")
        )
        self.new_character_button.setIcon(ui_icon("add"))
        self.edit_character_button = QPushButton(
            self.tr_text("video_storyboard_edit_character", "Edit character")
        )
        self.edit_character_button.setIcon(ui_icon("edit"))
        self.edit_character_button.setEnabled(False)
        self.delete_character_button = QPushButton(
            self.tr_text("video_storyboard_delete_character", "Delete character")
        )
        self.delete_character_button.setIcon(ui_icon("delete", danger=True))
        self.delete_character_button.setEnabled(False)
        self.new_character_state_button = QPushButton(
            self.tr_text("video_storyboard_new_character_state", "New state")
        )
        self.new_character_state_button.setIcon(ui_icon("add"))
        self.delete_character_state_button = QPushButton(
            self.tr_text("video_storyboard_delete_character_state", "Delete state")
        )
        self.delete_character_state_button.setIcon(ui_icon("delete", danger=True))
        self.new_character_state_button.setEnabled(False)
        self.delete_character_state_button.setEnabled(False)
        character_actions.addWidget(self.new_character_button)
        character_actions.addWidget(self.edit_character_button)
        character_actions.addWidget(self.delete_character_button)
        character_actions.addSpacing(8)
        character_actions.addWidget(self.new_character_state_button)
        character_actions.addWidget(self.delete_character_state_button)
        character_actions.addStretch(1)
        characters_panel_layout.addLayout(character_actions)
        characters_layout.addWidget(self.continuity_characters_panel, 1)
        self.manual_characters_label = QLabel(
            self.tr_text(
                "video_storyboard_manual_character_definitions",
                "Initial character definitions (one per line, used before analysis)",
            )
        )
        characters_layout.addWidget(self.manual_characters_label)
        self.style_characters_edit.setMaximumHeight(100)
        characters_layout.addWidget(self.style_characters_edit)
        self.continuity_characters_tree.itemSelectionChanged.connect(
            self._sync_character_state_actions
        )
        self.new_character_button.clicked.connect(self._new_character)
        self.edit_character_button.clicked.connect(self._edit_character)
        self.delete_character_button.clicked.connect(self._delete_character)
        self.new_character_state_button.clicked.connect(
            self._new_character_state
        )
        self.delete_character_state_button.clicked.connect(
            self._delete_character_state
        )
        tabs.addTab(
            characters_page,
            self.tr_text("video_storyboard_characters_tab", "Characters"),
        )

        locations_page = QWidget()
        locations_layout = QVBoxLayout(locations_page)
        locations_layout.setContentsMargins(8, 8, 8, 8)
        locations_panel = QWidget()
        locations_panel_layout = QHBoxLayout(locations_panel)
        locations_panel_layout.setContentsMargins(0, 0, 0, 0)
        locations_panel_layout.setSpacing(8)
        self.continuity_locations_tree = self._continuity_tree()
        locations_panel_layout.addWidget(self.continuity_locations_tree, 1)
        location_actions = QVBoxLayout()
        self.new_location_button = QPushButton(
            self.tr_text("video_storyboard_new_location", "New location")
        )
        self.new_location_button.setIcon(ui_icon("add"))
        self.edit_location_button = QPushButton(
            self.tr_text("video_storyboard_edit_location", "Edit location")
        )
        self.edit_location_button.setIcon(ui_icon("edit"))
        self.delete_location_button = QPushButton(
            self.tr_text("video_storyboard_delete_location", "Delete location")
        )
        self.delete_location_button.setIcon(ui_icon("delete", danger=True))
        self.new_location_state_button = QPushButton(
            self.tr_text("video_storyboard_new_location_state", "New state")
        )
        self.new_location_state_button.setIcon(ui_icon("add"))
        self.delete_location_state_button = QPushButton(
            self.tr_text("video_storyboard_delete_location_state", "Delete state")
        )
        self.delete_location_state_button.setIcon(ui_icon("delete", danger=True))
        for button in (
            self.edit_location_button,
            self.delete_location_button,
            self.new_location_state_button,
            self.delete_location_state_button,
        ):
            button.setEnabled(False)
        location_actions.addWidget(self.new_location_button)
        location_actions.addWidget(self.edit_location_button)
        location_actions.addWidget(self.delete_location_button)
        location_actions.addSpacing(8)
        location_actions.addWidget(self.new_location_state_button)
        location_actions.addWidget(self.delete_location_state_button)
        location_actions.addStretch(1)
        locations_panel_layout.addLayout(location_actions)
        locations_layout.addWidget(locations_panel, 1)
        self.continuity_locations_tree.itemSelectionChanged.connect(
            self._sync_location_state_actions
        )
        self.new_location_button.clicked.connect(self._new_location)
        self.edit_location_button.clicked.connect(self._edit_location)
        self.delete_location_button.clicked.connect(self._delete_location)
        self.new_location_state_button.clicked.connect(self._new_location_state)
        self.delete_location_state_button.clicked.connect(self._delete_location_state)
        tabs.addTab(
            locations_page,
            self.tr_text("video_storyboard_locations_tab", "Locations"),
        )

        video_page = QWidget()
        video_page_layout = QVBoxLayout(video_page)
        video_page_layout.setContentsMargins(8, 8, 8, 8)
        render_form = QFormLayout()
        self.video_zoom_spin = QDoubleSpinBox()
        self.video_zoom_spin.setRange(0.0, 1_000_000.0)
        self.video_zoom_spin.setDecimals(1)
        self.video_zoom_spin.setSingleStep(1.0)
        self.video_zoom_spin.setSuffix(" %")
        self.video_zoom_spin.setToolTip(
            self.tr_text(
                "video_storyboard_zoom_travel_help",
                "Total smooth zoom applied from the first to the last frame of each scene.",
            )
        )
        render_form.addRow(
            self.tr_text(
                "video_storyboard_zoom_travel",
                "Video zoom travel",
            ),
            self.video_zoom_spin,
        )
        self.video_transition_spin = QDoubleSpinBox()
        self.video_transition_spin.setRange(0.0, 3.0)
        self.video_transition_spin.setDecimals(2)
        self.video_transition_spin.setSingleStep(0.1)
        self.video_transition_spin.setSuffix(" s")
        render_form.addRow(
            self.tr_text(
                "video_storyboard_transition_duration",
                "Transition duration",
            ),
            self.video_transition_spin,
        )
        self.default_motion_combo = QComboBox()
        self.default_motion_combo.addItem(
            self.tr_text("zoom_in", "Zoom in"), "zoom_in"
        )
        self.default_motion_combo.addItem(
            self.tr_text("zoom_out", "Zoom out"), "zoom_out"
        )
        self.default_motion_combo.addItem(
            self.tr_text("video_storyboard_motion_alternate", "Alternate"),
            "alternate",
        )
        render_form.addRow(
            self.tr_text(
                "video_storyboard_default_motion",
                "Default motion",
            ),
            self.default_motion_combo,
        )
        video_page_layout.addLayout(render_form)

        video_page_layout.addStretch(1)
        tabs.addTab(
            video_page,
            self.tr_text("video_storyboard_video_motion", "Video motion"),
        )
        outer.addWidget(tabs)
        regenerate_row = QHBoxLayout()
        regenerate_row.addStretch(1)
        self.regenerate_all_button = QPushButton(
            self.tr_text(
                "video_storyboard_regenerate_all",
                "Regenerate all frames",
            )
        )
        self.regenerate_all_button.setIcon(ui_icon("regenerate"))
        regenerate_row.addWidget(self.regenerate_all_button)
        outer.addLayout(regenerate_row)

        for editor in (
            self.style_medium_edit,
            self.style_palette_edit,
            self.style_lighting_edit,
            self.seed_edit,
            self.story_era_edit,
            self.style_negative_edit,
        ):
            editor.textChanged.connect(self._project_controls_changed)
        self.style_characters_edit.textChanged.connect(
            self._project_controls_changed
        )
        self.style_gallery.currentItemChanged.connect(
            self._style_gallery_changed
        )
        self.video_zoom_spin.valueChanged.connect(
            self._project_controls_changed
        )
        self.video_transition_spin.valueChanged.connect(
            self._project_controls_changed
        )
        self.default_motion_combo.currentIndexChanged.connect(
            self._project_controls_changed
        )
        self.regenerate_all_button.clicked.connect(
            self._request_regenerate_all
        )
        return group

    def _custom_style_icon(self) -> QIcon:
        pixmap = QPixmap(144, 144)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self.palette().color(self.palette().ColorRole.Base))
        painter.setPen(QPen(self.palette().color(self.palette().ColorRole.Mid), 2))
        painter.drawRoundedRect(QRect(2, 2, 140, 140), 10, 10)
        painter.setPen(self.palette().color(self.palette().ColorRole.Text))
        font = painter.font()
        font.setPointSize(30)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "+")
        painter.end()
        return QIcon(pixmap)

    def _build_inspector(self) -> QWidget:
        inspector = QFrame()
        inspector.setObjectName("card")
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(14, 14, 14, 14)
        heading = QLabel(
            self.tr_text(
                "video_storyboard_scene_inspector",
                "Selected scene",
            )
        )
        heading.setObjectName("sectionTitle")
        self.inspector_heading = heading
        self.scene_meta_label = QLabel(
            self.tr_text(
                "video_storyboard_no_scene_selected",
                "No scene selected",
            )
        )
        self.scene_meta_label.setObjectName("helperLabel")
        heading.hide()
        self.scene_meta_label.setObjectName("sectionTitle")
        layout.addWidget(self.scene_meta_label)

        duration_row = QHBoxLayout()
        duration_row.addWidget(
            QLabel(self.tr_text("video_storyboard_duration", "Duration"))
        )
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.001, 600.0)
        self.duration_spin.setDecimals(3)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setSuffix(" s")
        duration_row.addWidget(self.duration_spin, 1)
        layout.addLayout(duration_row)

        self.inspector_tabs = QTabWidget()
        self.video_inspector_tab = QWidget()
        video_layout = QVBoxLayout(self.video_inspector_tab)
        video_layout.setContentsMargins(8, 10, 8, 8)
        self.scene_video_widget = _AspectRatioVideoWidget()
        self.scene_video_widget.setStyleSheet("background: #090f1a;")
        video_layout.addWidget(self.scene_video_widget)

        video_playback = QHBoxLayout()
        self.scene_video_play_button = QPushButton(self.tr_text("play", "Play"))
        self.scene_video_play_button.setIcon(ui_icon("play"))
        self.scene_video_pause_button = QPushButton(self.tr_text("pause", "Pause"))
        self.scene_video_pause_button.setIcon(ui_icon("pause"))
        self.scene_video_stop_button = QPushButton(self.tr_text("stop", "Stop"))
        self.scene_video_stop_button.setIcon(ui_icon("stop"))
        video_playback.addWidget(self.scene_video_play_button)
        video_playback.addWidget(self.scene_video_pause_button)
        video_playback.addWidget(self.scene_video_stop_button)
        video_layout.addLayout(video_playback)

        video_motion_row = QHBoxLayout()
        video_motion_row.addWidget(
            QLabel(self.tr_text("video_storyboard_motion_in", "In"))
        )
        self.video_motion_in_combo = self._frame_motion_combo()
        video_motion_row.addWidget(self.video_motion_in_combo, 1)
        video_motion_row.addSpacing(8)
        video_motion_row.addWidget(
            QLabel(self.tr_text("video_storyboard_motion_out", "Out"))
        )
        self.video_motion_out_combo = self._frame_motion_combo()
        video_motion_row.addWidget(self.video_motion_out_combo, 1)
        video_layout.addLayout(video_motion_row)

        video_prompt_label = QLabel(
            self.tr_text("video_storyboard_video_prompt", "Video prompt")
        )
        video_prompt_label.setObjectName("sectionTitle")
        self.scene_video_prompt_edit = QPlainTextEdit()
        self.scene_video_prompt_edit.setReadOnly(True)
        self.scene_video_prompt_edit.setMaximumHeight(110)
        self._video_prompt_highlighter = VideoStoryboardPromptHighlighter(
            self.scene_video_prompt_edit.document()
        )
        video_layout.addWidget(video_prompt_label)
        video_layout.addWidget(self.scene_video_prompt_edit)
        self.regenerate_video_button = QPushButton(
            self.tr_text(
                "video_storyboard_regenerate_video",
                "Regenerate video",
            )
        )
        self.regenerate_video_button.setIcon(ui_icon("convert_video"))
        video_actions_row = QHBoxLayout()
        video_actions_row.addWidget(self.regenerate_video_button, 1)
        self.inspector_edit_video_button = QPushButton(
            self.tr_text("storyboard_edit_video", "Edit Video")
        )
        self.inspector_edit_video_button.setIcon(ui_icon("edit"))
        self.inspector_edit_video_button.clicked.connect(self._request_video_edit)
        video_actions_row.addWidget(self.inspector_edit_video_button, 1)
        video_layout.addLayout(video_actions_row)
        video_layout.addStretch(1)

        self.frame_inspector_tab = QWidget()
        frame_layout = QVBoxLayout(self.frame_inspector_tab)
        frame_layout.setContentsMargins(8, 10, 8, 8)
        self.current_preview = _PixmapPreview()
        frame_layout.addWidget(self.current_preview)
        for preview in (self.current_preview, self.scene_video_widget):
            preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            preview.customContextMenuRequested.connect(
                lambda point, preview=preview: self._open_frame_context_menu(
                    self._selected_scene_id, preview.mapToGlobal(point)
                )
            )

        motion_row = QHBoxLayout()
        motion_row.addWidget(QLabel(self.tr_text("video_storyboard_motion_in", "In")))
        self.motion_in_combo = self._frame_motion_combo()
        motion_row.addWidget(self.motion_in_combo, 1)
        motion_row.addSpacing(8)
        motion_row.addWidget(QLabel(self.tr_text("video_storyboard_motion_out", "Out")))
        self.motion_out_combo = self._frame_motion_combo()
        motion_row.addWidget(self.motion_out_combo, 1)
        frame_layout.addLayout(motion_row)

        narration_label = QLabel(
            self.tr_text(
                "video_storyboard_narration",
                "Audiobook text",
            )
        )
        narration_label.setObjectName("sectionTitle")
        self.narration_edit = QPlainTextEdit()
        self.narration_edit.setReadOnly(True)
        self.narration_edit.setMaximumHeight(95)
        narration_label.hide()
        self.narration_edit.hide()

        prompt_label = QLabel(
            self.tr_text(
                "video_storyboard_generation_prompt",
                "Generation prompt (English)",
            )
        )
        prompt_label.setObjectName("sectionTitle")
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setReadOnly(False)
        self.prompt_edit.setMaximumHeight(135)
        self._prompt_highlighter = VideoStoryboardPromptHighlighter(
            self.prompt_edit.document()
        )
        frame_layout.addWidget(prompt_label)
        frame_layout.addWidget(self.prompt_edit)
        self.regenerate_button = QPushButton(
            self.tr_text(
                "video_storyboard_regenerate_frame",
                "Regenerate frame",
            )
        )
        self.regenerate_button.setIcon(ui_icon("bolt"))
        self.inspector_edit_frame_button = QPushButton(
            self.tr_text(
                "video_storyboard_edit_frame",
                "Edit frame",
            )
        )
        self.inspector_edit_frame_button.setIcon(ui_icon("edit"))
        frame_actions = QHBoxLayout()
        frame_actions.addWidget(self.regenerate_button, 1)
        frame_actions.addWidget(self.inspector_edit_frame_button, 1)
        frame_layout.addLayout(frame_actions)
        self.inspector_generate_video_button = QPushButton(self.tr_text("video_storyboard_generate_video_for_scene", "Generate Video for this scene"))
        self.inspector_generate_video_button.setIcon(ui_icon("convert_video"))
        self.inspector_generate_video_button.setStyleSheet("QPushButton { background: #2166bd; color: white; border: 1px solid #438ce3; border-radius: 5px; padding: 9px; font-weight: 600; } QPushButton:hover { background: #2978d8; } QPushButton:disabled { background: #34465e; color: #a8b3c2; }")
        self.inspector_generate_video_button.clicked.connect(self._request_video_generation)
        frame_layout.addWidget(self.inspector_generate_video_button)
        self.candidate_group = QGroupBox(
            self.tr_text(
                "video_storyboard_candidate",
                "Generated candidate",
            )
        )
        candidate_layout = QVBoxLayout(self.candidate_group)
        self.candidate_preview = _PixmapPreview()
        candidate_layout.addWidget(self.candidate_preview)
        action_row = QHBoxLayout()
        self.accept_candidate_button = QPushButton(
            self.tr_text(
                "video_storyboard_accept_candidate",
                "Accept and replace",
            )
        )
        self.accept_candidate_button.setObjectName("primaryButton")
        self.accept_candidate_button.setIcon(ui_icon("apply"))
        self.reject_candidate_button = QPushButton(
            self.tr_text(
                "video_storyboard_reject_candidate",
                "Discard",
            )
        )
        self.reject_candidate_button.setIcon(ui_icon("cancel"))
        action_row.addWidget(self.accept_candidate_button)
        action_row.addWidget(self.reject_candidate_button)
        candidate_layout.addLayout(action_row)
        self.candidate_group.hide()
        frame_layout.addWidget(self.candidate_group)
        frame_layout.addStretch(1)

        self.inspector_tabs.addTab(
            self.video_inspector_tab,
            self.tr_text("video_storyboard_video_track", "Video"),
        )
        self.inspector_tabs.addTab(
            self.frame_inspector_tab,
            self.tr_text("video_storyboard_current_frame", "Frame"),
        )
        self.inspector_tabs.setTabVisible(0, False)
        self.inspector_tabs.setCurrentIndex(1)
        layout.addWidget(self.inspector_tabs, 1)

        self.duration_spin.valueChanged.connect(self._duration_edited)
        self.prompt_edit.textChanged.connect(self._prompt_edited)
        self.motion_in_combo.currentIndexChanged.connect(
            self._frame_motion_changed
        )
        self.motion_out_combo.currentIndexChanged.connect(
            self._frame_motion_changed
        )
        self.video_motion_in_combo.currentIndexChanged.connect(
            self._video_motion_changed
        )
        self.video_motion_out_combo.currentIndexChanged.connect(
            self._video_motion_changed
        )
        self._prompt_history_timer = QTimer(self)
        self._prompt_history_timer.setSingleShot(True)
        self._prompt_history_timer.setInterval(750)
        self._prompt_history_timer.timeout.connect(
            self._finish_prompt_history
        )
        self.regenerate_button.clicked.connect(self._request_regeneration)
        self.inspector_edit_frame_button.clicked.connect(self._request_frame_edit)
        self.regenerate_video_button.clicked.connect(self._request_video_generation)
        self.scene_video_play_button.clicked.connect(self._play_scene_video)
        self.scene_video_pause_button.clicked.connect(self._pause_scene_video)
        self.scene_video_stop_button.clicked.connect(self._stop_scene_video)
        self.accept_candidate_button.clicked.connect(self._accept_candidate)
        self.reject_candidate_button.clicked.connect(self._reject_candidate)
        return inspector

    def _frame_motion_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem(
            self.tr_text("video_storyboard_motion_fixed", "None / fixed"),
            "none",
        )
        combo.addItem(
            self.tr_text("video_storyboard_motion_zoom_in", "Zoom in"),
            "zoom_in",
        )
        combo.addItem(
            self.tr_text("video_storyboard_motion_zoom_out", "Zoom out"),
            "zoom_out",
        )
        return combo

    def set_configuration(self, settings: dict[str, Any] | None) -> None:
        self._configuration = dict(settings) if isinstance(settings, dict) else {}
        enabled = (
            bool(settings.get("enabled", False))
            if isinstance(settings, dict)
            else False
        )
        self.status_label.setText(
            self.tr_text(
                "video_storyboard_enabled_status",
                "Enabled · Ollama + ComfyUI",
            )
            if enabled
            else self.tr_text(
                "video_storyboard_disabled_status",
                "Not enabled",
            )
        )
        self._sync_rendered_header()
        self._sync_project_controls()

    def set_ffmpeg_path(self, path: str) -> None:
        self._ffmpeg_path = str(path or "ffmpeg/ffmpeg.exe")

    def _setup_preview_player(self) -> None:
        self.preview_player = QMediaPlayer(self)
        self.preview_audio_output = QAudioOutput(self)
        self.preview_audio_output.setVolume(0.85)
        self.preview_player.setAudioOutput(self.preview_audio_output)
        self.preview_player.positionChanged.connect(
            self._on_preview_position_changed
        )
        self.preview_player.durationChanged.connect(
            lambda _duration: self._update_preview_time()
        )
        self.preview_player.playbackStateChanged.connect(
            self._sync_preview_controls
        )
        self.preview_player.errorOccurred.connect(self._on_preview_error)
        self.scene_video_player = QMediaPlayer(self)
        self.scene_video_audio_output = QAudioOutput(self)
        self.scene_video_audio_output.setMuted(True)
        self.scene_video_player.setAudioOutput(self.scene_video_audio_output)
        self.scene_video_player.setVideoOutput(self.scene_video_widget)
        self.scene_video_widget.videoSink().videoFrameChanged.connect(
            self._on_scene_video_frame_changed
        )
        self.scene_video_player.durationChanged.connect(
            self._on_scene_video_duration_changed
        )
        self.scene_video_player.playbackStateChanged.connect(
            self._sync_scene_video_controls
        )
        self.scene_video_player.mediaStatusChanged.connect(
            self._on_scene_video_media_status_changed
        )
        self.scene_video_player.errorOccurred.connect(
            self._on_scene_video_error
        )
        self._sync_preview_controls()
        self._sync_scene_video_controls()

    def set_audiobook_text(self, text: str) -> None:
        self._audiobook_text = str(text or "")

    def set_audiobook_source(
        self,
        project_id: int | None,
        title: str,
        text: str,
        narration_cues: Iterable[dict[str, Any]],
        total_duration_seconds: float,
        audio_path: str = "",
        voice_start_offset_seconds: float = 0.0,
        project_dir: str = "",
    ) -> None:
        source_text = str(text or "")
        source_changed = (
            project_id != self._source_project_id
            or source_text != self._audiobook_text
        )
        self._source_project_id = project_id
        self._source_project_dir = str(project_dir or "")
        self._source_title = str(title or "")
        self._audiobook_text = source_text
        previous_offset = self._voice_start_offset_seconds
        self._voice_start_offset_seconds = max(
            0.0,
            float(voice_start_offset_seconds or 0.0),
        )
        if self._scenes and not source_changed:
            self._adjust_first_scene_for_offset(
                self._voice_start_offset_seconds - previous_offset
            )
        normalized_audio_path = str(audio_path or "")
        if normalized_audio_path != self._audio_path:
            self.stop_preview()
            self._audio_path = normalized_audio_path
            if self._audio_path and Path(self._audio_path).is_file():
                self.preview_player.setSource(
                    QUrl.fromLocalFile(str(Path(self._audio_path).resolve()))
                )
            else:
                self.preview_player.setSource(QUrl())
        self._narration_cues = [
            StoryboardNarrationCue.from_value(value)
            for value in narration_cues
        ]
        self._source_duration_seconds = max(
            float(total_duration_seconds or 0.0),
            max(
                (
                    cue.start_seconds + cue.duration_seconds
                    for cue in self._narration_cues
                ),
                default=0.0,
            ),
        )
        self.timeline_canvas.set_narration_cues(self._narration_cues)
        self.timeline_canvas.set_source_duration(
            self._source_duration_seconds
        )
        if source_changed:
            if self._frame_batch_dialog is not None and not self._frame_batch_mode:
                dialog = self._frame_batch_dialog
                self._frame_batch_dialog = None
                dialog.close()
            self._rendered_output_path = ""
            self._sync_rendered_header()
            if self._scenes:
                self.set_scenes([])
            else:
                self._reset_scene_history()
            self._plan_metadata = {}
            self._sync_project_controls()
            self._clear_candidate()
        timed = sum(cue.timing_ready for cue in self._narration_cues)
        self.source_summary_label.setText(
            self.tr_text(
                "video_storyboard_source_summary",
                "{title} · {timed}/{count} timed segments · {duration}",
                title=self._source_title
                or self.tr_text("video_storyboard_unsaved_source", "Unsaved text"),
                timed=timed,
                count=len(self._narration_cues),
                duration=StoryboardTimelineCanvas._format_time(
                    self._source_duration_seconds
                ),
            )
        )
        self.analyze_button.setEnabled(bool(source_text.strip()))
        self.generate_frames_button.setEnabled(bool(self._scenes))
        self._sync_preview_controls()
        self._update_preview_time()

    def source_payload(self) -> dict[str, Any]:
        return {
            "project_id": self._source_project_id,
            "title": self._source_title,
            "text": self._audiobook_text,
            "duration_seconds": self._source_duration_seconds,
            "audio_path": self._audio_path,
            "voice_start_offset_seconds": self._voice_start_offset_seconds,
            "narration_cues": [
                cue.as_dict() for cue in self._narration_cues
            ],
            "storyboard_overrides": self.project_overrides(),
        }

    def audio_path(self) -> str:
        return self._audio_path

    def voice_start_offset_seconds(self) -> float:
        return self._voice_start_offset_seconds

    def set_scenes(
        self,
        values: Iterable[StoryboardScene | dict[str, Any]],
    ) -> None:
        self._scenes = [
            StoryboardScene.from_value(value, index)
            for index, value in enumerate(values)
        ]
        self._selected_transition_index = None
        self.timeline_canvas.selected_transition_index = None
        self._show_transition_controls(False)
        self._recalculate_starts()
        self.timeline_canvas.set_scenes(self._scenes)
        self.generate_frames_button.setEnabled(bool(self._scenes))
        self.generate_all_videos_button.setEnabled(bool(self._scenes))
        self.regenerate_all_button.setEnabled(bool(self._scenes))
        self._sync_output_actions()
        self._sync_preview_controls()
        if self._scenes:
            self._select_scene(
                self.timeline_canvas.selected_scene_id
                or self._scenes[0].scene_id
            )
        else:
            self._selected_scene_id = ""
            self._show_selected_scene(None)
        self._reset_scene_history()

    def set_analysis_result(
        self,
        values: Iterable[StoryboardScene | dict[str, Any]] | dict[str, Any],
    ) -> None:
        self.analyze_button.setEnabled(bool(self._audiobook_text.strip()))
        if isinstance(values, dict):
            previous_video_overrides = self._plan_metadata.get(
                "video_overrides"
            )
            raw_scenes = values.get("scenes", [])
            self._plan_metadata = {
                key: value
                for key, value in values.items()
                if key != "scenes"
            }
            self._sync_prompt_markup_plan()
            self._rendered_output_path = str(
                self._plan_metadata.get("rendered_output_path") or ""
            )
            if isinstance(previous_video_overrides, dict):
                self._plan_metadata.setdefault(
                    "video_overrides",
                    dict(previous_video_overrides),
                )
            self.set_scenes(
                raw_scenes if isinstance(raw_scenes, list) else []
            )
            self._sync_project_controls()
        else:
            self._plan_metadata = {}
            self._sync_prompt_markup_plan()
            self._rendered_output_path = ""
            self.set_scenes(values)
        self.finish_operation(
            self.tr_text(
                "video_storyboard_analysis_ready",
                "Scene plan ready. No frames have been generated yet.",
            )
        )
        self.append_activity(
            self.tr_text(
                "video_storyboard_analysis_ready_count",
                "Analysis complete: {count} scenes prepared; waiting for frame generation.",
                count=len(self._scenes),
            )
        )

    def set_analysis_partial_result(self, values: dict[str, Any]) -> None:
        previous_video_overrides = self._plan_metadata.get(
            "video_overrides"
        )
        raw_scenes = values.get("scenes", [])
        self._plan_metadata = {
            key: value for key, value in values.items() if key != "scenes"
        }
        self._sync_prompt_markup_plan()
        self._rendered_output_path = ""
        if isinstance(previous_video_overrides, dict):
            self._plan_metadata.setdefault(
                "video_overrides",
                dict(previous_video_overrides),
            )
        self.set_scenes(raw_scenes if isinstance(raw_scenes, list) else [])
        self._sync_project_controls()
        self.generate_frames_button.setEnabled(False)
        self.render_button.setEnabled(False)
        completed = max(0, int(values.get("completed_blocks") or 0))
        total = max(completed, int(values.get("total_blocks") or completed or 1))
        if values.get("analysis_phase") == "discovery":
            message = self.tr_text(
                "video_storyboard_discovery_partial",
                "Discovery block {current}/{total} saved.",
                current=completed,
                total=total,
            )
        elif values.get("analysis_phase") == "continuity":
            continuity = values.get("continuity", {})
            characters = continuity.get("characters", []) if isinstance(continuity, dict) else []
            locations = continuity.get("locations", []) if isinstance(continuity, dict) else []
            message = self.tr_text(
                "video_storyboard_continuity_partial",
                "Continuity block {current}/{total} saved: {characters} characters and {locations} locations tracked.",
                current=completed,
                total=total,
                characters=len(characters) if isinstance(characters, list) else 0,
                locations=len(locations) if isinstance(locations, list) else 0,
            )
        else:
            message = self.tr_text(
                "video_storyboard_analysis_partial",
                "Analysis block {current}/{total} ready: {count} scenes saved.",
                current=completed,
                total=total,
                count=len(self._scenes),
            )
        self.set_operation(message, round(completed / max(1, total) * 100))
        self.append_activity(message)

    def restore_project_state(self, state: dict[str, Any]) -> None:
        plan = state.get("plan", {})
        scenes = state.get("scenes", [])
        self._plan_metadata = dict(plan) if isinstance(plan, dict) else {}
        self._sync_prompt_markup_plan()
        self._rendered_output_path = str(
            self._plan_metadata.get("rendered_output_path") or ""
        )
        restored_scenes = [
            dict(scene)
            for scene in scenes
            if isinstance(scene, dict)
        ] if isinstance(scenes, list) else []
        stored_offset = max(
            0.0,
            float(
                self._plan_metadata.get("voice_start_offset_seconds")
                or 0.0
            ),
        )
        offset_delta = self._voice_start_offset_seconds - stored_offset
        if restored_scenes and abs(offset_delta) > 0.0001:
            duration_key = (
                "duration_seconds"
                if "duration_seconds" in restored_scenes[0]
                else "duration"
            )
            restored_scenes[0][duration_key] = max(
                1.0,
                float(restored_scenes[0].get(duration_key) or 0.0)
                + offset_delta,
            )
        self._plan_metadata["voice_start_offset_seconds"] = (
            self._voice_start_offset_seconds
        )
        self.set_scenes(restored_scenes)
        self._sync_project_controls()
        self.analyze_button.setEnabled(bool(self._audiobook_text.strip()))
        status = str(state.get("analysis_status") or "ready")
        message = self.tr_text(
            "video_storyboard_state_restored",
            "Restored {count} saved storyboard scenes ({status}).",
            count=len(self._scenes),
            status=status,
        )
        self.finish_operation(message)
        self.append_activity(message)
        if abs(offset_delta) > 0.0001:
            self.projectChanged.emit(self.project_state())

    def set_analysis_failed(self, error: str) -> None:
        self.analyze_button.setEnabled(bool(self._audiobook_text.strip()))
        message = self.tr_text(
            "video_storyboard_analysis_failed",
            "Audiobook analysis failed: {error}",
            error=error,
        )
        self.finish_operation(message)
        self.append_activity(message)

    def scenes(self) -> list[dict[str, Any]]:
        return [scene.as_dict() for scene in self._scenes]

    def project_state(self) -> dict[str, Any]:
        return {
            "source": self.source_payload(),
            "plan": dict(self._plan_metadata),
            "scenes": self.scenes(),
        }

    def frame_generation_payload(self) -> dict[str, Any]:
        return {
            "source": self.source_payload(),
            "plan": dict(self._plan_metadata),
            "scenes": self.scenes(),
        }

    def project_overrides(self) -> dict[str, Any]:
        current_style_item = self.style_gallery.currentItem()
        style_mode = normalize_storyboard_style_id(
            current_style_item.data(Qt.ItemDataRole.UserRole)
            if current_style_item is not None
            else self._plan_metadata.get("style_mode")
        )
        style = {
            "medium": self.style_medium_edit.text().strip(),
            "palette": self.style_palette_edit.text().strip(),
            "lighting": self.style_lighting_edit.text().strip(),
            "characters": [
                line.strip()
                for line in self.style_characters_edit.toPlainText().splitlines()
                if line.strip()
            ],
            "negative": self.style_negative_edit.text().strip(),
        }
        era = self.story_era_edit.text().strip()
        era_source = (
            "detected"
            if era and era == self._detected_era_value
            else "user"
        )
        return {
            "seed": self._effective_seed(),
            "style_mode": style_mode,
            "style": style,
            "narrative": {
                "era": era,
                "era_source": era_source,
            },
            "video": {
                "zoom_percent": self.video_zoom_spin.value(),
                "transition_seconds": self.video_transition_spin.value(),
                "motion": str(
                    self.default_motion_combo.currentData() or "alternate"
                ),
            },
        }

    def _effective_seed(self) -> int:
        raw_seed = self.seed_edit.text().strip()
        if raw_seed:
            try:
                value = int(raw_seed)
            except ValueError:
                value = -1
            if value >= 0:
                return min(value, 2**63 - 1)
        return int.from_bytes(
            hashlib.sha256(self._audiobook_text.encode("utf-8")).digest()[:6],
            "big",
        )

    def render_overrides(self) -> dict[str, Any]:
        return dict(self.project_overrides().get("video", {}))

    def regeneration_payload(
        self,
        scene_id: str,
        prompt: str,
        overrides: dict[str, Any] | None = None,
        shot: str | None = None,
    ) -> dict[str, Any] | None:
        scene = next(
            (item.as_dict() for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return None
        scene["prompt"] = prompt
        if shot is not None:
            scene["shot"] = str(shot).strip()
        if isinstance(overrides, dict):
            scene["generation_overrides"] = dict(overrides)
            override_style = overrides.get("style", {})
            if isinstance(override_style, dict) and isinstance(
                override_style.get("characters"),
                list,
            ):
                scene["characters"] = [
                    str(value).split(":", 1)[0].strip()
                    for value in override_style["characters"]
                    if str(value).split(":", 1)[0].strip()
                ]
            location_state_ids = overrides.get("location_state_ids", [])
            if isinstance(location_state_ids, list):
                scene["locations"] = [
                    str(value).strip()
                    for value in location_state_ids
                    if str(value).strip()
                ]
            narrative = overrides.get("narrative", {})
            if isinstance(narrative, dict) and narrative.get("era"):
                scene["era"] = str(narrative["era"])
                scene["era_state_id"] = ""
        return {
            "source": self.source_payload(),
            "plan": dict(self._plan_metadata),
            "scenes": [scene],
        }

    def selected_scene(self) -> StoryboardScene | None:
        return next(
            (
                item
                for item in self._scenes
                if item.scene_id == self._selected_scene_id
            ),
            None,
        )

    def set_regeneration_preview(
        self,
        scene_id: str,
        image_path: str,
        compiled_prompt: str = "",
    ) -> None:
        if scene_id not in {item.scene_id for item in self._scenes}:
            return
        self._select_scene(scene_id)
        if (
            self._regeneration_dialog is not None
            and self._regeneration_dialog.scene_id == scene_id
        ):
            if compiled_prompt:
                self._regeneration_dialog.set_compiled_prompt(compiled_prompt)
            self._regeneration_dialog.set_candidate(image_path)
        self.regenerate_button.setEnabled(True)
        self.finish_operation(
            self.tr_text(
                "video_storyboard_candidate_ready",
                "Candidate ready for review.",
            )
        )
        self.append_activity(
            self.tr_text(
                "video_storyboard_candidate_ready_scene",
                "Scene {scene}: candidate generated.",
                scene=scene_id,
            )
        )

    def set_regeneration_failed(self, scene_id: str, error: str) -> None:
        self.regenerate_button.setEnabled(True)
        message = self.tr_text(
            "video_storyboard_regeneration_failed",
            "Scene {scene} could not be regenerated: {error}",
            scene=scene_id,
            error=error,
        )
        self.finish_operation(message)
        self.append_activity(message)
        if (
            self._regeneration_dialog is not None
            and self._regeneration_dialog.scene_id == scene_id
        ):
            self._regeneration_dialog.set_failed(message)

    def set_regeneration_progress(
        self,
        scene_id: str,
        message: str,
        percentage: int,
    ) -> None:
        if (
            self._regeneration_dialog is not None
            and self._regeneration_dialog.scene_id == scene_id
        ):
            self._regeneration_dialog.set_progress(message, percentage)

    def set_frame_generation_progress(
        self,
        current: int,
        total: int,
        message: str,
        percentage: int,
    ) -> None:
        if self._frame_batch_dialog is not None:
            self._frame_batch_dialog.set_progress(
                current,
                total,
                message,
                percentage,
            )

    def set_frame_generated(self, scene_id: str, image_path: str) -> None:
        scene = next(
            (item for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return
        if (
            self._frame_generation_history_active
            and not self._frame_generation_history_recorded
            and self._frame_generation_start_snapshot is not None
        ):
            self._push_undo_snapshot(self._frame_generation_start_snapshot)
            self._frame_generation_history_recorded = True
        scene.image_path = image_path
        scene.status = "generated"
        self.timeline_canvas._pixmap_cache.clear()
        self.timeline_canvas.update()
        if self._selected_scene_id == scene_id:
            self.current_preview.set_image(
                image_path,
                self.tr_text(
                    "video_storyboard_frame_pending",
                    "Frame not generated",
                ),
            )
            self.convert_frame_video_button.setEnabled(
                bool(image_path and Path(image_path).is_file())
            )
        self.scenesChanged.emit(self.scenes())
        self._sync_output_actions()
        self._remember_scene_snapshot()
        self.append_activity(
            self.tr_text(
                "video_storyboard_frame_generated",
                "Scene {scene}: frame generated.",
                scene=scene_id,
            )
        )
        if self._frame_batch_dialog is not None and self._frame_batch_mode:
            self._frame_batch_completed += 1
            self._frame_batch_dialog.frame_ready(
                scene_id,
                self._frame_batch_completed,
                len(self._scenes),
            )

    def set_frame_generation_finished(self) -> None:
        self._finish_frame_generation_history()
        self.generate_frames_button.setEnabled(bool(self._scenes))
        self.regenerate_all_button.setEnabled(bool(self._scenes))
        self._sync_output_actions()
        message = self.tr_text(
            "video_storyboard_frames_complete",
            "All storyboard frames have been generated.",
        )
        self.finish_operation(message)
        self.append_activity(message)
        if self._frame_batch_dialog is not None:
            dialog = self._frame_batch_dialog
            self._frame_batch_dialog = None
            dialog.set_finished(True, message)
        self._frame_batch_mode = ""

    def set_frame_generation_failed(self, error: str) -> None:
        self._finish_frame_generation_history()
        self.generate_frames_button.setEnabled(bool(self._scenes))
        self.regenerate_all_button.setEnabled(bool(self._scenes))
        self._sync_output_actions()
        message = self.tr_text(
            "video_storyboard_frames_failed",
            "Frame generation failed: {error}",
            error=error,
        )
        self.finish_operation(message)
        self.append_activity(message)
        if self._frame_batch_dialog is not None:
            dialog = self._frame_batch_dialog
            self._frame_batch_dialog = None
            dialog.set_finished(False, message)
        self._frame_batch_mode = ""

    def set_render_finished(self, output_path: str) -> None:
        self._rendered_output_path = str(output_path or "")
        self._plan_metadata["rendered_output_path"] = self._rendered_output_path
        self._sync_output_actions()
        self._sync_rendered_header()
        self.projectChanged.emit(self.project_state())
        message = self.tr_text(
            "video_storyboard_render_complete",
            "Final video created: {path}",
            path=output_path,
        )
        self.finish_operation(message)
        self.append_activity(message)

    def set_existing_render_output(self, output_path: str) -> None:
        candidate = str(output_path or "")
        if candidate and Path(candidate).is_file():
            self._rendered_output_path = candidate
            self._plan_metadata["rendered_output_path"] = candidate
        else:
            self._rendered_output_path = ""
            self._plan_metadata.pop("rendered_output_path", None)
        self._sync_output_actions()
        self._sync_rendered_header()

    def _sync_rendered_header(self) -> None:
        if not hasattr(self, "rendered_video_button"):
            return
        ready = bool(
            self._rendered_output_path
            and Path(self._rendered_output_path).is_file()
        )
        self.status_label.setVisible(not ready)
        self.rendered_video_button.setVisible(ready)
        if ready:
            output = Path(self._rendered_output_path)
            self.rendered_video_button.setText(output.name)
            self.rendered_video_button.setToolTip(
                self.tr_text(
                    "video_storyboard_open_rendered_video",
                    "Open final video: {path}",
                    path=str(output),
                )
            )

    def _open_rendered_video(self) -> None:
        path = Path(self._rendered_output_path)
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def set_render_failed(self, error: str) -> None:
        self._sync_output_actions()
        message = self.tr_text(
            "video_storyboard_render_failed",
            "Video rendering failed: {error}",
            error=error,
        )
        self.finish_operation(message)
        self.append_activity(message)

    def set_operation(self, text: str, progress: int | None = None) -> None:
        self.operation_label.setText(text)
        self.operation_progress.show()
        if progress is None or progress < 0:
            self.operation_progress.setRange(0, 0)
        else:
            self.operation_progress.setRange(0, 100)
            self.operation_progress.setValue(max(0, min(100, progress)))

    def finish_operation(self, text: str | None = None) -> None:
        self.operation_progress.hide()
        self.operation_progress.setRange(0, 100)
        self.operation_label.setText(
            text or self.tr_text("video_storyboard_ready", "Ready")
        )

    def append_activity(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.appendPlainText(f"[{timestamp}] {message}")

    def _select_scene(self, scene_id: str) -> None:
        self._selected_transition_index = None
        self._show_transition_controls(False)
        self._selected_scene_id = scene_id
        self.timeline_canvas.select_scene(scene_id)
        self._show_selected_scene(self.selected_scene())

    def _show_selected_scene(self, scene: StoryboardScene | None) -> None:
        enabled = scene is not None
        self._sync_video_action_visibility(scene)
        scene_changed = bool(
            scene is not None and scene.scene_id != self._inspector_scene_id
        )
        self.duration_spin.setEnabled(enabled)
        self.narration_edit.setEnabled(enabled)
        self.prompt_edit.setEnabled(enabled)
        self.motion_in_combo.setEnabled(enabled)
        self.motion_out_combo.setEnabled(enabled)
        self.regenerate_button.setEnabled(enabled)
        frame_edit_enabled = bool(
            scene is not None
            and scene.image_path
            and Path(scene.image_path).is_file()
        )
        self.inspector_edit_frame_button.setEnabled(frame_edit_enabled)
        self.inspector_generate_video_button.setVisible(bool(scene is not None and not (scene.video_path and Path(scene.video_path).is_file())))
        self.inspector_generate_video_button.setEnabled(frame_edit_enabled)
        self.edit_frame_button.setEnabled(frame_edit_enabled)
        self.regenerate_video_button.setEnabled(enabled)
        self.timeline_regenerate_button.setEnabled(enabled)
        self.replace_frame_button.setEnabled(enabled)
        self.copy_frame_button.setEnabled(enabled)
        self.paste_frame_button.setEnabled(enabled)
        self.convert_frame_video_button.setEnabled(
            bool(scene is not None and scene.image_path and Path(scene.image_path).is_file())
        )
        self.delete_frame_button.setEnabled(enabled)
        self.split_frame_button.setEnabled(
            bool(scene is not None and scene.duration_seconds >= 2.0)
        )
        if scene is None:
            self._inspector_scene_id = ""
            self.scene_meta_label.setText(
                self.tr_text(
                    "video_storyboard_no_scene_selected",
                    "No scene selected",
                )
            )
            self.inspector_tabs.setTabVisible(0, False)
            self.inspector_tabs.setCurrentIndex(1)
            self._clear_scene_video_source()
            self.current_preview.set_image(
                "",
                self.tr_text("video_storyboard_no_frame", "No frame"),
            )
            self.narration_edit.clear()
            self.prompt_edit.clear()
            self.candidate_group.hide()
            return
        self._inspector_scene_id = scene.scene_id
        number = self._scenes.index(scene) + 1
        self.scene_meta_label.setText(
            self.tr_text(
                "video_storyboard_scene_meta",
                "Scene {number} · starts at {start} · {duration:.1f} s",
                number=number,
                start=StoryboardTimelineCanvas._format_time(
                    scene.start_seconds
                ),
                duration=scene.duration_seconds,
            )
        )
        self.current_preview.set_image(
            scene.image_path,
            self.tr_text(
                "video_storyboard_frame_pending",
                "Frame not generated",
            ),
        )
        has_video = bool(scene.video_path and Path(scene.video_path).is_file())
        self.inspector_tabs.setTabVisible(0, has_video)
        if has_video:
            self._load_scene_video(
                scene,
                autoplay=(
                    self.preview_player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState
                ),
            )
            if scene_changed:
                self.inspector_tabs.setCurrentIndex(0)
        else:
            self._clear_scene_video_source()
            self.inspector_tabs.setCurrentIndex(1)
        self._restoring_duration = True
        self.duration_spin.setValue(scene.duration_seconds)
        self._restoring_duration = False
        self._updating_frame_motion = True
        self.motion_in_combo.blockSignals(True)
        self.motion_out_combo.blockSignals(True)
        try:
            motion_in = scene.motion_in or scene.motion or "none"
            motion_out = scene.motion_out or "none"
            self.motion_in_combo.setCurrentIndex(
                max(0, self.motion_in_combo.findData(motion_in))
            )
            self.motion_out_combo.setCurrentIndex(
                max(0, self.motion_out_combo.findData(motion_out))
            )
        finally:
            self.motion_in_combo.blockSignals(False)
            self.motion_out_combo.blockSignals(False)
            self._updating_frame_motion = False
        self._updating_video_motion = True
        self.video_motion_in_combo.blockSignals(True)
        self.video_motion_out_combo.blockSignals(True)
        try:
            self.video_motion_in_combo.setCurrentIndex(
                max(0, self.video_motion_in_combo.findData(scene.video_motion_in))
            )
            self.video_motion_out_combo.setCurrentIndex(
                max(0, self.video_motion_out_combo.findData(scene.video_motion_out))
            )
        finally:
            self.video_motion_in_combo.blockSignals(False)
            self.video_motion_out_combo.blockSignals(False)
            self._updating_video_motion = False
        self.scene_video_prompt_edit.setPlainText(
            decorate_storyboard_prompt(scene.video_prompt, self._plan_metadata)
        )
        self.narration_edit.setPlainText(scene.narration)
        self.prompt_edit.blockSignals(True)
        self.prompt_edit.setPlainText(self._editable_prompt_text(scene))
        self.prompt_edit.blockSignals(False)
        if self._candidate_scene_id != scene.scene_id:
            self.candidate_group.hide()

    def _duration_edited(self, value: float) -> None:
        if self._restoring_duration or not self._selected_scene_id:
            return
        self.timeline_canvas.set_scene_duration(
            self._selected_scene_id,
            value,
        )
        self._show_selected_scene(self.selected_scene())

    def _prompt_edited(self) -> None:
        scene = self.selected_scene()
        if scene is not None:
            if not self._prompt_history_open and not self._history_restoring:
                self._push_undo_snapshot()
                self._prompt_history_open = True
            self._prompt_history_timer.start()
            self._apply_editable_prompt(
                scene,
                self.prompt_edit.toPlainText(),
            )
            self.scenesChanged.emit(self.scenes())
            self._remember_scene_snapshot()

    def _frame_motion_changed(self, _index: int) -> None:
        if self._updating_frame_motion or self._history_restoring:
            return
        scene = self.selected_scene()
        if scene is None:
            return
        self._record_scene_edit()
        scene.motion_in = str(self.motion_in_combo.currentData() or "none")
        scene.motion_out = str(self.motion_out_combo.currentData() or "none")
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()

    def _video_motion_changed(self, _index: int) -> None:
        if self._updating_video_motion or self._history_restoring:
            return
        scene = self.selected_scene()
        if scene is None or not scene.video_path:
            return
        self._record_scene_edit()
        scene.video_motion_in = str(
            self.video_motion_in_combo.currentData() or "none"
        )
        scene.video_motion_out = str(
            self.video_motion_out_combo.currentData() or "none"
        )
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()

    def _editable_prompt_text(self, scene: StoryboardScene) -> str:
        prompt = str(scene.prompt or "").strip()
        shot = str(scene.shot or "").strip()
        if not shot:
            return decorate_storyboard_prompt(prompt, self._plan_metadata)
        return decorate_storyboard_prompt(
            f"{prompt}\n\nSHOT AND COMPOSITION: {shot}".strip(),
            self._plan_metadata,
        )

    def _apply_editable_prompt(
        self,
        scene: StoryboardScene,
        editable_text: str,
    ) -> None:
        marker = "\n\nSHOT AND COMPOSITION:"
        text = strip_storyboard_prompt_markers(
            str(editable_text or "").strip(),
            self._plan_metadata,
        )
        if marker not in text:
            scene.prompt = text
            scene.shot = ""
            self._bind_prompt_entities(scene)
            return
        prompt, _marker, shot = text.rpartition(marker)
        scene.prompt = prompt.strip()
        scene.shot = shot.strip()
        self._bind_prompt_entities(scene)

    def _bind_prompt_entities(self, scene: StoryboardScene) -> None:
        scene_value = scene.as_dict()
        character_ids = [
            line.split(":", 1)[0].strip()
            for line in canonical_character_lines_for_prompt(
                self._plan_metadata,
                scene_value,
                scene.prompt,
            )
            if line.split(":", 1)[0].strip()
        ]
        location_ids = canonical_location_state_ids_for_prompt(
            self._plan_metadata,
            scene_value,
            scene.prompt,
        )
        for value in character_ids:
            if value not in scene.characters:
                scene.characters.append(value)
        for value in location_ids:
            if value not in scene.locations:
                scene.locations.append(value)

    def _on_timing_changed(self, _values: object) -> None:
        if not self._history_restoring and not self._timing_history_active:
            self._push_undo_snapshot(self._last_scene_snapshot)
        self._recalculate_starts()
        scene = self.selected_scene()
        if scene is not None:
            self._restoring_duration = True
            self.duration_spin.setValue(scene.duration_seconds)
            self._restoring_duration = False
            self._show_selected_scene(scene)
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()

    def _scene_snapshot(self) -> dict[str, Any]:
        return {
            "scenes": deepcopy(self.scenes()),
            "selected_scene_id": self._selected_scene_id,
            "selected_transition_index": self._selected_transition_index,
        }

    def _remember_scene_snapshot(self) -> None:
        self._last_scene_snapshot = self._scene_snapshot()

    def _reset_scene_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._timing_history_active = False
        self._finish_prompt_history()
        self._finish_frame_generation_history()
        self._remember_scene_snapshot()
        self._sync_history_actions()

    def _push_undo_snapshot(
        self,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        if self._history_restoring:
            return
        value = deepcopy(snapshot if snapshot is not None else self._scene_snapshot())
        if not self._undo_stack or self._undo_stack[-1] != value:
            self._undo_stack.append(value)
            del self._undo_stack[:-100]
        self._redo_stack.clear()
        self._sync_history_actions()

    def _record_scene_edit(self) -> None:
        self._finish_prompt_history()
        self._push_undo_snapshot()

    def _begin_timing_history(self) -> None:
        if self._history_restoring or self._timing_history_active:
            return
        self._finish_prompt_history()
        self._push_undo_snapshot()
        self._timing_history_active = True

    def _finish_timing_history(self) -> None:
        self._timing_history_active = False
        self._remember_scene_snapshot()

    def _finish_prompt_history(self) -> None:
        self._prompt_history_open = False
        timer = getattr(self, "_prompt_history_timer", None)
        if timer is not None:
            timer.stop()

    def _finish_frame_generation_history(self) -> None:
        self._frame_generation_history_active = False
        self._frame_generation_history_recorded = False
        self._frame_generation_start_snapshot = None

    def _undo_scene_edit(self) -> None:
        if not self._undo_stack:
            return
        self._finish_prompt_history()
        current = self._scene_snapshot()
        target = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._apply_scene_snapshot(target)

    def _redo_scene_edit(self) -> None:
        if not self._redo_stack:
            return
        self._finish_prompt_history()
        current = self._scene_snapshot()
        target = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._apply_scene_snapshot(target)

    def _apply_scene_snapshot(self, snapshot: dict[str, Any]) -> None:
        raw_scenes = snapshot.get("scenes", [])
        self._history_restoring = True
        try:
            self._scenes = [
                StoryboardScene.from_value(value, index)
                for index, value in enumerate(raw_scenes)
                if isinstance(value, dict)
            ] if isinstance(raw_scenes, list) else []
            self._recalculate_starts()
            self.timeline_canvas.set_scenes(self._scenes)
            requested = str(snapshot.get("selected_scene_id") or "")
            raw_transition = snapshot.get("selected_transition_index")
            transition_index = (
                int(raw_transition)
                if isinstance(raw_transition, int)
                and 0 <= raw_transition < len(self._scenes) - 1
                else None
            )
            available = {scene.scene_id for scene in self._scenes}
            selected = requested if requested in available else (
                self._scenes[0].scene_id if self._scenes else ""
            )
            self._selected_scene_id = selected
            if transition_index is not None:
                self._select_transition(transition_index)
            elif selected:
                self._select_scene(selected)
            else:
                self._show_selected_scene(None)
            self.generate_frames_button.setEnabled(bool(self._scenes))
            self.generate_all_videos_button.setEnabled(bool(self._scenes))
            self.regenerate_all_button.setEnabled(bool(self._scenes))
            self._sync_output_actions()
        finally:
            self._history_restoring = False
        self._remember_scene_snapshot()
        self._sync_history_actions()
        self.scenesChanged.emit(self.scenes())

    def _sync_history_actions(self) -> None:
        if not hasattr(self, "undo_button"):
            return
        self.undo_button.setEnabled(bool(self._undo_stack))
        self.redo_button.setEnabled(bool(self._redo_stack))

    def _select_transition(self, boundary_index: int) -> None:
        if not 0 <= boundary_index < len(self._scenes) - 1:
            self._clear_timeline_selection()
            return
        self._selected_transition_index = boundary_index
        self._selected_scene_id = ""
        self.timeline_canvas.select_transition(boundary_index)
        self._show_selected_scene(None)
        transition = self._scenes[boundary_index + 1].transition or "none"
        self._updating_transition_controls = True
        self.transition_combo.blockSignals(True)
        try:
            self.transition_combo.setCurrentIndex(
                max(0, self.transition_combo.findData(transition))
            )
        finally:
            self.transition_combo.blockSignals(False)
            self._updating_transition_controls = False
        self._show_transition_controls(True)

    def _clear_timeline_selection(self) -> None:
        self._selected_transition_index = None
        self._selected_scene_id = ""
        self.timeline_canvas.clear_selection()
        self._show_transition_controls(False)
        self._show_selected_scene(None)

    def _show_transition_controls(self, visible: bool) -> None:
        self.transition_label.setVisible(visible)
        self.transition_combo.setVisible(visible)

    def _transition_changed(self, _index: int) -> None:
        if self._updating_transition_controls or self._history_restoring:
            return
        boundary = self._selected_transition_index
        if boundary is None or not 0 <= boundary < len(self._scenes) - 1:
            return
        self._record_scene_edit()
        self._scenes[boundary + 1].transition = str(
            self.transition_combo.currentData() or "none"
        )
        self.timeline_canvas.update()
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()

    @staticmethod
    def _scene_has_video(scene: StoryboardScene | None) -> bool:
        return bool(scene and scene.video_path and Path(scene.video_path).is_file())

    def _sync_video_action_visibility(self, scene: StoryboardScene | None) -> None:
        has_video = self._scene_has_video(scene)
        for button in (
            self.copy_last_frame_button, self.edit_video_button,
            self.delete_video_button, self.timeline_regenerate_video_button,
        ):
            button.setVisible(has_video)
            button.setEnabled(has_video)
        self.copy_last_frame_button.setEnabled(has_video and self._video_copy_task is None)
        self.inspector_edit_video_button.setVisible(has_video)
        self.inspector_edit_video_button.setEnabled(has_video)
        for button in (
            self.copy_frame_button, self.paste_frame_button, self.replace_frame_button,
            self.timeline_regenerate_button, self.edit_frame_button, self.split_frame_button,
            self.delete_frame_button, self.convert_frame_video_button,
        ):
            button.setVisible(not has_video)

    def _copy_video_last_frame(self) -> None:
        scene = self.selected_scene()
        if not self._scene_has_video(scene) or self._video_copy_task is not None:
            return
        task = VideoLastFrameCopy(self)
        self._video_copy_task = task
        self._sync_video_action_visibility(scene)
        self.append_activity(self.tr_text("storyboard_copy_last_frame_loading", "Copying the last video frame…"))

        def finished(ok: bool, error: str) -> None:
            self._video_copy_task = None
            self._sync_video_action_visibility(self.selected_scene())
            if ok:
                self.append_activity(self.tr_text("storyboard_copy_last_frame_done", "Last video frame copied to clipboard."))
            else:
                message = self.tr_text("storyboard_copy_last_frame_error", "Could not copy the last video frame: {error}", error=error)
                self.append_activity(message)
                QMessageBox.warning(self, self.tr_text("storyboard_copy_last_frame", "Copy Last Frame"), message)
            task.deleteLater()

        task.finished.connect(finished)
        task.start(scene.video_path, self._ffmpeg_path)

    def _delete_selected_video(self) -> None:
        scene = self.selected_scene()
        if scene is None or not scene.video_path:
            return
        self._record_scene_edit()
        self._clear_scene_video_source()
        scene.video_path = ""
        scene.video_duration_seconds = 0.0
        scene.video_motion_in = scene.video_motion_out = "none"
        scene.video_frame_role = "start"
        self.timeline_canvas.update()
        self._show_selected_scene(scene)
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()
        self.append_activity(self.tr_text("storyboard_video_removed", "Video removed from scene {scene}.", scene=scene.scene_id))

    def _request_video_edit(self) -> None:
        scene = self.selected_scene()
        if not self._scene_has_video(scene):
            return
        if self._video_edit_dialog is not None:
            self._video_edit_dialog.show()
            self._video_edit_dialog.raise_()
            self._video_edit_dialog.activateWindow()
            return
        self.preview_player.pause()
        self.scene_video_player.pause()
        dialog = StoryboardVideoEditDialog(
            self.tr_text, scene.scene_id, scene.video_path, self._ffmpeg_path, self,
        )
        self._video_edit_dialog = dialog
        dialog.videoAccepted.connect(self._apply_edited_video)
        dialog.destroyed.connect(lambda: setattr(self, "_video_edit_dialog", None))
        dialog.open()

    def _apply_edited_video(self, scene_id: str, path: str, duration: float) -> None:
        scene = next((item for item in self._scenes if item.scene_id == scene_id), None)
        if scene is None or not Path(path).is_file():
            return
        self.set_scene_video(scene_id, path, scene.video_prompt, scene.video_frame_role, duration)

    def _build_video_context_menu(self, scene: StoryboardScene) -> QMenu:
        menu = QMenu(self)
        copy = menu.addAction(ui_icon("copy"), self.tr_text("storyboard_copy_last_frame", "Copy Last Frame"))
        copy.setEnabled(self._video_copy_task is None)
        copy.triggered.connect(self._copy_video_last_frame)
        menu.addSeparator()
        edit = menu.addAction(ui_icon("edit"), self.tr_text("storyboard_edit_video", "Edit Video"))
        edit.triggered.connect(self._request_video_edit)
        delete = menu.addAction(ui_icon("delete"), self.tr_text("storyboard_delete_video", "Delete Video"))
        delete.triggered.connect(self._delete_selected_video)
        regenerate = menu.addAction(ui_icon("regenerate"), self.tr_text("video_storyboard_regenerate_video", "Regenerate Video"))
        regenerate.setEnabled(bool(scene.image_path and Path(scene.image_path).is_file()))
        regenerate.triggered.connect(self._request_video_generation)
        menu.addSeparator()
        undo = menu.addAction(ui_icon("undo"), self.tr_text("video_storyboard_undo", "Undo"))
        undo.setEnabled(bool(self._undo_stack))
        undo.triggered.connect(self._undo_scene_edit)
        redo = menu.addAction(ui_icon("redo"), self.tr_text("video_storyboard_redo", "Redo"))
        redo.setEnabled(bool(self._redo_stack))
        redo.triggered.connect(self._redo_scene_edit)
        return menu

    def _build_frame_context_menu(self, scene_id: str) -> QMenu | None:
        scene = next(
            (item for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return None
        self._select_scene(scene_id)
        if self._scene_has_video(scene):
            return self._build_video_context_menu(scene)
        menu = QMenu(self)
        copy_action = menu.addAction(
            ui_icon("copy"),
            self.tr_text("video_storyboard_copy_frame", "Copy")
        )
        copy_action.setEnabled(
            bool(scene.image_path and Path(scene.image_path).is_file())
        )
        paste_action = menu.addAction(
            ui_icon("paste"),
            self.tr_text("video_storyboard_paste_frame", "Paste")
        )
        paste_action.setEnabled(not QApplication.clipboard().image().isNull())
        copy_action.triggered.connect(self._copy_selected_frame)
        paste_action.triggered.connect(self._paste_selected_frame)
        menu.addSeparator()
        replace_action = menu.addAction(
            ui_icon("replace_image"),
            self.tr_text("video_storyboard_replace_frame", "Replace frame with an image"),
        )
        regenerate_action = menu.addAction(
            ui_icon("bolt"),
            self.tr_text("video_storyboard_regenerate_frame", "Regenerate frame"),
        )
        edit_action = menu.addAction(
            ui_icon("edit"),
            self.tr_text("video_storyboard_edit_frame", "Edit frame"),
        )
        edit_action.setEnabled(
            bool(scene.image_path and Path(scene.image_path).is_file())
        )
        split_action = menu.addAction(
            ui_icon("split"),
            self.tr_text("video_storyboard_split_frame", "Split frame into two"),
        )
        split_action.setEnabled(scene.duration_seconds >= 2.0)
        delete_action = menu.addAction(
            ui_icon("delete"),
            self.tr_text("video_storyboard_clear_frame", "Delete frame"),
        )
        convert_action = menu.addAction(
            ui_icon("convert_video"),
            self.tr_text(
                "video_storyboard_convert_to_video",
                "Generate scene video",
            ),
        )
        convert_action.setEnabled(
            bool(scene.image_path and Path(scene.image_path).is_file())
        )
        menu.addSeparator()
        undo_action = menu.addAction(
            ui_icon("undo"), self.tr_text("video_storyboard_undo", "Undo")
        )
        redo_action = menu.addAction(
            ui_icon("redo"), self.tr_text("video_storyboard_redo", "Redo")
        )
        undo_action.setEnabled(bool(self._undo_stack))
        redo_action.setEnabled(bool(self._redo_stack))
        replace_action.triggered.connect(self._request_frame_replacement)
        regenerate_action.triggered.connect(self._request_regeneration)
        edit_action.triggered.connect(self._request_frame_edit)
        split_action.triggered.connect(self._split_selected_frame)
        delete_action.triggered.connect(self._delete_selected_frame)
        convert_action.triggered.connect(self._request_video_generation)
        undo_action.triggered.connect(self._undo_scene_edit)
        redo_action.triggered.connect(self._redo_scene_edit)
        return menu

    def _open_frame_context_menu(self, scene_id: str, global_pos: QPoint) -> None:
        menu = self._build_frame_context_menu(scene_id)
        if menu is not None:
            menu.exec(global_pos)
            menu.deleteLater()

    def _request_regeneration(self) -> None:
        scene = self.selected_scene()
        if scene is None:
            return
        if self._regeneration_dialog is not None:
            self._regeneration_dialog.show_and_raise()
            return
        dialog = VideoStoryboardRegenerationDialog(
            self.tr_text,
            scene.as_dict(),
            dict(self._plan_metadata),
            self,
            project_dir=self._source_project_dir,
        )
        dialog.generateRequested.connect(self._request_dialog_regeneration)
        dialog.candidateAccepted.connect(self._accept_dialog_candidate)
        dialog.candidateRejected.connect(self._reject_dialog_candidate)
        dialog.destroyed.connect(
            lambda _object=None, selected=dialog: self._clear_regeneration_dialog(
                selected
            )
        )
        self._regeneration_dialog = dialog
        dialog.open()

    def _copy_selected_frame(self) -> None:
        scene = self.selected_scene()
        if scene is None or not scene.image_path:
            return
        image = QImage(scene.image_path)
        if not image.isNull():
            QApplication.clipboard().setImage(image)

    def _paste_selected_frame(self) -> None:
        scene = self.selected_scene()
        if scene is None:
            return
        image = QApplication.clipboard().image()
        if not image.isNull():
            image = fit_pasted_image(
                self.tr_text, image,
                int(self._configuration.get("image", {}).get("width") or 1280),
                int(self._configuration.get("image", {}).get("height") or 720), self,
            )
        if not image.isNull():
            self.editFrameRequested.emit(scene.scene_id, image.copy())

    def _request_frame_replacement(self) -> None:
        scene = self.selected_scene()
        if scene is None:
            return
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr_text(
                "video_storyboard_replace_frame_dialog",
                "Choose replacement image",
            ),
            "",
            self.tr_text(
                "video_storyboard_image_files",
                "Image files (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)",
            ),
        )
        if selected:
            self.replaceFrameRequested.emit(scene.scene_id, selected)

    def _request_frame_edit(self) -> None:
        scene = self.selected_scene()
        if (
            scene is None
            or not scene.image_path
            or not Path(scene.image_path).is_file()
        ):
            return
        if self._image_edit_dialog is not None:
            self._image_edit_dialog.show()
            self._image_edit_dialog.raise_()
            self._image_edit_dialog.activateWindow()
            return
        previous_video_path = ""
        scene_index = self._scenes.index(scene)
        if scene_index > 0:
            previous = self._scenes[scene_index - 1]
            if previous.video_path and Path(previous.video_path).is_file():
                previous_video_path = previous.video_path
        dialog = VideoStoryboardImageEditDialog(
            self.tr_text,
            scene.scene_id,
            scene.image_path,
            previous_video_path=previous_video_path,
            plan=self._plan_metadata,
            configuration=self._configuration,
            project_dir=self._source_project_dir,
            ffmpeg_path=self._ffmpeg_path,
            seed=int(self._plan_metadata.get("base_seed") or 0),
            width=int(self._configuration.get("image", {}).get("width") or 1280),
            height=int(self._configuration.get("image", {}).get("height") or 720),
            parent=self,
        )
        dialog.imageAccepted.connect(self.editFrameRequested.emit)
        dialog.generateEditRequested.connect(self.imageEditRequested.emit)
        dialog.cancelEditRequested.connect(self.cancelImageEditRequested.emit)
        dialog.destroyed.connect(
            lambda _object=None, selected=dialog: self._clear_image_edit_dialog(
                selected
            )
        )
        self._image_edit_dialog = dialog
        dialog.open()

    def set_image_edit_progress(self, scene_id: str, message: str, percentage: int = -1) -> None:
        dialog = self._image_edit_dialog
        if dialog is not None and dialog.scene_id == str(scene_id):
            dialog.set_edit_progress(message, percentage)

    def set_image_edit_candidate(self, scene_id: str, image_path: str) -> None:
        dialog = self._image_edit_dialog
        if dialog is not None and dialog.scene_id == str(scene_id):
            dialog.set_edit_candidate(image_path)

    def set_image_edit_failed(self, scene_id: str, error: str) -> None:
        dialog = self._image_edit_dialog
        if dialog is not None and dialog.scene_id == str(scene_id):
            dialog.set_edit_failed(error)

    def _clear_image_edit_dialog(
        self,
        dialog: VideoStoryboardImageEditDialog,
    ) -> None:
        if self._image_edit_dialog is dialog:
            self._image_edit_dialog = None

    def _request_video_generation(self) -> None:
        scene = self.selected_scene()
        if scene is None or not scene.image_path or not Path(scene.image_path).is_file():
            return
        if self._video_dialog is not None:
            self._video_dialog.show_and_raise()
            return
        scene_value = scene.as_dict()
        scene_value["_video_provider"] = self._configuration.get("video_provider", "comfyui")
        scene_value["_runpod_config"] = {k: v for k, v in self._configuration.get("runpod", {}).items() if k in {"video_size", "video_endpoint", "image_endpoint", "edit_endpoint"}}
        video_config = self._configuration.get("comfyui_video", {})
        if isinstance(video_config, dict):
            scene_value["_video_config"] = dict(video_config)
        dialog = VideoStoryboardVideoDialog(
            self.tr_text,
            scene_value,
            dict(self._plan_metadata),
            self,
        )
        dialog.generateRequested.connect(self._request_dialog_video_generation)
        dialog.importRequested.connect(
            lambda path, scene_id=scene.scene_id: self.importVideoRequested.emit(
                {"scene_id": scene_id, "source_path": path}
            )
        )
        dialog.candidateAccepted.connect(self._accept_dialog_video_candidate)
        dialog.candidateRejected.connect(self._reject_dialog_video_candidate)
        dialog.destroyed.connect(
            lambda _object=None, selected=dialog: self._clear_video_dialog(selected)
        )
        self._video_dialog = dialog
        dialog.open()

    def _request_generate_all_videos(self) -> None:
        plan = dict(self._plan_metadata)
        eligible: list[tuple[StoryboardScene, dict[str, Any], str]] = []
        for scene in self._scenes:
            if not scene.image_path or not Path(scene.image_path).is_file():
                continue
            raw_scene = scene.as_dict()
            prompt = str(scene.video_prompt or "").strip()
            if not prompt:
                prompt = compile_effective_scene_prompt(plan, raw_scene)
            if not prompt:
                continue
            eligible.append((scene, raw_scene, prompt))
        if not eligible:
            self.append_activity(
                self.tr_text(
                    "video_storyboard_auto_video_no_eligible_scenes",
                    "No scene with a generated frame is available for video generation.",
                )
            )
            return

        existing_count = sum(
            1
            for scene, _raw_scene, _prompt in eligible
            if scene.video_path and Path(scene.video_path).is_file()
        )
        scope = self._choose_generate_all_videos_scope(
            len(eligible) - existing_count,
            len(eligible),
            existing_count,
        )
        if scope is None:
            return

        requests: list[dict[str, Any]] = []
        for scene, raw_scene, prompt in eligible:
            if (
                scope == "missing"
                and scene.video_path
                and Path(scene.video_path).is_file()
            ):
                continue
            requests.append(
                {
                    "scene": raw_scene,
                    "plan": plan,
                    "prompt": prompt,
                    # A scene with a missing clip can still retain the user's
                    # preferred reference position from an earlier attempt.
                    # New scenes default to the start frame.
                    "frame_role": str(scene.video_frame_role or "start"),
                    "automatic": True,
                }
            )
        if not requests:
            self.append_activity(
                self.tr_text(
                    "video_storyboard_auto_video_no_targets",
                    "All eligible scenes already have a video.",
                )
            )
            return
        self.generate_all_videos_button.setEnabled(False)
        self.generateAllVideosRequested.emit(requests)

    def _choose_generate_all_videos_scope(
        self,
        missing_count: int,
        total_count: int,
        existing_count: int,
    ) -> str | None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle(
            self.tr_text(
                "video_storyboard_generate_all_videos_title",
                "Generate scene videos",
            )
        )
        dialog.setText(
            self.tr_text(
                "video_storyboard_generate_all_videos_message",
                "Choose whether to generate only missing videos or regenerate every eligible scene.",
            )
        )
        dialog.setInformativeText(
            self.tr_text(
                "video_storyboard_generate_all_videos_summary",
                "Eligible scenes: {total} · Existing videos: {existing} · Missing videos: {missing}",
                total=total_count,
                existing=existing_count,
                missing=missing_count,
            )
        )
        missing_button = dialog.addButton(
            self.tr_text(
                "video_storyboard_generate_missing_videos",
                "Only missing ({count})",
                count=missing_count,
            ),
            QMessageBox.ButtonRole.AcceptRole,
        )
        overwrite_button = dialog.addButton(
            self.tr_text(
                "video_storyboard_overwrite_all_videos",
                "Overwrite all ({count})",
                count=total_count,
            ),
            QMessageBox.ButtonRole.DestructiveRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        missing_button.setEnabled(missing_count > 0)
        dialog.setDefaultButton(
            missing_button if missing_count > 0 else overwrite_button
        )
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is missing_button:
            return "missing"
        if clicked is overwrite_button:
            return "all"
        return None

    def set_auto_video_generation_progress(
        self,
        current: int,
        total: int,
        scene_id: str,
    ) -> None:
        message = self.tr_text(
            "video_storyboard_auto_video_progress",
            "Generating scene video {current}/{total}: {scene}",
            current=current,
            total=total,
            scene=scene_id,
        )
        self.set_operation(message, 0)
        self.append_activity(message)

    def finish_auto_video_generation(
        self,
        completed: int,
        total: int,
        failed: int,
    ) -> None:
        self.generate_all_videos_button.setEnabled(bool(self._scenes))
        message = self.tr_text(
            "video_storyboard_auto_video_complete",
            "Automatic video generation finished: {completed}/{total} scenes accepted.",
            completed=completed,
            total=total,
        )
        self.finish_operation(message)
        self.append_activity(message)

    def _request_dialog_video_generation(self, request: object) -> None:
        if not isinstance(request, dict):
            return
        scene_id = str(request.get("scene_id") or "")
        scene = next(
            (item.as_dict() for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return
        operation = self.tr_text(
            "video_storyboard_generating_scene_video",
            "Generating video for scene {scene}...",
            scene=scene_id,
        )
        self.set_operation(operation, 0)
        self.append_activity(operation)
        self.generateVideoRequested.emit(
            {
                "scene": scene,
                "plan": dict(self._plan_metadata),
                "prompt": str(request.get("prompt") or ""),
                "frame_role": str(request.get("frame_role") or "start"),
            }
        )

    def set_video_generation_progress(
        self,
        scene_id: str,
        message: str,
        percentage: int,
    ) -> None:
        self.set_operation(message, percentage)
        if self._video_dialog is not None and self._video_dialog.scene_id == scene_id:
            self._video_dialog.set_progress(message, percentage)

    def set_video_candidate(
        self,
        scene_id: str,
        path: str,
        duration_seconds: float = 0.0,
    ) -> None:
        if self._video_dialog is None or self._video_dialog.scene_id != scene_id:
            return
        self._video_dialog.set_candidate(path, duration_seconds)
        self.finish_operation(
            self.tr_text(
                "video_storyboard_video_candidate_ready",
                "Video candidate ready. Play it before accepting or discarding it.",
            )
        )

    def set_video_generation_failed(self, scene_id: str, error: str) -> None:
        if self._video_dialog is not None and self._video_dialog.scene_id == scene_id:
            self._video_dialog.set_failed(error)
        self.finish_operation(error)
        self.append_activity(error)

    def _accept_dialog_video_candidate(self, payload: object) -> None:
        if isinstance(payload, dict):
            self.videoCandidateAccepted.emit(payload)

    def _reject_dialog_video_candidate(self, scene_id: str, path: str) -> None:
        self.videoCandidateRejected.emit(scene_id, path)
        self.append_activity(
            self.tr_text(
                "video_storyboard_video_candidate_discarded",
                "Scene {scene}: video candidate discarded.",
                scene=scene_id,
            )
        )

    def _clear_video_dialog(self, dialog: VideoStoryboardVideoDialog) -> None:
        if self._video_dialog is dialog:
            self._video_dialog = None

    def set_scene_video(
        self,
        scene_id: str,
        video_path: str,
        prompt: str,
        frame_role: str,
        duration_seconds: float = 0.0,
        *, continuation_frame: str = "",
    ) -> None:
        scene = next(
            (item for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return
        self._record_scene_edit()
        if continuation_frame and 0 < duration_seconds < scene.duration_seconds and Path(continuation_frame).is_file():
            index = self._scenes.index(scene)
            clone = StoryboardScene.from_value(scene.as_dict(), index + 1)
            clone.scene_id = self._unique_split_scene_id(scene.scene_id)
            clone.duration_seconds = scene.duration_seconds - duration_seconds
            clone.image_path = continuation_frame
            clone.video_path = ""
            clone.video_duration_seconds = 0.0
            clone.video_frame_role = "start"
            clone.motion = clone.motion_in = clone.motion_out = "none"
            clone.video_motion_in = clone.video_motion_out = "none"
            scene.duration_seconds = duration_seconds
            self._scenes.insert(index + 1, clone)
            self._recalculate_starts()
            self.timeline_canvas.set_scenes(self._scenes)
        had_video = bool(scene.video_path)
        scene.video_path = str(video_path)
        scene.video_prompt = str(prompt).strip()
        scene.video_frame_role = "end" if frame_role == "end" else "start"
        scene.video_duration_seconds = max(
            0.0,
            float(duration_seconds or scene.duration_seconds),
        )
        if not had_video:
            scene.video_motion_in = "none"
            scene.video_motion_out = "none"
        self.timeline_canvas.update()
        self._show_selected_scene(scene)
        if not had_video:
            self.inspector_tabs.setCurrentIndex(0)
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()
        self.finish_operation(
            self.tr_text(
                "video_storyboard_video_accepted",
                "Scene {scene}: video layer accepted.",
                scene=scene_id,
            )
        )
        self.append_activity(self.operation_label.text())

    def _split_selected_frame(self) -> None:
        scene = self.selected_scene()
        if scene is None or scene.duration_seconds < 2.0:
            return
        self._record_scene_edit()
        index = self._scenes.index(scene)
        first_duration = round(scene.duration_seconds / 2.0, 3)
        second_duration = round(scene.duration_seconds - first_duration, 3)
        scene.duration_seconds = first_duration
        clone = StoryboardScene.from_value(scene.as_dict(), index + 1)
        clone.scene_id = self._unique_split_scene_id(scene.scene_id)
        clone.duration_seconds = second_duration
        self._scenes.insert(index + 1, clone)
        self._recalculate_starts()
        self.timeline_canvas.set_scenes(self._scenes)
        self._select_scene(clone.scene_id)
        self._sync_output_actions()
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()
        self.append_activity(
            self.tr_text(
                "video_storyboard_frame_split_done",
                "Frame {scene} was split into two equal timeline blocks.",
                scene=scene.scene_id,
            )
        )

    def _unique_split_scene_id(self, source_id: str) -> str:
        existing = {scene.scene_id for scene in self._scenes}
        base = f"{source_id}-split"
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _delete_selected_frame(self) -> None:
        scene = self.selected_scene()
        if scene is None:
            return
        answer = QMessageBox.question(
            self,
            self.tr_text(
                "video_storyboard_delete_frame_title",
                "Delete frame?",
            ),
            self.tr_text(
                "video_storyboard_delete_frame_confirmation",
                "Delete this frame from the timeline? Its duration will be absorbed by the adjacent frame.",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._record_scene_edit()
        index = self._scenes.index(scene)
        removed = self._scenes.pop(index)
        if self._scenes:
            recipient_index = index - 1 if index > 0 else 0
            self._scenes[recipient_index].duration_seconds += removed.duration_seconds
            selected = self._scenes[recipient_index]
        else:
            selected = None
        self._recalculate_starts()
        self.timeline_canvas.set_scenes(self._scenes)
        if selected is not None:
            self._select_scene(selected.scene_id)
        else:
            self._selected_scene_id = ""
            self._show_selected_scene(None)
        self.timeline_canvas._pixmap_cache.clear()
        self.timeline_canvas.update()
        self.generate_frames_button.setEnabled(bool(self._scenes))
        self.regenerate_all_button.setEnabled(bool(self._scenes))
        self._sync_output_actions()
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()
        self.append_activity(
            self.tr_text(
                "video_storyboard_frame_deleted",
                "Frame {scene} was deleted and its duration was absorbed by an adjacent frame.",
                scene=removed.scene_id,
            )
        )

    def _request_dialog_regeneration(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        scene_id = str(payload.get("scene_id") or "")
        generation_payload = self.regeneration_payload(
            scene_id,
            str(payload.get("prompt") or ""),
            (
                payload.get("overrides")
                if isinstance(payload.get("overrides"), dict)
                else {}
            ),
            str(payload.get("shot") or ""),
        )
        if generation_payload is not None and self._regeneration_dialog is not None:
            scenes = generation_payload.get("scenes", [])
            plan = generation_payload.get("plan", {})
            if (
                isinstance(scenes, list)
                and scenes
                and isinstance(scenes[0], dict)
                and isinstance(plan, dict)
            ):
                self._regeneration_dialog.set_compiled_prompt(
                    compile_effective_scene_prompt(plan, scenes[0])
                )
        operation = self.tr_text(
            "video_storyboard_generating_scene",
            "Generating frame for scene {scene}...",
            scene=scene_id,
        )
        self.set_operation(operation)
        self.append_activity(operation)
        self.regenerateRequested.emit(payload)

    def _accept_dialog_candidate(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        scene_id = str(payload.get("scene_id") or "")
        scene = next(
            (item for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        image_path = str(payload.get("image_path") or "")
        prompt = str(payload.get("prompt") or "").strip()
        shot = str(payload.get("shot") or "").strip()
        overrides = payload.get("overrides", {})
        if scene is None or not image_path:
            return
        self._record_scene_edit()
        scene.image_path = image_path
        scene.status = "generated"
        scene.prompt = prompt
        scene.shot = shot
        scene.generation_overrides = (
            dict(overrides) if isinstance(overrides, dict) else {}
        )
        location_state_ids = scene.generation_overrides.get(
            "location_state_ids", []
        )
        if isinstance(location_state_ids, list):
            scene.locations = [
                str(value).strip()
                for value in location_state_ids
                if str(value).strip()
            ]
        override_style = scene.generation_overrides.get("style", {})
        if isinstance(override_style, dict) and isinstance(
            override_style.get("characters"),
            list,
        ):
            scene.characters = [
                str(value).split(":", 1)[0].strip()
                for value in override_style["characters"]
                if str(value).split(":", 1)[0].strip()
            ]
        narrative = scene.generation_overrides.get("narrative", {})
        if isinstance(narrative, dict) and narrative.get("era"):
            scene.era = str(narrative["era"])
            scene.era_state_id = ""
        self.timeline_canvas._pixmap_cache.clear()
        self.timeline_canvas.update()
        self.current_preview.set_image(
            image_path,
            self.tr_text("video_storyboard_frame_pending", "Frame not generated"),
        )
        self.regenerationAccepted.emit(scene_id, image_path, prompt)
        self.scenesChanged.emit(self.scenes())
        self._sync_output_actions()
        self.append_activity(
            self.tr_text(
                "video_storyboard_candidate_accepted",
                "Scene {scene}: candidate accepted.",
                scene=scene_id,
            )
        )

    def _reject_dialog_candidate(self, scene_id: str, path: str) -> None:
        self.regenerationRejected.emit(scene_id, path)
        self.append_activity(
            self.tr_text(
                "video_storyboard_candidate_rejected",
                "Scene {scene}: candidate discarded.",
                scene=scene_id,
            )
        )

    def _clear_regeneration_dialog(
        self,
        dialog: VideoStoryboardRegenerationDialog,
    ) -> None:
        if self._regeneration_dialog is dialog:
            self._regeneration_dialog = None

    def _request_analysis(self) -> None:
        if not self._audiobook_text.strip():
            return
        self.analyzeRequested.emit(self.source_payload())

    def set_analysis_started(self) -> None:
        self.analyze_button.setEnabled(False)
        operation = self.tr_text(
            "video_storyboard_analyzing",
            "Analyzing audiobook text and timings...",
        )
        self.set_operation(operation)
        self.append_activity(operation)

    def _request_frame_generation(self) -> None:
        self._open_frame_batch_dialog("generate")

    def _request_regenerate_all(self) -> None:
        self._open_frame_batch_dialog("regenerate")

    def _open_frame_batch_dialog(self, mode: str) -> None:
        if not self._scenes:
            return
        if self._frame_batch_dialog is not None:
            self._frame_batch_dialog.show_and_raise()
            return
        existing_count = sum(
            bool(scene.image_path and Path(scene.image_path).is_file())
            for scene in self._scenes
        )
        dialog = VideoStoryboardFrameBatchDialog(
            self.tr_text,
            mode=mode,
            total_count=len(self._scenes),
            existing_count=existing_count,
            parent=self,
        )
        dialog.startRequested.connect(
            lambda overwrite, selected_mode=mode: self._start_frame_batch(
                selected_mode,
                overwrite,
            )
        )
        dialog.cancelRequested.connect(self.cancelFrameGenerationRequested.emit)
        dialog.settingsRequested.connect(self.storyboardSettingsRequested.emit)
        dialog.destroyed.connect(
            lambda _object=None, selected=dialog: self._clear_frame_batch_dialog(
                selected
            )
        )
        self._frame_batch_dialog = dialog
        dialog.open()

    def _start_frame_batch(self, mode: str, overwrite: bool) -> None:
        if not self._scenes:
            return
        payload = self.frame_generation_payload()
        if mode == "regenerate":
            effective = self.project_overrides()
            plan = payload.setdefault("plan", {})
            if isinstance(plan, dict):
                plan["base_seed"] = effective["seed"]
                plan["style_mode"] = effective["style_mode"]
                plan["style"] = deepcopy(effective["style"])
                plan["narrative_context"] = deepcopy(effective["narrative"])
                plan["video_overrides"] = deepcopy(effective["video"])
        scenes = payload.get("scenes", [])
        if overwrite and isinstance(scenes, list):
            for scene in scenes:
                if isinstance(scene, dict):
                    scene["image_path"] = ""
                    scene["status"] = "planned"
        self.generate_frames_button.setEnabled(False)
        self.regenerate_all_button.setEnabled(False)
        self._frame_generation_history_active = True
        self._frame_generation_history_recorded = False
        self._frame_generation_start_snapshot = self._scene_snapshot()
        self._frame_batch_mode = mode
        self._frame_batch_completed = 0
        operation = self.tr_text(
            "video_storyboard_regenerating_all_frames"
            if mode == "regenerate"
            else "video_storyboard_generating_all_frames",
            "Regenerating storyboard frames with the project visual direction..."
            if mode == "regenerate"
            else "Starting frame generation...",
        )
        self.set_operation(operation)
        self.append_activity(operation)
        self.generateFramesRequested.emit(payload)

    def _clear_frame_batch_dialog(
        self,
        dialog: VideoStoryboardFrameBatchDialog,
    ) -> None:
        if self._frame_batch_dialog is dialog:
            self._frame_batch_dialog = None

    def _accept_candidate(self) -> None:
        scene = next(
            (
                item
                for item in self._scenes
                if item.scene_id == self._candidate_scene_id
            ),
            None,
        )
        if scene is None or not self._candidate_path:
            return
        self._record_scene_edit()
        scene.image_path = self._candidate_path
        scene.status = "generated"
        self.timeline_canvas._pixmap_cache.clear()
        self.timeline_canvas.update()
        self.current_preview.set_image(
            scene.image_path,
            self.tr_text(
                "video_storyboard_frame_pending",
                "Frame not generated",
            ),
        )
        self.candidate_group.hide()
        self.regenerationAccepted.emit(
            scene.scene_id,
            scene.image_path,
            scene.prompt,
        )
        self.scenesChanged.emit(self.scenes())
        self._sync_output_actions()
        self.append_activity(
            self.tr_text(
                "video_storyboard_candidate_accepted",
                "Scene {scene}: candidate accepted.",
                scene=scene.scene_id,
            )
        )
        self._clear_candidate()

    def _reject_candidate(self) -> None:
        if not self._candidate_scene_id:
            return
        scene_id, path = self._candidate_scene_id, self._candidate_path
        self.candidate_group.hide()
        self.regenerationRejected.emit(scene_id, path)
        self.append_activity(
            self.tr_text(
                "video_storyboard_candidate_rejected",
                "Scene {scene}: candidate discarded.",
                scene=scene_id,
            )
        )
        self._clear_candidate()

    def _clear_candidate(self) -> None:
        self._candidate_scene_id = ""
        self._candidate_path = ""
        self.candidate_preview.set_image(
            "",
            self.tr_text(
                "video_storyboard_preview_unavailable",
                "Preview unavailable",
            ),
        )

    def _request_render(self) -> None:
        if not self._all_frames_ready():
            return
        self.render_button.setEnabled(False)
        operation = self.tr_text(
            "video_storyboard_rendering",
            "Rendering final video...",
        )
        self.set_operation(operation)
        self.append_activity(operation)
        self.renderRequested.emit(self._effective_render_scenes())

    def set_frame_replaced(self, scene_id: str, image_path: str) -> None:
        scene = next(
            (item for item in self._scenes if item.scene_id == scene_id),
            None,
        )
        if scene is None:
            return
        self._record_scene_edit()
        scene.image_path = str(image_path)
        scene.status = "replaced"
        self.timeline_canvas._pixmap_cache.clear()
        self.timeline_canvas.update()
        if self._selected_scene_id == scene_id:
            self.current_preview.set_image(
                scene.image_path,
                self.tr_text("video_storyboard_frame_pending", "Frame not generated"),
            )
            self.convert_frame_video_button.setEnabled(
                bool(scene.image_path and Path(scene.image_path).is_file())
            )
        self._sync_output_actions()
        self.scenesChanged.emit(self.scenes())
        self._remember_scene_snapshot()
        self.append_activity(
            self.tr_text(
                "video_storyboard_frame_replaced",
                "Frame {scene} was replaced with an imported image.",
                scene=scene_id,
            )
        )

    def set_frame_replacement_failed(self, error: str) -> None:
        self.append_activity(
            self.tr_text(
                "video_storyboard_frame_replace_failed",
                "The frame image could not be replaced: {error}",
                error=error,
            )
        )

    def _all_frames_ready(self) -> bool:
        return bool(self._scenes) and all(
            bool(scene.image_path) and Path(scene.image_path).is_file()
            for scene in self._scenes
        )

    def _sync_output_actions(self) -> None:
        frames_ready = self._all_frames_ready()
        self.render_button.setVisible(frames_ready)
        self.render_button.setEnabled(frames_ready)
        output_ready = bool(
            self._rendered_output_path
            and Path(self._rendered_output_path).is_file()
        )
        self.open_output_folder_button.setVisible(output_ready)
        self.open_output_folder_button.setEnabled(output_ready)
        self._sync_rendered_header()

    def _effective_render_scenes(self) -> list[dict[str, Any]]:
        scenes = self.scenes()
        motion = str(self.render_overrides().get("motion") or "alternate")
        for index, scene in enumerate(scenes):
            if motion in {"zoom_in", "zoom_out"}:
                scene["motion"] = motion
            elif motion == "alternate":
                scene["motion"] = "zoom_in" if index % 2 == 0 else "zoom_out"
        return scenes

    def _sync_project_controls(self) -> None:
        if not hasattr(self, "video_zoom_spin"):
            return
        style = self._plan_metadata.get("style", {})
        if not isinstance(style, dict):
            style = {}
        elif "continuity" in style:
            # Migrate older storyboard projects in memory. Continuity was a
            # generic prompt field and is no longer part of generation.
            style = {
                key: value
                for key, value in style.items()
                if key != "continuity"
            }
            self._plan_metadata["style"] = style
        narrative = self._plan_metadata.get("narrative_context", {})
        if not isinstance(narrative, dict):
            narrative = {}
        overrides = self._plan_metadata.get("video_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
        defaults = self._configuration.get("video", {})
        if not isinstance(defaults, dict):
            defaults = {}
        image_defaults = self._configuration.get("image", {})
        if not isinstance(image_defaults, dict):
            image_defaults = {}
        raw_style_mode = self._plan_metadata.get("style_mode")
        if not raw_style_mode and style.get("medium"):
            raw_style_mode = storyboard_style_id_from_prompt(
                style.get("medium")
            )
        style_mode = normalize_storyboard_style_id(
            raw_style_mode
            or image_defaults.get("style_mode")
            or DEFAULT_STORYBOARD_STYLE_ID
        )
        catalog_style = storyboard_style(style_mode)
        widgets = (
            self.style_gallery,
            self.style_medium_edit,
            self.style_palette_edit,
            self.style_lighting_edit,
            self.seed_edit,
            self.story_era_edit,
            self.style_characters_edit,
            self.style_negative_edit,
            self.video_zoom_spin,
            self.video_transition_spin,
            self.default_motion_combo,
        )
        self._updating_project_controls = True
        for widget in widgets:
            widget.blockSignals(True)
        try:
            selected_item = self._style_gallery_item(style_mode)
            self.style_gallery.setCurrentItem(selected_item)
            if selected_item is not None:
                QTimer.singleShot(0, self._center_selected_style)
            medium = str(style.get("medium") or "")
            if not medium and catalog_style is not None:
                medium = catalog_style.prompt
            self.style_medium_edit.setText(medium)
            self.style_palette_edit.setText(str(style.get("palette") or ""))
            self.style_lighting_edit.setText(str(style.get("lighting") or ""))
            stored_seed = self._plan_metadata.get("base_seed")
            if stored_seed is None:
                stored_seed = int.from_bytes(
                    hashlib.sha256(
                        self._audiobook_text.encode("utf-8")
                    ).digest()[:6],
                    "big",
                )
            self.seed_edit.setText(str(stored_seed))
            self._detected_era_value = (
                str(narrative.get("era") or "").strip()
                if narrative.get("era_source") == "detected"
                else ""
            )
            self.story_era_edit.setText(str(narrative.get("era") or ""))
            characters = style.get("characters", [])
            self.style_characters_edit.setPlainText(
                "\n".join(str(value) for value in characters)
                if isinstance(characters, list)
                else ""
            )
            self.style_negative_edit.setText(str(style.get("negative") or ""))
            self.video_zoom_spin.setValue(
                float(
                    (
                        overrides["zoom_percent"]
                        if "zoom_percent" in overrides
                        else defaults.get("zoom_percent", 30.0)
                    )
                )
            )
            self.video_transition_spin.setValue(
                float(
                    overrides.get(
                        "transition_seconds",
                        defaults.get("transition_seconds", 0.7),
                    )
                    or 0.0
                )
            )
            motion = str(overrides.get("motion") or "alternate")
            motion_index = self.default_motion_combo.findData(motion)
            self.default_motion_combo.setCurrentIndex(
                max(0, motion_index)
            )
        finally:
            for widget in widgets:
                widget.blockSignals(False)
            self._updating_project_controls = False
        self._sync_continuity_trees()

    def _sync_continuity_trees(self) -> None:
        if not hasattr(self, "continuity_characters_tree"):
            return
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
        characters = [
            value for value in continuity.get("characters", [])
            if isinstance(value, dict)
        ]
        locations = [
            value for value in continuity.get("locations", [])
            if isinstance(value, dict)
        ]
        eras = [
            value for value in continuity.get("eras", [])
            if isinstance(value, dict)
        ]
        self._updating_continuity_trees = True
        try:
            self.continuity_characters_tree.clear()
            for character in characters:
                states = [
                    state for state in character.get("states", [])
                    if isinstance(state, dict)
                ]
                identity_item = QTreeWidgetItem(
                    [
                        str(character.get("name") or character.get("id") or ""),
                        self._continuity_time(states[0].get("from_seconds")) if states else "",
                        self._continuity_time(states[-1].get("to_seconds")) if states else "",
                        str(character.get("identity_description") or ""),
                    ]
                )
                identity_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"kind": "character", "id": character.get("id", "")},
                )
                identity_item.setFlags(
                    identity_item.flags() | Qt.ItemFlag.ItemIsEditable
                )
                self.continuity_characters_tree.addTopLevelItem(identity_item)
                for index, state in enumerate(states, start=1):
                    state_item = QTreeWidgetItem(
                        [
                            self.tr_text(
                                "video_storyboard_continuity_state",
                                "State {number}",
                                number=index,
                            ),
                            self._continuity_time(state.get("from_seconds")),
                            self._continuity_time(state.get("to_seconds")),
                            str(state.get("description") or ""),
                        ]
                    )
                    state_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        {
                            "kind": "character_state",
                            "parent_id": character.get("id", ""),
                            "id": state.get("id", ""),
                        },
                    )
                    state_item.setToolTip(
                        3,
                        str(state.get("evidence") or state.get("change_reason") or ""),
                    )
                    state_item.setFlags(
                        state_item.flags() | Qt.ItemFlag.ItemIsEditable
                    )
                    identity_item.addChild(state_item)
                identity_item.setExpanded(True)

            self.continuity_locations_tree.clear()
            for location in locations:
                states = [
                    state for state in location.get("states", [])
                    if isinstance(state, dict)
                ]
                identity_item = QTreeWidgetItem(
                    [
                        str(location.get("name") or location.get("id") or ""),
                        self._continuity_time(states[0].get("from_seconds")) if states else "",
                        self._continuity_time(states[-1].get("to_seconds")) if states else "",
                        str(location.get("identity_description") or ""),
                    ]
                )
                identity_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"kind": "location", "id": location.get("id", "")},
                )
                identity_item.setFlags(
                    identity_item.flags() | Qt.ItemFlag.ItemIsEditable
                )
                self.continuity_locations_tree.addTopLevelItem(identity_item)
                for index, state in enumerate(states, start=1):
                    state_item = QTreeWidgetItem(
                        [
                            self.tr_text(
                                "video_storyboard_continuity_state",
                                "State {number}",
                                number=index,
                            ),
                            self._continuity_time(state.get("from_seconds")),
                            self._continuity_time(state.get("to_seconds")),
                            str(state.get("description") or ""),
                        ]
                    )
                    state_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        {
                            "kind": "location_state",
                            "parent_id": location.get("id", ""),
                            "id": state.get("id", ""),
                        },
                    )
                    state_item.setFlags(
                        state_item.flags() | Qt.ItemFlag.ItemIsEditable
                    )
                    identity_item.addChild(state_item)
                identity_item.setExpanded(True)

            self.continuity_eras_tree.clear()
            for era in eras:
                era_item = QTreeWidgetItem(
                    [
                        str(era.get("description") or era.get("id") or ""),
                        self._continuity_time(era.get("from_seconds")),
                        self._continuity_time(era.get("to_seconds")),
                        str(era.get("material_culture") or ""),
                    ]
                )
                era_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"kind": "era", "id": era.get("id", "")},
                )
                era_item.setFlags(era_item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.continuity_eras_tree.addTopLevelItem(era_item)
        finally:
            self._updating_continuity_trees = False
        has_characters = bool(characters)
        # Keep entity actions available even before an automatic analysis has
        # discovered the first character.
        self.continuity_characters_panel.setVisible(True)
        self.manual_characters_label.setVisible(not has_characters)
        self.style_characters_edit.setVisible(not has_characters)
        self._sync_character_state_actions()
        self._sync_location_state_actions()

    @staticmethod
    def _continuity_time(value: object) -> str:
        if value is None or value == "":
            return "—"
        try:
            return StoryboardTimelineCanvas._format_time(float(value))
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _parse_continuity_time(value: str) -> float | None:
        text = str(value or "").strip().replace(",", ".")
        if not text or text == "—":
            return None
        try:
            parts = [float(part) for part in text.split(":")]
        except ValueError:
            return None
        if len(parts) == 1:
            seconds = parts[0]
        elif len(parts) == 2:
            seconds = parts[0] * 60.0 + parts[1]
        elif len(parts) == 3:
            seconds = parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
        else:
            return None
        return seconds if seconds >= 0.0 else None

    def _sync_character_state_actions(self) -> None:
        if not hasattr(self, "new_character_state_button"):
            return
        item = self.continuity_characters_tree.currentItem()
        reference = (
            item.data(0, Qt.ItemDataRole.UserRole)
            if item is not None
            else None
        )
        kind = str(reference.get("kind") or "") if isinstance(reference, dict) else ""
        self.new_character_state_button.setEnabled(
            kind in {"character", "character_state"}
        )
        self.delete_character_state_button.setEnabled(kind == "character_state")
        self.delete_character_button.setEnabled(
            kind in {"character", "character_state"}
        )
        self.edit_character_button.setEnabled(
            kind in {"character", "character_state"}
        )

    def _sync_location_state_actions(self) -> None:
        if not hasattr(self, "new_location_state_button"):
            return
        item = self.continuity_locations_tree.currentItem()
        reference = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        kind = str(reference.get("kind") or "") if isinstance(reference, dict) else ""
        selected = kind in {"location", "location_state"}
        self.edit_location_button.setEnabled(selected)
        self.delete_location_button.setEnabled(selected)
        self.new_location_state_button.setEnabled(selected)
        self.delete_location_state_button.setEnabled(kind == "location_state")

    def _new_character(self) -> None:
        total = self._entity_total_duration()
        dialog = VideoStoryboardEntityDialog(
            self.tr_text, "character", total, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        name = str(values["name"])
        continuity = self._plan_metadata.setdefault("continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            self._plan_metadata["continuity"] = continuity
        characters = continuity.setdefault("characters", [])
        if not isinstance(characters, list):
            characters = []
            continuity["characters"] = characters
        character_id = self._unique_continuity_id(name, characters, "character")
        state_id = f"{character_id}_state_1"
        reference = self._import_entity_reference(
            str(values.get("reference_image_path") or ""), "characters", name
        )
        characters.append(
            {
                "id": character_id,
                "name": name,
                "aliases": list(values["aliases"]),
                "identity_description": str(values["identity_description"]),
                "reference_image_path": reference,
                "user_authored": True,
                "states": [
                    {
                        "id": state_id,
                        "description": str(values["state_description"]),
                        "from_seconds": float(values["from_seconds"]),
                        "to_seconds": float(values["to_seconds"]),
                        "change_reason": "Manually added by the user.",
                    }
                ],
            }
        )
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_character(character_id)

    def _edit_character(self) -> None:
        character = self._selected_character_record()
        if character is None:
            return
        dialog = VideoStoryboardEntityDialog(
            self.tr_text,
            "character",
            self._entity_total_duration(),
            character,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        old_name = str(character.get("name") or "")
        old_reference = str(character.get("reference_image_path") or "")
        character["name"] = str(values["name"])
        character["aliases"] = list(values["aliases"])
        character["identity_description"] = str(values["identity_description"])
        character["reference_image_path"] = self._import_entity_reference(
            str(values.get("reference_image_path") or ""),
            "characters",
            str(values["name"]),
        )
        self._replace_entity_reference_paths(
            old_reference,
            str(character.get("reference_image_path") or ""),
            old_name,
            str(values["name"]),
        )
        states = [value for value in character.get("states", []) if isinstance(value, dict)]
        if states:
            states[0].update({
                "description": str(values["state_description"]),
                "from_seconds": float(values["from_seconds"]),
                "to_seconds": float(values["to_seconds"]),
            })
        else:
            character["states"] = [{
                "id": f"{character.get('id') or 'character'}_state_1",
                "description": str(values["state_description"]),
                "from_seconds": float(values["from_seconds"]),
                "to_seconds": float(values["to_seconds"]),
                "change_reason": "Manually added by the user.",
            }]
        if old_name and old_name != character["name"]:
            character.setdefault("aliases", []).append(old_name)
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_character(str(character.get("id") or ""))

    def _entity_total_duration(self) -> float:
        return max(
            self._source_duration_seconds,
            max(
                (scene.start_seconds + scene.duration_seconds for scene in self._scenes),
                default=0.0,
            ),
            1.0,
        )

    def _import_entity_reference(self, source: str, collection: str, name: str) -> str:
        source_path = Path(str(source or ""))
        if not source_path.is_file():
            return ""
        if not self._source_project_dir:
            return str(source_path.resolve())
        normalized = self._unique_continuity_id(name, [], collection.rstrip("s"))
        target_dir = Path(self._source_project_dir) / "storyboard" / "references" / collection
        target = target_dir / f"{normalized}.png"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            image = QImage(str(source_path))
            if image.isNull() or not image.save(str(target), "PNG"):
                raise OSError("Qt could not encode the reference image.")
        except OSError as exc:
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_reference_copy_failed", "Reference image unavailable"),
                str(exc),
            )
            return str(source_path.resolve())
        return str(target.resolve())

    def _replace_entity_reference_paths(
        self,
        old_path: str,
        new_path: str,
        old_label: str,
        new_label: str,
    ) -> None:
        old_key = str(old_path or "").casefold()
        old_name = str(old_label or "").casefold()
        for scene in self._scenes:
            overrides = scene.generation_overrides
            references = overrides.get("reference_images", []) if isinstance(overrides, dict) else []
            if not isinstance(references, list):
                continue
            updated: list[object] = []
            for reference in references:
                if not isinstance(reference, dict):
                    updated.append(reference)
                    continue
                matches = bool(
                    (old_key and str(reference.get("path") or "").casefold() == old_key)
                    or (old_name and str(reference.get("label") or "").casefold() == old_name)
                )
                if not matches:
                    updated.append(reference)
                elif new_path:
                    replacement = dict(reference)
                    replacement.update({"path": new_path, "label": new_label})
                    updated.append(replacement)
            overrides["reference_images"] = updated

    @staticmethod
    def _unique_continuity_id(
        name: str,
        records: list[object],
        fallback: str,
    ) -> str:
        normalized = unicodedata.normalize("NFKD", str(name or ""))
        base = re.sub(
            r"[^a-z0-9]+",
            "_",
            normalized.encode("ascii", "ignore").decode("ascii").casefold(),
        ).strip("_") or fallback
        existing = {
            str(value.get("id") or "")
            for value in records
            if isinstance(value, dict)
        }
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _delete_character(self) -> None:
        character = self._selected_character_record()
        if character is None:
            return
        name = str(character.get("name") or character.get("id") or "")
        answer = QMessageBox.question(
            self,
            self.tr_text(
                "video_storyboard_delete_character_title",
                "Delete character?",
            ),
            self.tr_text(
                "video_storyboard_delete_character_confirmation",
                "Delete {name} and all of this character's visual states? Scene references will also be removed.",
                name=name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            return
        character_id = str(character.get("id") or "")
        reference_path = str(character.get("reference_image_path") or "").casefold()
        state_ids = {
            str(state.get("id") or "")
            for state in character.get("states", [])
            if isinstance(state, dict)
        }
        removed_references = {
            value.casefold()
            for value in state_ids | {character_id, name}
            if value
        }
        continuity["characters"] = [
            value
            for value in continuity.get("characters", [])
            if not isinstance(value, dict)
            or str(value.get("id") or "") != character_id
        ]
        for assignment in continuity.get("assignments", []):
            if not isinstance(assignment, dict):
                continue
            assignment["character_ids"] = [
                value
                for value in assignment.get("character_ids", [])
                if str(value).casefold() not in removed_references
            ]
        for scene in self._scenes:
            scene.characters = [
                value
                for value in scene.characters
                if str(value).casefold() not in removed_references
            ]
            overrides = scene.generation_overrides
            if isinstance(overrides, dict) and isinstance(
                overrides.get("reference_images"), list
            ):
                overrides["reference_images"] = [
                    reference for reference in overrides["reference_images"]
                    if not isinstance(reference, dict)
                    or (
                        str(reference.get("path") or "").casefold() != reference_path
                        and str(reference.get("label") or "").casefold()
                        not in {name.casefold(), character_id.casefold()}
                    )
                ]
            style = overrides.get("style", {}) if isinstance(overrides, dict) else {}
            if isinstance(style, dict) and isinstance(style.get("characters"), list):
                style["characters"] = [
                    line
                    for line in style["characters"]
                    if str(line).split(":", 1)[0].strip().casefold()
                    not in removed_references
                ]
        self._commit_continuity_changes()
        self._sync_continuity_trees()

    def _select_character(self, character_id: str) -> None:
        for index in range(self.continuity_characters_tree.topLevelItemCount()):
            item = self.continuity_characters_tree.topLevelItem(index)
            reference = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(reference, dict) and reference.get("id") == character_id:
                self.continuity_characters_tree.setCurrentItem(item)
                return

    def _selected_character_record(self) -> dict[str, Any] | None:
        item = self.continuity_characters_tree.currentItem()
        if item is None:
            return None
        reference = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(reference, dict):
            return None
        character_id = str(
            reference.get("parent_id") or reference.get("id") or ""
        )
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            return None
        return next(
            (
                character
                for character in continuity.get("characters", [])
                if isinstance(character, dict)
                and str(character.get("id") or "") == character_id
            ),
            None,
        )

    def _new_location(self) -> None:
        dialog = VideoStoryboardEntityDialog(
            self.tr_text, "location", self._entity_total_duration(), parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        continuity = self._plan_metadata.setdefault("continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            self._plan_metadata["continuity"] = continuity
        locations = continuity.setdefault("locations", [])
        if not isinstance(locations, list):
            locations = []
            continuity["locations"] = locations
        location_id = self._unique_continuity_id(str(values["name"]), locations, "location")
        locations.append({
            "id": location_id,
            "name": str(values["name"]),
            "aliases": list(values["aliases"]),
            "identity_description": str(values["identity_description"]),
            "reference_image_path": self._import_entity_reference(
                str(values.get("reference_image_path") or ""),
                "locations",
                str(values["name"]),
            ),
            "user_authored": True,
            "states": [{
                "id": f"{location_id}_state_1",
                "description": str(values["state_description"]),
                "from_seconds": float(values["from_seconds"]),
                "to_seconds": float(values["to_seconds"]),
                "change_reason": "Manually added by the user.",
            }],
        })
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_location(location_id)

    def _edit_location(self) -> None:
        location = self._selected_location_record()
        if location is None:
            return
        dialog = VideoStoryboardEntityDialog(
            self.tr_text, "location", self._entity_total_duration(), location, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        old_name = str(location.get("name") or "")
        old_reference = str(location.get("reference_image_path") or "")
        location["name"] = str(values["name"])
        location["aliases"] = list(values["aliases"])
        location["identity_description"] = str(values["identity_description"])
        location["reference_image_path"] = self._import_entity_reference(
            str(values.get("reference_image_path") or ""),
            "locations",
            str(values["name"]),
        )
        self._replace_entity_reference_paths(
            old_reference,
            str(location.get("reference_image_path") or ""),
            old_name,
            str(values["name"]),
        )
        states = [value for value in location.get("states", []) if isinstance(value, dict)]
        if states:
            states[0].update({
                "description": str(values["state_description"]),
                "from_seconds": float(values["from_seconds"]),
                "to_seconds": float(values["to_seconds"]),
            })
        else:
            location["states"] = [{
                "id": f"{location.get('id') or 'location'}_state_1",
                "description": str(values["state_description"]),
                "from_seconds": float(values["from_seconds"]),
                "to_seconds": float(values["to_seconds"]),
                "change_reason": "Manually added by the user.",
            }]
        if old_name and old_name != location["name"]:
            location.setdefault("aliases", []).append(old_name)
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_location(str(location.get("id") or ""))

    def _delete_location(self) -> None:
        location = self._selected_location_record()
        if location is None:
            return
        name = str(location.get("name") or location.get("id") or "")
        answer = QMessageBox.question(
            self,
            self.tr_text("video_storyboard_delete_location_title", "Delete location?"),
            self.tr_text(
                "video_storyboard_delete_location_confirmation",
                "Delete {name} and all of this location's visual states? Scene references will also be removed.",
                name=name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            return
        location_id = str(location.get("id") or "")
        reference_path = str(location.get("reference_image_path") or "").casefold()
        state_ids = {
            str(state.get("id") or "") for state in location.get("states", [])
            if isinstance(state, dict)
        }
        removed = {value.casefold() for value in state_ids | {location_id, name} if value}
        continuity["locations"] = [
            value for value in continuity.get("locations", [])
            if not isinstance(value, dict) or str(value.get("id") or "") != location_id
        ]
        for assignment in continuity.get("assignments", []):
            if isinstance(assignment, dict):
                assignment["location_ids"] = [
                    value for value in assignment.get("location_ids", [])
                    if str(value).casefold() not in removed
                ]
        for scene in self._scenes:
            scene.locations = [
                value for value in scene.locations if str(value).casefold() not in removed
            ]
            overrides = scene.generation_overrides
            if isinstance(overrides, dict):
                overrides["location_state_ids"] = [
                    value for value in overrides.get("location_state_ids", [])
                    if str(value).casefold() not in removed
                ]
                if isinstance(overrides.get("reference_images"), list):
                    overrides["reference_images"] = [
                        reference for reference in overrides["reference_images"]
                        if not isinstance(reference, dict)
                        or (
                            str(reference.get("path") or "").casefold() != reference_path
                            and str(reference.get("label") or "").casefold()
                            not in {name.casefold(), location_id.casefold()}
                        )
                    ]
        self._commit_continuity_changes()
        self._sync_continuity_trees()

    def _selected_location_record(self) -> dict[str, Any] | None:
        item = self.continuity_locations_tree.currentItem()
        reference = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(reference, dict):
            return None
        location_id = str(reference.get("parent_id") or reference.get("id") or "")
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            return None
        return next((
            value for value in continuity.get("locations", [])
            if isinstance(value, dict) and str(value.get("id") or "") == location_id
        ), None)

    def _select_location(self, location_id: str) -> None:
        for index in range(self.continuity_locations_tree.topLevelItemCount()):
            item = self.continuity_locations_tree.topLevelItem(index)
            reference = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(reference, dict) and reference.get("id") == location_id:
                self.continuity_locations_tree.setCurrentItem(item)
                return

    def _select_location_state(self, state_id: str) -> None:
        for index in range(self.continuity_locations_tree.topLevelItemCount()):
            parent = self.continuity_locations_tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                reference = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(reference, dict) and reference.get("id") == state_id:
                    self.continuity_locations_tree.setCurrentItem(child)
                    return

    def _new_location_state(self) -> None:
        location = self._selected_location_record()
        if location is None:
            return
        states = [value for value in location.get("states", []) if isinstance(value, dict)]
        start = self._state_time_value(states[-1].get("to_seconds"), 0.0) if states else 0.0
        total = self._entity_total_duration()
        if start >= total and states:
            start = self._state_time_value(states[-1].get("from_seconds"), 0.0)
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr_text("video_storyboard_new_location_state", "New location state"))
        dialog.resize(760, 410)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        start_spin = QDoubleSpinBox()
        end_spin = QDoubleSpinBox()
        for spin in (start_spin, end_spin):
            spin.setRange(0.0, max(1_000_000.0, total))
            spin.setDecimals(3)
            spin.setSuffix(" s")
        start_spin.setValue(start)
        end_spin.setValue(max(start, total))
        description = QPlainTextEdit()
        description.setMinimumSize(540, 160)
        form.addRow(self.tr_text("video_storyboard_continuity_from", "From"), start_spin)
        form.addRow(self.tr_text("video_storyboard_continuity_to", "To"), end_spin)
        form.addRow(self.tr_text("video_storyboard_continuity_definition", "Visual definition"), description)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._add_location_state_record(location, start_spin.value(), end_spin.value(), description.toPlainText())

    def _add_location_state_record(
        self, location: dict[str, Any], from_seconds: float, to_seconds: float, description: str
    ) -> str:
        states = location.setdefault("states", [])
        if not isinstance(states, list):
            states = []
            location["states"] = states
        location_id = str(location.get("id") or "location")
        existing = {str(value.get("id") or "") for value in states if isinstance(value, dict)}
        number = 1
        state_id = f"{location_id}_state_{number}"
        while state_id in existing:
            number += 1
            state_id = f"{location_id}_state_{number}"
        states.append({
            "id": state_id,
            "description": str(description or "").strip(),
            "from_seconds": max(0.0, float(from_seconds)),
            "to_seconds": max(float(from_seconds), float(to_seconds)),
            "change_reason": "Manually added by the user.",
        })
        states.sort(key=lambda value: self._state_time_value(value.get("from_seconds") if isinstance(value, dict) else None, 0.0))
        self._rebind_location_state_references(location, existing)
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_location_state(state_id)
        return state_id

    def _delete_location_state(self) -> None:
        item = self.continuity_locations_tree.currentItem()
        reference = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(reference, dict) or reference.get("kind") != "location_state":
            return
        location = self._selected_location_record()
        state_id = str(reference.get("id") or "")
        if location is None or not state_id:
            return
        answer = QMessageBox.question(
            self,
            self.tr_text("video_storyboard_delete_location_state_title", "Delete location state?"),
            self.tr_text("video_storyboard_delete_location_state_confirmation", "Delete the selected location state? Scene references will be updated when possible."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        states = location.get("states", [])
        if not isinstance(states, list):
            return
        previous = {str(value.get("id") or "") for value in states if isinstance(value, dict)}
        location["states"] = [
            value for value in states
            if not isinstance(value, dict) or str(value.get("id") or "") != state_id
        ]
        self._rebind_location_state_references(location, previous)
        self._commit_continuity_changes()
        self._sync_continuity_trees()

    def _rebind_location_state_references(
        self, location: dict[str, Any], previous_state_ids: set[str]
    ) -> None:
        location_id = str(location.get("id") or "")
        name = str(location.get("name") or "")
        known = {value.casefold() for value in previous_state_ids | {location_id, name} if value}
        states = [value for value in location.get("states", []) if isinstance(value, dict) and value.get("id")]
        for scene in self._scenes:
            current = [str(value) for value in scene.locations]
            if not any(value.casefold() in known for value in current):
                continue
            retained = [value for value in current if value.casefold() not in known]
            moment = scene.start_seconds + scene.duration_seconds / 2.0
            active = [state for state in states if self._state_contains_time(state, moment)]
            if active:
                retained.append(str(max(active, key=lambda value: self._state_time_value(value.get("from_seconds"), 0.0)).get("id") or ""))
            scene.locations = [value for value in retained if value]
            if isinstance(scene.generation_overrides, dict):
                scene.generation_overrides["location_state_ids"] = list(scene.locations)

    def _new_character_state(self) -> None:
        character = self._selected_character_record()
        if character is None:
            return
        states = [
            state for state in character.get("states", [])
            if isinstance(state, dict)
        ]
        total = max(
            self._source_duration_seconds,
            max(
                (
                    scene.start_seconds + scene.duration_seconds
                    for scene in self._scenes
                ),
                default=0.0,
            ),
            1.0,
        )
        default_from = 0.0
        if states:
            try:
                default_from = float(states[-1].get("to_seconds") or 0.0)
            except (TypeError, ValueError):
                default_from = 0.0
            if default_from >= total:
                try:
                    default_from = float(states[-1].get("from_seconds") or 0.0)
                except (TypeError, ValueError):
                    default_from = 0.0

        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr_text("video_storyboard_new_character_state", "New state")
        )
        dialog.resize(760, 360)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        start_spin = QDoubleSpinBox()
        end_spin = QDoubleSpinBox()
        for spin in (start_spin, end_spin):
            spin.setRange(0.0, max(1_000_000.0, total))
            spin.setDecimals(3)
            spin.setSuffix(" s")
        start_spin.setValue(default_from)
        end_spin.setValue(max(default_from, total))
        description_edit = QPlainTextEdit()
        description_edit.setMinimumSize(540, 140)
        form.addRow(self.tr_text("video_storyboard_continuity_from", "From"), start_spin)
        form.addRow(self.tr_text("video_storyboard_continuity_to", "To"), end_spin)
        form.addRow(
            self.tr_text(
                "video_storyboard_continuity_definition",
                "Visual definition",
            ),
            description_edit,
        )
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if end_spin.value() < start_spin.value():
            QMessageBox.warning(
                self,
                self.tr_text("video_storyboard_invalid_state_time", "Invalid time range"),
                self.tr_text(
                    "video_storyboard_invalid_state_time_detail",
                    "The end time must be equal to or later than the start time.",
                ),
            )
            return
        self._add_character_state_record(
            character,
            start_spin.value(),
            end_spin.value(),
            description_edit.toPlainText().strip(),
        )

    def _add_character_state_record(
        self,
        character: dict[str, Any],
        from_seconds: float,
        to_seconds: float,
        description: str,
    ) -> str:
        states = character.setdefault("states", [])
        if not isinstance(states, list):
            states = []
            character["states"] = states
        character_id = str(character.get("id") or "character").strip()
        existing_ids = {
            str(state.get("id") or "")
            for state in states
            if isinstance(state, dict)
        }
        number = 1
        state_id = f"{character_id}_state_{number}"
        while state_id in existing_ids:
            number += 1
            state_id = f"{character_id}_state_{number}"
        previous_state_ids = set(existing_ids)
        states.append(
            {
                "id": state_id,
                "description": str(description or "").strip(),
                "from_seconds": max(0.0, float(from_seconds)),
                "to_seconds": max(float(from_seconds), float(to_seconds)),
                "change_reason": "Manually added by the user.",
            }
        )
        states.sort(key=lambda state: float(state.get("from_seconds") or 0.0))
        self._rebind_character_state_references(character, previous_state_ids)
        self._commit_continuity_changes()
        self._sync_continuity_trees()
        self._select_character_state(state_id)
        return state_id

    def _delete_character_state(self) -> None:
        item = self.continuity_characters_tree.currentItem()
        reference = (
            item.data(0, Qt.ItemDataRole.UserRole)
            if item is not None
            else None
        )
        if not isinstance(reference, dict) or reference.get("kind") != "character_state":
            return
        character = self._selected_character_record()
        state_id = str(reference.get("id") or "")
        if character is None or not state_id:
            return
        answer = QMessageBox.question(
            self,
            self.tr_text("video_storyboard_delete_state_title", "Delete character state?"),
            self.tr_text(
                "video_storyboard_delete_state_confirmation",
                "Delete the selected character state? Scene references will be updated to another active state when possible.",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        states = character.get("states", [])
        if not isinstance(states, list):
            return
        previous_state_ids = {
            str(state.get("id") or "")
            for state in states
            if isinstance(state, dict)
        }
        character["states"] = [
            state
            for state in states
            if not isinstance(state, dict) or str(state.get("id") or "") != state_id
        ]
        self._rebind_character_state_references(character, previous_state_ids)
        self._commit_continuity_changes()
        self._sync_continuity_trees()

    def _select_character_state(self, state_id: str) -> None:
        for index in range(self.continuity_characters_tree.topLevelItemCount()):
            parent = self.continuity_characters_tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                reference = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(reference, dict) and reference.get("id") == state_id:
                    self.continuity_characters_tree.setCurrentItem(child)
                    return

    def _rebind_character_state_references(
        self,
        character: dict[str, Any],
        previous_state_ids: set[str],
    ) -> None:
        character_id = str(character.get("id") or "")
        character_name = str(character.get("name") or "")
        known_references = {
            value.casefold()
            for value in previous_state_ids | {character_id, character_name}
            if value
        }
        states = [
            state for state in character.get("states", [])
            if isinstance(state, dict) and str(state.get("id") or "")
        ]
        state_lines = {
            line.split(":", 1)[0].strip(): line
            for line in self._continuity_character_lines(
                {"characters": [character]}
            )
        }
        for scene in self._scenes:
            references = [str(value) for value in scene.characters]
            if not any(value.casefold() in known_references for value in references):
                continue
            retained = [
                value
                for value in references
                if value.casefold() not in known_references
            ]
            moment = scene.start_seconds + scene.duration_seconds / 2.0
            active = [
                state for state in states
                if self._state_contains_time(state, moment)
            ]
            selected_id = ""
            if active:
                selected = max(
                    active,
                    key=lambda state: self._state_time_value(
                        state.get("from_seconds"), 0.0
                    ),
                )
                selected_id = str(selected.get("id") or "")
                retained.append(selected_id)
            scene.characters = retained
            overrides = scene.generation_overrides
            style = overrides.get("style", {}) if isinstance(overrides, dict) else {}
            lines = style.get("characters", []) if isinstance(style, dict) else []
            if isinstance(lines, list):
                kept_lines = [
                    str(line)
                    for line in lines
                    if str(line).split(":", 1)[0].strip().casefold()
                    not in known_references
                ]
                if selected_id and selected_id in state_lines:
                    kept_lines.append(state_lines[selected_id])
                style["characters"] = kept_lines

    @staticmethod
    def _state_time_value(value: object, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _state_contains_time(cls, state: dict[str, Any], moment: float) -> bool:
        return (
            cls._state_time_value(state.get("from_seconds"), float("-inf"))
            <= moment
            <= cls._state_time_value(state.get("to_seconds"), float("inf"))
        )

    def _commit_continuity_changes(self) -> None:
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(continuity, dict):
            return
        style = self._plan_metadata.setdefault("style", {})
        if isinstance(style, dict):
            lines = self._continuity_character_lines(continuity)
            style["characters"] = lines
            self.style_characters_edit.blockSignals(True)
            self.style_characters_edit.setPlainText("\n".join(lines))
            self.style_characters_edit.blockSignals(False)
        self._sync_prompt_markup_plan()
        self.projectChanged.emit(self.project_state())

    def _sync_prompt_markup_plan(self) -> None:
        if hasattr(self, "_prompt_highlighter"):
            self._prompt_highlighter.set_plan(self._plan_metadata)
        if hasattr(self, "_video_prompt_highlighter"):
            self._video_prompt_highlighter.set_plan(self._plan_metadata)

    def _continuity_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if self._updating_continuity_trees:
            return
        reference = item.data(0, Qt.ItemDataRole.UserRole)
        continuity = self._plan_metadata.get("continuity", {})
        if not isinstance(reference, dict) or not isinstance(continuity, dict):
            return
        kind = str(reference.get("kind") or "")
        if kind in {"character_state", "location_state"} and column in {1, 2}:
            value = self._parse_continuity_time(item.text(column))
            if value is None:
                self._sync_continuity_trees()
                return
            record = (
                self._selected_character_record()
                if kind == "character_state"
                else self._selected_location_record()
            )
            if record is None:
                self._sync_continuity_trees()
                return
            previous_state_ids = {
                str(state.get("id") or "")
                for state in record.get("states", [])
                if isinstance(state, dict)
            }
            for state in record.get("states", []):
                if not isinstance(state, dict) or state.get("id") != reference.get("id"):
                    continue
                if column == 1:
                    state["from_seconds"] = min(
                        value,
                        self._state_time_value(state.get("to_seconds"), value),
                    )
                else:
                    state["to_seconds"] = max(
                        value,
                        self._state_time_value(state.get("from_seconds"), value),
                    )
                break
            record["states"].sort(
                key=lambda state: self._state_time_value(
                    state.get("from_seconds") if isinstance(state, dict) else None,
                    0.0,
                )
            )
            if kind == "character_state":
                self._rebind_character_state_references(record, previous_state_ids)
            else:
                self._rebind_location_state_references(record, previous_state_ids)
            self._commit_continuity_changes()
            self._sync_continuity_trees()
            if kind == "character_state":
                self._select_character_state(str(reference.get("id") or ""))
            else:
                self._select_location_state(str(reference.get("id") or ""))
            return
        if column != 3:
            self._sync_continuity_trees()
            return
        collection = "locations" if kind.startswith("location") else "characters"
        if kind == "era":
            for era in continuity.get("eras", []):
                if isinstance(era, dict) and era.get("id") == reference.get("id"):
                    era["material_culture"] = item.text(3).strip()
                    break
        else:
            for record in continuity.get(collection, []):
                if not isinstance(record, dict):
                    continue
                record_id = reference.get("parent_id") or reference.get("id")
                if record.get("id") != record_id:
                    continue
                if kind in {"character", "location"}:
                    record["identity_description"] = item.text(3).strip()
                else:
                    for state in record.get("states", []):
                        if isinstance(state, dict) and state.get("id") == reference.get("id"):
                            state["description"] = item.text(3).strip()
                            break
                break
        self._commit_continuity_changes()

    @staticmethod
    def _continuity_character_lines(continuity: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for character in continuity.get("characters", []):
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            identity = str(character.get("identity_description") or "").strip()
            for state in character.get("states", []):
                if not isinstance(state, dict):
                    continue
                state_id = str(state.get("id") or "").strip()
                description = ", ".join(
                    value for value in (
                        name,
                        identity,
                        str(state.get("description") or "").strip(),
                    ) if value
                )
                if state_id and description:
                    lines.append(f"{state_id}: {description}")
        return lines

    def _project_controls_changed(self, *_args: object) -> None:
        if self._updating_project_controls:
            return
        overrides = self.project_overrides()
        self._plan_metadata["base_seed"] = overrides["seed"]
        self._plan_metadata["style_mode"] = overrides["style_mode"]
        self._plan_metadata["style"] = overrides["style"]
        self._plan_metadata["narrative_context"] = overrides["narrative"]
        self._plan_metadata["video_overrides"] = overrides["video"]
        if overrides["narrative"].get("era_source") == "user":
            self._apply_user_era_override(
                str(overrides["narrative"].get("era") or "")
            )
        self.projectChanged.emit(self.project_state())

    def _apply_user_era_override(self, era: str) -> None:
        continuity = self._plan_metadata.get("continuity")
        if not isinstance(continuity, dict):
            return
        description = str(era or "").strip()
        continuity["era_locked"] = bool(description)
        assignments = continuity.get("assignments", [])
        if not description:
            continuity["eras"] = []
            if isinstance(assignments, list):
                for assignment in assignments:
                    if isinstance(assignment, dict):
                        assignment["era_id"] = ""
            for scene in self._scenes:
                scene.era = ""
                scene.era_state_id = ""
            self._sync_continuity_trees()
            return
        duration = max(
            self._source_duration_seconds,
            sum(scene.duration_seconds for scene in self._scenes),
        )
        period_id = "era_user_override"
        continuity["eras"] = [
            {
                "id": period_id,
                "description": description,
                "material_culture": "",
                "from_seconds": 0.0,
                "to_seconds": duration,
                "source_unit_start": 0,
                "reason": "user_override",
                "evidence": "",
            }
        ]
        if isinstance(assignments, list):
            for assignment in assignments:
                if isinstance(assignment, dict):
                    assignment["era_id"] = period_id
        for scene in self._scenes:
            scene.era = description
            scene.era_state_id = period_id
        self._sync_continuity_trees()

    def _style_gallery_item(self, identifier: object) -> QListWidgetItem | None:
        normalized = normalize_storyboard_style_id(identifier)
        for index in range(self.style_gallery.count()):
            item = self.style_gallery.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == normalized:
                return item
        return self.style_gallery.item(0)

    def _center_selected_style(self) -> None:
        selected_item = self.style_gallery.currentItem()
        if selected_item is not None:
            self.style_gallery.scrollToItem(
                selected_item,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        QTimer.singleShot(0, self._center_selected_style)

    def _style_gallery_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if self._updating_project_controls or current is None:
            return
        previous_mode = normalize_storyboard_style_id(
            self._plan_metadata.get("style_mode")
        )
        selected = normalize_storyboard_style_id(
            current.data(Qt.ItemDataRole.UserRole)
        )
        selected_style = storyboard_style(selected)
        self.style_medium_edit.blockSignals(True)
        try:
            if selected_style is not None:
                self.style_medium_edit.setText(selected_style.prompt)
            elif previous_mode != "custom":
                self.style_medium_edit.clear()
        finally:
            self.style_medium_edit.blockSignals(False)
        self._project_controls_changed()

    def _play_preview(self) -> None:
        if not self._preview_available():
            return
        duration = self.preview_player.duration()
        if duration > 0 and self.preview_player.position() >= duration - 20:
            self.preview_player.setPosition(0)
        self.preview_player.play()
        seconds = self.preview_player.position() / 1000.0
        self._sync_scene_video_for_preview(
            self._scene_at_seconds(seconds),
            seconds,
        )

    def _seek_preview(self, seconds: float) -> None:
        target_seconds = max(
            0.0,
            min(float(seconds), self.timeline_canvas.total_seconds),
        )
        target_ms = round(target_seconds * 1000)
        if self._preview_available():
            media_duration = self.preview_player.duration()
            if media_duration > 0:
                target_ms = min(target_ms, media_duration)
            self.preview_player.setPosition(target_ms)
        self._on_preview_position_changed(target_ms)
        self._sync_preview_controls()

    def _pause_preview(self) -> None:
        self.preview_player.pause()
        if hasattr(self, "scene_video_player"):
            self.scene_video_player.pause()

    def stop_preview(self) -> None:
        if not hasattr(self, "preview_player"):
            return
        self.preview_player.stop()
        self.preview_player.setPosition(0)
        self._stop_scene_video()
        self.timeline_canvas.set_playhead(None)
        self._update_preview_time()

    def _preview_available(self) -> bool:
        return bool(
            self._scenes
            and self._audio_path
            and Path(self._audio_path).is_file()
        )

    def _on_preview_position_changed(self, position_ms: int) -> None:
        seconds = max(0.0, position_ms / 1000.0)
        self.timeline_canvas.set_playhead(seconds)
        active = self._scene_at_seconds(seconds)
        if active is not None and active.scene_id != self._selected_scene_id:
            self._select_scene(active.scene_id)
        self._sync_scene_video_for_preview(active, seconds)
        x = round(seconds * self.timeline_canvas.pixels_per_second)
        self.timeline_scroll.ensureVisible(
            x,
            self.timeline_canvas.VIDEO_TOP,
            max(60, self.timeline_scroll.viewport().width() // 4),
            0,
        )
        self._update_preview_time(position_ms)

    def _scene_at_seconds(self, seconds: float) -> StoryboardScene | None:
        return next(
            (
                scene
                for scene in self._scenes
                if scene.start_seconds
                <= seconds
                < scene.start_seconds + scene.duration_seconds
            ),
            (
                self._scenes[-1]
                if self._scenes and seconds >= self.timeline_canvas.total_seconds
                else None
            ),
        )

    def _load_scene_video(
        self,
        scene: StoryboardScene,
        *,
        autoplay: bool,
    ) -> None:
        path = str(scene.video_path or "")
        if not path or not Path(path).is_file():
            self._clear_scene_video_source()
            return
        if path == self._loaded_scene_video_path:
            return
        self.scene_video_player.stop()
        self._loaded_scene_video_path = path
        self._scene_video_pause_on_first_frame = not autoplay
        self.scene_video_player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        QTimer.singleShot(0, self.scene_video_player.play)

    def _clear_scene_video_source(self) -> None:
        if not hasattr(self, "scene_video_player"):
            return
        self._scene_video_pause_on_first_frame = False
        self.scene_video_player.stop()
        if self._loaded_scene_video_path:
            self.scene_video_player.setSource(QUrl())
        self._loaded_scene_video_path = ""
        self._sync_scene_video_controls()

    def _play_scene_video(self) -> None:
        scene = self.selected_scene()
        if scene is None or not scene.video_path:
            return
        self._load_scene_video(scene, autoplay=True)
        duration = self.scene_video_player.duration()
        if duration > 0 and self.scene_video_player.position() >= duration - 20:
            self.scene_video_player.setPosition(0)
        self._set_scene_video_playback_rate(scene)
        self._scene_video_pause_on_first_frame = False
        self.scene_video_player.play()

    def _pause_scene_video(self) -> None:
        if hasattr(self, "scene_video_player"):
            self.scene_video_player.pause()

    def _stop_scene_video(self) -> None:
        if not hasattr(self, "scene_video_player"):
            return
        scene = self.selected_scene()
        if scene is None or not scene.video_path:
            self.scene_video_player.stop()
            return
        self.scene_video_player.pause()
        self.scene_video_player.setPosition(0)
        self._scene_video_pause_on_first_frame = True
        QTimer.singleShot(0, self.scene_video_player.play)

    def _set_scene_video_playback_rate(self, scene: StoryboardScene) -> None:
        media_duration = self.scene_video_player.duration() / 1000.0
        if media_duration <= 0:
            return
        self.scene_video_player.setPlaybackRate(
            max(0.05, media_duration / max(0.1, scene.duration_seconds))
        )

    def _sync_scene_video_for_preview(
        self,
        scene: StoryboardScene | None,
        timeline_seconds: float,
    ) -> None:
        if scene is None or not scene.video_path or not Path(scene.video_path).is_file():
            self._clear_scene_video_source()
            return
        playing = (
            self.preview_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        self._load_scene_video(scene, autoplay=playing)
        media_duration = self.scene_video_player.duration()
        if media_duration > 0:
            relative = max(
                0.0,
                min(
                    scene.duration_seconds,
                    timeline_seconds - scene.start_seconds,
                ),
            )
            target = round(
                relative / max(0.1, scene.duration_seconds) * media_duration
            )
            if abs(self.scene_video_player.position() - target) > 180:
                self.scene_video_player.setPosition(target)
            self._set_scene_video_playback_rate(scene)
        if playing:
            self._scene_video_pause_on_first_frame = False
            self.scene_video_player.play()
        else:
            self.scene_video_player.pause()

    def _on_scene_video_frame_changed(self, frame: Any) -> None:
        if not frame.isValid() or not self._scene_video_pause_on_first_frame:
            return
        self._scene_video_pause_on_first_frame = False
        QTimer.singleShot(0, self.scene_video_player.pause)

    def _on_scene_video_media_status_changed(
        self,
        status: QMediaPlayer.MediaStatus,
    ) -> None:
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._loaded_scene_video_path
            and self.preview_player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            # QVideoWidget clears its surface at EOF on Windows.  Keep a
            # useful cover frame in Selected scene after manual playback.
            QTimer.singleShot(0, self._prime_scene_video_cover)

    def _prime_scene_video_cover(self) -> None:
        if not self._loaded_scene_video_path:
            return
        self.scene_video_player.pause()
        self.scene_video_player.setPosition(0)
        self._scene_video_pause_on_first_frame = True
        QTimer.singleShot(0, self.scene_video_player.play)

    def _on_scene_video_duration_changed(self, _duration: int) -> None:
        scene = self.selected_scene()
        if scene is None:
            return
        self._set_scene_video_playback_rate(scene)
        if (
            self.preview_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._sync_scene_video_for_preview(
                scene,
                self.preview_player.position() / 1000.0,
            )

    def _on_scene_video_error(
        self,
        _error: QMediaPlayer.Error,
        message: str,
    ) -> None:
        if message:
            self.append_activity(
                self.tr_text(
                    "video_storyboard_preview_error",
                    "Storyboard preview failed: {error}",
                    error=message,
                )
            )
        self._sync_scene_video_controls()

    def _sync_scene_video_controls(self, *_args: object) -> None:
        scene = self.selected_scene()
        available = bool(
            scene is not None
            and scene.video_path
            and Path(scene.video_path).is_file()
        )
        playing = bool(
            hasattr(self, "scene_video_player")
            and self.scene_video_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        self.scene_video_play_button.setEnabled(available and not playing)
        self.scene_video_pause_button.setEnabled(available and playing)
        self.scene_video_stop_button.setEnabled(available)

    def _update_preview_time(self, position_ms: int | None = None) -> None:
        if position_ms is None:
            position_ms = (
                self.preview_player.position()
                if hasattr(self, "preview_player")
                else 0
            )
        media_duration = (
            self.preview_player.duration()
            if hasattr(self, "preview_player")
            else 0
        )
        duration_seconds = max(
            self._source_duration_seconds,
            media_duration / 1000.0,
            self.timeline_canvas.total_seconds,
        )
        self.preview_time_label.setText(
            f"{StoryboardTimelineCanvas._format_time(position_ms / 1000.0)} / "
            f"{StoryboardTimelineCanvas._format_time(duration_seconds)}"
        )

    def _sync_preview_controls(self, *_args: object) -> None:
        available = self._preview_available()
        playing = (
            hasattr(self, "preview_player")
            and self.preview_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        self.preview_play_button.setEnabled(available and not playing)
        self.preview_pause_button.setEnabled(available and playing)
        self.preview_stop_button.setEnabled(
            available
            and (
                playing
                or self.preview_player.position() > 0
                or self.preview_player.playbackState()
                == QMediaPlayer.PlaybackState.PausedState
            )
        )

    def _on_preview_error(
        self,
        _error: QMediaPlayer.Error,
        message: str,
    ) -> None:
        if not message:
            return
        self.append_activity(
            self.tr_text(
                "video_storyboard_preview_error",
                "Audiobook preview failed: {error}",
                error=message,
            )
        )
        self._sync_preview_controls()

    def _zoom_in(self) -> None:
        self.timeline_canvas.zoom_in()
        self._center_timeline_on_playhead()
        self._sync_zoom_controls()

    def _zoom_out(self) -> None:
        self.timeline_canvas.zoom_out()
        self._center_timeline_on_playhead()
        self._sync_zoom_controls()

    def _center_timeline_on_playhead(self) -> None:
        self.timeline_canvas._text_hover.hide()
        seconds = self.timeline_canvas.playhead_seconds
        if seconds is None:
            seconds = self.preview_player.position() / 1000.0
        scrollbar = self.timeline_scroll.horizontalScrollBar()
        target = round(seconds * self.timeline_canvas.pixels_per_second - self.timeline_scroll.viewport().width() / 2)
        scrollbar.setValue(target)

    def _sync_zoom_controls(self) -> None:
        index = self.timeline_canvas.zoom_index
        self.zoom_out_button.setEnabled(index > 0)
        self.zoom_in_button.setEnabled(
            index < len(self.timeline_canvas.ZOOM_LEVELS) - 1
        )
        base = self.timeline_canvas.ZOOM_LEVELS[2]
        self.zoom_label.setText(
            f"{round(self.timeline_canvas.pixels_per_second / base * 100)}%"
        )

    def _recalculate_starts(self) -> None:
        cursor = 0.0
        for scene in self._scenes:
            scene.start_seconds = cursor
            cursor += scene.duration_seconds

    def _adjust_first_scene_for_offset(self, delta_seconds: float) -> None:
        if not self._scenes or abs(delta_seconds) <= 0.0001:
            return
        self._scenes[0].duration_seconds = max(
            1.0,
            self._scenes[0].duration_seconds + delta_seconds,
        )
        self._plan_metadata["voice_start_offset_seconds"] = (
            self._voice_start_offset_seconds
        )
        self._recalculate_starts()
        self.timeline_canvas.set_scenes(self._scenes)
        self.scenesChanged.emit(self.scenes())

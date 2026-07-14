from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import wave
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, QThread, QTimer, Qt
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGraphicsDropShadowEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QHeaderView,
    QInputDialog,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.core.audio_pipeline import AudioGenerationOptions
from app.core.audio_mix import AudioMixSettings
from app.core.audiobook_store import AudiobookStore, StoredSegment
from app.core.project_manager import DocumentImportError, ProjectManager
from app.core.settings_manager import SettingsManager
from app.server.engine_host_client import EngineHostClient
from app.server.server_controller import LocalServerController
from app.tts.base import BaseTTSEngine, TTSEngineError
from app.tts.engine_registry import TTS_ENGINES
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.chatterbox_voice_manager import (
    ChatterboxReferenceVoice,
    ChatterboxReferenceVoiceManager,
)
from app.tts.kokoro_preview import kokoro_preview_text_for_language
from app.tts.kokoro_python_manager import KokoroPythonManager
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.qwen_manager import QwenManager
from app.tts.piper_engine import PiperTTSEngine
from app.tts.voice_gallery_manager import (
    DEFAULT_GALLERY_CATALOG_URL,
    GalleryVoice,
    VoiceGalleryManager,
)
from app.tts.voice_manager import VoiceInfo, VoiceManager
from app.utils.i18n import Translator
from app.utils.paths import application_root, resolve_app_path, resource_root
from app.utils.gpu_detection import detect_gpus, format_gpu_detection
from app.workers.chatterbox_worker import (
    ChatterboxHardwareWorker,
    ChatterboxInstallWorker,
    ChatterboxPreviewWorker,
)
from app.workers.chatterbox_voice_worker import ChatterboxVoiceWorker
from app.workers.engine_host_generation_worker import EngineHostGenerationWorker
from app.workers.engine_host_memory_worker import EngineHostMemoryWorker
from app.workers.kokoro_worker import KokoroInstallWorker, KokoroPreviewWorker
from app.workers.omnivoice_worker import (
    OmniVoiceHardwareWorker,
    OmniVoiceInstallWorker,
    OmniVoicePreviewWorker,
)
from app.workers.qwen_worker import (
    QwenHardwareWorker,
    QwenInstallWorker,
    QwenPreviewWorker,
)
from app.workers.verification_worker import (
    AudiobookRebuildWorker,
    FasterWhisperInstallWorker,
    FasterWhisperPreloadWorker,
    SegmentRegenerationWorker,
    SegmentVerificationWorker,
)
from app.workers.voice_catalog_worker import VoiceGalleryWorker
from app.verification.faster_whisper_manager import (
    FasterWhisperManager,
    FasterWhisperVerifier,
)
from mutagen import File as MutagenFile

from .audio_mix_preview_panel import AudioMixPreviewContext, AudioMixPreviewPanel
from .icons import ICON_LIGHT, ui_icon
from .markup_highlighter import LTVMarkupHighlighter
from .voice_manager_dialog import VoiceManagerDialog
from .widgets import FilePicker, LogView, PathPicker


class WindowResizeHandle(QWidget):
    def __init__(
        self,
        main_window: "MainWindow",
        edges: Qt.Edge,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.main_window = main_window
        self.edges = edges
        self._manual_start_pos: QPoint | None = None
        self._manual_start_geometry = None
        self.setObjectName("windowResizeHandle")
        self.setMouseTracking(True)
        self.setCursor(self._cursor_for_edges(edges))
        self.setStyleSheet(
            "QWidget#windowResizeHandle { background: rgba(255, 255, 255, 1); }"
        )

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (top and left) or (bottom and right):
            return Qt.CursorShape.SizeFDiagCursor
        if (top and right) or (bottom and left):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton or self.main_window.isMaximized():
            super().mousePressEvent(event)
            return

        window_handle = self.main_window.windowHandle()
        if window_handle is not None and window_handle.startSystemResize(self.edges):
            event.accept()
            return

        self._manual_start_pos = event.globalPosition().toPoint()
        self._manual_start_geometry = self.main_window.geometry()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._manual_start_pos is None or self._manual_start_geometry is None:
            super().mouseMoveEvent(event)
            return

        delta = event.globalPosition().toPoint() - self._manual_start_pos
        geometry = self._manual_start_geometry
        new_geometry = geometry.__class__(geometry)
        minimum_width = self.main_window.minimumWidth()
        minimum_height = self.main_window.minimumHeight()

        if self.edges & Qt.Edge.LeftEdge:
            new_geometry.setLeft(
                min(geometry.right() - minimum_width + 1, geometry.left() + delta.x())
            )
        if self.edges & Qt.Edge.RightEdge:
            new_geometry.setRight(
                max(geometry.left() + minimum_width - 1, geometry.right() + delta.x())
            )
        if self.edges & Qt.Edge.TopEdge:
            new_geometry.setTop(
                min(geometry.bottom() - minimum_height + 1, geometry.top() + delta.y())
            )
        if self.edges & Qt.Edge.BottomEdge:
            new_geometry.setBottom(
                max(geometry.top() + minimum_height - 1, geometry.bottom() + delta.y())
            )

        self.main_window.setGeometry(new_geometry)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._manual_start_pos = None
        self._manual_start_geometry = None
        super().mouseReleaseEvent(event)


class WindowTitleBar(QFrame):
    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self._drag_offset: QPoint | None = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(38)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.main_window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            if self.main_window.isMaximized():
                self.main_window.showNormal()
                self.main_window._update_window_button_state()
            self.main_window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.main_window._toggle_maximized_window()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class WindowControlButton(QPushButton):
    _CUSTOM_GLYPH_NAMES = {"window_minimize", "window_maximize", "window_restore", "close"}

    def __init__(
        self,
        icon_name: str,
        *,
        hover_icon_name: str | None = None,
        normal_color: str = "#475569",
        hover_color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.hover_icon_name = hover_icon_name or icon_name
        self.normal_color = normal_color
        self.hover_color = hover_color or normal_color
        self._hovered = False
        self._refresh_icon()

    def _uses_custom_glyph(self) -> bool:
        return self.icon_name in self._CUSTOM_GLYPH_NAMES

    def _refresh_icon(self) -> None:
        if self._uses_custom_glyph():
            self.setIcon(QIcon())
            self.update()
            return
        icon_name = self.hover_icon_name if self._hovered else self.icon_name
        icon_color = self.hover_color if self._hovered else self.normal_color
        self.setIcon(ui_icon(icon_name, color=icon_color))

    def _active_color(self) -> str:
        return self.hover_color if self._hovered else self.normal_color

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._uses_custom_glyph():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(self._active_color()))
        pen.setWidthF(1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        center_x = self.width() // 2
        center_y = self.height() // 2

        if self.icon_name == "window_minimize":
            line_half = 5
            y = center_y + 3
            painter.drawLine(center_x - line_half, y, center_x + line_half, y)
        elif self.icon_name == "window_maximize":
            side = 11
            painter.drawRect(center_x - side // 2, center_y - side // 2 + 1, side, side)
        elif self.icon_name == "window_restore":
            side = 9
            painter.drawRect(center_x - side // 2 + 2, center_y - side // 2 - 1, side, side)
            painter.drawRect(center_x - side // 2 - 2, center_y - side // 2 + 3, side, side)
        elif self.icon_name == "close":
            line_half = 5
            painter.drawLine(
                center_x - line_half,
                center_y - line_half,
                center_x + line_half,
                center_y + line_half,
            )
            painter.drawLine(
                center_x + line_half,
                center_y - line_half,
                center_x - line_half,
                center_y + line_half,
            )

        painter.end()

    def set_icon_name(self, icon_name: str) -> None:
        self.icon_name = icon_name
        self.hover_icon_name = icon_name
        self._refresh_icon()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)


MARKUP_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "name": "pause",
        "label": "Pause",
        "template": "{{pause }}",
        "cursor": "{{pause ",
        "color": "#92400e",
        "background": "#fff7ed",
        "help": "Adds a silence pause. Examples: {{pause 700ms}}, {{pause 0.7s}}, {{pause.long}}, {{pause random 500 1200}}.",
    },
    {
        "name": "voice",
        "label": "Voice",
        "template": "{{voice \"\"}}",
        "cursor": "{{voice \"",
        "color": "#6d28d9",
        "background": "#f5f3ff",
        "help": "Changes the voice for following text. The name can be partial; the closest voice in the selected engine is used. Examples: {{voice \"Serena - Spanish\"}}, {{voice \"ser spa\"}}, {{voice \"edu\"}}.",
    },
    {
        "name": "lang",
        "label": "Lang",
        "template": "{{lang }}",
        "cursor": "{{lang ",
        "color": "#0f766e",
        "background": "#ecfdf5",
        "help": "Sets the language for following text. Example: {{lang es}}.",
    },
    {
        "name": "speed",
        "label": "Speed",
        "template": "{{speed }}",
        "cursor": "{{speed ",
        "color": "#7c2d12",
        "background": "#fffbeb",
        "help": "Changes speaking speed with FFmpeg postprocessing, so it works even if the TTS engine does not support speed. Examples: {{speed 0.9}}, {{speed.slow}}, {{speed.fast}}.",
    },
    {
        "name": "volume",
        "label": "Volume",
        "template": "{{volume }}",
        "cursor": "{{volume ",
        "color": "#0369a1",
        "background": "#eff6ff",
        "help": "Changes voice volume with FFmpeg postprocessing. Use a multiplier, percent, dB, or LUFS normalization. Examples: {{volume 0.8}}, {{volume 80%}}, {{volume -3db}}, {{volume.normalize -16}}.",
    },
    {
        "name": "chapter",
        "label": "Chapter",
        "template": "{{chapter \"\"}}",
        "cursor": "{{chapter \"",
        "color": "#2563eb",
        "background": "#eff6ff",
        "help": "Starts a new chapter/section. Example: {{chapter \"Lesson 1\"}}.",
    },
    {
        "name": "cmd",
        "label": "Model Cmd",
        "template": "{{cmd\n\"instruct\": \"\"}}",
        "cursor": "{{cmd\n\"instruct\": \"",
        "color": "#c2410c",
        "background": "#fff7ed",
        "help": "Applies TTS parameters only to the next segment. Example: {{cmd \"instruct\": \"Warm tone\", \"temperature\": 0.7, \"top_p\": 0.9}}.",
    },
    {
        "name": "preset",
        "label": "Preset",
        "template": "{{preset\n\"instruct\": \"\"}}",
        "cursor": "{{preset\n\"instruct\": \"",
        "color": "#b45309",
        "background": "#fffbeb",
        "help": "Applies TTS parameters to every following segment until {{reset.preset}}. One-shot {{cmd}} can override it for one segment.",
    },
    {
        "name": "reset",
        "label": "Reset",
        "template": "{{reset}}",
        "cursor": "{{reset}}",
        "color": "#64748b",
        "background": "#f8fafc",
        "help": "Resets voice, language, speed and other markup state. Use {{reset.preset}} to clear persistent TTS parameters.",
    },
)


class ReferenceVoiceImportDialog(QDialog):
    def __init__(self, source: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("Import reference voice")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel("Import reference voice")
        title.setObjectName("sectionLabel")
        helper = QLabel(
            "WAV/MP3 files will be normalized to WAV, mono, 24 kHz. "
            "Use a clean voice-only clip between 3 and 20 seconds."
        )
        helper.setObjectName("helperLabel")
        helper.setWordWrap(True)
        source_label = QLabel(str(source))
        source_label.setObjectName("helperLabel")
        source_label.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(source_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_edit = QLineEdit(source.stem)
        self.language_combo = QComboBox()
        for label, code in (
            ("Reference / Auto", "reference"),
            ("English", "en"),
            ("Spanish", "es"),
            ("French", "fr"),
            ("German", "de"),
            ("Italian", "it"),
            ("Portuguese", "pt"),
            ("Japanese", "ja"),
            ("Chinese", "zh"),
            ("Hindi", "hi"),
        ):
            self.language_combo.addItem(label, code)
        self.short_description_edit = QLineEdit()
        self.short_description_edit.setPlaceholderText(
            "Warm narrator, energetic promo, calm teacher..."
        )
        self.gender_combo = QComboBox()
        for label, value in (
            ("Not specified", ""),
            ("Female", "female"),
            ("Male", "male"),
            ("Neutral", "neutral"),
        ):
            self.gender_combo.addItem(label, value)
        self.age_style_combo = QComboBox()
        for label, value in (
            ("Not specified", ""),
            ("Child", "child"),
            ("Young adult", "young_adult"),
            ("Middle aged", "middle_aged"),
            ("Mature", "mature"),
            ("Elderly", "elderly"),
        ):
            self.age_style_combo.addItem(label, value)
        self.voice_style_edit = QLineEdit()
        self.voice_style_edit.setPlaceholderText(
            "storyteller, documentary, character, podcast..."
        )
        self.reference_text_edit = QTextEdit()
        self.reference_text_edit.setMaximumHeight(90)
        self.reference_text_edit.setPlaceholderText(
            "Transcript of the reference audio. Recommended for better cloning."
        )
        form.addRow("Voice name", self.name_edit)
        form.addRow("Language", self.language_combo)
        form.addRow("Short description", self.short_description_edit)
        form.addRow("Gender", self.gender_combo)
        form.addRow("Age style", self.age_style_combo)
        form.addRow("Voice style", self.voice_style_edit)
        form.addRow("Reference transcript", self.reference_text_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancel")
        import_button = QPushButton("Import voice")
        import_button.setIcon(ui_icon("save"))
        cancel_button.clicked.connect(self.reject)
        import_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(import_button)
        layout.addLayout(buttons)

    def values(self) -> dict[str, object]:
        language_code = str(self.language_combo.currentData() or "reference")
        language_name = self.language_combo.currentText()
        if language_code == "reference":
            language_name = "Reference"
        gender = str(self.gender_combo.currentData() or "")
        age_style = str(self.age_style_combo.currentData() or "")
        voice_style = self.voice_style_edit.text().strip()
        tags = ["imported", "reference"]
        for value in (language_code, gender, age_style, voice_style):
            if value and value != "reference":
                tags.append(value)
        return {
            "name": self.name_edit.text().strip() or self.source.stem,
            "language": language_code,
            "language_name": language_name,
            "ref_text": self.reference_text_edit.toPlainText().strip(),
            "short_description": self.short_description_edit.text().strip(),
            "gender": gender,
            "age_style": age_style,
            "voice_style": voice_style,
            "tags": tags,
        }


class OmniVoiceDesignDialog(QDialog):
    VALID_GENDERS = {"male", "female"}
    VALID_AGES = {"child", "teenager", "young adult", "middle-aged", "elderly"}
    VALID_PITCHES = {
        "very low pitch",
        "low pitch",
        "moderate pitch",
        "high pitch",
        "very high pitch",
    }
    VALID_ACCENTS = {
        "american accent",
        "british accent",
        "australian accent",
        "chinese accent",
        "canadian accent",
        "indian accent",
        "korean accent",
        "portuguese accent",
        "russian accent",
        "japanese accent",
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        translate: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._translate = translate or (lambda _key, default: default)
        self.preview_path: Path | None = None
        self.setWindowTitle(self.tr_text("omnivoice_design_title", "OmniVoice Voice Designer"))
        self.setMinimumSize(860, 620)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        title = QLabel(self.tr_text("omnivoice_design_title", "OmniVoice Voice Designer"))
        title.setObjectName("sectionLabel")
        subtitle = QLabel(
            self.tr_text(
                "omnivoice_design_subtitle",
                "Create a synthetic reference voice using only OmniVoice supported parameters.",
            )
        )
        subtitle.setObjectName("helperLabel")
        subtitle.setWordWrap(True)
        left_layout.addWidget(title)
        left_layout.addWidget(subtitle)

        form = QFormLayout()
        self.name_edit = QLineEdit(
            self.tr_text("omnivoice_design_default_name", "New designed voice")
        )
        self.language_combo = QComboBox()
        for label, value in (
            (self.tr_text("language_english", "English"), "English"),
            (self.tr_text("language_spanish", "Spanish"), "Spanish"),
            (self.tr_text("language_french", "French"), "French"),
            (self.tr_text("language_german", "German"), "German"),
            (self.tr_text("language_italian", "Italian"), "Italian"),
            (self.tr_text("language_portuguese", "Portuguese"), "Portuguese"),
            (self.tr_text("auto", "Auto"), "auto"),
        ):
            self.language_combo.addItem(label, value)

        self.gender_combo = QComboBox()
        for label, value in (
            (self.tr_text("none_option", "None"), ""),
            (self.tr_text("omnivoice_gender_female", "Female"), "female"),
            (self.tr_text("omnivoice_gender_male", "Male"), "male"),
        ):
            self.gender_combo.addItem(label, value)
        self.gender_combo.setCurrentIndex(1)

        self.age_combo = QComboBox()
        for label, value in (
            (self.tr_text("none_option", "None"), ""),
            (self.tr_text("omnivoice_age_child", "Child"), "child"),
            (self.tr_text("omnivoice_age_teenager", "Teenager"), "teenager"),
            (self.tr_text("omnivoice_age_young_adult", "Young adult"), "young adult"),
            (self.tr_text("omnivoice_age_middle_aged", "Middle-aged"), "middle-aged"),
            (self.tr_text("omnivoice_age_elderly", "Elderly"), "elderly"),
        ):
            self.age_combo.addItem(label, value)
        self.age_combo.setCurrentIndex(3)

        self.pitch_combo = QComboBox()
        for label, value in (
            (self.tr_text("none_option", "None"), ""),
            (self.tr_text("omnivoice_pitch_very_low", "Very low pitch"), "very low pitch"),
            (self.tr_text("omnivoice_pitch_low", "Low pitch"), "low pitch"),
            (self.tr_text("omnivoice_pitch_moderate", "Moderate pitch"), "moderate pitch"),
            (self.tr_text("omnivoice_pitch_high", "High pitch"), "high pitch"),
            (self.tr_text("omnivoice_pitch_very_high", "Very high pitch"), "very high pitch"),
        ):
            self.pitch_combo.addItem(label, value)
        self.pitch_combo.setCurrentIndex(3)

        self.whisper_checkbox = QCheckBox(
            self.tr_text("omnivoice_style_whisper", "Whisper")
        )

        self.accent_combo = QComboBox()
        self.accent_combo.addItem(
            self.tr_text("omnivoice_no_accent", "No accent instruction"),
            "",
        )
        for accent in sorted(self.VALID_ACCENTS):
            self.accent_combo.addItem(
                self.tr_text(
                    f"omnivoice_accent_{accent.replace(' ', '_')}",
                    accent.title(),
                ),
                accent,
            )

        form.addRow(self.tr_text("name", "Name"), self.name_edit)
        form.addRow(self.tr_text("language", "Language"), self.language_combo)
        form.addRow(self.tr_text("omnivoice_gender", "Gender"), self.gender_combo)
        form.addRow(self.tr_text("omnivoice_age", "Age"), self.age_combo)
        form.addRow(self.tr_text("omnivoice_pitch", "Pitch"), self.pitch_combo)
        form.addRow(self.tr_text("omnivoice_style", "Style"), self.whisper_checkbox)
        form.addRow(self.tr_text("omnivoice_accent", "Accent"), self.accent_combo)
        left_layout.addLayout(form)

        valid_help = QLabel(
            self.tr_text(
                "omnivoice_design_help",
                "OmniVoice accepts one value per category: gender, age, pitch, optional whisper, and optional English accent.",
            )
        )
        valid_help.setObjectName("helperLabel")
        valid_help.setWordWrap(True)
        left_layout.addWidget(valid_help)

        self.instruct_preview_label = QLabel("")
        self.instruct_preview_label.setObjectName("helperLabel")
        self.instruct_preview_label.setWordWrap(True)
        left_layout.addWidget(self.instruct_preview_label)
        left_layout.addStretch(1)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        preview_title = QLabel(self.tr_text("preview", "Preview"))
        preview_title.setObjectName("sectionLabel")
        self.sample_text_edit = QTextEdit()
        self.sample_text_edit.setPlainText(
            self.tr_text(
                "omnivoice_design_sample_text",
                "The sun dipped below the horizon, painting the sky in shades of gold and violet as the quiet village came to life.",
            )
        )
        self.sample_text_edit.setMinimumHeight(140)
        self.status_label = QLabel(
            self.tr_text(
                "omnivoice_design_status_ready",
                "Choose parameters and generate a preview.",
            )
        )
        self.status_label.setObjectName("helperLabel")
        self.status_label.setWordWrap(True)
        self.generate_button = QPushButton(
            self.tr_text("generate_preview", "Generate Preview")
        )
        self.generate_button.setIcon(ui_icon("preview"))
        self.play_button = QPushButton(self.tr_text("play_preview", "Play Preview"))
        self.play_button.setIcon(ui_icon("play"))
        self.play_button.setEnabled(False)
        self.save_button = QPushButton(self.tr_text("save_as_voice", "Save as Voice"))
        self.save_button.setIcon(ui_icon("save"))
        self.save_button.setEnabled(False)
        close_button = QPushButton(self.tr_text("close", "Close"))
        close_button.clicked.connect(self.reject)
        right_layout.addWidget(preview_title)
        right_layout.addWidget(QLabel(self.tr_text("sample_text", "Sample text")))
        right_layout.addWidget(self.sample_text_edit)
        right_layout.addWidget(self.status_label)
        right_layout.addStretch(1)
        right_layout.addWidget(self.generate_button)
        right_layout.addWidget(self.play_button)
        right_layout.addWidget(self.save_button)
        right_layout.addWidget(close_button)

        layout.addWidget(left, 3)
        layout.addWidget(right, 1)

        for widget in (
            self.gender_combo,
            self.age_combo,
            self.pitch_combo,
            self.accent_combo,
        ):
            widget.currentIndexChanged.connect(self._refresh_instruct_preview)
        self.whisper_checkbox.toggled.connect(self._refresh_instruct_preview)
        self._refresh_instruct_preview()

    def tr_text(self, key: str, default: str) -> str:
        return self._translate(key, default)

    def instruction(self) -> str:
        parts: list[str] = []
        gender = str(self.gender_combo.currentData() or "").strip().lower()
        age = str(self.age_combo.currentData() or "").strip().lower()
        pitch = str(self.pitch_combo.currentData() or "").strip().lower()
        accent = str(self.accent_combo.currentData() or "").strip().lower()
        if gender in self.VALID_GENDERS:
            parts.append(gender)
        if age in self.VALID_AGES:
            parts.append(age)
        if pitch in self.VALID_PITCHES:
            parts.append(pitch)
        if self.whisper_checkbox.isChecked():
            parts.append("whisper")
        if accent in self.VALID_ACCENTS:
            parts.append(accent)

        deduped: list[str] = []
        for part in parts:
            if part and part not in deduped:
                deduped.append(part)
        return ", ".join(deduped)

    def _refresh_instruct_preview(self) -> None:
        instruction = self.instruction()
        fallback = self.tr_text("none_option", "None")
        self.instruct_preview_label.setText(
            self.tr_text(
                "omnivoice_design_current_instruction",
                "Current OmniVoice instruction: {instruction}",
            ).format(instruction=instruction or fallback)
        )

    def voice_metadata(self) -> dict[str, object]:
        language = str(self.language_combo.currentData() or "auto")
        instruction = self.instruction()
        tags = [
            "designed",
            "omnivoice",
            *[
                part.casefold().replace(" ", "_")
                for part in instruction.split(", ")
                if part
            ],
        ]
        return {
            "name": self.name_edit.text().strip()
            or self.tr_text("omnivoice_design_default_name", "Designed voice"),
            "language": language.casefold() if language != "auto" else "reference",
            "language_name": self.language_combo.currentText()
            if language != "auto"
            else self.tr_text("reference", "Reference"),
            "ref_text": self.sample_text_edit.toPlainText().strip(),
            "short_description": instruction,
            "gender": str(self.gender_combo.currentData() or ""),
            "age_style": str(self.age_combo.currentData() or "").replace(" ", "_"),
            "voice_style": instruction,
            "tags": tags,
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._window_shadow_margin = 5
        self._resize_border_px = 8
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.settings
        self.engine_host_client = EngineHostClient(self.settings_manager)
        self.local_server_controller = LocalServerController(self.settings_manager)
        self.translator = Translator(str(self.settings.get("ui_language", "en")))
        self.kokoro_python_manager = KokoroPythonManager()
        self.chatterbox_manager = ChatterboxManager()
        self.chatterbox_reference_voice_manager = ChatterboxReferenceVoiceManager()
        self.qwen_manager = QwenManager()
        self.omnivoice_manager = OmniVoiceManager()
        gallery_settings = self.settings.get("voice_gallery", {})
        self.voice_gallery_manager = VoiceGalleryManager(
            catalog_url=str(
                gallery_settings.get("catalog_url") or DEFAULT_GALLERY_CATALOG_URL
            ),
            local_catalog_path=str(gallery_settings.get("local_catalog_path", "")),
        )
        self.voice_gallery_manager.ensure_seed_loaded()
        self.audiobook_store = AudiobookStore()
        self.faster_whisper_manager = FasterWhisperManager()
        self.gpu_detection_result = detect_gpus()
        self.current_audiobook_id = self._stored_project_id()
        self.project_dirty = False
        self._loading_project = False
        self.voices: list[VoiceInfo] = []
        self.voice_page_rows: list[dict[str, object]] = []
        self.worker: EngineHostGenerationWorker | None = None
        self.worker_thread: QThread | None = None
        self.worker_uses_preloaded_engine = False
        self.kokoro_python_worker: KokoroInstallWorker | None = None
        self.kokoro_python_thread: QThread | None = None
        self.kokoro_python_preview_worker: KokoroPreviewWorker | None = None
        self.kokoro_python_preview_thread: QThread | None = None
        self.chatterbox_worker: ChatterboxInstallWorker | None = None
        self.chatterbox_thread: QThread | None = None
        self.chatterbox_preview_worker: ChatterboxPreviewWorker | None = None
        self.chatterbox_preview_thread: QThread | None = None
        self.chatterbox_hardware_worker: ChatterboxHardwareWorker | None = None
        self.chatterbox_hardware_thread: QThread | None = None
        self.chatterbox_voice_worker: ChatterboxVoiceWorker | None = None
        self.chatterbox_voice_thread: QThread | None = None
        self.voice_gallery_worker: VoiceGalleryWorker | None = None
        self.voice_gallery_thread: QThread | None = None
        self.qwen_worker: QwenInstallWorker | None = None
        self.qwen_thread: QThread | None = None
        self.qwen_preview_worker: QwenPreviewWorker | None = None
        self.qwen_preview_thread: QThread | None = None
        self.qwen_hardware_worker: QwenHardwareWorker | None = None
        self.qwen_hardware_thread: QThread | None = None
        self.omnivoice_worker: OmniVoiceInstallWorker | None = None
        self.omnivoice_thread: QThread | None = None
        self.omnivoice_preview_worker: OmniVoicePreviewWorker | None = None
        self.omnivoice_preview_thread: QThread | None = None
        self.omnivoice_design_dialog: OmniVoiceDesignDialog | None = None
        self.omnivoice_design_preview_path: Path | None = None
        self.omnivoice_design_worker: OmniVoicePreviewWorker | None = None
        self.omnivoice_design_thread: QThread | None = None
        self.omnivoice_hardware_worker: OmniVoiceHardwareWorker | None = None
        self.omnivoice_hardware_thread: QThread | None = None
        self.preload_worker: EngineHostMemoryWorker | None = None
        self.preload_thread: QThread | None = None
        self.whisper_worker: FasterWhisperInstallWorker | None = None
        self.whisper_thread: QThread | None = None
        self.whisper_preload_worker: FasterWhisperPreloadWorker | None = None
        self.whisper_preload_thread: QThread | None = None
        self.verification_worker: SegmentVerificationWorker | None = None
        self.verification_thread: QThread | None = None
        self.segment_regeneration_worker: SegmentRegenerationWorker | None = None
        self.segment_regeneration_thread: QThread | None = None
        self.segment_regeneration_uses_preloaded_engine = False
        self.audiobook_rebuild_worker: AudiobookRebuildWorker | None = None
        self.audiobook_rebuild_thread: QThread | None = None
        self.review_segments: list[StoredSegment] = []
        self.selected_review_segment_id: int | None = None
        self.review_regeneration_dialog: QDialog | None = None
        self.review_rebuild_dialog: QDialog | None = None
        self.review_rebuild_dialog_label: QLabel | None = None
        self.review_rebuild_dialog_progress: QProgressBar | None = None
        self.review_candidate_segment_id: int | None = None
        self.review_candidate_wav: Path | None = None
        self.review_after_generation_outputs: list[str] = []
        self.preloaded_whisper_verifier: FasterWhisperVerifier | None = None
        self.whisper_model_loaded = False
        self.preloaded_tts_engine: BaseTTSEngine | None = None
        self.preloaded_tts_engine_id: str | None = None
        self.host_loaded_tts_engine_ids: set[str] = set()
        self.host_generation_engine_id: str | None = None
        self.preloading_tts_engine_id: str | None = None
        self.loaded_tts_engine_id: str | None = None
        self.installer_setup_queue: list[str] = []
        self.installer_setup_running = False
        self.generation_started_at: float | None = None
        self.progress_current = 0
        self.progress_total = 0
        self.last_output_folder: Path | None = None
        self.generation_timer = QTimer(self)
        self.generation_timer.setInterval(1000)
        self.generation_timer.timeout.connect(self._update_generation_time)
        self.kokoro_audio_output = QAudioOutput(self)
        self.kokoro_sample_player = QMediaPlayer(self)
        self.kokoro_sample_player.setAudioOutput(self.kokoro_audio_output)
        self.kokoro_sample_player.playbackStateChanged.connect(
            self._on_kokoro_playback_state_changed
        )
        self.chatterbox_audio_output = QAudioOutput(self)
        self.chatterbox_sample_player = QMediaPlayer(self)
        self.chatterbox_sample_player.setAudioOutput(self.chatterbox_audio_output)
        self.chatterbox_sample_player.playbackStateChanged.connect(
            self._on_chatterbox_playback_state_changed
        )
        self.qwen_audio_output = QAudioOutput(self)
        self.qwen_sample_player = QMediaPlayer(self)
        self.qwen_sample_player.setAudioOutput(self.qwen_audio_output)
        self.qwen_sample_player.playbackStateChanged.connect(
            self._on_qwen_playback_state_changed
        )
        self.omnivoice_audio_output = QAudioOutput(self)
        self.omnivoice_sample_player = QMediaPlayer(self)
        self.omnivoice_sample_player.setAudioOutput(self.omnivoice_audio_output)
        self.omnivoice_sample_player.playbackStateChanged.connect(
            self._on_omnivoice_playback_state_changed
        )
        self.music_library_audio_output = QAudioOutput(self)
        self.music_library_player = QMediaPlayer(self)
        self.music_library_player.setAudioOutput(self.music_library_audio_output)
        self.voices_audio_output = QAudioOutput(self)
        self.voices_audio_output.setVolume(1.0)
        self.voices_player = QMediaPlayer(self)
        self.voices_player.setAudioOutput(self.voices_audio_output)
        self.voices_player.playbackStateChanged.connect(
            self._on_voice_preview_playback_state_changed
        )
        self.review_audio_output = QAudioOutput(self)
        self.review_audio_output.setVolume(1.0)
        self.review_player = QMediaPlayer(self)
        self.review_player.setAudioOutput(self.review_audio_output)

        self.setWindowTitle(self.tr("app_title", "LocalText2Voice"))
        logo_path = resource_root() / "assets" / "logotipo.png"
        self.setWindowIcon(QIcon(str(logo_path)))
        self.setMinimumSize(1180, 760)
        self.resize(1360, 860)
        self._build_ui()
        self._apply_style()
        self._load_voices()
        self._restore_settings()
        self._restore_active_project()
        self._set_running(False)
        QTimer.singleShot(700, self._maybe_start_local_server)
        QTimer.singleShot(900, self._run_pending_installer_setup)
        QTimer.singleShot(1200, self._sync_engine_host_memory_state)

    def tr(self, key: str, default: str | None = None, **values: object) -> str:
        return self.translator.text(key, default, **values)

    def _build_ui(self) -> None:
        central_widget = QWidget()
        central_widget.setObjectName("rootWidget")
        root_layout = QVBoxLayout(central_widget)
        self.root_layout = root_layout
        root_layout.setContentsMargins(
            self._window_shadow_margin,
            self._window_shadow_margin,
            self._window_shadow_margin,
            self._window_shadow_margin,
        )
        root_layout.setSpacing(0)

        self.app_frame = QFrame()
        self.app_frame.setObjectName("appFrame")
        self.app_shadow_effect = QGraphicsDropShadowEffect(self)
        self.app_shadow_effect.setBlurRadius(10)
        self.app_shadow_effect.setOffset(0, 0)
        self.app_shadow_effect.setColor(QColor(15, 23, 42, 82))
        self.app_frame.setGraphicsEffect(self.app_shadow_effect)

        frame_layout = QVBoxLayout(self.app_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        frame_layout.addWidget(self._build_title_bar())

        body_widget = QWidget()
        body_widget.setObjectName("appBody")
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self._build_sidebar())

        content_widget = QWidget()
        content_widget.setObjectName("contentArea")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(24, 18, 24, 20)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._build_page_header())

        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self._build_generation_page())
        self.page_stack.addWidget(self._build_settings_page())
        self.page_stack.addWidget(self._build_music_page())
        self.page_stack.addWidget(self._build_review_page())
        self.audio_mix_preview_panel = AudioMixPreviewPanel(self.tr)
        self.audio_mix_preview_panel.backRequested.connect(self._show_generation)
        self.audio_mix_preview_panel.openFolderRequested.connect(
            self._open_last_output_folder
        )
        self.audio_mix_preview_panel.changeMusicRequested.connect(self._show_music_page)
        self.audio_mix_preview_panel.settingsChanged.connect(
            self._on_mix_preview_settings_changed
        )
        self.audio_mix_preview_panel.renderFinished.connect(
            self._on_mix_preview_render_finished
        )
        self.audio_mix_preview_panel.errorOccurred.connect(
            lambda message: self.log_view.append_event(message)
        )
        self.audio_mix_preview_panel.log.connect(self.log_view.append_event)
        self.page_stack.addWidget(self.audio_mix_preview_panel)
        self.page_stack.addWidget(self._build_voices_page())
        content_layout.addWidget(self.page_stack, 1)

        body_layout.addWidget(content_widget, 1)
        frame_layout.addWidget(body_widget, 1)
        root_layout.addWidget(self.app_frame, 1)
        self.setCentralWidget(central_widget)
        self.resize_handles = self._create_resize_handles(self.app_frame)
        self._position_resize_handles()
        self._update_window_frame_margins()
        QTimer.singleShot(0, self._enable_windows_native_resize_border)
        self._apply_language_direction()
        self._show_generation()

    def _create_resize_handles(self, parent: QWidget) -> list[WindowResizeHandle]:
        handles = [
            WindowResizeHandle(self, Qt.Edge.TopEdge | Qt.Edge.LeftEdge, parent),
            WindowResizeHandle(self, Qt.Edge.TopEdge, parent),
            WindowResizeHandle(self, Qt.Edge.TopEdge | Qt.Edge.RightEdge, parent),
            WindowResizeHandle(self, Qt.Edge.RightEdge, parent),
            WindowResizeHandle(self, Qt.Edge.BottomEdge | Qt.Edge.RightEdge, parent),
            WindowResizeHandle(self, Qt.Edge.BottomEdge, parent),
            WindowResizeHandle(self, Qt.Edge.BottomEdge | Qt.Edge.LeftEdge, parent),
            WindowResizeHandle(self, Qt.Edge.LeftEdge, parent),
        ]
        for handle in handles:
            handle.raise_()
        return handles

    def _position_resize_handles(self) -> None:
        if not hasattr(self, "resize_handles"):
            return
        host = self.app_frame if hasattr(self, "app_frame") else self
        width = host.width()
        height = host.height()
        edge = max(10, self._resize_border_px)
        corner = max(18, edge * 2)
        geometries = (
            (0, 0, corner, corner),
            (corner, 0, max(0, width - corner * 2), edge),
            (max(0, width - corner), 0, corner, corner),
            (max(0, width - edge), corner, edge, max(0, height - corner * 2)),
            (
                max(0, width - corner),
                max(0, height - corner),
                corner,
                corner,
            ),
            (corner, max(0, height - edge), max(0, width - corner * 2), edge),
            (0, max(0, height - corner), corner, corner),
            (0, corner, edge, max(0, height - corner * 2)),
        )
        visible = not self.isMaximized() and not self.isFullScreen()
        for handle, geometry in zip(self.resize_handles, geometries, strict=True):
            handle.setGeometry(*geometry)
            handle.setVisible(visible)
            handle.raise_()

    def _build_title_bar(self) -> QWidget:
        title_bar = WindowTitleBar(self)
        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        logo_label = QLabel()
        logo_label.setObjectName("titleBarLogoLabel")
        logo_label.setFixedSize(24, 24)
        logo_label.setPixmap(
            QPixmap(str(resource_root() / "assets" / "logotipo.png")).scaled(
                24,
                24,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.app_menu_bar = QMenuBar()
        self.app_menu_bar.setObjectName("appMenuBar")
        self.app_menu_bar.setFixedHeight(30)
        self._populate_app_menus()
        layout.addWidget(self.app_menu_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self.title_minimize_button = WindowControlButton("window_minimize")
        self.title_minimize_button.setObjectName("titleBarButton")
        self.title_minimize_button.setToolTip("Minimize")
        self.title_minimize_button.setIconSize(QSize(13, 13))
        self.title_minimize_button.clicked.connect(self.showMinimized)
        layout.addWidget(self.title_minimize_button)

        self.title_maximize_button = WindowControlButton("window_maximize")
        self.title_maximize_button.setObjectName("titleBarButton")
        self.title_maximize_button.setToolTip("Maximize")
        self.title_maximize_button.setIconSize(QSize(12, 12))
        self.title_maximize_button.clicked.connect(self._toggle_maximized_window)
        layout.addWidget(self.title_maximize_button)

        self.title_close_button = WindowControlButton(
            "close",
            hover_color="#ffffff",
        )
        self.title_close_button.setObjectName("titleBarCloseButton")
        self.title_close_button.setToolTip(self.tr("close", "Close"))
        self.title_close_button.setIconSize(QSize(13, 13))
        self.title_close_button.clicked.connect(self.close)
        layout.addWidget(self.title_close_button)
        return title_bar

    def _build_app_menu_row(self) -> QWidget:
        row = QWidget()
        row.setObjectName("appMenuRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        logo_label = QLabel()
        logo_label.setObjectName("menuLogoLabel")
        logo_label.setFixedSize(28, 28)
        logo_label.setPixmap(
            QPixmap(str(resource_root() / "assets" / "logotipo.png")).scaled(
                28,
                28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label)

        self.app_menu_bar = QMenuBar()
        self.app_menu_bar.setObjectName("appMenuBar")
        self._populate_app_menus()
        layout.addWidget(self.app_menu_bar)
        layout.addStretch(1)
        return row

    def _populate_app_menus(self) -> None:
        file_menu = self.app_menu_bar.addMenu(self.tr("menu_file", "File"))
        self.new_project_action = QAction(
            self.tr("file_new_project", "New Project"),
            self,
        )
        self.new_project_action.setShortcut("Ctrl+N")
        self.new_project_action.triggered.connect(self._new_project)
        file_menu.addAction(self.new_project_action)

        self.open_project_action = QAction(
            self.tr("file_open_project", "Open Project"),
            self,
        )
        self.open_project_action.setShortcut("Ctrl+O")
        self.open_project_action.triggered.connect(self._open_project_dialog)
        file_menu.addAction(self.open_project_action)

        file_menu.addSeparator()
        self.save_project_action = QAction(self.tr("file_save", "Save"), self)
        self.save_project_action.setShortcut("Ctrl+S")
        self.save_project_action.triggered.connect(self._save_project)
        file_menu.addAction(self.save_project_action)

        self.save_project_as_action = QAction(
            self.tr("file_save_as", "Save As"),
            self,
        )
        self.save_project_as_action.setShortcut("Ctrl+Shift+S")
        self.save_project_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(self.save_project_as_action)

        file_menu.addSeparator()
        self.export_source_text_action = QAction(
            self.tr("file_export_source_text", "Export Source Text..."),
            self,
        )
        self.export_source_text_action.triggered.connect(self._export_source_text)
        file_menu.addAction(self.export_source_text_action)

        file_menu.addSeparator()
        exit_action = QAction(self.tr("file_exit", "Exit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.app_menu_bar.addMenu(self.tr("menu_edit", "Edit"))
        undo_action = QAction(self.tr("edit_undo", "Undo"), self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(lambda: self.text_editor.undo())
        edit_menu.addAction(undo_action)
        redo_action = QAction(self.tr("edit_redo", "Redo"), self)
        redo_action.setShortcut("Ctrl+Y")
        redo_action.triggered.connect(lambda: self.text_editor.redo())
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        cut_action = QAction(self.tr("edit_cut", "Cut"), self)
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(lambda: self.text_editor.cut())
        edit_menu.addAction(cut_action)
        copy_action = QAction(self.tr("edit_copy", "Copy"), self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(lambda: self.text_editor.copy())
        edit_menu.addAction(copy_action)
        paste_action = QAction(self.tr("edit_paste", "Paste"), self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(lambda: self.text_editor.paste())
        edit_menu.addAction(paste_action)

        selection_menu = self.app_menu_bar.addMenu(
            self.tr("menu_selection", "Selection")
        )
        select_all_action = QAction(self.tr("selection_select_all", "Select All"), self)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(lambda: self.text_editor.selectAll())
        selection_menu.addAction(select_all_action)

        view_menu = self.app_menu_bar.addMenu(self.tr("menu_view", "View"))
        for label_key, default, callback in (
            ("nav_generate", "Generate", self._show_generation),
            ("nav_voices", "Voices", self._show_voices_page),
            ("nav_music", "Music", self._show_music_page),
            ("nav_review", "Review", self._show_review_page),
            ("audio_mix_preview", "Audio Mix", self._show_mix_preview_page),
        ):
            action = QAction(self.tr(label_key, default), self)
            action.triggered.connect(callback)
            view_menu.addAction(action)
        view_menu.addSeparator()
        self.markup_toolbar_action = QAction(
            self.tr("markup_toolbar", "Markup Toolbar"),
            self,
        )
        self.markup_toolbar_action.setCheckable(True)
        self.markup_toolbar_action.setChecked(
            bool(self.settings.get("show_markup_toolbar", True))
        )
        self.markup_toolbar_action.toggled.connect(self._set_markup_toolbar_visible)
        view_menu.addAction(self.markup_toolbar_action)

        help_menu = self.app_menu_bar.addMenu(self.tr("menu_help", "Help"))
        about_action = QAction(self.tr("help_about", "About LocalText2Voice"), self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(260)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        brand_widget = QWidget()
        brand_widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(0, 8, 0, 12)
        brand_layout.setSpacing(10)
        logo_label = QLabel()
        logo_label.setObjectName("logoLabel")
        logo_label.setFixedSize(44, 44)
        logo_label.setPixmap(
            QPixmap(str(resource_root() / "assets" / "logotipo.png")).scaled(
                44,
                44,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(0)
        title = QLabel(self.tr("app_title", "LocalText2Voice"))
        title.setObjectName("sidebarTitleLabel")
        sidebar_subtitle = QLabel(self.tr("sidebar_subtitle", "AI Voice & Audio Production"))
        sidebar_subtitle.setObjectName("sidebarSubtitleLabel")
        sidebar_subtitle.setWordWrap(True)
        title_layout.addWidget(title)
        title_layout.addWidget(sidebar_subtitle)
        brand_layout.addWidget(logo_label)
        brand_layout.addLayout(title_layout, 1)
        layout.addWidget(brand_widget)

        self.nav_buttons: dict[str, QPushButton] = {}
        nav_items = (
            ("generate", "generate", self.tr("nav_generate", "Generate"), self._show_generation),
            ("voices", "voice", self.tr("nav_voices", "Voices"), self._show_voices_page),
            ("music", "music", self.tr("nav_music", "Music"), self._show_music_page),
            ("review", "review", self.tr("nav_review", "Review"), self._show_review_page),
            (
                "mix",
                "waveform",
                self.tr("audio_mix_preview", "Audio Mix"),
                self._show_mix_preview_page,
            ),
        )
        for key, icon_name, text, callback in nav_items:
            button = self._sidebar_button(icon_name, text)
            button.clicked.connect(callback)
            self.nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)

        engine_card = QFrame()
        engine_card.setObjectName("sidebarStatusCard")
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(14, 14, 14, 14)
        engine_layout.setSpacing(8)
        engine_header = QHBoxLayout()
        engine_icon = QLabel()
        engine_icon.setObjectName("engineStatusIcon")
        engine_icon.setPixmap(ui_icon("voice", color=ICON_LIGHT).pixmap(22, 22))
        engine_icon.setFixedSize(34, 34)
        engine_header_text = QVBoxLayout()
        engine_title = QLabel(self.tr("voice_generation_engine", "Voice Generation Engine"))
        engine_title.setObjectName("sidebarStatusTitle")
        engine_title.setWordWrap(True)
        self.header_engine_label = QLabel()
        self.header_engine_label.setObjectName("sidebarStatusText")
        self.sidebar_ready_label = QLabel(self.tr("ready", "Ready"))
        self.sidebar_ready_label.setObjectName("sidebarReadyText")
        engine_header_text.addWidget(engine_title)
        engine_header_text.addWidget(self.sidebar_ready_label)
        engine_header.addWidget(engine_icon)
        engine_header.addLayout(engine_header_text, 1)
        engine_layout.addLayout(engine_header)
        self.sidebar_engine_detail_label = QLabel("Piper")
        self.sidebar_engine_detail_label.setObjectName("helperLabel")
        self.sidebar_engine_detail_label.setWordWrap(True)
        engine_layout.addWidget(self.sidebar_engine_detail_label)
        layout.addWidget(engine_card)

        footer = QHBoxLayout()
        footer.addWidget(QLabel("v1.0.0"))
        footer.addStretch(1)
        author_credit = QLabel(
            'By Esteban, <a href="https://andromedanova.com">'
            "AndromedaNova.com</a>"
        )
        author_credit.setObjectName("authorCreditLabel")
        author_credit.setOpenExternalLinks(True)
        author_credit.setWordWrap(True)
        footer.addWidget(author_credit)
        layout.addLayout(footer)
        return sidebar

    def _sidebar_button(self, icon_name: str, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setIcon(ui_icon(icon_name))
        button.setIconSize(QSize(20, 20))
        button.setCheckable(True)
        button.setProperty("active", False)
        button.setProperty("icon_name", icon_name)
        return button

    def _build_page_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(14)
        self.page_icon_label = QLabel()
        self.page_icon_label.setObjectName("pageIconLabel")
        self.page_icon_label.setFixedSize(48, 48)
        self.page_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_title_layout = QVBoxLayout()
        page_title_layout.setSpacing(2)
        self.page_title_label = QLabel()
        self.page_title_label.setObjectName("pageTitleLabel")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitleLabel")
        page_title_layout.addWidget(self.page_title_label)
        page_title_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.page_icon_label)
        header_layout.addLayout(page_title_layout, 1)
        self.ui_language_combo = QComboBox()
        self.ui_language_combo.setIconSize(QSize(18, 18))
        self.ui_language_combo.setToolTip(
            self.tr("interface_language", "Interface language")
        )
        for locale in Translator.available_languages():
            self.ui_language_combo.addItem(
                ui_icon("language"),
                locale.name,
                locale.code,
            )
        self.ui_language_combo.currentIndexChanged.connect(
            self._change_ui_language
        )
        self.settings_button = QPushButton(self.tr("settings", "Settings"))
        self.settings_button.setIcon(ui_icon("settings"))
        self.settings_button.setIconSize(QSize(18, 18))
        self.settings_button.clicked.connect(self._toggle_settings)
        self.header_open_output_button = QPushButton(
            self.tr("open_output_folder", "Open output folder")
        )
        self.header_open_output_button.setIcon(ui_icon("folder"))
        self.header_open_output_button.setIconSize(QSize(18, 18))
        self.header_open_output_button.clicked.connect(self._open_last_output_folder)
        self.header_open_output_button.setVisible(False)
        header_layout.addWidget(self.ui_language_combo)
        header_layout.addWidget(self.settings_button)
        header_layout.addWidget(self.header_open_output_button)
        return header

    def _apply_language_direction(self) -> None:
        direction = (
            Qt.LayoutDirection.RightToLeft
            if self.translator.direction == "rtl"
            else Qt.LayoutDirection.LeftToRight
        )
        self.setLayoutDirection(direction)

    def _build_generation_page(self) -> QWidget:
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(14)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_editor_panel())

        lower_panel = QWidget()
        lower_layout = QHBoxLayout(lower_panel)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(14)
        self.hidden_voice_panel = self._build_voice_panel()
        self.hidden_voice_panel.setVisible(False)
        lower_layout.addWidget(self._build_log_panel(), 1)
        splitter.addWidget(lower_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root_layout.addWidget(splitter, 1)

        status_frame = QFrame()
        status_frame.setObjectName("card")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(8)

        self.status_label = QLabel(self.tr("ready", "Ready"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        self.time_label = QLabel()
        self.time_label.setObjectName("helperLabel")
        self.time_label.setVisible(False)
        status_layout.addWidget(self.time_label)

        button_layout = QHBoxLayout()
        self.open_output_button = QPushButton(
            self.tr("open_output_folder", "Open output folder")
        )
        self.open_output_button.setIcon(ui_icon("folder"))
        self.open_output_button.setIconSize(QSize(18, 18))
        self.open_output_button.clicked.connect(self._open_last_output_folder)
        self.open_output_button.setVisible(False)
        self.generation_voice_label = QLabel(self.tr("select_voice", "Select Voice"))
        self.generation_voice_label.setObjectName("formLabel")
        self.generation_voice_combo = QComboBox()
        self.generation_voice_combo.setMinimumContentsLength(24)
        self.generation_voice_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.generation_voice_combo.currentIndexChanged.connect(
            self._on_generation_voice_selected
        )
        button_layout.addWidget(self.generation_voice_label)
        button_layout.addWidget(self.generation_voice_combo, 1)
        button_layout.addStretch(1)
        self.cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.cancel_button.setIcon(ui_icon("cancel"))
        self.cancel_button.setIconSize(QSize(18, 18))
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.generate_button = QPushButton(
            self.tr("generate_audio", "Generate Audio")
        )
        self.generate_button.setIcon(ui_icon("generate"))
        self.generate_button.setIconSize(QSize(18, 18))
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self._start_generation)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.generate_button)
        status_layout.addLayout(button_layout)
        root_layout.addWidget(status_frame)
        return widget

    def _build_editor_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header = QLabel(self.tr("source_text", "Source text"))
        header.setObjectName("sectionLabel")
        header_layout.addWidget(header)
        header_layout.addStretch(1)

        self.import_button = QPushButton(self.tr("import_file", "Import file"))
        self.import_button.setIcon(ui_icon("file"))
        self.import_button.setIconSize(QSize(18, 18))
        self.import_button.clicked.connect(self._import_document)
        header_layout.addWidget(self.import_button)
        layout.addLayout(header_layout)
        self.markup_toolbar = self._build_markup_toolbar()
        self.markup_toolbar.setVisible(
            bool(self.settings.get("show_markup_toolbar", True))
        )
        layout.addWidget(self.markup_toolbar)

        self.text_editor = QTextEdit()
        self.text_editor.setAcceptRichText(False)
        self.text_editor.setPlaceholderText(
            self.tr(
                "text_placeholder",
                "Paste a chapter, lesson, article, or complete course here...",
            )
        )
        self.markup_highlighter = LTVMarkupHighlighter(self.text_editor.document())
        self.markup_highlighter.set_enabled(
            bool(self.settings.get("editor_syntax_highlighting", True))
        )
        self.text_editor.textChanged.connect(self._mark_project_dirty)
        self.text_editor.textChanged.connect(self._update_markup_editor_assist)
        self.text_editor.cursorPositionChanged.connect(self._update_markup_editor_assist)
        layout.addWidget(self.text_editor, 1)
        return frame

    def _build_markup_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("markupToolbar")
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        intro = QLabel(self.tr("markup_toolbar_hint", "Insert markup:"))
        intro.setObjectName("helperLabel")
        layout.addWidget(intro)
        for command in MARKUP_COMMANDS:
            button = QPushButton(command["label"])
            button.setObjectName("markupCommandButton")
            button.setProperty("markup_color", command["color"])
            button.setToolTip(command["help"])
            button.setStyleSheet(
                "QPushButton#markupCommandButton {"
                f"background: {command['background']};"
                f"color: {command['color']};"
                f"border: 1px solid {command['color']};"
                "border-radius: 14px;"
                "padding: 5px 10px;"
                "font-weight: 700;"
                "}"
                "QPushButton#markupCommandButton:hover {"
                "background: #ffffff;"
                "}"
            )
            button.clicked.connect(
                lambda _checked=False, item=command: self._insert_markup_template(item)
            )
            layout.addWidget(button)
        layout.addStretch(1)
        return toolbar

    def _insert_markup_template(self, command: dict[str, str]) -> None:
        if not hasattr(self, "text_editor"):
            return
        template = command["template"]
        cursor_marker = command["cursor"]
        cursor = self.text_editor.textCursor()
        insert_position = cursor.position()
        cursor.insertText(template)
        cursor_position = insert_position + len(cursor_marker)
        cursor.setPosition(cursor_position)
        self.text_editor.setTextCursor(cursor)
        self.text_editor.setFocus()
        self._update_markup_editor_assist()

    def _update_markup_editor_assist(self) -> None:
        if not hasattr(self, "text_editor"):
            return
        self._show_markup_context_help()

    def _show_markup_context_help(self) -> None:
        context = self._markup_context()
        if context is None:
            QToolTip.hideText()
            return
        command_name = context.get("command", "")
        if not command_name:
            QToolTip.hideText()
            return
        command = self._markup_command_by_name(command_name)
        if command is None:
            QToolTip.showText(
                self.text_editor.mapToGlobal(
                    self.text_editor.cursorRect().bottomRight()
                ),
                self._markup_help_card(
                    "Unknown command",
                    self.tr(
                        "markup_unknown_command_help",
                        "Unknown markup command. Try {{pause}}, {{voice}}, {{lang}}, {{speed}}, {{chapter}}, {{cmd}} or {{preset}}.",
                    ),
                ),
                self.text_editor,
            )
            return
        QToolTip.showText(
            self.text_editor.mapToGlobal(self.text_editor.cursorRect().bottomRight()),
            self._markup_help_card(f"{{{{{command['name']}}}}}", command["help"]),
            self.text_editor,
        )

    @staticmethod
    def _markup_help_card(title: str, help_text: str) -> str:
        body = escape(help_text)
        body = body.replace(". Examples:", ".<br><br><b>Examples:</b>")
        body = body.replace(". Example:", ".<br><br><b>Example:</b>")
        body = body.replace(". Try", ".<br><br><b>Try:</b>")
        return (
            "<qt>"
            "<div style='min-width: 380px; padding: 10px; line-height: 145%;'>"
            f"<b>{escape(title)}</b><br><br>{body}"
            "</div>"
            "</qt>"
        )

    def _markup_context(self) -> dict[str, str] | None:
        text = self.text_editor.toPlainText()
        cursor_position = self.text_editor.textCursor().position()
        before_cursor = text[:cursor_position]
        opening = before_cursor.rfind("{{")
        if opening < 0:
            return None
        closing = before_cursor.rfind("}}")
        if closing > opening:
            return None
        content = before_cursor[opening + 2 :]
        command_match = re.match(r"\s*([A-Za-z]*)", content)
        command = command_match.group(1).casefold() if command_match else ""
        return {"content": content, "command": command, "start": str(opening)}

    @staticmethod
    def _markup_command_by_name(name: str) -> dict[str, str] | None:
        normalized = name.casefold()
        for command in MARKUP_COMMANDS:
            if command["name"] == normalized:
                return command
        return None

    def _build_voice_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel(self.tr("voice_selection", "Voice selection"))
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        self.language_combo = QComboBox()
        self.language_combo.currentIndexChanged.connect(self._filter_voices)

        voice_row = QWidget()
        voice_layout = QHBoxLayout(voice_row)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(8)
        self.voice_combo = QComboBox()
        self.voice_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.voice_combo.setMinimumContentsLength(24)
        self.manage_voices_button = QPushButton(
            self.tr("manage_voices", "Manage voices")
        )
        self.manage_voices_button.setIcon(ui_icon("voice"))
        self.manage_voices_button.setIconSize(QSize(18, 18))
        self.manage_voices_button.clicked.connect(self._open_voice_manager)
        voice_layout.addWidget(self.voice_combo, 1)
        voice_layout.addWidget(self.manage_voices_button)

        form.addRow(self.tr("language", "Language"), self.language_combo)
        form.addRow(self.tr("voice", "Voice"), voice_row)
        layout.addLayout(form)

        self.voice_help_label = QLabel(
            self.tr(
                "voice_help",
                "Voices are discovered from voices/**/*.onnx when the matching "
                ".onnx.json file is present.",
            )
        )
        self.voice_help_label.setWordWrap(True)
        self.voice_help_label.setObjectName("helperLabel")
        layout.addWidget(self.voice_help_label)
        layout.addStretch(1)
        return frame

    def _build_music_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(self.tr("music_library", "Music Library"))
        title.setObjectName("sectionLabel")
        subtitle = QLabel(
            self.tr(
                "music_library_help",
                "Select the default music for your podcasts.",
            )
        )
        subtitle.setObjectName("helperLabel")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)

        self.import_music_button = QPushButton(self.tr("import_music", "Import music"))
        self.import_music_button.setIcon(ui_icon("file"))
        self.import_music_button.setIconSize(QSize(18, 18))
        self.import_music_button.clicked.connect(self._import_music_file)
        self.open_music_folder_button = QPushButton(
            self.tr("open_music_folder", "Open music folder")
        )
        self.open_music_folder_button.setIcon(ui_icon("folder"))
        self.open_music_folder_button.setIconSize(QSize(18, 18))
        self.open_music_folder_button.clicked.connect(self._open_music_folder)
        header.addWidget(self.import_music_button)
        header.addWidget(self.open_music_folder_button)
        card_layout.addLayout(header)

        self.music_table = QTableWidget(0, 5)
        self.music_table.setHorizontalHeaderLabels(
            [
                self.tr("default", "Default"),
                self.tr("track_name", "Track"),
                self.tr("duration", "Duration"),
                self.tr("file_size", "Size"),
                self.tr("actions", "Actions"),
            ]
        )
        self.music_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.music_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.music_table.verticalHeader().setVisible(False)
        self.music_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.music_table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )
        self.music_table.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.music_table.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.music_table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.music_table.setAlternatingRowColors(True)
        card_layout.addWidget(self.music_table, 1)

        self.music_status_label = QLabel()
        self.music_status_label.setObjectName("helperLabel")
        self.music_status_label.setWordWrap(True)
        card_layout.addWidget(self.music_status_label)

        layout.addWidget(card, 1)
        return widget

    def _build_review_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(self.tr("review_library", "Generation Review"))
        title.setObjectName("sectionLabel")
        self.review_subtitle_label = QLabel(
            self.tr(
                "review_library_help",
                "Review generated segments, similarity scores, and transcripts.",
            )
        )
        self.review_subtitle_label.setObjectName("helperLabel")
        self.review_subtitle_label.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self.review_subtitle_label)
        header.addLayout(title_box, 1)

        filter_box = QHBoxLayout()
        filter_box.setSpacing(6)
        filter_label = QLabel(self.tr("filter", "Filter"))
        filter_label.setObjectName("helperLabel")
        self.review_filter_combo = QComboBox()
        self.review_filter_combo.addItem(self.tr("review_filter_all", "All"), "all")
        self.review_filter_combo.addItem(
            self.tr("review_filter_attention", "Needs attention"),
            "attention",
        )
        self.review_filter_combo.addItem(
            self.tr("review_filter_retry", "Needs retry"),
            "retry_needed",
        )
        self.review_filter_combo.addItem(
            self.tr("review_filter_review", "Needs review"),
            "review",
        )
        self.review_filter_combo.addItem(
            self.tr("review_filter_approved", "Approved"),
            "approved",
        )
        self.review_filter_combo.addItem(
            self.tr("review_filter_not_verified", "Not verified"),
            "not_verified",
        )
        self.review_filter_combo.addItem(
            self.tr("review_filter_edited", "Edited"),
            "edited",
        )
        self.review_filter_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_review_page()
        )
        filter_box.addWidget(filter_label)
        filter_box.addWidget(self.review_filter_combo)
        header.addLayout(filter_box)

        self.review_refresh_button = QPushButton(self.tr("refresh", "Refresh"))
        self.review_refresh_button.setIcon(ui_icon("refresh"))
        self.review_refresh_button.setIconSize(QSize(18, 18))
        self.review_refresh_button.clicked.connect(self._refresh_review_page)
        self.review_verify_button = QPushButton(
            self.tr("verify_segments", "Verify segments")
        )
        self.review_verify_button.setIcon(ui_icon("review"))
        self.review_verify_button.setIconSize(QSize(18, 18))
        self.review_verify_button.clicked.connect(self._start_latest_verification)
        self.review_rebuild_button = QPushButton(
            self.tr("review_rebuild_audiobook", "Rebuild audiobook")
        )
        self.review_rebuild_button.setIcon(ui_icon("render"))
        self.review_rebuild_button.setIconSize(QSize(18, 18))
        self.review_rebuild_button.clicked.connect(self._start_review_rebuild)
        header.addWidget(self.review_refresh_button)
        header.addWidget(self.review_verify_button)
        header.addWidget(self.review_rebuild_button)
        card_layout.addLayout(header)

        self.review_table = QTableWidget(0, 7)
        self.review_table.setHorizontalHeaderLabels(
            [
                "#",
                self.tr("chapter", "Chapter"),
                self.tr("status", "Status"),
                self.tr("similarity", "Similarity"),
                self.tr("source_text", "Source text"),
                self.tr("transcript", "Transcript"),
                self.tr("actions", "Actions"),
            ]
        )
        self.review_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.review_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setAlternatingRowColors(True)
        self.review_table.itemSelectionChanged.connect(
            self._on_review_selection_changed
        )
        review_header = self.review_table.horizontalHeader()
        review_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        review_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        review_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        review_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        review_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        review_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        review_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        card_layout.addWidget(self.review_table, 1)

        detail_frame = QFrame()
        detail_frame.setObjectName("inlineStatusFrame")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(8)
        self.review_detail_label = QLabel(
            self.tr(
                "review_select_segment",
                "Select a segment to inspect the full original and transcript.",
            )
        )
        self.review_detail_label.setObjectName("helperLabel")
        detail_layout.addWidget(self.review_detail_label)
        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        source_box = QWidget()
        source_layout = QVBoxLayout(source_box)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(4)
        source_label = QLabel(
            self.tr("review_source_editable", "Source text (editable)")
        )
        source_label.setObjectName("helperLabel")
        self.review_source_detail = QTextEdit()
        self.review_source_detail.setReadOnly(False)
        self.review_source_detail.setMinimumHeight(110)
        self.review_source_detail.setPlaceholderText(
            self.tr(
                "review_source_edit_hint",
                "You can edit this text here, then save and regenerate the segment.",
            )
        )
        source_layout.addWidget(source_label)
        source_layout.addWidget(self.review_source_detail)
        transcript_box = QWidget()
        transcript_layout = QVBoxLayout(transcript_box)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        transcript_layout.setSpacing(4)
        transcript_label = QLabel(self.tr("transcript", "Transcript"))
        transcript_label.setObjectName("helperLabel")
        self.review_transcript_detail = QTextEdit()
        self.review_transcript_detail.setReadOnly(True)
        self.review_transcript_detail.setMinimumHeight(110)
        transcript_layout.addWidget(transcript_label)
        transcript_layout.addWidget(self.review_transcript_detail)
        detail_splitter.addWidget(source_box)
        detail_splitter.addWidget(transcript_box)
        detail_splitter.setSizes([1, 1])
        detail_layout.addWidget(detail_splitter)
        detail_actions = QHBoxLayout()
        detail_actions.setSpacing(8)
        self.review_save_text_button = QPushButton(
            self.tr("review_save_text", "Save text changes")
        )
        self.review_save_text_button.setIcon(ui_icon("save"))
        self.review_save_text_button.clicked.connect(self._save_review_segment_text)
        self.review_play_current_button = QPushButton(
            self.tr("review_play_current", "Play current audio")
        )
        self.review_play_current_button.setIcon(ui_icon("play"))
        self.review_play_current_button.clicked.connect(self._play_selected_review_audio)
        self.review_regenerate_button = QPushButton(
            self.tr("review_regenerate_segment", "Regenerate segment")
        )
        self.review_regenerate_button.setIcon(ui_icon("regenerate"))
        self.review_regenerate_button.clicked.connect(
            self._regenerate_selected_review_segment
        )
        detail_actions.addWidget(self.review_save_text_button)
        detail_actions.addWidget(self.review_play_current_button)
        detail_actions.addWidget(self.review_regenerate_button)
        detail_actions.addStretch(1)
        detail_layout.addLayout(detail_actions)
        card_layout.addWidget(detail_frame)

        self.review_status_label = QLabel()
        self.review_status_label.setObjectName("helperLabel")
        self.review_status_label.setWordWrap(True)
        self.review_progress_bar = QProgressBar()
        self.review_progress_bar.setRange(0, 100)
        self.review_progress_bar.setValue(0)
        self.review_progress_bar.setVisible(False)
        card_layout.addWidget(self.review_status_label)
        card_layout.addWidget(self.review_progress_bar)

        layout.addWidget(card, 1)
        return widget

    def _build_voices_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(self.tr("voices_library", "Voice Library"))
        title.setObjectName("sectionLabel")
        self.voices_engine_label = QLabel()
        self.voices_engine_label.setObjectName("helperLabel")
        self.voices_engine_label.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self.voices_engine_label)
        header.addLayout(title_box, 1)

        self.voices_refresh_button = QPushButton(self.tr("refresh", "Refresh"))
        self.voices_refresh_button.setIcon(ui_icon("refresh"))
        self.voices_refresh_button.setIconSize(QSize(18, 18))
        self.voices_refresh_button.clicked.connect(self._refresh_voices_page)
        self.voices_sync_button = QPushButton(self.tr("sync_catalog", "Sync catalog"))
        self.voices_sync_button.setIcon(ui_icon("save"))
        self.voices_sync_button.setIconSize(QSize(18, 18))
        self.voices_sync_button.clicked.connect(self._sync_voice_gallery)
        self.voices_manage_button = QPushButton(self.tr("manage", "Manage"))
        self.voices_manage_button.setIcon(ui_icon("settings"))
        self.voices_manage_button.setIconSize(QSize(18, 18))
        self.voices_manage_button.clicked.connect(self._voices_primary_manage_action)
        self.voices_design_button = QPushButton("Design Voice")
        self.voices_design_button.setIcon(ui_icon("render"))
        self.voices_design_button.setIconSize(QSize(18, 18))
        self.voices_design_button.clicked.connect(self._open_omnivoice_design_dialog)
        header.addWidget(self.voices_refresh_button)
        header.addWidget(self.voices_sync_button)
        header.addWidget(self.voices_manage_button)
        header.addWidget(self.voices_design_button)
        card_layout.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.voices_filter_edit = QLineEdit()
        self.voices_filter_edit.setPlaceholderText(
            self.tr(
                "search_voices_descriptions_tags",
                "Search voices, descriptions, styles or tags...",
            )
        )
        self.voices_filter_edit.textChanged.connect(self._refresh_voices_page)
        self.voices_filter_field_combo = QComboBox()
        for label, field in (
            (self.tr("all_fields", "All fields"), "all"),
            (self.tr("voice", "Voice"), "name"),
            (self.tr("short_description", "Short description"), "short_description"),
            (self.tr("language", "Language"), "language"),
            (self.tr("gender", "Gender"), "gender"),
            (self.tr("age_style", "Age style"), "age_style"),
            (self.tr("voice_style", "Voice style"), "voice_style"),
            (self.tr("tags", "Tags"), "tags"),
        ):
            self.voices_filter_field_combo.addItem(label, field)
        self.voices_filter_field_combo.currentIndexChanged.connect(
            self._refresh_voices_page
        )
        filters.addWidget(self.voices_filter_edit, 1)
        filters.addWidget(self.voices_filter_field_combo)
        card_layout.addLayout(filters)

        self.voices_table = QTableWidget(0, 10)
        self.voices_table.setHorizontalHeaderLabels(
            [
                self.tr("selected", "Selected"),
                self.tr("voice", "Voice"),
                self.tr("short_description", "Short description"),
                self.tr("language", "Language"),
                self.tr("gender", "Gender"),
                self.tr("age_style", "Age style"),
                self.tr("voice_style", "Voice style"),
                self.tr("type", "Type"),
                self.tr("status", "Status"),
                self.tr("actions", "Actions"),
            ]
        )
        self.voices_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.voices_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.voices_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.voices_table.verticalHeader().setVisible(False)
        self.voices_table.setAlternatingRowColors(True)
        self.voices_table.setSortingEnabled(True)
        voices_header = self.voices_table.horizontalHeader()
        voices_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        voices_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        voices_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        card_layout.addWidget(self.voices_table, 1)

        self.voices_status_label = QLabel()
        self.voices_status_label.setObjectName("helperLabel")
        self.voices_status_label.setWordWrap(True)
        self.voices_progress_bar = QProgressBar()
        self.voices_progress_bar.setRange(0, 100)
        self.voices_progress_bar.setValue(0)
        self.voices_progress_bar.setVisible(False)
        card_layout.addWidget(self.voices_status_label)
        card_layout.addWidget(self.voices_progress_bar)

        self.voice_preview_frame = QFrame()
        self.voice_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.voice_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.voice_preview_status_label = QLabel()
        self.voice_preview_status_label.setObjectName("helperLabel")
        self.voice_preview_status_label.setWordWrap(True)
        self.voice_preview_bar = QProgressBar()
        self.voice_preview_bar.setRange(0, 0)
        self.voice_preview_bar.setTextVisible(False)
        self.voice_preview_bar.setFixedWidth(150)
        preview_layout.addWidget(self.voice_preview_status_label, 1)
        preview_layout.addWidget(self.voice_preview_bar)
        self.voice_preview_frame.setVisible(False)
        card_layout.addWidget(self.voice_preview_frame)

        layout.addWidget(card, 1)
        return widget

    def _build_settings_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        self.back_button = QPushButton(
            self.tr("back_to_generation", "Back to generation")
        )
        self.back_button.setIcon(ui_icon("back"))
        self.back_button.clicked.connect(self._show_generation)
        self.back_button.setVisible(False)
        title = QLabel(self.tr("settings", "Settings"))
        title.setObjectName("sectionLabel")
        title.setVisible(False)
        header_layout.addWidget(self.back_button)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.addTab(
            self._build_general_settings(),
            ui_icon("settings"),
            self.tr("general_settings", "General"),
        )
        self.settings_tabs.addTab(
            self._build_tts_engine_settings(),
            ui_icon("voice"),
            self.tr("tts_models_tab", "TTS Engines"),
        )
        self.settings_tabs.addTab(
            self._build_review_settings(),
            ui_icon("review"),
            self.tr("review_settings", "Review"),
        )
        self.settings_tabs.addTab(
            self._build_local_server_settings(),
            ui_icon("server"),
            self.tr("local_server_settings", "Local Server"),
        )
        self.settings_tabs.addTab(
            self._build_advanced_settings(),
            ui_icon("settings"),
            self.tr("advanced_settings", "Advanced"),
        )
        layout.addWidget(self.settings_tabs, 1)
        return widget

    def _toggle_settings(self) -> None:
        if self.page_stack.currentIndex() != 1:
            self._show_settings_page()
        else:
            self._show_generation()

    def _show_generation(self) -> None:
        self._show_page(
            0,
            "generate",
            self.tr("generation_settings", "Generation"),
            self.tr(
                "generation_page_subtitle",
                "Paste long-form text, choose a voice, and generate clean MP3 narration.",
            ),
            "generate",
        )

    def _show_settings_page(self) -> None:
        self._show_page(
            1,
            "settings",
            self.tr("settings", "Settings"),
            self.tr(
                "settings_page_subtitle",
                "Configure engines, output, podcast mixing, and advanced generation options.",
            ),
            "settings",
        )
        self._refresh_wav_cache_stats()

    def _run_pending_installer_setup(self) -> None:
        setup = self.settings.get("installer_setup", {})
        if not isinstance(setup, dict) or setup.get("completed"):
            return
        pending = [
            str(item)
            for item in setup.get("pending_installs", [])
            if str(item) in {"omnivoice", "faster_whisper"}
        ]
        if not pending:
            return

        profile = str(setup.get("profile", "gpu")).casefold()
        if "omnivoice" in pending:
            self.settings["tts_engine"] = "omnivoice"
        if "faster_whisper" in pending:
            review = self.settings.get("review", {})
            if not isinstance(review, dict):
                review = {}
            review["enabled"] = True
            review["auto_verify_after_generation"] = True
            self.settings["review"] = review
        self.settings_manager.save(self.settings)
        self._restore_settings()
        self._show_settings_page()
        if hasattr(self, "settings_tabs"):
            self.settings_tabs.setCurrentIndex(1)

        QMessageBox.information(
            self,
            self.tr("installer_setup_title", "LocalText2Voice setup"),
            self.tr(
                "installer_gpu_setup_message",
                "The GPU profile was selected during installation. "
                "LocalText2Voice will now download and prepare OmniVoice and Faster Whisper. "
                "This can take several minutes and requires an internet connection.",
            )
            if profile == "gpu"
            else self.tr(
                "installer_setup_message",
                "LocalText2Voice will now prepare the selected optional components.",
            ),
        )
        self.installer_setup_queue = pending
        self.installer_setup_running = True
        self._start_next_installer_setup_task()

    def _start_next_installer_setup_task(self) -> None:
        if not self.installer_setup_running:
            return
        while self.installer_setup_queue:
            task = self.installer_setup_queue.pop(0)
            if task == "omnivoice":
                if OmniVoiceManager().is_installed():
                    self.log_view.append_event("Installer setup: OmniVoice already installed.")
                    continue
                self._select_combo_data(self.tts_engine_combo, "omnivoice")
                self._on_tts_engine_changed()
                if hasattr(self, "settings_tabs"):
                    self.settings_tabs.setCurrentIndex(1)
                self._install_omnivoice()
                return
            if task == "faster_whisper":
                if FasterWhisperManager().is_installed():
                    self.log_view.append_event(
                        "Installer setup: Faster Whisper already installed."
                    )
                    continue
                if hasattr(self, "settings_tabs"):
                    self.settings_tabs.setCurrentIndex(2)
                self._install_faster_whisper()
                return
        self._complete_pending_installer_setup()

    def _continue_pending_installer_setup(self) -> None:
        if self.installer_setup_running:
            QTimer.singleShot(500, self._start_next_installer_setup_task)

    def _abort_pending_installer_setup(self, message: str) -> None:
        if not self.installer_setup_running:
            return
        setup = self.settings.get("installer_setup", {})
        if not isinstance(setup, dict):
            setup = {}
        setup["completed"] = False
        setup["last_error"] = message
        setup["pending_installs"] = self.installer_setup_queue
        self.settings["installer_setup"] = setup
        self.settings_manager.save(self.settings)
        self.installer_setup_queue = []
        self.installer_setup_running = False

    def _complete_pending_installer_setup(self) -> None:
        setup = self.settings.get("installer_setup", {})
        if not isinstance(setup, dict):
            setup = {}
        setup["pending_installs"] = []
        setup["completed"] = True
        setup["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.settings["installer_setup"] = setup
        self.settings_manager.save(self.settings)
        self.installer_setup_running = False
        self._refresh_tts_engine_table()
        self._refresh_whisper_status()
        QMessageBox.information(
            self,
            self.tr("installer_setup_title", "LocalText2Voice setup"),
            self.tr(
                "installer_setup_complete",
                "Optional components are ready. You can start creating audiobooks.",
            ),
        )

    def _show_mix_preview_page(self) -> None:
        self._ensure_audio_mix_preview_context()
        self._show_page(
            4,
            "mix",
            self.tr("audio_mix_preview", "Audio Mix"),
            self.tr(
                "audio_mix_preview_subtitle",
                "Mix your voice narration with background music.",
            ),
            "generate",
        )

    def _show_voices_page(self) -> None:
        self._show_page(
            5,
            "voices",
            self.tr("voices_library", "Voice Library"),
            self.tr(
                "voices_library_subtitle",
                "Manage and test voices for the currently selected TTS engine.",
            ),
            "voice",
        )
        self._refresh_voices_page()

    def _show_review_page(self) -> None:
        self._show_page(
            3,
            "review",
            self.tr("review_library", "Generation Review"),
            self.tr(
                "review_page_subtitle",
                "Inspect generated segments and automatic transcription scores.",
            ),
            "review",
        )
        self._refresh_review_page()

    def _show_music_page(self) -> None:
        self._show_page(
            2,
            "music",
            self.tr("music_library", "Music Library"),
            self.tr(
                "music_library_subtitle",
                "Manage reusable MP3/WAV music tracks for podcast intros, backgrounds, and outros.",
            ),
            "music",
        )
        self._refresh_music_library()

    def _show_page(
        self,
        index: int,
        nav_key: str,
        title: str,
        subtitle: str,
        icon_name: str,
    ) -> None:
        self.page_stack.setCurrentIndex(index)
        self.settings_button.setText(self.tr("settings", "Settings"))
        self.settings_button.setIcon(ui_icon("settings"))
        self._set_active_nav(nav_key)
        self.page_title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.page_icon_label.setPixmap(ui_icon(icon_name, color=ICON_LIGHT).pixmap(32, 32))
        show_output_button = nav_key == "mix"
        self.header_open_output_button.setVisible(show_output_button)
        self.header_open_output_button.setEnabled(
            show_output_button and self._current_output_folder() is not None
        )

    def _custom_tts_engines(self) -> list[dict[str, object]]:
        engines = self.settings.get("custom_tts_engines", [])
        if not isinstance(engines, list):
            return []
        result: list[dict[str, object]] = []
        for item in engines:
            if isinstance(item, dict) and str(item.get("id", "")).strip():
                result.append(dict(item))
        return result

    @staticmethod
    def _custom_engine_key(engine_id: object) -> str:
        return f"custom:{str(engine_id).strip()}"

    def _custom_engine_by_key(self, engine_key: str) -> dict[str, object] | None:
        if not engine_key.startswith("custom:"):
            return None
        wanted = engine_key.split(":", 1)[1]
        for engine in self._custom_tts_engines():
            if str(engine.get("id", "")) == wanted:
                return engine
        return None

    def _populate_tts_engine_combo(self, selected_engine: object | None = None) -> None:
        if not hasattr(self, "tts_engine_combo"):
            return
        selected = (
            selected_engine
            if selected_engine is not None
            else self.tts_engine_combo.currentData()
        )
        self.tts_engine_combo.blockSignals(True)
        self.tts_engine_combo.clear()
        for engine in TTS_ENGINES:
            self.tts_engine_combo.addItem(
                ui_icon("voice"),
                self._tts_engine_label(engine.engine_id),
                engine.engine_id,
            )
        for engine in self._custom_tts_engines():
            key = self._custom_engine_key(engine.get("id", ""))
            self.tts_engine_combo.addItem(
                ui_icon("settings"),
                self._tts_engine_label(key),
                key,
            )
        if selected is not None:
            self._select_combo_data(self.tts_engine_combo, selected)
        self.tts_engine_combo.blockSignals(False)

    @staticmethod
    def _custom_tts_engine_defaults() -> dict[str, object]:
        return {
            "id": "",
            "name": "",
            "location": "local_http",
            "url": "http://127.0.0.1:7851/api/tts-generate",
            "method": "POST",
            "voice": "",
            "language": "",
            "api_key": "",
            "auth_header": "",
            "headers_json": '{\n  "Content-Type": "application/json"\n}',
            "body_template": (
                '{\n'
                '  "text": "{{text}}",\n'
                '  "voice": "{{voice}}",\n'
                '  "language": "{{language}}",\n'
                '  "speed": {{speed}}\n'
                '}'
            ),
            "response_mode": "audio_wav",
            "json_audio_path": "",
            "sample_rate": 24000,
            "timeout_seconds": 120,
        }

    @staticmethod
    def _custom_engine_slug(name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
        return slug[:48] or "custom-engine"

    def _unique_custom_engine_id(
        self,
        name: str,
        existing_id: str | None = None,
    ) -> str:
        base = existing_id or self._custom_engine_slug(name)
        existing = {
            str(engine.get("id", ""))
            for engine in self._custom_tts_engines()
            if str(engine.get("id", "")) != (existing_id or "")
        }
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _set_active_nav(self, active_key: str) -> None:
        if not hasattr(self, "nav_buttons"):
            return
        for key, button in self.nav_buttons.items():
            is_active = key == active_key
            button.setChecked(is_active)
            button.setProperty("active", is_active)
            icon_name = str(button.property("icon_name") or key)
            button.setIcon(ui_icon(icon_name, active=is_active))
            button.style().unpolish(button)
            button.style().polish(button)

    def _build_review_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.whisper_missing_frame = QFrame()
        self.whisper_missing_frame.setObjectName("whisperMissingFrame")
        self.whisper_missing_frame.setStyleSheet(
            "QFrame#whisperMissingFrame {"
            " background: #fff7ed;"
            " border: 1px solid #fb923c;"
            " border-radius: 10px;"
            "}"
            "QLabel { color: #7c2d12; }"
        )
        missing_layout = QHBoxLayout(self.whisper_missing_frame)
        missing_layout.setContentsMargins(14, 12, 14, 12)
        missing_layout.setSpacing(12)
        missing_icon = QLabel()
        missing_icon.setPixmap(ui_icon("warning").pixmap(22, 22))
        missing_text = QLabel(
            self.tr(
                "whisper_missing_message",
                "Faster Whisper is not installed. Generation Review needs it "
                "to transcribe segment audio, compare it with the source text, "
                "and mark segments that need attention.",
            )
        )
        missing_text.setWordWrap(True)
        missing_text.setObjectName("helperLabel")
        self.whisper_missing_install_button = QPushButton(
            self.tr("install_whisper_now", "Install Faster Whisper")
        )
        self.whisper_missing_install_button.setIcon(ui_icon("apply"))
        self.whisper_missing_install_button.clicked.connect(self._install_faster_whisper)
        missing_layout.addWidget(missing_icon)
        missing_layout.addWidget(missing_text, 1)
        missing_layout.addWidget(self.whisper_missing_install_button)
        layout.addWidget(self.whisper_missing_frame)

        group = QGroupBox(self.tr("review_settings", "Generation Review"))
        form = QFormLayout(group)
        form.setSpacing(10)

        self.review_enabled_checkbox = QCheckBox(
            self.tr("review_enable", "Enable generation review")
        )
        self.review_enabled_checkbox.toggled.connect(lambda _checked: self._save_settings())
        self.review_auto_checkbox = QCheckBox(
            self.tr(
                "review_auto",
                "Automatically review generated segments after generation",
            )
        )
        self.review_auto_checkbox.toggled.connect(lambda _checked: self._save_settings())
        form.addRow("", self.review_enabled_checkbox)
        form.addRow("", self.review_auto_checkbox)

        self.review_model_combo = QComboBox()
        self.review_model_combo.addItem("Faster Whisper small", "small")
        self.review_model_combo.currentIndexChanged.connect(lambda _index: self._save_settings())
        form.addRow(self.tr("review_model", "Review model"), self.review_model_combo)

        self.review_device_combo = QComboBox()
        self.review_device_combo.addItem("CPU", "cpu")
        self.review_device_combo.addItem("Auto", "auto")
        self.review_device_combo.addItem("CUDA / NVIDIA GPU", "cuda")
        self.review_device_combo.currentIndexChanged.connect(lambda _index: self._save_settings())
        form.addRow(self.tr("review_device", "Compute device"), self.review_device_combo)

        self.review_compute_combo = QComboBox()
        self.review_compute_combo.addItem("int8 (recommended CPU)", "int8")
        self.review_compute_combo.addItem("default", "default")
        self.review_compute_combo.addItem("float16", "float16")
        self.review_compute_combo.currentIndexChanged.connect(lambda _index: self._save_settings())
        form.addRow(self.tr("review_compute_type", "Compute type"), self.review_compute_combo)

        self.review_language_combo = QComboBox()
        for label, value in (
            ("Auto", "auto"),
            ("English", "en"),
            ("Spanish", "es"),
            ("French", "fr"),
            ("German", "de"),
            ("Italian", "it"),
            ("Portuguese", "pt"),
        ):
            self.review_language_combo.addItem(label, value)
        self.review_language_combo.currentIndexChanged.connect(lambda _index: self._save_settings())
        form.addRow(self.tr("review_language", "Transcription language"), self.review_language_combo)

        self.review_beam_spin = QSpinBox()
        self.review_beam_spin.setRange(1, 5)
        self.review_beam_spin.setValue(1)
        self.review_beam_spin.valueChanged.connect(lambda _value: self._save_settings())
        form.addRow(self.tr("review_beam_size", "Beam size"), self.review_beam_spin)

        self.review_threshold_spin = QDoubleSpinBox()
        self.review_threshold_spin.setRange(50.0, 100.0)
        self.review_threshold_spin.setDecimals(1)
        self.review_threshold_spin.setSuffix(" %")
        self.review_threshold_spin.setValue(92.0)
        self.review_threshold_spin.valueChanged.connect(lambda _value: self._save_settings())
        form.addRow(
            self.tr("review_threshold", "Approval threshold"),
            self.review_threshold_spin,
        )

        self.review_max_retries_spin = QSpinBox()
        self.review_max_retries_spin.setRange(0, 5)
        self.review_max_retries_spin.setValue(0)
        self.review_max_retries_spin.valueChanged.connect(lambda _value: self._save_settings())
        form.addRow(
            self.tr("review_max_retries", "Automatic retries"),
            self.review_max_retries_spin,
        )
        retries_help = QLabel(
            self.tr(
                "review_retries_help",
                "Automatic retries only run for segments below the threshold. "
                "Each retry regenerates a candidate, transcribes it with Whisper, "
                "scores it, and keeps the best-scoring audio in the database.",
            )
        )
        retries_help.setObjectName("helperLabel")
        retries_help.setWordWrap(True)
        form.addRow("", retries_help)

        actions = QHBoxLayout()
        self.whisper_install_button = QPushButton(self.tr("install", "Install"))
        self.whisper_install_button.setIcon(ui_icon("apply"))
        self.whisper_install_button.clicked.connect(self._install_faster_whisper)
        self.whisper_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.whisper_remove_button.setIcon(ui_icon("delete"))
        self.whisper_remove_button.clicked.connect(self._remove_faster_whisper)
        self.whisper_load_button = QPushButton(
            self.tr("load_into_memory", "Load into memory")
        )
        self.whisper_load_button.setIcon(ui_icon("open"))
        self.whisper_load_button.clicked.connect(self._toggle_faster_whisper_preload)
        actions.addWidget(self.whisper_install_button)
        actions.addWidget(self.whisper_remove_button)
        actions.addWidget(self.whisper_load_button)
        form.addRow("", actions)

        self.whisper_progress_bar = QProgressBar()
        self.whisper_progress_bar.setRange(0, 100)
        self.whisper_progress_bar.setValue(0)
        self.whisper_progress_bar.setVisible(False)
        self.whisper_status_label = QLabel()
        self.whisper_status_label.setObjectName("helperLabel")
        self.whisper_status_label.setWordWrap(True)
        form.addRow("", self.whisper_status_label)
        form.addRow("", self.whisper_progress_bar)

        note = QLabel(
            self.tr(
                "review_help",
                "Faster Whisper small is downloaded on demand and used to "
                "transcribe generated segment WAV files for similarity scoring. "
                "When language is Auto, LocalText2Voice uses the segment language "
                "when available and falls back to Whisper auto-detection.",
            )
        )
        note.setObjectName("helperLabel")
        note.setWordWrap(True)
        form.addRow("", note)

        layout.addWidget(group)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _build_local_server_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        desktop_group = QGroupBox(
            self.tr("desktop_mcp_settings", "MCP Desktop Clients")
        )
        desktop_layout = QVBoxLayout(desktop_group)
        desktop_layout.setSpacing(10)

        desktop_intro = QLabel(
            self.tr(
                "desktop_mcp_intro",
                "Use this stdio MCP configuration for Claude Desktop and other "
                "desktop clients that launch local MCP servers.",
            )
        )
        desktop_intro.setObjectName("helperLabel")
        desktop_intro.setWordWrap(True)
        desktop_layout.addWidget(desktop_intro)

        claude_title = QLabel(self.tr("claude_desktop", "Claude Desktop"))
        claude_title.setObjectName("sectionTitle")
        desktop_layout.addWidget(claude_title)

        self.local_mcp_json_edit = QTextEdit()
        self.local_mcp_json_edit.setReadOnly(True)
        self.local_mcp_json_edit.setMinimumHeight(150)
        self.local_mcp_json_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        desktop_layout.addWidget(self.local_mcp_json_edit)

        mcp_actions = QHBoxLayout()
        self.copy_mcp_json_button = QPushButton(
            self.tr("copy_mcp_json", "Copy JSON")
        )
        self.copy_mcp_json_button.setIcon(ui_icon("copy"))
        self.copy_mcp_json_button.clicked.connect(self._copy_mcp_desktop_json)
        self.open_claude_config_button = QPushButton(
            self.tr("open_claude_config", "Open Claude config")
        )
        self.open_claude_config_button.setIcon(ui_icon("open"))
        self.open_claude_config_button.clicked.connect(self._open_claude_config)
        mcp_actions.addWidget(self.copy_mcp_json_button)
        mcp_actions.addWidget(self.open_claude_config_button)
        mcp_actions.addStretch(1)
        desktop_layout.addLayout(mcp_actions)

        claude_help = QLabel(
            self.tr(
                "claude_mcp_help",
                "Paste or merge this block into %APPDATA%\\Claude\\claude_desktop_config.json, "
                "then restart Claude Desktop. Use Developer settings or Connectors in Claude "
                "to confirm that LocalText2Voice tools are loaded.",
            )
        )
        claude_help.setObjectName("helperLabel")
        claude_help.setWordWrap(True)
        desktop_layout.addWidget(claude_help)

        codex_separator = QFrame()
        codex_separator.setFrameShape(QFrame.Shape.HLine)
        codex_separator.setFrameShadow(QFrame.Shadow.Sunken)
        desktop_layout.addWidget(codex_separator)

        codex_title = QLabel(
            self.tr("codex_chatgpt_desktop", "Codex / ChatGPT Desktop")
        )
        codex_title.setObjectName("sectionTitle")
        desktop_layout.addWidget(codex_title)

        self.local_codex_toml_edit = QTextEdit()
        self.local_codex_toml_edit.setReadOnly(True)
        self.local_codex_toml_edit.setMinimumHeight(135)
        self.local_codex_toml_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        desktop_layout.addWidget(self.local_codex_toml_edit)

        codex_actions = QHBoxLayout()
        self.copy_codex_toml_button = QPushButton(
            self.tr("copy_codex_toml", "Copy TOML")
        )
        self.copy_codex_toml_button.setIcon(ui_icon("copy"))
        self.copy_codex_toml_button.clicked.connect(self._copy_codex_mcp_toml)
        self.open_codex_config_button = QPushButton(
            self.tr("open_codex_config", "Open Codex config")
        )
        self.open_codex_config_button.setIcon(ui_icon("open"))
        self.open_codex_config_button.clicked.connect(self._open_codex_config)
        codex_actions.addWidget(self.copy_codex_toml_button)
        codex_actions.addWidget(self.open_codex_config_button)
        codex_actions.addStretch(1)
        desktop_layout.addLayout(codex_actions)

        codex_help = QLabel(
            self.tr(
                "codex_mcp_help",
                "Paste or merge this block into ~/.codex/config.toml, then restart Codex "
                "or ChatGPT Desktop. The paths are generated for this Windows user and the "
                "current LocalText2Voice installation.",
            )
        )
        codex_help.setObjectName("helperLabel")
        codex_help.setWordWrap(True)
        desktop_layout.addWidget(codex_help)

        layout.addWidget(desktop_group)

        group = QGroupBox(
            self.tr("local_server_settings", "Advanced HTTP / Remote MCP Server")
        )
        form = QFormLayout(group)
        form.setSpacing(10)

        intro = QLabel(
            self.tr(
                "local_server_intro",
                "Optional advanced server for HTTP clients or future remote workflows. "
                "For Claude Desktop, prefer the stdio configuration above.",
            )
        )
        intro.setObjectName("helperLabel")
        intro.setWordWrap(True)
        form.addRow("", intro)

        self.local_server_enabled_checkbox = QCheckBox(
            self.tr("enable_local_server", "Enable local server")
        )
        self.local_server_enabled_checkbox.toggled.connect(
            self._on_local_server_enabled_changed
        )
        form.addRow("", self.local_server_enabled_checkbox)

        self.local_server_auto_start_checkbox = QCheckBox(
            self.tr("local_server_auto_start", "Start server when LocalText2Voice opens")
        )
        self.local_server_auto_start_checkbox.toggled.connect(
            lambda _checked: self._save_settings()
        )
        form.addRow("", self.local_server_auto_start_checkbox)

        host_row = QWidget()
        host_layout = QHBoxLayout(host_row)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(8)
        self.local_server_host_edit = QLineEdit("127.0.0.1")
        self.local_server_host_edit.textChanged.connect(
            lambda _text: self._on_local_server_field_changed()
        )
        self.local_server_port_spin = QSpinBox()
        self.local_server_port_spin.setRange(1024, 65535)
        self.local_server_port_spin.setValue(8765)
        self.local_server_port_spin.valueChanged.connect(
            lambda _value: self._on_local_server_field_changed()
        )
        host_layout.addWidget(self.local_server_host_edit, 1)
        host_layout.addWidget(QLabel(":"))
        host_layout.addWidget(self.local_server_port_spin)
        form.addRow(self.tr("local_server_bind", "Bind address"), host_row)

        self.local_server_allow_lan_checkbox = QCheckBox(
            self.tr("local_server_allow_lan", "Allow access from this LAN")
        )
        self.local_server_allow_lan_checkbox.setToolTip(
            self.tr(
                "local_server_allow_lan_tip",
                "Keep this off unless you need another device on your network to connect.",
            )
        )
        self.local_server_allow_lan_checkbox.toggled.connect(
            lambda _checked: self._on_local_server_field_changed()
        )
        form.addRow("", self.local_server_allow_lan_checkbox)

        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.setSpacing(8)
        self.local_server_token_edit = QLineEdit()
        self.local_server_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_server_token_edit.textChanged.connect(
            lambda _text: self._on_local_server_field_changed()
        )
        self.local_server_show_token_button = QPushButton(
            self.tr("show_token", "Show")
        )
        self.local_server_show_token_button.setIcon(ui_icon("show"))
        self.local_server_show_token_button.setCheckable(True)
        self.local_server_show_token_button.toggled.connect(
            self._toggle_local_server_token_visibility
        )
        self.local_server_copy_token_button = QPushButton(
            self.tr("copy_token", "Copy")
        )
        self.local_server_copy_token_button.setIcon(ui_icon("copy"))
        self.local_server_copy_token_button.clicked.connect(
            self._copy_local_server_token
        )
        self.local_server_generate_token_button = QPushButton(
            self.tr("generate_token", "Generate token")
        )
        self.local_server_generate_token_button.setIcon(ui_icon("refresh"))
        self.local_server_generate_token_button.clicked.connect(
            self._generate_local_server_token
        )
        token_layout.addWidget(self.local_server_token_edit, 1)
        token_layout.addWidget(self.local_server_show_token_button)
        token_layout.addWidget(self.local_server_copy_token_button)
        token_layout.addWidget(self.local_server_generate_token_button)
        form.addRow(self.tr("local_server_token", "Access token"), token_row)

        self.local_server_max_jobs_spin = QSpinBox()
        self.local_server_max_jobs_spin.setRange(1, 1)
        self.local_server_max_jobs_spin.setValue(1)
        self.local_server_max_jobs_spin.setToolTip(
            self.tr(
                "local_server_one_job_tip",
                "Heavy TTS models are safest with one generation job at a time.",
            )
        )
        self.local_server_max_jobs_spin.valueChanged.connect(
            lambda _value: self._on_local_server_field_changed()
        )
        form.addRow(
            self.tr("local_server_parallel_jobs", "Parallel jobs"),
            self.local_server_max_jobs_spin,
        )

        self.local_server_status_label = QLabel("")
        self.local_server_status_label.setObjectName("helperLabel")
        self.local_server_status_label.setWordWrap(True)
        form.addRow("", self.local_server_status_label)

        self.local_server_endpoint_label = QLabel("")
        self.local_server_endpoint_label.setObjectName("helperLabel")
        self.local_server_endpoint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.local_server_endpoint_label.setWordWrap(True)
        form.addRow(self.tr("local_server_mcp_url", "MCP endpoint"), self.local_server_endpoint_label)

        http_help = QLabel(
            self.tr(
                "local_server_http_help",
                "HTTP endpoints: GET /health, GET /voices, GET /background-music, "
                "POST /jobs, GET /jobs/{job_id}, POST /jobs/{job_id}/cancel.",
            )
        )
        http_help.setObjectName("helperLabel")
        http_help.setWordWrap(True)
        form.addRow("", http_help)

        actions = QHBoxLayout()
        self.local_server_start_stop_button = QPushButton()
        self.local_server_start_stop_button.setIcon(ui_icon("server"))
        self.local_server_start_stop_button.clicked.connect(
            self._toggle_local_server
        )
        actions.addWidget(self.local_server_start_stop_button)
        actions.addStretch(1)
        form.addRow("", actions)

        layout.addWidget(group)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        self._refresh_local_server_status()
        self._refresh_mcp_desktop_json()
        return scroll

    def _local_server_settings_from_ui(self) -> dict[str, object]:
        current = self.settings.get("local_server", {})
        fallback = dict(current) if isinstance(current, dict) else {}
        if not hasattr(self, "local_server_enabled_checkbox"):
            return fallback
        return {
            "enabled": self.local_server_enabled_checkbox.isChecked(),
            "auto_start": self.local_server_auto_start_checkbox.isChecked(),
            "host": self.local_server_host_edit.text().strip() or "127.0.0.1",
            "port": self.local_server_port_spin.value(),
            "auth_token": self.local_server_token_edit.text().strip(),
            "allow_lan": self.local_server_allow_lan_checkbox.isChecked(),
            "serve_files": True,
            "max_parallel_jobs": self.local_server_max_jobs_spin.value(),
        }

    def _mcp_desktop_config(self) -> dict[str, object]:
        app_root = application_root()
        if getattr(sys, "frozen", False):
            command = app_root / "LocalText2VoiceMCP.exe"
            args: list[str] = []
        else:
            venv_python = app_root / ".venv" / "Scripts" / "python.exe"
            command = venv_python if venv_python.exists() else Path(sys.executable)
            args = [str(app_root / "mcp_stdio_bridge.py")]
        return {
            "mcpServers": {
                "localtext2voice": {
                    "command": str(command),
                    "args": args,
                    "cwd": str(app_root),
                }
            }
        }

    def _mcp_desktop_config_text(self) -> str:
        return json.dumps(self._mcp_desktop_config(), indent=2, ensure_ascii=False)

    def _refresh_mcp_desktop_json(self) -> None:
        if hasattr(self, "local_mcp_json_edit"):
            self.local_mcp_json_edit.setPlainText(self._mcp_desktop_config_text())
        if hasattr(self, "local_codex_toml_edit"):
            self.local_codex_toml_edit.setPlainText(self._codex_mcp_config_text())

    def _copy_mcp_desktop_json(self) -> None:
        QApplication.clipboard().setText(self._mcp_desktop_config_text())
        self.statusBar().showMessage(
            self.tr("mcp_json_copied", "MCP configuration copied to clipboard."),
            3000,
        )

    def _claude_config_path(self) -> Path:
        app_data = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return app_data / "Claude" / "claude_desktop_config.json"

    def _codex_mcp_config_text(self) -> str:
        app_root = application_root()
        if getattr(sys, "frozen", False):
            command = app_root / "LocalText2VoiceMCP.exe"
            args: list[str] = []
        else:
            venv_python = app_root / ".venv" / "Scripts" / "python.exe"
            command = venv_python if venv_python.exists() else Path(sys.executable)
            args = [str(app_root / "mcp_stdio_bridge.py")]

        def literal(value: str) -> str:
            if "'" not in value:
                return f"'{value}'"
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        args_text = ", ".join(literal(value) for value in args)
        return "\n".join(
            [
                "[mcp_servers.localtext2voice]",
                f"command = {literal(str(command))}",
                f"args = [{args_text}]",
                f"cwd = {literal(str(app_root))}",
            ]
        )

    def _copy_codex_mcp_toml(self) -> None:
        QApplication.clipboard().setText(self._codex_mcp_config_text())
        self.statusBar().showMessage(
            self.tr("codex_toml_copied", "Codex MCP configuration copied to clipboard."),
            3000,
        )

    @staticmethod
    def _codex_config_path() -> Path:
        codex_home = os.environ.get("CODEX_HOME", "").strip()
        return (Path(codex_home) if codex_home else Path.home() / ".codex") / "config.toml"

    def _open_codex_config(self) -> None:
        path = self._codex_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_claude_config(self) -> None:
        path = self._claude_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _restore_local_server_settings(self) -> None:
        if not hasattr(self, "local_server_enabled_checkbox"):
            return
        settings = self.settings.get("local_server", {})
        if not isinstance(settings, dict):
            settings = {}
        if not str(settings.get("auth_token", "")).strip():
            settings = dict(settings)
            settings["auth_token"] = secrets.token_urlsafe(24)
            self.settings["local_server"] = settings
            self.settings_manager.save(self.settings)
        widgets = (
            self.local_server_enabled_checkbox,
            self.local_server_auto_start_checkbox,
            self.local_server_host_edit,
            self.local_server_port_spin,
            self.local_server_token_edit,
            self.local_server_allow_lan_checkbox,
            self.local_server_max_jobs_spin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.local_server_enabled_checkbox.setChecked(bool(settings.get("enabled", False)))
        self.local_server_auto_start_checkbox.setChecked(bool(settings.get("auto_start", False)))
        self.local_server_host_edit.setText(str(settings.get("host", "127.0.0.1")))
        self.local_server_port_spin.setValue(int(settings.get("port", 8765) or 8765))
        self.local_server_token_edit.setText(str(settings.get("auth_token", "")))
        self.local_server_allow_lan_checkbox.setChecked(bool(settings.get("allow_lan", False)))
        self.local_server_max_jobs_spin.setValue(1)
        for widget in widgets:
            widget.blockSignals(False)
        self._refresh_local_server_status()
        self._refresh_mcp_desktop_json()

    def _maybe_start_local_server(self) -> None:
        settings = self.settings.get("local_server", {})
        if not isinstance(settings, dict):
            return
        if bool(settings.get("enabled", False)) or bool(settings.get("auto_start", False)):
            self._start_local_server(show_errors=False)
        else:
            self._refresh_local_server_status()

    def _toggle_local_server(self) -> None:
        if self.local_server_controller.is_running():
            self._stop_local_server()
            return
        self._start_local_server(show_errors=True)

    def _start_local_server(self, show_errors: bool = True) -> None:
        if not hasattr(self, "local_server_enabled_checkbox"):
            return
        if not self.local_server_token_edit.text().strip():
            self.local_server_token_edit.setText(secrets.token_urlsafe(24))
        self.local_server_enabled_checkbox.blockSignals(True)
        self.local_server_enabled_checkbox.setChecked(True)
        self.local_server_enabled_checkbox.blockSignals(False)
        self._save_settings()
        try:
            self.local_server_controller.start()
            self.log_view.append_event(
                f"Local MCP/HTTP server started: {self.local_server_controller.endpoint_url()}"
            )
        except Exception as exc:
            self.local_server_enabled_checkbox.blockSignals(True)
            self.local_server_enabled_checkbox.setChecked(False)
            self.local_server_enabled_checkbox.blockSignals(False)
            self._save_settings()
            message = f"Could not start local server: {exc}"
            self.log_view.append_event(message)
            if show_errors:
                self._show_error(
                    self.tr("local_server_start_failed", "Local server failed"),
                    message,
                )
        self._refresh_local_server_status()

    def _stop_local_server(self) -> None:
        self.local_server_controller.stop()
        if hasattr(self, "local_server_enabled_checkbox"):
            self.local_server_enabled_checkbox.blockSignals(True)
            self.local_server_enabled_checkbox.setChecked(False)
            self.local_server_enabled_checkbox.blockSignals(False)
        self._save_settings()
        self.log_view.append_event("Local MCP/HTTP server stopped.")
        self._refresh_local_server_status()

    def _on_local_server_enabled_changed(self, enabled: bool) -> None:
        if enabled:
            self._start_local_server(show_errors=True)
        else:
            self._stop_local_server()

    def _on_local_server_field_changed(self) -> None:
        self._save_settings()
        self._refresh_local_server_status()

    def _generate_local_server_token(self) -> None:
        self.local_server_token_edit.setText(secrets.token_urlsafe(24))
        self._save_settings()
        self._refresh_local_server_status()

    def _toggle_local_server_token_visibility(self, checked: bool) -> None:
        if checked:
            self.local_server_token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.local_server_show_token_button.setText(self.tr("hide_token", "Hide"))
            self.local_server_show_token_button.setIcon(ui_icon("hide"))
        else:
            self.local_server_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.local_server_show_token_button.setText(self.tr("show_token", "Show"))
            self.local_server_show_token_button.setIcon(ui_icon("show"))

    def _copy_local_server_token(self) -> None:
        token = self.local_server_token_edit.text().strip()
        if not token:
            QMessageBox.information(
                self,
                self.tr("local_server_token", "Access token"),
                self.tr("no_token_to_copy", "There is no access token to copy yet."),
            )
            return
        QApplication.clipboard().setText(token)
        self.statusBar().showMessage(
            self.tr("token_copied", "Access token copied to clipboard."),
            3000,
        )

    def _refresh_local_server_status(self) -> None:
        if not hasattr(self, "local_server_status_label"):
            return
        running = self.local_server_controller.is_running()
        endpoint = self.local_server_controller.endpoint_url()
        self.local_server_endpoint_label.setText(endpoint)
        self.local_server_start_stop_button.setText(
            self.tr("stop_server", "Stop server")
            if running
            else self.tr("start_server", "Start server")
        )
        self.local_server_start_stop_button.setIcon(
            ui_icon("stop", danger=True) if running else ui_icon("server")
        )
        token_hint = (
            self.tr(
                "local_server_token_hint",
                "Use the access token as a Bearer token, or as ?token=... for file URLs.",
            )
            if self.local_server_token_edit.text().strip()
            else self.tr(
                "local_server_no_token_hint",
                "No token is configured. This is acceptable only for localhost experiments.",
            )
        )
        status = (
            self.tr("local_server_running", "Running")
            if running
            else self.tr("local_server_stopped", "Stopped")
        )
        self.local_server_status_label.setText(
            self.tr(
                "local_server_status_text",
                "Status: {status}. {token_hint}",
                status=status,
                token_hint=token_hint,
            )
        )

    def _build_tts_engine_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        group = QGroupBox(
            self.tr("voice_generation_engine", "Voice Generation Engine")
        )
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(12)

        self.tts_engine_combo = QComboBox()
        self._populate_tts_engine_combo()
        self.tts_engine_combo.setVisible(False)

        intro = QLabel(
            self.tr(
                "tts_models_intro",
                "Choose the voice generation engine here. Local engines can be "
                "installed or removed on demand; API engines only need their "
                "credentials below.",
            )
        )
        intro.setWordWrap(True)
        intro.setObjectName("helperLabel")
        group_layout.addWidget(intro)

        engine_actions = QHBoxLayout()
        engine_actions.setSpacing(8)
        self.add_custom_engine_button = QPushButton(
            self.tr("add_new_engine", "Add New Engine")
        )
        self.add_custom_engine_button.setIcon(ui_icon("add"))
        self.add_custom_engine_button.clicked.connect(self._add_custom_tts_engine)
        engine_actions.addStretch(1)
        engine_actions.addWidget(self.add_custom_engine_button)
        group_layout.addLayout(engine_actions)

        self.tts_engine_table = QTableWidget()
        self.tts_engine_table.setColumnCount(8)
        self.tts_engine_table.setHorizontalHeaderLabels(
            [
                self.tr("engine_table_type", "Type"),
                self.tr("engine_table_name", "Name"),
                self.tr("engine_table_speed", "Speed"),
                self.tr("engine_table_quality", "Quality"),
                self.tr("engine_table_gpu", "Needs GPU"),
                self.tr("engine_table_installed", "Installed"),
                self.tr("engine_table_selected", "Selected"),
                self.tr("engine_table_actions", "Actions"),
            ]
        )
        self.tts_engine_table.verticalHeader().setVisible(False)
        self.tts_engine_table.setAlternatingRowColors(True)
        self.tts_engine_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tts_engine_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tts_engine_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tts_engine_table.setMinimumHeight(245)
        header = self.tts_engine_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.tts_engine_table.cellClicked.connect(self._on_tts_engine_table_clicked)
        group_layout.addWidget(self.tts_engine_table)

        selected_title = QLabel(
            self.tr("selected_engine_parameters", "Selected engine parameters")
        )
        selected_title.setObjectName("sectionLabel")
        group_layout.addWidget(selected_title)

        self.engine_settings_stack = QStackedWidget()
        self.engine_stack_indexes: dict[str, int] = {}
        for engine_id, panel in (
            ("piper", self._build_piper_engine_panel()),
            ("kokoro", self._build_kokoro_python_engine_panel()),
            ("chatterbox", self._build_chatterbox_engine_panel()),
            ("qwen", self._build_qwen_engine_panel()),
            ("omnivoice", self._build_omnivoice_engine_panel()),
            ("openai", self._build_openai_engine_panel()),
            ("elevenlabs", self._build_elevenlabs_engine_panel()),
            ("gemini", self._build_gemini_engine_panel()),
            ("azure", self._build_azure_engine_panel()),
            ("custom", self._build_custom_engine_panel()),
        ):
            self.engine_stack_indexes[engine_id] = self.engine_settings_stack.addWidget(
                panel
            )
        group_layout.addWidget(self.engine_settings_stack)

        note = QLabel(
            self.tr(
                "api_key_local_note",
                "API keys are stored locally in config.json. Leave API engines "
                "empty unless you want to use that provider.",
            )
        )
        note.setWordWrap(True)
        note.setObjectName("helperLabel")
        group_layout.addWidget(note)
        self.tts_engine_combo.currentIndexChanged.connect(
            self._on_tts_engine_changed
        )
        self._refresh_tts_engine_table()
        layout.addWidget(group)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        self._refresh_mcp_desktop_json()
        return scroll

    def _build_piper_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.piper_path_edit = QLineEdit()
        self.piper_path_edit.setPlaceholderText("engines/piper/piper.exe")
        self.piper_path_edit.textChanged.connect(
            lambda _text: self._refresh_tts_engine_table()
        )
        form.addRow(
            self.tr("piper_executable", "Piper executable"),
            self.piper_path_edit,
        )
        helper = QLabel(
            self.tr(
                "piper_engine_help",
                "Free and offline. Uses local .onnx voices from the voices folder.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        form.addRow("", helper)
        return panel

    def _on_kokoro_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "kokoro_python_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if self.kokoro_python_preview_frame.isVisible():
                self._show_kokoro_python_preview_status(
                    self.tr(
                        "kokoro_preview_ready",
                        "Playing Kokoro preview.",
                    ),
                    busy=False,
                )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.kokoro_python_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_kokoro_python_preview_status)

    def _build_kokoro_python_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        self.kokoro_python_voice_combo = QComboBox()
        for voice in self.kokoro_python_manager.list_voices():
            self.kokoro_python_voice_combo.addItem(voice.display_name, voice.voice_id)
        form.addRow(
            self.tr("kokoro_voice", "Kokoro voice"),
            self.kokoro_python_voice_combo,
        )
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.kokoro_python_install_button = QPushButton(
            self.tr("install", "Install")
        )
        self.kokoro_python_install_button.setIcon(ui_icon("apply"))
        self.kokoro_python_install_button.clicked.connect(
            self._install_kokoro_python
        )
        self.kokoro_python_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.kokoro_python_remove_button.setIcon(ui_icon("delete"))
        self.kokoro_python_remove_button.clicked.connect(self._remove_kokoro_python)
        self.kokoro_python_test_button = QPushButton(
            self.tr("test_voice", "Test voice")
        )
        self.kokoro_python_test_button.setIcon(ui_icon("preview"))
        self.kokoro_python_test_button.clicked.connect(self._test_kokoro_python_voice)
        self.kokoro_python_load_button = QPushButton(
            self.tr("load_into_memory", "Load into memory")
        )
        self.kokoro_python_load_button.setIcon(ui_icon("open"))
        self.kokoro_python_load_button.clicked.connect(
            lambda _checked=False: self._toggle_preloaded_tts_engine("kokoro")
        )
        self.kokoro_python_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.kokoro_python_cancel_button.setIcon(ui_icon("cancel"))
        self.kokoro_python_cancel_button.setObjectName("secondaryButton")
        self.kokoro_python_cancel_button.clicked.connect(
            self._cancel_kokoro_python_operation
        )
        actions.addWidget(self.kokoro_python_install_button)
        actions.addWidget(self.kokoro_python_remove_button)
        actions.addWidget(self.kokoro_python_test_button)
        actions.addWidget(self.kokoro_python_load_button)
        actions.addWidget(self.kokoro_python_cancel_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.kokoro_python_progress_bar = QProgressBar()
        self.kokoro_python_progress_bar.setRange(0, 100)
        self.kokoro_python_progress_bar.setValue(0)
        self.kokoro_python_progress_bar.setVisible(False)
        layout.addWidget(self.kokoro_python_progress_bar)

        info_frame = QFrame()
        info_frame.setObjectName("inlineStatusFrame")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(2)
        self.kokoro_python_status_label = QLabel()
        self.kokoro_python_status_label.setObjectName("helperLabel")
        self.kokoro_python_status_label.setWordWrap(True)
        self.kokoro_python_path_label = QLabel()
        self.kokoro_python_path_label.setObjectName("helperLabel")
        self.kokoro_python_path_label.setWordWrap(True)
        info_layout.addWidget(self.kokoro_python_status_label)
        info_layout.addWidget(self.kokoro_python_path_label)
        layout.addWidget(info_frame)

        self.kokoro_python_preview_frame = QFrame()
        self.kokoro_python_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.kokoro_python_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.kokoro_python_preview_status_label = QLabel()
        self.kokoro_python_preview_status_label.setObjectName("helperLabel")
        self.kokoro_python_preview_bar = QProgressBar()
        self.kokoro_python_preview_bar.setRange(0, 0)
        self.kokoro_python_preview_bar.setTextVisible(False)
        self.kokoro_python_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.kokoro_python_preview_status_label, 1)
        preview_layout.addWidget(self.kokoro_python_preview_bar)
        self.kokoro_python_preview_frame.setVisible(False)
        layout.addWidget(self.kokoro_python_preview_frame)

        helper = QLabel(
            self.tr(
                "kokoro_help",
                "Local Kokoro engine. It automatically uses CUDA when the "
                "installed runtime and hardware support it, otherwise it uses CPU.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addWidget(helper)
        self._refresh_kokoro_python_status()
        return panel

    def _refresh_kokoro_python_status(self) -> None:
        if not hasattr(self, "kokoro_python_status_label"):
            return
        installed = self.kokoro_python_manager.is_installed()
        runtime_ready = self.kokoro_python_manager.has_runtime()
        operation_running = self.kokoro_python_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if installed
            else self.tr("not_installed", "Not installed")
        )
        if installed:
            manifest = self.kokoro_python_manager.runtime_dependency_manifest()
            backend = str(manifest.get("backend", "CPUExecutionProvider"))
            backend_label = "CUDA" if "CUDA" in backend else "CPU"
            model_name = self.kokoro_python_manager.model_path_for_provider(
                "auto"
            ).name
            status_text = f"{status_text} - {backend_label} ({model_name})"
        self.kokoro_python_status_label.setText(
            self.tr(
                "kokoro_status",
                "Kokoro status: {status}",
                status=status_text,
            )
        )
        self.kokoro_python_path_label.setText(
            self.tr(
                "kokoro_model_path",
                "Model path: {path}",
                path=str(self.kokoro_python_manager.install_dir),
            )
        )
        self.kokoro_python_install_button.setEnabled(
            not installed and not operation_running
        )
        self.kokoro_python_remove_button.setEnabled(
            self.kokoro_python_manager.install_dir.exists() and not operation_running
        )
        self.kokoro_python_test_button.setEnabled(
            installed
            and runtime_ready
            and self.kokoro_python_preview_thread is None
            and not operation_running
        )
        self._configure_preload_button(
            self.kokoro_python_load_button,
            "kokoro",
            installed and runtime_ready and not operation_running,
        )
        self.kokoro_python_cancel_button.setVisible(operation_running)
        self.kokoro_python_cancel_button.setEnabled(operation_running)
        self._refresh_tts_engine_table()

    def _install_kokoro_python(self) -> None:
        self._start_kokoro_python_operation("install")

    def _remove_kokoro_python(self) -> None:
        if self.preloaded_tts_engine_id == "kokoro":
            self._unload_preloaded_tts_engine()
        self._start_kokoro_python_operation("remove")

    def _cancel_kokoro_python_operation(self) -> None:
        if self.kokoro_python_worker is None:
            return
        self.kokoro_python_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.kokoro_python_worker.request_cancel()

    def _start_kokoro_python_operation(self, operation: str) -> None:
        if self.kokoro_python_thread is not None:
            return
        self.kokoro_python_progress_bar.setVisible(True)
        self.kokoro_python_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "kokoro_installing",
                "Kokoro operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = KokoroInstallWorker(KokoroPythonManager(), operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_kokoro_python_progress)
        worker.finished.connect(self._on_kokoro_python_finished)
        worker.failed.connect(self._on_kokoro_python_failed)
        worker.cancelled.connect(self._on_kokoro_python_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_kokoro_python_worker)
        self.kokoro_python_thread = thread
        self.kokoro_python_worker = worker
        self._refresh_kokoro_python_status()
        thread.start()

    def _on_kokoro_python_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        percentage = int((current / total) * 100) if total else 0
        self.kokoro_python_progress_bar.setValue(max(0, min(100, percentage)))
        self.kokoro_python_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_kokoro_python_finished(self, path: str) -> None:
        self.kokoro_python_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("kokoro_ready", "Kokoro ready: {path}", path=path)
        )

    def _on_kokoro_python_failed(self, message: str) -> None:
        self.kokoro_python_progress_bar.setVisible(False)
        self._hide_voice_preview_status()
        if (
            hasattr(self, "kokoro_python_preview_frame")
            and self.kokoro_python_preview_thread is not None
        ):
            self._hide_kokoro_python_preview_status()
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_kokoro_python_cancelled(self) -> None:
        self.kokoro_python_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr(
                "kokoro_cancelled",
                "Kokoro installation cancelled.",
            )
        )

    def _clear_kokoro_python_worker(self) -> None:
        self.kokoro_python_worker = None
        self.kokoro_python_thread = None
        self.kokoro_python_progress_bar.setVisible(False)
        self.kokoro_python_manager = KokoroPythonManager()
        self._refresh_kokoro_python_status()

    def _test_kokoro_python_voice(self) -> None:
        if self.kokoro_python_preview_thread is not None:
            return
        if not self.kokoro_python_manager.is_installed():
            self._show_error(
                self.tr("missing_voice", "No voice selected"),
                self.tr(
                    "kokoro_not_installed",
                    "Kokoro is not installed yet.",
                ),
            )
            return
        voice_id = str(self.kokoro_python_voice_combo.currentData() or "af_heart")
        lang = self._kokoro_python_language_for_voice(voice_id)
        thread = QThread(self)
        worker = KokoroPreviewWorker(
            KokoroPythonManager(),
            voice_id,
            lang,
            self.speed_spin.value(),
            kokoro_preview_text_for_language(lang),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_kokoro_python_preview_ready)
        worker.failed.connect(self._on_kokoro_python_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_kokoro_python_preview_worker)
        self.kokoro_python_preview_thread = thread
        self.kokoro_python_preview_worker = worker
        self.loaded_tts_engine_id = "kokoro"
        self._update_header_engine_label()
        self._refresh_kokoro_python_status()
        self._show_kokoro_python_preview_status(
            self.tr(
                "kokoro_preview_generating",
                "Generating Kokoro preview...",
            ),
            busy=True,
        )
        self.log_view.append_event(
            self.tr(
                "kokoro_preview_generating",
                "Generating Kokoro preview...",
            )
        )
        thread.start()

    def _on_kokoro_python_preview_ready(self, path: str) -> None:
        self._show_voice_preview_status(
            self.tr("voice_preview_playing", "Playing voice preview."),
            busy=False,
        )
        self._show_kokoro_python_preview_status(
            self.tr(
                "kokoro_preview_ready",
                "Playing Kokoro preview.",
            ),
            busy=False,
        )
        self.kokoro_sample_player.setSource(QUrl.fromLocalFile(path))
        self.kokoro_sample_player.play()
        self.log_view.append_event(
            self.tr(
                "kokoro_preview_ready",
                "Playing Kokoro preview.",
            )
        )

    def _show_kokoro_python_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "kokoro_python_preview_frame"):
            return
        self.kokoro_python_preview_status_label.setText(message)
        self.kokoro_python_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.kokoro_python_preview_bar.setValue(100)
        self.kokoro_python_preview_frame.setVisible(True)

    def _hide_kokoro_python_preview_status(self) -> None:
        if not hasattr(self, "kokoro_python_preview_frame"):
            return
        self.kokoro_python_preview_frame.setVisible(False)

    def _clear_kokoro_python_preview_worker(self) -> None:
        self.kokoro_python_preview_worker = None
        self.kokoro_python_preview_thread = None
        self.loaded_tts_engine_id = self.preloaded_tts_engine_id
        self._update_header_engine_label()
        if (
            hasattr(self, "kokoro_python_preview_frame")
            and self.kokoro_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_kokoro_python_preview_status)
        self._refresh_kokoro_python_status()

    def _kokoro_python_language_for_voice(self, voice_id: str) -> str:
        voice = next(
            (
                candidate
                for candidate in self.kokoro_python_manager.list_voices()
                if candidate.voice_id == voice_id
            ),
            None,
        )
        return voice.language if voice is not None else "en-us"

    def _build_chatterbox_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.chatterbox_status_label = QLabel()
        self.chatterbox_status_label.setObjectName("helperLabel")
        self.chatterbox_path_label = QLabel()
        self.chatterbox_path_label.setObjectName("helperLabel")
        self.chatterbox_path_label.setWordWrap(True)
        self.chatterbox_runtime_label = QLabel()
        self.chatterbox_runtime_label.setObjectName("helperLabel")
        self.chatterbox_runtime_label.setWordWrap(True)

        hardware_frame = QFrame()
        hardware_frame.setObjectName("inlineStatusFrame")
        hardware_layout = QVBoxLayout(hardware_frame)
        hardware_layout.setContentsMargins(12, 10, 12, 10)
        hardware_title_row = QHBoxLayout()
        hardware_title = QLabel(
            self.tr("hardware_acceleration", "Hardware acceleration")
        )
        hardware_title.setObjectName("sectionLabel")
        self.chatterbox_detect_gpu_button = QPushButton(
            self.tr("detect_gpu", "Detect GPU")
        )
        self.chatterbox_detect_gpu_button.setIcon(ui_icon("refresh"))
        self.chatterbox_detect_gpu_button.clicked.connect(
            self._detect_chatterbox_hardware
        )
        hardware_title_row.addWidget(hardware_title)
        hardware_title_row.addStretch(1)
        hardware_title_row.addWidget(self.chatterbox_detect_gpu_button)
        self.chatterbox_hardware_label = QLabel()
        self.chatterbox_hardware_label.setObjectName("helperLabel")
        self.chatterbox_hardware_label.setWordWrap(True)
        hardware_layout.addLayout(hardware_title_row)
        hardware_layout.addWidget(self.chatterbox_hardware_label)

        self.chatterbox_progress_bar = QProgressBar()
        self.chatterbox_progress_bar.setRange(0, 100)
        self.chatterbox_progress_bar.setValue(0)
        self.chatterbox_progress_bar.setVisible(False)

        form = QFormLayout()
        form.setSpacing(10)
        self.chatterbox_model_combo = QComboBox()
        for model in self.chatterbox_manager.list_models():
            self.chatterbox_model_combo.addItem(model.display_name, model.model_id)
        self.chatterbox_language_combo = QComboBox()
        for language in self.chatterbox_manager.list_languages():
            self.chatterbox_language_combo.addItem(
                language.display_name,
                language.language_id,
            )
        self.chatterbox_device_combo = QComboBox()
        self.chatterbox_device_combo.addItem("Auto (recommended)", "auto")
        self.chatterbox_device_combo.addItem("CUDA / NVIDIA GPU", "cuda")
        self.chatterbox_device_combo.addItem("CPU only", "cpu")
        self.chatterbox_device_combo.addItem("Apple MPS", "mps")
        self.chatterbox_reference_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr(
                "audio_files_filter",
                "Audio files (*.wav *.mp3 *.flac *.m4a);;All files (*.*)",
            ),
        )
        self.chatterbox_exaggeration_spin = self._ratio_spin(0.5)
        self.chatterbox_cfg_spin = self._ratio_spin(0.5)
        form.addRow(
            self.tr("chatterbox_model", "Chatterbox model"),
            self.chatterbox_model_combo,
        )
        form.addRow(
            self.tr("chatterbox_language", "Language"),
            self.chatterbox_language_combo,
        )
        form.addRow(
            self.tr("chatterbox_device", "Compute device"),
            self.chatterbox_device_combo,
        )
        form.addRow(
            self.tr("reference_audio", "Reference audio"),
            self.chatterbox_reference_picker,
        )
        form.addRow(
            self.tr("exaggeration", "Emotion exaggeration"),
            self.chatterbox_exaggeration_spin,
        )
        form.addRow(self.tr("cfg_weight", "CFG weight"), self.chatterbox_cfg_spin)

        actions = QHBoxLayout()
        self.chatterbox_install_button = QPushButton(self.tr("install", "Install"))
        self.chatterbox_install_button.setIcon(ui_icon("apply"))
        self.chatterbox_install_button.clicked.connect(self._install_chatterbox)
        self.chatterbox_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.chatterbox_remove_button.setIcon(ui_icon("delete"))
        self.chatterbox_remove_button.clicked.connect(self._remove_chatterbox)
        self.chatterbox_test_button = QPushButton(self.tr("test_voice", "Test voice"))
        self.chatterbox_test_button.setIcon(ui_icon("preview"))
        self.chatterbox_test_button.clicked.connect(self._test_chatterbox_voice)
        self.chatterbox_load_button = QPushButton(
            self.tr("load_into_memory", "Load into memory")
        )
        self.chatterbox_load_button.setIcon(ui_icon("open"))
        self.chatterbox_load_button.clicked.connect(
            lambda _checked=False: self._toggle_preloaded_tts_engine("chatterbox")
        )
        self.chatterbox_load_button.setToolTip(
            self.tr(
                "load_into_memory_help",
                "Prepared for a persistent Chatterbox runtime. The current engine "
                "runs as an external process per job.",
            )
        )
        self.chatterbox_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.chatterbox_cancel_button.setIcon(ui_icon("cancel"))
        self.chatterbox_cancel_button.setObjectName("secondaryButton")
        self.chatterbox_cancel_button.clicked.connect(
            self._cancel_chatterbox_operation
        )
        actions.addWidget(self.chatterbox_install_button)
        actions.addWidget(self.chatterbox_remove_button)
        actions.addWidget(self.chatterbox_test_button)
        actions.addWidget(self.chatterbox_load_button)
        actions.addWidget(self.chatterbox_cancel_button)
        actions.addStretch(1)

        self.chatterbox_preview_frame = QFrame()
        self.chatterbox_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.chatterbox_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.chatterbox_preview_status_label = QLabel()
        self.chatterbox_preview_status_label.setObjectName("helperLabel")
        self.chatterbox_preview_bar = QProgressBar()
        self.chatterbox_preview_bar.setRange(0, 0)
        self.chatterbox_preview_bar.setTextVisible(False)
        self.chatterbox_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.chatterbox_preview_status_label, 1)
        preview_layout.addWidget(self.chatterbox_preview_bar)
        self.chatterbox_preview_frame.setVisible(False)

        helper = QLabel(
            self.tr(
                "chatterbox_help",
                "Advanced local engine. Auto uses CUDA when available and "
                "falls back to CPU on normal PCs. CUDA/NVIDIA is recommended "
                "for speed, but not required.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addLayout(form)
        layout.addWidget(self.chatterbox_progress_bar)
        layout.addWidget(self.chatterbox_preview_frame)
        layout.addWidget(helper)
        layout.addWidget(self.chatterbox_status_label)
        layout.addWidget(self.chatterbox_path_label)
        layout.addWidget(self.chatterbox_runtime_label)
        layout.addWidget(hardware_frame)
        self._refresh_chatterbox_hardware_status()
        self._refresh_chatterbox_status()
        return panel

    def _refresh_chatterbox_hardware_status(self) -> None:
        if not hasattr(self, "chatterbox_hardware_label"):
            return
        self.chatterbox_hardware_label.setText(format_gpu_detection(detect_gpus()))

    def _detect_chatterbox_hardware(self) -> None:
        if self.chatterbox_hardware_thread is not None:
            return
        self.chatterbox_detect_gpu_button.setEnabled(False)
        self.chatterbox_hardware_label.setText(
            self.tr("detecting_gpu", "Detecting GPU and CUDA runtime...")
        )
        thread = QThread(self)
        worker = ChatterboxHardwareWorker(
            ChatterboxManager(),
            include_runtime=True,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_chatterbox_hardware_ready)
        worker.failed.connect(self._on_chatterbox_hardware_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_hardware_worker)
        self.chatterbox_hardware_thread = thread
        self.chatterbox_hardware_worker = worker
        self._refresh_chatterbox_status()
        thread.start()

    def _on_chatterbox_hardware_ready(self, message: str) -> None:
        self.chatterbox_hardware_label.setText(message)
        self.log_view.append_event(message.replace("\n", " | "))

    def _on_chatterbox_hardware_failed(self, message: str) -> None:
        self.chatterbox_hardware_label.setText(message)
        self.log_view.append_event(message)

    def _clear_chatterbox_hardware_worker(self) -> None:
        self.chatterbox_hardware_worker = None
        self.chatterbox_hardware_thread = None
        self._refresh_chatterbox_status()

    def _refresh_chatterbox_status(self) -> None:
        if not hasattr(self, "chatterbox_status_label"):
            return
        model_cache_installed = self.chatterbox_manager.is_installed()
        runtime_ready = self.chatterbox_manager.has_runtime()
        runtime_current = self.chatterbox_manager.runtime_is_current()
        ready = model_cache_installed
        operation_running = self.chatterbox_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if ready
            else self.tr("update_available", "Update available")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        runtime_status = (
            self.tr("installed", "Installed")
            if runtime_ready and runtime_current
            else self.tr("update_available", "Update available")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        self.chatterbox_status_label.setText(
            self.tr(
                "chatterbox_status",
                "Chatterbox status: {status}",
                status=status_text,
            )
        )
        self.chatterbox_path_label.setText(
            self.tr(
                "chatterbox_model_path",
                "Model cache: {path}",
                path=str(self.chatterbox_manager.cache_dir),
            )
        )
        self.chatterbox_runtime_label.setText(
            self.tr(
                "chatterbox_runtime_status",
                "Runtime: {status} ({path})",
                status=runtime_status,
                path=str(self.chatterbox_manager.runtime_path),
            )
        )
        self.chatterbox_install_button.setEnabled(not operation_running)
        self.chatterbox_remove_button.setEnabled(
            (runtime_ready or model_cache_installed) and not operation_running
        )
        self.chatterbox_test_button.setEnabled(
            ready
            and self.chatterbox_preview_thread is None
            and not operation_running
        )
        self.chatterbox_detect_gpu_button.setEnabled(
            self.chatterbox_hardware_thread is None and not operation_running
        )
        self.chatterbox_cancel_button.setVisible(operation_running)
        self.chatterbox_cancel_button.setEnabled(operation_running)
        if hasattr(self, "chatterbox_load_button"):
            self._configure_preload_button(
                self.chatterbox_load_button,
                "chatterbox",
                ready and not operation_running,
            )
            self.chatterbox_load_button.setToolTip(
                self.tr(
                    "load_into_memory_help",
                    "Load Chatterbox now and keep it waiting for generation jobs.",
                )
            )
        self._refresh_tts_engine_table()

    def _install_chatterbox(self) -> None:
        self._start_chatterbox_operation("install")

    def _remove_chatterbox(self) -> None:
        if self.preloaded_tts_engine_id == "chatterbox":
            self._unload_preloaded_tts_engine()
        self._start_chatterbox_operation("remove")

    def _cancel_chatterbox_operation(self) -> None:
        if self.chatterbox_worker is None:
            return
        self.chatterbox_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.chatterbox_worker.request_cancel()

    def _start_chatterbox_operation(self, operation: str) -> None:
        if self.chatterbox_thread is not None:
            return
        self.chatterbox_progress_bar.setVisible(True)
        self.chatterbox_progress_bar.setRange(0, 0 if operation == "install" else 100)
        self.chatterbox_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "chatterbox_installing",
                "Chatterbox operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = ChatterboxInstallWorker(
            ChatterboxManager(),
            operation,
            str(self.chatterbox_model_combo.currentData() or "multilingual_v3"),
            str(self.chatterbox_device_combo.currentData() or "auto"),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_chatterbox_progress)
        worker.finished.connect(self._on_chatterbox_finished)
        worker.failed.connect(self._on_chatterbox_failed)
        worker.cancelled.connect(self._on_chatterbox_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_worker)
        self.chatterbox_thread = thread
        self.chatterbox_worker = worker
        self._refresh_chatterbox_status()
        thread.start()

    def _on_chatterbox_progress(self, current: int, total: int, message: str) -> None:
        if total:
            self.chatterbox_progress_bar.setRange(0, 100)
            percentage = int((current / total) * 100)
            self.chatterbox_progress_bar.setValue(max(0, min(100, percentage)))
        self.chatterbox_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_chatterbox_finished(self, path: str) -> None:
        self.chatterbox_progress_bar.setRange(0, 100)
        self.chatterbox_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("chatterbox_ready", "Chatterbox ready: {path}", path=path)
        )

    def _on_chatterbox_failed(self, message: str) -> None:
        self.chatterbox_progress_bar.setVisible(False)
        self._hide_voice_preview_status()
        if (
            hasattr(self, "chatterbox_preview_frame")
            and self.chatterbox_preview_thread is not None
        ):
            self._hide_chatterbox_preview_status()
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_chatterbox_cancelled(self) -> None:
        self.chatterbox_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("chatterbox_cancelled", "Chatterbox operation cancelled.")
        )

    def _clear_chatterbox_worker(self) -> None:
        self.chatterbox_worker = None
        self.chatterbox_thread = None
        self.chatterbox_progress_bar.setVisible(False)
        self.chatterbox_manager = ChatterboxManager()
        self._refresh_chatterbox_status()

    def _test_chatterbox_voice(self) -> None:
        if self.chatterbox_preview_thread is not None:
            return
        voice_config = self._chatterbox_voice_config_for_ui()
        if voice_config is None:
            return
        thread = QThread(self)
        worker = ChatterboxPreviewWorker(
            ChatterboxManager(),
            voice_config,
            self._chatterbox_preview_text(
                str(voice_config.get("language", "en"))
            ),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_chatterbox_preview_ready)
        worker.failed.connect(self._on_chatterbox_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_preview_worker)
        self.chatterbox_preview_thread = thread
        self.chatterbox_preview_worker = worker
        self.loaded_tts_engine_id = "chatterbox"
        self._update_header_engine_label()
        self._refresh_chatterbox_status()
        self._show_chatterbox_preview_status(
            self.tr(
                "chatterbox_preview_generating",
                "Generating Chatterbox preview...",
            ),
            busy=True,
        )
        self.log_view.append_event(
            self.tr(
                "chatterbox_preview_generating",
                "Generating Chatterbox preview...",
            )
        )
        thread.start()

    def _on_chatterbox_preview_ready(self, path: str) -> None:
        self._show_voice_preview_status(
            self.tr("voice_preview_playing", "Playing voice preview."),
            busy=False,
        )
        self._show_chatterbox_preview_status(
            self.tr("chatterbox_preview_ready", "Playing Chatterbox preview."),
            busy=False,
        )
        self.chatterbox_sample_player.setSource(QUrl.fromLocalFile(path))
        self.chatterbox_sample_player.play()
        self.log_view.append_event(
            self.tr("chatterbox_preview_ready", "Playing Chatterbox preview.")
        )

    def _show_chatterbox_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        self.chatterbox_preview_status_label.setText(message)
        self.chatterbox_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.chatterbox_preview_bar.setValue(100)
        self.chatterbox_preview_frame.setVisible(True)

    def _hide_chatterbox_preview_status(self) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        self.chatterbox_preview_frame.setVisible(False)

    def _on_chatterbox_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "chatterbox_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_chatterbox_preview_status(
                self.tr("chatterbox_preview_ready", "Playing Chatterbox preview."),
                busy=False,
            )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.chatterbox_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_chatterbox_preview_status)

    def _clear_chatterbox_preview_worker(self) -> None:
        self.chatterbox_preview_worker = None
        self.chatterbox_preview_thread = None
        self.loaded_tts_engine_id = self.preloaded_tts_engine_id
        self._update_header_engine_label()
        if (
            hasattr(self, "chatterbox_preview_frame")
            and self.chatterbox_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_chatterbox_preview_status)
        self._refresh_chatterbox_status()

    def _chatterbox_voice_config_for_ui(self) -> dict[str, object] | None:
        if not self.chatterbox_manager.has_runtime():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "chatterbox_runtime_missing",
                    "Chatterbox Python dependencies are not installed yet. "
                    "Open Settings > TTS Engines and click Install.",
                ),
            )
            return None
        reference_path = self.chatterbox_reference_picker.path()
        reference_text = str(reference_path or "")
        if reference_path is not None and not reference_path.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "reference_audio_missing",
                    "Reference audio file not found: {path}",
                    path=reference_text,
                ),
            )
            return None
        model = str(self.chatterbox_model_combo.currentData() or "multilingual_v3")
        if model == "turbo" and reference_path is None:
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "chatterbox_turbo_reference_required",
                    "Chatterbox Turbo requires a 5-20 second reference audio file.",
                ),
            )
            return None
        return {
            "engine": "chatterbox",
            "speed": self.speed_spin.value(),
            "model": model,
            "language": self.chatterbox_language_combo.currentData() or "en",
            "device": self.chatterbox_device_combo.currentData() or "auto",
            "reference_audio_path": reference_text,
            "exaggeration": self.chatterbox_exaggeration_spin.value(),
            "cfg_weight": self.chatterbox_cfg_spin.value(),
            "cache_dir": str(self.chatterbox_manager.cache_dir),
        }

    @staticmethod
    def _chatterbox_preview_text(language: str) -> str:
        texts = {
            "de": "Der Mond ist heute Nacht wunderschon.",
            "en": "The moon looks beautiful tonight.",
            "es": "La luna esta preciosa esta noche.",
            "fr": "La lune est magnifique ce soir.",
            "it": "La luna e bellissima stasera.",
            "ja": "今夜の月はとてもきれいです。",
            "pt": "A lua esta linda esta noite.",
            "zh": "今晚的月亮很美。",
        }
        return texts.get(language.lower(), texts["en"])

    def _build_qwen_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.qwen_status_label = QLabel()
        self.qwen_status_label.setObjectName("helperLabel")
        self.qwen_path_label = QLabel()
        self.qwen_path_label.setObjectName("helperLabel")
        self.qwen_path_label.setWordWrap(True)
        self.qwen_runtime_label = QLabel()
        self.qwen_runtime_label.setObjectName("helperLabel")
        self.qwen_runtime_label.setWordWrap(True)

        hardware_frame = QFrame()
        hardware_frame.setObjectName("inlineStatusFrame")
        hardware_layout = QVBoxLayout(hardware_frame)
        hardware_layout.setContentsMargins(12, 10, 12, 10)
        hardware_title_row = QHBoxLayout()
        hardware_title = QLabel(
            self.tr("hardware_acceleration", "Hardware acceleration")
        )
        hardware_title.setObjectName("sectionLabel")
        self.qwen_detect_gpu_button = QPushButton(self.tr("detect_gpu", "Detect GPU"))
        self.qwen_detect_gpu_button.setIcon(ui_icon("refresh"))
        self.qwen_detect_gpu_button.clicked.connect(self._detect_qwen_hardware)
        hardware_title_row.addWidget(hardware_title)
        hardware_title_row.addStretch(1)
        hardware_title_row.addWidget(self.qwen_detect_gpu_button)
        self.qwen_hardware_label = QLabel()
        self.qwen_hardware_label.setObjectName("helperLabel")
        self.qwen_hardware_label.setWordWrap(True)
        hardware_layout.addLayout(hardware_title_row)
        hardware_layout.addWidget(self.qwen_hardware_label)

        self.qwen_progress_bar = QProgressBar()
        self.qwen_progress_bar.setRange(0, 100)
        self.qwen_progress_bar.setValue(0)
        self.qwen_progress_bar.setVisible(False)

        form = QFormLayout()
        form.setSpacing(10)
        self.qwen_model_combo = QComboBox()
        for model in self.qwen_manager.list_models():
            self.qwen_model_combo.addItem(model.display_name, model.model_id)
        self.qwen_language_combo = QComboBox()
        for language in self.qwen_manager.list_languages():
            self.qwen_language_combo.addItem(
                language.display_name,
                language.language_id,
            )
        self.qwen_speaker_combo = QComboBox()
        for voice in self.qwen_manager.list_voices():
            self.qwen_speaker_combo.addItem(voice.display_name, voice.voice_id)
        self.qwen_device_combo = QComboBox()
        self.qwen_device_combo.addItem("Auto (recommended)", "auto")
        self.qwen_device_combo.addItem("CUDA / NVIDIA GPU", "cuda")
        self.qwen_device_combo.addItem("CPU only", "cpu")
        self.qwen_dtype_combo = QComboBox()
        self.qwen_dtype_combo.addItem("Auto", "auto")
        self.qwen_dtype_combo.addItem("bfloat16", "bfloat16")
        self.qwen_dtype_combo.addItem("float16", "float16")
        self.qwen_dtype_combo.addItem("float32", "float32")
        self.qwen_instruct_edit = QLineEdit()
        self.qwen_instruct_edit.setPlaceholderText(
            self.tr(
                "qwen_instruct_placeholder",
                "Optional style: calm narrator, warm course voice...",
            )
        )
        form.addRow(self.tr("qwen_model", "Qwen3 model"), self.qwen_model_combo)
        form.addRow(self.tr("qwen_language", "Language"), self.qwen_language_combo)
        form.addRow(self.tr("qwen_speaker", "Speaker"), self.qwen_speaker_combo)
        form.addRow(self.tr("qwen_device", "Compute device"), self.qwen_device_combo)
        form.addRow(self.tr("qwen_dtype", "Precision"), self.qwen_dtype_combo)
        form.addRow(
            self.tr("qwen_instruct", "Voice instruction"),
            self.qwen_instruct_edit,
        )

        actions = QHBoxLayout()
        self.qwen_install_button = QPushButton(self.tr("install", "Install"))
        self.qwen_install_button.setIcon(ui_icon("apply"))
        self.qwen_install_button.clicked.connect(self._install_qwen)
        self.qwen_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.qwen_remove_button.setIcon(ui_icon("delete"))
        self.qwen_remove_button.clicked.connect(self._remove_qwen)
        self.qwen_test_button = QPushButton(self.tr("test_voice", "Test voice"))
        self.qwen_test_button.setIcon(ui_icon("preview"))
        self.qwen_test_button.clicked.connect(self._test_qwen_voice)
        self.qwen_load_button = QPushButton(
            self.tr("load_into_memory", "Load into memory")
        )
        self.qwen_load_button.setIcon(ui_icon("open"))
        self.qwen_load_button.clicked.connect(
            lambda _checked=False: self._toggle_preloaded_tts_engine("qwen")
        )
        self.qwen_load_button.setToolTip(
            self.tr(
                "qwen_load_into_memory_help",
                "Qwen3 TTS loads once at the start of a generation job and "
                "stays in memory while blocks are rendered.",
            )
        )
        self.qwen_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.qwen_cancel_button.setIcon(ui_icon("cancel"))
        self.qwen_cancel_button.setObjectName("secondaryButton")
        self.qwen_cancel_button.clicked.connect(self._cancel_qwen_operation)
        actions.addWidget(self.qwen_install_button)
        actions.addWidget(self.qwen_remove_button)
        actions.addWidget(self.qwen_test_button)
        actions.addWidget(self.qwen_load_button)
        actions.addWidget(self.qwen_cancel_button)
        actions.addStretch(1)

        self.qwen_preview_frame = QFrame()
        self.qwen_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.qwen_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.qwen_preview_status_label = QLabel()
        self.qwen_preview_status_label.setObjectName("helperLabel")
        self.qwen_preview_bar = QProgressBar()
        self.qwen_preview_bar.setRange(0, 0)
        self.qwen_preview_bar.setTextVisible(False)
        self.qwen_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.qwen_preview_status_label, 1)
        preview_layout.addWidget(self.qwen_preview_bar)
        self.qwen_preview_frame.setVisible(False)

        helper = QLabel(
            self.tr(
                "qwen_help",
                "Advanced local neural TTS. The app downloads Qwen3 TTS on "
                "demand into the local data folder and uses CUDA automatically "
                "when the embedded runtime can see an NVIDIA GPU.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addLayout(form)
        layout.addWidget(self.qwen_progress_bar)
        layout.addWidget(self.qwen_preview_frame)
        layout.addWidget(helper)
        layout.addWidget(self.qwen_status_label)
        layout.addWidget(self.qwen_path_label)
        layout.addWidget(self.qwen_runtime_label)
        layout.addWidget(hardware_frame)
        self._refresh_qwen_hardware_status()
        self._refresh_qwen_status()
        return panel

    def _refresh_qwen_hardware_status(self) -> None:
        if not hasattr(self, "qwen_hardware_label"):
            return
        self.qwen_hardware_label.setText(format_gpu_detection(detect_gpus()))

    def _detect_qwen_hardware(self) -> None:
        if self.qwen_hardware_thread is not None:
            return
        self.qwen_detect_gpu_button.setEnabled(False)
        self.qwen_hardware_label.setText(
            self.tr("detecting_gpu", "Detecting GPU and CUDA runtime...")
        )
        thread = QThread(self)
        worker = QwenHardwareWorker(
            QwenManager(),
            include_runtime=True,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_qwen_hardware_ready)
        worker.failed.connect(self._on_qwen_hardware_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_qwen_hardware_worker)
        self.qwen_hardware_thread = thread
        self.qwen_hardware_worker = worker
        self._refresh_qwen_status()
        thread.start()

    def _on_qwen_hardware_ready(self, message: str) -> None:
        self.qwen_hardware_label.setText(message)
        self.log_view.append_event(message.replace("\n", " | "))

    def _on_qwen_hardware_failed(self, message: str) -> None:
        self.qwen_hardware_label.setText(message)
        self.log_view.append_event(message)

    def _clear_qwen_hardware_worker(self) -> None:
        self.qwen_hardware_worker = None
        self.qwen_hardware_thread = None
        self._refresh_qwen_status()

    def _refresh_qwen_status(self) -> None:
        if not hasattr(self, "qwen_status_label"):
            return
        installed = self.qwen_manager.is_installed()
        runtime_ready = self.qwen_manager.has_runtime()
        operation_running = self.qwen_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if installed
            else self.tr("not_installed", "Not installed")
        )
        runtime_status = (
            self.tr("installed", "Installed")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        self.qwen_status_label.setText(
            self.tr("qwen_status", "Qwen3 TTS status: {status}", status=status_text)
        )
        self.qwen_path_label.setText(
            self.tr(
                "qwen_model_path",
                "Model cache: {path}",
                path=str(self.qwen_manager.cache_dir),
            )
        )
        self.qwen_runtime_label.setText(
            self.tr(
                "qwen_runtime_status",
                "Runtime: {status}",
                status=runtime_status,
            )
        )
        self.qwen_install_button.setEnabled(not operation_running)
        self.qwen_remove_button.setEnabled(
            (runtime_ready or self.qwen_manager.install_dir.exists())
            and not operation_running
        )
        self.qwen_test_button.setEnabled(
            installed
            and self.qwen_preview_thread is None
            and not operation_running
        )
        self.qwen_detect_gpu_button.setEnabled(
            self.qwen_hardware_thread is None and not operation_running
        )
        self.qwen_cancel_button.setVisible(operation_running)
        self.qwen_cancel_button.setEnabled(operation_running)
        if hasattr(self, "qwen_load_button"):
            self._configure_preload_button(
                self.qwen_load_button,
                "qwen",
                installed and runtime_ready and not operation_running,
            )
        self._refresh_tts_engine_table()

    def _install_qwen(self) -> None:
        self._start_qwen_operation("install")

    def _remove_qwen(self) -> None:
        if self.preloaded_tts_engine_id == "qwen":
            self._unload_preloaded_tts_engine()
        self._start_qwen_operation("remove")

    def _cancel_qwen_operation(self) -> None:
        if self.qwen_worker is None:
            return
        self.qwen_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.qwen_worker.request_cancel()

    def _start_qwen_operation(self, operation: str) -> None:
        if self.qwen_thread is not None:
            return
        self.qwen_progress_bar.setVisible(True)
        self.qwen_progress_bar.setRange(0, 0 if operation == "install" else 100)
        self.qwen_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "qwen_installing",
                "Qwen3 TTS operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = QwenInstallWorker(
            QwenManager(),
            operation,
            str(self.qwen_model_combo.currentData() or "custom_voice_0_6b"),
            str(self.qwen_device_combo.currentData() or "auto"),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_qwen_progress)
        worker.finished.connect(self._on_qwen_finished)
        worker.failed.connect(self._on_qwen_failed)
        worker.cancelled.connect(self._on_qwen_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_qwen_worker)
        self.qwen_thread = thread
        self.qwen_worker = worker
        self._refresh_qwen_status()
        thread.start()

    def _on_qwen_progress(self, current: int, total: int, message: str) -> None:
        if total:
            self.qwen_progress_bar.setRange(0, 100)
            percentage = int((current / total) * 100)
            self.qwen_progress_bar.setValue(max(0, min(100, percentage)))
        self.qwen_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_qwen_finished(self, path: str) -> None:
        self.qwen_progress_bar.setRange(0, 100)
        self.qwen_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("qwen_ready", "Qwen3 TTS ready: {path}", path=path)
        )

    def _on_qwen_failed(self, message: str) -> None:
        self.qwen_progress_bar.setVisible(False)
        self._hide_voice_preview_status()
        if (
            hasattr(self, "qwen_preview_frame")
            and self.qwen_preview_thread is not None
        ):
            self._hide_qwen_preview_status()
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_qwen_cancelled(self) -> None:
        self.qwen_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("qwen_cancelled", "Qwen3 TTS operation cancelled.")
        )

    def _clear_qwen_worker(self) -> None:
        self.qwen_worker = None
        self.qwen_thread = None
        self.qwen_progress_bar.setVisible(False)
        self.qwen_manager = QwenManager()
        self._refresh_qwen_status()

    def _test_qwen_voice(self) -> None:
        if self.qwen_preview_thread is not None:
            return
        voice_config = self._qwen_voice_config_for_ui()
        if voice_config is None:
            return
        thread = QThread(self)
        worker = QwenPreviewWorker(
            QwenManager(),
            voice_config,
            self._qwen_preview_text(str(voice_config.get("language", "Spanish"))),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_qwen_preview_ready)
        worker.failed.connect(self._on_qwen_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_qwen_preview_worker)
        self.qwen_preview_thread = thread
        self.qwen_preview_worker = worker
        self.loaded_tts_engine_id = "qwen"
        self._update_header_engine_label()
        self._refresh_qwen_status()
        self._show_qwen_preview_status(
            self.tr("qwen_preview_generating", "Generating Qwen3 TTS preview..."),
            busy=True,
        )
        self.log_view.append_event(
            self.tr("qwen_preview_generating", "Generating Qwen3 TTS preview...")
        )
        thread.start()

    def _on_qwen_preview_ready(self, path: str) -> None:
        self._show_voice_preview_status(
            self.tr("voice_preview_playing", "Playing voice preview."),
            busy=False,
        )
        self._show_qwen_preview_status(
            self.tr("qwen_preview_ready", "Playing Qwen3 TTS preview."),
            busy=False,
        )
        self.qwen_sample_player.setSource(QUrl.fromLocalFile(path))
        self.qwen_sample_player.play()
        self.log_view.append_event(
            self.tr("qwen_preview_ready", "Playing Qwen3 TTS preview.")
        )

    def _show_qwen_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "qwen_preview_frame"):
            return
        self.qwen_preview_status_label.setText(message)
        self.qwen_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.qwen_preview_bar.setValue(100)
        self.qwen_preview_frame.setVisible(True)

    def _hide_qwen_preview_status(self) -> None:
        if not hasattr(self, "qwen_preview_frame"):
            return
        self.qwen_preview_frame.setVisible(False)

    def _on_qwen_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "qwen_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_qwen_preview_status(
                self.tr("qwen_preview_ready", "Playing Qwen3 TTS preview."),
                busy=False,
            )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.qwen_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_qwen_preview_status)

    def _clear_qwen_preview_worker(self) -> None:
        self.qwen_preview_worker = None
        self.qwen_preview_thread = None
        self.loaded_tts_engine_id = self.preloaded_tts_engine_id
        self._update_header_engine_label()
        if (
            hasattr(self, "qwen_preview_frame")
            and self.qwen_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_qwen_preview_status)
        self._refresh_qwen_status()

    def _qwen_voice_config_for_ui(self) -> dict[str, object] | None:
        if not self.qwen_manager.is_installed():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "qwen_runtime_missing",
                    "Qwen3 TTS is not installed yet. Open Settings > TTS Engines "
                    "and click Install.",
                ),
            )
            return None
        model = str(self.qwen_model_combo.currentData() or "custom_voice_0_6b")
        return {
            "engine": "qwen",
            "speed": self.speed_spin.value(),
            "model": model,
            "model_repo": self.qwen_manager.model_repo(model),
            "language": self.qwen_language_combo.currentData() or "Spanish",
            "speaker": self.qwen_speaker_combo.currentData() or "Serena",
            "device": self.qwen_device_combo.currentData() or "auto",
            "dtype": self.qwen_dtype_combo.currentData() or "auto",
            "instruct": self.qwen_instruct_edit.text().strip(),
            "cache_dir": str(self.qwen_manager.cache_dir),
        }

    @staticmethod
    def _qwen_preview_text(language: str) -> str:
        texts = {
            "Chinese": "今晚的月亮很美。",
            "English": "The moon looks beautiful tonight.",
            "French": "La lune est magnifique ce soir.",
            "German": "Der Mond ist heute Nacht wunderschon.",
            "Italian": "La luna e bellissima stasera.",
            "Japanese": "今夜の月はとてもきれいです。",
            "Korean": "오늘 밤 달이 정말 아름다워요.",
            "Portuguese": "A lua esta linda esta noite.",
            "Russian": "Луна сегодня ночью прекрасна.",
            "Spanish": "La luna esta preciosa esta noche.",
        }
        return texts.get(language, texts["English"])

    def _build_omnivoice_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        self.omnivoice_status_label = QLabel()
        self.omnivoice_status_label.setObjectName("helperLabel")
        self.omnivoice_path_label = QLabel()
        self.omnivoice_path_label.setObjectName("helperLabel")
        self.omnivoice_path_label.setWordWrap(True)
        self.omnivoice_runtime_label = QLabel()
        self.omnivoice_runtime_label.setObjectName("helperLabel")
        self.omnivoice_runtime_label.setWordWrap(True)

        hardware_frame = QFrame()
        hardware_frame.setObjectName("inlineStatusFrame")
        hardware_layout = QVBoxLayout(hardware_frame)
        hardware_layout.setContentsMargins(12, 10, 12, 10)
        hardware_title_row = QHBoxLayout()
        hardware_title = QLabel(
            self.tr("hardware_acceleration", "Hardware acceleration")
        )
        hardware_title.setObjectName("sectionLabel")
        self.omnivoice_detect_gpu_button = QPushButton(
            self.tr("detect_gpu", "Detect GPU")
        )
        self.omnivoice_detect_gpu_button.setIcon(ui_icon("refresh"))
        self.omnivoice_detect_gpu_button.clicked.connect(
            self._detect_omnivoice_hardware
        )
        hardware_title_row.addWidget(hardware_title)
        hardware_title_row.addStretch(1)
        hardware_title_row.addWidget(self.omnivoice_detect_gpu_button)
        self.omnivoice_hardware_label = QLabel()
        self.omnivoice_hardware_label.setObjectName("helperLabel")
        self.omnivoice_hardware_label.setWordWrap(True)
        hardware_layout.addLayout(hardware_title_row)
        hardware_layout.addWidget(self.omnivoice_hardware_label)

        self.omnivoice_progress_bar = QProgressBar()
        self.omnivoice_progress_bar.setRange(0, 100)
        self.omnivoice_progress_bar.setValue(0)
        self.omnivoice_progress_bar.setVisible(False)

        form = QFormLayout()
        form.setSpacing(10)
        self.omnivoice_model_combo = QComboBox()
        for model in self.omnivoice_manager.list_models():
            self.omnivoice_model_combo.addItem(model.display_name, model.model_id)
        self.omnivoice_mode_combo = QComboBox()
        self.omnivoice_mode_combo.addItem("Voice cloning", "clone")
        self.omnivoice_mode_combo.currentIndexChanged.connect(
            lambda _index: (
                self._refresh_generation_voice_combo(),
                self._refresh_voices_page()
                if hasattr(self, "page_stack") and self.page_stack.currentIndex() == 5
                else None,
            )
        )
        self.omnivoice_language_combo = QComboBox()
        for label, value in (
            ("Auto", "auto"),
            ("English", "English"),
            ("Spanish", "Spanish"),
            ("French", "French"),
            ("German", "German"),
            ("Italian", "Italian"),
            ("Portuguese", "Portuguese"),
            ("Chinese", "Chinese"),
            ("Japanese", "Japanese"),
            ("Korean", "Korean"),
        ):
            self.omnivoice_language_combo.addItem(label, value)
        self.omnivoice_device_combo = QComboBox()
        self.omnivoice_device_combo.addItem("Auto (recommended)", "auto")
        self.omnivoice_device_combo.addItem("CUDA / NVIDIA GPU", "cuda")
        self.omnivoice_device_combo.addItem("CPU only", "cpu")
        self.omnivoice_dtype_combo = QComboBox()
        self.omnivoice_dtype_combo.addItem("Auto", "auto")
        self.omnivoice_dtype_combo.addItem("float16", "float16")
        self.omnivoice_dtype_combo.addItem("bfloat16", "bfloat16")
        self.omnivoice_dtype_combo.addItem("float32", "float32")
        self.omnivoice_instruct_edit = QLineEdit()
        self.omnivoice_instruct_edit.setPlaceholderText(
            self.tr(
                "omnivoice_instruct_placeholder",
                "Example: female, young adult, moderate pitch",
            )
        )
        self.omnivoice_reference_picker = FilePicker(
            self.tr("browse", "Browse"),
            "Audio files (*.wav *.mp3 *.flac *.m4a);;All files (*.*)",
        )
        self.omnivoice_reference_text_edit = QTextEdit()
        self.omnivoice_reference_text_edit.setMaximumHeight(70)
        self.omnivoice_reference_text_edit.setPlaceholderText(
            self.tr(
                "omnivoice_reference_text_placeholder",
                "Optional transcript for the reference audio.",
            )
        )
        self.omnivoice_num_step_spin = QSpinBox()
        self.omnivoice_num_step_spin.setRange(8, 64)
        self.omnivoice_num_step_spin.setSingleStep(4)
        self.omnivoice_num_step_spin.setValue(32)
        self.omnivoice_engine_speed_spin = QDoubleSpinBox()
        self.omnivoice_engine_speed_spin.setRange(0.25, 4.0)
        self.omnivoice_engine_speed_spin.setSingleStep(0.05)
        self.omnivoice_engine_speed_spin.setDecimals(2)
        self.omnivoice_engine_speed_spin.setValue(1.0)
        self.omnivoice_duration_spin = QDoubleSpinBox()
        self.omnivoice_duration_spin.setRange(0.0, 180.0)
        self.omnivoice_duration_spin.setSingleStep(0.5)
        self.omnivoice_duration_spin.setDecimals(1)
        self.omnivoice_duration_spin.setSuffix(" s")
        self.omnivoice_duration_spin.setValue(0.0)
        form.addRow(
            self.tr("omnivoice_model", "OmniVoice model"),
            self.omnivoice_model_combo,
        )
        form.addRow(self.tr("language", "Language"), self.omnivoice_language_combo)
        form.addRow(
            self.tr("omnivoice_device", "Compute device"),
            self.omnivoice_device_combo,
        )
        form.addRow(
            self.tr("omnivoice_dtype", "Precision"),
            self.omnivoice_dtype_combo,
        )
        form.addRow(
            self.tr("omnivoice_reference_audio", "Reference audio"),
            self.omnivoice_reference_picker,
        )
        form.addRow(
            self.tr("omnivoice_reference_text", "Reference transcript"),
            self.omnivoice_reference_text_edit,
        )
        form.addRow(
            self.tr("omnivoice_num_step", "Diffusion steps"),
            self.omnivoice_num_step_spin,
        )
        form.addRow(
            self.tr("omnivoice_speed", "Engine speed"),
            self.omnivoice_engine_speed_spin,
        )
        form.addRow(
            self.tr("omnivoice_duration", "Fixed duration"),
            self.omnivoice_duration_spin,
        )

        actions = QHBoxLayout()
        self.omnivoice_install_button = QPushButton(self.tr("install", "Install"))
        self.omnivoice_install_button.setIcon(ui_icon("apply"))
        self.omnivoice_install_button.clicked.connect(self._install_omnivoice)
        self.omnivoice_remove_button = QPushButton(self.tr("remove", "Remove"))
        self.omnivoice_remove_button.setIcon(ui_icon("delete"))
        self.omnivoice_remove_button.clicked.connect(self._remove_omnivoice)
        self.omnivoice_test_button = QPushButton(self.tr("test_voice", "Test voice"))
        self.omnivoice_test_button.setIcon(ui_icon("preview"))
        self.omnivoice_test_button.clicked.connect(self._test_omnivoice_voice)
        self.omnivoice_load_button = QPushButton(
            self.tr("load_into_memory", "Load into memory")
        )
        self.omnivoice_load_button.setIcon(ui_icon("open"))
        self.omnivoice_load_button.clicked.connect(
            lambda _checked=False: self._toggle_preloaded_tts_engine("omnivoice")
        )
        self.omnivoice_cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        self.omnivoice_cancel_button.setIcon(ui_icon("cancel"))
        self.omnivoice_cancel_button.setObjectName("secondaryButton")
        self.omnivoice_cancel_button.clicked.connect(self._cancel_omnivoice_operation)
        actions.addWidget(self.omnivoice_install_button)
        actions.addWidget(self.omnivoice_remove_button)
        actions.addWidget(self.omnivoice_test_button)
        actions.addWidget(self.omnivoice_load_button)
        actions.addWidget(self.omnivoice_cancel_button)
        actions.addStretch(1)

        self.omnivoice_preview_frame = QFrame()
        self.omnivoice_preview_frame.setObjectName("inlineStatusFrame")
        preview_layout = QHBoxLayout(self.omnivoice_preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(10)
        self.omnivoice_preview_status_label = QLabel()
        self.omnivoice_preview_status_label.setObjectName("helperLabel")
        self.omnivoice_preview_bar = QProgressBar()
        self.omnivoice_preview_bar.setRange(0, 0)
        self.omnivoice_preview_bar.setTextVisible(False)
        self.omnivoice_preview_bar.setFixedWidth(140)
        preview_layout.addWidget(self.omnivoice_preview_status_label, 1)
        preview_layout.addWidget(self.omnivoice_preview_bar)
        self.omnivoice_preview_frame.setVisible(False)

        helper = QLabel(
            self.tr(
                "omnivoice_help",
                "OmniVoice is a multilingual local TTS engine. LocalText2Voice "
                "uses it in voice cloning mode with a reference voice from the "
                "voice gallery.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        layout.addLayout(form)
        layout.addWidget(self.omnivoice_progress_bar)
        layout.addWidget(self.omnivoice_preview_frame)
        layout.addWidget(helper)
        layout.addWidget(self.omnivoice_status_label)
        layout.addWidget(self.omnivoice_path_label)
        layout.addWidget(self.omnivoice_runtime_label)
        layout.addWidget(hardware_frame)
        self._refresh_omnivoice_hardware_status()
        self._refresh_omnivoice_status()
        return panel

    def _refresh_omnivoice_hardware_status(self) -> None:
        if not hasattr(self, "omnivoice_hardware_label"):
            return
        self.omnivoice_hardware_label.setText(format_gpu_detection(detect_gpus()))

    def _detect_omnivoice_hardware(self) -> None:
        if self.omnivoice_hardware_thread is not None:
            return
        self.omnivoice_detect_gpu_button.setEnabled(False)
        self.omnivoice_hardware_label.setText(
            self.tr("detecting_gpu", "Detecting GPU and CUDA runtime...")
        )
        thread = QThread(self)
        worker = OmniVoiceHardwareWorker(
            OmniVoiceManager(),
            include_runtime=True,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_omnivoice_hardware_ready)
        worker.failed.connect(self._on_omnivoice_hardware_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_omnivoice_hardware_worker)
        self.omnivoice_hardware_thread = thread
        self.omnivoice_hardware_worker = worker
        self._refresh_omnivoice_status()
        thread.start()

    def _on_omnivoice_hardware_ready(self, message: str) -> None:
        self.omnivoice_hardware_label.setText(message)
        self.log_view.append_event(message.replace("\n", " | "))

    def _on_omnivoice_hardware_failed(self, message: str) -> None:
        self.omnivoice_hardware_label.setText(message)
        self.log_view.append_event(message)

    def _clear_omnivoice_hardware_worker(self) -> None:
        self.omnivoice_hardware_worker = None
        self.omnivoice_hardware_thread = None
        self._refresh_omnivoice_status()

    def _refresh_omnivoice_status(self) -> None:
        if not hasattr(self, "omnivoice_status_label"):
            return
        installed = self.omnivoice_manager.is_installed()
        runtime_ready = self.omnivoice_manager.has_runtime()
        operation_running = self.omnivoice_thread is not None
        status_text = (
            self.tr("installed", "Installed")
            if installed
            else self.tr("not_installed", "Not installed")
        )
        runtime_status = (
            self.tr("installed", "Installed")
            if runtime_ready
            else self.tr("not_installed", "Not installed")
        )
        self.omnivoice_status_label.setText(
            self.tr(
                "omnivoice_status",
                "OmniVoice status: {status}",
                status=status_text,
            )
        )
        self.omnivoice_path_label.setText(
            self.tr(
                "omnivoice_model_path",
                "Model cache: {path}",
                path=str(self.omnivoice_manager.cache_dir),
            )
        )
        self.omnivoice_runtime_label.setText(
            self.tr(
                "omnivoice_runtime_status",
                "Runtime: {status}",
                status=runtime_status,
            )
        )
        self.omnivoice_install_button.setEnabled(not operation_running)
        self.omnivoice_remove_button.setEnabled(
            (runtime_ready or self.omnivoice_manager.install_dir.exists())
            and not operation_running
        )
        self.omnivoice_test_button.setEnabled(
            installed
            and self.omnivoice_preview_thread is None
            and not operation_running
        )
        self.omnivoice_detect_gpu_button.setEnabled(
            self.omnivoice_hardware_thread is None and not operation_running
        )
        self.omnivoice_cancel_button.setVisible(operation_running)
        self.omnivoice_cancel_button.setEnabled(operation_running)
        self._configure_preload_button(
            self.omnivoice_load_button,
            "omnivoice",
            installed and runtime_ready and not operation_running,
        )
        self._refresh_tts_engine_table()

    def _ensure_default_omnivoice_reference(
        self,
        *,
        allow_sync: bool = False,
    ) -> bool:
        if not hasattr(self, "omnivoice_reference_picker"):
            return False
        current_path = self.omnivoice_reference_picker.path()
        if current_path is not None and current_path.is_file():
            return True

        voice_id = "omnivoice_en_harold_storyteller"
        voice = self.voice_gallery_manager.get_voice(voice_id)
        if voice is None and allow_sync:
            try:
                self.voice_gallery_manager.sync()
                voice = self.voice_gallery_manager.get_voice(voice_id)
            except Exception as exc:
                self.log_view.append_event(
                    "Could not sync OmniVoice default reference voice: "
                    f"{exc}"
                )
        if voice is None:
            return False

        try:
            audio_path = self.voice_gallery_manager.ensure_voice_audio(voice)
        except Exception as exc:
            self.log_view.append_event(
                f"Could not prepare OmniVoice default reference voice: {exc}"
            )
            return False
        if audio_path is None or not audio_path.is_file():
            return False

        self.omnivoice_reference_picker.set_path(audio_path)
        self.omnivoice_reference_text_edit.setPlainText(voice.ref_text)
        self._select_combo_data(self.omnivoice_mode_combo, "clone")
        self.log_view.append_event(
            "OmniVoice default reference voice ready: "
            f"{voice.name} ({audio_path})"
        )
        return True

    def _install_omnivoice(self) -> None:
        self._start_omnivoice_operation("install")

    def _remove_omnivoice(self) -> None:
        if self.preloaded_tts_engine_id == "omnivoice":
            self._unload_preloaded_tts_engine()
        self._start_omnivoice_operation("remove")

    def _cancel_omnivoice_operation(self) -> None:
        if self.omnivoice_worker is None:
            return
        self.omnivoice_cancel_button.setEnabled(False)
        self.log_view.append_event(self.tr("cancelling", "Cancelling generation..."))
        self.omnivoice_worker.request_cancel()

    def _start_omnivoice_operation(self, operation: str) -> None:
        if self.omnivoice_thread is not None:
            return
        self.omnivoice_progress_bar.setVisible(True)
        self.omnivoice_progress_bar.setRange(0, 0 if operation == "install" else 100)
        self.omnivoice_progress_bar.setValue(0)
        self.log_view.append_event(
            self.tr(
                "omnivoice_installing",
                "OmniVoice operation started: {operation}",
                operation=operation,
            )
        )
        thread = QThread(self)
        worker = OmniVoiceInstallWorker(
            OmniVoiceManager(),
            operation,
            str(self.omnivoice_model_combo.currentData() or "omnivoice"),
            str(self.omnivoice_device_combo.currentData() or "auto"),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_omnivoice_progress)
        worker.finished.connect(self._on_omnivoice_finished)
        worker.failed.connect(self._on_omnivoice_failed)
        worker.cancelled.connect(self._on_omnivoice_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_omnivoice_worker)
        self.omnivoice_thread = thread
        self.omnivoice_worker = worker
        self._refresh_omnivoice_status()
        thread.start()

    def _on_omnivoice_progress(self, current: int, total: int, message: str) -> None:
        if total:
            self.omnivoice_progress_bar.setRange(0, 100)
            percentage = int((current / total) * 100)
            self.omnivoice_progress_bar.setValue(max(0, min(100, percentage)))
        self.omnivoice_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_omnivoice_finished(self, path: str) -> None:
        self.omnivoice_progress_bar.setRange(0, 100)
        self.omnivoice_progress_bar.setValue(100)
        self.log_view.append_event(
            self.tr("omnivoice_ready", "OmniVoice ready: {path}", path=path)
        )
        if self._ensure_default_omnivoice_reference(allow_sync=True):
            self._save_settings()
        self._continue_pending_installer_setup()

    def _on_omnivoice_failed(self, message: str) -> None:
        self.omnivoice_progress_bar.setVisible(False)
        self._hide_voice_preview_status()
        if (
            hasattr(self, "omnivoice_preview_frame")
            and self.omnivoice_preview_thread is not None
        ):
            self._hide_omnivoice_preview_status()
        self.log_view.append_event(message)
        self._abort_pending_installer_setup(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_omnivoice_cancelled(self) -> None:
        self.omnivoice_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("omnivoice_cancelled", "OmniVoice operation cancelled.")
        )
        self._abort_pending_installer_setup("OmniVoice operation cancelled.")

    def _clear_omnivoice_worker(self) -> None:
        self.omnivoice_worker = None
        self.omnivoice_thread = None
        self.omnivoice_progress_bar.setVisible(False)
        self.omnivoice_manager = OmniVoiceManager()
        self._refresh_omnivoice_status()

    def _test_omnivoice_voice(self) -> None:
        if self.omnivoice_preview_thread is not None:
            return
        voice_config = self._omnivoice_voice_config_for_ui()
        if voice_config is None:
            return
        thread = QThread(self)
        worker = OmniVoicePreviewWorker(
            OmniVoiceManager(),
            voice_config,
            "The moon looks beautiful tonight.",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_omnivoice_preview_ready)
        worker.failed.connect(self._on_omnivoice_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_omnivoice_preview_worker)
        self.omnivoice_preview_thread = thread
        self.omnivoice_preview_worker = worker
        self.loaded_tts_engine_id = "omnivoice"
        self._update_header_engine_label()
        self._refresh_omnivoice_status()
        self._show_omnivoice_preview_status(
            self.tr("omnivoice_preview_generating", "Generating OmniVoice preview..."),
            busy=True,
        )
        self.log_view.append_event(
            self.tr("omnivoice_preview_generating", "Generating OmniVoice preview...")
        )
        thread.start()

    def _on_omnivoice_preview_ready(self, path: str) -> None:
        self._show_voice_preview_status(
            self.tr("voice_preview_playing", "Playing voice preview."),
            busy=False,
        )
        self._show_omnivoice_preview_status(
            self.tr("omnivoice_preview_ready", "Playing OmniVoice preview."),
            busy=False,
        )
        self.omnivoice_sample_player.setSource(QUrl.fromLocalFile(path))
        self.omnivoice_sample_player.play()
        self.log_view.append_event(
            self.tr("omnivoice_preview_ready", "Playing OmniVoice preview.")
        )

    def _show_omnivoice_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "omnivoice_preview_frame"):
            return
        self.omnivoice_preview_status_label.setText(message)
        self.omnivoice_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.omnivoice_preview_bar.setValue(100)
        self.omnivoice_preview_frame.setVisible(True)

    def _hide_omnivoice_preview_status(self) -> None:
        if not hasattr(self, "omnivoice_preview_frame"):
            return
        self.omnivoice_preview_frame.setVisible(False)

    def _on_omnivoice_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if not hasattr(self, "omnivoice_preview_frame"):
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_omnivoice_preview_status(
                self.tr("omnivoice_preview_ready", "Playing OmniVoice preview."),
                busy=False,
            )
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self.omnivoice_preview_thread is None
        ):
            QTimer.singleShot(800, self._hide_omnivoice_preview_status)

    def _clear_omnivoice_preview_worker(self) -> None:
        self.omnivoice_preview_worker = None
        self.omnivoice_preview_thread = None
        self.loaded_tts_engine_id = self.preloaded_tts_engine_id
        self._update_header_engine_label()
        if (
            hasattr(self, "omnivoice_preview_frame")
            and self.omnivoice_sample_player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
        ):
            QTimer.singleShot(800, self._hide_omnivoice_preview_status)
        self._refresh_omnivoice_status()

    def _omnivoice_voice_config_for_ui(self) -> dict[str, object] | None:
        if not self.omnivoice_manager.is_installed():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "omnivoice_runtime_missing",
                    "OmniVoice is not installed yet. Open Settings > TTS Engines "
                    "and click Install.",
                ),
            )
            return None
        self._ensure_default_omnivoice_reference(allow_sync=True)
        mode = "clone"
        reference_path = self.omnivoice_reference_picker.path()
        if reference_path is None or not reference_path.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "omnivoice_reference_required",
                    "OmniVoice voice cloning requires a reference audio file.",
                ),
            )
            return None
        model = str(self.omnivoice_model_combo.currentData() or "omnivoice")
        return {
            "engine": "omnivoice",
            "speed": self.speed_spin.value(),
            "model": model,
            "model_repo": self.omnivoice_manager.model_repo(model),
            "mode": mode,
            "language": self.omnivoice_language_combo.currentData() or "auto",
            "device": self.omnivoice_device_combo.currentData() or "auto",
            "dtype": self.omnivoice_dtype_combo.currentData() or "auto",
            "instruct": "",
            "reference_audio_path": str(reference_path or ""),
            "reference_text": self.omnivoice_reference_text_edit.toPlainText().strip(),
            "num_step": self.omnivoice_num_step_spin.value(),
            "engine_speed": self.omnivoice_engine_speed_spin.value(),
            "duration": self.omnivoice_duration_spin.value(),
            "cache_dir": str(self.omnivoice_manager.cache_dir),
        }

    def _build_openai_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.openai_api_key_edit = self._password_edit()
        self.openai_model_combo = QComboBox()
        for model in ("gpt-4o-mini-tts", "tts-1", "tts-1-hd"):
            self.openai_model_combo.addItem(model, model)
        self.openai_voice_combo = QComboBox()
        for voice in (
            "marin",
            "cedar",
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "fable",
            "nova",
            "onyx",
            "sage",
            "shimmer",
            "verse",
        ):
            self.openai_voice_combo.addItem(voice, voice)
        self.openai_instructions_edit = QLineEdit()
        self.openai_instructions_edit.setPlaceholderText(
            self.tr(
                "openai_instructions_placeholder",
                "Optional: calm narrator, energetic course teacher...",
            )
        )

        form.addRow(self.tr("api_key", "API key"), self.openai_api_key_edit)
        form.addRow(self.tr("openai_model", "Model"), self.openai_model_combo)
        form.addRow(self.tr("openai_voice", "Voice"), self.openai_voice_combo)
        form.addRow(
            self.tr("openai_instructions", "Style instructions"),
            self.openai_instructions_edit,
        )
        return panel

    def _build_elevenlabs_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.elevenlabs_api_key_edit = self._password_edit()
        self.elevenlabs_voice_id_edit = QLineEdit()
        self.elevenlabs_voice_id_edit.setPlaceholderText(
            self.tr(
                "elevenlabs_voice_id_placeholder",
                "Paste a voice_id from your ElevenLabs account",
            )
        )
        self.elevenlabs_model_combo = QComboBox()
        for model in (
            "eleven_flash_v2_5",
            "eleven_multilingual_v2",
            "eleven_v3",
        ):
            self.elevenlabs_model_combo.addItem(model, model)
        self.elevenlabs_output_combo = QComboBox()
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_pcm_24000", "PCM 24 kHz (recommended)"),
            "pcm_24000",
        )
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_pcm_44100", "PCM 44.1 kHz"),
            "pcm_44100",
        )
        self.elevenlabs_output_combo.addItem(
            self.tr("elevenlabs_wav_44100", "WAV 44.1 kHz (Pro tier)"),
            "wav_44100",
        )
        self.elevenlabs_stability_spin = self._ratio_spin(0.5)
        self.elevenlabs_similarity_spin = self._ratio_spin(0.75)
        self.elevenlabs_style_spin = self._ratio_spin(0.0)
        self.elevenlabs_speaker_boost_checkbox = QCheckBox(
            self.tr("speaker_boost", "Speaker boost")
        )

        form.addRow(self.tr("api_key", "API key"), self.elevenlabs_api_key_edit)
        form.addRow(
            self.tr("elevenlabs_voice_id", "Voice ID"),
            self.elevenlabs_voice_id_edit,
        )
        form.addRow(
            self.tr("elevenlabs_model", "Model"),
            self.elevenlabs_model_combo,
        )
        form.addRow(
            self.tr("elevenlabs_output_format", "Output format"),
            self.elevenlabs_output_combo,
        )
        form.addRow(
            self.tr("stability", "Stability"),
            self.elevenlabs_stability_spin,
        )
        form.addRow(
            self.tr("similarity", "Similarity"),
            self.elevenlabs_similarity_spin,
        )
        form.addRow(
            self.tr("style_exaggeration", "Style exaggeration"),
            self.elevenlabs_style_spin,
        )
        form.addRow("", self.elevenlabs_speaker_boost_checkbox)
        return panel

    def _build_gemini_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.gemini_api_key_edit = self._password_edit()
        self.gemini_model_combo = QComboBox()
        for model in (
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-tts",
            "gemini-2.5-flash-lite-preview-tts",
            "gemini-2.5-pro-tts",
        ):
            self.gemini_model_combo.addItem(model, model)
        self.gemini_voice_combo = QComboBox()
        for voice in (
            "Kore",
            "Puck",
            "Charon",
            "Zephyr",
            "Fenrir",
            "Leda",
            "Orus",
            "Aoede",
            "Callirrhoe",
            "Autonoe",
            "Enceladus",
            "Iapetus",
            "Umbriel",
            "Algieba",
            "Despina",
            "Erinome",
            "Algenib",
            "Rasalgethi",
            "Laomedeia",
            "Achernar",
            "Alnilam",
            "Schedar",
            "Gacrux",
            "Pulcherrima",
            "Achird",
            "Zubenelgenubi",
            "Vindemiatrix",
            "Sadachbia",
            "Sadaltager",
            "Sulafat",
        ):
            self.gemini_voice_combo.addItem(voice, voice)
        self.gemini_prompt_edit = QLineEdit()
        self.gemini_prompt_edit.setPlaceholderText(
            self.tr(
                "gemini_prompt_placeholder",
                "Optional: Say this as a warm podcast narrator.",
            )
        )

        form.addRow(self.tr("api_key", "API key"), self.gemini_api_key_edit)
        form.addRow(self.tr("gemini_model", "Gemini model"), self.gemini_model_combo)
        form.addRow(self.tr("gemini_voice", "Voice"), self.gemini_voice_combo)
        form.addRow(
            self.tr("gemini_prompt", "Style prompt"),
            self.gemini_prompt_edit,
        )
        helper = QLabel(
            self.tr(
                "gemini_help",
                "Remote Gemini TTS via Google AI Studio API key. Output is "
                "wrapped as WAV so it can use the same MP3 pipeline.",
            )
        )
        helper.setWordWrap(True)
        helper.setObjectName("helperLabel")
        form.addRow("", helper)
        return panel

    def _build_azure_engine_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setSpacing(10)
        self.azure_api_key_edit = self._password_edit()
        self.azure_region_edit = QLineEdit()
        self.azure_region_edit.setPlaceholderText(
            self.tr("azure_region_placeholder", "Example: westeurope")
        )
        self.azure_voice_edit = QLineEdit()
        self.azure_voice_edit.setPlaceholderText("en-US-JennyNeural")
        self.azure_output_combo = QComboBox()
        self.azure_output_combo.addItem(
            "RIFF 24 kHz mono PCM",
            "riff-24khz-16bit-mono-pcm",
        )
        self.azure_output_combo.addItem(
            "RIFF 48 kHz mono PCM",
            "riff-48khz-16bit-mono-pcm",
        )
        self.azure_style_edit = QLineEdit()
        self.azure_style_edit.setPlaceholderText(
            self.tr("azure_style_placeholder", "Optional: cheerful, sad, calm...")
        )
        form.addRow(self.tr("api_key", "API key"), self.azure_api_key_edit)
        form.addRow(self.tr("azure_region", "Region"), self.azure_region_edit)
        form.addRow(self.tr("azure_voice", "Voice name"), self.azure_voice_edit)
        form.addRow(
            self.tr("azure_output_format", "Output format"),
            self.azure_output_combo,
        )
        form.addRow(self.tr("azure_style", "Style"), self.azure_style_edit)
        return panel

    def _build_custom_engine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        self.custom_engine_status_label = QLabel()
        self.custom_engine_status_label.setObjectName("helperLabel")
        self.custom_engine_status_label.setWordWrap(True)
        self.custom_engine_endpoint_label = QLabel()
        self.custom_engine_endpoint_label.setObjectName("helperLabel")
        self.custom_engine_endpoint_label.setWordWrap(True)
        layout.addWidget(self.custom_engine_status_label)
        layout.addWidget(self.custom_engine_endpoint_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.custom_engine_configure_button = QPushButton(
            self.tr("configure", "Configure")
        )
        self.custom_engine_configure_button.setIcon(ui_icon("settings"))
        self.custom_engine_configure_button.clicked.connect(
            self._edit_selected_custom_tts_engine
        )
        self.custom_engine_delete_button = QPushButton(self.tr("remove", "Remove"))
        self.custom_engine_delete_button.setIcon(ui_icon("delete"))
        self.custom_engine_delete_button.clicked.connect(
            self._delete_selected_custom_tts_engine
        )
        actions.addWidget(self.custom_engine_configure_button)
        actions.addWidget(self.custom_engine_delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        helper = QLabel(
            self.tr(
                "custom_engine_help",
                "Custom engines call an HTTP endpoint that returns WAV/PCM audio "
                "or JSON containing audio. Use placeholders like {{text}}, "
                "{{voice}}, {{language}}, {{speed}}, {{api_key}}, and {{output_path}}.",
            )
        )
        helper.setObjectName("helperLabel")
        helper.setWordWrap(True)
        layout.addWidget(helper)
        self._refresh_custom_engine_panel()
        return panel

    def _add_custom_tts_engine(self) -> None:
        self._open_custom_tts_engine_dialog(None)

    def _edit_selected_custom_tts_engine(self) -> None:
        engine = self._custom_engine_by_key(
            str(self.tts_engine_combo.currentData() or "")
        )
        if engine is not None:
            self._open_custom_tts_engine_dialog(engine)

    def _delete_selected_custom_tts_engine(self) -> None:
        engine_key = str(self.tts_engine_combo.currentData() or "")
        engine = self._custom_engine_by_key(engine_key)
        if engine is None:
            return
        choice = QMessageBox.question(
            self,
            self.tr("remove", "Remove"),
            self.tr(
                "custom_engine_delete_confirm",
                "Remove custom engine '{name}'?",
                name=str(engine.get("name", "")),
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        engine_id = str(engine.get("id", ""))
        self.settings["custom_tts_engines"] = [
            item
            for item in self._custom_tts_engines()
            if str(item.get("id", "")) != engine_id
        ]
        if str(self.settings.get("tts_engine", "")) == engine_key:
            self.settings["tts_engine"] = "piper"
        self._populate_tts_engine_combo("piper")
        self._select_tts_engine("piper")
        self._save_settings()
        self.log_view.append_event(
            self.tr(
                "custom_engine_deleted",
                "Custom TTS engine removed: {name}",
                name=str(engine.get("name", "")),
            )
        )

    def _open_custom_tts_engine_dialog(
        self,
        engine: dict[str, object] | None,
    ) -> None:
        data = self._custom_tts_engine_defaults()
        editing_id = ""
        if engine is not None:
            data.update(engine)
            editing_id = str(engine.get("id", ""))

        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr("custom_engine_dialog_title", "Custom TTS Engine")
        )
        dialog.setMinimumWidth(720)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setSpacing(8)

        name_edit = QLineEdit(str(data.get("name", "")))
        name_edit.setPlaceholderText("AllTalk local, My Studio TTS...")
        location_combo = QComboBox()
        location_combo.addItem(
            self.tr("custom_engine_local_http", "Local HTTP endpoint"),
            "local_http",
        )
        location_combo.addItem(
            self.tr("custom_engine_remote_http", "Remote HTTP API"),
            "remote_http",
        )
        self._select_combo_data(location_combo, data.get("location", "local_http"))

        url_edit = QLineEdit(str(data.get("url", "")))
        url_edit.setPlaceholderText("http://127.0.0.1:7851/api/tts-generate")
        method_combo = QComboBox()
        for method in ("POST", "GET"):
            method_combo.addItem(method, method)
        self._select_combo_data(method_combo, data.get("method", "POST"))

        voice_edit = QLineEdit(str(data.get("voice", "")))
        language_edit = QLineEdit(str(data.get("language", "")))
        api_key_edit = self._password_edit()
        api_key_edit.setText(str(data.get("api_key", "")))
        auth_header_edit = QLineEdit(str(data.get("auth_header", "")))
        auth_header_edit.setPlaceholderText("Authorization: Bearer {{api_key}}")

        headers_edit = QTextEdit()
        headers_edit.setPlainText(str(data.get("headers_json", "")))
        headers_edit.setFixedHeight(72)
        body_edit = QTextEdit()
        body_edit.setPlainText(str(data.get("body_template", "")))
        body_edit.setFixedHeight(128)

        response_combo = QComboBox()
        for label, value in (
            (
                self.tr("custom_engine_response_wav", "HTTP response is WAV audio"),
                "audio_wav",
            ),
            (
                self.tr(
                    "custom_engine_response_pcm",
                    "HTTP response is raw PCM 16-bit mono",
                ),
                "audio_pcm",
            ),
            (
                self.tr(
                    "custom_engine_response_json_base64",
                    "JSON field contains base64 audio",
                ),
                "json_base64",
            ),
            (
                self.tr(
                    "custom_engine_response_json_url",
                    "JSON field contains audio URL",
                ),
                "json_url",
            ),
            (
                self.tr(
                    "custom_engine_response_json_path",
                    "JSON field contains local audio file path",
                ),
                "json_path",
            ),
        ):
            response_combo.addItem(label, value)
        self._select_combo_data(
            response_combo,
            data.get("response_mode", "audio_wav"),
        )

        json_path_edit = QLineEdit(str(data.get("json_audio_path", "")))
        json_path_edit.setPlaceholderText("audio.data / output.url / file_path")
        sample_rate_spin = QSpinBox()
        sample_rate_spin.setRange(8000, 192000)
        sample_rate_spin.setValue(int(data.get("sample_rate", 24000) or 24000))
        timeout_spin = QSpinBox()
        timeout_spin.setRange(10, 1800)
        timeout_spin.setSuffix(" s")
        timeout_spin.setValue(int(data.get("timeout_seconds", 120) or 120))

        form.addRow(self.tr("custom_engine_name", "Name"), name_edit)
        form.addRow(self.tr("custom_engine_location", "Location"), location_combo)
        form.addRow(self.tr("custom_engine_url", "Endpoint URL"), url_edit)
        form.addRow(self.tr("custom_engine_method", "HTTP method"), method_combo)
        form.addRow(self.tr("custom_engine_default_voice", "Default voice"), voice_edit)
        form.addRow(
            self.tr("custom_engine_default_language", "Default language"),
            language_edit,
        )
        form.addRow(self.tr("api_key", "API key"), api_key_edit)
        form.addRow(
            self.tr("custom_engine_auth_header", "Auth header template"),
            auth_header_edit,
        )
        form.addRow(
            self.tr("custom_engine_headers_json", "Headers JSON"),
            headers_edit,
        )
        form.addRow(
            self.tr("custom_engine_body_template", "Body template"),
            body_edit,
        )
        form.addRow(
            self.tr("custom_engine_response_mode", "Response mode"),
            response_combo,
        )
        form.addRow(
            self.tr("custom_engine_json_field", "JSON audio field"),
            json_path_edit,
        )
        form.addRow(
            self.tr("custom_engine_sample_rate", "PCM sample rate"),
            sample_rate_spin,
        )
        form.addRow(self.tr("custom_engine_timeout", "Timeout"), timeout_spin)
        layout.addLayout(form)

        help_label = QLabel(
            self.tr(
                "custom_engine_template_help",
                "Templates can use {{text}}, {{voice}}, {{language}}, {{speed}}, "
                "{{model}}, {{api_key}}, and {{output_path}}. For JSON responses, "
                "use dot paths like audio.data or result.file.",
            )
        )
        help_label.setObjectName("helperLabel")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        save_button = QPushButton(self.tr("save", "Save"))
        save_button.setIcon(ui_icon("save"))
        cancel_button.clicked.connect(dialog.reject)
        save_button.clicked.connect(dialog.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = name_edit.text().strip()
        url = url_edit.text().strip()
        if not name or not url:
            self._show_error(
                self.tr("custom_engine_invalid", "Invalid custom engine"),
                self.tr(
                    "custom_engine_invalid_message",
                    "Custom engines require at least a name and an endpoint URL.",
                ),
            )
            return

        saved_engine = {
            "id": self._unique_custom_engine_id(name, editing_id or None),
            "name": name,
            "location": location_combo.currentData() or "local_http",
            "url": url,
            "method": method_combo.currentData() or "POST",
            "voice": voice_edit.text().strip(),
            "language": language_edit.text().strip(),
            "api_key": api_key_edit.text().strip(),
            "auth_header": auth_header_edit.text().strip(),
            "headers_json": headers_edit.toPlainText().strip(),
            "body_template": body_edit.toPlainText().strip(),
            "response_mode": response_combo.currentData() or "audio_wav",
            "json_audio_path": json_path_edit.text().strip(),
            "sample_rate": sample_rate_spin.value(),
            "timeout_seconds": timeout_spin.value(),
        }

        engines = self._custom_tts_engines()
        if editing_id:
            engines = [
                saved_engine if str(item.get("id", "")) == editing_id else item
                for item in engines
            ]
        else:
            engines.append(saved_engine)
        self.settings["custom_tts_engines"] = engines
        selected_key = self._custom_engine_key(saved_engine["id"])
        self._populate_tts_engine_combo(selected_key)
        self._select_tts_engine(selected_key)
        self._save_settings()
        self.log_view.append_event(
            self.tr(
                "custom_engine_saved",
                "Custom TTS engine saved: {name}",
                name=name,
            )
        )

    def _refresh_custom_engine_panel(self) -> None:
        if not hasattr(self, "custom_engine_status_label"):
            return
        engine_key = str(self.tts_engine_combo.currentData() or "")
        engine = self._custom_engine_by_key(engine_key)
        has_engine = engine is not None
        if not has_engine:
            self.custom_engine_status_label.setText(
                self.tr("custom_engine_missing", "No custom engine is selected.")
            )
            self.custom_engine_endpoint_label.setText("")
        else:
            self.custom_engine_status_label.setText(
                self.tr(
                    "custom_engine_status",
                    "Custom engine: {name} ({type})",
                    name=str(engine.get("name", "")),
                    type=str(engine.get("location", "local_http")),
                )
            )
            self.custom_engine_endpoint_label.setText(
                self.tr(
                    "custom_engine_endpoint",
                    "Endpoint: {url}",
                    url=str(engine.get("url", "")),
                )
            )
        self.custom_engine_configure_button.setEnabled(has_engine)
        self.custom_engine_delete_button.setEnabled(has_engine)

    def _on_tts_engine_changed(self) -> None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        stack_key = "custom" if engine_id.startswith("custom:") else engine_id
        index = self.engine_stack_indexes.get(stack_key, 0)
        self.engine_settings_stack.setCurrentIndex(index)
        self._update_voice_panel_for_engine()
        self._refresh_tts_engine_table()
        self._refresh_custom_engine_panel()
        self._refresh_generation_voice_combo()
        self._update_header_engine_label()
        if hasattr(self, "page_stack") and self.page_stack.currentIndex() == 5:
            self._refresh_voices_page()

    def _update_header_engine_label(self) -> None:
        if not hasattr(self, "header_engine_label"):
            return
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        engine_label = self._tts_engine_label(engine_id)
        self.header_engine_label.setText(engine_label)
        if self.preloading_tts_engine_id == engine_id:
            status_text = self.tr("loading_into_memory", "Loading into memory...")
        elif self.worker_thread is not None and self.loaded_tts_engine_id == engine_id:
            status_text = self.tr("active", "Active")
        else:
            status_text = self.tr("ready", "Ready")
        if hasattr(self, "sidebar_ready_label"):
            self.sidebar_ready_label.setText(status_text)
        if hasattr(self, "sidebar_engine_detail_label"):
            if self.preloading_tts_engine_id == engine_id:
                memory_text = self.tr("loading_into_memory", "Loading into memory...")
            elif (
                self.preloaded_tts_engine_id == engine_id
                or engine_id in self.host_loaded_tts_engine_ids
            ):
                memory_text = self.tr("loaded_in_memory", "Loaded in memory")
            else:
                memory_text = self.tr("not_loaded_in_memory", "Not loaded in memory")
            self.sidebar_engine_detail_label.setText(
                "\n".join(
                    [
                        self.tr(
                            "sidebar_selected_engine",
                            "Engine: {engine}",
                            engine=engine_label,
                        ),
                        self.tr(
                            "sidebar_model_memory",
                            "Model memory: {memory}",
                            memory=memory_text,
                        ),
                        self.tr(
                            "sidebar_hardware_acceleration",
                            "Hardware acceleration: {hardware}",
                            hardware=self._sidebar_hardware_summary(),
                        ),
                    ]
                )
            )

    def _sidebar_hardware_summary(self) -> str:
        result = self.gpu_detection_result
        if result.has_nvidia_gpu:
            gpu = next((candidate for candidate in result.gpus if candidate.is_nvidia), None)
            if gpu is None:
                return self.tr("cuda_detected", "CUDA detected")
            parts = [gpu.name]
            if gpu.memory_total_gb is not None:
                parts.append(f"{gpu.memory_total_gb:.1f} GB")
            if gpu.driver_version:
                parts.append(f"driver {gpu.driver_version}")
            return self.tr(
                "cuda_detected_summary",
                "CUDA detected ({details})",
                details=", ".join(parts),
            )
        if result.gpus:
            gpu = result.gpus[0]
            return self.tr(
                "gpu_no_cuda_summary",
                "GPU detected, no CUDA ({name})",
                name=gpu.name,
            )
        return self.tr("no_gpu_detected", "No GPU detected")

    def _update_voice_panel_for_engine(self) -> None:
        if not hasattr(self, "language_combo"):
            return
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        piper_selected = engine_id == "piper"
        self.language_combo.setEnabled(piper_selected)
        self.voice_combo.setEnabled(piper_selected)
        self.manage_voices_button.setEnabled(piper_selected)
        if piper_selected:
            self.voice_help_label.setText(
                self.tr(
                    "voice_help",
                    "Voices are discovered from voices/**/*.onnx when the matching "
                    ".onnx.json file is present.",
                )
            )
        else:
            self.voice_help_label.setText(
                self.tr(
                    "api_voice_config_help",
                    "This engine uses the voice and credentials configured in Settings > TTS Engines.",
                )
            )

    @staticmethod
    def _password_edit() -> QLineEdit:
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("sk-... / API key")
        return edit

    @staticmethod
    def _ratio_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(default)
        return spin

    def _tts_engine_label(self, engine_id: str) -> str:
        custom_engine = self._custom_engine_by_key(engine_id)
        if custom_engine is not None:
            return str(custom_engine.get("name", engine_id) or engine_id)
        labels = {
            "piper": self.tr("tts_engine_piper", "Piper"),
            "kokoro": self.tr(
                "tts_engine_kokoro",
                "Kokoro",
            ),
            "chatterbox": self.tr(
                "tts_engine_chatterbox",
                "Chatterbox",
            ),
            "qwen": self.tr("tts_engine_qwen", "Qwen3 TTS"),
            "omnivoice": self.tr("tts_engine_omnivoice", "OmniVoice"),
            "openai": self.tr("tts_engine_openai", "OpenAI TTS (API)"),
            "elevenlabs": self.tr("tts_engine_elevenlabs", "ElevenLabs (API)"),
            "gemini": self.tr("tts_engine_gemini", "Google Gemini TTS (API)"),
            "azure": self.tr("tts_engine_azure", "Azure Speech (API)"),
        }
        return labels.get(engine_id, engine_id)

    def _engine_table_rows(self) -> list[dict[str, str]]:
        current_engine = str(self.tts_engine_combo.currentData() or "piper")
        piper_path_edit = getattr(self, "piper_path_edit", None)
        piper_path_value = (
            piper_path_edit.text().strip()
            if piper_path_edit is not None
            else str(self.settings.get("piper_path", "engines/piper/piper.exe"))
        )
        piper_runtime_ready = resolve_app_path(
            piper_path_value or "engines/piper/piper.exe"
        ).exists()
        piper_installed = piper_runtime_ready and bool(self.voices)
        kokoro_installed = self.kokoro_python_manager.is_installed()
        chatterbox_runtime_ready = self.chatterbox_manager.has_runtime()
        chatterbox_ready = self.chatterbox_manager.is_installed()
        qwen_runtime_ready = self.qwen_manager.has_runtime()
        qwen_ready = self.qwen_manager.is_installed()
        omnivoice_runtime_ready = self.omnivoice_manager.has_runtime()
        omnivoice_ready = self.omnivoice_manager.is_installed()

        local_type = self.tr("engine_type_local", "Local")
        remote_type = self.tr("engine_type_remote", "Remote")
        installed = self.tr("installed", "Installed")
        not_installed = self.tr("not_installed", "Not installed")
        update_available = self.tr("update_available", "Update available")

        rows = [
            {
                "engine_id": "piper",
                "type": local_type,
                "name": self._tts_engine_label("piper"),
                "speed": self.tr("engine_speed_fast", "Fast"),
                "quality": self.tr("engine_quality_good", "Good"),
                "gpu": self.tr("no", "No"),
                "installed": installed if piper_installed else not_installed,
            },
            {
                "engine_id": "kokoro",
                "type": local_type,
                "name": self._tts_engine_label("kokoro"),
                "speed": self.tr("engine_speed_medium", "Medium"),
                "quality": self.tr("engine_quality_better", "Better"),
                "gpu": self.tr("automatic", "Automatic"),
                "installed": installed if kokoro_installed else not_installed,
            },
            {
                "engine_id": "chatterbox",
                "type": local_type,
                "name": self._tts_engine_label("chatterbox"),
                "speed": self.tr("engine_speed_slow", "Slow"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": self.tr("recommended", "Recommended"),
                "installed": (
                    installed
                    if chatterbox_ready
                    else update_available
                    if chatterbox_runtime_ready
                    else not_installed
                ),
            },
            {
                "engine_id": "qwen",
                "type": local_type,
                "name": self._tts_engine_label("qwen"),
                "speed": self.tr("engine_speed_slow", "Slow"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": self.tr("recommended", "Recommended"),
                "installed": (
                    installed
                    if qwen_ready
                    else update_available
                    if qwen_runtime_ready
                    else not_installed
                ),
            },
            {
                "engine_id": "omnivoice",
                "type": local_type,
                "name": self._tts_engine_label("omnivoice"),
                "speed": self.tr("engine_speed_medium", "Medium"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": self.tr("recommended", "Recommended"),
                "installed": (
                    installed
                    if omnivoice_ready
                    else update_available
                    if omnivoice_runtime_ready
                    else not_installed
                ),
            },
            {
                "engine_id": "openai",
                "type": remote_type,
                "name": self._tts_engine_label("openai"),
                "speed": self.tr("engine_speed_fast_network", "Fast, network"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": "",
                "installed": "",
            },
            {
                "engine_id": "elevenlabs",
                "type": remote_type,
                "name": self._tts_engine_label("elevenlabs"),
                "speed": self.tr("engine_speed_fast_network", "Fast, network"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": "",
                "installed": "",
            },
            {
                "engine_id": "gemini",
                "type": remote_type,
                "name": self._tts_engine_label("gemini"),
                "speed": self.tr("engine_speed_fast_network", "Fast, network"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": "",
                "installed": "",
            },
            {
                "engine_id": "azure",
                "type": remote_type,
                "name": self._tts_engine_label("azure"),
                "speed": self.tr("engine_speed_fast_network", "Fast, network"),
                "quality": self.tr("engine_quality_high", "High"),
                "gpu": "",
                "installed": "",
            },
        ]
        for engine in self._custom_tts_engines():
            engine_id = self._custom_engine_key(engine.get("id", ""))
            is_local = str(engine.get("location", "local_http")) == "local_http"
            rows.append(
                {
                    "engine_id": engine_id,
                    "type": local_type if is_local else remote_type,
                    "name": str(engine.get("name", engine_id)),
                    "speed": self.tr("engine_speed_depends", "Depends"),
                    "quality": self.tr("engine_quality_custom", "Custom"),
                    "gpu": self.tr("depends", "Depends") if is_local else "",
                    "installed": self.tr("configured", "Configured"),
                }
            )
        for row in rows:
            engine_id = row["engine_id"]
            if engine_id != current_engine:
                row["selected"] = ""
            elif self.preloading_tts_engine_id == engine_id:
                row["selected"] = self.tr("selected_loading", "Selected / loading")
            elif (
                self.preloaded_tts_engine_id == engine_id
                or engine_id in self.host_loaded_tts_engine_ids
            ):
                row["selected"] = self.tr("selected_loaded", "Selected / loaded")
            elif engine_id in {"chatterbox", "qwen", "omnivoice"}:
                row["selected"] = self.tr(
                    "selected_not_loaded",
                    "Selected / not loaded",
                )
            else:
                row["selected"] = self.tr("selected", "Selected")
        return rows

    def _refresh_tts_engine_table(self) -> None:
        if not hasattr(self, "tts_engine_table"):
            return
        self.tts_engine_table.blockSignals(True)
        self.tts_engine_table.setRowCount(0)
        current_engine = str(self.tts_engine_combo.currentData() or "piper")
        for row_index, row in enumerate(self._engine_table_rows()):
            self.tts_engine_table.insertRow(row_index)
            for column, key in enumerate(
                ("type", "name", "speed", "quality", "gpu", "installed", "selected")
            ):
                item = QTableWidgetItem(row[key])
                item.setData(Qt.ItemDataRole.UserRole, row["engine_id"])
                if column in (0, 2, 3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tts_engine_table.setItem(row_index, column, item)
            self.tts_engine_table.setCellWidget(
                row_index,
                7,
                self._build_tts_engine_action_widget(row["engine_id"]),
            )
            if row["engine_id"] == current_engine:
                self.tts_engine_table.selectRow(row_index)
        self.tts_engine_table.blockSignals(False)
        self.tts_engine_table.resizeRowsToContents()

    def _build_tts_engine_action_widget(self, engine_id: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        select_button = QPushButton(self.tr("select", "Select"))
        select_button.setIcon(ui_icon("apply"))
        select_button.clicked.connect(
            lambda _checked=False, selected=engine_id: self._select_tts_engine(selected)
        )
        layout.addWidget(select_button)

        if engine_id == "piper":
            manage_button = QPushButton(self.tr("manage_voices", "Manage voices"))
            manage_button.setIcon(ui_icon("voice"))
            manage_button.clicked.connect(self._open_voice_manager)
            layout.addWidget(manage_button)
        elif engine_id == "kokoro":
            installed = self.kokoro_python_manager.is_installed()
            if installed:
                remove_button = QPushButton(self.tr("uninstall", "Uninstall"))
                remove_button.setIcon(ui_icon("delete"))
                remove_button.clicked.connect(self._remove_kokoro_python)
                remove_button.setEnabled(self.kokoro_python_thread is None)
                layout.addWidget(remove_button)
                load_button = QPushButton()
                load_button.clicked.connect(
                    lambda _checked=False: self._toggle_preloaded_tts_engine("kokoro")
                )
                self._configure_preload_button(load_button, "kokoro", True)
                layout.addWidget(load_button)
            else:
                install_button = QPushButton(self.tr("install", "Install"))
                install_button.setIcon(ui_icon("apply"))
                install_button.clicked.connect(
                    lambda _checked=False: self._select_and_install_engine("kokoro")
                )
                install_button.setEnabled(self.kokoro_python_thread is None)
                layout.addWidget(install_button)
        elif engine_id == "chatterbox":
            runtime_ready = self.chatterbox_manager.has_runtime()
            installed = self.chatterbox_manager.is_installed()
            if installed:
                remove_button = QPushButton(self.tr("uninstall", "Uninstall"))
                remove_button.setIcon(ui_icon("delete"))
                remove_button.clicked.connect(self._remove_chatterbox)
                remove_button.setEnabled(self.chatterbox_thread is None)
                layout.addWidget(remove_button)
                load_button = QPushButton()
                load_button.clicked.connect(
                    lambda _checked=False: self._toggle_preloaded_tts_engine(
                        "chatterbox"
                    )
                )
                self._configure_preload_button(load_button, "chatterbox", True)
                layout.addWidget(load_button)
            else:
                install_button = QPushButton(
                    self.tr(
                        "update" if runtime_ready else "install",
                        "Update" if runtime_ready else "Install",
                    )
                )
                install_button.setIcon(ui_icon("apply"))
                install_button.clicked.connect(
                    lambda _checked=False: self._select_and_install_engine("chatterbox")
                )
                install_button.setEnabled(self.chatterbox_thread is None)
                layout.addWidget(install_button)
        elif engine_id == "qwen":
            runtime_ready = self.qwen_manager.has_runtime()
            installed = self.qwen_manager.is_installed()
            if installed:
                remove_button = QPushButton(self.tr("uninstall", "Uninstall"))
                remove_button.setIcon(ui_icon("delete"))
                remove_button.clicked.connect(self._remove_qwen)
                remove_button.setEnabled(self.qwen_thread is None)
                layout.addWidget(remove_button)
                load_button = QPushButton()
                load_button.clicked.connect(
                    lambda _checked=False: self._toggle_preloaded_tts_engine("qwen")
                )
                self._configure_preload_button(load_button, "qwen", True)
                layout.addWidget(load_button)
            else:
                install_button = QPushButton(
                    self.tr(
                        "update" if runtime_ready else "install",
                        "Update" if runtime_ready else "Install",
                    )
                )
                install_button.setIcon(ui_icon("apply"))
                install_button.clicked.connect(
                    lambda _checked=False: self._select_and_install_engine("qwen")
                )
                install_button.setEnabled(self.qwen_thread is None)
                layout.addWidget(install_button)
        elif engine_id == "omnivoice":
            runtime_ready = self.omnivoice_manager.has_runtime()
            installed = self.omnivoice_manager.is_installed()
            if installed:
                remove_button = QPushButton(self.tr("uninstall", "Uninstall"))
                remove_button.setIcon(ui_icon("delete"))
                remove_button.clicked.connect(self._remove_omnivoice)
                remove_button.setEnabled(self.omnivoice_thread is None)
                layout.addWidget(remove_button)
                load_button = QPushButton()
                load_button.clicked.connect(
                    lambda _checked=False: self._toggle_preloaded_tts_engine(
                        "omnivoice"
                    )
                )
                self._configure_preload_button(load_button, "omnivoice", True)
                layout.addWidget(load_button)
            else:
                install_button = QPushButton(
                    self.tr(
                        "update" if runtime_ready else "install",
                        "Update" if runtime_ready else "Install",
                    )
                )
                install_button.setIcon(ui_icon("apply"))
                install_button.clicked.connect(
                    lambda _checked=False: self._select_and_install_engine(
                        "omnivoice"
                    )
                )
                install_button.setEnabled(self.omnivoice_thread is None)
                layout.addWidget(install_button)
        elif engine_id.startswith("custom:"):
            configure_button = QPushButton(self.tr("configure", "Configure"))
            configure_button.setIcon(ui_icon("settings"))
            configure_button.clicked.connect(
                lambda _checked=False, selected=engine_id: (
                    self._select_tts_engine(selected),
                    self._edit_selected_custom_tts_engine(),
                )
            )
            layout.addWidget(configure_button)
            remove_button = QPushButton(self.tr("remove", "Remove"))
            remove_button.setIcon(ui_icon("delete"))
            remove_button.clicked.connect(
                lambda _checked=False, selected=engine_id: (
                    self._select_tts_engine(selected),
                    self._delete_selected_custom_tts_engine(),
                )
            )
            layout.addWidget(remove_button)

        layout.addStretch(1)
        return widget

    def _on_tts_engine_table_clicked(self, row: int, _column: int) -> None:
        item = self.tts_engine_table.item(row, 0)
        if item is None:
            return
        engine_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if engine_id:
            self._select_tts_engine(engine_id)

    def _select_tts_engine(self, engine_id: str) -> None:
        self._select_combo_data(self.tts_engine_combo, engine_id)
        self._on_tts_engine_changed()

    def _select_and_install_engine(self, engine_id: str) -> None:
        self._select_tts_engine(engine_id)
        if engine_id == "kokoro":
            self._install_kokoro_python()
        elif engine_id == "chatterbox":
            self._install_chatterbox()
        elif engine_id == "qwen":
            self._install_qwen()
        elif engine_id == "omnivoice":
            self._install_omnivoice()

    def _configure_preload_button(
        self,
        button: QPushButton,
        engine_id: str,
        can_load: bool,
    ) -> None:
        loaded = (
            engine_id in self.host_loaded_tts_engine_ids
            or (
                self.preloaded_tts_engine is not None
                and self.preloaded_tts_engine_id == engine_id
            )
        )
        loading = self.preloading_tts_engine_id == engine_id
        if loaded:
            button.setText(self.tr("unload_from_memory", "Unload from memory"))
            button.setIcon(ui_icon("delete"))
            button.setEnabled(self.preload_thread is None and self.worker_thread is None)
        elif loading:
            button.setText(self.tr("loading_into_memory", "Loading into memory..."))
            button.setIcon(ui_icon("refresh"))
            button.setEnabled(False)
        else:
            button.setText(self.tr("load_into_memory", "Load into memory"))
            button.setIcon(ui_icon("open"))
            button.setEnabled(
                can_load and self.preload_thread is None and self.worker_thread is None
            )

    def _toggle_preloaded_tts_engine(self, engine_id: str) -> None:
        if engine_id in self.host_loaded_tts_engine_ids:
            self._start_engine_host_memory_action(engine_id, load=False)
            return
        self._start_preload_tts_engine(engine_id)

    def _start_preload_tts_engine(self, engine_id: str) -> None:
        if self.preload_thread is not None:
            return
        if engine_id not in {"kokoro", "chatterbox", "qwen", "omnivoice"}:
            return
        self._select_tts_engine(engine_id)
        voice_config = self._current_voice_config()
        if voice_config is None:
            return
        self._unload_preloaded_tts_engine(log_message=False)
        self._save_settings()
        self._start_engine_host_memory_action(
            engine_id,
            load=True,
            voice_config=voice_config,
        )

    def _start_engine_host_memory_action(
        self,
        engine_id: str,
        load: bool,
        voice_config: dict[str, object] | None = None,
    ) -> None:
        if self.preload_thread is not None:
            return
        self.preloading_tts_engine_id = engine_id
        self.log_view.append_event(
            self.tr(
                "preloading_engine" if load else "unloading_engine",
                "Loading {engine} into memory..."
                if load
                else "Unloading {engine} from memory...",
                engine=self._tts_engine_label(engine_id),
            )
        )
        self._refresh_all_engine_status()

        thread = QThread(self)
        worker = EngineHostMemoryWorker(
            self.engine_host_client,
            engine_id,
            load,
            voice_config,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_tts_engine_preloaded)
        worker.failed.connect(self._on_tts_engine_preload_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_preload_worker)
        self.preload_thread = thread
        self.preload_worker = worker
        thread.start()

    def _on_tts_engine_preloaded(
        self,
        engine_id: str,
        loaded: bool,
    ) -> None:
        if loaded:
            self.host_loaded_tts_engine_ids.add(engine_id)
            self.loaded_tts_engine_id = engine_id
        else:
            self.host_loaded_tts_engine_ids.discard(engine_id)
            if self.loaded_tts_engine_id == engine_id:
                self.loaded_tts_engine_id = None
        self.log_view.append_event(
            self.tr(
                "engine_loaded_in_memory" if loaded else "engine_unloaded_from_memory",
                "{engine} loaded in memory and waiting for requests."
                if loaded
                else "{engine} unloaded from memory.",
                engine=self._tts_engine_label(engine_id),
            )
        )
        self._refresh_all_engine_status()

    def _on_tts_engine_preload_failed(self, message: str) -> None:
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)
        self._unload_preloaded_tts_engine(log_message=False)

    def _clear_preload_worker(self) -> None:
        self.preload_worker = None
        self.preload_thread = None
        self.preloading_tts_engine_id = None
        self._refresh_all_engine_status()

    def _unload_preloaded_tts_engine(self, log_message: bool = True) -> None:
        if self.preload_worker is not None:
            self.preload_worker.request_cancel()
        engine_id = self.preloaded_tts_engine_id
        engine = self.preloaded_tts_engine
        self.preloaded_tts_engine = None
        self.preloaded_tts_engine_id = None
        if self.loaded_tts_engine_id == engine_id:
            self.loaded_tts_engine_id = None
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:
                self.log_view.append_event(f"TTS engine unload warning: {exc}")
        if log_message and engine_id:
            self.log_view.append_event(
                self.tr(
                    "engine_unloaded_from_memory",
                    "{engine} unloaded from memory.",
                    engine=self._tts_engine_label(engine_id),
                )
            )
        self._refresh_all_engine_status()

    def _refresh_all_engine_status(self) -> None:
        self._update_header_engine_label()
        self._refresh_tts_engine_table()
        self._refresh_generation_voice_combo()
        self._refresh_kokoro_python_status()
        self._refresh_chatterbox_status()
        self._refresh_qwen_status()
        self._refresh_omnivoice_status()
        self._refresh_custom_engine_panel()

    def _sync_engine_host_memory_state(self) -> None:
        if not self.engine_host_client.health(timeout=0.25):
            return
        try:
            status = self.engine_host_client.request_json(
                "GET",
                "/engines/memory",
                timeout=3.0,
            )
        except Exception as exc:
            self.log_view.append_event(f"Engine host status warning: {exc}")
            return
        if not isinstance(status, dict):
            return
        self.host_loaded_tts_engine_ids = {
            str(engine_id)
            for engine_id, details in status.items()
            if isinstance(details, dict) and bool(details.get("loaded", False))
        }
        selected_engine = str(self.tts_engine_combo.currentData() or "piper")
        if selected_engine in self.host_loaded_tts_engine_ids:
            self.loaded_tts_engine_id = selected_engine
        self._refresh_all_engine_status()

    def _build_general_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        narration_group = QGroupBox(
            self.tr("narration_settings", "Narration and export")
        )
        narration_form = QFormLayout(narration_group)
        narration_form.setSpacing(10)

        editor_group = QGroupBox(
            self.tr("text_editor_settings", "Text Editor")
        )
        editor_form = QFormLayout(editor_group)
        editor_form.setSpacing(10)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix("x")

        self.split_combo = QComboBox()
        self.split_combo.addItem(
            ui_icon("file"),
            self.tr("split_safe", "Split by safe chunks"),
            "safe_chunks",
        )
        self.split_combo.addItem(
            ui_icon("file"),
            self.tr("split_chapters", "Split by chapters/headings"),
            "chapters",
        )

        self.export_combo = QComboBox()
        self.export_combo.addItem(
            ui_icon("save"),
            self.tr("export_single", "Single MP3"),
            "single",
        )
        self.export_combo.addItem(
            ui_icon("save"),
            self.tr("export_chapters", "MP3 per chapter/block"),
            "chapters",
        )

        self.editor_highlighting_checkbox = QCheckBox(
            self.tr("editor_syntax_highlighting", "Highlight markup commands in editor")
        )
        self.editor_highlighting_checkbox.toggled.connect(
            self._set_editor_highlighting_enabled
        )
        self.markup_toolbar_checkbox = QCheckBox(
            self.tr("show_markup_toolbar", "Show markup toolbar")
        )
        self.markup_toolbar_checkbox.toggled.connect(self._set_markup_toolbar_visible)
        self.markup_corrector_checkbox = QCheckBox(
            self.tr("markup_corrector_enabled", "Markup corrector enabled")
        )
        self.markup_corrector_checkbox.toggled.connect(
            self._set_markup_corrector_enabled
        )

        output_path = str(resolve_app_path(self.settings.get("output_dir", "output")))
        self.output_picker = PathPicker(
            self.tr("browse", "Browse"),
            output_path,
        )
        self.normalize_checkbox = QCheckBox(
            self.tr("normalize_clean_audio", "Normalize clean narration")
        )
        normalize_help = QLabel(
            self.tr(
                "normalize_clean_audio_help",
                "Uses FFmpeg loudness normalization (-16 LUFS) when encoding the clean narration MP3. "
                "It helps segments feel more even in perceived volume, but it is not a full studio compressor.",
            )
        )
        normalize_help.setObjectName("helperLabel")
        normalize_help.setWordWrap(True)
        self.open_folder_checkbox = QCheckBox(
            self.tr("open_output", "Open output folder when finished")
        )
        self.podcast_enabled_checkbox = QCheckBox(
            self.tr("create_podcast_mix", "Create podcast mix")
        )
        self.background_enabled_checkbox = QCheckBox(
            self.tr("background_music", "Background music")
        )
        self.background_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )

        narration_form.addRow(self.tr("voice_speed", "Voice speed"), self.speed_spin)
        narration_form.addRow(self.tr("export_mode", "Output type"), self.export_combo)
        narration_form.addRow(
            self.tr("output_folder", "Output folder"),
            self.output_picker,
        )
        narration_form.addRow("", self.normalize_checkbox)
        narration_form.addRow("", normalize_help)

        editor_form.addRow("", self.editor_highlighting_checkbox)
        editor_form.addRow("", self.markup_toolbar_checkbox)
        editor_form.addRow("", self.markup_corrector_checkbox)

        cache_group = QGroupBox(
            self.tr("temporary_audio_cache", "Temporary audio cache")
        )
        cache_layout = QVBoxLayout(cache_group)
        cache_layout.setSpacing(8)
        cache_help = QLabel(
            self.tr(
                "temporary_audio_cache_help",
                "Segment WAV files are kept for review and regeneration. "
                "You can delete them after the final mix to free disk space.",
            )
        )
        cache_help.setObjectName("helperLabel")
        cache_help.setWordWrap(True)
        self.auto_delete_segment_wavs_checkbox = QCheckBox(
            self.tr(
                "auto_delete_segment_wavs_after_mix",
                "Delete segment WAV cache after rendering the full mix",
            )
        )
        self.wav_cache_stats_label = QLabel("")
        self.wav_cache_stats_label.setObjectName("helperLabel")
        cleanup_buttons = QHBoxLayout()
        self.cleanup_current_wavs_button = QPushButton(
            self.tr("cleanup_current_project_wavs", "Clean current project WAVs")
        )
        self.cleanup_current_wavs_button.setIcon(ui_icon("delete"))
        self.cleanup_current_wavs_button.clicked.connect(
            self._cleanup_current_project_wavs
        )
        self.cleanup_all_wavs_button = QPushButton(
            self.tr("cleanup_all_project_wavs", "Clean all project WAVs")
        )
        self.cleanup_all_wavs_button.setIcon(ui_icon("delete"))
        self.cleanup_all_wavs_button.clicked.connect(self._cleanup_all_project_wavs)
        self.cleanup_temp_audio_button = QPushButton(
            self.tr("cleanup_abandoned_temp_audio", "Clean abandoned temp audio")
        )
        self.cleanup_temp_audio_button.setIcon(ui_icon("delete"))
        self.cleanup_temp_audio_button.clicked.connect(
            self._cleanup_abandoned_temp_audio
        )
        cleanup_buttons.addWidget(self.cleanup_current_wavs_button)
        cleanup_buttons.addWidget(self.cleanup_all_wavs_button)
        cleanup_buttons.addWidget(self.cleanup_temp_audio_button)
        cleanup_buttons.addStretch(1)
        cache_layout.addWidget(cache_help)
        cache_layout.addWidget(self.auto_delete_segment_wavs_checkbox)
        cache_layout.addWidget(self.wav_cache_stats_label)
        cache_layout.addLayout(cleanup_buttons)

        grid.addWidget(narration_group, 0, 0)
        grid.addWidget(editor_group, 0, 1)
        grid.addWidget(cache_group, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _set_editor_highlighting_enabled(self, enabled: bool) -> None:
        if hasattr(self, "markup_highlighter"):
            self.markup_highlighter.set_enabled(enabled)

    def _set_markup_corrector_enabled(self, enabled: bool) -> None:
        if hasattr(self, "markup_highlighter"):
            self.markup_highlighter.set_corrector_enabled(enabled)

    def _set_markup_toolbar_visible(self, visible: bool) -> None:
        self.settings["show_markup_toolbar"] = bool(visible)
        if hasattr(self, "markup_toolbar"):
            self.markup_toolbar.setVisible(bool(visible))
        if hasattr(self, "markup_toolbar_checkbox"):
            self.markup_toolbar_checkbox.blockSignals(True)
            self.markup_toolbar_checkbox.setChecked(bool(visible))
            self.markup_toolbar_checkbox.blockSignals(False)
        if hasattr(self, "markup_toolbar_action"):
            self.markup_toolbar_action.blockSignals(True)
            self.markup_toolbar_action.setChecked(bool(visible))
            self.markup_toolbar_action.blockSignals(False)

    def _build_advanced_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)

        pause_group = QGroupBox(
            self.tr("natural_pauses", "Natural paragraph pauses")
        )
        pause_form = QFormLayout(pause_group)
        self.adaptive_pause_checkbox = QCheckBox(
            self.tr(
                "adaptive_pause",
                "Adapt pause to paragraph length and reading rhythm",
            )
        )
        pause_form.addRow("", self.adaptive_pause_checkbox)

        paragraph_pause_row = QWidget()
        paragraph_pause_layout = QHBoxLayout(paragraph_pause_row)
        paragraph_pause_layout.setContentsMargins(0, 0, 0, 0)
        paragraph_pause_layout.setSpacing(6)
        self.paragraph_pause_min_spin = self._seconds_spin(0.45)
        self.paragraph_pause_max_spin = self._seconds_spin(0.90)
        self.paragraph_pause_min_spin.valueChanged.connect(
            self.paragraph_pause_max_spin.setMinimum
        )
        paragraph_pause_layout.addWidget(self.paragraph_pause_min_spin)
        paragraph_pause_layout.addWidget(QLabel(self.tr("to", "to")))
        paragraph_pause_layout.addWidget(self.paragraph_pause_max_spin)
        pause_form.addRow(
            self.tr("base_paragraph_pause", "Base paragraph pause"),
            paragraph_pause_row,
        )

        self.paragraph_length_reference_spin = QSpinBox()
        self.paragraph_length_reference_spin.setRange(50, 5000)
        self.paragraph_length_reference_spin.setSuffix(
            self.tr("characters_suffix", " chars")
        )
        self.paragraph_length_extra_spin = self._seconds_spin(0.65)
        pause_form.addRow(
            self.tr("long_paragraph_reference", "Long paragraph reference"),
            self.paragraph_length_reference_spin,
        )
        pause_form.addRow(
            self.tr("long_paragraph_extra", "Extra pause after long paragraph"),
            self.paragraph_length_extra_spin,
        )

        self.periodic_pause_every_spin = QSpinBox()
        self.periodic_pause_every_spin.setRange(0, 100)
        self.periodic_pause_every_spin.setSpecialValueText(
            self.tr("disabled", "Disabled")
        )
        periodic_pause_row = QWidget()
        periodic_pause_layout = QHBoxLayout(periodic_pause_row)
        periodic_pause_layout.setContentsMargins(0, 0, 0, 0)
        periodic_pause_layout.setSpacing(6)
        self.periodic_pause_min_spin = self._seconds_spin(0.35)
        self.periodic_pause_max_spin = self._seconds_spin(0.75)
        self.periodic_pause_min_spin.valueChanged.connect(
            self.periodic_pause_max_spin.setMinimum
        )
        periodic_pause_layout.addWidget(self.periodic_pause_min_spin)
        periodic_pause_layout.addWidget(QLabel(self.tr("to", "to")))
        periodic_pause_layout.addWidget(self.periodic_pause_max_spin)
        pause_form.addRow(
            self.tr("periodic_pause_every", "Extra pause every N paragraphs"),
            self.periodic_pause_every_spin,
        )
        pause_form.addRow(
            self.tr("periodic_pause_duration", "Periodic extra pause"),
            periodic_pause_row,
        )

        podcast_group = QGroupBox(
            self.tr("podcast_mix_settings", "Podcast mix")
        )
        podcast_form = QFormLayout(podcast_group)
        self.background_loop_checkbox = QCheckBox(
            self.tr("loop_background", "Loop background during narration")
        )
        self.voice_volume_db_spin = self._db_spin(-12.0, 6.0, 0.0)
        self.background_volume_spin = self._db_spin(-36.0, 0.0, -7.0)
        self.voice_start_offset_spin = self._milliseconds_spin(
            -300000,
            300000,
            2000,
        )
        self.music_tail_spin = self._milliseconds_spin(0, 600000, 2000)
        self.fade_in_spin = self._seconds_spin(1.0)
        self.fade_out_spin = self._seconds_spin(1.0)
        self.podcast_gap_spin = self._seconds_spin(0.5)
        self.podcast_normalize_checkbox = QCheckBox(
            self.tr("normalize_podcast", "Normalize podcast output to -16 LUFS")
        )
        self.podcast_ducking_checkbox = QCheckBox(
            self.tr(
                "enable_ducking",
                "Lower background music while narration is speaking",
            )
        )
        self.ducking_strength_combo = QComboBox()
        self.ducking_strength_combo.addItem(self.tr("ducking_low", "Low"), "low")
        self.ducking_strength_combo.addItem(
            self.tr("ducking_medium", "Medium"),
            "medium",
        )
        self.ducking_strength_combo.addItem(self.tr("ducking_high", "High"), "high")
        podcast_form.addRow("", self.background_loop_checkbox)
        podcast_form.addRow(
            self.tr("voice_volume", "Voice volume"),
            self.voice_volume_db_spin,
        )
        podcast_form.addRow(
            self.tr("music_volume", "Music volume"),
            self.background_volume_spin,
        )
        podcast_form.addRow(
            self.tr("voice_start_offset", "Voice start offset"),
            self.voice_start_offset_spin,
        )
        podcast_form.addRow(
            self.tr("music_tail", "Music after voice"),
            self.music_tail_spin,
        )
        podcast_form.addRow(
            self.tr("music_fade_in", "Music fade in"),
            self.fade_in_spin,
        )
        podcast_form.addRow(
            self.tr("music_fade_out", "Music fade out"),
            self.fade_out_spin,
        )
        podcast_form.addRow(
            self.tr("podcast_gap", "Silence between sections"),
            self.podcast_gap_spin,
        )
        podcast_form.addRow("", self.podcast_normalize_checkbox)
        podcast_form.addRow("", self.podcast_ducking_checkbox)
        podcast_form.addRow(
            self.tr("ducking_strength", "Ducking strength"),
            self.ducking_strength_combo,
        )

        splitting_group = QGroupBox(
            self.tr("text_splitting_settings", "Text splitting")
        )
        splitting_form = QFormLayout(splitting_group)
        splitting_form.setSpacing(10)
        splitting_help = QLabel(
            self.tr(
                "text_splitting_help",
                "Default chunking applies to every engine unless a specific engine override is set.",
            )
        )
        splitting_help.setObjectName("helperLabel")
        splitting_help.setWordWrap(True)
        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(1, 5000)
        self.chunk_size_spin.setSingleStep(20)
        self.chunk_size_spin.setSuffix(self.tr("characters_suffix", " chars"))
        self.chatterbox_chunk_size_spin = self._engine_chunk_spin()
        self.qwen_chunk_size_spin = self._engine_chunk_spin()
        self.omnivoice_chunk_size_spin = self._engine_chunk_spin()
        self.kokoro_chunk_size_spin = self._engine_chunk_spin()
        self.piper_chunk_size_spin = self._engine_chunk_spin()
        splitting_form.addRow(self.tr("split_mode", "Text splitting"), self.split_combo)
        splitting_form.addRow(
            self.tr("default_chunk_size", "Default chunk size"),
            self.chunk_size_spin,
        )
        splitting_form.addRow(
            self.tr("piper_chunk_size", "Piper override"),
            self.piper_chunk_size_spin,
        )
        splitting_form.addRow(
            self.tr("kokoro_chunk_size", "Kokoro override"),
            self.kokoro_chunk_size_spin,
        )
        splitting_form.addRow(
            self.tr("chatterbox_chunk_size", "Chatterbox override"),
            self.chatterbox_chunk_size_spin,
        )
        splitting_form.addRow(
            self.tr("qwen_chunk_size", "Qwen override"),
            self.qwen_chunk_size_spin,
        )
        splitting_form.addRow(
            self.tr("omnivoice_chunk_size", "OmniVoice override"),
            self.omnivoice_chunk_size_spin,
        )
        splitting_form.addRow("", splitting_help)

        grid.addWidget(pause_group, 0, 0)
        grid.addWidget(podcast_group, 0, 1)
        grid.addWidget(splitting_group, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _seconds_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 30.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def _engine_chunk_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 5000)
        spin.setSingleStep(20)
        spin.setSpecialValueText(self.tr("use_default", "Use default"))
        spin.setSuffix(self.tr("characters_suffix", " chars"))
        return spin

    def _current_chunk_size(self, engine_id: str | None = None) -> int:
        default_size = max(
            1,
            int(
                self.chunk_size_spin.value()
                if hasattr(self, "chunk_size_spin")
                else self.settings.get("chunk_size", 2500)
            ),
        )
        engine = str(
            engine_id
            or (
                self.tts_engine_combo.currentData()
                if hasattr(self, "tts_engine_combo")
                else self.settings.get("tts_engine", "piper")
            )
            or "piper"
        )
        spin_map = {
            "piper": getattr(self, "piper_chunk_size_spin", None),
            "kokoro": getattr(self, "kokoro_chunk_size_spin", None),
            "chatterbox": getattr(self, "chatterbox_chunk_size_spin", None),
            "qwen": getattr(self, "qwen_chunk_size_spin", None),
            "omnivoice": getattr(self, "omnivoice_chunk_size_spin", None),
        }
        override = 0
        spin = spin_map.get(engine)
        if spin is not None:
            override = int(spin.value())
        else:
            overrides = self.settings.get("engine_chunk_sizes", {})
            if isinstance(overrides, dict):
                try:
                    override = int(overrides.get(engine, 0) or 0)
                except (TypeError, ValueError):
                    override = 0
        if 0 < override < 1:
            override = 1
        return override if override > 0 else default_size

    @staticmethod
    def _db_spin(
        minimum: float,
        maximum: float,
        value: float,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setSuffix(" dB")
        spin.setValue(value)
        return spin

    @staticmethod
    def _milliseconds_spin(
        minimum: int,
        maximum: int,
        value: int,
    ) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(100)
        spin.setSuffix(" ms")
        spin.setValue(value)
        return spin

    def _build_log_panel(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel(self.tr("log", "Log"))
        title.setObjectName("sectionLabel")
        layout.addWidget(title)
        self.log_view = LogView()
        layout.addWidget(self.log_view)
        return widget

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: transparent;
            }
            QWidget {
                background: #f8fafc;
                color: #111827;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QWidget#rootWidget {
                background: transparent;
            }
            QFrame#appFrame {
                background: #f8fafc;
                border: 1px solid rgba(148, 163, 184, 0.35);
                border-radius: 10px;
            }
            QWidget#contentArea {
                background: #f8fafc;
            }
            QWidget#appBody {
                background: #f8fafc;
            }
            QFrame#windowTitleBar {
                background: #ffffff;
                border-bottom: 1px solid #e5eaf3;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel#titleBarLogoLabel, QLabel#menuLogoLabel {
                background: transparent;
            }
            QMenuBar#appMenuBar {
                background: transparent;
                border: none;
                color: #374151;
                spacing: 2px;
                padding: 0;
                margin: 0;
            }
            QMenuBar#appMenuBar::item {
                background: transparent;
                border-radius: 6px;
                padding: 6px 10px;
                margin: 0 1px;
            }
            QMenuBar#appMenuBar::item:selected {
                background: #eef2f7;
                color: #111827;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #dbe3ef;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 28px 7px 12px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #eef5ff;
                color: #1769ff;
            }
            QPushButton#titleBarButton,
            QPushButton#titleBarCloseButton {
                background: transparent;
                border: none;
                border-radius: 0px;
                color: #475569;
                font-weight: 700;
                min-width: 42px;
                max-width: 42px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
            }
            QPushButton#titleBarButton:hover {
                background: #edf2f7;
                color: #111827;
            }
            QPushButton#titleBarCloseButton:hover {
                background: #e81123;
                color: #ffffff;
            }
            QFrame#sidebar {
                background: #ffffff;
                border-right: 1px solid #e5e7eb;
            }
            QLabel#closeDot, QLabel#minDot, QLabel#maxDot {
                border-radius: 6px;
            }
            QLabel#closeDot { background: #ff5f57; }
            QLabel#minDot { background: #ffbd2e; }
            QLabel#maxDot { background: #28c840; }
            QLabel#sidebarTitleLabel {
                color: #111827;
                font-size: 13pt;
                font-weight: 700;
            }
            QLabel#sidebarSubtitleLabel {
                color: #7c8798;
                font-size: 8.5pt;
            }
            QPushButton#navButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                color: #1f2937;
                text-align: left;
                padding: 10px 12px;
                font-weight: 500;
            }
            QPushButton#navButton:hover {
                background: #f3f6fb;
            }
            QPushButton#navButton[active="true"] {
                background: #eef5ff;
                border-color: #cfe0ff;
                color: #1769ff;
                font-weight: 700;
            }
            QFrame#sidebarStatusCard {
                background: #ffffff;
                border: 1px solid #e5eaf3;
                border-radius: 10px;
            }
            QLabel#engineStatusIcon {
                background: #1769ff;
                border-radius: 8px;
                padding: 6px;
            }
            QLabel#sidebarStatusTitle {
                color: #111827;
                font-weight: 700;
            }
            QLabel#sidebarStatusText, QLabel#sidebarReadyText {
                color: #6b7280;
                font-size: 8.5pt;
            }
            QLabel#sidebarReadyText {
                color: #16a34a;
            }
            QLabel#pageIconLabel {
                background: #1769ff;
                border-radius: 12px;
                padding: 8px;
            }
            QLabel#pageTitleLabel {
                color: #111827;
                font-size: 20pt;
                font-weight: 800;
            }
            QFrame#card, QTabWidget::pane {
                background: white;
                border: 1px solid #e5eaf3;
                border-radius: 12px;
            }
            QFrame#card QLabel, QTabWidget::pane QLabel {
                background: transparent;
                border: none;
            }
            QLabel#titleLabel {
                color: #162033;
                font-size: 24pt;
                font-weight: 700;
                background: transparent;
            }
            QLabel#subtitleLabel, QLabel#helperLabel {
                color: #667085;
                background: transparent;
            }
            QLabel#authorCreditLabel {
                color: #657084;
                font-size: 9pt;
            }
            QLabel#authorCreditLabel a {
                color: #3859d9;
                text-decoration: none;
            }
            QLabel#sectionLabel {
                font-size: 12pt;
                font-weight: 700;
                background: transparent;
                border: none;
            }
            QFrame#inlineStatusFrame {
                background: #f7f9fd;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
            QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox {
                background: #ffffff;
                border: 1px solid #dbe2ec;
                border-radius: 8px;
                padding: 8px;
                selection-background-color: #1769ff;
            }
            QTextEdit:focus, QPlainTextEdit:focus, QLineEdit:focus,
            QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #1769ff;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #dbe2ec;
                border-radius: 8px;
                padding: 9px 14px;
                color: #1f2937;
            }
            QPushButton:hover {
                background: #f6f8fb;
            }
            QPushButton#inlineActionButton {
                background: transparent;
                border: 1px solid #dbe2ec;
                border-radius: 6px;
                padding: 3px 8px;
                color: #1769ff;
                font-size: 8.5pt;
                font-weight: 600;
            }
            QPushButton#inlineActionButton:hover {
                background: #eef5ff;
                border-color: #cfe0ff;
            }
            QPushButton#primaryButton {
                background: #1769ff;
                border-color: #1769ff;
                color: white;
                font-weight: 700;
                padding: 9px 18px;
            }
            QPushButton#primaryButton:hover {
                background: #0f58dd;
            }
            QPushButton#dangerButton {
                border-color: #ffb4b4;
                color: #dc2626;
                background: #fffafa;
                font-weight: 600;
            }
            QPushButton:disabled {
                background: #f1f5f9;
                color: #98a2b3;
                border-color: #e5e7eb;
            }
            QProgressBar {
                border: 1px solid #dbe2ec;
                border-radius: 8px;
                background: #f1f5f9;
                text-align: center;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background: #1769ff;
                border-radius: 7px;
            }
            QTabBar::tab {
                background: #f3f6fb;
                border: 1px solid #e0e6ef;
                padding: 8px 18px;
            }
            QTabBar::tab:selected {
                background: white;
                color: #1769ff;
                font-weight: 700;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #dbeafe;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                width: 16px;
                height: 16px;
                margin: -7px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #1769ff;
                border-radius: 2px;
            }
            QScrollBar:horizontal {
                background: #eef2f7;
                height: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #94a3b8;
                border-radius: 6px;
            }
            """
        )

    def _load_voices(self) -> None:
        self.voices = VoiceManager(application_root() / "voices").discover()
        current_language = self.language_combo.currentData()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(
            ui_icon("language"),
            self.tr("all_languages", "All languages"),
            "",
        )
        for language in sorted({voice.language for voice in self.voices}):
            self.language_combo.addItem(ui_icon("language"), language, language)
        self.language_combo.blockSignals(False)
        self._select_combo_data(self.language_combo, current_language or "")
        self._filter_voices()
        self._refresh_tts_engine_table()

    def _refresh_voices(self) -> None:
        selected_voice = self.voice_combo.currentData()
        self._load_voices()
        self._select_combo_data(self.voice_combo, selected_voice)
        self.log_view.append_event(
            self.tr(
                "voices_found",
                "Discovered {count} valid Piper voice(s).",
                count=len(self.voices),
            )
        )
        self._refresh_generation_voice_combo()

    def _open_voice_manager(self) -> None:
        dialog = VoiceManagerDialog(
            application_root() / "voices",
            self.translator,
            self,
        )
        dialog.voices_changed.connect(self._refresh_voices)
        dialog.exec()
        self._refresh_voices()

    def _filter_voices(self) -> None:
        language = self.language_combo.currentData() or ""
        selected_voice = self.voice_combo.currentData()
        self.voice_combo.clear()
        matching = [
            voice for voice in self.voices if not language or voice.language == language
        ]
        for voice in matching:
            self.voice_combo.addItem(
                ui_icon("voice"),
                voice.display_name,
                voice.voice_id,
            )
        self._select_combo_data(self.voice_combo, selected_voice)
        if not matching:
            self.voice_combo.addItem(
                ui_icon("info"),
                self.tr("no_voices", "No valid Piper voices found"),
                None,
            )
        self._refresh_generation_voice_combo()

    def _refresh_generation_voice_combo(self) -> None:
        if not hasattr(self, "generation_voice_combo"):
            return
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        rows = self._generation_voice_rows(engine_id)
        selected_index = 0
        self.generation_voice_combo.blockSignals(True)
        self.generation_voice_combo.clear()
        for index, row in enumerate(rows):
            label = str(row.get("name", ""))
            language = str(row.get("language", ""))
            if language and language not in label and engine_id not in {"qwen"}:
                label = f"{label} - {language}"
            self.generation_voice_combo.addItem(ui_icon("voice"), label, row)
            if bool(row.get("selected")):
                selected_index = index
        if rows:
            self.generation_voice_combo.setCurrentIndex(selected_index)
            self.generation_voice_combo.setEnabled(True)
        else:
            self.generation_voice_combo.addItem(
                ui_icon("info"),
                self.tr("no_voices", "No voices available"),
                None,
            )
            self.generation_voice_combo.setEnabled(False)
        self.generation_voice_combo.blockSignals(False)

    def _generation_voice_rows(self, engine_id: str) -> list[dict[str, object]]:
        rows = self._voice_page_rows(engine_id)
        usable_rows: list[dict[str, object]] = []
        for row in rows:
            gallery_voice = row.get("gallery_voice")
            if engine_id == "chatterbox" and not bool(row.get("installed")):
                continue
            if (
                isinstance(gallery_voice, GalleryVoice)
                and gallery_voice.is_reference_audio
                and not bool(row.get("installed"))
            ):
                continue
            usable_rows.append(row)
        return usable_rows

    def _on_generation_voice_selected(self, index: int) -> None:
        if index < 0 or not hasattr(self, "generation_voice_combo"):
            return
        row = self.generation_voice_combo.itemData(index)
        if not isinstance(row, dict):
            return
        self._select_voice_page_row_data(row, refresh=False)
        if hasattr(self, "page_stack") and self.page_stack.currentIndex() == 5:
            self._refresh_voices_page()

    def _refresh_voices_page(self) -> None:
        if not hasattr(self, "voices_table"):
            return
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        engine_label = self._tts_engine_label(engine_id)
        self.voices_engine_label.setText(
            self.tr(
                "voices_for_selected_engine",
                "Showing voices compatible with the selected engine: {engine}",
                engine=engine_label,
            )
        )
        self.voices_manage_button.setText(self._voices_manage_button_text(engine_id))
        self.voices_manage_button.setEnabled(engine_id in {"piper", "chatterbox", "omnivoice"})
        self.voices_design_button.setVisible(engine_id == "omnivoice")
        self.voices_design_button.setEnabled(
            engine_id == "omnivoice" and self.omnivoice_manager.is_installed()
        )
        self.voice_page_rows = self._filter_voice_page_rows(
            self._voice_page_rows(engine_id)
        )
        self.voices_table.setSortingEnabled(False)
        self.voices_table.setRowCount(0)
        for row_index, row in enumerate(self.voice_page_rows):
            self.voices_table.insertRow(row_index)
            selected = bool(row.get("selected"))
            values = (
                self.tr("selected", "Selected") if selected else "",
                str(row.get("name", "")),
                str(row.get("short_description", "")),
                str(row.get("language", "")),
                str(row.get("gender", "")),
                str(row.get("age_style", "")),
                str(row.get("voice_style", "")),
                str(row.get("type", "")),
                str(row.get("status", "")),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row)
                if column == 0:
                    if selected:
                        item.setIcon(ui_icon("apply", active=True))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif column in {3, 4, 5, 6, 7, 8}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.voices_table.setItem(row_index, column, item)
            self.voices_table.setCellWidget(
                row_index,
                9,
                self._voice_actions_widget(row),
            )
            self.voices_table.setRowHeight(row_index, 42)
        self.voices_status_label.setText(self._voices_status_text(engine_id))
        self.voices_table.setSortingEnabled(True)
        self.voices_table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.voices_table.resizeRowsToContents()
        self._refresh_generation_voice_combo()

    def _filter_voice_page_rows(
        self,
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not hasattr(self, "voices_filter_edit"):
            return rows
        query = self.voices_filter_edit.text().strip().casefold()
        if not query:
            return rows
        field = str(self.voices_filter_field_combo.currentData() or "all")
        field_map = {
            "name": ("name",),
            "short_description": ("short_description",),
            "language": ("language",),
            "gender": ("gender",),
            "age_style": ("age_style",),
            "voice_style": ("voice_style",),
            "tags": ("tags",),
            "all": (
                "name",
                "short_description",
                "language",
                "gender",
                "age_style",
                "voice_style",
                "type",
                "status",
                "tags",
            ),
        }
        keys = field_map.get(field, field_map["all"])
        filtered: list[dict[str, object]] = []
        for row in rows:
            haystack = " ".join(str(row.get(key, "")) for key in keys).casefold()
            if query in haystack:
                filtered.append(row)
        return filtered

    def _voices_manage_button_text(self, engine_id: str) -> str:
        if engine_id == "piper":
            return self.tr("download_piper_voices", "Download Piper voices")
        if engine_id == "chatterbox":
            return self.tr("import_reference_voice", "Import reference voice")
        if engine_id == "omnivoice":
            return self.tr("import_reference_voice", "Import reference voice")
        return self.tr("manage", "Manage")

    def _voices_status_text(self, engine_id: str) -> str:
        if engine_id == "piper":
            return self.tr(
                "piper_voices_page_help",
                "Piper voices are local .onnx models. Use Manage to browse and download from rhasspy/piper-voices.",
            )
        if engine_id == "kokoro":
            return self.tr(
                "kokoro_voices_page_help",
                "Kokoro voices are included in the Kokoro voice asset downloaded with the engine.",
            )
        if engine_id == "qwen":
            return self.tr(
                "qwen_voices_page_help",
                "Qwen speakers are built into the selected Qwen3 TTS model.",
            )
        if engine_id == "omnivoice":
            return self.tr(
                "omnivoice_voices_page_help",
                "OmniVoice uses gallery voices as cloning references. Selecting a voice downloads its preview/reference audio when needed.",
            )
        if engine_id == "chatterbox":
            return self.tr(
                "chatterbox_voices_page_help",
                "Chatterbox uses the same gallery reference voices as OmniVoice. Selecting a voice downloads its preview/reference audio when needed.",
            )
        return self.tr(
            "api_voices_page_help",
            "API voices are configured locally and used only when that provider is selected.",
        )

    def _voice_page_rows(self, engine_id: str) -> list[dict[str, object]]:
        if engine_id == "piper":
            selected_id = self.voice_combo.currentData()
            rows = [
                {
                    "engine": "piper",
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": voice.language,
                    "type": self.tr("local_model", "Local model"),
                    "status": self.tr("installed", "Installed"),
                    "selected": voice.voice_id == selected_id,
                    "voice": voice,
                }
                for voice in self.voices
            ]
            return self._merge_voice_gallery_rows(engine_id, rows)
        if engine_id == "kokoro":
            selected_id = self.kokoro_python_voice_combo.currentData()
            status = (
                self.tr("installed", "Installed")
                if self.kokoro_python_manager.is_installed()
                else self.tr("not_installed", "Not installed")
            )
            rows = [
                {
                    "engine": "kokoro",
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": voice.language,
                    "type": self.tr("local_voice", "Local voice"),
                    "status": status,
                    "selected": voice.voice_id == selected_id,
                    "voice": voice,
                }
                for voice in self.kokoro_python_manager.list_voices()
            ]
            return self._merge_voice_gallery_rows(engine_id, rows)
        if engine_id == "qwen":
            selected_speaker = self.qwen_speaker_combo.currentData()
            selected_language = self.qwen_language_combo.currentData()
            status = (
                self.tr("installed", "Installed")
                if self.qwen_manager.is_installed()
                else self.tr("not_installed", "Not installed")
            )
            rows: list[dict[str, object]] = []
            for language in self.qwen_manager.list_languages():
                for voice in self.qwen_manager.list_voices():
                    rows.append(
                        {
                            "engine": "qwen",
                            "id": f"{language.language_id}:{voice.voice_id}",
                            "speaker_id": voice.voice_id,
                            "language_id": language.language_id,
                            "name": f"{voice.display_name} - {language.display_name}",
                            "language": language.display_name,
                            "type": self.tr("model_speaker", "Model speaker"),
                            "status": status,
                            "selected": (
                                voice.voice_id == selected_speaker
                                and language.language_id == selected_language
                            ),
                            "voice": voice,
                        }
                    )
            return self._merge_voice_gallery_rows(engine_id, rows)
        if engine_id == "omnivoice":
            return self._merge_voice_gallery_rows(engine_id, [])
        if engine_id == "chatterbox":
            return self._merge_voice_gallery_rows(engine_id, [])
        if engine_id == "openai":
            selected = self.openai_voice_combo.currentData()
            return [
                {
                    "engine": "openai",
                    "id": self.openai_voice_combo.itemData(index),
                    "name": self.openai_voice_combo.itemText(index),
                    "language": self.tr("multi_language", "Multilingual"),
                    "type": self.tr("api_voice", "API voice"),
                    "status": self.tr("ready", "Ready"),
                    "selected": self.openai_voice_combo.itemData(index) == selected,
                }
                for index in range(self.openai_voice_combo.count())
            ]
        if engine_id == "gemini":
            selected = self.gemini_voice_combo.currentData()
            return [
                {
                    "engine": "gemini",
                    "id": self.gemini_voice_combo.itemData(index),
                    "name": self.gemini_voice_combo.itemText(index),
                    "language": self.tr("multi_language", "Multilingual"),
                    "type": self.tr("api_voice", "API voice"),
                    "status": self.tr("ready", "Ready"),
                    "selected": self.gemini_voice_combo.itemData(index) == selected,
                }
                for index in range(self.gemini_voice_combo.count())
            ]
        if engine_id == "elevenlabs":
            voice_id = self.elevenlabs_voice_id_edit.text().strip()
            return [
                {
                    "engine": "elevenlabs",
                    "id": voice_id,
                    "name": voice_id or self.tr("not_configured", "Not configured"),
                    "language": self.tr("depends_on_voice", "Depends on voice"),
                    "type": self.tr("api_voice_id", "API voice ID"),
                    "status": (
                        self.tr("configured", "Configured")
                        if voice_id
                        else self.tr("not_configured", "Not configured")
                    ),
                    "selected": bool(voice_id),
                }
            ]
        if engine_id == "azure":
            voice = self.azure_voice_edit.text().strip()
            return [
                {
                    "engine": "azure",
                    "id": voice,
                    "name": voice or self.tr("not_configured", "Not configured"),
                    "language": voice.split("-", 2)[0] if "-" in voice else "",
                    "type": self.tr("api_voice_name", "API voice name"),
                    "status": (
                        self.tr("configured", "Configured")
                        if voice
                        else self.tr("not_configured", "Not configured")
                    ),
                    "selected": bool(voice),
                }
            ]
        if engine_id.startswith("custom:"):
            engine = self._custom_engine_by_key(engine_id)
            if engine is None:
                return []
            voice = str(engine.get("voice", "")).strip()
            language = str(engine.get("language", "")).strip()
            return [
                {
                    "engine": engine_id,
                    "id": voice or str(engine.get("id", "")),
                    "name": voice or str(engine.get("name", "")),
                    "language": language or self.tr("depends_on_engine", "Depends on engine"),
                    "type": self.tr("custom_engine_voice", "Custom engine voice"),
                    "status": self.tr("configured", "Configured"),
                    "selected": True,
                }
            ]
        return []

    def _merge_voice_gallery_rows(
        self,
        engine_id: str,
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        gallery_voices = self.voice_gallery_manager.list_voices(engine_id)
        matched: set[str] = set()
        merged: list[dict[str, object]] = []
        for row in rows:
            gallery_voice = self._matching_gallery_voice(engine_id, row, gallery_voices)
            if gallery_voice is not None:
                row = dict(row)
                row["gallery_voice"] = gallery_voice
                row["preview_source"] = self.voice_gallery_manager.preview_source(gallery_voice)
                row["short_description"] = gallery_voice.short_description
                row["gender"] = gallery_voice.gender
                row["age_style"] = gallery_voice.age_style
                row["voice_style"] = gallery_voice.voice_style
                row["tags"] = ", ".join(gallery_voice.tags)
                if gallery_voice.voice_type:
                    row["type"] = gallery_voice.voice_type
                if gallery_voice.is_builtin:
                    row["status"] = self.tr("built_in", "Built in")
                elif self.voice_gallery_manager.is_installed(gallery_voice):
                    row["status"] = self.tr("installed", "Installed")
                    row["installed"] = True
                    if gallery_voice.installed_path:
                        row["path"] = Path(gallery_voice.installed_path)
                else:
                    row["status"] = self.tr("available", "Available")
                    row["installed"] = False
                matched.add(gallery_voice.voice_id)
            merged.append(row)

        for gallery_voice in gallery_voices:
            if gallery_voice.voice_id in matched:
                continue
            merged.append(self._gallery_voice_row(engine_id, gallery_voice))
        return merged

    def _matching_gallery_voice(
        self,
        engine_id: str,
        row: dict[str, object],
        gallery_voices: list[GalleryVoice],
    ) -> GalleryVoice | None:
        for voice in gallery_voices:
            if engine_id == "qwen":
                if (
                    str(row.get("speaker_id", "")).casefold()
                    == (voice.speaker_id or voice.engine_voice_id).casefold()
                    and str(row.get("language_id", "")).casefold()
                    == (voice.language_name or voice.language).casefold()
                ):
                    return voice
            elif engine_id == "kokoro":
                if str(row.get("id", "")).casefold() == (
                    voice.engine_voice_id or voice.voice_id
                ).casefold():
                    return voice
            elif engine_id == "chatterbox":
                if str(row.get("name", "")).casefold() == voice.name.casefold():
                    return voice
            elif engine_id == "omnivoice":
                if str(row.get("id", "")).casefold() == (
                    voice.engine_voice_id or voice.voice_id
                ).casefold():
                    return voice
            elif engine_id == "piper":
                if str(row.get("id", "")).casefold() == (
                    voice.engine_voice_id or voice.voice_id
                ).casefold():
                    return voice
        return None

    def _gallery_voice_row(
        self,
        engine_id: str,
        voice: GalleryVoice,
    ) -> dict[str, object]:
        installed = self.voice_gallery_manager.is_installed(voice)
        status = (
            self.tr("built_in", "Built in")
            if voice.is_builtin
            else self.tr("installed", "Installed")
            if installed
            else self.tr("available", "Available")
        )
        row: dict[str, object] = {
            "engine": engine_id,
            "id": voice.voice_id,
            "name": voice.name,
            "language": voice.language_name or voice.language,
            "short_description": voice.short_description,
            "gender": voice.gender,
            "age_style": voice.age_style,
            "voice_style": voice.voice_style,
            "tags": ", ".join(voice.tags),
            "type": voice.voice_type,
            "status": status,
            "selected": self._is_gallery_voice_selected(engine_id, voice),
            "gallery_voice": voice,
            "preview_source": self.voice_gallery_manager.preview_source(voice),
            "installed": installed,
        }
        if voice.installed_path:
            row["path"] = Path(voice.installed_path)
        if engine_id == "qwen":
            row["speaker_id"] = voice.speaker_id or voice.engine_voice_id
            row["language_id"] = voice.language_name or voice.language
        elif engine_id == "kokoro":
            row["id"] = voice.engine_voice_id or voice.voice_id
        return row

    def _is_gallery_voice_selected(self, engine_id: str, voice: GalleryVoice) -> bool:
        if engine_id == "qwen":
            return (
                str(self.qwen_speaker_combo.currentData() or "").casefold()
                == (voice.speaker_id or voice.engine_voice_id).casefold()
                and str(self.qwen_language_combo.currentData() or "").casefold()
                == (voice.language_name or voice.language).casefold()
            )
        if engine_id == "kokoro":
            return str(self.kokoro_python_voice_combo.currentData() or "").casefold() == (
                voice.engine_voice_id or voice.voice_id
            ).casefold()
        if engine_id == "chatterbox":
            path = Path(voice.installed_path) if voice.installed_path else None
            selected = self.chatterbox_reference_picker.path()
            return bool(path and selected and path.resolve() == selected.resolve())
        if engine_id == "omnivoice":
            selected = self.omnivoice_reference_picker.path()
            return bool(
                selected
                and voice.installed_path
                and Path(voice.installed_path).resolve() == selected.resolve()
            )
        if engine_id == "piper":
            return str(self.voice_combo.currentData() or "").casefold() == (
                voice.engine_voice_id or voice.voice_id
            ).casefold()
        return False

    def _chatterbox_voice_page_rows(self) -> list[dict[str, object]]:
        selected_path = self.chatterbox_reference_picker.path()
        selected_resolved = selected_path.resolve() if selected_path else None
        rows: list[dict[str, object]] = []
        installed_by_name = {
            voice.file_name.lower(): voice
            for voice in self.chatterbox_reference_voice_manager.list_installed_voices()
        }
        for voice in self.chatterbox_reference_voice_manager.list_remote_voices():
            installed = self.chatterbox_reference_voice_manager.is_installed(voice)
            path = self.chatterbox_reference_voice_manager.path_for(voice)
            rows.append(
                {
                    "engine": "chatterbox",
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": "Reference",
                    "type": self.tr("reference_voice", "Reference voice"),
                    "status": (
                        self.tr("installed", "Installed")
                        if installed
                        else self.tr("available", "Available")
                    ),
                    "selected": (
                        installed
                        and selected_resolved is not None
                        and selected_resolved == path.resolve()
                    ),
                    "voice": voice,
                    "installed": installed,
                    "path": path,
                }
            )
        remote_names = {
            voice.file_name.lower()
            for voice in self.chatterbox_reference_voice_manager.list_remote_voices()
        }
        for voice in installed_by_name.values():
            if voice.file_name.lower() in remote_names:
                continue
            path = self.chatterbox_reference_voice_manager.path_for(voice)
            rows.append(
                {
                    "engine": "chatterbox",
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": "Reference",
                    "type": self.tr("imported_reference", "Imported reference"),
                    "status": self.tr("installed", "Installed"),
                    "selected": (
                        selected_resolved is not None
                        and selected_resolved == path.resolve()
                    ),
                    "voice": voice,
                    "installed": True,
                    "path": path,
                }
            )
        return rows

    def _voice_actions_widget(self, row: dict[str, object]) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        engine_id = str(row.get("engine", ""))

        select_button = self._small_icon_button(
            "apply",
            self.tr("select", "Select"),
            lambda _checked=False, voice_row=row: self._select_voice_page_row_data(
                voice_row
            ),
        )
        layout.addWidget(select_button)

        gallery_voice = row.get("gallery_voice")
        preview_source = str(row.get("preview_source", ""))
        if isinstance(gallery_voice, GalleryVoice) and preview_source:
            play_button = self._small_icon_button(
                "play",
                self.tr("play_sample", "Play sample"),
                lambda _checked=False, voice_row=row: self._play_voice_page_sample_data(
                    voice_row
                ),
            )
            layout.addWidget(play_button)
            if gallery_voice.is_reference_audio:
                if self.voice_gallery_manager.is_installed(gallery_voice):
                    remove_button = self._small_icon_button(
                        "delete",
                        self.tr("remove", "Remove"),
                        lambda _checked=False, voice_row=row: self._remove_gallery_voice_data(
                            voice_row
                        ),
                    )
                    remove_button.setObjectName("dangerButton")
                    layout.addWidget(remove_button)
                else:
                    install_button = self._small_icon_button(
                        "save",
                        self.tr("install", "Install"),
                        lambda _checked=False, voice_row=row: self._install_gallery_voice_data(
                            voice_row
                        ),
                    )
                    layout.addWidget(install_button)

        if engine_id in {"piper", "kokoro", "qwen", "omnivoice", "chatterbox"}:
            test_button = self._small_icon_button(
                "preview",
                self.tr("test_voice", "Test voice"),
                lambda _checked=False, voice_row=row: self._test_voice_page_row_data(
                    voice_row
                ),
            )
            test_button.setEnabled(
                (engine_id != "chatterbox" or bool(row.get("installed")))
                and not (
                    isinstance(gallery_voice, GalleryVoice)
                    and gallery_voice.is_reference_audio
                    and not bool(row.get("installed"))
                )
            )
            layout.addWidget(test_button)
        if engine_id == "chatterbox" and not isinstance(gallery_voice, GalleryVoice):
            if bool(row.get("installed")):
                play_button = self._small_icon_button(
                    "play",
                    self.tr("play_sample", "Play sample"),
                    lambda _checked=False, voice_row=row: self._play_voice_page_sample_data(
                        voice_row
                    ),
                )
                remove_button = self._small_icon_button(
                    "delete",
                    self.tr("remove", "Remove"),
                    lambda _checked=False, voice_row=row: self._remove_chatterbox_reference_voice_data(
                        voice_row
                    ),
                )
                remove_button.setObjectName("dangerButton")
                layout.addWidget(play_button)
                layout.addWidget(remove_button)
            else:
                play_button = self._small_icon_button(
                    "play",
                    self.tr("play_sample", "Play sample"),
                    lambda _checked=False, voice_row=row: self._play_voice_page_sample_data(
                        voice_row
                    ),
                )
                install_button = self._small_icon_button(
                    "save",
                    self.tr("install", "Install"),
                    lambda _checked=False, voice_row=row: self._install_chatterbox_reference_voice_data(
                        voice_row
                    ),
                )
                layout.addWidget(play_button)
                layout.addWidget(install_button)
        if engine_id == "piper":
            manage_button = self._small_icon_button(
                "settings",
                self.tr("manage_voices", "Manage voices"),
                lambda _checked=False: self._open_voice_manager(),
            )
            layout.addWidget(manage_button)
        layout.addStretch(1)
        return widget

    def _small_icon_button(
        self,
        icon_name: str,
        tooltip: str,
        callback: object,
    ) -> QPushButton:
        button = QPushButton()
        button.setIcon(ui_icon(icon_name))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip)
        button.setFixedSize(32, 30)
        button.clicked.connect(callback)  # type: ignore[arg-type]
        return button

    def _select_voice_page_row(self, row_index: int) -> None:
        if not 0 <= row_index < len(self.voice_page_rows):
            return
        row = self.voice_page_rows[row_index]
        self._select_voice_page_row_data(row)

    def _select_voice_page_row_data(
        self,
        row: dict[str, object],
        refresh: bool = True,
    ) -> None:
        engine_id = str(row.get("engine", ""))
        voice_id = row.get("id")
        gallery_voice = row.get("gallery_voice")
        if isinstance(gallery_voice, GalleryVoice):
            if (
                gallery_voice.is_reference_audio
                and not self.voice_gallery_manager.is_installed(gallery_voice)
            ):
                self._start_voice_gallery_operation("install", gallery_voice)
                return
            installed_path = (
                Path(gallery_voice.installed_path)
                if gallery_voice.installed_path
                else None
            )
            if engine_id == "chatterbox" and installed_path is not None:
                self.chatterbox_reference_picker.set_path(installed_path)
            elif engine_id == "omnivoice":
                self._apply_omnivoice_gallery_reference(gallery_voice)
        if engine_id == "piper":
            voice = row.get("voice")
            if isinstance(voice, VoiceInfo):
                self._select_combo_data(self.language_combo, voice.language)
                self._filter_voices()
                self._select_combo_data(self.voice_combo, voice.voice_id)
            elif isinstance(gallery_voice, GalleryVoice):
                self._select_combo_data(
                    self.voice_combo,
                    gallery_voice.engine_voice_id or gallery_voice.voice_id,
                )
        elif engine_id == "kokoro":
            self._select_combo_data(self.kokoro_python_voice_combo, voice_id)
        elif engine_id == "qwen":
            self._select_combo_data(
                self.qwen_language_combo,
                row.get("language_id", self.qwen_language_combo.currentData()),
            )
            self._select_combo_data(
                self.qwen_speaker_combo,
                row.get("speaker_id", voice_id),
            )
        elif engine_id == "omnivoice":
            if not isinstance(gallery_voice, GalleryVoice):
                self._select_combo_data(self.omnivoice_mode_combo, voice_id)
        elif engine_id == "chatterbox":
            if not bool(row.get("installed")):
                self._install_chatterbox_reference_voice_data(row)
                return
            path = row.get("path")
            if isinstance(path, Path):
                self.chatterbox_reference_picker.set_path(path)
        elif engine_id == "openai":
            self._select_combo_data(self.openai_voice_combo, voice_id)
        elif engine_id == "gemini":
            self._select_combo_data(self.gemini_voice_combo, voice_id)
        elif engine_id.startswith("custom:"):
            pass
        self._save_settings()
        if refresh:
            self._refresh_voices_page()
        else:
            self._refresh_generation_voice_combo()

    def _apply_omnivoice_gallery_reference(self, voice: GalleryVoice) -> bool:
        try:
            audio_path = self.voice_gallery_manager.ensure_voice_audio(voice)
        except Exception as exc:
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                f"Could not prepare OmniVoice reference voice: {exc}",
            )
            return False
        if audio_path is None or not audio_path.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                f"OmniVoice voice does not provide reference audio: {voice.name}",
            )
            return False
        self._select_combo_data(self.omnivoice_mode_combo, "clone")
        self.omnivoice_reference_picker.set_path(audio_path)
        self.omnivoice_reference_text_edit.setPlainText(voice.ref_text)
        self.log_view.append_event(
            f"OmniVoice reference voice selected: {voice.name}"
        )
        return True

    def _test_voice_page_row(self, row_index: int) -> None:
        if not 0 <= row_index < len(self.voice_page_rows):
            return
        self._test_voice_page_row_data(self.voice_page_rows[row_index])

    def _test_voice_page_row_data(self, row: dict[str, object]) -> None:
        engine_id = str(row.get("engine", ""))
        self._show_voice_preview_status(
            self.tr(
                "voice_preview_preparing",
                "Preparing {engine} voice preview. Loading the engine may take a moment...",
                engine=self._tts_engine_label(engine_id),
            ),
            busy=True,
        )
        self._select_voice_page_row_data(row)
        row_index = self._voice_page_row_index(row)
        if engine_id == "piper":
            if row_index is not None:
                self._test_piper_voice_page_row(row_index)
        elif engine_id == "kokoro":
            self._test_kokoro_python_voice()
        elif engine_id == "qwen":
            self._test_qwen_voice()
        elif engine_id == "omnivoice":
            self._test_omnivoice_voice()
        elif engine_id == "chatterbox":
            self._test_chatterbox_voice()

    def _voice_page_row_index(self, row: dict[str, object]) -> int | None:
        row_id = row.get("id")
        row_engine = row.get("engine")
        for index, candidate in enumerate(self.voice_page_rows):
            if candidate.get("engine") == row_engine and candidate.get("id") == row_id:
                return index
        return None

    def _test_piper_voice_page_row(self, row_index: int) -> None:
        row = self.voice_page_rows[row_index]
        voice = row.get("voice")
        if not isinstance(voice, VoiceInfo):
            return
        output_path = Path(tempfile.gettempdir()) / "localtext2voice_piper_preview.wav"
        config = voice.as_config(self.speed_spin.value())
        config["engine"] = "piper"
        config["piper_path"] = str(self.piper_path_edit.text().strip())
        try:
            PiperTTSEngine(
                resolve_app_path(
                    self.piper_path_edit.text().strip()
                    or "engines/piper/piper.exe"
                )
            ).synthesize_to_wav(
                self._preview_text_for_voice_language(voice.voice_id, voice.language),
                output_path,
                config,
            )
        except TTSEngineError as exc:
            self._hide_voice_preview_status()
            self._show_error(self.tr("generation_failed", "Generation failed"), str(exc))
            return
        self._show_voice_preview_status(
            self.tr("voice_preview_playing", "Playing voice preview."),
            busy=False,
        )
        self.voices_player.setSource(QUrl.fromLocalFile(str(output_path)))
        self.voices_player.play()

    @staticmethod
    def _preview_text_for_voice_language(voice_id: str, language: str = "") -> str:
        normalized = f"{voice_id} {language}".strip().lower().replace("-", "_")
        candidates = (
            ("es_", "La luna esta preciosa esta noche."),
            ("it_", "La luna e bellissima stasera."),
            ("fr_", "La lune est magnifique ce soir."),
            ("de_", "Der Mond ist heute Nacht wunderschoen."),
            ("pt_", "A lua esta linda esta noite."),
            ("en_", "The moon looks beautiful tonight."),
            ("zh_", "今晚的月亮很美。"),
            ("ja_", "今夜の月はとてもきれいです。"),
        )
        for prefix, text in candidates:
            if normalized.startswith(prefix) or f" {prefix}" in normalized:
                return text
        if normalized.startswith("es") or " es" in normalized:
            return "La luna esta preciosa esta noche."
        if normalized.startswith("it") or " it" in normalized:
            return "La luna e bellissima stasera."
        return "The moon looks beautiful tonight."

    def _show_voice_preview_status(self, message: str, busy: bool) -> None:
        if not hasattr(self, "voice_preview_frame"):
            return
        self.voice_preview_status_label.setText(message)
        self.voice_preview_bar.setRange(0, 0 if busy else 100)
        if not busy:
            self.voice_preview_bar.setValue(100)
        self.voice_preview_frame.setVisible(True)

    def _hide_voice_preview_status(self) -> None:
        if hasattr(self, "voice_preview_frame"):
            self.voice_preview_frame.setVisible(False)

    def _on_voice_preview_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_voice_preview_status(
                self.tr("voice_preview_playing", "Playing voice preview."),
                busy=False,
            )
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            QTimer.singleShot(900, self._hide_voice_preview_status)

    def _play_voice_page_sample(self, row_index: int) -> None:
        if not 0 <= row_index < len(self.voice_page_rows):
            return
        row = self.voice_page_rows[row_index]
        self._play_voice_page_sample_data(row)

    def _play_voice_page_sample_data(self, row: dict[str, object]) -> None:
        gallery_voice = row.get("gallery_voice")
        if isinstance(gallery_voice, GalleryVoice):
            source = self.voice_gallery_manager.preview_source(gallery_voice)
            if source:
                if source.startswith("http://") or source.startswith("https://"):
                    self.voices_player.setSource(QUrl(source))
                else:
                    self.voices_player.setSource(QUrl.fromLocalFile(str(Path(source))))
                self.voices_player.play()
                return
        voice = row.get("voice")
        path = row.get("path")
        if isinstance(path, Path) and path.is_file():
            self.voices_player.setSource(QUrl.fromLocalFile(str(path)))
            self.voices_player.play()
            return
        if isinstance(voice, ChatterboxReferenceVoice) and voice.source_url:
            self.voices_player.setSource(QUrl(voice.source_url))
            self.voices_player.play()

    def _sync_voice_gallery(self) -> None:
        self._start_voice_gallery_operation("sync")

    def _install_gallery_voice_data(self, row: dict[str, object]) -> None:
        gallery_voice = row.get("gallery_voice")
        if isinstance(gallery_voice, GalleryVoice):
            self._start_voice_gallery_operation("install", gallery_voice)

    def _remove_gallery_voice_data(self, row: dict[str, object]) -> None:
        gallery_voice = row.get("gallery_voice")
        if not isinstance(gallery_voice, GalleryVoice):
            return
        choice = QMessageBox.question(
            self,
            self.tr("remove_voice", "Remove voice"),
            self.tr(
                "remove_voice_confirm",
                "Remove {voice} from this computer?",
                voice=gallery_voice.name,
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._start_voice_gallery_operation("remove", gallery_voice)

    def _start_voice_gallery_operation(
        self,
        operation: str,
        voice: GalleryVoice | None = None,
    ) -> None:
        if self.voice_gallery_thread is not None:
            return
        self.voices_progress_bar.setVisible(True)
        self.voices_progress_bar.setRange(0, 0 if operation == "sync" else 100)
        self.voices_progress_bar.setValue(0)
        self.voices_status_label.setText(
            self.tr("voice_gallery_working", "Updating voice gallery...")
        )
        self.voices_sync_button.setEnabled(False)
        self.voices_manage_button.setEnabled(False)
        thread = QThread(self)
        gallery_settings = self.settings.get("voice_gallery", {})
        manager = VoiceGalleryManager(
            catalog_url=str(
                gallery_settings.get("catalog_url") or DEFAULT_GALLERY_CATALOG_URL
            ),
            local_catalog_path=str(gallery_settings.get("local_catalog_path", "")),
        )
        worker = VoiceGalleryWorker(manager, operation, voice)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_voice_gallery_progress)
        worker.finished.connect(self._on_voice_gallery_finished)
        worker.failed.connect(self._on_voice_gallery_failed)
        worker.cancelled.connect(self._on_voice_gallery_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_voice_gallery_worker)
        self.voice_gallery_worker = worker
        self.voice_gallery_thread = thread
        thread.start()

    def _on_voice_gallery_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if total:
            self.voices_progress_bar.setRange(0, 100)
            self.voices_progress_bar.setValue(max(0, min(100, int(current / total * 100))))
        self.voices_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_voice_gallery_finished(self, message: str) -> None:
        self.voices_progress_bar.setVisible(False)
        gallery_settings = self.settings.setdefault("voice_gallery", {})
        if isinstance(gallery_settings, dict):
            gallery_settings["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.settings_manager.save(self.settings)
        self.voice_gallery_manager = VoiceGalleryManager(
            catalog_url=str(
                gallery_settings.get("catalog_url") or DEFAULT_GALLERY_CATALOG_URL
            ),
            local_catalog_path=str(gallery_settings.get("local_catalog_path", "")),
        )
        self.log_view.append_event(message)
        self._refresh_voices_page()

    def _on_voice_gallery_failed(self, message: str) -> None:
        self.voices_progress_bar.setVisible(False)
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_voice_gallery_cancelled(self) -> None:
        self.voices_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("voice_gallery_cancelled", "Voice gallery operation cancelled.")
        )

    def _clear_voice_gallery_worker(self) -> None:
        self.voice_gallery_worker = None
        self.voice_gallery_thread = None
        self.voices_progress_bar.setVisible(False)
        if hasattr(self, "voices_sync_button"):
            self.voices_sync_button.setEnabled(True)
        self._refresh_voices_page()

    def _voices_primary_manage_action(self) -> None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        if engine_id == "piper":
            self._open_voice_manager()
            self._refresh_voices_page()
            return
        if engine_id == "chatterbox":
            self._import_gallery_reference_voice("chatterbox")
            return
        if engine_id == "omnivoice":
            self._import_gallery_reference_voice("omnivoice")

    def _show_chatterbox_voice_manage_menu(self) -> None:
        self._import_chatterbox_reference_voice()

    def _import_chatterbox_reference_voice(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("import_reference_voice", "Import reference voice"),
            "",
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        if not selected:
            return
        try:
            destination = self.chatterbox_reference_voice_manager.import_voice(
                Path(selected)
            )
        except Exception as exc:
            self._show_error(self.tr("import_failed", "Import failed"), str(exc))
            return
        self.log_view.append_event(f"Imported Chatterbox reference voice: {destination.name}")
        self._refresh_voices_page()

    def _import_gallery_reference_voice(self, engine_id: str) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("import_reference_voice", "Import reference voice"),
            "",
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        if not selected:
            return
        source = Path(selected)
        dialog = ReferenceVoiceImportDialog(source, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            voice = self.voice_gallery_manager.import_reference_voice(
                engine_id,
                source,
                name=str(values["name"]),
                language=str(values["language"]),
                language_name=str(values["language_name"]),
                ref_text=str(values["ref_text"]),
                short_description=str(values["short_description"]),
                gender=str(values["gender"]),
                age_style=str(values["age_style"]),
                voice_style=str(values["voice_style"]),
                tags=list(values["tags"]),
                ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            )
        except Exception as exc:
            self._show_error(self.tr("import_failed", "Import failed"), str(exc))
            return
        self.log_view.append_event(f"Imported {engine_id} reference voice: {voice.name}")
        self._refresh_voices_page()

    def _open_omnivoice_design_dialog(self) -> None:
        if not self.omnivoice_manager.is_installed():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "omnivoice_runtime_missing",
                    "OmniVoice is not installed yet. Open Settings > TTS Engines "
                    "and click Install.",
                ),
            )
            return
        dialog = OmniVoiceDesignDialog(self, self.tr)
        dialog.generate_button.clicked.connect(
            lambda _checked=False: self._start_omnivoice_design_preview(dialog)
        )
        dialog.play_button.clicked.connect(
            lambda _checked=False: self._play_omnivoice_design_preview(dialog)
        )
        dialog.save_button.clicked.connect(
            lambda _checked=False: self._save_omnivoice_design_voice(dialog)
        )
        self.omnivoice_design_dialog = dialog
        dialog.exec()
        if self.omnivoice_design_dialog is dialog:
            self.omnivoice_design_dialog = None

    def _start_omnivoice_design_preview(
        self,
        dialog: OmniVoiceDesignDialog,
    ) -> None:
        if self.omnivoice_design_thread is not None:
            return
        sample_text = dialog.sample_text_edit.toPlainText().strip()
        if not sample_text:
            self._show_error(
                self.tr("missing_text", "Missing text"),
                self.tr(
                    "omnivoice_design_missing_sample",
                    "Add sample text before generating a preview.",
                ),
            )
            return
        design_instruct = dialog.instruction()
        voice_config = {
            "engine": "omnivoice",
            "model": self.omnivoice_model_combo.currentData() or "omnivoice",
            "mode": "design",
            "language": dialog.language_combo.currentData() or "auto",
            "device": self.omnivoice_device_combo.currentData() or "auto",
            "dtype": self.omnivoice_dtype_combo.currentData() or "auto",
            "instruct": design_instruct,
            "num_step": self.omnivoice_num_step_spin.value(),
            "speed": self.omnivoice_engine_speed_spin.value(),
            "duration": self.omnivoice_duration_spin.value(),
        }
        thread = QThread(self)
        worker = OmniVoicePreviewWorker(
            OmniVoiceManager(),
            voice_config,
            sample_text,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda path: self._on_omnivoice_design_preview_ready(dialog, path))
        worker.failed.connect(lambda message: self._on_omnivoice_design_preview_failed(dialog, message))
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_omnivoice_design_worker)
        self.omnivoice_design_worker = worker
        self.omnivoice_design_thread = thread
        dialog.generate_button.setEnabled(False)
        dialog.save_button.setEnabled(False)
        dialog.status_label.setText(
            self.tr(
                "omnivoice_design_generating",
                "Generating preview with OmniVoice...",
            )
        )
        self.log_view.append_event(
            self.tr(
                "omnivoice_design_log_generating",
                "Generating OmniVoice designed voice preview...",
            )
        )
        self.log_view.append_event(f"OmniVoice design instruct: {design_instruct}")
        thread.start()

    def _on_omnivoice_design_preview_ready(
        self,
        dialog: OmniVoiceDesignDialog,
        path: str,
    ) -> None:
        preview_path = Path(path)
        self.omnivoice_design_preview_path = preview_path
        dialog.preview_path = preview_path
        dialog.status_label.setText(
            self.tr(
                "omnivoice_design_preview_ready",
                "Preview ready. Listen, then save it as a reusable voice.",
            )
        )
        dialog.play_button.setEnabled(True)
        dialog.save_button.setEnabled(True)
        self._play_omnivoice_design_preview(dialog)
        self.log_view.append_event(
            self.tr(
                "omnivoice_design_log_preview_ready",
                "OmniVoice designed voice preview ready.",
            )
        )

    def _on_omnivoice_design_preview_failed(
        self,
        dialog: OmniVoiceDesignDialog,
        message: str,
    ) -> None:
        first_line = message.splitlines()[0] if message else "Unknown error"
        dialog.status_label.setText(
            self.tr(
                "omnivoice_design_preview_failed",
                "Preview failed: {message}",
                message=first_line,
            )
        )
        self.log_view.append_event(message)
        visible_message = message if len(message) <= 1200 else message[:1200] + "\n..."
        self._show_error(self.tr("generation_failed", "Generation failed"), visible_message)

    def _clear_omnivoice_design_worker(self) -> None:
        self.omnivoice_design_worker = None
        self.omnivoice_design_thread = None
        if self.omnivoice_design_dialog is not None:
            self.omnivoice_design_dialog.generate_button.setEnabled(True)

    def _play_omnivoice_design_preview(self, dialog: OmniVoiceDesignDialog) -> None:
        if dialog.preview_path is None or not dialog.preview_path.is_file():
            return
        self.voices_player.setSource(QUrl.fromLocalFile(str(dialog.preview_path)))
        self.voices_player.play()

    def _save_omnivoice_design_voice(self, dialog: OmniVoiceDesignDialog) -> None:
        if dialog.preview_path is None or not dialog.preview_path.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "omnivoice_design_generate_before_save",
                    "Generate a preview before saving the designed voice.",
                ),
            )
            return
        metadata = dialog.voice_metadata()
        try:
            voice = self.voice_gallery_manager.import_reference_voice(
                "omnivoice",
                dialog.preview_path,
                name=str(metadata["name"]),
                language=str(metadata["language"]),
                language_name=str(metadata["language_name"]),
                ref_text=str(metadata["ref_text"]),
                short_description=str(metadata["short_description"]),
                gender=str(metadata["gender"]),
                age_style=str(metadata["age_style"]),
                voice_style=str(metadata["voice_style"]),
                tags=list(metadata["tags"]),
                ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
                min_duration_seconds=1.0,
                max_duration_seconds=60.0,
            )
        except Exception as exc:
            self._show_error(self.tr("import_failed", "Import failed"), str(exc))
            return
        dialog.status_label.setText(
            self.tr("omnivoice_design_saved_voice", "Saved voice: {name}", name=voice.name)
        )
        self.log_view.append_event(
            self.tr(
                "omnivoice_design_log_saved_voice",
                "Saved OmniVoice designed voice: {name}",
                name=voice.name,
            )
        )
        self._refresh_voices_page()

    def _install_chatterbox_reference_voice(self, row_index: int) -> None:
        if not 0 <= row_index < len(self.voice_page_rows):
            return
        self._install_chatterbox_reference_voice_data(self.voice_page_rows[row_index])

    def _install_chatterbox_reference_voice_data(
        self,
        row: dict[str, object],
    ) -> None:
        voice = row.get("voice")
        if isinstance(voice, ChatterboxReferenceVoice):
            self._start_chatterbox_voice_operation("install", voice)

    def _remove_chatterbox_reference_voice(self, row_index: int) -> None:
        if not 0 <= row_index < len(self.voice_page_rows):
            return
        self._remove_chatterbox_reference_voice_data(self.voice_page_rows[row_index])

    def _remove_chatterbox_reference_voice_data(
        self,
        row: dict[str, object],
    ) -> None:
        voice = row.get("voice")
        if not isinstance(voice, ChatterboxReferenceVoice):
            return
        choice = QMessageBox.question(
            self,
            self.tr("remove_voice", "Remove voice"),
            self.tr(
                "remove_voice_confirm",
                "Remove {voice} from this computer?",
                voice=voice.display_name,
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._start_chatterbox_voice_operation("remove", voice)

    def _start_chatterbox_voice_operation(
        self,
        operation: str,
        voice: ChatterboxReferenceVoice | None = None,
    ) -> None:
        if self.chatterbox_voice_thread is not None:
            return
        self.voices_progress_bar.setVisible(True)
        self.voices_progress_bar.setRange(0, 0 if operation == "install_pack" else 100)
        self.voices_progress_bar.setValue(0)
        thread = QThread(self)
        worker = ChatterboxVoiceWorker(
            ChatterboxReferenceVoiceManager(),
            operation,
            voice,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_chatterbox_voice_progress)
        worker.finished.connect(self._on_chatterbox_voice_finished)
        worker.failed.connect(self._on_chatterbox_voice_failed)
        worker.cancelled.connect(self._on_chatterbox_voice_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_chatterbox_voice_worker)
        self.chatterbox_voice_worker = worker
        self.chatterbox_voice_thread = thread
        thread.start()

    def _on_chatterbox_voice_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if total:
            self.voices_progress_bar.setRange(0, 100)
            self.voices_progress_bar.setValue(max(0, min(100, int(current / total * 100))))
        self.voices_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_chatterbox_voice_finished(self, message: str) -> None:
        self.voices_progress_bar.setVisible(False)
        self.chatterbox_reference_voice_manager = ChatterboxReferenceVoiceManager()
        self.log_view.append_event(f"Chatterbox reference voices updated: {message}")
        self._refresh_voices_page()

    def _on_chatterbox_voice_failed(self, message: str) -> None:
        self.voices_progress_bar.setVisible(False)
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_chatterbox_voice_cancelled(self) -> None:
        self.voices_progress_bar.setVisible(False)
        self.log_view.append_event(
            self.tr("voice_download_cancelled", "Voice download cancelled")
        )

    def _clear_chatterbox_voice_worker(self) -> None:
        self.chatterbox_voice_worker = None
        self.chatterbox_voice_thread = None
        self.voices_progress_bar.setVisible(False)
        self._refresh_voices_page()

    def _restore_settings(self) -> None:
        self._ensure_default_music_selection()
        selected_engine = self.settings.get("tts_engine", "piper")
        if selected_engine == "kokoro_python":
            selected_engine = "kokoro"
        self._populate_tts_engine_combo(selected_engine)
        self._select_combo_data(
            self.tts_engine_combo,
            selected_engine,
        )
        self.piper_path_edit.setText(
            str(self.settings.get("piper_path", "engines/piper/piper.exe"))
        )
        api_tts = self._api_tts_settings()
        openai = api_tts.get("openai", {})
        self.openai_api_key_edit.setText(str(openai.get("api_key", "")))
        self._select_combo_data(
            self.openai_model_combo,
            openai.get("model", "gpt-4o-mini-tts"),
        )
        self._select_combo_data(self.openai_voice_combo, openai.get("voice", "marin"))
        self.openai_instructions_edit.setText(
            str(openai.get("instructions", ""))
        )

        kokoro = self.settings.get("kokoro", {})
        if not isinstance(kokoro, dict):
            kokoro = {}
        self._select_combo_data(
            self.kokoro_python_voice_combo,
            kokoro.get("voice", "af_heart"),
        )

        chatterbox = self.settings.get("chatterbox", {})
        if not isinstance(chatterbox, dict):
            chatterbox = {}
        self._select_combo_data(
            self.chatterbox_model_combo,
            chatterbox.get("model", "multilingual_v3"),
        )
        self._select_combo_data(
            self.chatterbox_language_combo,
            chatterbox.get("language", "en"),
        )
        self._select_combo_data(
            self.chatterbox_device_combo,
            chatterbox.get("device", "auto"),
        )
        self.chatterbox_reference_picker.set_path(
            str(chatterbox.get("reference_audio_path", ""))
        )
        self.chatterbox_exaggeration_spin.setValue(
            float(chatterbox.get("exaggeration", 0.5))
        )
        self.chatterbox_cfg_spin.setValue(
            float(chatterbox.get("cfg_weight", 0.5))
        )

        qwen = self.settings.get("qwen", {})
        if not isinstance(qwen, dict):
            qwen = {}
        self._select_combo_data(
            self.qwen_model_combo,
            qwen.get("model", "custom_voice_0_6b"),
        )
        self._select_combo_data(
            self.qwen_language_combo,
            qwen.get("language", "Spanish"),
        )
        self._select_combo_data(
            self.qwen_speaker_combo,
            qwen.get("speaker", "Serena"),
        )
        self._select_combo_data(
            self.qwen_device_combo,
            qwen.get("device", "auto"),
        )
        self._select_combo_data(
            self.qwen_dtype_combo,
            qwen.get("dtype", "auto"),
        )
        self.qwen_instruct_edit.setText(str(qwen.get("instruct", "")))

        omnivoice = self.settings.get("omnivoice", {})
        if not isinstance(omnivoice, dict):
            omnivoice = {}
        self._select_combo_data(
            self.omnivoice_model_combo,
            omnivoice.get("model", "omnivoice"),
        )
        self._select_combo_data(
            self.omnivoice_mode_combo,
            "clone",
        )
        self._select_combo_data(
            self.omnivoice_language_combo,
            omnivoice.get("language", "auto"),
        )
        self._select_combo_data(
            self.omnivoice_device_combo,
            omnivoice.get("device", "auto"),
        )
        self._select_combo_data(
            self.omnivoice_dtype_combo,
            omnivoice.get("dtype", "auto"),
        )
        self.omnivoice_instruct_edit.setText(
            str(
                omnivoice.get(
                    "instruct",
                    "female, young adult, moderate pitch",
                )
            )
        )
        self.omnivoice_reference_picker.set_path(
            str(omnivoice.get("reference_audio_path", ""))
        )
        self.omnivoice_reference_text_edit.setPlainText(
            str(omnivoice.get("reference_text", ""))
        )
        self._ensure_default_omnivoice_reference(allow_sync=False)
        self.omnivoice_num_step_spin.setValue(
            int(omnivoice.get("num_step", 32))
        )
        self.omnivoice_engine_speed_spin.setValue(
            float(omnivoice.get("speed", 1.0))
        )
        self.omnivoice_duration_spin.setValue(
            float(omnivoice.get("duration", 0.0))
        )

        review = self.settings.get("review", {})
        if not isinstance(review, dict):
            review = {}
        self.review_enabled_checkbox.setChecked(bool(review.get("enabled", False)))
        self.review_auto_checkbox.setChecked(
            bool(review.get("auto_verify_after_generation", False))
        )
        self._select_combo_data(self.review_model_combo, review.get("model", "small"))
        self._select_combo_data(self.review_device_combo, review.get("device", "cpu"))
        self._select_combo_data(
            self.review_compute_combo,
            review.get("compute_type", "int8"),
        )
        self._select_combo_data(
            self.review_language_combo,
            review.get("language", "auto"),
        )
        self.review_beam_spin.setValue(int(review.get("beam_size", 1)))
        self.review_threshold_spin.setValue(
            float(review.get("approve_threshold", 92.0))
        )
        self.review_max_retries_spin.setValue(int(review.get("max_retries", 0)))
        self._refresh_whisper_status()
        self._restore_local_server_settings()

        elevenlabs = api_tts.get("elevenlabs", {})
        self.elevenlabs_api_key_edit.setText(str(elevenlabs.get("api_key", "")))
        self.elevenlabs_voice_id_edit.setText(
            str(elevenlabs.get("voice_id", ""))
        )
        self._select_combo_data(
            self.elevenlabs_model_combo,
            elevenlabs.get("model_id", "eleven_flash_v2_5"),
        )
        self._select_combo_data(
            self.elevenlabs_output_combo,
            elevenlabs.get("output_format", "pcm_24000"),
        )
        self.elevenlabs_stability_spin.setValue(
            float(elevenlabs.get("stability", 0.5))
        )
        self.elevenlabs_similarity_spin.setValue(
            float(elevenlabs.get("similarity_boost", 0.75))
        )
        self.elevenlabs_style_spin.setValue(float(elevenlabs.get("style", 0.0)))
        self.elevenlabs_speaker_boost_checkbox.setChecked(
            bool(elevenlabs.get("use_speaker_boost", True))
        )

        gemini = api_tts.get("gemini", {})
        self.gemini_api_key_edit.setText(str(gemini.get("api_key", "")))
        self._select_combo_data(
            self.gemini_model_combo,
            gemini.get("model", "gemini-3.1-flash-tts-preview"),
        )
        self._select_combo_data(
            self.gemini_voice_combo,
            gemini.get("voice", "Kore"),
        )
        self.gemini_prompt_edit.setText(str(gemini.get("prompt", "")))

        azure = api_tts.get("azure", {})
        self.azure_api_key_edit.setText(str(azure.get("api_key", "")))
        self.azure_region_edit.setText(str(azure.get("region", "")))
        self.azure_voice_edit.setText(
            str(azure.get("voice", "en-US-JennyNeural"))
        )
        self._select_combo_data(
            self.azure_output_combo,
            azure.get("output_format", "riff-24khz-16bit-mono-pcm"),
        )
        self.azure_style_edit.setText(str(azure.get("style", "")))
        self._on_tts_engine_changed()

        self.speed_spin.setValue(float(self.settings.get("speed", 1.0)))
        paragraph_min = max(
            0,
            int(self.settings.get("paragraph_pause_min_ms", 450)),
        )
        paragraph_max = max(
            paragraph_min,
            int(self.settings.get("paragraph_pause_max_ms", 900)),
        )
        self.paragraph_pause_min_spin.setValue(paragraph_min / 1000)
        self.paragraph_pause_max_spin.setValue(paragraph_max / 1000)
        self.adaptive_pause_checkbox.setChecked(
            bool(self.settings.get("adaptive_paragraph_pause", True))
        )
        self.paragraph_length_reference_spin.setValue(
            int(self.settings.get("paragraph_length_reference_chars", 600))
        )
        self.paragraph_length_extra_spin.setValue(
            int(self.settings.get("paragraph_length_extra_ms", 650)) / 1000
        )
        self.periodic_pause_every_spin.setValue(
            int(self.settings.get("periodic_pause_every_paragraphs", 5))
        )
        periodic_min = max(
            0,
            int(self.settings.get("periodic_pause_min_ms", 350)),
        )
        periodic_max = max(
            periodic_min,
            int(self.settings.get("periodic_pause_max_ms", 750)),
        )
        self.periodic_pause_min_spin.setValue(periodic_min / 1000)
        self.periodic_pause_max_spin.setValue(periodic_max / 1000)
        self._select_combo_data(
            self.split_combo,
            self.settings.get("split_mode", "safe_chunks"),
        )
        try:
            default_chunk_size = int(self.settings.get("chunk_size", 2500))
        except (TypeError, ValueError):
            default_chunk_size = 2500
        self.chunk_size_spin.setValue(default_chunk_size)
        engine_chunk_sizes = self.settings.get("engine_chunk_sizes", {})
        if not isinstance(engine_chunk_sizes, dict):
            engine_chunk_sizes = {}

        def chunk_override(engine: str) -> int:
            try:
                return int(engine_chunk_sizes.get(engine, 0) or 0)
            except (TypeError, ValueError):
                return 0

        self.piper_chunk_size_spin.setValue(chunk_override("piper"))
        self.kokoro_chunk_size_spin.setValue(chunk_override("kokoro"))
        self.chatterbox_chunk_size_spin.setValue(chunk_override("chatterbox"))
        self.qwen_chunk_size_spin.setValue(chunk_override("qwen"))
        self.omnivoice_chunk_size_spin.setValue(chunk_override("omnivoice"))
        self._select_combo_data(
            self.export_combo,
            self.settings.get("export_mode", "single"),
        )
        self.normalize_checkbox.setChecked(
            bool(self.settings.get("normalize_audio", False))
        )
        self.auto_delete_segment_wavs_checkbox.setChecked(
            bool(self.settings.get("auto_delete_segment_wavs_after_mix", False))
        )
        self._refresh_wav_cache_stats()
        self.editor_highlighting_checkbox.setChecked(
            bool(self.settings.get("editor_syntax_highlighting", True))
        )
        self._set_editor_highlighting_enabled(
            self.editor_highlighting_checkbox.isChecked()
        )
        self._set_markup_toolbar_visible(
            bool(self.settings.get("show_markup_toolbar", True))
        )
        self.markup_corrector_checkbox.setChecked(
            bool(self.settings.get("markup_corrector_enabled", True))
        )
        self._set_markup_corrector_enabled(
            self.markup_corrector_checkbox.isChecked()
        )
        self.podcast_enabled_checkbox.setChecked(
            bool(self.settings.get("podcast_enabled", False))
        )
        self.background_enabled_checkbox.setChecked(
            bool(self.settings.get("background_enabled", False))
        )
        self.background_picker.set_path(self.settings.get("background_path", ""))
        self.background_loop_checkbox.setChecked(
            bool(self.settings.get("background_loop", True))
        )
        self.voice_volume_db_spin.setValue(
            float(self.settings.get("voice_volume_db", 0.0))
        )
        self.background_volume_spin.setValue(
            float(
                self.settings.get(
                    "music_volume_db",
                    self._percent_to_db(
                        int(self.settings.get("background_volume_percent", 45))
                    ),
                )
            )
        )
        self.voice_start_offset_spin.setValue(
            int(self.settings.get("voice_start_offset_ms", 2000))
        )
        self.music_tail_spin.setValue(
            int(self.settings.get("music_tail_ms", 2000))
        )
        self.fade_in_spin.setValue(
            float(self.settings.get("music_fade_in_seconds", 1.0))
        )
        self.fade_out_spin.setValue(
            float(self.settings.get("music_fade_out_seconds", 1.0))
        )
        self.podcast_gap_spin.setValue(
            int(self.settings.get("podcast_gap_ms", 500)) / 1000
        )
        self.podcast_normalize_checkbox.setChecked(
            bool(self.settings.get("podcast_normalize", True))
        )
        self.podcast_ducking_checkbox.setChecked(
            bool(self.settings.get("podcast_ducking", True))
        )
        self._select_combo_data(
            self.ducking_strength_combo,
            self.settings.get("ducking_strength", "low"),
        )
        self.open_folder_checkbox.setChecked(
            bool(self.settings.get("open_output_on_finish", True))
        )
        self.ui_language_combo.blockSignals(True)
        self._select_combo_data(
            self.ui_language_combo,
            self.settings.get("ui_language", "en"),
        )
        self.ui_language_combo.blockSignals(False)
        saved_language = str(self.settings.get("language", ""))
        self._select_combo_data(self.language_combo, saved_language)
        self._filter_voices()
        self._select_combo_data(
            self.voice_combo,
            self.settings.get("voice_id", ""),
        )
        self.log_view.append_event(
            self.tr(
                "voices_found",
                "Discovered {count} valid Piper voice(s).",
                count=len(self.voices),
            )
        )

    def _api_tts_settings(self) -> dict[str, dict[str, object]]:
        value = self.settings.get("api_tts", {})
        if not isinstance(value, dict):
            return {}
        return {
            str(key): dict(item) if isinstance(item, dict) else {}
            for key, item in value.items()
        }

    def _change_ui_language(self) -> None:
        language = str(self.ui_language_combo.currentData() or "en")
        if language == self.translator.language:
            return
        text = self.text_editor.toPlainText()
        page_index = self.page_stack.currentIndex()
        self._save_settings()
        self.settings["ui_language"] = language
        self.settings_manager.save(self.settings)
        self.translator.set_language(language)

        old_central = self.takeCentralWidget()
        self.setWindowTitle(self.tr("app_title", "LocalText2Voice"))
        self._build_ui()
        self._apply_style()
        self._load_voices()
        self._restore_settings()
        self.text_editor.setPlainText(text)
        if page_index == 1:
            self._show_settings_page()
        elif page_index == 2:
            self._show_music_page()
        elif page_index == 3:
            self._show_review_page()
        elif page_index == 4:
            self._show_mix_preview_page()
        elif page_index == 5:
            self._show_voices_page()
        else:
            self._show_generation()
        self._set_running(False)
        if old_central is not None:
            old_central.deleteLater()

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _import_document(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("import_file", "Import file"),
            "",
            self.tr(
                "supported_documents",
                "Supported documents (*.txt *.md *.docx);;"
                "Text files (*.txt);;Markdown files (*.md);;"
                "Word documents (*.docx)",
            ),
        )
        if not path_text:
            return
        try:
            text = ProjectManager.import_document(Path(path_text))
            self.text_editor.setPlainText(text)
            self.log_view.append_event(f"Imported: {path_text}")
        except DocumentImportError as exc:
            self._show_error(self.tr("import_failed", "Import failed"), str(exc))

    def _stored_project_id(self) -> int | None:
        try:
            value = self.settings.get("current_project_id")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _mark_project_dirty(self) -> None:
        if not getattr(self, "_loading_project", False):
            self.project_dirty = True

    def _current_audiobook(self):
        audiobook = self.audiobook_store.get_audiobook(self.current_audiobook_id)
        return audiobook or self.audiobook_store.latest_audiobook()

    def _refresh_wav_cache_stats(self) -> None:
        if not hasattr(self, "wav_cache_stats_label"):
            return
        current = self._current_audiobook()
        current_count, current_bytes = (
            self.audiobook_store.segment_wav_cache_stats(current.id)
            if current is not None
            else (0, 0)
        )
        all_count, all_bytes = self.audiobook_store.segment_wav_cache_stats()
        temp_count, temp_bytes = self._temp_audio_cache_stats()
        self.wav_cache_stats_label.setText(
            self.tr(
                "wav_cache_stats",
                "Current project: {current_count} WAV(s), {current_size}. "
                "All projects: {all_count} WAV(s), {all_size}. "
                "Abandoned temp: {temp_count} file(s), {temp_size}.",
                current_count=current_count,
                current_size=self._format_bytes(current_bytes),
                all_count=all_count,
                all_size=self._format_bytes(all_bytes),
                temp_count=temp_count,
                temp_size=self._format_bytes(temp_bytes),
            )
        )
        cleanup_allowed = self._can_cleanup_wav_cache()
        self.cleanup_current_wavs_button.setEnabled(
            cleanup_allowed and current_count > 0
        )
        self.cleanup_all_wavs_button.setEnabled(cleanup_allowed and all_count > 0)
        self.cleanup_temp_audio_button.setEnabled(
            cleanup_allowed and temp_count > 0
        )

    def _can_cleanup_wav_cache(self) -> bool:
        return (
            self.worker is None
            and self.verification_thread is None
            and self.segment_regeneration_thread is None
            and self.audiobook_rebuild_thread is None
        )

    def _cleanup_current_project_wavs(self) -> None:
        if not self._can_cleanup_wav_cache():
            QMessageBox.information(
                self,
                self.tr("delete_wav_cache", "Delete WAV cache"),
                self.tr(
                    "cleanup_wavs_busy",
                    "Wait until generation or review work finishes before deleting WAV files.",
                ),
            )
            return
        audiobook = self._current_audiobook()
        if audiobook is None:
            QMessageBox.information(
                self,
                self.tr("delete_wav_cache", "Delete WAV cache"),
                self.tr("no_current_project", "No current project found."),
            )
            return
        choice = QMessageBox.question(
            self,
            self.tr("delete_wav_cache", "Delete WAV cache"),
            self.tr(
                "cleanup_current_wavs_confirm",
                "Delete segment WAV files for the current project? "
                "Final MP3 files will not be deleted.",
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._cleanup_wav_cache(audiobook.id)

    def _cleanup_all_project_wavs(self) -> None:
        if not self._can_cleanup_wav_cache():
            QMessageBox.information(
                self,
                self.tr("delete_wav_cache", "Delete WAV cache"),
                self.tr(
                    "cleanup_wavs_busy",
                    "Wait until generation or review work finishes before deleting WAV files.",
                ),
            )
            return
        choice = QMessageBox.question(
            self,
            self.tr("delete_wav_cache", "Delete WAV cache"),
            self.tr(
                "cleanup_all_wavs_confirm",
                "Delete segment WAV files for all projects? "
                "Final MP3 files will not be deleted.",
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._cleanup_wav_cache(None)

    def _cleanup_abandoned_temp_audio(self) -> None:
        if not self._can_cleanup_wav_cache():
            QMessageBox.information(
                self,
                self.tr("delete_wav_cache", "Delete WAV cache"),
                self.tr(
                    "cleanup_wavs_busy",
                    "Wait until generation or review work finishes before deleting WAV files.",
                ),
            )
            return
        dirs = self._temp_audio_cache_dirs()
        if not dirs:
            QMessageBox.information(
                self,
                self.tr("delete_wav_cache", "Delete WAV cache"),
                self.tr("cleanup_wavs_none", "No segment WAV files found."),
            )
            self._refresh_wav_cache_stats()
            return
        choice = QMessageBox.question(
            self,
            self.tr("delete_wav_cache", "Delete WAV cache"),
            self.tr(
                "cleanup_temp_audio_confirm",
                "Delete abandoned LocalText2Voice temporary audio folders? "
                "Final MP3 files and project files will not be deleted.",
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        count, size = self._delete_temp_audio_cache_dirs(dirs)
        self._refresh_wav_cache_stats()
        message = self.tr(
            "cleanup_temp_audio_complete",
            "Deleted {count} temporary file(s), freed {size}.",
            count=count,
            size=self._format_bytes(size),
        )
        self.log_view.append_event(message)
        QMessageBox.information(
            self,
            self.tr("delete_wav_cache", "Delete WAV cache"),
            message,
        )

    def _cleanup_wav_cache(self, audiobook_id: int | None) -> None:
        try:
            count, size = self.audiobook_store.cleanup_segment_wav_cache(audiobook_id)
        except OSError as exc:
            self._show_error(
                self.tr("cleanup_wavs_failed", "WAV cleanup failed"),
                str(exc),
            )
            return
        self._refresh_wav_cache_stats()
        if hasattr(self, "review_table"):
            self._refresh_review_page()
        message = (
            self.tr(
                "cleanup_wavs_complete",
                "Deleted {count} WAV file(s), freed {size}.",
                count=count,
                size=self._format_bytes(size),
            )
            if count
            else self.tr("cleanup_wavs_none", "No segment WAV files found.")
        )
        self.log_view.append_event(message)
        QMessageBox.information(
            self,
            self.tr("delete_wav_cache", "Delete WAV cache"),
            message,
        )

    def _temp_audio_cache_stats(self) -> tuple[int, int]:
        count = 0
        size = 0
        for directory in self._temp_audio_cache_dirs():
            directory_count, directory_size = self._directory_file_stats(directory)
            count += directory_count
            size += directory_size
        return count, size

    def _temp_audio_cache_dirs(self) -> list[Path]:
        temp_root = Path(tempfile.gettempdir())
        active_dirs: set[Path] = set()
        if hasattr(self, "audio_mix_preview_panel"):
            try:
                active_dirs.add(
                    Path(self.audio_mix_preview_panel.temp_dir.name).resolve()
                )
            except OSError:
                pass
        directories: list[Path] = []
        try:
            children = list(temp_root.iterdir())
        except OSError:
            return []
        for child in children:
            if not child.is_dir():
                continue
            if not child.name.startswith("local_text_2_voice_"):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if resolved in active_dirs:
                continue
            directories.append(child)
        return sorted(directories)

    @staticmethod
    def _directory_file_stats(directory: Path) -> tuple[int, int]:
        count = 0
        size = 0
        try:
            files = [path for path in directory.rglob("*") if path.is_file()]
        except OSError:
            return 0, 0
        for path in files:
            try:
                size += path.stat().st_size
            except OSError:
                continue
            count += 1
        return count, size

    def _delete_temp_audio_cache_dirs(self, directories: list[Path]) -> tuple[int, int]:
        count = 0
        size = 0
        for directory in directories:
            directory_count, directory_size = self._directory_file_stats(directory)
            try:
                shutil.rmtree(directory)
            except OSError:
                continue
            count += directory_count
            size += directory_size
        return count, size

    def _current_project_title(self) -> str:
        metadata = self.settings.get("metadata", {})
        title = ""
        if isinstance(metadata, dict):
            title = str(metadata.get("title", "")).strip()
        if not title:
            audiobook = self.audiobook_store.get_audiobook(self.current_audiobook_id)
            title = audiobook.title if audiobook is not None else ""
        return title or self.tr("untitled_project", "Untitled Audiobook")

    def _project_settings_snapshot(self) -> dict[str, object]:
        self._save_settings()
        snapshot = json.loads(json.dumps(self.settings))
        snapshot["current_project_id"] = self.current_audiobook_id
        return snapshot

    def _project_voice_config_snapshot(self) -> dict[str, object]:
        voice_config = self._current_voice_config()
        if voice_config is not None:
            return voice_config
        return {"engine": str(self.settings.get("tts_engine", "piper") or "piper")}

    def _default_project_parent(self) -> Path:
        stored = str(self.settings.get("last_project_parent", "") or "").strip()
        if stored:
            parent = Path(stored).expanduser()
        else:
            parent = Path.home() / "Documents" / "LocalText2Voice Projects"
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            parent = Path.home()
        return parent

    @staticmethod
    def _safe_project_folder_name(title: str) -> str:
        name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", title).strip(" .-")
        name = re.sub(r"\s+", " ", name)
        return name[:80] or "Audiobook"

    def _prompt_project_location(
        self,
        title: str,
        caption: str,
    ) -> tuple[str, Path] | None:
        project_title, accepted = QInputDialog.getText(
            self,
            caption,
            self.tr("project_name_prompt", "Project name"),
            text=title,
        )
        if not accepted:
            return None
        project_title = project_title.strip() or self.tr(
            "untitled_project",
            "Untitled Audiobook",
        )
        parent_text = QFileDialog.getExistingDirectory(
            self,
            self.tr(
                "project_parent_folder_prompt",
                "Choose where to create the project folder",
            ),
            str(self._default_project_parent()),
        )
        if not parent_text:
            return None
        parent = Path(parent_text)
        target = parent / self._safe_project_folder_name(project_title)
        if not self._confirm_project_directory(target):
            return None
        target.mkdir(parents=True, exist_ok=True)
        self.settings["last_project_parent"] = str(parent)
        try:
            self.settings_manager.save(self.settings)
        except OSError:
            pass
        return project_title, target

    def _confirm_project_directory(self, project_dir: Path) -> bool:
        if not project_dir.exists():
            return True
        try:
            has_content = any(project_dir.iterdir())
        except OSError:
            has_content = True
        if not has_content:
            return True
        choice = QMessageBox.question(
            self,
            self.tr("project_folder_exists_title", "Project folder exists"),
            self.tr(
                "project_folder_exists_message",
                "The selected project folder is not empty. Use it anyway?",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return choice == QMessageBox.StandardButton.Yes

    @staticmethod
    def _project_manifest_filter() -> str:
        return (
            "LocalText2Voice Project (project.localtext2voice.json project.json "
            "*.lt2vproj *.json);;All files (*.*)"
        )

    def _project_output_dir(self, project_dir: Path) -> Path:
        output_dir = project_dir / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _save_project(self) -> bool:
        try:
            snapshot = self._project_settings_snapshot()
            voice_config = self._project_voice_config_snapshot()
            output_dir = self.output_picker.path()
            if not output_dir.is_absolute():
                output_dir = resolve_app_path(output_dir)
            if self.current_audiobook_id is None:
                location = self._prompt_project_location(
                    self._current_project_title(),
                    self.tr("file_save", "Save"),
                )
                if location is None:
                    return False
                title, project_dir = location
                output_dir = self._project_output_dir(project_dir)
                self.output_picker.set_path(output_dir)
                snapshot = self._project_settings_snapshot()
                audiobook = self.audiobook_store.create_audiobook(
                    self.text_editor.toPlainText(),
                    voice_config,
                    output_dir,
                    str(self.split_combo.currentData() or "safe_chunks"),
                    str(self.export_combo.currentData() or "single"),
                    title,
                    snapshot,
                    project_dir,
                )
            else:
                audiobook = self.audiobook_store.save_audiobook_project(
                    self.current_audiobook_id,
                    self.text_editor.toPlainText(),
                    voice_config,
                    output_dir,
                    str(self.split_combo.currentData() or "safe_chunks"),
                    str(self.export_combo.currentData() or "single"),
                    self._current_project_title(),
                    snapshot,
                )
            self._set_current_project(audiobook.id)
            self.project_dirty = False
            self.log_view.append_event(
                self.tr(
                    "project_saved",
                    "Project saved: {title}",
                    title=audiobook.title,
                )
            )
            return True
        except Exception as exc:
            self._show_error(self.tr("project_save_failed", "Could not save project"), str(exc))
            return False

    def _save_project_as(self) -> bool:
        current_title = self._current_project_title()
        location = self._prompt_project_location(
            f"{current_title} Copy",
            self.tr("file_save_as", "Save As"),
        )
        if location is None:
            return False
        title, project_dir = location
        try:
            snapshot = self._project_settings_snapshot()
            voice_config = self._project_voice_config_snapshot()
            output_dir = self._project_output_dir(project_dir)
            self.output_picker.set_path(output_dir)
            snapshot = self._project_settings_snapshot()
            if self.current_audiobook_id is not None:
                audiobook = self.audiobook_store.clone_audiobook(
                    self.current_audiobook_id,
                    title,
                    self.text_editor.toPlainText(),
                    voice_config,
                    output_dir,
                    str(self.split_combo.currentData() or "safe_chunks"),
                    str(self.export_combo.currentData() or "single"),
                    snapshot,
                    project_dir,
                )
            else:
                audiobook = self.audiobook_store.create_audiobook(
                    self.text_editor.toPlainText(),
                    voice_config,
                    output_dir,
                    str(self.split_combo.currentData() or "safe_chunks"),
                    str(self.export_combo.currentData() or "single"),
                    title,
                    snapshot,
                    project_dir,
                )
            self._set_current_project(audiobook.id)
            self.project_dirty = False
            self.log_view.append_event(
                self.tr(
                    "project_saved_as",
                    "Project saved as: {title}",
                    title=audiobook.title,
                )
            )
            return True
        except Exception as exc:
            self._show_error(self.tr("project_save_failed", "Could not save project"), str(exc))
            return False

    def _new_project(self) -> None:
        if not self._confirm_project_switch():
            return
        location = self._prompt_project_location(
            self.tr("untitled_project", "Untitled Audiobook"),
            self.tr("file_new_project", "New Project"),
        )
        if location is None:
            return
        title, project_dir = location
        output_dir = self._project_output_dir(project_dir)
        self.output_picker.set_path(output_dir)
        audiobook = self.audiobook_store.create_audiobook(
            "",
            {"engine": str(self.settings.get("tts_engine", "piper") or "piper")},
            output_dir,
            str(self.split_combo.currentData() or "safe_chunks"),
            str(self.export_combo.currentData() or "single"),
            title,
            self._project_settings_snapshot(),
            project_dir,
        )
        self._load_project(audiobook.id)
        self.log_view.append_event(
            self.tr("project_created", "New project created: {title}", title=title)
        )

    def _open_project_dialog(self) -> None:
        if not self._confirm_project_switch():
            return
        path_text, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("file_open_project", "Open Project"),
            str(self._default_project_parent()),
            self._project_manifest_filter(),
        )
        if not path_text:
            return
        try:
            audiobook = self.audiobook_store.import_project_manifest(Path(path_text))
        except Exception as exc:
            self._show_error(self.tr("file_open_project", "Open Project"), str(exc))
            return
        self.settings["last_project_parent"] = str(Path(path_text).parent.parent)
        self._load_project(audiobook.id)

    def _export_source_text(self) -> None:
        text = self.text_editor.toPlainText()
        audiobook = self.audiobook_store.get_audiobook(self.current_audiobook_id)
        default_path = (
            audiobook.project_dir / "source.txt"
            if audiobook is not None
            else self._default_project_parent() / "source.txt"
        )
        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("file_export_source_text", "Export Source Text..."),
            str(default_path),
            "Text files (*.txt);;Markdown files (*.md);;All files (*.*)",
        )
        if not path_text:
            return
        try:
            Path(path_text).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._show_error(
                self.tr("project_save_failed", "Could not save project"),
                str(exc),
            )
            return
        self.log_view.append_event(
            self.tr("source_text_exported", "Source text exported: {path}", path=path_text)
        )

    def _project_status_text(self, audiobook) -> str:
        if audiobook.clean_mp3_path and Path(audiobook.clean_mp3_path).is_file():
            return self.tr("completed", "Completed")
        segments = self.audiobook_store.list_segments(audiobook.id)
        if segments:
            return self.tr("in_progress", "In progress")
        return self.tr("draft", "Draft")

    def _load_project(self, audiobook_id: int) -> None:
        audiobook = self.audiobook_store.get_audiobook(audiobook_id)
        if audiobook is None:
            self._show_error(
                self.tr("file_open_project", "Open Project"),
                self.tr("project_not_found", "Project not found."),
            )
            return
        self._set_current_project(audiobook.id)
        try:
            project_settings = json.loads(audiobook.project_settings_json or "{}")
        except json.JSONDecodeError:
            project_settings = {}
        if isinstance(project_settings, dict):
            self.settings.update(project_settings)
            self.settings["current_project_id"] = audiobook.id
            self.settings_manager.save(self.settings)
            self._restore_settings()
        self._loading_project = True
        self.text_editor.setPlainText(audiobook.source_text)
        self._loading_project = False
        self.project_dirty = False
        if audiobook.clean_mp3_path and Path(audiobook.clean_mp3_path).is_file():
            self._set_audio_mix_preview_context(Path(audiobook.clean_mp3_path))
        else:
            self.audio_mix_preview_panel.clear_context()
        self._refresh_review_page()
        self._show_generation()
        self.log_view.append_event(
            self.tr("project_opened", "Project opened: {title}", title=audiobook.title)
        )

    def _restore_active_project(self) -> None:
        audiobook = self.audiobook_store.get_audiobook(self.current_audiobook_id)
        if audiobook is None:
            self._set_current_project(None)
            return
        self._loading_project = True
        self.text_editor.setPlainText(audiobook.source_text)
        self._loading_project = False
        self.project_dirty = False
        if audiobook.clean_mp3_path and Path(audiobook.clean_mp3_path).is_file():
            self._set_audio_mix_preview_context(Path(audiobook.clean_mp3_path))
        else:
            self.audio_mix_preview_panel.clear_context()

    def _set_current_project(self, audiobook_id: int | None) -> None:
        self.current_audiobook_id = audiobook_id
        self.settings["current_project_id"] = audiobook_id
        try:
            self.settings_manager.save(self.settings)
        except OSError as exc:
            self.log_view.append_event(f"Could not save current project id: {exc}")

    def _confirm_project_switch(self) -> bool:
        if not self.project_dirty:
            return True
        choice = QMessageBox.question(
            self,
            self.tr("unsaved_project", "Unsaved project"),
            self.tr(
                "unsaved_project_message",
                "Save changes to the current project before continuing?",
            ),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_project()
        return True

    def _show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            self.tr("help_about", "About LocalText2Voice"),
            self.tr(
                "about_text",
                "LocalText2Voice\nAI Voice & Audio Production\nBy Esteban, AndromedaNova.com",
            ),
        )

    def _enable_windows_native_resize_border(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            get_window_long = user32.GetWindowLongPtrW
            set_window_long = user32.SetWindowLongPtrW
            get_window_long.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
            get_window_long.restype = ctypes.c_void_p
            set_window_long.argtypes = [
                ctypes.wintypes.HWND,
                ctypes.c_int,
                ctypes.c_void_p,
            ]
            set_window_long.restype = ctypes.c_void_p

            gwl_style = -16
            ws_thickframe = 0x00040000
            ws_sysmenu = 0x00080000
            ws_minimizebox = 0x00020000
            ws_maximizebox = 0x00010000
            style = int(get_window_long(hwnd, gwl_style) or 0)
            style |= ws_thickframe | ws_sysmenu | ws_minimizebox | ws_maximizebox
            set_window_long(hwnd, gwl_style, ctypes.c_void_p(style))

            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_nozorder = 0x0004
            swp_noactivate = 0x0010
            swp_framechanged = 0x0020
            user32.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                swp_nomove
                | swp_nosize
                | swp_nozorder
                | swp_noactivate
                | swp_framechanged,
            )
        except Exception as exc:
            if hasattr(self, "log_view"):
                self.log_view.append_event(f"Windows resize border setup failed: {exc}")

    def _toggle_maximized_window(self) -> None:
        was_maximized = self._is_window_maximized()
        if was_maximized:
            self.showNormal()
        else:
            self.showMaximized()
        self._set_maximize_button_state(not was_maximized)
        self._update_window_frame_margins()

    def _is_window_maximized(self) -> bool:
        return bool(self.windowState() & Qt.WindowState.WindowMaximized)

    def _set_maximize_button_state(self, maximized: bool) -> None:
        self.title_maximize_button.set_icon_name(
            "window_restore" if maximized else "window_maximize"
        )
        self.title_maximize_button.setToolTip(
            "Restore" if maximized else "Maximize"
        )

    def _update_window_button_state(self) -> None:
        if not hasattr(self, "title_maximize_button"):
            return
        self._set_maximize_button_state(self._is_window_maximized())

    def _update_window_frame_margins(self) -> None:
        if not hasattr(self, "root_layout"):
            return
        margin = 0 if self.isMaximized() or self.isFullScreen() else self._window_shadow_margin
        self.root_layout.setContentsMargins(margin, margin, margin, margin)
        if hasattr(self, "app_shadow_effect"):
            self.app_shadow_effect.setEnabled(margin > 0)
        self._position_resize_handles()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_window_button_state()
            self._update_window_frame_margins()
            self._position_resize_handles()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_resize_handles()

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if sys.platform != "win32" or self.isMaximized():
            return super().nativeEvent(event_type, message)
        try:
            message_address = (
                message.__int__() if hasattr(message, "__int__") else int(message)
            )
            msg = ctypes.wintypes.MSG.from_address(message_address)
        except (TypeError, ValueError, OSError):
            return super().nativeEvent(event_type, message)
        if msg.message != 0x0084:  # WM_NCHITTEST
            return super().nativeEvent(event_type, message)

        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        position = self.mapFromGlobal(QPoint(x, y))
        border = self._resize_border_px
        left = position.x() <= border
        right = position.x() >= self.width() - border
        top = position.y() <= border
        bottom = position.y() >= self.height() - border

        hit_test_values = {
            (True, False, True, False): 13,  # HTTOPLEFT
            (False, True, True, False): 14,  # HTTOPRIGHT
            (True, False, False, True): 16,  # HTBOTTOMLEFT
            (False, True, False, True): 17,  # HTBOTTOMRIGHT
        }
        for flags, result in hit_test_values.items():
            if (left, right, top, bottom) == flags:
                return True, result
        if top:
            return True, 12  # HTTOP
        if bottom:
            return True, 15  # HTBOTTOM
        if left:
            return True, 10  # HTLEFT
        if right:
            return True, 11  # HTRIGHT
        return False, 0

    def _music_library_dir(self) -> Path:
        directory = application_root() / "music" / "background"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _ensure_default_music_selection(self) -> None:
        if self.settings.get("background_path"):
            return
        default_track = self._music_library_dir() / "relax1.mp3"
        if default_track.is_file():
            self.settings["background_enabled"] = True
            self.settings["background_path"] = str(
                default_track.relative_to(application_root())
            )

    def _music_files(self) -> list[Path]:
        directory = self._music_library_dir()
        return sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in {".mp3", ".wav"}
            ),
            key=lambda path: path.name.lower(),
        )

    def _refresh_music_library(self) -> None:
        if not hasattr(self, "music_table"):
            return
        files = self._music_files()
        selected = self._resolved_audio_path(
            Path(str(self.settings.get("background_path", "")))
            if self.settings.get("background_path")
            else None
        )
        self.music_table.setRowCount(0)
        for row, path in enumerate(files):
            self.music_table.insertRow(row)
            is_default = selected is not None and selected == path
            default_item = QTableWidgetItem(
                self.tr("selected", "Selected") if is_default else ""
            )
            default_item.setIcon(ui_icon("apply", active=is_default))
            self.music_table.setItem(row, 0, default_item)

            name_item = QTableWidgetItem(path.stem)
            name_item.setIcon(ui_icon("music"))
            name_item.setToolTip(str(path))
            self.music_table.setItem(row, 1, name_item)
            self.music_table.setItem(row, 2, QTableWidgetItem(self._music_duration_text(path)))
            self.music_table.setItem(row, 3, QTableWidgetItem(self._format_bytes(path.stat().st_size)))
            self.music_table.setCellWidget(row, 4, self._music_actions_widget(path))
            self.music_table.setRowHeight(row, 42)
        self.music_status_label.setText(
            self.tr(
                "music_library_count",
                "{count} music file(s) in {folder}",
                count=len(files),
                folder=str(self._music_library_dir()),
            )
        )

    def _music_actions_widget(self, path: Path) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for icon_name, tooltip, callback in (
            (
                "apply",
                self.tr("select_default", "Use as default"),
                lambda _checked=False, item=path: self._select_default_music(item),
            ),
            (
                "play",
                self.tr("play", "Play"),
                lambda _checked=False, item=path: self._play_music(item),
            ),
            (
                "pause",
                self.tr("pause", "Pause"),
                lambda _checked=False: self.music_library_player.pause(),
            ),
            (
                "stop",
                self.tr("stop", "Stop"),
                lambda _checked=False: self.music_library_player.stop(),
            ),
            (
                "file",
                self.tr("rename", "Rename"),
                lambda _checked=False, item=path: self._rename_music(item),
            ),
            (
                "delete",
                self.tr("delete", "Delete"),
                lambda _checked=False, item=path: self._delete_music(item),
            ),
        ):
            button = QPushButton()
            button.setIcon(ui_icon(icon_name))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tooltip)
            button.setFixedSize(32, 30)
            button.clicked.connect(callback)
            if icon_name in {"stop", "delete"}:
                button.setObjectName("dangerButton")
            layout.addWidget(button)
        return widget

    def _import_music_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("import_music", "Import music"),
            "",
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        if not selected:
            return
        source = Path(selected)
        destination = self._unique_music_destination(source.name)
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            self._show_error(
                self.tr("import_failed", "Import failed"),
                str(exc),
            )
            return
        self.log_view.append_event(f"Imported music: {destination.name}")
        self._refresh_music_library()

    def _unique_music_destination(self, file_name: str) -> Path:
        destination = self._music_library_dir() / Path(file_name).name
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while True:
            candidate = destination.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _select_default_music(self, path: Path) -> None:
        relative = path.relative_to(application_root())
        self.settings["background_enabled"] = True
        self.settings["background_path"] = str(relative)
        self.background_enabled_checkbox.setChecked(True)
        self.background_picker.set_path(relative)
        self.settings_manager.save(self.settings)
        self.log_view.append_event(f"Default podcast music selected: {path.name}")
        self._refresh_music_library()
        self._refresh_audio_mix_music(path)

    def _play_music(self, path: Path) -> None:
        self.music_library_player.setSource(QUrl.fromLocalFile(str(path)))
        self.music_library_player.play()
        self.log_view.append_event(f"Playing music: {path.name}")

    def _refresh_audio_mix_music(self, path: Path | None = None) -> None:
        if not hasattr(self, "audio_mix_preview_panel"):
            return
        context = self.audio_mix_preview_panel.context
        if context is None:
            return
        selected = path if path is not None else self._selected_music_path()
        self.audio_mix_preview_panel.set_context(
            replace(
                context,
                music_path=selected if selected is not None and selected.is_file() else None,
                settings=self.audio_mix_preview_panel.current_settings(),
            )
        )

    def _rename_music(self, path: Path) -> None:
        new_name, accepted = QInputDialog.getText(
            self,
            self.tr("rename_music", "Rename music"),
            self.tr("new_name", "New name"),
            text=path.stem,
        )
        if not accepted:
            return
        safe_name = new_name.strip()
        if not safe_name:
            return
        destination = path.with_name(f"{safe_name}{path.suffix}")
        if destination.exists() and destination != path:
            self._show_error(
                self.tr("rename_failed", "Rename failed"),
                self.tr("file_already_exists", "A file with that name already exists."),
            )
            return
        try:
            path.rename(destination)
        except OSError as exc:
            self._show_error(self.tr("rename_failed", "Rename failed"), str(exc))
            return
        if self._is_default_music(path):
            self._select_default_music(destination)
        else:
            self._refresh_music_library()

    def _delete_music(self, path: Path) -> None:
        choice = QMessageBox.question(
            self,
            self.tr("delete_music", "Delete music"),
            self.tr(
                "delete_music_confirm",
                "Delete {name} from the music library?",
                name=path.name,
            ),
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as exc:
            self._show_error(self.tr("delete_failed", "Delete failed"), str(exc))
            return
        if self._is_default_music(path):
            self.settings["background_enabled"] = False
            self.settings["background_path"] = ""
            self.background_enabled_checkbox.setChecked(False)
            self.background_picker.set_path("")
            self.settings_manager.save(self.settings)
        self.music_library_player.stop()
        self.log_view.append_event(f"Deleted music: {path.name}")
        self._refresh_music_library()

    def _is_default_music(self, path: Path) -> bool:
        configured = self.settings.get("background_path")
        if not configured:
            return False
        resolved = self._resolved_audio_path(Path(str(configured)))
        return resolved == path

    def _selected_music_path(self) -> Path | None:
        if not bool(self.settings.get("background_enabled", True)):
            return None
        configured = str(self.settings.get("background_path", "")).strip()
        if not configured:
            configured = "music/background/relax1.mp3"
        resolved = self._resolved_audio_path(Path(configured))
        return resolved if resolved is not None and resolved.is_file() else None

    def _open_music_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._music_library_dir())))

    def _music_duration_text(self, path: Path) -> str:
        duration = self._probe_audio_duration(path)
        if duration is None:
            return "--:--"
        total = max(0, round(duration))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _probe_audio_duration(self, path: Path) -> float | None:
        try:
            audio = MutagenFile(path)
        except Exception:
            return None
        info = getattr(audio, "info", None)
        length = getattr(info, "length", None)
        if length is None:
            return None
        try:
            return float(length)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_bytes(size: int) -> str:
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
            value /= 1024
        return f"{size} B"

    def _current_voice_config(self) -> dict[str, object] | None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        speed = self.speed_spin.value()
        if engine_id == "piper":
            voice_id = self.voice_combo.currentData()
            voice = next(
                (
                    candidate
                    for candidate in self.voices
                    if candidate.voice_id == voice_id
                ),
                None,
            )
            if voice is None:
                self._show_error(
                    self.tr("missing_voice", "No voice selected"),
                    self.tr(
                        "missing_voice_message",
                        "Add a Piper .onnx model and its .onnx.json file to the "
                        "voices folder.",
                    ),
                )
                return None
            config = voice.as_config(speed)
            config["engine"] = "piper"
            return config

        if engine_id == "openai":
            return {
                "engine": "openai",
                "speed": speed,
                "api_key": self.openai_api_key_edit.text().strip(),
                "model": self.openai_model_combo.currentData(),
                "voice": self.openai_voice_combo.currentData(),
                "instructions": self.openai_instructions_edit.text().strip(),
            }
        if engine_id == "kokoro":
            voice_id = str(
                self.kokoro_python_voice_combo.currentData() or "af_heart"
            )
            return {
                "engine": "kokoro",
                "speed": speed,
                "voice": voice_id,
                "lang": self._kokoro_python_language_for_voice(voice_id),
                "provider": "auto",
                "model_path": str(
                    self.kokoro_python_manager.model_path_for_provider("auto")
                ),
            }
        if engine_id == "chatterbox":
            return self._chatterbox_voice_config_for_ui()
        if engine_id == "qwen":
            return self._qwen_voice_config_for_ui()
        if engine_id == "omnivoice":
            return self._omnivoice_voice_config_for_ui()
        if engine_id == "elevenlabs":
            return {
                "engine": "elevenlabs",
                "speed": speed,
                "api_key": self.elevenlabs_api_key_edit.text().strip(),
                "voice_id": self.elevenlabs_voice_id_edit.text().strip(),
                "model_id": self.elevenlabs_model_combo.currentData(),
                "output_format": self.elevenlabs_output_combo.currentData(),
                "stability": self.elevenlabs_stability_spin.value(),
                "similarity_boost": self.elevenlabs_similarity_spin.value(),
                "style": self.elevenlabs_style_spin.value(),
                "use_speaker_boost": (
                    self.elevenlabs_speaker_boost_checkbox.isChecked()
                ),
            }
        if engine_id == "gemini":
            return {
                "engine": "gemini",
                "speed": speed,
                "api_key": self.gemini_api_key_edit.text().strip(),
                "model": self.gemini_model_combo.currentData(),
                "voice": self.gemini_voice_combo.currentData(),
                "prompt": self.gemini_prompt_edit.text().strip(),
            }
        if engine_id == "azure":
            return {
                "engine": "azure",
                "speed": speed,
                "api_key": self.azure_api_key_edit.text().strip(),
                "region": self.azure_region_edit.text().strip(),
                "voice": self.azure_voice_edit.text().strip(),
                "output_format": self.azure_output_combo.currentData(),
                "style": self.azure_style_edit.text().strip(),
            }
        if engine_id.startswith("custom:"):
            engine = self._custom_engine_by_key(engine_id)
            if engine is None:
                self._show_error(
                    self.tr("generation_failed", "Generation failed"),
                    self.tr(
                        "custom_engine_missing",
                        "No custom engine is selected.",
                    ),
                )
                return None
            config = dict(engine)
            config["engine"] = engine_id
            config["speed"] = speed
            config["ffmpeg_path"] = self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe")
            config.setdefault("name", self._tts_engine_label(engine_id))
            return config
        self._show_error(
            self.tr("generation_failed", "Generation failed"),
            f"Unknown TTS engine: {engine_id}",
        )
        return None

    def _start_generation(self) -> None:
        if self.worker_thread is not None:
            return

        text = self.text_editor.toPlainText().strip()
        if not text:
            self._show_error(
                self.tr("missing_text", "Missing text"),
                self.tr("missing_text_message", "Paste or import text first."),
            )
            return

        voice_config = self._current_voice_config()
        if voice_config is None:
            return
        engine_id = str(voice_config.get("engine", "piper"))

        output_dir = self.output_picker.path()
        if not output_dir.is_absolute():
            output_dir = resolve_app_path(output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error(
                self.tr("output_error", "Output folder error"),
                str(exc),
            )
            return

        self._save_settings()
        metadata = self.settings.get("metadata", {})
        title = (
            str(metadata.get("title", "Audiobook"))
            if isinstance(metadata, dict)
            else "Audiobook"
        )
        request = {
            "text": text,
            "title": title or "Audiobook",
            "engine_id": engine_id,
            "voice": str(
                voice_config.get("voice") or voice_config.get("voice_id") or ""
            ),
            "language": str(
                voice_config.get("language") or voice_config.get("lang") or ""
            ),
            "speed": float(voice_config.get("speed", self.speed_spin.value())),
            "output_dir": str(output_dir),
            "split_mode": str(self.split_combo.currentData()),
            "export_mode": str(self.export_combo.currentData()),
            "project_audiobook_id": self.current_audiobook_id,
            # The desktop UI owns the interactive Review and Audio Mix transitions.
            "review_policy": "off",
            "mix_policy": "clean_only",
            "client": "desktop_ui",
        }
        self.log_view.clear()
        self.log_view.append_event(self.tr("starting", "Starting generation..."))
        self.progress_bar.setValue(0)
        self.status_label.setText(self.tr("preparing", "Preparing audio job..."))
        self.generation_started_at = time.monotonic()
        self.progress_current = 0
        self.progress_total = 0
        self.last_output_folder = None
        self.open_output_button.setVisible(False)
        self.header_open_output_button.setEnabled(False)
        self.time_label.setVisible(True)
        self._update_generation_time()
        self.generation_timer.start()
        if self.preloaded_tts_engine is not None:
            self.log_view.append_event(
                self.tr(
                    "moving_generation_to_shared_host",
                    "Generation uses the shared engine host; releasing the legacy "
                    "in-process {engine} copy.",
                    engine=self._tts_engine_label(engine_id),
                )
            )
            self._unload_preloaded_tts_engine(log_message=False)
        self.worker_uses_preloaded_engine = False
        self.host_generation_engine_id = engine_id
        self.loaded_tts_engine_id = engine_id
        self._update_header_engine_label()
        self._set_running(True)

        thread = QThread(self)
        worker = EngineHostGenerationWorker(self.engine_host_client, request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_host_generation_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self.worker_thread = thread
        self.worker = worker
        thread.start()

    def _on_host_generation_finished(self, result: dict[str, object]) -> None:
        audiobook_id = result.get("audiobook_id")
        try:
            resolved_id = int(audiobook_id) if audiobook_id is not None else None
        except (TypeError, ValueError):
            resolved_id = None
        if resolved_id is not None:
            self._set_current_project(resolved_id)
        engine_id = self.host_generation_engine_id
        if engine_id:
            self.host_loaded_tts_engine_ids.add(engine_id)
        outputs_value = result.get("outputs", [])
        outputs = (
            [str(path) for path in outputs_value]
            if isinstance(outputs_value, list)
            else []
        )
        if not outputs:
            clean_mp3 = str(result.get("clean_mp3", "") or "")
            mix_mp3 = str(result.get("mix_mp3", "") or "")
            outputs = [path for path in (clean_mp3, mix_mp3) if path]
        self._on_finished(outputs)

    def _cancel_generation(self) -> None:
        if self.worker is None:
            return
        self.status_label.setText(
            self.tr("cancelling", "Cancelling generation...")
        )
        self.cancel_button.setEnabled(False)
        self.worker.request_cancel()

    def _on_progress(self, current: int, total: int, status: str) -> None:
        percentage = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(min(99, percentage))
        self.status_label.setText(status)
        self.progress_current = current
        self.progress_total = total
        self._update_generation_time()

    def _on_finished(self, output_paths: list[str]) -> None:
        self.generation_timer.stop()
        self.progress_current = self.progress_total
        self._update_generation_time()
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr(
                "generation_complete",
                "Generation complete. Created {count} file(s).",
                count=len(output_paths),
            )
        )
        self.log_view.append_event(self.status_label.text())
        if output_paths:
            audiobook = self._current_audiobook()
            if audiobook is not None:
                self._set_current_project(audiobook.id)
                self.project_dirty = False
            self.last_output_folder = Path(output_paths[0]).parent
            self.header_open_output_button.setEnabled(
                self.header_open_output_button.isVisible()
                and self._current_output_folder() is not None
            )
            if (
                self.review_enabled_checkbox.isChecked()
                and self.review_auto_checkbox.isChecked()
            ):
                if (
                    self.faster_whisper_manager.is_installed()
                    and self.verification_thread is None
                ):
                    self.review_after_generation_outputs = list(output_paths)
                    self._start_verification_for_latest(show_review=False)
                    return
                self.log_view.append_event(
                    "Automatic review skipped: Faster Whisper is not ready."
                )
            self._show_audio_mix_preview(output_paths)

    def _on_failed(self, message: str) -> None:
        self.generation_timer.stop()
        self._update_generation_time()
        self.status_label.setText(self.tr("generation_failed", "Generation failed"))
        self.log_view.append_event(message)
        if self.worker_uses_preloaded_engine:
            self._unload_preloaded_tts_engine(log_message=True)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_cancelled(self) -> None:
        self.generation_timer.stop()
        self._update_generation_time()
        self.status_label.setText(
            self.tr("generation_cancelled", "Generation cancelled")
        )
        self.log_view.append_event(
            self.tr("generation_cancelled", "Generation cancelled")
        )
        if self.worker_uses_preloaded_engine:
            self._unload_preloaded_tts_engine(log_message=True)

    def _clear_worker(self) -> None:
        self.generation_timer.stop()
        self.worker = None
        self.worker_thread = None
        self.worker_uses_preloaded_engine = False
        selected_engine = str(self.tts_engine_combo.currentData() or "piper")
        self.loaded_tts_engine_id = (
            selected_engine
            if selected_engine in self.host_loaded_tts_engine_ids
            else self.preloaded_tts_engine_id
        )
        self.host_generation_engine_id = None
        self._update_header_engine_label()
        self._set_running(False)

    def _update_generation_time(self) -> None:
        if self.generation_started_at is None:
            return
        elapsed = max(0, round(time.monotonic() - self.generation_started_at))
        if self.progress_current > 0 and self.progress_total > self.progress_current:
            remaining = round(
                elapsed
                / self.progress_current
                * (self.progress_total - self.progress_current)
            )
            remaining_text = self._format_duration(remaining)
        elif self.progress_total > 0 and self.progress_current >= self.progress_total:
            remaining_text = self._format_duration(0)
        else:
            remaining_text = self.tr("calculating", "Calculating...")
        self.time_label.setText(
            self.tr(
                "generation_time_status",
                "Elapsed: {elapsed} | Estimated remaining: {remaining}",
                elapsed=self._format_duration(elapsed),
                remaining=remaining_text,
            )
        )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _open_last_output_folder(self) -> None:
        folder = self._current_output_folder()
        if folder is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(folder))
            )

    def _current_output_folder(self) -> Path | None:
        if (
            hasattr(self, "audio_mix_preview_panel")
            and self.header_open_output_button.isVisible()
            and self.audio_mix_preview_panel.context is not None
        ):
            return self.audio_mix_preview_panel.context.output_dir
        return self.last_output_folder

    def _show_audio_mix_preview(self, output_paths: list[str]) -> None:
        narration_path = self._first_narration_output(output_paths)
        if narration_path is None:
            return
        self._set_audio_mix_preview_context(narration_path)
        self._show_mix_preview_page()

    def _ensure_audio_mix_preview_context(self) -> None:
        if not hasattr(self, "audio_mix_preview_panel"):
            return
        narration_path = self._latest_clean_narration_path()
        if narration_path is None:
            return
        context = self.audio_mix_preview_panel.context
        if context is not None and context.voice_path.resolve() == narration_path.resolve():
            return
        self._set_audio_mix_preview_context(narration_path)

    def _latest_clean_narration_path(self) -> Path | None:
        audiobook = self._current_audiobook()
        if audiobook is None:
            return None
        clean_path, _mix_path = self.audiobook_store.audiobook_output_paths(
            audiobook.id
        )
        if clean_path:
            path = Path(clean_path)
            if path.is_file():
                return path
        return None

    def _set_audio_mix_preview_context(self, narration_path: Path) -> None:
        if not narration_path.is_file():
            self.log_view.append_event(
                f"Audio Mix skipped: narration file not found: {narration_path}"
            )
            return
        output_dir = narration_path.parent
        self.last_output_folder = output_dir
        self.header_open_output_button.setEnabled(
            self.header_open_output_button.isVisible()
        )
        context = AudioMixPreviewContext(
            voice_path=narration_path,
            output_dir=output_dir,
            ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            music_path=self._selected_music_path(),
            settings=self._current_mix_settings(),
            metadata=dict(self.settings.get("metadata", {})),
        )
        self.audio_mix_preview_panel.set_context(context)

    @staticmethod
    def _first_narration_output(output_paths: list[str]) -> Path | None:
        for output_path in output_paths:
            path = Path(output_path)
            stem = path.stem.lower()
            if not stem.endswith("_mix") and not stem.endswith("_podcast"):
                return path
        return Path(output_paths[0]) if output_paths else None

    def _current_mix_settings(self) -> AudioMixSettings:
        return AudioMixSettings(
            voice_volume_db=self.voice_volume_db_spin.value(),
            music_volume_db=self.background_volume_spin.value(),
            voice_start_offset_ms=self.voice_start_offset_spin.value(),
            music_tail_ms=self.music_tail_spin.value(),
            music_fade_in_seconds=self.fade_in_spin.value(),
            music_fade_out_seconds=self.fade_out_spin.value(),
            ducking_enabled=self.podcast_ducking_checkbox.isChecked(),
            ducking_strength=str(
                self.ducking_strength_combo.currentData() or "low"
            ),
            loop_background=self.background_loop_checkbox.isChecked(),
            normalize=self.podcast_normalize_checkbox.isChecked(),
            mp3_bitrate=str(self.settings.get("mp3_bitrate", "128k")),
        )

    def _on_mix_preview_settings_changed(self, settings: AudioMixSettings) -> None:
        self.voice_volume_db_spin.blockSignals(True)
        self.background_volume_spin.blockSignals(True)
        self.voice_start_offset_spin.blockSignals(True)
        self.music_tail_spin.blockSignals(True)
        self.fade_in_spin.blockSignals(True)
        self.fade_out_spin.blockSignals(True)
        self.podcast_ducking_checkbox.blockSignals(True)
        self.ducking_strength_combo.blockSignals(True)
        self.voice_volume_db_spin.setValue(settings.voice_volume_db)
        self.background_volume_spin.setValue(settings.music_volume_db)
        self.voice_start_offset_spin.setValue(settings.voice_start_offset_ms)
        self.music_tail_spin.setValue(settings.music_tail_ms)
        self.fade_in_spin.setValue(settings.music_fade_in_seconds)
        self.fade_out_spin.setValue(settings.music_fade_out_seconds)
        self.podcast_ducking_checkbox.setChecked(settings.ducking_enabled)
        self._select_combo_data(
            self.ducking_strength_combo,
            settings.ducking_strength,
        )
        self.voice_volume_db_spin.blockSignals(False)
        self.background_volume_spin.blockSignals(False)
        self.voice_start_offset_spin.blockSignals(False)
        self.music_tail_spin.blockSignals(False)
        self.fade_in_spin.blockSignals(False)
        self.fade_out_spin.blockSignals(False)
        self.podcast_ducking_checkbox.blockSignals(False)
        self.ducking_strength_combo.blockSignals(False)
        self._save_settings()

    def _on_mix_preview_render_finished(self, path: str) -> None:
        self.last_output_folder = Path(path).parent
        self.log_view.append_event(f"Saved mix preview render: {path}")
        if not bool(self.settings.get("auto_delete_segment_wavs_after_mix", False)):
            return
        audiobook = self._current_audiobook()
        if audiobook is None:
            return
        try:
            count, size = self.audiobook_store.cleanup_segment_wav_cache(audiobook.id)
        except OSError as exc:
            self.log_view.append_event(
                self.tr(
                    "cleanup_wavs_failed_with_error",
                    "WAV cleanup failed: {error}",
                    error=str(exc),
                )
            )
            return
        if count:
            self.log_view.append_event(
                self.tr(
                    "auto_cleanup_wavs_done",
                    "Auto cleanup removed {count} WAV file(s), freed {size}.",
                    count=count,
                    size=self._format_bytes(size),
                )
            )
        self._refresh_wav_cache_stats()
        if hasattr(self, "review_table"):
            self._refresh_review_page()

    def _refresh_review_page(self) -> None:
        audiobook = self._current_audiobook()
        previous_selection = self.selected_review_segment_id
        current_selection = self._selected_review_segment() if hasattr(self, "review_table") else None
        if current_selection is not None:
            previous_selection = current_selection.id
        self.review_segments = []
        self.review_table.blockSignals(True)
        self.review_table.setRowCount(0)
        if audiobook is None:
            self.review_table.blockSignals(False)
            self.selected_review_segment_id = None
            self._clear_review_detail()
            self.review_status_label.setText(
                self.tr("review_no_audiobook", "No generated audiobook found yet.")
            )
            self.review_verify_button.setEnabled(False)
            self.review_rebuild_button.setEnabled(False)
            return
        segments = self.audiobook_store.list_segments(audiobook.id)
        pending_count = self._review_pending_count(segments)
        dirty_count = self._review_dirty_count(segments)
        filtered_segments = [
            segment for segment in segments if self._review_filter_matches(segment)
        ]
        self.review_segments = filtered_segments
        self.review_subtitle_label.setText(
            self.tr(
                "review_current_audiobook",
                "Latest audiobook: {title} ({count} segment(s)).",
                title=audiobook.title,
                count=len(segments),
            )
        )
        self.review_verify_button.setEnabled(
            pending_count > 0
            and self.faster_whisper_manager.is_installed()
            and self.verification_thread is None
        )
        self.review_verify_button.setText(
            self.tr(
                "verify_pending_segments",
                "Verify pending ({count})",
                count=pending_count,
            )
            if pending_count
            else self.tr("verify_pending_segments_none", "No pending verification")
        )
        self.review_rebuild_button.setEnabled(
            bool(segments)
            and self.audiobook_rebuild_thread is None
            and self.segment_regeneration_thread is None
        )
        self.review_rebuild_button.setText(
            self.tr(
                "review_rebuild_audiobook_dirty",
                "Rebuild audiobook",
            )
            if dirty_count
            else self.tr("review_rebuild_audiobook", "Rebuild audiobook")
        )
        self.review_rebuild_button.setIcon(
            ui_icon("warning", danger=True)
            if dirty_count
            else ui_icon("render")
        )
        self.review_rebuild_button.setToolTip(
            self.tr(
                "review_rebuild_dirty_tooltip",
                "{count} changed segment(s) need rebuilding.",
                count=dirty_count,
            )
            if dirty_count
            else self.tr(
                "review_rebuild_clean_tooltip",
                "Rebuild the audiobook from current segment audio files.",
            )
        )
        self.review_table.setRowCount(len(filtered_segments))
        selected_row = -1
        for row_index, segment in enumerate(filtered_segments):
            if previous_selection == segment.id:
                selected_row = row_index
            score = (
                ""
                if segment.similarity_score is None
                else f"{segment.similarity_score:.1f}%"
            )
            state = self._segment_review_state(segment)
            values = [
                str(segment.sequence_index),
                segment.chapter_title,
                state,
                score,
                self._short_table_text(segment.source_text),
                self._short_table_text(segment.transcript_text),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, segment.id)
                item.setToolTip(value)
                if column in {0, 3}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._apply_review_item_style(item, state)
                self.review_table.setItem(row_index, column, item)

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(4)
            play_button = QPushButton()
            play_button.setIcon(ui_icon("play"))
            play_button.setToolTip(self.tr("play", "Play"))
            play_button.setFixedWidth(34)
            play_button.setEnabled(Path(segment.wav_path).is_file())
            play_button.clicked.connect(
                lambda _checked=False, path=segment.wav_path: self._play_review_audio(path)
            )
            action_layout.addWidget(play_button)
            self.review_table.setCellWidget(row_index, 6, action_widget)
        self.review_table.blockSignals(False)
        if selected_row >= 0:
            self.review_table.selectRow(selected_row)
            self.selected_review_segment_id = previous_selection
            self._on_review_selection_changed()
        else:
            self.selected_review_segment_id = None
            self._clear_review_detail()
        self.review_status_label.setText(
            self.tr(
                "review_filter_status",
                "Showing {shown} of {total} segment(s). Review database: {path}",
                shown=len(filtered_segments),
                total=len(segments),
                path=str(self.audiobook_store.db_path),
            )
        )

    def _segment_review_state(self, segment: StoredSegment) -> str:
        if segment.status in {"failed", "edited", "rendering"}:
            return segment.status
        return segment.verification_status or segment.status

    def _review_filter_matches(self, segment: StoredSegment) -> bool:
        if not hasattr(self, "review_filter_combo"):
            return True
        filter_value = str(self.review_filter_combo.currentData() or "all")
        state = self._segment_review_state(segment)
        if filter_value == "all":
            return True
        if filter_value == "attention":
            return state in {"retry_needed", "review", "failed", "edited"}
        return state == filter_value

    @staticmethod
    def _review_pending_count(segments: list[StoredSegment]) -> int:
        return sum(
            1
            for segment in segments
            if segment.status in {"rendered", "verified"}
            and Path(segment.wav_path).is_file()
            and (
                segment.similarity_score is None
                or segment.verification_status in {"", "not_verified"}
            )
        )

    @staticmethod
    def _review_dirty_count(segments: list[StoredSegment]) -> int:
        return sum(1 for segment in segments if segment.needs_rebuild)

    @staticmethod
    def _review_failed_count(segments: list[StoredSegment]) -> int:
        return sum(
            1
            for segment in segments
            if segment.status in {"failed", "edited"}
            or segment.verification_status in {"retry_needed", "review"}
        )

    def _apply_review_item_style(self, item: QTableWidgetItem, state: str) -> None:
        state = state.casefold()
        background: QColor | None = None
        foreground: QColor | None = None
        if state in {"retry_needed", "failed"}:
            background = QColor("#fff1f2")
            foreground = QColor("#b91c1c")
        elif state == "review":
            background = QColor("#fffbeb")
            foreground = QColor("#92400e")
        elif state == "edited":
            background = QColor("#eff6ff")
            foreground = QColor("#1d4ed8")
        elif state == "approved":
            background = QColor("#ecfdf5")
            foreground = QColor("#047857")
        if background is not None:
            item.setBackground(QBrush(background))
        if foreground is not None:
            item.setForeground(QBrush(foreground))

    def _on_review_selection_changed(self) -> None:
        segment = self._selected_review_segment()
        if segment is None:
            self._clear_review_detail()
            return
        self.selected_review_segment_id = segment.id
        state = self._segment_review_state(segment)
        score = (
            self.tr("not_available", "N/A")
            if segment.similarity_score is None
            else f"{segment.similarity_score:.1f}%"
        )
        self.review_detail_label.setText(
            self.tr(
                "review_selected_segment",
                "Segment {index} | Status: {status} | Similarity: {score}",
                index=segment.sequence_index,
                status=state,
                score=score,
            )
        )
        self.review_source_detail.setPlainText(segment.source_text)
        self.review_transcript_detail.setPlainText(segment.transcript_text)
        has_audio = Path(segment.wav_path).is_file()
        busy = self.segment_regeneration_thread is not None
        self.review_save_text_button.setEnabled(not busy)
        self.review_play_current_button.setEnabled(has_audio)
        self.review_regenerate_button.setEnabled(not busy)

    def _clear_review_detail(self) -> None:
        if not hasattr(self, "review_source_detail"):
            return
        self.review_detail_label.setText(
            self.tr(
                "review_select_segment",
                "Select a segment to inspect the full original and transcript.",
            )
        )
        self.review_source_detail.clear()
        self.review_transcript_detail.clear()
        self.review_save_text_button.setEnabled(False)
        self.review_play_current_button.setEnabled(False)
        self.review_regenerate_button.setEnabled(False)

    def _selected_review_segment(self) -> StoredSegment | None:
        selected_items = self.review_table.selectedItems()
        if not selected_items:
            return None
        segment_id = selected_items[0].data(Qt.ItemDataRole.UserRole)
        try:
            wanted_id = int(segment_id)
        except (TypeError, ValueError):
            return None
        for segment in self.review_segments:
            if segment.id == wanted_id:
                return segment
        return self.audiobook_store.get_segment(wanted_id)

    def _save_review_segment_text(self) -> None:
        segment = self._selected_review_segment()
        if segment is None:
            return
        new_text = self.review_source_detail.toPlainText().strip()
        if not new_text:
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr("review_empty_segment", "Segment text cannot be empty."),
            )
            return
        if new_text == segment.source_text:
            self.review_status_label.setText(
                self.tr("review_no_text_changes", "No text changes to save.")
            )
            return
        self.audiobook_store.update_segment_text(segment.id, new_text)
        self.log_view.append_event(f"Edited segment {segment.sequence_index}.")
        self.selected_review_segment_id = segment.id
        self._refresh_review_page()

    def _play_selected_review_audio(self) -> None:
        segment = self._selected_review_segment()
        if segment is not None:
            self._play_review_audio(segment.wav_path)

    def _regenerate_selected_review_segment(self) -> None:
        segment = self._selected_review_segment()
        if segment is not None:
            self._regenerate_review_segment(segment.id)

    def _regenerate_review_segment(self, segment_id: int) -> None:
        if self.segment_regeneration_thread is not None:
            return
        segment = self.audiobook_store.get_segment(segment_id)
        if segment is None:
            return
        fallback_voice_config = self._current_voice_config()
        if fallback_voice_config is None:
            return
        voice_config = self._stored_segment_voice_config(segment, fallback_voice_config)
        engine_id = str(voice_config.get("engine", "piper"))
        preloaded_engine = (
            self.preloaded_tts_engine
            if self.preloaded_tts_engine_id == engine_id
            else None
        )
        self.segment_regeneration_uses_preloaded_engine = preloaded_engine is not None
        piper_path = resolve_app_path(
            self.piper_path_edit.text().strip() or "engines/piper/piper.exe"
        )
        candidate_wav = self._review_candidate_wav_path(segment)
        self.review_candidate_segment_id = segment.id
        self.review_candidate_wav = candidate_wav
        self._show_review_regeneration_dialog(segment)
        self.review_progress_bar.setVisible(True)
        self.review_progress_bar.setRange(0, 100)
        self.review_progress_bar.setValue(0)
        self.review_status_label.setText(
            self.tr(
                "review_regenerating_segment",
                "Regenerating segment {index}...",
                index=segment.sequence_index,
            )
        )
        thread = QThread(self)
        worker = SegmentRegenerationWorker(
            AudiobookStore(),
            segment_id,
            piper_path,
            fallback_voice_config,
            preloaded_engine,
            candidate_wav,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_review_regeneration_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_review_segment_regenerated)
        worker.failed.connect(self._on_review_regeneration_failed)
        worker.cancelled.connect(self._on_review_regeneration_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_segment_regeneration_worker)
        self.segment_regeneration_worker = worker
        self.segment_regeneration_thread = thread
        self._refresh_review_page()
        thread.start()

    def _review_candidate_wav_path(self, segment: StoredSegment) -> Path:
        current = Path(segment.wav_path)
        directory = (
            current.parent
            if current.parent.name == "candidates"
            else current.parent / "candidates"
        )
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return directory / f"segment_{segment.sequence_index:04d}_{stamp}.wav"

    def _show_review_regeneration_dialog(self, segment: StoredSegment) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr(
                "review_regeneration_title",
                "Regenerated segment preview",
            )
        )
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        self.review_regeneration_label = QLabel(
            self.tr(
                "review_regenerating_segment",
                "Regenerating segment {index}...",
                index=segment.sequence_index,
            )
        )
        self.review_regeneration_label.setWordWrap(True)
        self.review_regeneration_progress = QProgressBar()
        self.review_regeneration_progress.setRange(0, 0)
        layout.addWidget(self.review_regeneration_label)
        layout.addWidget(self.review_regeneration_progress)

        actions = QHBoxLayout()
        self.review_candidate_play_button = QPushButton(
            self.tr("review_play_candidate", "Play candidate")
        )
        self.review_candidate_play_button.setIcon(ui_icon("play"))
        self.review_candidate_play_button.setEnabled(False)
        self.review_candidate_play_button.clicked.connect(self._play_review_candidate)
        self.review_candidate_approve_button = QPushButton(
            self.tr("review_approve_candidate", "Approve and replace")
        )
        self.review_candidate_approve_button.setIcon(ui_icon("apply"))
        self.review_candidate_approve_button.setEnabled(False)
        self.review_candidate_approve_button.clicked.connect(self._approve_review_candidate)
        self.review_candidate_discard_button = QPushButton(
            self.tr("review_discard_candidate", "Discard")
        )
        self.review_candidate_discard_button.setIcon(ui_icon("cancel"))
        self.review_candidate_discard_button.clicked.connect(self._discard_review_candidate)
        actions.addStretch(1)
        actions.addWidget(self.review_candidate_play_button)
        actions.addWidget(self.review_candidate_approve_button)
        actions.addWidget(self.review_candidate_discard_button)
        layout.addLayout(actions)
        dialog.finished.connect(self._on_review_regeneration_dialog_finished)
        self.review_regeneration_dialog = dialog
        dialog.show()

    def _stored_segment_voice_config(
        self,
        segment: StoredSegment,
        fallback: dict,
    ) -> dict:
        try:
            stored = json.loads(segment.engine_config_json or "{}")
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict) and stored.get("engine"):
            return stored
        return dict(fallback)

    def _on_review_regeneration_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        self._on_review_progress(current, total, message)
        if hasattr(self, "review_regeneration_label"):
            self.review_regeneration_label.setText(message)
        if hasattr(self, "review_regeneration_progress"):
            if total > 0:
                self.review_regeneration_progress.setRange(0, 100)
                self.review_regeneration_progress.setValue(
                    round(current / total * 100)
                )
            else:
                self.review_regeneration_progress.setRange(0, 0)

    def _on_review_segment_regenerated(self, segment_id: int, candidate_path: str) -> None:
        self.review_progress_bar.setVisible(False)
        self.review_candidate_segment_id = segment_id
        self.review_candidate_wav = Path(candidate_path)
        segment = self.audiobook_store.get_segment(segment_id)
        if segment is not None:
            self.log_view.append_event(
                f"Candidate audio for segment {segment.sequence_index} is ready."
            )
        if hasattr(self, "review_regeneration_label"):
            self.review_regeneration_label.setText(
                self.tr(
                    "review_candidate_ready",
                    "Candidate generated. Listen to it, then approve or discard it.",
                )
            )
        if hasattr(self, "review_regeneration_progress"):
            self.review_regeneration_progress.setRange(0, 100)
            self.review_regeneration_progress.setValue(100)
        for button_name in (
            "review_candidate_play_button",
            "review_candidate_approve_button",
        ):
            if hasattr(self, button_name):
                getattr(self, button_name).setEnabled(True)
        self._play_review_candidate()
        self._refresh_review_page()

    def _on_review_regeneration_failed(self, message: str) -> None:
        self.review_progress_bar.setVisible(False)
        if hasattr(self, "review_regeneration_label"):
            self.review_regeneration_label.setText(message)
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_review_regeneration_cancelled(self) -> None:
        self.review_progress_bar.setVisible(False)
        if hasattr(self, "review_regeneration_label"):
            self.review_regeneration_label.setText(
                self.tr("generation_cancelled", "Generation cancelled")
            )
        self.log_view.append_event("Segment regeneration cancelled.")

    def _clear_segment_regeneration_worker(self) -> None:
        self.segment_regeneration_worker = None
        self.segment_regeneration_thread = None
        self.segment_regeneration_uses_preloaded_engine = False
        self._refresh_review_page()

    def _play_review_candidate(self) -> None:
        if self.review_candidate_wav is None:
            return
        self._play_review_audio(str(self.review_candidate_wav))

    def _approve_review_candidate(self) -> None:
        if self.review_candidate_segment_id is None or self.review_candidate_wav is None:
            return
        segment = self.audiobook_store.get_segment(self.review_candidate_segment_id)
        if segment is None:
            return
        candidate = self.review_candidate_wav
        if not candidate.is_file():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr("review_candidate_missing", "Candidate audio file is missing."),
            )
            return
        self.review_player.stop()
        self.review_player.setSource(QUrl())
        try:
            duration_ms = round(self._wav_duration_seconds(candidate) * 1000)
            self.audiobook_store.mark_segment_rendered(
                segment.id,
                candidate,
                duration_ms,
                0,
            )
        except (OSError, wave.Error) as exc:
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                f"Could not approve candidate audio: {exc}",
            )
            return
        self.log_view.append_event(
            f"Approved regenerated audio for segment {segment.sequence_index}."
        )
        self.review_candidate_wav = None
        self.review_candidate_segment_id = None
        if self.review_regeneration_dialog is not None:
            self.review_regeneration_dialog.close()
        self._refresh_review_page()

    def _discard_review_candidate(self) -> None:
        if self.segment_regeneration_worker is not None:
            self.segment_regeneration_worker.request_cancel()
        candidate = self.review_candidate_wav
        if candidate is not None and candidate.is_file():
            try:
                candidate.unlink()
            except OSError as exc:
                self.log_view.append_event(f"Could not delete candidate audio: {exc}")
        self.review_candidate_wav = None
        self.review_candidate_segment_id = None
        if self.review_regeneration_dialog is not None:
            self.review_regeneration_dialog.close()

    def _on_review_regeneration_dialog_finished(self) -> None:
        if self.segment_regeneration_worker is not None:
            self.segment_regeneration_worker.request_cancel()
        candidate = self.review_candidate_wav
        if candidate is not None and candidate.is_file():
            try:
                candidate.unlink()
            except OSError as exc:
                self.log_view.append_event(f"Could not delete candidate audio: {exc}")
        self.review_candidate_wav = None
        self.review_candidate_segment_id = None
        self.review_regeneration_dialog = None
        self._refresh_review_page()

    @staticmethod
    def _wav_duration_seconds(path: Path) -> float:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()

    def _start_review_rebuild(self) -> None:
        if self.audiobook_rebuild_thread is not None:
            return
        audiobook = self._current_audiobook()
        if audiobook is None:
            return
        options = self._review_rebuild_options(audiobook)
        self.review_progress_bar.setVisible(True)
        self.review_progress_bar.setRange(0, 100)
        self.review_progress_bar.setValue(0)
        self.review_status_label.setText(
            self.tr("review_rebuilding", "Rebuilding audiobook from segments...")
        )
        self._show_review_rebuild_dialog()
        thread = QThread(self)
        worker = AudiobookRebuildWorker(
            AudiobookStore(),
            audiobook.id,
            options.output_dir,
            options.ffmpeg_path,
            options,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_review_rebuild_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_review_rebuild_finished)
        worker.failed.connect(self._on_review_rebuild_failed)
        worker.cancelled.connect(self._on_review_rebuild_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_audiobook_rebuild_worker)
        self.audiobook_rebuild_worker = worker
        self.audiobook_rebuild_thread = thread
        self._refresh_review_page()
        thread.start()

    def _show_review_rebuild_dialog(self) -> None:
        if self.review_rebuild_dialog is not None:
            self.review_rebuild_dialog.close()
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr("review_rebuild_progress_title", "Rebuilding audiobook")
        )
        dialog.setModal(True)
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            self.tr("review_rebuilding", "Rebuilding audiobook from segments...")
        )
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(label)
        layout.addWidget(progress)
        dialog.finished.connect(self._clear_review_rebuild_dialog)
        self.review_rebuild_dialog = dialog
        self.review_rebuild_dialog_label = label
        self.review_rebuild_dialog_progress = progress
        dialog.show()

    def _clear_review_rebuild_dialog(self, _result: int = 0) -> None:
        self.review_rebuild_dialog = None
        self.review_rebuild_dialog_label = None
        self.review_rebuild_dialog_progress = None

    def _close_review_rebuild_dialog(
        self,
        message: str | None = None,
        delay_ms: int = 700,
    ) -> None:
        dialog = self.review_rebuild_dialog
        if dialog is None:
            return
        if message and self.review_rebuild_dialog_label is not None:
            self.review_rebuild_dialog_label.setText(message)
        if self.review_rebuild_dialog_progress is not None:
            self.review_rebuild_dialog_progress.setRange(0, 100)
            self.review_rebuild_dialog_progress.setValue(100)
        if delay_ms <= 0:
            dialog.accept()
            return
        QTimer.singleShot(delay_ms, dialog.accept)

    def _review_rebuild_options(self, audiobook) -> AudioGenerationOptions:
        output_dir = audiobook.output_dir
        if not str(output_dir) or str(output_dir) == ".":
            output_dir = self.output_picker.path()
        if not output_dir.is_absolute():
            output_dir = resolve_app_path(output_dir)
        voice_config = {
            "engine": str(
                self.settings.get(
                    "tts_engine",
                    self.tts_engine_combo.currentData() if hasattr(self, "tts_engine_combo") else "piper",
                )
                or "piper"
            )
        }
        return AudioGenerationOptions(
            output_dir=output_dir,
            voice_config=voice_config,
            ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            split_mode=str(audiobook.split_mode or self.split_combo.currentData()),
            export_mode="single",
            chunk_size=self._current_chunk_size(
                str(voice_config.get("engine", "piper"))
            ),
            pause_between_blocks_ms=int(
                self.settings.get("pause_between_blocks_ms", 350)
            ),
            pause_between_chapters_ms=int(
                self.settings.get("pause_between_chapters_ms", 900)
            ),
            paragraph_pause_min_ms=round(
                self.paragraph_pause_min_spin.value() * 1000
            ),
            paragraph_pause_max_ms=round(
                self.paragraph_pause_max_spin.value() * 1000
            ),
            adaptive_paragraph_pause=self.adaptive_pause_checkbox.isChecked(),
            paragraph_length_reference_chars=(
                self.paragraph_length_reference_spin.value()
            ),
            paragraph_length_extra_ms=round(
                self.paragraph_length_extra_spin.value() * 1000
            ),
            periodic_pause_every_paragraphs=(
                self.periodic_pause_every_spin.value()
            ),
            periodic_pause_min_ms=round(
                self.periodic_pause_min_spin.value() * 1000
            ),
            periodic_pause_max_ms=round(
                self.periodic_pause_max_spin.value() * 1000
            ),
            normalize_audio=self.normalize_checkbox.isChecked(),
            mp3_bitrate=str(self.settings.get("mp3_bitrate", "128k")),
            metadata=dict(self.settings.get("metadata", {})),
        )

    def _on_review_rebuild_finished(self, path: str) -> None:
        self.review_progress_bar.setVisible(False)
        self.last_output_folder = Path(path).parent
        self.header_open_output_button.setEnabled(
            self.header_open_output_button.isVisible()
            and self._current_output_folder() is not None
        )
        self.log_view.append_event(f"Review rebuild saved: {path}")
        self._set_audio_mix_preview_context(Path(path))
        self._close_review_rebuild_dialog(
            self.tr("review_rebuild_complete", "Audiobook rebuilt.")
        )
        self._refresh_review_page()

    def _on_review_rebuild_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        self._on_review_progress(current, total, message)
        if self.review_rebuild_dialog_label is not None:
            self.review_rebuild_dialog_label.setText(message)
        if self.review_rebuild_dialog_progress is None:
            return
        if total <= 0:
            self.review_rebuild_dialog_progress.setRange(0, 0)
            return
        self.review_rebuild_dialog_progress.setRange(0, 100)
        self.review_rebuild_dialog_progress.setValue(round(current / total * 100))

    def _on_review_rebuild_failed(self, message: str) -> None:
        self._close_review_rebuild_dialog(delay_ms=0)
        self._on_review_failed(message)

    def _on_review_rebuild_cancelled(self) -> None:
        self._close_review_rebuild_dialog(delay_ms=0)
        self._on_review_cancelled()

    def _clear_audiobook_rebuild_worker(self) -> None:
        self.audiobook_rebuild_worker = None
        self.audiobook_rebuild_thread = None
        self._refresh_review_page()

    def _refresh_whisper_status(self) -> None:
        if not hasattr(self, "whisper_status_label"):
            return
        installed = self.faster_whisper_manager.is_installed()
        runtime_ready = self.faster_whisper_manager.has_runtime()
        loaded = self.whisper_model_loaded
        status = (
            self.tr("loaded_in_memory", "Loaded in memory")
            if loaded
            else (
                self.tr("installed", "Installed")
                if installed
                else self.tr("not_installed", "Not installed")
            )
        )
        self.whisper_status_label.setText(
            self.tr(
                "whisper_status",
                "Faster Whisper status: {status}. Runtime: {runtime}. Model cache: {path}",
                status=status,
                runtime=(
                    self.tr("installed", "Installed")
                    if runtime_ready
                    else self.tr("not_installed", "Not installed")
                ),
                path=str(self.faster_whisper_manager.cache_dir),
            )
        )
        self.whisper_install_button.setEnabled(self.whisper_thread is None)
        if hasattr(self, "whisper_missing_frame"):
            self.whisper_missing_frame.setVisible(not installed)
        if hasattr(self, "whisper_missing_install_button"):
            self.whisper_missing_install_button.setEnabled(self.whisper_thread is None)
        self.whisper_remove_button.setEnabled(
            self.whisper_thread is None
            and (installed or runtime_ready or self.faster_whisper_manager.install_dir.exists())
        )
        self.whisper_load_button.setEnabled(
            installed
            and self.whisper_thread is None
            and self.whisper_preload_thread is None
        )
        self.whisper_load_button.setText(
            self.tr("unload_from_memory", "Unload from memory")
            if loaded
            else self.tr("load_into_memory", "Load into memory")
        )
        self._refresh_review_page()

    def _install_faster_whisper(self) -> None:
        self._start_faster_whisper_operation("install")

    def _remove_faster_whisper(self) -> None:
        self._unload_faster_whisper()
        self._start_faster_whisper_operation("remove")

    def _start_faster_whisper_operation(self, operation: str) -> None:
        if self.whisper_thread is not None:
            return
        self.whisper_progress_bar.setVisible(True)
        self.whisper_progress_bar.setRange(0, 0 if operation == "install" else 100)
        self.whisper_progress_bar.setValue(0)
        worker = FasterWhisperInstallWorker(FasterWhisperManager(), operation)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_whisper_progress)
        worker.finished.connect(self._on_whisper_finished)
        worker.failed.connect(self._on_whisper_failed)
        worker.cancelled.connect(self._on_whisper_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_whisper_worker)
        self.whisper_thread = thread
        self.whisper_worker = worker
        self._refresh_whisper_status()
        thread.start()

    def _on_whisper_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.whisper_progress_bar.setRange(0, 100)
            self.whisper_progress_bar.setValue(round(current / total * 100))
        self.whisper_status_label.setText(message)
        self.log_view.append_event(message)

    def _on_whisper_finished(self, message: str) -> None:
        self.whisper_progress_bar.setVisible(False)
        self.log_view.append_event(f"Faster Whisper operation complete: {message}")
        self._continue_pending_installer_setup()

    def _on_whisper_failed(self, message: str) -> None:
        self.whisper_progress_bar.setVisible(False)
        self.log_view.append_event(message)
        self._abort_pending_installer_setup(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_whisper_cancelled(self) -> None:
        self.whisper_progress_bar.setVisible(False)
        self.log_view.append_event("Faster Whisper operation cancelled.")
        self._abort_pending_installer_setup("Faster Whisper operation cancelled.")

    def _clear_whisper_worker(self) -> None:
        self.whisper_worker = None
        self.whisper_thread = None
        self.faster_whisper_manager = FasterWhisperManager()
        self._refresh_whisper_status()

    def _toggle_faster_whisper_preload(self) -> None:
        if self.whisper_model_loaded:
            self._unload_faster_whisper()
            return
        if self.whisper_preload_thread is not None:
            return
        verifier = FasterWhisperVerifier(FasterWhisperManager())
        thread = QThread(self)
        worker = FasterWhisperPreloadWorker(
            verifier,
            str(self.review_device_combo.currentData() or "cpu"),
            str(self.review_compute_combo.currentData() or "int8"),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(lambda: self._on_whisper_preloaded(verifier))
        worker.failed.connect(self._on_whisper_preload_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_whisper_preload_worker)
        self.whisper_preload_thread = thread
        self.whisper_preload_worker = worker
        self.whisper_status_label.setText("Loading Faster Whisper model...")
        thread.start()

    def _on_whisper_preloaded(self, verifier: FasterWhisperVerifier) -> None:
        self.preloaded_whisper_verifier = verifier
        self.whisper_model_loaded = True
        self.log_view.append_event("Faster Whisper model loaded in memory.")
        self._refresh_whisper_status()

    def _on_whisper_preload_failed(self, message: str) -> None:
        self.whisper_model_loaded = False
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _clear_whisper_preload_worker(self) -> None:
        self.whisper_preload_worker = None
        self.whisper_preload_thread = None
        self._refresh_whisper_status()

    def _unload_faster_whisper(self) -> None:
        if self.preloaded_whisper_verifier is not None:
            self.preloaded_whisper_verifier.close(force=True)
        self.preloaded_whisper_verifier = None
        self.whisper_model_loaded = False
        self._refresh_whisper_status()

    def _start_latest_verification(self) -> None:
        self._start_verification_for_latest(show_review=True)

    def _start_verification_for_latest(self, show_review: bool = False) -> None:
        if self.verification_thread is not None:
            return
        audiobook = self._current_audiobook()
        if audiobook is None:
            self.log_view.append_event("No audiobook project is available for review.")
            return
        if not self.faster_whisper_manager.is_installed():
            self.log_view.append_event(
                "Faster Whisper small is not installed. Install it from Settings > Review."
            )
            return
        max_retries = self.review_max_retries_spin.value()
        fallback_voice_config: dict[str, object] = {}
        if max_retries > 0:
            current_config = self._current_voice_config()
            if current_config is None:
                return
            fallback_voice_config = dict(current_config)
        if show_review:
            self._show_review_page()
        self.review_progress_bar.setVisible(True)
        self.review_progress_bar.setRange(0, 100)
        self.review_progress_bar.setValue(0)
        verifier = self.preloaded_whisper_verifier
        piper_path = resolve_app_path(
            self.piper_path_edit.text().strip() or "engines/piper/piper.exe"
        )
        worker = SegmentVerificationWorker(
            AudiobookStore(),
            audiobook.id,
            verifier,
            str(self.review_device_combo.currentData() or "cpu"),
            str(self.review_compute_combo.currentData() or "int8"),
            str(self.review_language_combo.currentData() or "auto"),
            self.review_beam_spin.value(),
            self.review_threshold_spin.value(),
            max_retries,
            piper_path,
            fallback_voice_config,
            True,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_review_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_review_finished)
        worker.failed.connect(self._on_review_failed)
        worker.cancelled.connect(self._on_review_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_verification_worker)
        self.verification_worker = worker
        self.verification_thread = thread
        thread.start()

    def _on_review_progress(self, current: int, total: int, message: str) -> None:
        percentage = round(current / total * 100) if total else 0
        self.review_progress_bar.setValue(percentage)
        self.review_status_label.setText(message)

    def _on_review_finished(self) -> None:
        self.review_progress_bar.setVisible(False)
        self.log_view.append_event("Generation review complete.")
        self._refresh_review_page()
        if self.review_after_generation_outputs:
            outputs = self.review_after_generation_outputs
            self.review_after_generation_outputs = []
            audiobook = self._current_audiobook()
            segments = (
                self.audiobook_store.list_segments(audiobook.id)
                if audiobook is not None
                else []
            )
            if (
                self._review_failed_count(segments) > 0
                or self._review_dirty_count(segments) > 0
            ):
                self._show_review_page()
            else:
                self._show_audio_mix_preview(outputs)

    def _on_review_failed(self, message: str) -> None:
        self.review_progress_bar.setVisible(False)
        self.log_view.append_event(message)
        self.review_after_generation_outputs = []
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_review_cancelled(self) -> None:
        self.review_progress_bar.setVisible(False)
        self.review_after_generation_outputs = []
        self.log_view.append_event("Generation review cancelled.")

    def _clear_verification_worker(self) -> None:
        self.verification_worker = None
        self.verification_thread = None
        self._refresh_review_page()

    def _play_review_audio(self, path_text: str) -> None:
        path = Path(path_text)
        if not path.is_file():
            return
        self.review_player.setSource(QUrl.fromLocalFile(str(path)))
        self.review_player.play()

    @staticmethod
    def _short_table_text(text: str, limit: int = 120) -> str:
        clean = " ".join(text.split())
        return clean if len(clean) <= limit else clean[: limit - 1] + "..."

    def _set_running(self, running: bool) -> None:
        self.generate_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.open_output_button.setEnabled(not running)
        self.header_open_output_button.setEnabled(
            not running
            and self.header_open_output_button.isVisible()
            and self._current_output_folder() is not None
        )
        self.text_editor.setReadOnly(running)
        for widget in (
            self.import_button,
            self.generation_voice_combo,
            self.language_combo,
            self.voice_combo,
            self.manage_voices_button,
            self.settings_button,
            self.back_button,
            self.speed_spin,
            self.split_combo,
            self.export_combo,
            self.ui_language_combo,
            self.tts_engine_combo,
            self.tts_engine_table,
            self.piper_path_edit,
            self.kokoro_python_voice_combo,
            self.kokoro_python_install_button,
            self.kokoro_python_remove_button,
            self.kokoro_python_test_button,
            self.kokoro_python_load_button,
            self.kokoro_python_cancel_button,
            self.chatterbox_model_combo,
            self.chatterbox_language_combo,
            self.chatterbox_device_combo,
            self.chatterbox_reference_picker,
            self.chatterbox_exaggeration_spin,
            self.chatterbox_cfg_spin,
            self.chatterbox_install_button,
            self.chatterbox_remove_button,
            self.chatterbox_test_button,
            self.chatterbox_load_button,
            self.chatterbox_detect_gpu_button,
            self.qwen_model_combo,
            self.qwen_language_combo,
            self.qwen_speaker_combo,
            self.qwen_device_combo,
            self.qwen_dtype_combo,
            self.qwen_instruct_edit,
            self.qwen_install_button,
            self.qwen_remove_button,
            self.qwen_test_button,
            self.qwen_load_button,
            self.qwen_detect_gpu_button,
            self.omnivoice_model_combo,
            self.omnivoice_mode_combo,
            self.omnivoice_device_combo,
            self.omnivoice_dtype_combo,
            self.omnivoice_instruct_edit,
            self.omnivoice_reference_picker,
            self.omnivoice_reference_text_edit,
            self.omnivoice_num_step_spin,
            self.omnivoice_engine_speed_spin,
            self.omnivoice_duration_spin,
            self.omnivoice_install_button,
            self.omnivoice_remove_button,
            self.omnivoice_test_button,
            self.omnivoice_load_button,
            self.omnivoice_detect_gpu_button,
            self.review_enabled_checkbox,
            self.review_auto_checkbox,
            self.review_filter_combo,
            self.review_rebuild_button,
            self.review_model_combo,
            self.review_device_combo,
            self.review_compute_combo,
            self.review_language_combo,
            self.review_beam_spin,
            self.review_threshold_spin,
            self.review_max_retries_spin,
            self.whisper_install_button,
            self.whisper_remove_button,
            self.whisper_load_button,
            self.openai_api_key_edit,
            self.openai_model_combo,
            self.openai_voice_combo,
            self.openai_instructions_edit,
            self.elevenlabs_api_key_edit,
            self.elevenlabs_voice_id_edit,
            self.elevenlabs_model_combo,
            self.elevenlabs_output_combo,
            self.elevenlabs_stability_spin,
            self.elevenlabs_similarity_spin,
            self.elevenlabs_style_spin,
            self.elevenlabs_speaker_boost_checkbox,
            self.gemini_api_key_edit,
            self.gemini_model_combo,
            self.gemini_voice_combo,
            self.gemini_prompt_edit,
            self.azure_api_key_edit,
            self.azure_region_edit,
            self.azure_voice_edit,
            self.azure_output_combo,
            self.azure_style_edit,
            self.paragraph_pause_min_spin,
            self.paragraph_pause_max_spin,
            self.adaptive_pause_checkbox,
            self.paragraph_length_reference_spin,
            self.paragraph_length_extra_spin,
            self.periodic_pause_every_spin,
            self.periodic_pause_min_spin,
            self.periodic_pause_max_spin,
            self.chunk_size_spin,
            self.piper_chunk_size_spin,
            self.kokoro_chunk_size_spin,
            self.chatterbox_chunk_size_spin,
            self.qwen_chunk_size_spin,
            self.omnivoice_chunk_size_spin,
            self.output_picker,
            self.normalize_checkbox,
            self.auto_delete_segment_wavs_checkbox,
            self.cleanup_current_wavs_button,
            self.cleanup_all_wavs_button,
            self.cleanup_temp_audio_button,
            self.editor_highlighting_checkbox,
            self.markup_toolbar_checkbox,
            self.markup_corrector_checkbox,
            self.podcast_enabled_checkbox,
            self.background_enabled_checkbox,
            self.background_picker,
            self.background_loop_checkbox,
            self.voice_volume_db_spin,
            self.background_volume_spin,
            self.voice_start_offset_spin,
            self.music_tail_spin,
            self.fade_in_spin,
            self.fade_out_spin,
            self.podcast_gap_spin,
            self.podcast_normalize_checkbox,
            self.podcast_ducking_checkbox,
            self.ducking_strength_combo,
            self.open_folder_checkbox,
            self.markup_toolbar,
        ):
            widget.setEnabled(not running)
        if not running:
            self._update_voice_panel_for_engine()
            self._refresh_generation_voice_combo()
            self._refresh_kokoro_python_status()
            self._refresh_chatterbox_status()
            self._refresh_qwen_status()
            self._refresh_omnivoice_status()
            self._refresh_wav_cache_stats()

    def _save_settings(self) -> None:
        output_dir = self.output_picker.path()
        self.settings.pop("kokoro_python", None)
        self.settings.update(
            {
                "output_dir": str(output_dir),
                "ui_language": self.ui_language_combo.currentData() or "en",
                "tts_engine": self.tts_engine_combo.currentData() or "piper",
                "piper_path": self.piper_path_edit.text().strip()
                or "engines/piper/piper.exe",
                "voice_id": self.voice_combo.currentData() or "",
                "language": self.language_combo.currentData() or "",
                "speed": self.speed_spin.value(),
                "paragraph_pause_min_ms": round(
                    self.paragraph_pause_min_spin.value() * 1000
                ),
                "paragraph_pause_max_ms": round(
                    self.paragraph_pause_max_spin.value() * 1000
                ),
                "adaptive_paragraph_pause": (
                    self.adaptive_pause_checkbox.isChecked()
                ),
                "paragraph_length_reference_chars": (
                    self.paragraph_length_reference_spin.value()
                ),
                "paragraph_length_extra_ms": round(
                    self.paragraph_length_extra_spin.value() * 1000
                ),
                "periodic_pause_every_paragraphs": (
                    self.periodic_pause_every_spin.value()
                ),
                "periodic_pause_min_ms": round(
                    self.periodic_pause_min_spin.value() * 1000
                ),
                "periodic_pause_max_ms": round(
                    self.periodic_pause_max_spin.value() * 1000
                ),
                "split_mode": self.split_combo.currentData(),
                "export_mode": self.export_combo.currentData(),
                "chunk_size": self.chunk_size_spin.value(),
                "engine_chunk_sizes": {
                    "piper": self.piper_chunk_size_spin.value(),
                    "kokoro": self.kokoro_chunk_size_spin.value(),
                    "chatterbox": self.chatterbox_chunk_size_spin.value(),
                    "qwen": self.qwen_chunk_size_spin.value(),
                    "omnivoice": self.omnivoice_chunk_size_spin.value(),
                },
                "normalize_audio": self.normalize_checkbox.isChecked(),
                "auto_delete_segment_wavs_after_mix": (
                    self.auto_delete_segment_wavs_checkbox.isChecked()
                ),
                "editor_syntax_highlighting": (
                    self.editor_highlighting_checkbox.isChecked()
                ),
                "show_markup_toolbar": (
                    self.markup_toolbar_checkbox.isChecked()
                    if hasattr(self, "markup_toolbar_checkbox")
                    else bool(self.settings.get("show_markup_toolbar", True))
                ),
                "markup_corrector_enabled": (
                    self.markup_corrector_checkbox.isChecked()
                    if hasattr(self, "markup_corrector_checkbox")
                    else bool(self.settings.get("markup_corrector_enabled", True))
                ),
                "podcast_enabled": self.podcast_enabled_checkbox.isChecked(),
                "background_enabled": (
                    self.background_enabled_checkbox.isChecked()
                ),
                "background_path": str(self.background_picker.path() or ""),
                "background_loop": self.background_loop_checkbox.isChecked(),
                "background_volume_percent": (
                    self._db_to_percent(self.background_volume_spin.value())
                ),
                "voice_volume_db": self.voice_volume_db_spin.value(),
                "music_volume_db": self.background_volume_spin.value(),
                "voice_start_offset_ms": self.voice_start_offset_spin.value(),
                "music_tail_ms": self.music_tail_spin.value(),
                "music_fade_in_seconds": self.fade_in_spin.value(),
                "music_fade_out_seconds": self.fade_out_spin.value(),
                "podcast_gap_ms": round(self.podcast_gap_spin.value() * 1000),
                "podcast_normalize": (
                    self.podcast_normalize_checkbox.isChecked()
                ),
                "podcast_ducking": self.podcast_ducking_checkbox.isChecked(),
                "ducking_strength": (
                    self.ducking_strength_combo.currentData() or "low"
                ),
                "open_output_on_finish": self.open_folder_checkbox.isChecked(),
                "kokoro": {
                    "voice": (
                        self.kokoro_python_voice_combo.currentData() or "af_heart"
                    ),
                    "lang": self._kokoro_python_language_for_voice(
                        str(
                            self.kokoro_python_voice_combo.currentData()
                            or "af_heart"
                        )
                    ),
                    "provider": "auto",
                },
                "chatterbox": {
                    "model": (
                        self.chatterbox_model_combo.currentData()
                        or "multilingual_v3"
                    ),
                    "language": self.chatterbox_language_combo.currentData() or "en",
                    "device": self.chatterbox_device_combo.currentData() or "auto",
                    "reference_audio_path": str(
                        self.chatterbox_reference_picker.path() or ""
                    ),
                    "exaggeration": self.chatterbox_exaggeration_spin.value(),
                    "cfg_weight": self.chatterbox_cfg_spin.value(),
                },
                "qwen": {
                    "model": (
                        self.qwen_model_combo.currentData()
                        or "custom_voice_0_6b"
                    ),
                    "language": self.qwen_language_combo.currentData() or "Spanish",
                    "speaker": self.qwen_speaker_combo.currentData() or "Serena",
                    "device": self.qwen_device_combo.currentData() or "auto",
                    "dtype": self.qwen_dtype_combo.currentData() or "auto",
                    "instruct": self.qwen_instruct_edit.text().strip(),
                },
                "omnivoice": {
                    "model": (
                        self.omnivoice_model_combo.currentData()
                        or "omnivoice"
                    ),
                    "mode": "clone",
                    "language": self.omnivoice_language_combo.currentData() or "auto",
                    "device": self.omnivoice_device_combo.currentData() or "auto",
                    "dtype": self.omnivoice_dtype_combo.currentData() or "auto",
                    "instruct": "",
                    "reference_audio_path": str(
                        self.omnivoice_reference_picker.path() or ""
                    ),
                    "reference_text": (
                        self.omnivoice_reference_text_edit.toPlainText().strip()
                    ),
                    "num_step": self.omnivoice_num_step_spin.value(),
                    "speed": self.omnivoice_engine_speed_spin.value(),
                    "duration": self.omnivoice_duration_spin.value(),
                },
                "review": {
                    "enabled": self.review_enabled_checkbox.isChecked(),
                    "auto_verify_after_generation": (
                        self.review_auto_checkbox.isChecked()
                    ),
                    "model": self.review_model_combo.currentData() or "small",
                    "device": self.review_device_combo.currentData() or "cpu",
                    "compute_type": (
                        self.review_compute_combo.currentData() or "int8"
                    ),
                    "language": (
                        self.review_language_combo.currentData() or "auto"
                    ),
                    "beam_size": self.review_beam_spin.value(),
                    "approve_threshold": self.review_threshold_spin.value(),
                    "max_retries": self.review_max_retries_spin.value(),
                    "preload_model": self.whisper_model_loaded,
                },
                "local_server": self._local_server_settings_from_ui(),
                "voice_gallery": {
                    "catalog_url": str(
                        self.settings.get("voice_gallery", {}).get(
                            "catalog_url",
                            "https://raw.githubusercontent.com/estebanstifli/LocalText2Voice-VoiceGallery/main/catalog.json",
                        )
                    ),
                    "local_catalog_path": str(
                        self.settings.get("voice_gallery", {}).get(
                            "local_catalog_path",
                            "",
                        )
                    ),
                    "auto_sync": bool(
                        self.settings.get("voice_gallery", {}).get("auto_sync", False)
                    ),
                    "last_sync_at": str(
                        self.settings.get("voice_gallery", {}).get(
                            "last_sync_at",
                            "",
                        )
                    ),
                },
                "api_tts": {
                    "openai": {
                        "api_key": self.openai_api_key_edit.text().strip(),
                        "model": self.openai_model_combo.currentData(),
                        "voice": self.openai_voice_combo.currentData(),
                        "instructions": (
                            self.openai_instructions_edit.text().strip()
                        ),
                        "timeout_seconds": 120,
                    },
                    "elevenlabs": {
                        "api_key": self.elevenlabs_api_key_edit.text().strip(),
                        "voice_id": (
                            self.elevenlabs_voice_id_edit.text().strip()
                        ),
                        "model_id": self.elevenlabs_model_combo.currentData(),
                        "output_format": (
                            self.elevenlabs_output_combo.currentData()
                        ),
                        "stability": self.elevenlabs_stability_spin.value(),
                        "similarity_boost": (
                            self.elevenlabs_similarity_spin.value()
                        ),
                        "style": self.elevenlabs_style_spin.value(),
                        "use_speaker_boost": (
                            self.elevenlabs_speaker_boost_checkbox.isChecked()
                        ),
                        "timeout_seconds": 120,
                    },
                    "gemini": {
                        "api_key": self.gemini_api_key_edit.text().strip(),
                        "model": self.gemini_model_combo.currentData(),
                        "voice": self.gemini_voice_combo.currentData(),
                        "prompt": self.gemini_prompt_edit.text().strip(),
                        "timeout_seconds": 180,
                    },
                    "azure": {
                        "api_key": self.azure_api_key_edit.text().strip(),
                        "region": self.azure_region_edit.text().strip(),
                        "voice": self.azure_voice_edit.text().strip(),
                        "output_format": self.azure_output_combo.currentData(),
                        "style": self.azure_style_edit.text().strip(),
                        "timeout_seconds": 120,
                    },
                },
                "custom_tts_engines": self._custom_tts_engines(),
            }
        )
        try:
            self.settings_manager.save(self.settings)
        except OSError as exc:
            self.log_view.append_event(f"Could not save config.json: {exc}")

    @staticmethod
    def _resolved_audio_path(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path if path.is_absolute() else resolve_app_path(path)

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} GB"

    @staticmethod
    def _percent_to_db(percent: int) -> float:
        if percent <= 0:
            return -36.0
        return max(-36.0, min(0.0, 20 * math.log10(percent / 100)))

    @staticmethod
    def _db_to_percent(db_value: float) -> int:
        return max(0, min(100, round((10 ** (db_value / 20)) * 100)))

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_project_switch():
            event.ignore()
            return
        if self.worker is not None:
            choice = QMessageBox.question(
                self,
                self.tr("generation_running", "Generation is running"),
                self.tr(
                    "close_running_message",
                    "Cancel the current generation and close the application?",
                ),
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_cancel()
            if self.worker_thread is not None:
                self.worker_thread.quit()
                if not self.worker_thread.wait(5000):
                    QMessageBox.warning(
                        self,
                        self.tr("still_stopping", "Still stopping"),
                        self.tr(
                            "still_stopping_message",
                            "The audio process is still stopping. Please wait a moment "
                            "and close the application again.",
                        ),
                    )
                    event.ignore()
                    return
        if self.preload_worker is not None:
            self.preload_worker.request_cancel()
            if self.preload_thread is not None:
                self.preload_thread.quit()
                self.preload_thread.wait(3000)
        if self.verification_worker is not None:
            self.verification_worker.request_cancel()
            if self.verification_thread is not None:
                self.verification_thread.quit()
                self.verification_thread.wait(3000)
        if self.segment_regeneration_worker is not None:
            self.segment_regeneration_worker.request_cancel()
            if self.segment_regeneration_thread is not None:
                self.segment_regeneration_thread.quit()
                self.segment_regeneration_thread.wait(3000)
        if self.audiobook_rebuild_worker is not None:
            self.audiobook_rebuild_worker.request_cancel()
            if self.audiobook_rebuild_thread is not None:
                self.audiobook_rebuild_thread.quit()
                self.audiobook_rebuild_thread.wait(3000)
        if self.whisper_worker is not None:
            self.whisper_worker.request_cancel()
            if self.whisper_thread is not None:
                self.whisper_thread.quit()
                self.whisper_thread.wait(3000)
        if self.whisper_preload_thread is not None:
            if self.whisper_preload_worker is not None:
                self.whisper_preload_worker.verifier.cancel_current()
            self._unload_faster_whisper()
            self.whisper_preload_thread.quit()
            self.whisper_preload_thread.wait(3000)
        else:
            self._unload_faster_whisper()
        self._unload_preloaded_tts_engine(log_message=False)
        self.local_server_controller.stop()
        self._save_settings()
        event.accept()

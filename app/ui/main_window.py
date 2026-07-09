from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import json
import re
import shutil
import sys
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path

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
    QVBoxLayout,
    QWidget,
)

from app.core.audio_pipeline import AudioGenerationOptions
from app.core.audio_mix import AudioMixSettings
from app.core.audiobook_store import AudiobookStore, StoredSegment
from app.core.project_manager import DocumentImportError, ProjectManager
from app.core.settings_manager import SettingsManager
from app.tts.base import BaseTTSEngine, TTSEngineError
from app.tts.engine_registry import TTS_ENGINES
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.chatterbox_voice_manager import (
    ChatterboxReferenceVoice,
    ChatterboxReferenceVoiceManager,
)
from app.tts.kokoro_preview import kokoro_preview_text_for_language
from app.tts.kokoro_python_manager import KokoroPythonManager
from app.tts.qwen_manager import QwenManager
from app.tts.piper_engine import PiperTTSEngine
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
from app.workers.generation_worker import GenerationWorker
from app.workers.kokoro_worker import KokoroInstallWorker, KokoroPreviewWorker
from app.workers.preload_worker import TTSEnginePreloadWorker
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
            line_half = 7
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
            line_half = 8
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._window_shadow_margin = 5
        self._resize_border_px = 8
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.settings
        self.translator = Translator(str(self.settings.get("ui_language", "en")))
        self.kokoro_python_manager = KokoroPythonManager()
        self.chatterbox_manager = ChatterboxManager()
        self.chatterbox_reference_voice_manager = ChatterboxReferenceVoiceManager()
        self.qwen_manager = QwenManager()
        self.audiobook_store = AudiobookStore()
        self.faster_whisper_manager = FasterWhisperManager()
        self.gpu_detection_result = detect_gpus()
        self.current_audiobook_id = self._stored_project_id()
        self.project_dirty = False
        self._loading_project = False
        self.voices: list[VoiceInfo] = []
        self.voice_page_rows: list[dict[str, object]] = []
        self.worker: GenerationWorker | None = None
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
        self.qwen_worker: QwenInstallWorker | None = None
        self.qwen_thread: QThread | None = None
        self.qwen_preview_worker: QwenPreviewWorker | None = None
        self.qwen_preview_thread: QThread | None = None
        self.qwen_hardware_worker: QwenHardwareWorker | None = None
        self.qwen_hardware_thread: QThread | None = None
        self.preload_worker: TTSEnginePreloadWorker | None = None
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
        self.preloading_tts_engine_id: str | None = None
        self.loaded_tts_engine_id: str | None = None
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
            ("audio_mix_preview", "Audio Mix Preview", self._show_mix_preview_page),
        ):
            action = QAction(self.tr(label_key, default), self)
            action.triggered.connect(callback)
            view_menu.addAction(action)

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
                self.tr("audio_mix_preview", "Audio Mix Preview"),
                self._show_mix_preview_page,
            ),
            ("export", "export", self.tr("nav_export", "Export"), self._show_export_page),
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
        layout.addWidget(self.text_editor, 1)
        return frame

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
        self.voices_manage_button = QPushButton(self.tr("manage", "Manage"))
        self.voices_manage_button.setIcon(ui_icon("settings"))
        self.voices_manage_button.setIconSize(QSize(18, 18))
        self.voices_manage_button.clicked.connect(self._voices_primary_manage_action)
        header.addWidget(self.voices_refresh_button)
        header.addWidget(self.voices_manage_button)
        card_layout.addLayout(header)

        self.voices_table = QTableWidget(0, 6)
        self.voices_table.setHorizontalHeaderLabels(
            [
                self.tr("selected", "Selected"),
                self.tr("voice", "Voice"),
                self.tr("language", "Language"),
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
        voices_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        voices_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
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

    def _show_mix_preview_page(self) -> None:
        self._ensure_audio_mix_preview_context()
        self._show_page(
            4,
            "mix",
            self.tr("audio_mix_preview", "Audio Mix Preview"),
            self.tr(
                "audio_mix_preview_subtitle",
                "Preview how your voice narration and background music blend together.",
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

    def _show_export_page(self) -> None:
        self._show_settings_page()
        self.settings_tabs.setCurrentIndex(0)
        self._set_active_nav("export")

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
        self.header_open_output_button.setEnabled(self.last_output_folder is not None)

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
        for engine in TTS_ENGINES:
            self.tts_engine_combo.addItem(
                ui_icon("voice"),
                self._tts_engine_label(engine.engine_id),
                engine.engine_id,
            )
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
            ("openai", self._build_openai_engine_panel()),
            ("elevenlabs", self._build_elevenlabs_engine_panel()),
            ("gemini", self._build_gemini_engine_panel()),
            ("azure", self._build_azure_engine_panel()),
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
        layout.addWidget(self.chatterbox_status_label)
        layout.addWidget(self.chatterbox_path_label)
        layout.addWidget(self.chatterbox_runtime_label)

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
        layout.addWidget(hardware_frame)

        self.chatterbox_progress_bar = QProgressBar()
        self.chatterbox_progress_bar.setRange(0, 100)
        self.chatterbox_progress_bar.setValue(0)
        self.chatterbox_progress_bar.setVisible(False)
        layout.addWidget(self.chatterbox_progress_bar)

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
        self.chatterbox_consent_checkbox = QCheckBox(
            self.tr(
                "voice_clone_consent",
                "I have permission to use this reference voice.",
            )
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
        form.addRow("", self.chatterbox_consent_checkbox)
        form.addRow(
            self.tr("exaggeration", "Emotion exaggeration"),
            self.chatterbox_exaggeration_spin,
        )
        form.addRow(self.tr("cfg_weight", "CFG weight"), self.chatterbox_cfg_spin)
        layout.addLayout(form)

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
        layout.addLayout(actions)

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
        layout.addWidget(self.chatterbox_preview_frame)

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
        layout.addWidget(helper)
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
        if reference_path is not None and not self.chatterbox_consent_checkbox.isChecked():
            self._show_error(
                self.tr("generation_failed", "Generation failed"),
                self.tr(
                    "voice_clone_consent_required",
                    "Confirm that you have permission to use the reference voice.",
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
        layout.addWidget(self.qwen_status_label)
        layout.addWidget(self.qwen_path_label)
        layout.addWidget(self.qwen_runtime_label)

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
        layout.addWidget(hardware_frame)

        self.qwen_progress_bar = QProgressBar()
        self.qwen_progress_bar.setRange(0, 100)
        self.qwen_progress_bar.setValue(0)
        self.qwen_progress_bar.setVisible(False)
        layout.addWidget(self.qwen_progress_bar)

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
        layout.addLayout(form)

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
        layout.addLayout(actions)

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
        layout.addWidget(self.qwen_preview_frame)

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
        layout.addWidget(helper)
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

    def _on_tts_engine_changed(self) -> None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        index = self.engine_stack_indexes.get(engine_id, 0)
        self.engine_settings_stack.setCurrentIndex(index)
        self._update_voice_panel_for_engine()
        self._refresh_tts_engine_table()
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
            elif self.preloaded_tts_engine_id == engine_id:
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
        for row in rows:
            engine_id = row["engine_id"]
            if engine_id != current_engine:
                row["selected"] = ""
            elif self.preloading_tts_engine_id == engine_id:
                row["selected"] = self.tr("selected_loading", "Selected / loading")
            elif self.preloaded_tts_engine_id == engine_id:
                row["selected"] = self.tr("selected_loaded", "Selected / loaded")
            elif engine_id in {"chatterbox", "qwen"}:
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

    def _configure_preload_button(
        self,
        button: QPushButton,
        engine_id: str,
        can_load: bool,
    ) -> None:
        loaded = (
            self.preloaded_tts_engine is not None
            and self.preloaded_tts_engine_id == engine_id
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
        if (
            self.preloaded_tts_engine is not None
            and self.preloaded_tts_engine_id == engine_id
        ):
            self._unload_preloaded_tts_engine()
            return
        self._start_preload_tts_engine(engine_id)

    def _start_preload_tts_engine(self, engine_id: str) -> None:
        if self.preload_thread is not None:
            return
        if engine_id not in {"kokoro", "chatterbox", "qwen"}:
            return
        self._select_tts_engine(engine_id)
        voice_config = self._current_voice_config()
        if voice_config is None:
            return
        self._unload_preloaded_tts_engine(log_message=False)
        piper_path = resolve_app_path(
            self.piper_path_edit.text().strip() or "engines/piper/piper.exe"
        )
        self.preloading_tts_engine_id = engine_id
        self.log_view.append_event(
            self.tr(
                "preloading_engine",
                "Loading {engine} into memory...",
                engine=self._tts_engine_label(engine_id),
            )
        )
        self._refresh_all_engine_status()

        thread = QThread(self)
        worker = TTSEnginePreloadWorker(engine_id, piper_path, voice_config)
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
        engine: BaseTTSEngine,
    ) -> None:
        self.preloaded_tts_engine = engine
        self.preloaded_tts_engine_id = engine_id
        self.loaded_tts_engine_id = engine_id
        self.log_view.append_event(
            self.tr(
                "engine_loaded_in_memory",
                "{engine} loaded in memory and waiting for requests.",
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

    def _build_general_settings(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        narration_group = QGroupBox(
            self.tr("narration_settings", "Narration and export")
        )
        left_form = QFormLayout(narration_group)
        left_form.setSpacing(10)
        output_group = QGroupBox(
            self.tr("output_and_podcast", "Output and podcast")
        )
        right_form = QFormLayout(output_group)
        right_form.setSpacing(10)

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

        left_form.addRow(self.tr("voice_speed", "Voice speed"), self.speed_spin)
        left_form.addRow(self.tr("split_mode", "Text splitting"), self.split_combo)
        left_form.addRow(self.tr("export_mode", "Output type"), self.export_combo)
        left_form.addRow("", self.editor_highlighting_checkbox)

        output_path = str(resolve_app_path(self.settings.get("output_dir", "output")))
        self.output_picker = PathPicker(
            self.tr("browse", "Browse"),
            output_path,
        )
        self.normalize_checkbox = QCheckBox(
            self.tr("normalize_clean_audio", "Normalize clean narration")
        )
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

        right_form.addRow(
            self.tr("output_folder", "Output folder"),
            self.output_picker,
        )
        right_form.addRow("", self.normalize_checkbox)

        grid.addWidget(narration_group, 0, 0)
        grid.addWidget(output_group, 0, 1)
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
        self.intro_enabled_checkbox = QCheckBox(
            self.tr("enable_intro", "Enable intro")
        )
        self.intro_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
        self.outro_enabled_checkbox = QCheckBox(
            self.tr("enable_outro", "Enable outro")
        )
        self.outro_picker = FilePicker(
            self.tr("browse", "Browse"),
            self.tr("audio_files", "Audio files (*.mp3 *.wav)"),
        )
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
        podcast_form.addRow(self.intro_enabled_checkbox, self.intro_picker)
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
        podcast_form.addRow(self.outro_enabled_checkbox, self.outro_picker)
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

        grid.addWidget(pause_group, 0, 0)
        grid.addWidget(podcast_group, 0, 1)
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
        if engine_id == "chatterbox":
            return [row for row in rows if bool(row.get("installed"))]
        return rows

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
        self.voices_manage_button.setEnabled(engine_id in {"piper", "chatterbox"})
        self.voice_page_rows = self._voice_page_rows(engine_id)
        self.voices_table.setSortingEnabled(False)
        self.voices_table.setRowCount(0)
        for row_index, row in enumerate(self.voice_page_rows):
            self.voices_table.insertRow(row_index)
            selected = bool(row.get("selected"))
            values = (
                self.tr("selected", "Selected") if selected else "",
                str(row.get("name", "")),
                str(row.get("language", "")),
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
                elif column in {2, 3, 4}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.voices_table.setItem(row_index, column, item)
            self.voices_table.setCellWidget(
                row_index,
                5,
                self._voice_actions_widget(row),
            )
            self.voices_table.setRowHeight(row_index, 42)
        self.voices_status_label.setText(self._voices_status_text(engine_id))
        self.voices_table.setSortingEnabled(True)
        self.voices_table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.voices_table.resizeRowsToContents()
        self._refresh_generation_voice_combo()

    def _voices_manage_button_text(self, engine_id: str) -> str:
        if engine_id == "piper":
            return self.tr("download_piper_voices", "Download Piper voices")
        if engine_id == "chatterbox":
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
        if engine_id == "chatterbox":
            return self.tr(
                "chatterbox_voices_page_help",
                "Chatterbox uses short reference audio clips. You can import your own WAV/MP3 files or install the available remote examples row by row.",
            )
        return self.tr(
            "api_voices_page_help",
            "API voices are configured locally and used only when that provider is selected.",
        )

    def _voice_page_rows(self, engine_id: str) -> list[dict[str, object]]:
        if engine_id == "piper":
            selected_id = self.voice_combo.currentData()
            return [
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
        if engine_id == "kokoro":
            selected_id = self.kokoro_python_voice_combo.currentData()
            status = (
                self.tr("installed", "Installed")
                if self.kokoro_python_manager.is_installed()
                else self.tr("not_installed", "Not installed")
            )
            return [
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
            return rows
        if engine_id == "chatterbox":
            return self._chatterbox_voice_page_rows()
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
        return []

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

        if engine_id in {"piper", "kokoro", "qwen", "chatterbox"}:
            test_button = self._small_icon_button(
                "preview",
                self.tr("test_voice", "Test voice"),
                lambda _checked=False, voice_row=row: self._test_voice_page_row_data(
                    voice_row
                ),
            )
            test_button.setEnabled(
                engine_id != "chatterbox" or bool(row.get("installed"))
            )
            layout.addWidget(test_button)
        if engine_id == "chatterbox":
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
        if engine_id == "piper":
            voice = row.get("voice")
            if isinstance(voice, VoiceInfo):
                self._select_combo_data(self.language_combo, voice.language)
                self._filter_voices()
                self._select_combo_data(self.voice_combo, voice.voice_id)
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
        elif engine_id == "chatterbox":
            if not bool(row.get("installed")):
                self._install_chatterbox_reference_voice_data(row)
                return
            path = row.get("path")
            if isinstance(path, Path):
                self.chatterbox_reference_picker.set_path(path)
                self.chatterbox_consent_checkbox.setChecked(True)
        elif engine_id == "openai":
            self._select_combo_data(self.openai_voice_combo, voice_id)
        elif engine_id == "gemini":
            self._select_combo_data(self.gemini_voice_combo, voice_id)
        self._save_settings()
        if refresh:
            self._refresh_voices_page()
        else:
            self._refresh_generation_voice_combo()

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
        voice = row.get("voice")
        path = row.get("path")
        if isinstance(path, Path) and path.is_file():
            self.voices_player.setSource(QUrl.fromLocalFile(str(path)))
            self.voices_player.play()
            return
        if isinstance(voice, ChatterboxReferenceVoice) and voice.source_url:
            self.voices_player.setSource(QUrl(voice.source_url))
            self.voices_player.play()

    def _voices_primary_manage_action(self) -> None:
        engine_id = str(self.tts_engine_combo.currentData() or "piper")
        if engine_id == "piper":
            self._open_voice_manager()
            self._refresh_voices_page()
            return
        if engine_id == "chatterbox":
            self._show_chatterbox_voice_manage_menu()

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
        self.chatterbox_consent_checkbox.setChecked(
            bool(chatterbox.get("voice_clone_consent", False))
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
        self._select_combo_data(
            self.export_combo,
            self.settings.get("export_mode", "single"),
        )
        self.normalize_checkbox.setChecked(
            bool(self.settings.get("normalize_audio", False))
        )
        self.editor_highlighting_checkbox.setChecked(
            bool(self.settings.get("editor_syntax_highlighting", True))
        )
        self._set_editor_highlighting_enabled(
            self.editor_highlighting_checkbox.isChecked()
        )
        self.podcast_enabled_checkbox.setChecked(
            bool(self.settings.get("podcast_enabled", False))
        )
        self.intro_enabled_checkbox.setChecked(
            bool(self.settings.get("intro_enabled", False))
        )
        self.intro_picker.set_path(self.settings.get("intro_path", ""))
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
        self.outro_enabled_checkbox.setChecked(
            bool(self.settings.get("outro_enabled", False))
        )
        self.outro_picker.set_path(self.settings.get("outro_path", ""))
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
        options = AudioGenerationOptions(
            output_dir=output_dir,
            voice_config=voice_config,
            ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            split_mode=str(self.split_combo.currentData()),
            export_mode=str(self.export_combo.currentData()),
            chunk_size=int(self.settings.get("chunk_size", 2500)),
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
            podcast_enabled=False,
            intro_enabled=self.intro_enabled_checkbox.isChecked(),
            intro_path=self._resolved_audio_path(self.intro_picker.path()),
            background_enabled=bool(self.settings.get("background_enabled", True)),
            background_path=self._selected_music_path(),
            background_loop=self.background_loop_checkbox.isChecked(),
            background_volume_percent=self._db_to_percent(
                self.background_volume_spin.value()
            ),
            voice_volume_db=self.voice_volume_db_spin.value(),
            music_volume_db=self.background_volume_spin.value(),
            voice_start_offset_ms=self.voice_start_offset_spin.value(),
            music_tail_ms=self.music_tail_spin.value(),
            outro_enabled=self.outro_enabled_checkbox.isChecked(),
            outro_path=self._resolved_audio_path(self.outro_picker.path()),
            music_fade_in_seconds=self.fade_in_spin.value(),
            music_fade_out_seconds=self.fade_out_spin.value(),
            podcast_gap_ms=round(self.podcast_gap_spin.value() * 1000),
            podcast_normalize=self.podcast_normalize_checkbox.isChecked(),
            podcast_ducking=self.podcast_ducking_checkbox.isChecked(),
            ducking_strength=str(
                self.ducking_strength_combo.currentData() or "low"
            ),
            mp3_bitrate=str(self.settings.get("mp3_bitrate", "128k")),
            metadata=dict(self.settings.get("metadata", {})),
            project_audiobook_id=self.current_audiobook_id,
            project_settings=json.loads(json.dumps(self.settings)),
        )
        piper_path = resolve_app_path(
            self.piper_path_edit.text().strip() or "engines/piper/piper.exe"
        )
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
        preloaded_engine = (
            self.preloaded_tts_engine
            if self.preloaded_tts_engine_id == engine_id
            else None
        )
        self.worker_uses_preloaded_engine = preloaded_engine is not None
        if preloaded_engine is not None:
            self.log_view.append_event(
                self.tr(
                    "using_preloaded_engine",
                    "Using preloaded {engine} engine.",
                    engine=self._tts_engine_label(engine_id),
                )
            )
        self.loaded_tts_engine_id = (
            engine_id
            if engine_id in {"kokoro", "chatterbox", "qwen"}
            else self.preloaded_tts_engine_id
        )
        self._update_header_engine_label()
        self._set_running(True)

        thread = QThread(self)
        worker = GenerationWorker(text, options, piper_path, preloaded_engine)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.log.connect(self.log_view.append_event)
        worker.finished.connect(self._on_finished)
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
            self.header_open_output_button.setEnabled(True)
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
        self.loaded_tts_engine_id = self.preloaded_tts_engine_id
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
        if self.last_output_folder is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.last_output_folder))
            )

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
                f"Audio Mix Preview skipped: narration file not found: {narration_path}"
            )
            return
        output_dir = narration_path.parent
        self.last_output_folder = output_dir
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
            chunk_size=int(self.settings.get("chunk_size", 2500)),
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
        self.header_open_output_button.setEnabled(True)
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

    def _on_whisper_failed(self, message: str) -> None:
        self.whisper_progress_bar.setVisible(False)
        self.log_view.append_event(message)
        self._show_error(self.tr("generation_failed", "Generation failed"), message)

    def _on_whisper_cancelled(self) -> None:
        self.whisper_progress_bar.setVisible(False)
        self.log_view.append_event("Faster Whisper operation cancelled.")

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
            not running and self.last_output_folder is not None
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
            self.chatterbox_consent_checkbox,
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
            self.output_picker,
            self.normalize_checkbox,
            self.editor_highlighting_checkbox,
            self.podcast_enabled_checkbox,
            self.intro_enabled_checkbox,
            self.intro_picker,
            self.background_enabled_checkbox,
            self.background_picker,
            self.background_loop_checkbox,
            self.voice_volume_db_spin,
            self.background_volume_spin,
            self.voice_start_offset_spin,
            self.music_tail_spin,
            self.outro_enabled_checkbox,
            self.outro_picker,
            self.fade_in_spin,
            self.fade_out_spin,
            self.podcast_gap_spin,
            self.podcast_normalize_checkbox,
            self.podcast_ducking_checkbox,
            self.ducking_strength_combo,
            self.open_folder_checkbox,
        ):
            widget.setEnabled(not running)
        if not running:
            self._update_voice_panel_for_engine()
            self._refresh_generation_voice_combo()
            self._refresh_kokoro_python_status()
            self._refresh_chatterbox_status()
            self._refresh_qwen_status()

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
                "normalize_audio": self.normalize_checkbox.isChecked(),
                "editor_syntax_highlighting": (
                    self.editor_highlighting_checkbox.isChecked()
                ),
                "podcast_enabled": self.podcast_enabled_checkbox.isChecked(),
                "intro_enabled": self.intro_enabled_checkbox.isChecked(),
                "intro_path": str(self.intro_picker.path() or ""),
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
                "outro_enabled": self.outro_enabled_checkbox.isChecked(),
                "outro_path": str(self.outro_picker.path() or ""),
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
                    "voice_clone_consent": (
                        self.chatterbox_consent_checkbox.isChecked()
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
        self._save_settings()
        event.accept()

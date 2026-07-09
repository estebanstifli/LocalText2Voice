from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSize, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.audio_mix import (
    AudioMixSettings,
    render_audio_mix,
    render_audio_preview_segment,
)
from app.core.waveform_preview import WaveformEnvelope, db_to_gain, generate_waveform_preview

from .icons import ICON_DANGER, ui_icon

WaveformSeries = tuple[WaveformEnvelope, QColor, float, float, bool]


@dataclass(frozen=True)
class AudioMixPreviewContext:
    voice_path: Path
    output_dir: Path
    ffmpeg_path: str | Path
    music_path: Path | None
    settings: AudioMixSettings
    metadata: dict[str, str]


class WaveformLoadWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        voice_path: Path,
        music_path: Path | None,
        ffmpeg_path: str | Path,
        temp_dir: Path,
    ) -> None:
        super().__init__()
        self.voice_path = voice_path
        self.music_path = music_path
        self.ffmpeg_path = ffmpeg_path
        self.temp_dir = temp_dir

    @Slot()
    def run(self) -> None:
        try:
            started = time.perf_counter()
            self.log.emit(f"Audio Mix Preview: loading voice waveform from {self.voice_path}")
            voice = generate_waveform_preview(
                self.voice_path,
                self.ffmpeg_path,
                self.temp_dir,
            )
            self.log.emit(
                "Audio Mix Preview: voice waveform loaded in "
                f"{time.perf_counter() - started:.2f} s "
                f"({len(voice.times)} points, {voice.duration_seconds:.2f} s)."
            )
            music = None
            if self.music_path is not None:
                music_started = time.perf_counter()
                self.log.emit(
                    f"Audio Mix Preview: loading music waveform from {self.music_path}"
                )
                music = generate_waveform_preview(
                    self.music_path,
                    self.ffmpeg_path,
                    self.temp_dir,
                )
                self.log.emit(
                    "Audio Mix Preview: music waveform loaded in "
                    f"{time.perf_counter() - music_started:.2f} s "
                    f"({len(music.times)} points, {music.duration_seconds:.2f} s)."
                )
            else:
                self.log.emit("Audio Mix Preview: no background music selected.")
            self.finished.emit(voice, music)
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewRenderWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        context: AudioMixPreviewContext,
        settings: AudioMixSettings,
        output_path: Path,
        start_seconds: float,
    ) -> None:
        super().__init__()
        self.context = context
        self.settings = settings
        self.output_path = output_path
        self.start_seconds = start_seconds

    @Slot()
    def run(self) -> None:
        try:
            started = time.perf_counter()
            self.log.emit(
                "Audio Mix Preview: rendering 15 second preview with FFmpeg."
            )
            result = render_audio_preview_segment(
                voice_path=self.context.voice_path,
                output_path=self.output_path,
                ffmpeg_path=self.context.ffmpeg_path,
                settings=self.settings,
                music_path=self.context.music_path,
                start_seconds=self.start_seconds,
                duration_seconds=15,
            )
            self.log.emit(
                "Audio Mix Preview: 15 second preview rendered in "
                f"{time.perf_counter() - started:.2f} s: {result}"
            )
            self.finished.emit(str(result))
        except Exception as exc:
            self.failed.emit(str(exc))


class FinalMixRenderWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        context: AudioMixPreviewContext,
        settings: AudioMixSettings,
        voice_duration_seconds: float,
        output_path: Path,
    ) -> None:
        super().__init__()
        self.context = context
        self.settings = settings
        self.voice_duration_seconds = voice_duration_seconds
        self.output_path = output_path

    @Slot()
    def run(self) -> None:
        try:
            started = time.perf_counter()
            self.log.emit(
                f"Audio Mix Preview: rendering full mix with FFmpeg to {self.output_path}"
            )
            result = render_audio_mix(
                voice_path=self.context.voice_path,
                output_path=self.output_path,
                ffmpeg_path=self.context.ffmpeg_path,
                settings=self.settings,
                music_path=self.context.music_path,
                voice_duration_seconds=self.voice_duration_seconds,
                metadata=self.context.metadata,
            )
            self.log.emit(
                "Audio Mix Preview: full mix rendered in "
                f"{time.perf_counter() - started:.2f} s: {result}"
            )
            self.finished.emit(str(result))
        except Exception as exc:
            self.failed.emit(str(exc))


class WaveformGraph(QWidget):
    cursorChanged = Signal(float)

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.message = message
        self.waveforms: list[WaveformSeries] = []
        self.duration_seconds = 1.0
        self.view_start_seconds = 0.0
        self.view_duration_seconds = 1.0
        self.cursor_seconds = 0.0
        self.header_action_button: QPushButton | None = None
        self.setMinimumHeight(122)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMouseTracking(True)

    def set_title(self, title: str) -> None:
        self.title = title
        self._position_header_action()
        self.update()

    def set_header_action_button(self, button: QPushButton) -> None:
        self.header_action_button = button
        button.setParent(self)
        button.setObjectName("inlineActionButton")
        button.setFixedHeight(24)
        button.show()
        self._position_header_action()

    def set_message(self, message: str) -> None:
        self.message = message
        self.update()

    def set_waveforms(
        self,
        waveforms: list[WaveformSeries],
        duration_seconds: float,
    ) -> None:
        self.waveforms = waveforms
        self.duration_seconds = max(0.01, duration_seconds)
        self.view_duration_seconds = min(
            self.view_duration_seconds,
            self.duration_seconds,
        )
        self.view_start_seconds = max(
            0.0,
            min(self.view_start_seconds, self.duration_seconds - self.view_duration_seconds),
        )
        self.update()

    def set_view(self, start_seconds: float, duration_seconds: float) -> None:
        self.view_duration_seconds = max(
            0.01,
            min(duration_seconds, self.duration_seconds),
        )
        self.view_start_seconds = max(
            0.0,
            min(start_seconds, self.duration_seconds - self.view_duration_seconds),
        )
        self.update()

    def set_cursor(self, seconds: float) -> None:
        self.cursor_seconds = max(0.0, min(seconds, self.duration_seconds))
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        seconds = self._seconds_at_x(event.position().x())
        self.cursorChanged.emit(seconds)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if event.buttons() & Qt.MouseButton.LeftButton:
            seconds = self._seconds_at_x(event.position().x())
            self.cursorChanged.emit(seconds)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._position_header_action()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#e5eaf3"), 1))
        painter.drawRoundedRect(rect, 8, 8)

        title_rect = rect.adjusted(12, 8, -12, -rect.height() + 28)
        painter.setPen(QColor("#12213f"))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft, self.title)

        graph_rect = self._graph_rect()
        if graph_rect.width() < 16 or graph_rect.height() < 16:
            painter.setPen(QColor("#6a7488"))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
            return

        waveform_rect = self._waveform_rect(graph_rect)
        center_y = waveform_rect.center().y()
        graph_path = QPainterPath()
        graph_path.addRoundedRect(graph_rect, 7, 7)
        painter.fillPath(graph_path, QColor("#101827"))
        painter.save()
        painter.setClipPath(graph_path)
        painter.setPen(QPen(QColor("#1f2b3f"), 1))
        tick_count = (
            6
            if waveform_rect.width() >= 650
            else 4
            if waveform_rect.width() >= 420
            else 2
        )
        for index in range(1, tick_count):
            x = waveform_rect.left() + round(
                waveform_rect.width() * index / tick_count
            )
            painter.drawLine(x, graph_rect.top(), x, graph_rect.bottom())
        for index in range(1, 4):
            y = graph_rect.top() + round(graph_rect.height() * index / 4)
            painter.drawLine(graph_rect.left(), y, graph_rect.right(), y)
        painter.drawLine(waveform_rect.left(), center_y, waveform_rect.right(), center_y)
        painter.setPen(QColor("#94a3b8"))
        for index in range(0, tick_count + 1):
            x = waveform_rect.left() + round(
                waveform_rect.width() * index / tick_count
            )
            seconds = (
                self.view_start_seconds
                + self.view_duration_seconds * index / tick_count
            )
            painter.drawText(
                x - 18,
                graph_rect.top() - 8,
                self._format_time(seconds),
            )
        for index, label in enumerate(("0 dB", "-6", "-12", "-18", "-24", "-inf")):
            y = graph_rect.top() + 16 + index * max(1, (graph_rect.height() - 30) // 5)
            painter.drawText(graph_rect.right() - 42, y, label)
        painter.restore()

        if not self.waveforms:
            painter.setPen(QColor("#6a7488"))
            painter.drawText(
                graph_rect,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
            return

        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(waveform_rect.adjusted(1, 1, -1, -1), 6, 6)
        painter.setClipPath(clip_path)
        for envelope, color, gain, offset_seconds, loop in self.waveforms:
            self._draw_envelope(
                painter,
                waveform_rect,
                envelope,
                color,
                gain,
                offset_seconds,
                loop,
            )
        painter.restore()

        cursor_x = waveform_rect.left() + (
            (self.cursor_seconds - self.view_start_seconds)
            / max(0.01, self.view_duration_seconds)
        ) * max(1, waveform_rect.width())
        painter.setPen(QPen(QColor("#ffffff"), 2))
        if waveform_rect.left() <= cursor_x <= waveform_rect.right():
            painter.drawLine(
                int(cursor_x),
                graph_rect.top() - 6,
                int(cursor_x),
                graph_rect.bottom(),
            )
            painter.setBrush(QColor("#ffffff"))
            painter.drawPolygon(
                [
                    QPoint(int(cursor_x) - 6, graph_rect.top() - 6),
                    QPoint(int(cursor_x) + 6, graph_rect.top() - 6),
                    QPoint(int(cursor_x), graph_rect.top() + 2),
                ]
            )

    def _graph_rect(self):
        rect = self.rect().adjusted(1, 1, -1, -1)
        width = rect.width()
        height = rect.height()
        left_margin = 44 if width >= 520 else 34 if width >= 380 else 18
        right_margin = 14 if width >= 420 else 8
        top_margin = 38 if height >= 135 else 32 if height >= 105 else 26
        bottom_margin = 14 if height >= 105 else 8
        return rect.adjusted(
            left_margin,
            top_margin,
            -right_margin,
            -bottom_margin,
        )

    def _position_header_action(self) -> None:
        if self.header_action_button is None:
            return
        text_width = self.fontMetrics().horizontalAdvance(self.title)
        x = min(12 + text_width + 10, max(12, self.width() - 94))
        self.header_action_button.move(x, 6)

    @staticmethod
    def _waveform_rect(graph_rect):
        right_padding = 46 if graph_rect.width() >= 260 else 2
        return graph_rect.adjusted(1, 1, -right_padding, -1)

    def _draw_envelope(
        self,
        painter: QPainter,
        graph_rect,
        envelope: WaveformEnvelope,
        color: QColor,
        gain: float,
        offset_seconds: float,
        loop: bool,
    ) -> None:
        if envelope.is_empty:
            return
        painter.setPen(QPen(color, 1))
        half_height = max(1, graph_rect.height() / 2 - 14)
        center_y = graph_rect.center().y()
        duration = max(self.view_duration_seconds, 0.01)
        width = max(1, graph_rect.width())
        first_repeat = 0
        repetitions = 1
        if loop and envelope.duration_seconds > 0:
            first_repeat = max(
                0,
                int((self.view_start_seconds - offset_seconds) // envelope.duration_seconds)
                - 1,
            )
            last_repeat = int(
                (
                    self.view_start_seconds
                    + self.view_duration_seconds
                    - offset_seconds
                )
                // envelope.duration_seconds
            ) + 2
            repetitions = min(
                int(self.duration_seconds // envelope.duration_seconds) + 2,
                max(first_repeat + 1, last_repeat),
            )
        for repeat_index in range(first_repeat, repetitions):
            repeat_offset = (
                repeat_index * envelope.duration_seconds
                if loop and envelope.duration_seconds > 0
                else 0
            )
            self._draw_envelope_once(
                painter,
                graph_rect,
                envelope,
                gain,
                offset_seconds + repeat_offset,
                duration,
                width,
                center_y,
                half_height,
            )

    def _draw_envelope_once(
        self,
        painter: QPainter,
        graph_rect,
        envelope: WaveformEnvelope,
        gain: float,
        offset_seconds: float,
        duration: float,
        width: int,
        center_y: int,
        half_height: float,
    ) -> None:
        for time_value, min_value, max_value in zip(
            envelope.times,
            envelope.minimums,
            envelope.maximums,
            strict=False,
        ):
            absolute_time = time_value + offset_seconds
            if (
                absolute_time < self.view_start_seconds
                or absolute_time > self.view_start_seconds + self.view_duration_seconds
            ):
                continue
            x = graph_rect.left() + (
                (absolute_time - self.view_start_seconds) / duration
            ) * width
            low = max(-1.0, min(1.0, min_value * gain))
            high = max(-1.0, min(1.0, max_value * gain))
            y1 = center_y - high * half_height
            y2 = center_y - low * half_height
            painter.drawLine(int(x), int(y1), int(x), int(y2))

    def _seconds_at_x(self, x_value: float) -> float:
        waveform_rect = self._waveform_rect(self._graph_rect())
        ratio = max(
            0.0,
            min(
                1.0,
                (x_value - waveform_rect.left()) / max(1, waveform_rect.width()),
            ),
        )
        return self.view_start_seconds + ratio * self.view_duration_seconds

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


class AudioMixPreviewPanel(QWidget):
    backRequested = Signal()
    openFolderRequested = Signal()
    changeMusicRequested = Signal()
    settingsChanged = Signal(object)
    renderFinished = Signal(str)
    errorOccurred = Signal(str)
    log = Signal(str)

    def __init__(self, tr_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr = tr_callback
        self.context: AudioMixPreviewContext | None = None
        self.voice_envelope: WaveformEnvelope | None = None
        self.music_envelope: WaveformEnvelope | None = None
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="local_text_2_voice_mix_preview_"
        )
        self.waveform_thread: QThread | None = None
        self.waveform_worker: WaveformLoadWorker | None = None
        self.render_thread: QThread | None = None
        self.render_worker: QObject | None = None
        self.total_duration_seconds = 1.0
        self.view_start_seconds = 0.0
        self.view_window_seconds = 60.0
        self.zoom_levels_seconds = [15.0, 30.0, 60.0, 300.0, 900.0, 0.0]
        self.zoom_level_index = 2
        self.preview_start_seconds = 0.0
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_player.playbackStateChanged.connect(
            self._on_playback_state_changed
        )
        self.preview_player.positionChanged.connect(self._on_preview_position_changed)
        self.voice_color = QColor("#2563eb")
        self.music_color = QColor("#f97316")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)
        self.info_label = QLabel(
            self.tr(
                "mix_preview_waiting",
                "Generate audio to inspect the voice and music mix here.",
            )
        )
        self.info_label.setObjectName("helperLabel")
        self.info_label.setWordWrap(True)
        card_layout.addWidget(self.info_label)

        self.voice_graph = WaveformGraph(
            self.tr("voice_waveform", "Voice"),
            self.tr("waveform_loading", "Loading waveform..."),
        )
        self.music_graph = WaveformGraph(
            self.tr("music_waveform", "Music"),
            self.tr("no_music_selected", "No background music selected."),
        )
        self.mix_graph = WaveformGraph(
            self.tr("mix_preview_waveform", "Mix Preview"),
            self.tr("waveform_loading", "Loading waveform..."),
        )
        self.voice_graph.cursorChanged.connect(self._set_shared_cursor)
        card_layout.addWidget(self.voice_graph)

        self.change_music_button = QPushButton(self.tr("change_music", "Change"))
        self.change_music_button.setIcon(ui_icon("folder"))
        self.change_music_button.setIconSize(QSize(14, 14))
        self.change_music_button.clicked.connect(self.changeMusicRequested.emit)
        self.music_graph.set_header_action_button(self.change_music_button)

        self.music_graph.cursorChanged.connect(self._set_shared_cursor)
        card_layout.addWidget(self.music_graph)
        self.mix_graph.cursorChanged.connect(self._set_shared_cursor)
        card_layout.addWidget(self.mix_graph)
        layout.addWidget(card, 1)

        controls = QFrame()
        controls.setObjectName("card")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(10)

        self.voice_slider, self.voice_spin = self._volume_control(-12, 6, 0)
        self.music_slider, self.music_spin = self._volume_control(-36, 0, -7)
        self.voice_offset_spin = self._milliseconds_spin(-300000, 300000, 2000)
        self.music_tail_spin = self._milliseconds_spin(0, 600000, 2000)
        self.fade_in_spin = self._seconds_spin(1.0)
        self.fade_out_spin = self._seconds_spin(1.0)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setIcon(ui_icon("zoom_out"))
        self.zoom_out_button.setIconSize(QSize(18, 18))
        self.zoom_out_button.setText("-")
        self.zoom_out_button.setToolTip(self.tr("zoom_out", "Zoom out"))
        self.zoom_out_button.setFixedWidth(48)
        self.zoom_in_button = QPushButton()
        self.zoom_in_button.setIcon(ui_icon("zoom_in"))
        self.zoom_in_button.setIconSize(QSize(18, 18))
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setToolTip(self.tr("zoom_in", "Zoom in"))
        self.zoom_in_button.setFixedWidth(48)
        self.timeline_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scroll.setRange(0, 0)
        self.timeline_scroll.setPageStep(1000)
        self.timeline_label = QLabel("00:00 - 00:00")
        self.timeline_label.setObjectName("helperLabel")
        self.ducking_checkbox = QCheckBox(
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

        controls_layout.addWidget(
            self._control_label("timeline", self.tr("timeline_zoom", "Timeline")),
            0,
            0,
        )
        controls_layout.addWidget(self.zoom_out_button, 0, 1)
        controls_layout.addWidget(self.zoom_in_button, 0, 2)
        controls_layout.addWidget(self.timeline_scroll, 0, 3)
        controls_layout.addWidget(self.timeline_label, 0, 4)
        controls_layout.addWidget(
            self._control_label("volume", self.tr("voice_volume", "Voice volume")),
            1,
            0,
        )
        controls_layout.addWidget(self.voice_slider, 1, 1, 1, 3)
        controls_layout.addWidget(self.voice_spin, 1, 4)
        controls_layout.addWidget(
            self._control_label("volume", self.tr("music_volume", "Music volume")),
            2,
            0,
        )
        controls_layout.addWidget(self.music_slider, 2, 1, 1, 3)
        controls_layout.addWidget(self.music_spin, 2, 4)
        controls_layout.addWidget(
            self._control_label(
                "offset",
                self.tr("voice_start_offset", "Voice start offset"),
            ),
            3,
            0,
        )
        controls_layout.addWidget(self.voice_offset_spin, 3, 1)
        controls_layout.addWidget(
            self._control_label("tail", self.tr("music_tail", "Music after voice")),
            3,
            2,
        )
        controls_layout.addWidget(self.music_tail_spin, 3, 3)
        controls_layout.addWidget(
            self._control_label("fade_in", self.tr("music_fade_in", "Music fade in")),
            4,
            0,
        )
        controls_layout.addWidget(self.fade_in_spin, 4, 1)
        controls_layout.addWidget(
            self._control_label(
                "fade_out",
                self.tr("music_fade_out", "Music fade out"),
            ),
            4,
            2,
        )
        controls_layout.addWidget(self.fade_out_spin, 4, 3)
        self.ducking_checkbox.setIcon(ui_icon("ducking"))
        self.ducking_checkbox.setIconSize(QSize(18, 18))
        controls_layout.addWidget(self.ducking_checkbox, 5, 0, 1, 2)
        controls_layout.addWidget(
            self._control_label(
                "ducking",
                self.tr("ducking_strength", "Ducking strength"),
            ),
            5,
            2,
        )
        controls_layout.addWidget(self.ducking_strength_combo, 5, 3)
        controls_layout.setColumnStretch(3, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        self.preview_button = QPushButton(
            self.tr("preview_15_seconds", "Preview 15 seconds")
        )
        self.preview_button.setIcon(ui_icon("play"))
        self.preview_button.setIconSize(QSize(18, 18))
        self.preview_button.clicked.connect(self._render_preview)
        self.stop_button = QPushButton(self.tr("stop_preview", "Stop preview"))
        self.stop_button.setIcon(ui_icon("stop", color=ICON_DANGER))
        self.stop_button.setIconSize(QSize(18, 18))
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.clicked.connect(self.preview_player.stop)
        self.render_button = QPushButton(
            self.tr("render_full_mix", "Render full mix")
        )
        self.render_button.setIcon(ui_icon("render"))
        self.render_button.setIconSize(QSize(18, 18))
        self.render_button.setObjectName("primaryButton")
        self.render_button.clicked.connect(self._render_full_mix)
        button_row.addStretch(1)
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.render_button)
        controls_layout.addLayout(button_row, 6, 0, 1, 5)
        layout.addWidget(controls)

        for widget in (
            self.voice_spin,
            self.music_spin,
            self.voice_offset_spin,
            self.music_tail_spin,
            self.fade_in_spin,
            self.fade_out_spin,
            self.ducking_checkbox,
            self.ducking_strength_combo,
        ):
            if isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self._on_controls_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_controls_changed)
            else:
                widget.valueChanged.connect(self._on_controls_changed)
        self.zoom_out_button.clicked.connect(self._on_zoom_out)
        self.zoom_in_button.clicked.connect(self._on_zoom_in)
        self.timeline_scroll.valueChanged.connect(self._on_timeline_scrolled)

    @staticmethod
    def _control_label(icon_name: str, text: str) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        icon_label = QLabel()
        icon_label.setStyleSheet("background: transparent; border: none;")
        icon_label.setPixmap(ui_icon(icon_name).pixmap(16, 16))
        text_label = QLabel(text)
        text_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(icon_label)
        layout.addWidget(text_label)
        layout.addStretch(1)
        return widget

    def _volume_control(
        self,
        minimum: int,
        maximum: int,
        default: int,
    ) -> tuple[QSlider, QDoubleSpinBox]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum * 10, maximum * 10)
        slider.setValue(default * 10)
        spin = QDoubleSpinBox()
        spin.setRange(float(minimum), float(maximum))
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" dB")
        spin.setValue(float(default))
        slider.valueChanged.connect(lambda value: spin.setValue(value / 10))
        spin.valueChanged.connect(lambda value: slider.setValue(round(value * 10)))
        return slider, spin

    @staticmethod
    def _seconds_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 30.0)
        spin.setSingleStep(0.1)
        spin.setDecimals(2)
        spin.setSuffix(" s")
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

    def set_context(self, context: AudioMixPreviewContext) -> None:
        self.context = context
        self.voice_envelope = None
        self.music_envelope = None
        self._apply_settings(context.settings)
        self.info_label.setText(
            self.tr(
                "mix_preview_loading",
                "Loading waveform preview for {file}...",
                file=context.voice_path.name,
            )
        )
        self.voice_graph.set_waveforms([], 1)
        self.music_graph.set_waveforms([], 1)
        self.mix_graph.set_waveforms([], 1)
        self.preview_button.setEnabled(False)
        self.render_button.setEnabled(False)
        self._load_waveforms()

    def clear_context(self) -> None:
        self._finish_waveform_thread()
        self.context = None
        self.voice_envelope = None
        self.music_envelope = None
        self.info_label.setText(
            self.tr(
                "mix_preview_no_audio",
                "Generate or open an audiobook before previewing the mix.",
            )
        )
        self.voice_graph.set_waveforms([], 1)
        self.music_graph.set_waveforms([], 1)
        self.mix_graph.set_waveforms([], 1)
        self.preview_button.setEnabled(False)
        self.render_button.setEnabled(False)

    def current_settings(self) -> AudioMixSettings:
        current = self.context.settings if self.context else AudioMixSettings()
        return AudioMixSettings(
            voice_volume_db=self.voice_spin.value(),
            music_volume_db=self.music_spin.value(),
            voice_start_offset_ms=self.voice_offset_spin.value(),
            music_tail_ms=self.music_tail_spin.value(),
            music_fade_in_seconds=self.fade_in_spin.value(),
            music_fade_out_seconds=self.fade_out_spin.value(),
            ducking_enabled=self.ducking_checkbox.isChecked(),
            ducking_strength=str(
                self.ducking_strength_combo.currentData() or "medium"
            ),
            loop_background=current.loop_background,
            normalize=current.normalize,
            mp3_bitrate=current.mp3_bitrate,
        )

    def _apply_settings(self, settings: AudioMixSettings) -> None:
        self.voice_spin.blockSignals(True)
        self.voice_slider.blockSignals(True)
        self.music_spin.blockSignals(True)
        self.music_slider.blockSignals(True)
        self.voice_offset_spin.blockSignals(True)
        self.music_tail_spin.blockSignals(True)
        self.fade_in_spin.blockSignals(True)
        self.fade_out_spin.blockSignals(True)
        self.ducking_checkbox.blockSignals(True)
        self.ducking_strength_combo.blockSignals(True)
        self.voice_spin.setValue(settings.voice_volume_db)
        self.voice_slider.setValue(round(settings.voice_volume_db * 10))
        self.music_spin.setValue(settings.music_volume_db)
        self.music_slider.setValue(round(settings.music_volume_db * 10))
        self.voice_offset_spin.setValue(settings.voice_start_offset_ms)
        self.music_tail_spin.setValue(settings.music_tail_ms)
        self.fade_in_spin.setValue(settings.music_fade_in_seconds)
        self.fade_out_spin.setValue(settings.music_fade_out_seconds)
        self.ducking_checkbox.setChecked(settings.ducking_enabled)
        index = self.ducking_strength_combo.findData(settings.ducking_strength)
        self.ducking_strength_combo.setCurrentIndex(max(0, index))
        self.voice_spin.blockSignals(False)
        self.voice_slider.blockSignals(False)
        self.music_spin.blockSignals(False)
        self.music_slider.blockSignals(False)
        self.voice_offset_spin.blockSignals(False)
        self.music_tail_spin.blockSignals(False)
        self.fade_in_spin.blockSignals(False)
        self.fade_out_spin.blockSignals(False)
        self.ducking_checkbox.blockSignals(False)
        self.ducking_strength_combo.blockSignals(False)

    def _load_waveforms(self) -> None:
        if self.context is None:
            return
        self._finish_waveform_thread()
        thread = QThread(self)
        worker = WaveformLoadWorker(
            self.context.voice_path,
            self.context.music_path,
            self.context.ffmpeg_path,
            Path(self.temp_dir.name),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log.emit)
        worker.finished.connect(self._on_waveforms_loaded)
        worker.failed.connect(self._on_waveform_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._waveform_thread_finished)
        self.waveform_thread = thread
        self.waveform_worker = worker
        thread.start()

    def _on_waveforms_loaded(
        self,
        voice: WaveformEnvelope,
        music: WaveformEnvelope | None,
    ) -> None:
        self.log.emit("Audio Mix Preview: waveform data ready for drawing.")
        self.voice_envelope = voice
        self.music_envelope = music
        self.info_label.setText(
            self.tr(
                "mix_preview_ready",
                "Move the volume sliders to check whether music competes with the voice.",
            )
        )
        self._update_waveforms()
        self.preview_button.setEnabled(True)
        self.render_button.setEnabled(True)

    def _on_waveform_failed(self, message: str) -> None:
        self.log.emit(f"Audio Mix Preview: waveform loading failed: {message}")
        self.info_label.setText(
            self.tr("mix_preview_error", "Waveform preview failed: {message}", message=message)
        )
        self.errorOccurred.emit(message)
        self.preview_button.setEnabled(True)
        self.render_button.setEnabled(False)

    def _on_controls_changed(self, *_args) -> None:
        self.settingsChanged.emit(self.current_settings())
        self._update_waveforms()

    def _update_waveforms(self) -> None:
        if self.voice_envelope is None:
            return
        settings = self.current_settings()
        voice_gain = db_to_gain(settings.voice_volume_db)
        music_gain = db_to_gain(settings.music_volume_db)
        voice_offset = settings.voice_start_offset_ms / 1000
        voice_trim = max(0.0, -voice_offset)
        voice_delay = max(0.0, voice_offset)
        effective_voice_duration = max(
            0.01,
            self.voice_envelope.duration_seconds - voice_trim,
        )
        duration = voice_delay + effective_voice_duration
        if self.music_envelope is not None:
            duration += max(0.0, settings.music_tail_ms / 1000)
            if not settings.loop_background:
                duration = max(duration, self.music_envelope.duration_seconds)
        self.total_duration_seconds = max(0.01, duration)
        self.voice_graph.set_waveforms(
            [(self.voice_envelope, self.voice_color, voice_gain, voice_offset, False)],
            self.total_duration_seconds,
        )
        if self.music_envelope is not None:
            self.music_graph.set_waveforms(
                [
                    (
                        self.music_envelope,
                        self.music_color,
                        music_gain,
                        0.0,
                        settings.loop_background,
                    )
                ],
                self.total_duration_seconds,
            )
            self.mix_graph.set_waveforms(
                [
                    (
                        self.voice_envelope,
                        self.voice_color,
                        voice_gain,
                        voice_offset,
                        False,
                    ),
                    (
                        self.music_envelope,
                        self.music_color,
                        music_gain,
                        0.0,
                        settings.loop_background,
                    ),
                ],
                self.total_duration_seconds,
            )
        else:
            self.music_graph.set_waveforms([], self.total_duration_seconds)
            self.mix_graph.set_waveforms(
                [
                    (
                        self.voice_envelope,
                        self.voice_color,
                        voice_gain,
                        voice_offset,
                        False,
                    )
                ],
                self.total_duration_seconds,
            )
        self._sync_timeline_controls()
        self._apply_view_to_graphs()

    def _set_shared_cursor(self, seconds: float) -> None:
        for graph in (self.voice_graph, self.music_graph, self.mix_graph):
            graph.set_cursor(seconds)

    def _on_zoom_out(self) -> None:
        if self.zoom_level_index < len(self.zoom_levels_seconds) - 1:
            self.zoom_level_index += 1
        self._apply_zoom_level()

    def _on_zoom_in(self) -> None:
        if self.zoom_level_index > 0:
            self.zoom_level_index -= 1
        self._apply_zoom_level()

    def _apply_zoom_level(self) -> None:
        requested = self._current_zoom_seconds()
        self.view_window_seconds = (
            self.total_duration_seconds if requested <= 0 else requested
        )
        self._sync_timeline_controls()
        self._apply_view_to_graphs()

    def _current_zoom_seconds(self) -> float:
        return self.zoom_levels_seconds[self.zoom_level_index]

    def _on_timeline_scrolled(self, value: int) -> None:
        self.view_start_seconds = value / 1000
        self._apply_view_to_graphs()

    def _sync_timeline_controls(self) -> None:
        total = max(0.01, self.total_duration_seconds)
        if self._current_zoom_seconds() == 0:
            self.view_window_seconds = total
        else:
            self.view_window_seconds = min(self.view_window_seconds, total)
        max_start = max(0.0, total - self.view_window_seconds)
        self.view_start_seconds = max(0.0, min(self.view_start_seconds, max_start))
        self.timeline_scroll.blockSignals(True)
        self.timeline_scroll.setRange(0, round(max_start * 1000))
        self.timeline_scroll.setPageStep(round(self.view_window_seconds * 1000))
        self.timeline_scroll.setValue(round(self.view_start_seconds * 1000))
        self.timeline_scroll.blockSignals(False)
        self.zoom_in_button.setEnabled(self.zoom_level_index > 0)
        self.zoom_out_button.setEnabled(
            self.zoom_level_index < len(self.zoom_levels_seconds) - 1
        )
        self._update_timeline_label()

    def _apply_view_to_graphs(self) -> None:
        window = min(self.view_window_seconds, self.total_duration_seconds)
        for graph in (self.voice_graph, self.music_graph, self.mix_graph):
            graph.set_view(self.view_start_seconds, window)
        self._update_timeline_label()

    def _update_timeline_label(self) -> None:
        end = min(
            self.total_duration_seconds,
            self.view_start_seconds + self.view_window_seconds,
        )
        self.timeline_label.setText(
            f"{self._format_time(self.view_start_seconds)} - {self._format_time(end)}"
        )

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _render_preview(self) -> None:
        if self.context is None:
            return
        self.preview_start_seconds = self.view_start_seconds
        self._set_shared_cursor(self.preview_start_seconds)
        output = Path(self.temp_dir.name) / (
            f"mix_preview_15s_{round(time.time() * 1000)}.wav"
        )
        self._start_render_worker(
            PreviewRenderWorker(
                self.context,
                self.current_settings(),
                output,
                self.preview_start_seconds,
            ),
            self._on_preview_rendered,
        )
        self.info_label.setText(
            self.tr("rendering_preview", "Rendering 15 second preview...")
        )

    def _render_full_mix(self) -> None:
        if self.context is None or self.voice_envelope is None:
            return
        output_path = self._next_mix_filename(self.context.output_dir)
        self._start_render_worker(
            FinalMixRenderWorker(
                self.context,
                self.current_settings(),
                self.voice_envelope.duration_seconds,
                output_path,
            ),
            self._on_full_mix_rendered,
        )
        self.info_label.setText(self.tr("rendering_mix", "Rendering full mix..."))

    def _start_render_worker(self, worker: QObject, success_slot) -> None:  # noqa: ANN001
        self._finish_render_thread()
        self.preview_button.setEnabled(False)
        self.render_button.setEnabled(False)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log.emit)
        worker.finished.connect(success_slot)
        worker.failed.connect(self._on_render_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._render_thread_finished)
        self.render_thread = thread
        self.render_worker = worker
        thread.start()

    def _on_preview_rendered(self, path: str) -> None:
        self.info_label.setText(self.tr("playing_preview", "Playing preview..."))
        self.preview_player.setSource(QUrl.fromLocalFile(path))
        self.preview_player.play()

    def _on_preview_position_changed(self, milliseconds: int) -> None:
        if (
            self.preview_player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            return
        seconds = self.preview_start_seconds + milliseconds / 1000
        self._set_shared_cursor(min(seconds, self.total_duration_seconds))

    def _on_full_mix_rendered(self, path: str) -> None:
        self.info_label.setText(
            self.tr("mix_saved", "Mix saved: {path}", path=path)
        )
        self.renderFinished.emit(path)

    def _on_render_failed(self, message: str) -> None:
        self.info_label.setText(
            self.tr("mix_preview_error", "Waveform preview failed: {message}", message=message)
        )
        self.errorOccurred.emit(message)

    def _render_thread_finished(self) -> None:
        self.render_thread = None
        self.render_worker = None
        self.preview_button.setEnabled(True)
        self.render_button.setEnabled(True)

    def _waveform_thread_finished(self) -> None:
        self.waveform_thread = None
        self.waveform_worker = None

    def _on_playback_state_changed(self, state) -> None:  # noqa: ANN001
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.info_label.setText(self.tr("playing_preview", "Playing preview..."))
        elif self.context is not None and self.voice_envelope is not None:
            self.info_label.setText(
                self.tr(
                    "mix_preview_ready",
                    "Move the volume sliders to check whether music competes with the voice.",
                )
            )

    @staticmethod
    def _next_mix_filename(output_dir: Path) -> Path:
        index = 1
        while True:
            candidate = output_dir / f"podcast_remix{index}.mp3"
            if not candidate.exists():
                return candidate
            index += 1

    def _finish_waveform_thread(self) -> None:
        if self.waveform_thread is not None and self.waveform_thread.isRunning():
            self.waveform_thread.quit()
            self.waveform_thread.wait(2000)
        self.waveform_thread = None

    def _finish_render_thread(self) -> None:
        if self.render_thread is not None and self.render_thread.isRunning():
            self.render_thread.quit()
            self.render_thread.wait(2000)
        self.render_thread = None

    def close(self) -> bool:
        self._finish_waveform_thread()
        self._finish_render_thread()
        return super().close()

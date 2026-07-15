from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSize, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTextCursor, QTextFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.audio_library import resolve_audio_reference
from app.core.audio_mix import (
    AudioMixSettings,
    render_audio_mix,
    render_audio_preview_segment,
)
from app.core.waveform_preview import WaveformEnvelope, db_to_gain, generate_waveform_preview
from app.core.audio_event_timeline import ResolvedAudioClip, SpeechInterval
from app.core.audiobook_store import StoredAudioEvent, StoredSegment

from .icons import ICON_DANGER, ui_icon

WaveformSeries = tuple[WaveformEnvelope, QColor, float, float, bool]
MIX_PREVIEW_DURATION_SECONDS = 60.0


@dataclass(frozen=True)
class AudioMixPreviewContext:
    voice_path: Path
    output_dir: Path
    ffmpeg_path: str | Path
    music_path: Path | None
    settings: AudioMixSettings
    metadata: dict[str, str]
    audiobook_id: int | None = None
    project_dir: Path | None = None
    project_settings: dict[str, object] | None = None
    segments: tuple[StoredSegment, ...] = ()
    audio_events: tuple[StoredAudioEvent, ...] = ()
    timeline_clips: tuple[ResolvedAudioClip, ...] = ()
    speech_intervals: tuple[SpeechInterval, ...] = ()
    stem_cache_dir: Path | None = None


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
        max_duration_seconds: float,
    ) -> None:
        super().__init__()
        self.voice_path = voice_path
        self.music_path = music_path
        self.ffmpeg_path = ffmpeg_path
        self.temp_dir = temp_dir
        self.max_duration_seconds = max_duration_seconds

    @Slot()
    def run(self) -> None:
        try:
            started = time.perf_counter()
            self.log.emit(f"Audio Mix: loading voice waveform from {self.voice_path}")
            voice = generate_waveform_preview(
                self.voice_path,
                self.ffmpeg_path,
                self.temp_dir,
                max_duration_seconds=self.max_duration_seconds,
            )
            self.log.emit(
                "Audio Mix: voice waveform loaded in "
                f"{time.perf_counter() - started:.2f} s "
                f"({len(voice.times)} points, {voice.duration_seconds:.2f} s)."
            )
            music = None
            if self.music_path is not None:
                music_started = time.perf_counter()
                self.log.emit(
                    f"Audio Mix: loading music waveform from {self.music_path}"
                )
                music = generate_waveform_preview(
                    self.music_path,
                    self.ffmpeg_path,
                    self.temp_dir,
                    max_duration_seconds=self.max_duration_seconds,
                )
                self.log.emit(
                    "Audio Mix: music waveform loaded in "
                    f"{time.perf_counter() - music_started:.2f} s "
                    f"({len(music.times)} points, {music.duration_seconds:.2f} s)."
                )
            else:
                self.log.emit("Audio Mix: no background music selected.")
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
        duration_seconds: float = MIX_PREVIEW_DURATION_SECONDS,
    ) -> None:
        super().__init__()
        self.context = context
        self.settings = settings
        self.output_path = output_path
        self.start_seconds = start_seconds
        self.duration_seconds = duration_seconds

    @Slot()
    def run(self) -> None:
        try:
            started = time.perf_counter()
            self.log.emit(
                "Audio Mix: rendering playable mix segment with FFmpeg "
                f"from {self.start_seconds:.2f}s for {self.duration_seconds:.2f}s."
            )
            result = render_audio_preview_segment(
                voice_path=self.context.voice_path,
                output_path=self.output_path,
                ffmpeg_path=self.context.ffmpeg_path,
                settings=self.settings,
                music_path=self.context.music_path,
                start_seconds=self.start_seconds,
                duration_seconds=self.duration_seconds,
                timeline_clips=self.context.timeline_clips,
                speech_intervals=self.context.speech_intervals,
                stem_cache_dir=self.context.stem_cache_dir,
            )
            self.log.emit(
                "Audio Mix: playable mix segment rendered in "
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
                f"Audio Mix: rendering full mix with FFmpeg to {self.output_path}"
            )
            result = render_audio_mix(
                voice_path=self.context.voice_path,
                output_path=self.output_path,
                ffmpeg_path=self.context.ffmpeg_path,
                settings=self.settings,
                music_path=self.context.music_path,
                voice_duration_seconds=self.voice_duration_seconds,
                metadata=self.context.metadata,
                timeline_clips=self.context.timeline_clips,
                speech_intervals=self.context.speech_intervals,
                stem_cache_dir=self.context.stem_cache_dir,
            )
            self.log.emit(
                "Audio Mix: full mix rendered in "
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
        self.header_action_button: QWidget | None = None
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

    def set_header_action_button(self, button: QWidget) -> None:
        self.header_action_button = button
        button.setParent(self)
        if isinstance(button, QPushButton):
            button.setObjectName("inlineActionButton")
        button.setFixedHeight(28)
        button.adjustSize()
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
        tick_count = (
            6
            if waveform_rect.width() >= 650
            else 4
            if waveform_rect.width() >= 420
            else 2
        )
        painter.setPen(QColor("#64748b"))
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

        graph_path = QPainterPath()
        graph_path.addRoundedRect(graph_rect, 7, 7)
        painter.fillPath(graph_path, QColor("#101827"))
        painter.save()
        painter.setClipPath(graph_path)
        painter.setPen(QPen(QColor("#1f2b3f"), 1))
        for index in range(1, tick_count):
            x = waveform_rect.left() + round(
                waveform_rect.width() * index / tick_count
            )
            painter.drawLine(x, graph_rect.top(), x, graph_rect.bottom())
        for index in range(1, 4):
            y = graph_rect.top() + round(graph_rect.height() * index / 4)
            painter.drawLine(graph_rect.left(), y, graph_rect.right(), y)
        painter.drawLine(waveform_rect.left(), center_y, waveform_rect.right(), center_y)
        painter.setPen(QColor("#cbd5e1"))
        amplitude_half_height = max(1, waveform_rect.height() / 2 - 14)
        for value, label in (
            (1.0, "1"),
            (0.5, "0,5"),
            (0.0, "0"),
            (-0.5, "-0,5"),
            (-1.0, "-1"),
        ):
            y = int(center_y - value * amplitude_half_height)
            painter.drawText(graph_rect.right() - 38, y + 5, label)
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
        top_margin = 54 if height >= 135 else 46 if height >= 105 else 40
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
        action_width = max(
            94,
            self.header_action_button.sizeHint().width(),
        )
        x = min(12 + text_width + 10, max(12, self.width() - action_width - 12))
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


class MultitrackWaveformGraph(QWidget):
    """Compact studio-style timeline for voice, music, ambient and SFX buses."""

    cursorChanged = Signal(float)
    TRACKS = ("voice", "background", "music", "ambient", "sfx")

    def __init__(self, tr_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr = tr_callback
        self.voice_series: WaveformSeries | None = None
        self.background_series: WaveformSeries | None = None
        self.clips: tuple[ResolvedAudioClip, ...] = ()
        self.duration_seconds = 1.0
        self.view_start_seconds = 0.0
        self.view_duration_seconds = 1.0
        self.cursor_seconds = 0.0
        self.setMinimumHeight(330)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(
        self,
        voice_series: WaveformSeries | None,
        background_series: WaveformSeries | None,
        clips: tuple[ResolvedAudioClip, ...],
        duration_seconds: float,
    ) -> None:
        self.voice_series = voice_series
        self.background_series = background_series
        self.clips = clips
        self.duration_seconds = max(0.01, duration_seconds)
        self.update()

    def set_view(self, start_seconds: float, duration_seconds: float) -> None:
        self.view_duration_seconds = max(
            0.01, min(duration_seconds, self.duration_seconds)
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
        self.cursorChanged.emit(self._seconds_at_x(event.position().x()))

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.cursorChanged.emit(self._seconds_at_x(event.position().x()))

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(outer, QColor("#08111f"))
        painter.setPen(QPen(QColor("#24324a"), 1))
        painter.drawRoundedRect(outer, 10, 10)

        label_width = 112
        header_height = 34
        timeline = outer.adjusted(label_width, header_height, -12, -10)
        lane_height = max(28, timeline.height() / len(self.TRACKS))
        colors = {
            "voice": QColor("#3b82f6"),
            "background": QColor("#f59e0b"),
            "music": QColor("#a855f7"),
            "ambient": QColor("#14b8a6"),
            "sfx": QColor("#f43f5e"),
        }
        labels = {
            "voice": self.tr("voice", "Voice"),
            "background": self.tr("background_music", "Global music"),
            "music": self.tr("markup_music", "Markup music"),
            "ambient": self.tr("ambient", "Ambient"),
            "sfx": self.tr("sfx", "SFX"),
        }

        painter.setPen(QColor("#94a3b8"))
        for tick in range(7):
            x = timeline.left() + round(timeline.width() * tick / 6)
            painter.drawLine(x, timeline.top(), x, timeline.bottom())
            seconds = self.view_start_seconds + self.view_duration_seconds * tick / 6
            painter.drawText(x - 20, outer.top() + 8, WaveformGraph._format_time(seconds))

        for index, track in enumerate(self.TRACKS):
            top = timeline.top() + round(index * lane_height)
            bottom = timeline.top() + round((index + 1) * lane_height)
            lane = timeline.adjusted(0, top - timeline.top(), 0, bottom - timeline.bottom())
            painter.fillRect(lane, QColor("#0f1b2d") if index % 2 == 0 else QColor("#0b1626"))
            painter.setPen(QColor("#26364f"))
            painter.drawLine(timeline.left(), bottom, timeline.right(), bottom)
            painter.setPen(colors[track])
            painter.drawText(outer.left() + 14, top, label_width - 20, bottom - top, Qt.AlignmentFlag.AlignVCenter, labels[track])

            series = self.voice_series if track == "voice" else self.background_series if track == "background" else None
            if series is not None:
                self._draw_series(painter, lane.adjusted(2, 5, -2, -5), series)

            for clip in self.clips:
                if clip.track != track:
                    continue
                start = clip.timeline_start_ms / 1000
                duration = max(0.12, (clip.playback_duration_ms or 1000) / 1000)
                end = start + duration
                view_end = self.view_start_seconds + self.view_duration_seconds
                if end < self.view_start_seconds or start > view_end:
                    continue
                left = self._x_for_seconds(max(start, self.view_start_seconds), timeline)
                right = self._x_for_seconds(min(end, view_end), timeline)
                block = lane.adjusted(left - lane.left() + 2, 7, right - lane.right() - 2, -7)
                fill = QColor(colors[track])
                fill.setAlpha(185)
                painter.setBrush(fill)
                painter.setPen(QPen(colors[track].lighter(135), 1))
                painter.drawRoundedRect(block, 5, 5)
                if block.width() > 55:
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(block.adjusted(7, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter, Path(clip.file_path).stem)

        cursor_x = self._x_for_seconds(self.cursor_seconds, timeline)
        if timeline.left() <= cursor_x <= timeline.right():
            painter.setPen(QPen(QColor("#f8fafc"), 2))
            painter.drawLine(cursor_x, timeline.top() - 8, cursor_x, timeline.bottom())
            painter.setBrush(QColor("#f8fafc"))
            painter.drawEllipse(QPoint(cursor_x, timeline.top() - 8), 4, 4)

    def _draw_series(self, painter: QPainter, lane, series: WaveformSeries) -> None:  # noqa: ANN001
        envelope, color, gain, offset_seconds, loop = series
        if envelope.is_empty:
            return
        painter.setPen(QPen(color, 1))
        center = lane.center().y()
        amplitude = max(2, lane.height() / 2)
        repeats = 1
        if loop and envelope.duration_seconds > 0:
            repeats = min(100, int(self.duration_seconds / envelope.duration_seconds) + 2)
        for repeat in range(repeats):
            repeat_offset = repeat * envelope.duration_seconds if loop else 0.0
            for time_value, low, high in zip(
                envelope.times, envelope.minimums, envelope.maximums, strict=False
            ):
                seconds = time_value + offset_seconds + repeat_offset
                if not self.view_start_seconds <= seconds <= self.view_start_seconds + self.view_duration_seconds:
                    continue
                x = self._x_for_seconds(seconds, lane)
                painter.drawLine(
                    x,
                    round(center - max(-1.0, min(1.0, high * gain)) * amplitude),
                    x,
                    round(center - max(-1.0, min(1.0, low * gain)) * amplitude),
                )

    def _seconds_at_x(self, x_value: float) -> float:
        timeline = self.rect().adjusted(113, 35, -13, -11)
        ratio = max(0.0, min(1.0, (x_value - timeline.left()) / max(1, timeline.width())))
        return self.view_start_seconds + ratio * self.view_duration_seconds

    def _x_for_seconds(self, seconds: float, rect) -> int:  # noqa: ANN001
        return rect.left() + round(
            (seconds - self.view_start_seconds)
            / max(0.01, self.view_duration_seconds)
            * rect.width()
        )


class AudioMixPreviewPanel(QWidget):
    backRequested = Signal()
    openFolderRequested = Signal()
    changeMusicRequested = Signal()
    settingsChanged = Signal(object)
    renderFinished = Signal(str)
    errorOccurred = Signal(str)
    log = Signal(str)
    sourcePositionRequested = Signal(int)
    resolveTimelineRequested = Signal()

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
        self.voice_source_duration_seconds = 1.0
        self.view_start_seconds = 0.0
        self.view_window_seconds = MIX_PREVIEW_DURATION_SECONDS
        self.zoom_levels_seconds = [15.0, 30.0, MIX_PREVIEW_DURATION_SECONDS]
        self.zoom_level_index = 2
        self.cursor_seconds = 0.0
        self.preview_start_seconds = 0.0
        self.preview_render_path: Path | None = None
        self.preview_render_signature: tuple[object, ...] | None = None
        self.pending_preview_signature: tuple[object, ...] | None = None
        self.pending_preview_position_seconds = 0.0
        self.preview_render_dirty = True
        self.advanced_full_render_path: Path | None = None
        self.advanced_full_render_signature: tuple[object, ...] | None = None
        self.pending_advanced_full_play = False
        self.editable_audio_events: list[StoredAudioEvent] = []
        self.dirty_event_uids: set[str] = set()
        self.segment_line_by_sequence: dict[int, int] = {}
        self.segment_ranges_ms: list[tuple[int, int, int]] = []
        self.current_highlighted_segment: int | None = None
        self.loading_event_details = False
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_player.playbackStateChanged.connect(
            self._on_playback_state_changed
        )
        self.preview_player.positionChanged.connect(self._on_preview_position_changed)
        self.preview_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.full_mix_dialog: QDialog | None = None
        self.full_mix_dialog_status_label: QLabel | None = None
        self.full_mix_dialog_progress: QProgressBar | None = None
        self.full_mix_dialog_open_button: QPushButton | None = None
        self.full_mix_dialog_close_button: QPushButton | None = None
        self.voice_color = QColor("#2563eb")
        self.music_color = QColor("#f97316")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.mix_tabs = QTabWidget()
        self.basic_tab = QWidget()
        self.advanced_tab = QWidget()
        basic_layout = QVBoxLayout(self.basic_tab)
        basic_layout.setContentsMargins(0, 8, 0, 0)
        basic_layout.setSpacing(12)
        advanced_tab_layout = QVBoxLayout(self.advanced_tab)
        advanced_tab_layout.setContentsMargins(0, 8, 0, 0)
        advanced_tab_layout.setSpacing(12)
        self.mix_tabs.addTab(self.basic_tab, self.tr("basic", "Basic"))
        self.mix_tabs.addTab(self.advanced_tab, self.tr("advanced", "Advanced"))

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
        layout.addWidget(self.info_label)

        self.mix_graph = WaveformGraph(
            self.tr("mix_preview_waveform", "Mix"),
            self.tr("waveform_loading", "Loading waveform..."),
        )
        self.header_actions = QWidget()
        self.header_actions.setObjectName("headerActions")
        header_actions_layout = QHBoxLayout(self.header_actions)
        header_actions_layout.setContentsMargins(0, 0, 0, 0)
        header_actions_layout.setSpacing(6)
        self.change_music_button = QPushButton(
            self.tr("change_music_background", "Change Music Background")
        )
        self.change_music_button.setObjectName("inlineActionButton")
        self.change_music_button.setIcon(ui_icon("folder"))
        self.change_music_button.setIconSize(QSize(14, 14))
        self.change_music_button.clicked.connect(self.changeMusicRequested.emit)
        self.background_mute_checkbox = QCheckBox()
        self.background_mute_checkbox.setIcon(ui_icon("mute"))
        self.background_mute_checkbox.setIconSize(QSize(16, 16))
        self.background_mute_checkbox.setToolTip(
            self.tr("mute_background_music", "Mute background music")
        )
        header_actions_layout.addWidget(self.change_music_button)
        header_actions_layout.addWidget(self.background_mute_checkbox)
        self.header_actions.adjustSize()
        self.mix_graph.set_header_action_button(self.header_actions)

        self.mix_graph.cursorChanged.connect(self._set_shared_cursor)
        card_layout.addWidget(self.mix_graph)
        playback_row = QHBoxLayout()
        playback_row.setSpacing(8)
        self.play_cursor_button = QPushButton(self.tr("play", "Play"))
        self.play_cursor_button.setIcon(ui_icon("play"))
        self.play_cursor_button.setIconSize(QSize(18, 18))
        self.play_cursor_button.clicked.connect(self._play_preview)
        self.pause_button = QPushButton(self.tr("pause", "Pause"))
        self.pause_button.setIcon(ui_icon("pause"))
        self.pause_button.setIconSize(QSize(18, 18))
        self.pause_button.clicked.connect(self._pause_playback)
        self.stop_button = QPushButton(self.tr("stop", "Stop"))
        self.stop_button.setIcon(ui_icon("stop", color=ICON_DANGER))
        self.stop_button.setIconSize(QSize(18, 18))
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.clicked.connect(self._stop_playback)
        for button in (
            self.play_cursor_button,
            self.pause_button,
            self.stop_button,
        ):
            button.setEnabled(False)
            playback_row.addWidget(button)
        playback_row.addStretch(1)
        card_layout.addLayout(playback_row)
        basic_layout.addWidget(card, 1)

        self.advanced_frame = QFrame()
        self.advanced_frame.setObjectName("card")
        advanced_layout = QVBoxLayout(self.advanced_frame)
        advanced_layout.setContentsMargins(16, 14, 16, 14)
        advanced_layout.setSpacing(12)
        advanced_title = QLabel(
            self.tr(
                "advanced_mix_editor",
                "Advanced audio timeline",
            )
        )
        advanced_title.setObjectName("sectionLabel")
        advanced_layout.addWidget(advanced_title)

        advanced_help = QLabel(
            self.tr(
                "advanced_mix_help",
                "Edit PLAY events against the generated narration timeline, then render the complete mix.",
            )
        )
        advanced_help.setObjectName("helperLabel")
        advanced_help.setWordWrap(True)
        advanced_layout.addWidget(advanced_help)

        self.track_volume_spins: dict[str, QDoubleSpinBox] = {}
        self.track_mute_checks: dict[str, QCheckBox] = {}
        self.track_solo_checks: dict[str, QCheckBox] = {}
        for track in ("voice", "background", "music", "ambient", "sfx"):
            volume = QDoubleSpinBox()
            volume.setRange(-36.0, 12.0)
            volume.setDecimals(1)
            volume.setSingleStep(0.5)
            volume.setSuffix(" dB")
            mute = QCheckBox()
            solo = QCheckBox()
            self.track_volume_spins[track] = volume
            self.track_mute_checks[track] = mute
            self.track_solo_checks[track] = solo
            volume.valueChanged.connect(
                lambda value, item=track: self._on_track_volume_changed(item, value)
            )
            mute.stateChanged.connect(self._on_controls_changed)
            solo.toggled.connect(
                lambda checked, item=track: self._on_track_solo_changed(
                    item,
                    checked,
                )
            )

        advanced_panels = QHBoxLayout()
        advanced_panels.setSpacing(10)
        self.segment_timeline_view = QPlainTextEdit()
        self.segment_timeline_view.setReadOnly(True)
        self.segment_timeline_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.segment_timeline_view.setFixedWidth(84)
        self.segment_timeline_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.segment_timeline_view.setObjectName("timelineTextPanel")

        self.segment_text_view = QPlainTextEdit()
        self.segment_text_view.setReadOnly(True)
        self.segment_text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.segment_text_view.setObjectName("segmentTextPanel")
        self.segment_text_view.setMinimumHeight(320)
        self.segment_text_view.verticalScrollBar().valueChanged.connect(
            self._sync_advanced_timeline_scroll
        )
        self.segment_timeline_view.verticalScrollBar().valueChanged.connect(
            self._sync_advanced_text_scroll
        )

        self.audio_event_list = QListWidget()
        self.audio_event_list.setObjectName("sfxEventPanel")
        self.audio_event_list.setMinimumWidth(260)
        self.audio_event_list.currentItemChanged.connect(
            self._on_audio_event_selection_changed
        )
        advanced_panels.addWidget(self.segment_timeline_view)
        advanced_panels.addWidget(self.segment_text_view, 1)
        advanced_panels.addWidget(self.audio_event_list)
        advanced_layout.addLayout(advanced_panels, 1)

        advanced_playback_row = QHBoxLayout()
        advanced_playback_row.setSpacing(8)
        self.advanced_play_button = QPushButton(self.tr("play", "Play"))
        self.advanced_play_button.setIcon(ui_icon("play"))
        self.advanced_play_button.setIconSize(QSize(18, 18))
        self.advanced_play_button.clicked.connect(self._play_advanced_full_mix)
        self.advanced_pause_button = QPushButton(self.tr("pause", "Pause"))
        self.advanced_pause_button.setIcon(ui_icon("pause"))
        self.advanced_pause_button.setIconSize(QSize(18, 18))
        self.advanced_pause_button.clicked.connect(self._pause_playback)
        self.advanced_stop_button = QPushButton(self.tr("stop", "Stop"))
        self.advanced_stop_button.setIcon(ui_icon("stop", color=ICON_DANGER))
        self.advanced_stop_button.setIconSize(QSize(18, 18))
        self.advanced_stop_button.setObjectName("dangerButton")
        self.advanced_stop_button.clicked.connect(self._stop_playback)
        self.apply_event_changes_button = QPushButton(
            self.tr("apply_or_render_full_mix", "Apply changes / render full mix")
        )
        self.apply_event_changes_button.setIcon(ui_icon("render"))
        self.apply_event_changes_button.setIconSize(QSize(18, 18))
        self.apply_event_changes_button.clicked.connect(self._render_full_mix)
        for button in (
            self.advanced_play_button,
            self.advanced_pause_button,
            self.advanced_stop_button,
        ):
            button.setEnabled(False)
            advanced_playback_row.addWidget(button)
        advanced_playback_row.addStretch(1)
        advanced_playback_row.addWidget(self.apply_event_changes_button)
        advanced_layout.addLayout(advanced_playback_row)

        self.event_details_frame = QFrame()
        self.event_details_frame.setObjectName("eventDetailsPanel")
        event_details_layout = QGridLayout(self.event_details_frame)
        event_details_layout.setContentsMargins(12, 10, 12, 10)
        event_details_layout.setHorizontalSpacing(10)
        event_details_layout.setVerticalSpacing(8)
        self.event_file_edit = QLineEdit()
        self.event_track_combo = QComboBox()
        for track, label in (
            ("sfx", self.tr("sfx", "SFX")),
            ("music", self.tr("markup_music", "Markup music")),
            ("ambient", self.tr("ambient", "Ambient")),
        ):
            self.event_track_combo.addItem(label, track)
        self.event_enabled_checkbox = QCheckBox(self.tr("enabled", "Enabled"))
        self.event_loop_checkbox = QCheckBox(self.tr("loop", "Loop"))
        self.event_trim_checkbox = QCheckBox(
            self.tr("trim_silence", "Trim silence")
        )
        self.event_volume_spin = self._event_db_spin(-36.0, 12.0, 0.0)
        self.event_start_spin = self._event_seconds_spin(0.0)
        self.event_duration_spin = self._event_seconds_spin(-1.0, minimum=-1.0)
        self.event_duration_spin.setSpecialValueText(self.tr("auto", "auto"))
        self.event_fade_in_spin = self._event_seconds_spin(0.0)
        self.event_fade_out_spin = self._event_seconds_spin(0.0)
        self.event_pan_spin = QDoubleSpinBox()
        self.event_pan_spin.setRange(-1.0, 1.0)
        self.event_pan_spin.setSingleStep(0.1)
        self.event_pan_spin.setDecimals(2)
        self.event_duck_spin = self._event_db_spin(0.0, 36.0, 0.0)
        self.event_status_label = QLabel(
            self.tr("select_fx_event", "Select an FX event to edit.")
        )
        self.event_status_label.setObjectName("helperLabel")
        event_details_layout.addWidget(
            self._control_label("file", self.tr("file", "File")), 0, 0
        )
        event_details_layout.addWidget(self.event_file_edit, 0, 1, 1, 3)
        event_details_layout.addWidget(QLabel(self.tr("track", "Track")), 0, 4)
        event_details_layout.addWidget(self.event_track_combo, 0, 5)
        event_details_layout.addWidget(self.event_enabled_checkbox, 1, 0)
        event_details_layout.addWidget(self.event_loop_checkbox, 1, 1)
        event_details_layout.addWidget(self.event_trim_checkbox, 1, 2)
        event_details_layout.addWidget(QLabel(self.tr("volume", "Volume")), 2, 0)
        event_details_layout.addWidget(self.event_volume_spin, 2, 1)
        event_details_layout.addWidget(QLabel(self.tr("start", "Start")), 2, 2)
        event_details_layout.addWidget(self.event_start_spin, 2, 3)
        event_details_layout.addWidget(QLabel(self.tr("duration", "Duration")), 2, 4)
        event_details_layout.addWidget(self.event_duration_spin, 2, 5)
        event_details_layout.addWidget(QLabel(self.tr("fade_in", "Fade in")), 3, 0)
        event_details_layout.addWidget(self.event_fade_in_spin, 3, 1)
        event_details_layout.addWidget(QLabel(self.tr("fade_out", "Fade out")), 3, 2)
        event_details_layout.addWidget(self.event_fade_out_spin, 3, 3)
        event_details_layout.addWidget(QLabel(self.tr("pan", "Pan")), 3, 4)
        event_details_layout.addWidget(self.event_pan_spin, 3, 5)
        event_details_layout.addWidget(
            QLabel(self.tr("duck_on_voice", "Duck on voice")), 4, 0
        )
        event_details_layout.addWidget(self.event_duck_spin, 4, 1)
        event_details_layout.addWidget(self.event_status_label, 4, 2, 1, 4)
        advanced_layout.addWidget(self.event_details_frame)
        advanced_tab_layout.addWidget(self.advanced_frame, 1)

        self._connect_event_detail_controls()

        controls = QFrame()
        controls.setObjectName("card")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(10)

        self.voice_slider, self.voice_spin = self._volume_control(-12, 6, 0)
        self.music_slider, self.music_spin = self._volume_control(-36, 0, -7)
        self.voice_spin.valueChanged.connect(
            lambda value: self._sync_advanced_track_volume("voice", value)
        )
        self.music_spin.valueChanged.connect(
            lambda value: self._sync_advanced_track_volume("background", value)
        )
        self.voice_offset_spin = self._milliseconds_spin(-300000, 300000, 2000)
        self.music_tail_spin = self._milliseconds_spin(0, 600000, 2000)
        self.fade_in_spin = self._seconds_spin(1.0)
        self.fade_out_spin = self._seconds_spin(1.0)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setIcon(ui_icon("zoom_out"))
        self.zoom_out_button.setIconSize(QSize(18, 18))
        self.zoom_out_button.setToolTip(self.tr("zoom_out", "Zoom out"))
        self.zoom_out_button.setFixedWidth(40)
        self.zoom_in_button = QPushButton()
        self.zoom_in_button.setIcon(ui_icon("zoom_in"))
        self.zoom_in_button.setIconSize(QSize(18, 18))
        self.zoom_in_button.setToolTip(self.tr("zoom_in", "Zoom in"))
        self.zoom_in_button.setFixedWidth(40)
        playback_row.addWidget(self.zoom_in_button)
        playback_row.addWidget(self.zoom_out_button)
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
        self.render_button = QPushButton(
            self.tr("render_full_mix", "Render full mix")
        )
        self.render_button.setIcon(ui_icon("render"))
        self.render_button.setIconSize(QSize(18, 18))
        self.render_button.setObjectName("primaryButton")
        self.render_button.clicked.connect(self._render_full_mix)
        button_row.addStretch(1)
        button_row.addWidget(self.render_button)
        controls_layout.addLayout(button_row, 6, 0, 1, 5)
        basic_layout.addWidget(controls)
        layout.addWidget(self.mix_tabs, 1)

        for widget in (
            self.voice_spin,
            self.music_spin,
            self.voice_offset_spin,
            self.music_tail_spin,
            self.fade_in_spin,
            self.fade_out_spin,
            self.ducking_checkbox,
            self.ducking_strength_combo,
            self.background_mute_checkbox,
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
    def _event_db_spin(
        minimum: float,
        maximum: float,
        value: float,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" dB")
        spin.setValue(value)
        return spin

    @staticmethod
    def _event_seconds_spin(
        value: float,
        *,
        minimum: float = 0.0,
        maximum: float = 3600.0,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def _connect_event_detail_controls(self) -> None:
        self.event_file_edit.textEdited.connect(self._on_event_detail_changed)
        self.event_track_combo.currentIndexChanged.connect(self._on_event_detail_changed)
        for widget in (
            self.event_enabled_checkbox,
            self.event_loop_checkbox,
            self.event_trim_checkbox,
        ):
            widget.stateChanged.connect(self._on_event_detail_changed)
        for widget in (
            self.event_volume_spin,
            self.event_start_spin,
            self.event_duration_spin,
            self.event_fade_in_spin,
            self.event_fade_out_spin,
            self.event_pan_spin,
            self.event_duck_spin,
        ):
            widget.valueChanged.connect(self._on_event_detail_changed)

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

    def _on_track_solo_changed(self, track: str, checked: bool) -> None:
        if checked:
            for other_track, checkbox in self.track_solo_checks.items():
                if other_track == track:
                    continue
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
        self._on_controls_changed()

    def _on_track_volume_changed(self, track: str, value: float) -> None:
        target = None
        if track == "voice" and hasattr(self, "voice_spin"):
            target = self.voice_spin
        elif track == "background" and hasattr(self, "music_spin"):
            target = self.music_spin
        if target is not None and abs(target.value() - value) > 0.001:
            target.setValue(value)
            return
        self._on_controls_changed()

    def _sync_advanced_track_volume(self, track: str, value: float) -> None:
        spin = self.track_volume_spins.get(track)
        if spin is None or abs(spin.value() - value) <= 0.001:
            return
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def _refresh_audio_event_table(self) -> None:
        self._refresh_advanced_timeline()
        self._refresh_audio_event_list()
        self.mix_tabs.setTabText(
            1,
            self.tr(
                "advanced_mix_tab_count",
                "Advanced ({count} events)",
                count=len(self.editable_audio_events),
            ),
        )

    def _refresh_advanced_timeline(self) -> None:
        segments = self.context.segments if self.context is not None else ()
        timeline_lines: list[str] = []
        text_lines: list[str] = []
        self.segment_line_by_sequence = {}
        self.segment_ranges_ms = []
        cursor_ms = 0
        for segment in segments:
            before_ms = (
                segment.resolved_pause_before_ms
                if segment.resolved_pause_before_ms is not None
                else segment.markup_pause_before_ms
            )
            if before_ms:
                cursor_ms += max(0, int(before_ms))
                timeline_lines.append("")
                text_lines.append("")
            line_index = len(text_lines)
            self.segment_line_by_sequence[segment.sequence_index] = line_index
            start_ms = cursor_ms
            end_ms = start_ms + max(1, segment.duration_ms)
            self.segment_ranges_ms.append(
                (segment.sequence_index, start_ms, end_ms)
            )
            timeline_lines.append(self._format_time(start_ms / 1000))
            text_lines.append(segment.source_text.replace("\n", " ").strip())
            cursor_ms = end_ms
            after_ms = (
                segment.resolved_pause_after_ms
                if segment.resolved_pause_after_ms is not None
                else segment.markup_pause_after_ms
            )
            if after_ms:
                cursor_ms += max(0, int(after_ms))
                timeline_lines.append("")
                text_lines.append("")
        self.segment_timeline_view.setPlainText("\n".join(timeline_lines))
        self.segment_text_view.setPlainText("\n".join(text_lines))
        self._highlight_advanced_segment(None)

    def _refresh_audio_event_list(self) -> None:
        selected_uid = self._selected_audio_event_uid()
        self.audio_event_list.blockSignals(True)
        self.audio_event_list.clear()
        play_events = [
            event for event in self.editable_audio_events if event.command_type == "play"
        ]
        for event in play_events:
            label = Path(event.file_reference or event.file_path).name
            if not label:
                label = event.event_id or event.event_uid
            if event.resolved_time_ms is not None:
                label = f"{self._format_time(event.resolved_time_ms / 1000)}  {label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, event.event_uid)
            item.setForeground(QColor("#b45309"))
            if event.event_uid in self.dirty_event_uids:
                item.setIcon(ui_icon("warning", danger=True))
                item.setText(f"! {label}")
            self.audio_event_list.addItem(item)
            if event.event_uid == selected_uid:
                self.audio_event_list.setCurrentItem(item)
        self.audio_event_list.blockSignals(False)
        if self.audio_event_list.currentItem() is None and self.audio_event_list.count():
            self.audio_event_list.setCurrentRow(0)
        if self.audio_event_list.count() == 0:
            self._load_event_details(None)
        else:
            self._load_event_details(self._selected_audio_event())

    def _selected_audio_event_uid(self) -> str:
        item = self.audio_event_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _selected_audio_event(self) -> StoredAudioEvent | None:
        selected_uid = self._selected_audio_event_uid()
        if not selected_uid:
            return None
        for event in self.editable_audio_events:
            if event.event_uid == selected_uid:
                return event
        return None

    def _on_audio_event_selection_changed(self, current, _previous) -> None:  # noqa: ANN001
        uid = "" if current is None else str(current.data(Qt.ItemDataRole.UserRole) or "")
        event = next(
            (item for item in self.editable_audio_events if item.event_uid == uid),
            None,
        )
        self._load_event_details(event)

    def _load_event_details(self, event: StoredAudioEvent | None) -> None:
        self.loading_event_details = True
        try:
            enabled = event is not None and event.command_type == "play"
            for widget in (
                self.event_file_edit,
                self.event_track_combo,
                self.event_enabled_checkbox,
                self.event_loop_checkbox,
                self.event_trim_checkbox,
                self.event_volume_spin,
                self.event_start_spin,
                self.event_duration_spin,
                self.event_fade_in_spin,
                self.event_fade_out_spin,
                self.event_pan_spin,
                self.event_duck_spin,
            ):
                widget.setEnabled(enabled)
            if event is None:
                self.event_file_edit.setText("")
                self.event_status_label.setText(
                    self.tr("select_fx_event", "Select an FX event to edit.")
                )
                return
            self.event_file_edit.setText(event.file_reference or Path(event.file_path).name)
            index = self.event_track_combo.findData(event.track)
            self.event_track_combo.setCurrentIndex(max(0, index))
            self.event_enabled_checkbox.setChecked(event.enabled)
            self.event_loop_checkbox.setChecked(event.loop)
            self.event_trim_checkbox.setChecked(event.trim_silence)
            self.event_volume_spin.setValue(event.volume_db)
            self.event_start_spin.setValue(event.source_start_ms / 1000)
            self.event_duration_spin.setValue(
                -1.0 if event.duration_ms is None else event.duration_ms / 1000
            )
            self.event_fade_in_spin.setValue(event.fade_in_ms / 1000)
            self.event_fade_out_spin.setValue(event.fade_out_ms / 1000)
            self.event_pan_spin.setValue(event.pan)
            self.event_duck_spin.setValue(event.duck_db)
            status = event.resolution_status
            if event.event_uid in self.dirty_event_uids:
                status = f"{status} \u00b7 {self.tr('pending_render', 'pending render')}"
            self.event_status_label.setText(status)
        finally:
            self.loading_event_details = False

    def set_context(self, context: AudioMixPreviewContext) -> None:
        self.context = context
        self.editable_audio_events = list(context.audio_events)
        self.dirty_event_uids.clear()
        self.advanced_full_render_path = None
        self.advanced_full_render_signature = None
        self.pending_advanced_full_play = False
        self.voice_envelope = None
        self.music_envelope = None
        self.voice_source_duration_seconds = 1.0
        self.cursor_seconds = 0.0
        self._mark_preview_dirty(clear_cached_file=True)
        self._apply_settings(context.settings)
        self._refresh_audio_event_table()
        self.info_label.setText(
            self.tr(
                "mix_preview_loading",
                "Loading waveform preview for {file}...",
                file=context.voice_path.name,
            )
        )
        self.mix_graph.set_waveforms([], 1)
        self._set_playback_controls_enabled(False)
        self._set_advanced_playback_controls_enabled(False)
        self.render_button.setEnabled(False)
        self.apply_event_changes_button.setEnabled(False)
        self._load_waveforms()

    def clear_context(self) -> None:
        self._finish_waveform_thread()
        self.context = None
        self.editable_audio_events = []
        self.dirty_event_uids.clear()
        self.advanced_full_render_path = None
        self.advanced_full_render_signature = None
        self.pending_advanced_full_play = False
        self.voice_envelope = None
        self.music_envelope = None
        self.voice_source_duration_seconds = 1.0
        self.cursor_seconds = 0.0
        self._mark_preview_dirty(clear_cached_file=True)
        self.info_label.setText(
            self.tr(
                "mix_preview_no_audio",
                "Generate or open an audiobook before previewing the mix.",
            )
        )
        self.mix_graph.set_waveforms([], 1)
        self.segment_timeline_view.clear()
        self.segment_text_view.clear()
        self.segment_line_by_sequence = {}
        self.segment_ranges_ms = []
        self.current_highlighted_segment = None
        self._set_playback_controls_enabled(False)
        self._set_advanced_playback_controls_enabled(False)
        self.render_button.setEnabled(False)
        self.apply_event_changes_button.setEnabled(False)
        self.audio_event_list.clear()
        self._load_event_details(None)
        self.mix_tabs.setTabText(
            1,
            self.tr("advanced_mix_tab_count", "Advanced ({count} events)", count=0),
        )

    def current_settings(self) -> AudioMixSettings:
        current = self.context.settings if self.context else AudioMixSettings()
        solo_track = next(
            (
                track
                for track, checkbox in self.track_solo_checks.items()
                if checkbox.isChecked()
            ),
            "",
        )
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
            markup_music_volume_db=self.track_volume_spins["music"].value(),
            ambient_volume_db=self.track_volume_spins["ambient"].value(),
            sfx_volume_db=self.track_volume_spins["sfx"].value(),
            voice_muted=self.track_mute_checks["voice"].isChecked(),
            background_music_muted=self.background_mute_checkbox.isChecked(),
            markup_music_muted=self.track_mute_checks["music"].isChecked(),
            ambient_muted=self.track_mute_checks["ambient"].isChecked(),
            sfx_muted=self.track_mute_checks["sfx"].isChecked(),
            solo_track=solo_track,
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
        self.background_mute_checkbox.blockSignals(True)
        for widget in (
            *self.track_volume_spins.values(),
            *self.track_mute_checks.values(),
            *self.track_solo_checks.values(),
        ):
            widget.blockSignals(True)
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
        self.track_volume_spins["music"].setValue(settings.markup_music_volume_db)
        self.track_volume_spins["ambient"].setValue(settings.ambient_volume_db)
        self.track_volume_spins["sfx"].setValue(settings.sfx_volume_db)
        self.track_volume_spins["voice"].setValue(settings.voice_volume_db)
        self.track_volume_spins["background"].setValue(settings.music_volume_db)
        self.track_mute_checks["voice"].setChecked(settings.voice_muted)
        self.track_mute_checks["background"].setChecked(
            settings.background_music_muted
        )
        self.background_mute_checkbox.setChecked(settings.background_music_muted)
        self.track_mute_checks["music"].setChecked(settings.markup_music_muted)
        self.track_mute_checks["ambient"].setChecked(settings.ambient_muted)
        self.track_mute_checks["sfx"].setChecked(settings.sfx_muted)
        for track, checkbox in self.track_solo_checks.items():
            checkbox.setChecked(settings.solo_track == track)
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
        self.background_mute_checkbox.blockSignals(False)
        for widget in (
            *self.track_volume_spins.values(),
            *self.track_mute_checks.values(),
            *self.track_solo_checks.values(),
        ):
            widget.blockSignals(False)

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
            MIX_PREVIEW_DURATION_SECONDS,
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
        self.log.emit("Audio Mix: waveform data ready for drawing.")
        self.voice_envelope = voice
        self.music_envelope = music
        self.voice_source_duration_seconds = (
            voice.source_duration_seconds or voice.duration_seconds
        )
        self.info_label.setText(
            self.tr(
                "mix_preview_ready",
                "Move the volume sliders to check whether music competes with the voice.",
            )
        )
        self._update_waveforms()
        self._set_playback_controls_enabled(True)
        self._set_advanced_playback_controls_enabled(True)
        self.render_button.setEnabled(True)
        self.apply_event_changes_button.setEnabled(True)

    def _on_waveform_failed(self, message: str) -> None:
        self.log.emit(f"Audio Mix: waveform loading failed: {message}")
        self.info_label.setText(
            self.tr("mix_preview_error", "Waveform preview failed: {message}", message=message)
        )
        self.errorOccurred.emit(message)
        self._set_playback_controls_enabled(False)
        self._set_advanced_playback_controls_enabled(False)
        self.render_button.setEnabled(False)
        self.apply_event_changes_button.setEnabled(False)

    def _on_controls_changed(self, *_args) -> None:
        self._sync_background_mute_controls()
        self._mark_preview_dirty()
        self._mark_advanced_full_render_dirty()
        self.settingsChanged.emit(self.current_settings())
        self._update_waveforms()

    def _sync_background_mute_controls(self) -> None:
        sender = self.sender()
        advanced_checkbox = self.track_mute_checks.get("background")
        if advanced_checkbox is None:
            return
        if sender is self.background_mute_checkbox:
            target = self.background_mute_checkbox.isChecked()
            if advanced_checkbox.isChecked() != target:
                advanced_checkbox.blockSignals(True)
                advanced_checkbox.setChecked(target)
                advanced_checkbox.blockSignals(False)
        elif sender is advanced_checkbox:
            target = advanced_checkbox.isChecked()
            if self.background_mute_checkbox.isChecked() != target:
                self.background_mute_checkbox.blockSignals(True)
                self.background_mute_checkbox.setChecked(target)
                self.background_mute_checkbox.blockSignals(False)

    def _on_event_detail_changed(self, *_args) -> None:
        if self.loading_event_details:
            return
        event = self._selected_audio_event()
        if event is None:
            return
        file_reference = self.event_file_edit.text().strip()
        resolved_path = self._resolve_event_file(file_reference)
        duration_ms = (
            None
            if self.event_duration_spin.value() < 0
            else round(self.event_duration_spin.value() * 1000)
        )
        resolution_status = event.resolution_status
        if not file_reference or resolved_path is None:
            resolution_status = "missing"
        elif resolution_status == "missing":
            resolution_status = "resolved"
        updated = replace(
            event,
            file_reference=file_reference,
            file_path=str(resolved_path) if resolved_path is not None else "",
            track=str(self.event_track_combo.currentData() or "sfx"),
            enabled=self.event_enabled_checkbox.isChecked(),
            source_start_ms=round(self.event_start_spin.value() * 1000),
            duration_ms=duration_ms,
            volume_db=self.event_volume_spin.value(),
            loop=self.event_loop_checkbox.isChecked(),
            fade_in_ms=round(self.event_fade_in_spin.value() * 1000),
            fade_out_ms=round(self.event_fade_out_spin.value() * 1000),
            pan=self.event_pan_spin.value(),
            duck_db=self.event_duck_spin.value(),
            trim_silence=self.event_trim_checkbox.isChecked(),
            resolution_status=resolution_status,
        )
        self._replace_editable_event(updated)
        self.dirty_event_uids.add(updated.event_uid)
        self._mark_preview_dirty()
        self._mark_advanced_full_render_dirty()
        self._refresh_audio_event_list()
        self._load_event_details(updated)

    def _replace_editable_event(self, updated: StoredAudioEvent) -> None:
        self.editable_audio_events = [
            updated if event.event_uid == updated.event_uid else event
            for event in self.editable_audio_events
        ]

    def _resolve_event_file(self, file_reference: str) -> Path | None:
        if not file_reference:
            return None
        settings = self.context.project_settings if self.context is not None else {}
        project_dir = self.context.project_dir if self.context is not None else None
        return resolve_audio_reference(
            file_reference,
            settings or {},
            project_dir=project_dir,
        )

    def _effective_timeline_clips(self) -> tuple[ResolvedAudioClip, ...]:
        events = self.editable_audio_events
        stops = {
            event.target_event_uid: event
            for event in events
            if event.command_type == "stop"
            and event.enabled
            and event.resolution_status == "resolved"
            and event.resolved_time_ms is not None
            and event.target_event_uid
        }
        clips: list[ResolvedAudioClip] = []
        project_duration_ms = self._advanced_project_duration_ms()
        for event in events:
            if (
                event.command_type != "play"
                or not event.enabled
                or event.resolution_status != "resolved"
                or event.resolved_time_ms is None
                or not event.file_path
            ):
                continue
            stop = stops.get(event.event_uid)
            fade_out_ms = event.fade_out_ms
            stop_duration_ms: int | None = None
            if stop is not None and stop.resolved_time_ms is not None:
                if stop.fade_out_ms >= 0:
                    fade_out_ms = stop.fade_out_ms
                stop_duration_ms = max(
                    1,
                    stop.resolved_time_ms - event.resolved_time_ms + fade_out_ms,
                )
            playback_duration_ms = event.duration_ms
            if stop_duration_ms is not None:
                playback_duration_ms = (
                    stop_duration_ms
                    if playback_duration_ms is None
                    else min(playback_duration_ms, stop_duration_ms)
                )
            if event.loop and playback_duration_ms is None:
                playback_duration_ms = max(
                    1,
                    min(24 * 60 * 60 * 1000, project_duration_ms - event.resolved_time_ms),
                )
            clips.append(
                ResolvedAudioClip(
                    event_uid=event.event_uid,
                    event_id=event.event_id,
                    track=event.track,
                    file_path=event.file_path,
                    timeline_start_ms=event.resolved_time_ms,
                    source_start_ms=event.source_start_ms,
                    playback_duration_ms=playback_duration_ms,
                    volume_db=event.volume_db,
                    loop=event.loop,
                    fade_in_ms=event.fade_in_ms,
                    fade_out_ms=fade_out_ms,
                    pan=event.pan,
                    duck_db=event.duck_db,
                    trim_silence=event.trim_silence,
                )
            )
        return tuple(clips)

    def _render_context(self) -> AudioMixPreviewContext:
        if self.context is None:
            raise RuntimeError("Audio Mix context is not available.")
        return replace(
            self.context,
            audio_events=tuple(self.editable_audio_events),
            timeline_clips=self._effective_timeline_clips(),
        )

    def _update_waveforms(self) -> None:
        if self.voice_envelope is None:
            return
        settings = self.current_settings()
        voice_gain = db_to_gain(settings.voice_volume_db)
        music_gain = db_to_gain(settings.music_volume_db)
        if settings.voice_muted or (
            settings.solo_track and settings.solo_track != "voice"
        ):
            voice_gain = 0.0
        if settings.background_music_muted or (
            settings.solo_track and settings.solo_track != "background"
        ):
            music_gain = 0.0
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
        self.total_duration_seconds = max(
            0.01,
            min(MIX_PREVIEW_DURATION_SECONDS, duration),
        )
        mix_waveforms: list[WaveformSeries] = [
            (
                self.voice_envelope,
                self.voice_color,
                voice_gain,
                voice_offset,
                False,
            )
        ]
        if self.music_envelope is not None and music_gain > 0:
            mix_waveforms.append(
                (
                    self.music_envelope,
                    self.music_color,
                    music_gain,
                    0.0,
                    settings.loop_background,
                )
            )
        self.mix_graph.set_waveforms(mix_waveforms, self.total_duration_seconds)
        self._sync_timeline_controls()
        self._apply_view_to_graphs()

    def _set_shared_cursor(self, seconds: float) -> None:
        self.cursor_seconds = max(0.0, min(seconds, self._playback_duration_seconds()))
        self.mix_graph.set_cursor(self.cursor_seconds)
        self._highlight_advanced_segment_for_seconds(self.cursor_seconds)

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
        self.mix_graph.set_view(self.view_start_seconds, window)
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

    def _sync_advanced_timeline_scroll(self, value: int) -> None:
        scrollbar = self.segment_timeline_view.verticalScrollBar()
        scrollbar.blockSignals(True)
        scrollbar.setValue(value)
        scrollbar.blockSignals(False)

    def _sync_advanced_text_scroll(self, value: int) -> None:
        scrollbar = self.segment_text_view.verticalScrollBar()
        scrollbar.blockSignals(True)
        scrollbar.setValue(value)
        scrollbar.blockSignals(False)

    def _highlight_advanced_segment_for_seconds(self, seconds: float) -> None:
        voice_seconds = seconds - max(
            0.0,
            self.current_settings().voice_start_offset_ms / 1000,
        )
        voice_ms = round(max(0.0, voice_seconds) * 1000)
        sequence = None
        for segment_sequence, start_ms, end_ms in self.segment_ranges_ms:
            if start_ms <= voice_ms <= end_ms:
                sequence = segment_sequence
                break
        self._highlight_advanced_segment(sequence)

    def _highlight_advanced_segment(self, segment_sequence: int | None) -> None:
        if self.current_highlighted_segment == segment_sequence:
            return
        self.current_highlighted_segment = segment_sequence
        selections: list[QTextEdit.ExtraSelection] = []
        if segment_sequence is not None:
            line = self.segment_line_by_sequence.get(segment_sequence)
            if line is not None:
                cursor = QTextCursor(
                    self.segment_text_view.document().findBlockByLineNumber(line)
                )
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QColor("#fef08a"))
                selection.format.setProperty(
                    QTextFormat.Property.FullWidthSelection,
                    True,
                )
                selections.append(selection)
                self.segment_text_view.setTextCursor(cursor)
                self.segment_text_view.ensureCursorVisible()
        self.segment_text_view.setExtraSelections(selections)

    def _advanced_project_duration_ms(self) -> int:
        segment_end = max(
            (end_ms for _sequence, _start_ms, end_ms in self.segment_ranges_ms),
            default=round(self.voice_source_duration_seconds * 1000),
        )
        settings = self.current_settings()
        return max(
            1,
            round(
                max(0, settings.voice_start_offset_ms)
                + segment_end
                + settings.music_tail_ms
            ),
        )

    def _playback_duration_seconds(self) -> float:
        return max(self.total_duration_seconds, self._advanced_project_duration_ms() / 1000)

    def _mark_advanced_full_render_dirty(self) -> None:
        self.advanced_full_render_signature = None

    def _current_audio_event_signature(self) -> tuple[object, ...]:
        return tuple(
            (
                event.event_uid,
                event.command_type,
                event.file_reference,
                event.file_path,
                event.track,
                event.enabled,
                event.resolved_time_ms,
                event.resolution_status,
                event.source_start_ms,
                event.duration_ms,
                round(event.volume_db, 3),
                event.loop,
                event.fade_in_ms,
                event.fade_out_ms,
                round(event.pan, 3),
                round(event.duck_db, 3),
                event.trim_silence,
                self._file_signature(Path(event.file_path) if event.file_path else None),
            )
            for event in self.editable_audio_events
        )

    def _advanced_full_signature(self) -> tuple[object, ...] | None:
        if self.context is None:
            return None
        return (
            "advanced-full-v1",
            self._current_preview_signature(),
            self._current_audio_event_signature(),
            round(self.voice_source_duration_seconds, 3),
        )

    def _play_advanced_full_mix(self) -> None:
        if self.context is None or self.voice_envelope is None:
            return
        signature = self._advanced_full_signature()
        can_reuse = (
            signature is not None
            and not self.dirty_event_uids
            and self.advanced_full_render_signature == signature
            and self.advanced_full_render_path is not None
            and self.advanced_full_render_path.is_file()
        )
        stale_cached = (
            self.advanced_full_render_path is not None
            and self.advanced_full_render_path.is_file()
            and not can_reuse
        )
        if stale_cached:
            choice = QMessageBox.question(
                self,
                self.tr("advanced_render_stale_title", "Changes detected"),
                self.tr(
                    "advanced_render_stale_prompt",
                    "\u00bfHa habido cambios, quiere renderizar de nuevo?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._play_advanced_cached()
                return
        if can_reuse:
            self._play_advanced_cached()
            return
        self._render_advanced_full_mix_for_play()

    def _render_advanced_full_mix_for_play(self) -> None:
        if self.context is None:
            return
        output = Path(self.temp_dir.name) / (
            f"advanced_full_mix_{round(time.time() * 1000)}.mp3"
        )
        self.pending_advanced_full_play = True
        self._start_render_worker(
            FinalMixRenderWorker(
                self._render_context(),
                self.current_settings(),
                self.voice_source_duration_seconds,
                output,
            ),
            self._on_advanced_full_preview_rendered,
        )
        self.info_label.setText(
            self.tr("rendering_full_playback_mix", "Rendering full mix for playback...")
        )

    def _on_advanced_full_preview_rendered(self, path: str) -> None:
        self.advanced_full_render_path = Path(path)
        self.advanced_full_render_signature = self._advanced_full_signature()
        self.dirty_event_uids.clear()
        self._refresh_audio_event_list()
        self._load_event_details(self._selected_audio_event())
        if self.pending_advanced_full_play:
            self.pending_advanced_full_play = False
            self._play_advanced_cached()

    def _play_advanced_cached(self) -> None:
        if (
            self.advanced_full_render_path is None
            or not self.advanced_full_render_path.is_file()
        ):
            self._render_advanced_full_mix_for_play()
            return
        self.preview_start_seconds = 0.0
        self._set_shared_cursor(0.0)
        self.info_label.setText(self.tr("playing_mix", "Playing mix preview..."))
        source = QUrl.fromLocalFile(str(self.advanced_full_render_path))
        if self.preview_player.source() != source:
            self.preview_player.setSource(source)
        QTimer.singleShot(0, self.preview_player.play)

    def _mark_preview_dirty(self, clear_cached_file: bool = False) -> None:
        self.preview_render_dirty = True
        self.pending_preview_signature = None
        self.pending_preview_position_seconds = 0.0
        if clear_cached_file:
            self.preview_player.stop()
            self.preview_player.setSource(QUrl())
            self.preview_render_path = None
            self.preview_render_signature = None

    def _current_preview_signature(self) -> tuple[object, ...] | None:
        if self.context is None:
            return None
        settings = self.current_settings()
        return (
            "mix-preview-v2",
            self._file_signature(self.context.voice_path),
            self._file_signature(self.context.music_path),
            str(self.context.ffmpeg_path),
            round(settings.voice_volume_db, 3),
            round(settings.music_volume_db, 3),
            settings.voice_start_offset_ms,
            settings.music_tail_ms,
            round(settings.music_fade_in_seconds, 3),
            round(settings.music_fade_out_seconds, 3),
            settings.ducking_enabled,
            settings.ducking_strength,
            settings.loop_background,
            settings.normalize,
            settings.mp3_bitrate,
            round(settings.markup_music_volume_db, 3),
            round(settings.ambient_volume_db, 3),
            round(settings.sfx_volume_db, 3),
            settings.voice_muted,
            settings.background_music_muted,
            settings.markup_music_muted,
            settings.ambient_muted,
            settings.sfx_muted,
            settings.solo_track,
            self._current_audio_event_signature(),
            round(self.total_duration_seconds, 3),
        )

    @staticmethod
    def _file_signature(path: Path | None) -> tuple[str, int, int] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return (str(path), 0, 0)
        return (str(path), stat.st_mtime_ns, stat.st_size)

    def _can_reuse_preview_render(
        self,
        signature: tuple[object, ...] | None,
    ) -> bool:
        return (
            signature is not None
            and not self.preview_render_dirty
            and self.preview_render_signature == signature
            and self.preview_render_path is not None
            and self.preview_render_path.is_file()
        )

    def _play_cached_preview(self, position_seconds: float) -> None:
        if self.preview_render_path is None or not self.preview_render_path.is_file():
            self._mark_preview_dirty(clear_cached_file=True)
            self._play_mix_from(position_seconds)
            return
        play_position = max(
            0.0,
            min(position_seconds, max(0.0, self.total_duration_seconds - 0.1)),
        )
        self.preview_start_seconds = 0.0
        self._set_shared_cursor(play_position)
        self.info_label.setText(self.tr("playing_mix", "Playing mix preview..."))
        self.log.emit(
            "Audio Mix: reusing cached 1 minute preview "
            f"from {play_position:.2f}s."
        )
        source = QUrl.fromLocalFile(str(self.preview_render_path))
        if self.preview_player.source() != source:
            self.preview_player.setSource(source)

        def start_playback() -> None:
            self.preview_player.setPosition(round(play_position * 1000))
            self.preview_player.play()

        QTimer.singleShot(0, start_playback)

    def _play_preview(self) -> None:
        self._play_mix_from(self.cursor_seconds)

    def _play_mix_from(self, start_seconds: float) -> None:
        if self.context is None:
            return
        play_position = max(
            0.0,
            min(start_seconds, max(0.0, self.total_duration_seconds - 0.1)),
        )
        self.preview_start_seconds = 0.0
        self._set_shared_cursor(play_position)
        signature = self._current_preview_signature()
        if self._can_reuse_preview_render(signature):
            self._play_cached_preview(play_position)
            return
        duration_seconds = max(
            0.1,
            min(
                MIX_PREVIEW_DURATION_SECONDS,
                max(0.1, self.total_duration_seconds),
            ),
        )
        output = Path(self.temp_dir.name) / (
            f"mix_play_{round(time.time() * 1000)}.mp3"
        )
        self.pending_preview_signature = signature
        self.pending_preview_position_seconds = play_position
        self._start_render_worker(
            PreviewRenderWorker(
                self._render_context(),
                self.current_settings(),
                output,
                0.0,
                duration_seconds,
            ),
            self._on_preview_rendered,
        )
        self.info_label.setText(
            self.tr(
                "rendering_playback_mix",
                "Preparing 1 minute mix preview...",
            )
        )

    def _render_full_mix(self) -> None:
        if self.context is None or self.voice_envelope is None:
            return
        output_path = self._next_mix_filename(self.context.output_dir)
        self._show_full_mix_dialog(output_path)
        self._start_render_worker(
            FinalMixRenderWorker(
                self._render_context(),
                self.current_settings(),
                self.voice_source_duration_seconds,
                output_path,
            ),
            self._on_full_mix_rendered,
        )
        self.info_label.setText(self.tr("rendering_mix", "Rendering full mix..."))

    def _show_full_mix_dialog(self, output_path: Path) -> None:
        if self.full_mix_dialog is not None:
            self.full_mix_dialog.close()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("audio_mix", "Audio Mix"))
        dialog.setModal(True)
        dialog.setMinimumWidth(780)
        dialog.resize(860, 260)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        title = QLabel(self.tr("rendering_mix", "Rendering full mix..."))
        title.setObjectName("sectionLabel")
        status = QLabel(
            self.tr(
                "mix_rendering_files",
                "Rendering mixed podcast file:\n{mix}\n\nClean voice file kept:\n{voice}",
                mix=str(output_path),
                voice=str(self.context.voice_path) if self.context else "",
            )
        )
        status.setWordWrap(True)
        status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        progress = QProgressBar()
        progress.setRange(0, 0)

        button_row = QHBoxLayout()
        open_button = QPushButton(self.tr("open_output_folder", "Open output folder"))
        open_button.setIcon(ui_icon("folder"))
        open_button.setEnabled(False)
        open_button.clicked.connect(self.openFolderRequested.emit)
        close_button = QPushButton(self.tr("close", "Close"))
        close_button.setEnabled(False)
        close_button.clicked.connect(dialog.accept)
        button_row.addStretch(1)
        button_row.addWidget(open_button)
        button_row.addWidget(close_button)

        layout.addWidget(title)
        layout.addWidget(status)
        layout.addWidget(progress)
        layout.addLayout(button_row)
        dialog.finished.connect(self._clear_full_mix_dialog)

        self.full_mix_dialog = dialog
        self.full_mix_dialog_status_label = status
        self.full_mix_dialog_progress = progress
        self.full_mix_dialog_open_button = open_button
        self.full_mix_dialog_close_button = close_button
        dialog.show()

    def _start_render_worker(self, worker: QObject, success_slot) -> None:  # noqa: ANN001
        self._finish_render_thread()
        self._set_playback_controls_enabled(False)
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
        rendered_signature = self.pending_preview_signature
        play_position = self.pending_preview_position_seconds
        self.pending_preview_signature = None
        self.pending_preview_position_seconds = 0.0
        self.preview_render_path = Path(path)
        self.preview_render_signature = rendered_signature
        self.preview_render_dirty = (
            rendered_signature is None
            or rendered_signature != self._current_preview_signature()
        )
        if self.preview_render_dirty:
            self.info_label.setText(
                self.tr(
                    "mix_preview_outdated",
                    "Preview rendered, but settings changed. Press Play again to refresh.",
                )
            )
            return
        self._play_cached_preview(play_position)

    def _on_preview_position_changed(self, milliseconds: int) -> None:
        if (
            self.preview_player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            return
        seconds = self.preview_start_seconds + milliseconds / 1000
        self._set_shared_cursor(min(seconds, self._playback_duration_seconds()))

    def _on_media_status_changed(self, status) -> None:  # noqa: ANN001
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        self.preview_player.stop()
        self.preview_player.setPosition(0)
        self.preview_start_seconds = 0.0
        self._set_shared_cursor(0.0)

    def _on_full_mix_rendered(self, path: str) -> None:
        self.info_label.setText(
            self.tr("mix_saved", "Mix saved: {path}", path=path)
        )
        if self.full_mix_dialog_status_label is not None:
            self.full_mix_dialog_status_label.setText(
                self.tr(
                    "mix_render_complete",
                    "Mix render complete.\n\nMixed podcast file:\n{path}\n\nClean voice file kept:\n{voice}",
                    path=path,
                    voice=str(self.context.voice_path) if self.context else "",
                )
            )
        if self.full_mix_dialog_progress is not None:
            self.full_mix_dialog_progress.setRange(0, 100)
            self.full_mix_dialog_progress.setValue(100)
        if self.full_mix_dialog_open_button is not None:
            self.full_mix_dialog_open_button.setEnabled(True)
        if self.full_mix_dialog_close_button is not None:
            self.full_mix_dialog_close_button.setEnabled(True)
        self.advanced_full_render_path = Path(path)
        self.advanced_full_render_signature = self._advanced_full_signature()
        self.dirty_event_uids.clear()
        self._refresh_audio_event_list()
        self._load_event_details(self._selected_audio_event())
        self.renderFinished.emit(path)

    def _on_render_failed(self, message: str) -> None:
        self.info_label.setText(
            self.tr("mix_preview_error", "Waveform preview failed: {message}", message=message)
        )
        if self.full_mix_dialog_status_label is not None:
            self.full_mix_dialog_status_label.setText(
                self.tr("mix_render_failed", "Mix render failed:\n{message}", message=message)
            )
        if self.full_mix_dialog_progress is not None:
            self.full_mix_dialog_progress.setRange(0, 100)
            self.full_mix_dialog_progress.setValue(0)
        if self.full_mix_dialog_close_button is not None:
            self.full_mix_dialog_close_button.setEnabled(True)
        self.errorOccurred.emit(message)

    def _clear_full_mix_dialog(self, _result: int = 0) -> None:
        self.full_mix_dialog = None
        self.full_mix_dialog_status_label = None
        self.full_mix_dialog_progress = None
        self.full_mix_dialog_open_button = None
        self.full_mix_dialog_close_button = None

    def _render_thread_finished(self) -> None:
        self.render_thread = None
        self.render_worker = None
        has_audio = self.context is not None and self.voice_envelope is not None
        self._set_playback_controls_enabled(has_audio)
        self._set_advanced_playback_controls_enabled(has_audio)
        self.render_button.setEnabled(has_audio)
        self.apply_event_changes_button.setEnabled(has_audio)

    def _waveform_thread_finished(self) -> None:
        self.waveform_thread = None
        self.waveform_worker = None

    def _on_playback_state_changed(self, state) -> None:  # noqa: ANN001
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.info_label.setText(self.tr("playing_mix", "Playing mix preview..."))
        elif self.context is not None and self.voice_envelope is not None:
            self.info_label.setText(
                self.tr(
                    "mix_preview_ready",
                    "Move the volume sliders to check whether music competes with the voice.",
                )
            )

    def _pause_playback(self) -> None:
        self.preview_player.pause()

    def _stop_playback(self) -> None:
        self.preview_player.stop()
        self.preview_player.setPosition(0)
        self.preview_start_seconds = 0.0
        self._set_shared_cursor(0.0)

    def _set_playback_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.play_cursor_button,
            self.pause_button,
            self.stop_button,
        ):
            button.setEnabled(enabled)

    def _set_advanced_playback_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.advanced_play_button,
            self.advanced_pause_button,
            self.advanced_stop_button,
        ):
            button.setEnabled(enabled)

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

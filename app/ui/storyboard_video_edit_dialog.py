from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from app.core.storyboard_video_edit import duration, export_arguments, slice_segments, source_position
from app.ui.icons import ui_icon
from app.utils.ffmpeg_utils import find_ffmpeg


def timecode(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{minutes:02}:{seconds:02}.{milliseconds:03}"


class _VideoEditTimeline(QWidget):
    seekRequested = Signal(float)
    rangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self.position = 0.0
        self.start, self.end = 0.0, 0.0
        self.minimum_range = 0.04
        self._drag = ""
        self.filmstrip = QImage()
        self.source_duration = 0.0
        self.setMinimumHeight(110)
        self.setMouseTracking(True)

    def _x(self, seconds):
        return 16 + max(0, self.width() - 32) * seconds / max(0.001, duration(self.segments))

    def _time(self, x):
        total = duration(self.segments)
        return min(total, max(0, (x - 16) / max(1, self.width() - 32) * total))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#131b28"))
        total = duration(self.segments)
        offset = 0
        for index, (start, end) in enumerate(self.segments):
            rect = QRectF(self._x(offset), 34, self._x(offset + end - start) - self._x(offset), 44)
            p.setPen(QPen(QColor("#131b28"), 2))
            p.setBrush(QColor("#294564" if index % 2 else "#355b7d"))
            p.drawRoundedRect(rect, 3, 3)
            if not self.filmstrip.isNull() and self.source_duration:
                source_rect = QRectF(start / self.source_duration * self.filmstrip.width(), 0,
                                     (end - start) / self.source_duration * self.filmstrip.width(), self.filmstrip.height())
                p.drawImage(rect.adjusted(1, 1, -1, -1), self.filmstrip, source_rect)
                p.fillRect(rect, QColor(10, 20, 32, 75))
            if rect.width() > 60:
                p.setPen(QColor("#e3edf9"))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{index + 1}  ·  {end - start:.2f}s")
            offset += end - start
        if total:
            left, right = self._x(self.start), self._x(self.end)
            p.fillRect(QRectF(left, 33, right - left, 46), QColor(75, 164, 240, 45))
            p.setPen(QPen(QColor("#61bbff"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(left, 32, right - left, 48))
            for x in (left, right):
                p.fillRect(QRectF(x - 4, 30, 8, 52), QColor("#61bbff"))
            p.setPen(QColor("#a7b7cc"))
            for i in range(5):
                x = self._x(total * i / 4)
                p.drawText(QRectF(max(0, min(self.width() - 78, x - 38)), 85, 78, 22),
                           Qt.AlignmentFlag.AlignCenter, timecode(total * i / 4))
            # White playhead is separate from the blue selection handles.
            x = self._x(self.position)
            p.setPen(QPen(QColor("white"), 2))
            p.drawLine(QPointF(x, 12), QPointF(x, 83))
            p.fillRect(QRectF(x - 5, 8, 10, 8), QColor("white"))
        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.segments:
            return
        x, y = event.position().x(), event.position().y()
        if 28 <= y <= 82 and abs(x - self._x(self.start)) < 9:
            self._drag = "in"
        elif 28 <= y <= 82 and abs(x - self._x(self.end)) < 9:
            self._drag = "out"
        else:
            self._drag = "seek"
        self._move(x)

    def mouseMoveEvent(self, event):
        if self._drag:
            self._move(event.position().x())

    def mouseReleaseEvent(self, event):
        self._drag = ""

    def _move(self, x):
        value = self._time(x)
        if self._drag == "in":
            self.rangeChanged.emit(min(value, max(0, self.end - self.minimum_range)), self.end)
        elif self._drag == "out":
            self.rangeChanged.emit(self.start, max(value, min(duration(self.segments), self.start + self.minimum_range)))
        else:
            self.seekRequested.emit(value)


class StoryboardVideoEditDialog(QDialog):
    videoAccepted = Signal(str, str, float)

    def __init__(self, tr, scene_id: str, video_path: str, ffmpeg_path: str, parent=None):
        super().__init__(parent)
        self.tr_text, self.scene_id, self.source = tr, scene_id, str(Path(video_path).resolve())
        self.ffmpeg_path = ffmpeg_path
        self.segments = []
        self._undo, self._redo = [], []
        self._source_duration = 0.0
        self._fps, self._has_audio = 25.0, False
        self._active_segment = 0
        self._busy, self._cancel_pending = False, False
        self._process = None
        self._thumbnail_process = None
        self._target = None
        self._error_bytes = bytearray()
        self._progress_buffer = ""
        self._seeking = False
        self._playing = False
        self._probe_ready = False
        self._priming_preview = True
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(tr("storyboard_edit_video", "Edit Video"))
        screen = self.screen().availableGeometry()
        self.resize(min(1050, screen.width() - 60), min(850, screen.height() - 70))
        self._build_ui()
        self._probe()

    def _text(self, key, default, **values):
        return self.tr_text("video_editor_" + key, default, **values)

    def _button(self, key, text, icon, callback, layout):
        button = QPushButton(self._text(key, text))
        button.setIcon(ui_icon(icon))
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        file_row = QHBoxLayout()
        self.file_link_button = QPushButton(Path(self.source).name)
        self.file_link_button.setFlat(True)
        self.file_link_button.setIcon(ui_icon("video_track"))
        self.file_link_button.setToolTip(self.source)
        self.file_link_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_link_button.setStyleSheet(
            "QPushButton { color: #1769ff; text-decoration: underline; border: 0; }"
        )
        self.file_link_button.clicked.connect(self._open_current_file)
        file_row.addWidget(self.file_link_button)
        self.open_folder_button = QPushButton(self.tr_text("open_folder", "Open Folder"))
        self.open_folder_button.setIcon(ui_icon("folder"))
        self.open_folder_button.clicked.connect(self._open_current_folder)
        file_row.addWidget(self.open_folder_button)
        file_row.addStretch()
        layout.addLayout(file_row)
        help_text = QLabel(self._text("help", "Drag the white playhead to seek and the blue handles to select a range. Keep selection trims the beginning and end; Delete selection removes the selected range."))
        help_text.setWordWrap(True)
        help_text.setObjectName("helperLabel")
        layout.addWidget(help_text)
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 180)
        self.video.setStyleSheet("background: #090f1a;")
        layout.addWidget(self.video, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.video.videoSink().videoFrameChanged.connect(self._first_preview_frame)
        self.player.positionChanged.connect(self._position_changed)
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(15)
        self._playback_timer.timeout.connect(lambda: self._position_changed(self.player.position()))
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(lambda error, message: self.status.setText(message))
        self.player.setSource(QUrl.fromLocalFile(self.source))
        playback = QHBoxLayout()
        self._button("play", "Play", "play", self._play, playback)
        self._button("pause", "Pause", "pause", self._pause, playback)
        self._button("stop", "Stop", "stop", self._stop, playback)
        self._button("back_frame", "−1 frame", "back", lambda: self._seek(self.timeline.position - 1 / self._fps), playback)
        self._button("next_frame", "+1 frame", "offset", lambda: self._seek(self.timeline.position + 1 / self._fps), playback)
        mute = self._button("mute", "Mute", "mute", lambda: self.audio.setMuted(mute.isChecked()), playback)
        mute.setCheckable(True)
        playback.addStretch()
        self.time_label = QLabel("00:00.000 / 00:00.000")
        playback.addWidget(self.time_label)
        layout.addLayout(playback)
        self.timeline = _VideoEditTimeline()
        self.timeline.seekRequested.connect(self._seek)
        self.timeline.rangeChanged.connect(self._set_range)
        layout.addWidget(self.timeline)
        selection = QHBoxLayout()
        self.in_spin, self.out_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for label, spin in (("In", self.in_spin), ("Out", self.out_spin)):
            selection.addWidget(QLabel(self._text(label.lower(), label)))
            spin.setDecimals(3)
            spin.setSuffix(" s")
            selection.addWidget(spin)
        self.in_spin.valueChanged.connect(lambda value: self._set_range(value, self.timeline.end))
        self.out_spin.valueChanged.connect(lambda value: self._set_range(self.timeline.start, value))
        self._button("mark_in", "Set In", "back", lambda: self._set_range(min(self.timeline.position, self.timeline.end - 1 / self._fps), self.timeline.end), selection)
        self._button("mark_out", "Set Out", "offset", lambda: self._set_range(self.timeline.start, max(self.timeline.position, self.timeline.start + 1 / self._fps)), selection)
        self.selection_label = QLabel()
        selection.addWidget(self.selection_label, 1)
        layout.addLayout(selection)
        operations = QHBoxLayout()
        self.keep_button = self._button("keep", "Keep selection", "crop", lambda: self._edit("keep"), operations)
        self.delete_button = self._button("delete", "Delete selection", "delete", lambda: self._edit("delete"), operations)
        layout.addLayout(operations)
        history = QHBoxLayout()
        self.undo_button = self._button("undo", "Undo", "undo", lambda: self._history(False), history)
        self.redo_button = self._button("redo", "Redo", "redo", lambda: self._history(True), history)
        self.reset_button = self._button("reset", "Reset", "refresh", self._reset, history)
        history.addStretch()
        self.result_label = QLabel()
        history.addWidget(self.result_label)
        layout.addLayout(history)
        note = QLabel(self._text("save_help", "Apply saves a new video. The original and the scene's timeline duration are preserved; storyboard playback adapts to fit the scene."))
        note.setWordWrap(True)
        note.setObjectName("helperLabel")
        layout.addWidget(note)
        self.status = QLabel(self._text("loading", "Loading video…"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = self._button("cancel", "Cancel", "cancel", self.reject, footer)
        self.apply_button = self._button("apply", "Apply video", "apply", self._export, footer)
        self.apply_button.setObjectName("primaryButton")
        layout.addLayout(footer)
        for sequence, callback in (
            ("Space", self._toggle_play),
            ("Ctrl+Z", lambda: self._history(False)), ("Ctrl+Y", lambda: self._history(True)),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
        self._refresh()

    def _open_current_file(self):
        if Path(self.source).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.source))

    def _open_current_folder(self):
        folder = Path(self.source).parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _first_preview_frame(self, frame):
        if self._priming_preview and frame.isValid():
            self._priming_preview = False
            if not self._playing:
                QTimer.singleShot(0, self.player.pause)

    def _probe(self):
        try:
            self.executable = find_ffmpeg(self.ffmpeg_path)
            probe = self.executable.with_name("ffprobe" + self.executable.suffix)
            if not probe.is_file():
                resolved = shutil.which("ffprobe")
                probe = Path(resolved) if resolved else None
        except Exception as exc:
            self.status.setText(str(exc))
            return
        process = QProcess(self)
        self._process = process
        self._using_ffprobe = probe is not None
        process.setProgram(str(probe or self.executable))
        process.setArguments(
            ["-v", "error", "-show_format", "-show_streams", "-of", "json", self.source]
            if probe else ["-hide_banner", "-i", self.source, "-map", "0:v:0", "-frames:v", "0", "-f", "null", "-"]
        )
        process.finished.connect(self._probed)
        process.errorOccurred.connect(self._process_error)
        process.start()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finish_failure(self._process.errorString() if self._process else "Could not start process.")

    def _probed(self, code, status):
        process = self._process
        if process is None:
            return
        raw = bytes(process.readAllStandardOutput())
        error = bytes(process.readAllStandardError()).decode(errors="replace")
        self._process = None
        process.deleteLater()
        if self._cancel_pending:
            super().reject()
            return
        try:
            if code != 0:
                raise ValueError(error)
            if self._using_ffprobe:
                data = json.loads(raw)
            else:
                length = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", error)
                if not length:
                    raise ValueError("Could not determine video duration.")
                seconds = int(length[1]) * 3600 + int(length[2]) * 60 + float(length[3])
                rate = re.search(r"Video:.*?([\d.]+) fps", error)
                data = {"format": {"duration": seconds}, "streams": [
                    {"codec_type": "video", "avg_frame_rate": rate[1] if rate else "25"}
                ] + ([{"codec_type": "audio"}] if "Audio:" in error else [])}
            video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
            self._source_duration = float(video.get("duration") or data["format"]["duration"])
            if not math.isfinite(self._source_duration) or self._source_duration <= 0:
                raise ValueError("Invalid video duration.")
            parts = str(video.get("avg_frame_rate", "25/1")).split("/")
            self._fps = float(parts[0]) / max(float(parts[1]) if len(parts) > 1 else 1, 0.001)
            self._fps = self._fps if 0 < self._fps <= 240 else 25
            self._has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
            self.timeline.minimum_range = min(1 / self._fps, self._source_duration)
            self.timeline.source_duration = self._source_duration
            self._probe_ready = True
            self.segments = [(0.0, self._source_duration)]
            self.timeline.end = self._source_duration
            self.in_spin.setSingleStep(1 / self._fps)
            self.out_spin.setSingleStep(1 / self._fps)
            self._set_range(self._source_duration * 0.1, self._source_duration * 0.9)
            self.status.clear()
            self._refresh()
            self._seek(0)
            if self._priming_preview:
                self.player.play()
            self._load_filmstrip()
        except Exception as exc:
            self.status.setText(self._text("load_error", "Could not read video: {error}", error=str(exc)))

    def _load_filmstrip(self):
        process = QProcess(self)
        self._thumbnail_process = process
        self._refresh()
        process.setProgram(str(self.executable))
        process.setArguments([
            "-hide_banner", "-loglevel", "error", "-i", self.source, "-an",
            "-vf", f"fps={8 / self._source_duration:.9f},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2,tile=8x1",
            "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
        ])
        def finished(code, status):
            if self._thumbnail_process is not process:
                return
            if code == 0:
                self.timeline.filmstrip = QImage.fromData(bytes(process.readAllStandardOutput()), "PNG")
                self.timeline.update()
            self._thumbnail_process = None
            process.deleteLater()
            self._refresh()
            if self._cancel_pending and self._process is None:
                super(StoryboardVideoEditDialog, self).reject()
        process.finished.connect(finished)
        process.errorOccurred.connect(lambda error: finished(-1, None) if error == QProcess.ProcessError.FailedToStart else None)
        process.start()

    def _set_range(self, start, end):
        if self._busy:
            return
        total = duration(self.segments)
        start = round(start * self._fps) / self._fps
        end = round(end * self._fps) / self._fps
        start = max(0, min(start, max(0, total - self.timeline.minimum_range)))
        end = min(total, max(start + self.timeline.minimum_range, end))
        self.timeline.start, self.timeline.end = start, end
        self._refresh()

    def _refresh(self):
        total = duration(self.segments)
        self.timeline.segments = self.segments
        self.timeline.position = min(self.timeline.position, total)
        self.timeline.start = min(self.timeline.start, total)
        self.timeline.end = min(self.timeline.end, total)
        for spin, value in ((self.in_spin, self.timeline.start), (self.out_spin, self.timeline.end)):
            spin.blockSignals(True)
            spin.setRange(0, total)
            spin.setValue(value)
            spin.setEnabled(not self._busy and bool(total))
            spin.blockSignals(False)
        selected = self.timeline.end - self.timeline.start
        for button in (self.keep_button, self.delete_button):
            button.setEnabled(not self._busy and selected > 0.000001)
        self.undo_button.setEnabled(not self._busy and bool(self._undo))
        self.redo_button.setEnabled(not self._busy and bool(self._redo))
        self.reset_button.setEnabled(not self._busy and self._probe_ready)
        self.apply_button.setEnabled(not self._busy and self._thumbnail_process is None and bool(total) and self.segments != [(0.0, self._source_duration)])
        self.timeline.setEnabled(not self._busy)
        self.result_label.setText(self._text("duration", "Result: {time} · {count} clips", time=timecode(total), count=len(self.segments)))
        self.selection_label.setText(self._text("selection", "Selected: {time}", time=timecode(selected)))
        self.time_label.setText(f"{timecode(self.timeline.position)} / {timecode(total)}")
        self.timeline.update()

    def _snapshot(self):
        return (list(self.segments), self.timeline.position, self.timeline.start, self.timeline.end)

    def _edit(self, operation):
        if self._busy or not self._probe_ready or operation not in {"keep", "delete"}:
            return
        start, end, position = self.timeline.start, self.timeline.end, self.timeline.position
        total = duration(self.segments)
        position = min(total, round(position * self._fps) / self._fps)
        if end <= start:
            return
        self._pause()
        before = self._snapshot()
        if operation == "keep":
            result = slice_segments(self.segments, start, end)
            position = max(0, position - start)
        else:
            result = slice_segments(self.segments, 0, start) + slice_segments(self.segments, end, total)
            position = start
        if result == self.segments:
            return
        self._undo.append(before)
        del self._undo[:-100]
        self._redo.clear()
        self.segments = result
        self.timeline.start, self.timeline.end = 0, duration(result)
        self._refresh()
        self._seek(min(position, duration(result)))

    def _history(self, redo):
        stack, other = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if self._busy or not stack:
            return
        self._pause()
        other.append(self._snapshot())
        self.segments, position, self.timeline.start, self.timeline.end = stack.pop()
        self._refresh()
        self._seek(position)

    def _reset(self):
        if self._busy or not self._probe_ready:
            return
        self._pause()
        self._undo.append(self._snapshot())
        self._redo.clear()
        self.segments = [(0.0, self._source_duration)]
        self.timeline.start, self.timeline.end = 0, self._source_duration
        self._refresh()
        self._seek(0)

    def _seek(self, seconds):
        if self._busy or not self.segments:
            return
        self._pause()
        self._seek_internal(seconds)

    def _seek_internal(self, seconds):
        total = duration(self.segments)
        seconds = min(max(0, seconds), total)
        self._active_segment, source = source_position(self.segments, seconds)
        source_start, source_end = self.segments[self._active_segment]
        if source >= source_end - 0.000001:
            source = max(source_start, source_end - 1 / self._fps)
        self.timeline.position = seconds
        self._seeking = True
        self.player.setPosition(round(source * 1000))
        self._seeking = False
        self._refresh()

    def _play(self):
        if self._busy or not self.segments:
            return
        if self.timeline.position >= duration(self.segments) - 1 / self._fps:
            self._seek_internal(0)
        self._playing = True
        self._playback_timer.start()
        self.player.play()

    def _pause(self):
        self._playing = False
        self._playback_timer.stop()
        self.player.pause()

    def _toggle_play(self):
        self._pause() if self._playing else self._play()

    def _stop(self):
        self._seek(0)

    def _position_changed(self, milliseconds):
        if self._seeking or not self.segments or not self._playing:
            return
        start, end = self.segments[self._active_segment]
        source = milliseconds / 1000
        offset = duration(self.segments[:self._active_segment])
        if source >= end - 0.001:
            self._advance_segment()
        elif source >= start - 0.01:
            self.timeline.position = min(offset + source - start, duration(self.segments))
            self._refresh()

    def _advance_segment(self):
        if self._active_segment + 1 < len(self.segments):
            next_time = duration(self.segments[:self._active_segment + 1])
            self._seek_internal(next_time)
            self.player.play()
        else:
            self._pause()
            self.timeline.position = duration(self.segments)
            self.player.setPosition(round(max(0, self.segments[-1][1] - 1 / self._fps) * 1000))
            self._refresh()

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing:
            self._advance_segment()

    def _export(self):
        if self._busy or self._thumbnail_process is not None or not self.segments:
            return
        self._pause()
        try:
            folder = Path(self.source).parent
            if folder.name != "edited":
                folder = folder / "edited"
            folder.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"-edit-[0-9a-f]{32}$", "", Path(self.source).stem)[:90]
            self._target = folder / f"{stem}-edit-{uuid.uuid4().hex}.mp4"
            args = export_arguments(self.source, str(self._target), self.segments, self._has_audio)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self._busy = True
        self._error_bytes.clear()
        self._progress_buffer = ""
        self._refresh()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText(self._text("saving", "Saving edited video…"))
        process = QProcess(self)
        self._process = process
        process.setProgram(str(self.executable))
        process.setArguments(args)
        process.readyReadStandardOutput.connect(self._read_progress)
        process.readyReadStandardError.connect(self._read_error)
        process.finished.connect(self._export_finished)
        process.errorOccurred.connect(self._process_error)
        process.start()

    def _read_progress(self):
        if self._process is None:
            return
        self._progress_buffer += bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        lines = self._progress_buffer.split("\n")
        self._progress_buffer = lines.pop()
        for line in lines:
            if line.startswith("out_time_us="):
                try:
                    self.progress.setValue(min(99, round(float(line.split("=", 1)[1]) / 1000000 / max(0.001, duration(self.segments)) * 100)))
                except ValueError:
                    pass

    def _read_error(self):
        if self._process is not None:
            self._error_bytes.extend(bytes(self._process.readAllStandardError()))
            del self._error_bytes[:-6000]

    def _export_finished(self, code, status):
        if self._process is None:
            return
        self._read_error()
        process, self._process = self._process, None
        process.deleteLater()
        if code == 0 and not self._cancel_pending and self._target and self._target.is_file() and self._target.stat().st_size:
            self.player.stop()
            self.player.setSource(QUrl())
            self.videoAccepted.emit(self.scene_id, str(self._target), duration(self.segments))
            self._target = None
            super().accept()
        else:
            self._finish_failure(self._error_bytes.decode(errors="replace"))

    def _finish_failure(self, error):
        if self._process is not None:
            self._process.deleteLater()
            self._process = None
        if self._target:
            self._target.unlink(missing_ok=True)
            self._target = None
        self._busy = False
        self.progress.hide()
        if self._cancel_pending:
            super().reject()
            return
        self.status.setText(self._text("save_error", "Could not save the video: {error}", error=error or "FFmpeg failed."))
        self._refresh()

    def reject(self):
        self._pause()
        self.player.setSource(QUrl())
        if self._thumbnail_process is not None:
            self._cancel_pending = True
            self._thumbnail_process.kill()
        if self._process is not None:
            self._cancel_pending = True
            self._process.kill()
            self.status.setText(self._text("cancelling", "Cancelling…"))
            return
        if self._thumbnail_process is not None:
            return
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()

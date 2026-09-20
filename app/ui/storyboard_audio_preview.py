"""Asynchronous preview copies for gain above Qt's 100% output ceiling."""
import hashlib
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

from app.utils.ffmpeg_utils import find_ffmpeg


def preview_gain_arguments(source, target, gain):
    return ['-y', '-hide_banner', '-loglevel', 'error', '-i', str(source),
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy',
            '-af', f'volume={gain:.6f},alimiter=limit=0.95:level=false:latency=1',
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]


class AmplifiedVideoPreview(QObject):
    failed = Signal(str)

    def __init__(self, player, audio_output, parent=None):
        super().__init__(parent)
        self.player, self.audio = player, audio_output
        self._desired = None
        self._process = None
        self._restore = None
        self._active_gain = 1.0
        self._active_path = ''
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._prepare)
        player.mediaStatusChanged.connect(self._media_ready)

    def reset(self):
        self._desired = None
        self._restore = None
        self._timer.stop()
        if self._process:
            self._process.kill()
            self._process = None

    def apply(self, source, gain, executable, cache_dir):
        source = Path(source).resolve()
        if not source.is_file():
            return
        stat = source.stat()
        gain = max(0.0, min(16.0, float(gain)))
        desired = (source.as_posix(), stat.st_mtime_ns, stat.st_size, gain, str(executable), str(cache_dir))
        current = self.player.source().toLocalFile()
        active_gain = self._active_gain if current == self._active_path else 1.0
        self.audio.setVolume(min(1, gain / active_gain))
        if desired == self._desired:
            return
        self.reset()
        self._desired = desired
        if gain <= 1:
            self._switch(str(source), 1, gain)
        else:
            self._timer.start()

    def _prepare(self):
        desired = self._desired
        if not desired:
            return
        source, mtime, size, gain, executable, cache_dir = desired
        key = hashlib.sha256(repr((source, mtime, size, gain)).encode()).hexdigest()
        target = Path(cache_dir) / (key + '.mp4')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            executable = str(find_ffmpeg(executable))
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if target.is_file():
            self._switch(str(target), gain, 1)
            return
        temporary = target.with_name(key + '-' + uuid.uuid4().hex + '.part.mp4')
        process = QProcess(self)
        self._process = process
        process.setProgram(executable)
        process.setArguments(preview_gain_arguments(source, temporary, gain))
        process.finished.connect(lambda code, _status: self._finished(process, code, desired, temporary, target))
        process.errorOccurred.connect(lambda error: self._failed_to_start(process, error))
        process.start()

    def _failed_to_start(self, process, error):
        if error == QProcess.ProcessError.FailedToStart and self._process is process:
            self._process = None
            self.failed.emit('Cannot prepare amplified preview: ' + process.errorString())
            process.deleteLater()

    def _finished(self, process, code, desired, temporary, target):
        active = self._process is process and self._desired == desired
        if self._process is process:
            self._process = None
        try:
            if active and code == 0 and temporary.is_file():
                temporary.replace(target)
                self._switch(str(target), desired[3], 1)
            elif active:
                detail = bytes(process.readAllStandardError()).decode('utf-8', errors='replace')[-1000:]
                self.failed.emit('Cannot prepare amplified preview: ' + detail)
        except OSError as exc:
            if active:
                self.failed.emit(str(exc))
        finally:
            temporary.unlink(missing_ok=True)
            process.deleteLater()

    def _switch(self, path, file_gain, output_volume):
        path = Path(path).resolve().as_posix()
        self._active_path, self._active_gain = path, file_gain
        self.audio.setVolume(output_volume)
        if self.player.source().toLocalFile() == path:
            return
        self._restore = (path, self.player.position(), self.player.playbackRate(),
                         self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.player.setSource(QUrl.fromLocalFile(path))

    def _media_ready(self, status):
        if not self._restore or status not in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            return
        path, position, rate, playing = self._restore
        self._restore = None
        if self.player.source().toLocalFile() != path:
            return
        self.player.setPlaybackRate(rate)
        self.player.setPosition(position)
        if playing:
            self.player.play()
        else:
            self.player.pause()

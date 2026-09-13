from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.utils.ffmpeg_utils import find_ffmpeg


class VideoLastFrameCopy(QObject):
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._done = False

    def start(self, path, ffmpeg_path):
        try:
            executable = find_ffmpeg(ffmpeg_path)
        except Exception as exc:
            self._complete(False, str(exc))
            return
        self.process.setProgram(str(executable))
        # Decode the tail and reverse it to copy the actual final decoded frame.
        self.process.setArguments([
            "-hide_banner", "-loglevel", "error", "-sseof", "-3", "-i", path,
            "-map", "0:v:0", "-an", "-vf", "reverse", "-frames:v", "1",
            "-f", "image2pipe", "-c:v", "png", "pipe:1",
        ])
        self.process.start()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(False, self.process.errorString())

    def _finished(self, code, status):
        image = QImage.fromData(bytes(self.process.readAllStandardOutput()), "PNG")
        if code == 0 and not image.isNull():
            QApplication.clipboard().setImage(image)
            self._complete(True, "")
        else:
            self._complete(False, bytes(self.process.readAllStandardError()).decode(errors="replace")[-3000:])

    def _complete(self, ok, error):
        if not self._done:
            self._done = True
            self.finished.emit(ok, error)


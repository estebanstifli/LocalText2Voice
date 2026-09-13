from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_video_comfyui import _probe_media_duration
from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg


class VideoStoryboardVideoImportWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, scene_id: str, source: Path, output: Path, ffmpeg_path: str):
        super().__init__()
        self.scene_id, self.source, self.output = scene_id, source, output
        self.ffmpeg_path = ffmpeg_path
        self._runner = None
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()
        if self._runner is not None:
            self._runner.cancel_current()

    @Slot()
    def run(self):
        owns_output = False
        try:
            if not self.source.is_file() or self.source.resolve() == self.output.resolve() or self.output.exists():
                raise ValueError("Choose a readable local video file.")
            owns_output = True
            self._runner = FFmpegRunner(find_ffmpeg(self.ffmpeg_path))
            if self._cancelled.is_set():
                raise ValueError("Cancelled")
            self.progress.emit("importing_video")
            self._runner.run([
                "-hide_banner", "-loglevel", "error", "-i", str(self.source),
                "-map", "0:v:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(self.output),
            ])
            seconds = _probe_media_duration(self.output, {"ffmpeg_path": self.ffmpeg_path})
            if seconds <= 0 or self._cancelled.is_set():
                raise ValueError("The video has no readable duration or the import was cancelled.")
            self.finished.emit({"scene_id": self.scene_id, "video_path": str(self.output), "video_duration_seconds": seconds})
        except Exception as exc:
            if owns_output:
                self.output.unlink(missing_ok=True)
            self.failed.emit(str(exc))

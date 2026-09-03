from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_renderer import render_storyboard_video
from app.utils.ffmpeg_utils import FFmpegError


class VideoStoryboardRenderWorker(QObject):
    progress = Signal(str, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        scenes: list[dict[str, Any]],
        audio_path: Path,
        output_path: Path,
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self.scenes = scenes
        self.audio_path = audio_path
        self.output_path = output_path
        self.settings = settings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = render_storyboard_video(
                self.scenes,
                self.audio_path,
                self.output_path,
                self.settings,
                progress=lambda stage, percentage: self.progress.emit(
                    stage, percentage
                ),
                cancelled=self._cancel_event.is_set,
            )
            self.finished.emit(result)
        except FFmpegError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected video rendering error: {exc}")

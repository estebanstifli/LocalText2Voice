from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_services import (
    VideoStoryboardServiceError,
    detect_comfyui,
    detect_ollama,
)


class VideoStoryboardServiceDetectionWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, service: str, base_url: str) -> None:
        super().__init__()
        self.service = service
        self.base_url = base_url

    @Slot()
    def run(self) -> None:
        try:
            if self.service == "ollama":
                result = detect_ollama(self.base_url)
            elif self.service == "comfyui":
                result = detect_comfyui(self.base_url)
            else:
                raise VideoStoryboardServiceError(
                    f"Unknown Video Storyboard service: {self.service}"
                )
            self.finished.emit(self.service, result)
        except VideoStoryboardServiceError as exc:
            self.failed.emit(self.service, str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(self.service, f"Unexpected detection error: {exc}")

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_image_edit import (
    VideoStoryboardImageEditError,
    generate_edited_storyboard_image,
    prepare_image_edit_runtime,
)


class VideoStoryboardImageEditWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        target: Path,
    ) -> None:
        super().__init__()
        self.request = dict(request)
        self.settings = dict(settings)
        self.target = Path(target)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            runtime = prepare_image_edit_runtime(
                self.settings,
                status=self.progress.emit,
                camera=self.request.get("camera"),
            )
            generation_settings = dict(self.settings)
            if runtime.get("camera_lora_model"):
                generation_settings["comfyui_image_edit"] = {
                    **self.settings.get("comfyui_image_edit", {}),
                    "camera_lora_model": runtime["camera_lora_model"],
                }
            result = generate_edited_storyboard_image(
                list(self.request.get("reference_images", [])),
                str(self.request.get("prompt") or ""),
                generation_settings,
                self.target,
                seed=int(self.request.get("seed") or 0),
                width=int(self.request.get("width") or 1280),
                height=int(self.request.get("height") or 720),
                scene_id=str(self.request.get("scene_id") or ""),
                status=self.progress.emit,
                cancelled=self._cancel_event.is_set,
                camera=self.request.get("camera"),
            )
            result["runtime"] = runtime
            self.finished.emit(result)
        except VideoStoryboardImageEditError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected image-editing error: {exc}")

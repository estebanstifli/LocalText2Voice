from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_video_comfyui import (
    VideoStoryboardVideoError,
    generate_storyboard_scene_video,
    prepare_video_runtime,
)


class VideoStoryboardVideoWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        scene: dict[str, Any],
        plan: dict[str, Any],
        settings: dict[str, Any],
        output_path: Path,
        *,
        prompt: str,
        frame_role: str,
        prepare_runtime: bool = True,
    ) -> None:
        super().__init__()
        self.scene = dict(scene)
        self.plan = dict(plan)
        self.settings = settings
        self.output_path = output_path
        self.prompt = prompt
        self.frame_role = frame_role
        self.prepare_runtime = prepare_runtime
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            if self.prepare_runtime:
                prepare_video_runtime(
                    self.settings,
                    status=self.progress.emit,
                )
            result = generate_storyboard_scene_video(
                self.scene,
                self.plan,
                self.settings,
                self.output_path,
                prompt=self.prompt,
                frame_role=self.frame_role,
                status=self.progress.emit,
                cancelled=self._cancel_event.is_set,
            )
            self.finished.emit(result)
        except VideoStoryboardVideoError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected video-generation error: {exc}")

from __future__ import annotations

import threading
import traceback
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_planner import (
    VideoStoryboardPlanningError,
    plan_video_storyboard,
)
from app.core.video_storyboard_comfyui import release_comfyui_memory


class VideoStoryboardPlannerWorker(QObject):
    progress = Signal(int, int)
    partial = Signal(object)
    status = Signal(str)
    trace = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        source: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self.source = source
        self.settings = settings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            self.status.emit("release_comfyui")
            release_comfyui_memory(self.settings)
            result = plan_video_storyboard(
                self.source,
                self.settings,
                progress=self.progress.emit,
                partial=self.partial.emit,
                cancelled=self._cancel_event.is_set,
                trace=self.trace.emit,
            )
            self.finished.emit(result)
        except VideoStoryboardPlanningError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected storyboard planning error: {exc}")

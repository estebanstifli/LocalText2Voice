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
from copy import deepcopy
from app.core.storyboard_analysis_review import AnalysisReviewPaused


class VideoStoryboardPlannerWorker(QObject):
    progress = Signal(int, int)
    partial = Signal(object)
    status = Signal(str)
    trace = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    reviewRequested = Signal(object)
    paused = Signal()

    def __init__(
        self,
        source: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self.source = source
        self.settings = settings
        self._cancel_event = threading.Event()
        self._review_event = threading.Event()
        self._review_response = None

    def cancel(self) -> None:
        self._cancel_event.set()
        self._review_event.set()

    def submit_review(self, response):
        # Called directly from the GUI thread: do not queue behind the waiting run slot.
        self._review_response = deepcopy(response)
        self._review_event.set()

    def _review(self, draft):
        if self._cancel_event.is_set():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        self._review_response = None
        self._review_event.clear()
        self.reviewRequested.emit(deepcopy(draft))
        while not self._review_event.wait(0.2):
            if self._cancel_event.is_set():
                break
        if self._cancel_event.is_set():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        if not self._review_response or self._review_response.get("action") == "pause":
            raise AnalysisReviewPaused()
        return self._review_response["draft"]

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
                review=self._review,
            )
            self.finished.emit(result)
        except AnalysisReviewPaused:
            self.paused.emit()
        except VideoStoryboardPlanningError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected storyboard planning error: {exc}")

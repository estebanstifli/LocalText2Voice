from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_comfyui import (
    VideoStoryboardImageError,
    generate_storyboard_frame,
    prepare_image_runtime,
)
from app.core.video_storyboard_image_edit import (
    VideoStoryboardImageEditError,
    prepare_image_edit_runtime,
)


class VideoStoryboardFrameWorker(QObject):
    progress = Signal(int, int, str, str)
    frameReady = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        payload: dict[str, Any],
        settings: dict[str, Any],
        output_dir: Path,
        *,
        candidate: bool = False,
    ) -> None:
        super().__init__()
        self.payload = payload
        self.settings = settings
        self.output_dir = output_dir
        self.candidate = candidate
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            scenes = self.payload.get("scenes", [])
            if not isinstance(scenes, list) or not scenes:
                raise VideoStoryboardImageError("The storyboard has no scenes to generate.")
            uses_editing = any(
                isinstance(scene, dict)
                and isinstance(scene.get("generation_overrides"), dict)
                and bool(scene["generation_overrides"].get("reference_images"))
                for scene in scenes
            )
            uses_generation = any(
                not (
                    isinstance(scene, dict)
                    and isinstance(scene.get("generation_overrides"), dict)
                    and bool(scene["generation_overrides"].get("reference_images"))
                )
                for scene in scenes
            )
            runtime: dict[str, Any] = {}
            if uses_generation:
                runtime["generation"] = prepare_image_runtime(
                    self.settings,
                    status=lambda stage: self.progress.emit(0, 0, "", stage),
                )
            if uses_editing:
                runtime["editing"] = prepare_image_edit_runtime(
                    self.settings,
                    status=lambda stage: self.progress.emit(0, 0, "", stage),
                )
            plan = dict(self.payload.get("plan", {}))
            plan.setdefault("style", {})
            plan.setdefault("base_seed", 0)
            results: list[dict[str, Any]] = []
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for index, scene in enumerate(scenes, start=1):
                if self._cancel_event.is_set():
                    raise VideoStoryboardImageError("Frame generation was cancelled.")
                scene_id = str(scene.get("scene_id") or scene.get("id") or f"{index:03d}")
                self.progress.emit(index, len(scenes), scene_id, "preparing")
                existing_path = Path(str(scene.get("image_path") or ""))
                if not self.candidate and existing_path.is_file():
                    result = {
                        "scene_id": scene_id,
                        "image_path": str(existing_path),
                        "prompt_id": "",
                        "compiled_prompt": "",
                        "seed": int(plan.get("base_seed") or 0),
                        "runtime": runtime,
                    }
                    self.progress.emit(index, len(scenes), scene_id, "existing")
                    self.frameReady.emit(result)
                    results.append(result)
                    continue
                filename = (
                    f"candidate-{scene_id}-{uuid.uuid4().hex[:8]}.png"
                    if self.candidate
                    else f"scene-{index:03d}-{scene_id}.png"
                )
                target = self.output_dir / filename
                result = generate_storyboard_frame(
                    scene,
                    plan,
                    self.settings,
                    target,
                    status=lambda stage, current=index, total=len(scenes), selected=scene_id: self.progress.emit(
                        current, total, selected, stage
                    ),
                    cancelled=self._cancel_event.is_set,
                )
                result["runtime"] = runtime
                self.frameReady.emit(result)
                results.append(result)
            self.finished.emit(results)
        except (VideoStoryboardImageError, VideoStoryboardImageEditError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected image-generation error: {exc}")

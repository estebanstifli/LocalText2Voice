from __future__ import annotations

import threading
import traceback
import uuid
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_comfyui import (
    VideoStoryboardImageError,
    generate_storyboard_frame,
    prepare_image_runtime,
    validate_generation_references,
    compile_effective_scene_prompt,
    compile_reference_edit_prompt,
)
from app.core.video_storyboard_image_edit import (
    VideoStoryboardImageEditError,
)
from app.core.storyboard_reference_images import scene_with_references
from app.core.storyboard_generation_errors import generation_error_details, redact_generation_error
from app.core.storyboard_provider_label import provider_label


class VideoStoryboardFrameWorker(QObject):
    progress = Signal(int, int, str, str)
    frameReady = Signal(object)
    frameFailed = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        payload: dict[str, Any],
        settings: dict[str, Any],
        output_dir: Path,
        *,
        candidate: bool = False,
        checkpoint_project_dir: Path | None = None,
        checkpoint_state: dict | None = None,
    ) -> None:
        super().__init__()
        self.payload = deepcopy(payload)
        self.settings = settings
        self.output_dir = output_dir
        self.candidate = candidate
        self._cancel_event = threading.Event()
        self.checkpoint = None
        self.checkpoint_project_dir = checkpoint_project_dir
        self.checkpoint_state = deepcopy(checkpoint_state or payload)

    def _record_progress(self, result):
        if self.checkpoint is not None:
            try:
                self.checkpoint.record(result)
            except OSError as exc:
                raise FrameCheckpointError(f"Cannot save frame progress; generation stopped. Completed image files remain on disk: {exc}") from exc

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            scenes = self.payload.get("scenes", [])
            if not isinstance(scenes, list) or not scenes:
                raise VideoStoryboardImageError("The storyboard has no scenes to generate.")
            if self.checkpoint_project_dir is not None and not self.candidate:
                from app.core.storyboard_frame_checkpoint import FrameCheckpoint
                self.checkpoint = FrameCheckpoint(self.checkpoint_project_dir, self.checkpoint_state)
            previous_errors = self.payload.get("plan", {}).get("frame_generation_errors", {})
            pending = [scene for scene in scenes if self.candidate
                       or str(scene.get("scene_id") or scene.get("id")) in previous_errors
                       or not Path(str(scene.get("image_path") or "")).is_file()]
            runtime: dict[str, Any] = {}
            if pending:
                runtime["generation"] = prepare_image_runtime(
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
                if not self.candidate and existing_path.is_file() and scene_id not in previous_errors:
                    result = {
                        "scene_id": scene_id,
                        "image_path": str(existing_path),
                        "prompt_id": "",
                        "compiled_prompt": "",
                        "seed": int(plan.get("base_seed") or 0),
                        "runtime": runtime,
                    }
                    self.progress.emit(index, len(scenes), scene_id, "existing")
                    self._record_progress(result)
                    self.frameReady.emit(result)
                    results.append(result)
                    continue
                # A new generation never replaces the previously accepted image.
                filename = (f"candidate-{scene_id}-{uuid.uuid4().hex[:8]}.png" if self.candidate
                            else f"scene-{index:03d}-{scene_id}-{uuid.uuid4().hex[:8]}.png")
                target = self.output_dir / filename
                temporary = target.with_name(f".{target.stem}-{uuid.uuid4().hex}.png")
                # Journal the destination first: even a kill immediately after the
                # atomic rename can recover the complete image, without guessing filenames.
                self._record_progress({"scene_id": scene_id, "image_path": str(target.resolve()), "status": "pending"})
                effective_scene = scene
                try:
                    effective_scene = scene_with_references(scene, plan, self.settings)
                    validate_generation_references(effective_scene, self.settings)
                    for attempt in range(3):
                        try:
                            result = generate_storyboard_frame(
                                effective_scene, plan, self.settings, temporary,
                                status=lambda stage, current=index, total=len(scenes), selected=scene_id:
                                    self.progress.emit(current, total, selected, stage),
                                cancelled=self._cancel_event.is_set,
                            )
                            break
                        except Exception as exc:
                            if self._cancel_event.is_set():
                                raise VideoStoryboardImageError("Frame generation was cancelled.") from exc
                            details = generation_error_details(exc)
                            if (not details["retryable"] or attempt == 2
                                    or self.settings.get("image_provider") == "runpod"):
                                raise
                            self.progress.emit(index, len(scenes), scene_id, "retrying")
                            if self._cancel_event.wait(2 ** (attempt + 1)):
                                raise VideoStoryboardImageError("Frame generation was cancelled.") from exc
                    if self._cancel_event.is_set():
                        raise VideoStoryboardImageError("Frame generation was cancelled.")
                    if not temporary.is_file():
                        raise VideoStoryboardImageError("The provider returned no image file.")
                    with temporary.open("rb+") as image_file:
                        os.fsync(image_file.fileno())
                    temporary.replace(target)
                    result.update(image_path=str(target), scene_id=scene_id, runtime=runtime, status="generated")
                    self._record_progress(result)
                    self.frameReady.emit(result)
                    results.append(result)
                except Exception as exc:
                    if isinstance(exc, FrameCheckpointError):
                        raise
                    if self._cancel_event.is_set() or self.candidate:
                        raise
                    failure = generation_error_details(exc)
                    failure["error"] = redact_generation_error(failure["error"], self.settings)
                    failure.update(scene_id=scene_id, status="failed", provider=provider_label(self.settings))
                    try:
                        references = effective_scene.get("generation_overrides", {}).get("reference_images", [])
                        failure["reference_images"] = references
                        prompt = compile_effective_scene_prompt(plan, effective_scene)
                        failure["compiled_prompt"] = compile_reference_edit_prompt(prompt, references) if references else prompt
                    except Exception:
                        failure["compiled_prompt"] = str(scene.get("image_prompt") or "")
                    results.append(failure)
                    self._record_progress(failure)
                    self.frameFailed.emit(failure)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            self.finished.emit(results)
        except (VideoStoryboardImageError, VideoStoryboardImageEditError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected image-generation error: {exc}")
        finally:
            if self.checkpoint is not None:
                from app.core.storyboard_frame_checkpoint import ACTIVE_RUNS
                ACTIVE_RUNS.discard(self.checkpoint.run_id)


class FrameCheckpointError(VideoStoryboardImageError):
    pass

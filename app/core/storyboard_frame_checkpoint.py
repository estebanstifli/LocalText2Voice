"""Worker-side recovery journal; never depends on delivery of GUI signals."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

ACTIVE_RUNS = set()


def scene_id(scene):
    return str(scene.get("scene_id") or scene.get("id") or "")


def signature(scene):
    keys = ("prompt", "image_prompt", "narration", "start_seconds", "duration", "duration_seconds",
            "characters", "locations", "generation_overrides")
    return hashlib.sha256(json.dumps({k: scene.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


class FrameCheckpoint:
    def __init__(self, project_dir, state):
        from app.core.video_storyboard_project import _WRITE_LOCK, _atomic_json
        self.pointer = Path(project_dir) / "storyboard" / "frame-checkpoint.json"
        self.run_id = uuid.uuid4().hex
        self.events = self.pointer.parent / "recovery" / (self.run_id + ".jsonl")
        self.events.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            _atomic_json(self.pointer, {"run_id": self.run_id, "state": deepcopy(state)})
            ACTIVE_RUNS.add(self.run_id)

    def record(self, result):
        data = {k: result[k] for k in ("scene_id", "image_path", "status", "error", "provider", "code") if k in result}
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self):
        # Only remove the pointer after the GUI has persisted all queued events.
        from app.core.video_storyboard_project import _WRITE_LOCK
        with _WRITE_LOCK:
            try:
                current = json.loads(self.pointer.read_text(encoding="utf-8"))
                if current.get("run_id") == self.run_id:
                    self.pointer.unlink()
            except FileNotFoundError:
                pass


def recover(project_dir, document):
    """Overlay durable results on unchanged scenes; ignore a torn final line."""
    from app.core.video_storyboard_project import storyboard_matches_source
    pointer = Path(project_dir) / "storyboard" / "frame-checkpoint.json"
    try:
        checkpoint = json.loads(pointer.read_text(encoding="utf-8"))
        run_id = str(checkpoint["run_id"])
        if run_id in ACTIVE_RUNS:
            return document, False
        if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            return document, False
        baseline = checkpoint["state"]
        if document is None:
            document = deepcopy(baseline)
            document.update(schema="localtext2voice.video-storyboard", version=3, analysis_status="ready")
            document["source_hash"] = hashlib.sha256(str(document.get("source", {}).get("text") or "").encode()).hexdigest()
        elif not storyboard_matches_source(document, baseline.get("source", {}).get("text", "")):
            return document, False
        originals = {scene_id(s): s for s in baseline.get("scenes", [])}
        targets = {scene_id(s): s for s in document.get("scenes", [])}
        events = pointer.parent / "recovery" / (run_id + ".jsonl")
        for line in events.read_text(encoding="utf-8").splitlines() if events.is_file() else []:
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(result.get("scene_id") or "")
            if key not in targets or key not in originals or signature(targets[key]) != signature(originals[key]):
                continue
            scene = targets[key]
            errors = document.setdefault("plan", {}).setdefault("frame_generation_errors", {})
            if result.get("status") == "failed":
                scene["status"] = "generation_failed"
                errors[key] = result
            elif result.get("image_path") and Path(result["image_path"]).is_file():
                scene.update(image_path=result["image_path"], status="generated")
                errors.pop(key, None)
        return document, True
    except (OSError, ValueError, KeyError, TypeError):
        return document, False

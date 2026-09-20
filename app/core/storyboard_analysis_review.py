"""Project-local analysis choices and resumable, credential-free review drafts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

PLANS = ("scenes", "basic", "full")


def choices(value):
    from app.core.storyboard_eras import era_mode
    value = value if isinstance(value, dict) else {}
    plan = value.get("plan") if value.get("plan") in PLANS else "full"
    return {"plan": plan, "review": bool(value.get("review", False)),
            "characters": bool(value.get("characters", plan != "scenes")),
            "locations": bool(value.get("locations", plan != "scenes")),
            "objects": bool(value.get("objects", False)),
            "era_mode": era_mode(value.get("era_mode"))}


def fingerprint(source, settings):
    data = {"pipeline_revision": 14, "source": {k: source.get(k) for k in ("text", "duration_seconds", "narration_cues", "voice_start_offset_seconds")}, "plan": choices(settings.get("analysis_choices")),
            "analysis": settings.get("analysis"), "scene": settings.get("scene"),
            "continuity": settings.get("continuity_analysis"),
            "era": settings.get("narrative_context")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def load_review(project_dir):
    try:
        value = json.loads((Path(project_dir) / "storyboard" / "analysis_review.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_review(project_dir, value):
    target = Path(project_dir) / "storyboard" / "analysis_review.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="review-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class AnalysisReviewPaused(Exception):
    pass

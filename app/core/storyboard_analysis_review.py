"""Project-local analysis choices and resumable, credential-free review drafts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

PLANS = ("scenes", "basic", "full")


def choices(value):
    value = value if isinstance(value, dict) else {}
    return {"plan": value.get("plan") if value.get("plan") in PLANS else "full",
            "review": bool(value.get("review", False))}


def fingerprint(source, settings):
    data = {"pipeline_revision": 11, "source": {k: source.get(k) for k in ("text", "duration_seconds", "narration_cues", "voice_start_offset_seconds")}, "plan": choices(settings.get("analysis_choices")),
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


def apply_basic_policy(result):
    """Basic continuity has stable profiles, never temporal appearance changes."""
    result = deepcopy(result)
    for key in ("character_events", "location_events"):
        result[key] = [e for e in result.get(key, []) if e.get("event_type") not in {"explicit_change"}]
    result["era_events"] = []
    return result


def freeze_basic_profiles(ledger, previous):
    for collection in ("characters", "locations"):
        old = {e["id"]: e for e in previous.get(collection, [])}
        for entity in ledger.get(collection, []):
            original = old.get(entity["id"])
            if original and original.get("states"):
                entity["identity_description"] = original.get("identity_description", "")
                entity["states"] = deepcopy(original["states"][:1])
            else:
                entity["states"] = entity.get("states", [])[:1]


class AnalysisReviewPaused(Exception):
    pass

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
    data = {"pipeline_revision": 15, "source": {k: source.get(k) for k in ("text", "duration_seconds", "narration_cues", "voice_start_offset_seconds")}, "plan": choices(settings.get("analysis_choices")),
            "analysis": settings.get("analysis"), "scene": settings.get("scene"),
            "continuity": settings.get("continuity_analysis"),
            "era": settings.get("narrative_context")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def load_review(project_dir):
    try:
        value = json.loads((Path(project_dir) / "storyboard" / "analysis_review.json").read_text(encoding="utf-8"))
        value = value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        value = {}
    if value.get("draft", {}).get("status") not in {"pending", "approved"}:
        recovery = load_recovery_plan(project_dir)
        draft = recovery.get("continuity", {}).get("analysis_review", {})
        if draft:
            value["draft"] = deepcopy(draft)
    return value


def load_recovery_plan(project_dir):
    """Offer saved work only for unfinished runs, never a completed analysis."""
    from app.core.video_storyboard_project import load_storyboard_state
    if not project_dir:
        return {}
    state = load_storyboard_state(Path(project_dir)) or {}
    plan = state.get("plan", {})
    draft = plan.get("continuity", {}).get("analysis_review", {})
    if (state.get("analysis_status") not in {"failed", "analyzing", "paused"}
            or plan.get("analysis_phase") == "complete"
            or draft.get("status") not in {"pending", "approved"}
            or not draft.get("reports")
            or not all(r.get("scene_excerpts") for r in draft["reports"])):
        return {}
    # Scene records are stored beside plan metadata by the project serializer.
    # Return the complete checkpoint, including prompts and any attached media.
    return {**plan, "scenes": deepcopy(state.get("scenes") or plan.get("scenes") or [])}


def resume_maximum_seconds(source, settings, review_state, manual_era=""):
    """Restore the per-analysis limit, including drafts saved before it was persisted."""
    limits = review_state.get("limits", {})
    fallback = settings.get("scene", {}).get("maximum_seconds", 8)
    saved = limits.get("maximum_scene_seconds")
    if isinstance(saved, int) and 4 <= saved <= 60:
        return saved
    draft = review_state.get("draft", {})
    if not draft.get("fingerprint"):
        return fallback
    candidate = deepcopy(settings)
    candidate["analysis_choices"] = review_state.get("choices", draft.get("choices", {}))
    candidate.setdefault("narrative_context", {})["era"] = manual_era
    analysis = candidate.setdefault("analysis", {})
    if limits.get("max_input_characters"):
        size = int(limits["max_input_characters"])
        analysis.update(max_block_characters=size, max_block_seconds=max(10.0, min(1000.0, size / 100.0)))
    if limits.get("max_output_tokens"):
        analysis["max_output_tokens"] = int(limits["max_output_tokens"])
    # Older versions omitted this value from the setup file. The full source/
    # settings fingerprint lets us recover it without trusting a guess.
    for seconds in range(4, 61):
        candidate.setdefault("scene", {})["maximum_seconds"] = seconds
        if fingerprint(source, candidate) == draft["fingerprint"]:
            return seconds
    return fallback


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

"""Use one text coordinate system for discovery, quotes and narration timings."""
from copy import deepcopy
import hashlib
import math


def analysis_source(source):
    """Prefer original text only when every spoken cue occurs there in order.

    TTS normalizes dates/numbers and may edit punctuation. In that case the
    actual narration becomes the analysis text. Never add spoken character
    lengths to offsets in a different document. The original stays untouched.
    """
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    result = deepcopy(source)
    original = str(source.get("text") or "").strip()
    raw_cues = result.get("narration_cues") or []
    cues = [c for c in raw_cues
            if isinstance(c, dict) and str(c.get("text") or "").strip()]
    cursor, previous_end = 0, 0.0
    exact = True
    for index, cue in enumerate(cues, 1):
        start = float(cue.get("start_seconds") or 0)
        end = float(cue.get("end_seconds") or start + float(cue.get("duration_seconds") or 0))
        if (not math.isfinite(start) or not math.isfinite(end) or end <= start
                or start < previous_end - .01):
            raise VideoStoryboardPlanningError(
                f"Narration segment {index} has missing, overlapping or invalid timings. Regenerate its audio before analysis.")
        previous_end = end
        spoken = str(cue["text"]).strip()
        found = original.find(spoken, cursor)
        if found < 0 or original[cursor:found].strip():
            exact = False
        if found >= 0:
            cursor = found + len(spoken)
        cue["text"] = spoken
    if cues and original[cursor:].strip():
        exact = False
    result["narration_cues"] = cues
    basis = "original_text"
    if cues and not exact:
        result["text"] = "\n".join(c["text"] for c in cues)
        basis = "timed_narration"
    else:
        result["text"] = original
    duration = float(source.get("duration_seconds") or previous_end)
    if cues and (not math.isfinite(duration) or previous_end > duration + .01):
        raise VideoStoryboardPlanningError("Narration timings extend beyond the audiobook duration.")
    if cues:
        result["duration_seconds"] = duration
    return result, {"coordinate_basis": basis, "text": result["text"],
                    "original_text_sha256": hashlib.sha256(original.encode()).hexdigest()}

"""Validate saved visual work against freshly reconstructed scene assignments."""
from copy import deepcopy
import math


def restored_scene(saved, assignment, proposal, *, index, semantic_index,
                   coverage_index, coverage_count, era, era_state_id, coordinate_basis):
    """Return a planner-shaped scene only when its source and timing still match."""
    if (not isinstance(saved, dict) or not isinstance(saved.get("prompt"), str)
            or not saved["prompt"].strip()):
        return None
    units = assignment["units"]
    expected = {
        "narration": assignment["narration"],
        "semantic_scene_id": f"{semantic_index:03d}",
        "source_proposal_id": str(proposal.get("source_proposal_id") or ""),
        "source_excerpt_id": str(proposal.get("source_excerpt_id") or ""),
        "source_coordinate_basis": coordinate_basis,
        "source_unit_start": units[0]["id"], "source_unit_end": units[-1]["id"],
        "source_text_start": units[0]["text_start"], "source_text_end": units[-1]["text_end"],
        "coverage_index": coverage_index, "coverage_count": coverage_count,
        "coverage_prompt_version": 2, "era": era, "era_state_id": era_state_id,
    }
    if any(saved.get(key) != value for key, value in expected.items()):
        return None
    start = round(assignment["start"], 3)
    duration = round(round(assignment["end"], 3) - start, 3)
    try:
        if not math.isclose(float(saved.get("start_seconds")), start, abs_tol=0.001, rel_tol=0):
            return None
        if not math.isclose(float(saved.get("duration", saved.get("duration_seconds"))), duration,
                            abs_tol=0.001, rel_tol=0):
            return None
    except (TypeError, ValueError):
        return None
    result = deepcopy(saved)
    result.update(id=f"{index:03d}", duration=duration, start_seconds=start)
    result.pop("scene_id", None)
    result.pop("duration_seconds", None)
    return result

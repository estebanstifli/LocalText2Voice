import pytest

from app.core.video_storyboard_appearance import appearance_schema, merge_appearance, selected_appearance
from app.core.video_storyboard_planner import _empty_continuity
from app.core.video_storyboard_comfyui import compile_scene_prompt


def units(*texts):
    return [{"id": i, "text": t, "start_seconds": 2.0 + i * 8,
             "end_seconds": 10.0 + i * 8} for i, t in enumerate(texts)]


def event(name="Ana", unit=0, kind="first_appearance", **values):
    return {"id": name.lower().replace(" ", "_"), "name": name, "aliases": [],
            "effective_unit_id": unit, "event_type": kind,
            "identity_description": "oval face, dark eyes", "context_description": "",
            "appearance": {}, "evidence": "", **values}


def test_manual_state_edit_remains_authoritative():
    text, meta = merge_appearance("", None, {"wardrobe": "red dress"}, kind="character", initial=True, changed=False)
    updated, updated_meta = merge_appearance("user's outfit", meta, {"wardrobe": "blue robe"},
                                           kind="character", initial=False, changed=True, source_backed=True)
    assert updated == "user's outfit" and updated_meta["manual"]


def group(name="siblings", unit=0, members=None):
    return {"id": name, "name": name, "aliases": ["the siblings"], "member_ids": members or ["ana", "luis"], "effective_unit_id": unit}

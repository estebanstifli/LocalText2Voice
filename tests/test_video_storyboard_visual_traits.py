from copy import deepcopy

from app.core.video_storyboard_planner import _empty_continuity
from app.core.video_storyboard_visual_traits import compact_visual_description, merge_visual_description
from app.core.video_storyboard_comfyui import compile_scene_prompt, compile_effective_scene_prompt


def event(name="first little pig", **overrides):
    return {"id": name.replace(" ", "_"), "name": name, "aliases": [],
            "effective_unit_id": 0, "event_type": "first_appearance",
            "identity_description": "Small pink piglet with upright ears and brown eyes",
            "state_description": "Young piglet wearing a red neckerchief", **overrides}


def block(*events):
    return {"character_events": list(events), "location_events": [], "era_events": []}


def units(*texts):
    return [{"id": i, "text": text, "start_seconds": 2.0 + i * 8,
             "end_seconds": 10.0 + i * 8} for i, text in enumerate(texts)]


def test_manual_text_edit_invalidates_old_inferred_metadata():
    text, metadata = merge_visual_description("", "green eyes", initial=True)
    edited, updated = merge_visual_description("hazel eyes", "blue eyes", evidence="She has blue eyes", metadata=metadata)
    assert edited == "hazel eyes"
    assert updated["facts"]["eyes"]["origin"] == "user_or_legacy"


def test_source_can_enrich_a_known_trait_without_contradicting_it():
    text, metadata = merge_visual_description("", "brown hair", initial=True, evidence="She had brown hair.")
    text, metadata = merge_visual_description(text, "long brown hair", metadata=metadata, evidence="Her long brown hair was tied back.")
    assert text == "long brown hair"
    text, _ = merge_visual_description(text, "blonde hair", metadata=metadata, evidence="She had blonde hair.")
    assert text == "long brown hair"


def test_mobility_defaults_removed_but_real_aids_and_glasses_kept():
    compact = compact_visual_description("oval face, brown eyes", "adult man using a wheelchair and wearing glasses, able-bodied, with all limbs unobstructed and able to walk and move unaided, without mobility aids")
    assert "wheelchair" in compact and "glasses" in compact
    assert not any(s in compact for s in ("able-bodied", "unaided", "unobstructed", "without mobility aids"))
    assert "without glasses" in compact_visual_description("older man without glasses")


def test_explicit_raw_override_is_never_compacted():
    raw = "SCENE: user text. " * 90
    assert compile_effective_scene_prompt({}, {"generation_overrides": {"raw_prompt": raw}}) == raw.strip()


def test_nonvisual_personality_is_not_an_image_identity_trait():
    assert compact_visual_description("brown eyes, loyal and hardworking personality") == "brown eyes"

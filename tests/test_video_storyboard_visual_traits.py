from copy import deepcopy

from app.core.video_storyboard_planner import (
    _empty_continuity, _merge_continuity_events,
    _normalize_continuity_event_semantics, _normalize_continuity_first_appearance_anchors,
    _placeholder_continuity_events, _continuity_analyzer_messages,
    _continuity_event_needs_visual_completion, _apply_continuity_visual_completions,
)
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


def test_empty_known_revelation_and_young_piglet_do_not_need_completion():
    ledger = _empty_continuity()
    source = units("The three little pigs lived with their mother.")
    _merge_continuity_events(ledger, block(event()), source)
    delta = event(event_type="stable_revelation", identity_description="", state_description="")
    assert _placeholder_continuity_events(block(delta), ledger) == []
    assert not _continuity_event_needs_visual_completion("character_events", event())
    assert not _continuity_event_needs_visual_completion("character_events", event(identity_description=""), ledger)


def test_collective_first_appearance_is_not_moved_forward_to_individual_name():
    source = units("Three little pigs lived together.", "The first little pig built a house.")
    response = block(event())
    _normalize_continuity_first_appearance_anchors(response, source, {})
    assert response["character_events"][0]["effective_unit_id"] == 0


def test_saved_pig_failure_pattern_does_not_redesign_a_known_character():
    # Reduced reproduction of the recorded five-block story: a repeated first
    # appearance followed by an empty revelation used to invent a third outfit.
    ledger = _empty_continuity()
    source = units("Three little pigs lived together.", "The first little pig looked through the window.", "So the three brothers worked together.")
    _merge_continuity_events(ledger, block(event()), source)
    original = deepcopy(ledger["characters"][0])
    response = block(
        event(effective_unit_id=1, identity_description="Pale-pink piglet with floppy ears and hazel eyes",
              state_description="Nine-year-old child wearing a blue vest"),
        event(effective_unit_id=2, event_type="stable_revelation", identity_description="", state_description=""),
    )
    _normalize_continuity_event_semantics(response, source, ledger)
    assert not _placeholder_continuity_events(response, ledger)
    _merge_continuity_events(ledger, response, source)
    pig = ledger["characters"][0]
    assert pig["identity_description"] == original["identity_description"]
    assert pig["states"] == original["states"]
    prompt = compile_scene_prompt({"continuity": ledger}, {"prompt": "first little pig smiles.", "characters": [pig["states"][0]["id"]]})
    assert "red neckerchief" in prompt and "floppy ears" not in prompt
    assert len(prompt.split()) < 50


def test_source_revelation_can_correct_an_invented_color_without_a_new_state():
    ledger = _empty_continuity()
    source = units("Clara arrived.", "Clara tenia los ojos azules.", "Clara smiled.")
    _merge_continuity_events(ledger, block(event("Clara", identity_description="oval face, green eyes", state_description="young woman wearing a red dress")), source)
    _merge_continuity_events(ledger, block(event("Clara", effective_unit_id=1, event_type="stable_revelation", identity_description="blue eyes", state_description="")), source)
    clara = ledger["characters"][0]
    assert "blue eyes" in clara["identity_description"] and "green eyes" not in clara["identity_description"]
    assert clara["identity_traits"]["facts"]["eyes"]["origin"] == "source"
    assert len(clara["states"]) == 1
    _merge_continuity_events(ledger, block(event("Clara", effective_unit_id=2, event_type="stable_revelation", identity_description="hazel eyes", state_description="")), source)
    assert "hazel" not in clara["identity_description"]


def test_source_description_is_not_mistaken_for_a_timed_outfit_change():
    ledger = _empty_continuity()
    source = units("Ana arrived.", "Ana was wearing a red dress.", "Ana changed into a blue shirt.")
    _merge_continuity_events(ledger, block(event("Ana", identity_description="brown eyes", state_description="young woman wearing a green dress, leather shoes")), source)
    response = block(event("Ana", effective_unit_id=1, event_type="explicit_change", identity_description="", state_description="wearing a red dress"))
    _normalize_continuity_event_semantics(response, source, ledger)
    assert response["character_events"][0]["event_type"] == "stable_revelation"
    _merge_continuity_events(ledger, response, source)
    ana = ledger["characters"][0]
    assert len(ana["states"]) == 1 and "red dress" in ana["states"][0]["description"]
    response = block(event("Ana", effective_unit_id=2, event_type="explicit_change", identity_description="", state_description="wearing a blue shirt"))
    _normalize_continuity_event_semantics(response, source, ledger)
    _merge_continuity_events(ledger, response, source)
    assert len(ana["states"]) == 2
    assert "red dress" in ana["states"][0]["description"]
    assert "red dress" not in ana["states"][1]["description"]
    assert "blue shirt" in ana["states"][1]["description"]
    assert "leather shoes" in ana["states"][1]["description"]


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


def test_completion_keeps_known_traits_and_only_fills_missing_ones():
    response = block(event(identity_description="brown eyes", state_description=""))
    _apply_continuity_visual_completions(response, {"replacements": [{
        "collection": "character_events", "id": "first_little_pig", "effective_unit_id": 0,
        "identity_description": "blue eyes, short pink bristles", "state_description": "young piglet",
    }]})
    assert "brown eyes" in response["character_events"][0]["identity_description"]
    assert "blue eyes" not in response["character_events"][0]["identity_description"]
    assert "pink bristles" in response["character_events"][0]["identity_description"]


def test_known_characters_remain_available_when_only_a_group_is_mentioned():
    ledger = _empty_continuity()
    source = units("Three little pigs lived together.")
    _merge_continuity_events(ledger, block(event()), source)
    system, user = _continuity_analyzer_messages(source, ledger, [], era_request="")
    assert "char_first_little_pig" in user
    assert "red neckerchief" in user
    assert "Never invent mobility" in system


def test_existing_long_prompt_is_compacted_without_mutating_ledger():
    ledger = _empty_continuity()
    source = units("The first little pig smiled.")
    _merge_continuity_events(ledger, block(event()), source)
    pig = ledger["characters"][0]
    pig["identity_description"] = ", ".join(["pink piglet with brown eyes and upright ears", "pale piglet with blue eyes and floppy ears"] * 4)
    before = deepcopy(ledger)
    prompt = compile_scene_prompt({"continuity": ledger}, {"prompt": "first little pig smiles.", "characters": [pig["states"][0]["id"]]})
    assert prompt.count("brown eyes") == 1 and "blue eyes" not in prompt
    assert len(prompt.split()) < 60
    assert ledger == before


def test_explicit_raw_override_is_never_compacted():
    raw = "SCENE: user text. " * 90
    assert compile_effective_scene_prompt({}, {"generation_overrides": {"raw_prompt": raw}}) == raw.strip()


def test_nonvisual_personality_is_not_an_image_identity_trait():
    assert compact_visual_description("brown eyes, loyal and hardworking personality") == "brown eyes"


def test_trait_provenance_survives_project_serialization(tmp_path):
    from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state
    ledger = _empty_continuity()
    _merge_continuity_events(ledger, block(event()), units("The little pig arrived."))
    save_storyboard_state(tmp_path, {"plan": {"continuity": ledger}, "source": {}, "scenes": []})
    restored = load_storyboard_state(tmp_path)
    assert restored["plan"]["continuity"] == ledger


def test_translated_source_delta_does_not_depend_on_english_change_keywords():
    ledger = _empty_continuity()
    source = units("Marie entre dans la maison.", "Marie remplace sa robe par une chemise bleue.")
    _merge_continuity_events(ledger, block(event("Marie", identity_description="brown eyes", state_description="wearing a red dress")), source)
    response = block(event("Marie", effective_unit_id=1, event_type="explicit_change", identity_description="",
        state_description="wearing a blue shirt", evidence=source[1]["text"]))
    _normalize_continuity_event_semantics(response, source, ledger)
    _merge_continuity_events(ledger, response, source)
    states = ledger["characters"][0]["states"]
    assert len(states) == 2
    assert "red dress" in states[0]["description"]
    assert "blue shirt" in states[1]["description"] and "dress" not in states[1]["description"]


def test_real_place_damage_replaces_intact_state_without_rebuilding_identity():
    ledger = _empty_continuity()
    source = units("The straw house stood by the road.", "The wolf destroyed the straw house.")
    initial = {"id": "house", "name": "straw house", "effective_unit_id": 0,
        "event_type": "first_appearance", "identity_description": "yellow straw house", "state_description": "intact"}
    _merge_continuity_events(ledger, {"location_events": [initial]}, source)
    delta = {**initial, "effective_unit_id": 1, "event_type": "explicit_change", "identity_description": "", "state_description": "destroyed"}
    _merge_continuity_events(ledger, {"location_events": [delta]}, source)
    house = ledger["locations"][0]
    assert house["identity_description"] == "yellow straw house"
    assert [s["description"] for s in house["states"]] == ["intact", "destroyed"]

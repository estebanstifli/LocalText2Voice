import pytest

from app.core.video_storyboard_appearance import appearance_schema, merge_appearance, selected_appearance
from app.core.video_storyboard_planner import (
    _empty_continuity, _merge_continuity_events, _normalize_continuity_event_semantics,
    _normalize_unit_binding_references, _finalize_continuity_periods,
    _placeholder_continuity_events, _continuity_visual_completion_messages,
    _continuity_analyzer_schema, _entity_by_reference, _unit_binder_messages,
)
from app.core.video_storyboard_comfyui import compile_scene_prompt


def units(*texts):
    return [{"id": i, "text": t, "start_seconds": 2.0 + i * 8,
             "end_seconds": 10.0 + i * 8} for i, t in enumerate(texts)]


def event(name="Ana", unit=0, kind="first_appearance", **values):
    return {"id": name.lower().replace(" ", "_"), "name": name, "aliases": [],
            "effective_unit_id": unit, "event_type": kind,
            "identity_description": "oval face, dark eyes", "context_description": "",
            "appearance": {}, "evidence": "", **values}


def merge(ledger, source, characters=(), locations=(), groups=()):
    response = {"character_events": list(characters), "location_events": list(locations),
                "era_events": [], "groups": list(groups)}
    _normalize_continuity_event_semantics(response, source, ledger)
    _merge_continuity_events(ledger, response, source)
    return response


@pytest.mark.parametrize("action", [
    "Has built a small yellow straw house", "Building a strong brick house, mortar",
    "Living with their mother in a cottage", "Hungry", "Face turns red from huffing",
    "They live happily ever after", "Sheltering two pigs with the door locked",
])
def test_legacy_actions_are_not_character_states_even_with_a_matching_quote(action):
    source = units("Ana arrives.", action)
    ledger = _empty_continuity()
    merge(ledger, source, [event(appearance={"wardrobe": "red dress"})])
    old_reply = event(unit=1, kind="explicit_change", identity_description="",
                      state_description=action, evidence=action)
    old_reply.pop("appearance")
    merge(ledger, source, [old_reply])
    assert len(ledger["characters"][0]["states"]) == 1
    assert ledger["characters"][0]["states"][0]["description"] == "red dress"


def test_wrong_entity_slots_and_empty_changes_never_create_a_state():
    ledger = _empty_continuity()
    source = units("Ana arrives.", "Ana has built a house.")
    merge(ledger, source, [event()])
    merge(ledger, source, [event(unit=1, kind="explicit_change", identity_description="",
                              appearance={"structure": "small house", "wardrobe": None}, evidence=source[1]["text"])])
    assert len(ledger["characters"][0]["states"]) == 1
    assert ledger["characters"][0]["states"][0]["description"] == ""


@pytest.mark.parametrize("slot,new", [
    ("age", "forty-year-old adult"), ("wardrobe", "white kimono and wooden sandals"),
    ("equipment", "wheelchair"), ("body", "healed scar on the left cheek"),
    ("grooming", "shaved head"), ("form", "large silver dragon with amber eyes"),
])
def test_typed_changes_do_not_depend_on_source_language(slot, new):
    source = units("登場人物のアナです。", "物語に記された外見の変化です。")
    ledger = _empty_continuity()
    merge(ledger, source, [event(appearance={"age": "young adult", "wardrobe": "red dress"})])
    merge(ledger, source, [event(unit=1, kind="explicit_change", identity_description="",
                              appearance={slot: new}, evidence=source[1]["text"])])
    states = ledger["characters"][0]["states"]
    assert len(states) == 2
    assert new in states[1]["description"]
    if slot != "wardrobe":
        assert "red dress" in states[1]["description"]


def test_revelation_corrects_inference_but_is_not_an_evolution_or_reinvention():
    source = units("Ana enters.", "Ana has always worn a yellow kimono.", "Ana smiles.")
    ledger = _empty_continuity()
    merge(ledger, source, [event(appearance={"wardrobe": "red dress"})])
    merge(ledger, source, [event(unit=1, kind="stable_revelation", identity_description="",
                              appearance={"wardrobe": "yellow kimono"}, evidence=source[1]["text"])])
    merge(ledger, source, [event(unit=2, appearance={"wardrobe": "blue suit"}, evidence=source[2]["text"])])
    states = ledger["characters"][0]["states"]
    assert len(states) == 1 and states[0]["description"] == "yellow kimono"


def test_explicit_slot_clearing_and_transformation_restore_base_identity():
    source = units("Ana arrived.", "A spell turns Ana into a silver dragon.", "The spell ends and Ana regains her human form.")
    ledger = _empty_continuity()
    merge(ledger, source, [event()])
    merge(ledger, source, [event(unit=1, kind="explicit_change", identity_description="",
                              appearance={"form": "silver dragon"}, evidence=source[1]["text"])])
    merge(ledger, source, [event(unit=2, kind="explicit_change", identity_description="",
                              appearance={"form": ""}, evidence=source[2]["text"])])
    character = ledger["characters"][0]
    assert len(character["states"]) == 3
    assert selected_appearance(character, character["states"][1]) == ("silver dragon", "")
    assert selected_appearance(character, character["states"][2]) == ("oval face, dark eyes", "")
    prompt = compile_scene_prompt({"continuity": ledger}, {
        "prompt": "Ana flies above the village.", "characters": [character["states"][1]["id"]]})
    assert "silver dragon" in prompt and "oval face" not in prompt


def test_same_block_location_ids_are_reused_and_conditions_replace_each_other():
    source = units("A lighthouse was under construction.", "The lighthouse was finished.", "The lighthouse collapsed.")
    ledger = _empty_continuity()
    merge(ledger, source, locations=[
        event("lighthouse", appearance={"condition": "under construction"}, identity_description="white stone tower"),
        event("lighthouse", 1, "explicit_change", id="completed_lighthouse", identity_description="",
              appearance={"condition": "finished"}, evidence=source[1]["text"]),
        event("lighthouse", 2, "explicit_change", id="ruined_lighthouse", identity_description="",
              appearance={"condition": "collapsed"}, evidence=source[2]["text"]),
    ])
    assert len(ledger["locations"]) == 1
    assert [s["description"] for s in ledger["locations"][0]["states"]] == ["under construction", "finished", "collapsed"]


def test_manual_state_edit_remains_authoritative():
    text, meta = merge_appearance("", None, {"wardrobe": "red dress"}, kind="character", initial=True, changed=False)
    updated, updated_meta = merge_appearance("user's outfit", meta, {"wardrobe": "blue robe"},
                                           kind="character", initial=False, changed=True, source_backed=True)
    assert updated == "user's outfit" and updated_meta["manual"]


def test_completion_receives_entity_name_context_and_evidence_not_just_opaque_id():
    source = units("They crossed the open fields towards the brick house.")
    response = {"location_events": [event("fields", identity_description="", context_description="open countryside outside the village",
                                          evidence="crossed the open fields")]}
    incomplete = _placeholder_continuity_events(response)
    assert incomplete[0]["name"] == "fields"
    system, user = _continuity_visual_completion_messages(incomplete, source, era_request="")
    assert "open countryside outside the village" in user and "crossed the open fields" in user
    assert "not another subject nearby" in system


def group(name="siblings", unit=0, members=None):
    return {"id": name, "name": name, "aliases": ["the siblings"], "member_ids": members or ["ana", "luis"], "effective_unit_id": unit}


def test_group_introduction_backdates_individuals_without_creating_shared_design():
    source = units("The siblings walked together.", "Ana smiled.", "Luis waved.")
    ledger = _empty_continuity()
    merge(ledger, source, [event("siblings"), event("Ana", 1), event("Luis", 2)], groups=[group()])
    assert len(ledger["characters"]) == 2
    assert [c["states"][0]["from_seconds"] for c in ledger["characters"]] == [2.0, 2.0]
    bindings = {"unit_bindings": [{"unit_id": 0, "character_ids": ["the siblings"], "location_ids": [], "era_id": ""}]}
    _normalize_unit_binding_references(bindings, ledger)
    assert bindings["unit_bindings"][0]["character_ids"] == ["char_ana", "char_luis"]
    _, binder = _unit_binder_messages(source, ledger, [])
    assert '"groups":' in binder and '"member_ids":["char_ana","char_luis"]' in binder


def test_group_membership_evolves_without_backdating_later_joiners_and_survives_save(tmp_path):
    from app.core.video_storyboard_project import load_storyboard_state, save_storyboard_state
    source = units("The crew sets sail.", "Eva joins the crew.", "The crew returns.")
    ledger = _empty_continuity()
    merge(ledger, source, [event("Ana"), event("Luis"), event("Eva", 1)], groups=[
        group("crew"), group("crew", 1, ["ana", "luis", "eva"]),
    ])
    assert ledger["characters"][2]["states"][0]["from_seconds"] == 10.0
    bindings = {"unit_bindings": [{"unit_id": i, "character_ids": ["crew"], "location_ids": [], "era_id": ""} for i in range(3)]}
    _normalize_unit_binding_references(bindings, ledger)
    assert len(bindings["unit_bindings"][0]["character_ids"]) == 2
    assert len(bindings["unit_bindings"][2]["character_ids"]) == 3
    _finalize_continuity_periods(ledger, 26)
    assert ledger["groups"][0]["states"][0]["to_seconds"] == 10.0
    save_storyboard_state(tmp_path, {"plan": {"continuity": ledger}, "source": {}, "scenes": []})
    assert load_storyboard_state(tmp_path)["plan"]["continuity"] == ledger


def test_unknown_group_members_warn_and_shared_alias_does_not_pick_first_person():
    source = units("Ana and Luis arrive.")
    ledger = _empty_continuity()
    merge(ledger, source, [event("Ana", aliases=["the sibling"]), event("Luis", aliases=["the sibling"])], groups=[group(members=["ana", "imaginary"])])
    assert not ledger["groups"] and "unknown members" in ledger["warnings"][0]
    assert _entity_by_reference(ledger["characters"], "the sibling") is None
    assert _entity_by_reference(ledger["characters"], "char_luis")["name"] == "Luis"


def test_anonymous_collective_is_not_forced_into_individuals():
    ledger = _empty_continuity()
    merge(ledger, units("An anonymous crowd fills the square."), [event("crowd", identity_description="crowd in dark winter coats")])
    assert len(ledger["characters"]) == 1 and not ledger["groups"]


def test_structured_schema_is_closed_and_appearance_types_are_separate():
    schema = _continuity_analyzer_schema()
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(schema)
    assert "condition" not in appearance_schema("character")["properties"]
    assert "wardrobe" not in appearance_schema("location")["properties"]
    assert "state_description" not in schema["properties"]["character_events"]["items"]["properties"]


def test_long_book_revelations_do_not_accumulate_a_hundred_identity_features():
    source = units("Ana appears.", *[f"Ana has a distinctive mark number {i}." for i in range(15)])
    ledger = _empty_continuity()
    merge(ledger, source, [event(identity_description="oval face, green eyes, brown hair")])
    for index in range(1, len(source)):
        merge(ledger, source, [event(unit=index, kind="stable_revelation",
              identity_description=f"distinctive mark number {index - 1}", evidence=source[index]["text"])])
    character = ledger["characters"][0]
    assert len(character["identity_traits"]["facts"]) <= 5
    assert len(character["identity_description"].split()) <= 35
    assert len(character["states"]) == 1


def test_typed_contract_does_not_require_an_age_change_for_someone_elses_birth():
    from app.core.video_storyboard_planner import _continuity_analyzer_validation_issues
    source = units("Ana wore a red coat when her daughter was born.")
    response = {"character_events": [event(appearance={"wardrobe": "red coat"})],
                "location_events": [], "era_events": [], "groups": []}
    _normalize_continuity_event_semantics(response, source, _empty_continuity())
    assert _continuity_analyzer_validation_issues(response, source, _empty_continuity()) == []

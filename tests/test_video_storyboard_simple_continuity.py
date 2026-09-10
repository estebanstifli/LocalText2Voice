from copy import deepcopy
import json
from unittest.mock import patch
import pytest

from app.core.storyboard_analysis_settings import request_continuity, CONTRACT, instruction, normalize
from app.core.storyboard_simple_continuity import registry_schema, profiles_schema
from app.core.video_storyboard_planner import (
    _empty_continuity, _continuity_analyzer_schema, _normalize_continuity_event_semantics,
    _merge_continuity_events, _finalize_continuity_periods,
)
from app.core.video_storyboard_comfyui import compile_scene_prompt


def units(*texts):
    return [{"id": i, "text": t, "start_seconds": i * 10.0, "end_seconds": (i + 1) * 10.0}
            for i, t in enumerate(texts)]


def roster(name="Ana", known_id="", unit=0):
    return {"name": name, "known_id": known_id, "unit": unit, "aliases": []}


def run(ledger, source, identities, profiles, *, groups=None, location=False):
    calls = []
    def request(settings, schema, system, user, **kwargs):
        label = kwargs["request_label"].rsplit(" / ", 1)[-1]
        calls.append((label, schema, json.loads(user)))
        if schema is None:
            return "Source-based place report."
        if label in {"character_registry", "location_registry"}:
            selected = label == ("location_registry" if location else "character_registry")
            return {"entities": identities if selected else [], "groups": groups or []}
        if label in {"character_appearance", "location_appearance"}:
            return profiles
        return {"era_events": []}
    result = request_continuity(request,
        {"continuity_analysis": {"process": "simple", "era_mode": "provided"}},
        _continuity_analyzer_schema(), "", "",
        context=(source, ledger, "Source-based character report.", ""), request_label="block")
    _normalize_continuity_event_semantics(result, source, ledger)
    _merge_continuity_events(ledger, result, source)
    return calls, result


def test_empty_change_lists_keep_one_state_and_do_not_expose_typed_slots():
    ledger = _empty_continuity()
    source = units("Ana arrives.", "Ana builds a house.")
    calls, _ = run(ledger, source, [roster()], {
        "definitions": [{"name": "Ana", "description": "Brown-eyed woman in a red coat"}],
        "changes": [], "revelations": [],
    })
    record = ledger["characters"][0]
    assert len(record["states"]) == 1
    assert record["states"][0]["description"] == ""
    prompt = compile_scene_prompt({"continuity": ledger}, {"prompt": "Ana builds a house.",
                                  "characters": [record["states"][0]["id"]]})
    assert prompt.count("red coat") == 1 and "unchanged" not in prompt
    profile_schema = next(c[1] for c in calls if c[0] == "character_appearance")
    assert "wardrobe" not in json.dumps(profile_schema) and "form" not in json.dumps(profile_schema)


def test_physical_change_replaces_profile_and_preserves_old_period():
    ledger = _empty_continuity()
    source = units("Ana arrives.", "Ana changes into a blue coat.")
    run(ledger, source, [roster()], {
        "definitions": [{"name": "Ana", "description": "Brown-eyed woman in a red coat"}],
        "changes": [{"name": "Ana", "unit": 1, "description": "Brown-eyed woman in a blue coat",
                     "evidence": source[1]["text"]}], "revelations": [],
    })
    _finalize_continuity_periods(ledger, 20)
    record = ledger["characters"][0]
    assert len(record["states"]) == 2 and record["states"][0]["to_seconds"] == 10
    for index, wanted, unwanted in [(0, "red coat", "blue coat"), (1, "blue coat", "red coat")]:
        prompt = compile_scene_prompt({"continuity": ledger},
            {"prompt": "Ana stands.", "characters": [record["states"][index]["id"]]})
        assert wanted in prompt and unwanted not in prompt


def test_revelation_is_not_a_new_period_and_existing_definitions_are_ignored():
    ledger = _empty_continuity()
    source = units("Ana arrives.", "Ana has blue eyes.")
    run(ledger, source, [roster()], {"definitions": [{"name": "Ana", "description": "Woman with green eyes"}],
                                   "changes": [], "revelations": []})
    run(ledger, source, [roster(known_id="char_ana")], {
        "definitions": [{"name": "Ana", "description": "Woman with hazel eyes"}],
        "changes": [], "revelations": [{"name": "Ana", "unit": 1, "description": "Woman with blue eyes",
                                       "evidence": "Narration Unit 1: 'Ana has blue eyes.'"}],
    })
    assert ledger["characters"][0]["identity_description"] == "Woman with blue eyes"
    assert len(ledger["characters"][0]["states"]) == 1


def test_group_uses_individual_profiles_and_invalid_evidence_warns():
    ledger = _empty_continuity()
    source = units("The siblings arrive.", "Ana smiles.", "Luis waves.")
    run(ledger, source, [roster("Ana", unit=1), roster("Luis", unit=2)], {
        "definitions": [{"name": "Ana", "description": "Woman in red coat"},
                        {"name": "Luis", "description": "Man in green shirt"}],
        "changes": [{"name": "Ana", "unit": 2, "description": "Woman in blue coat", "evidence": "Invented quotation"}],
        "revelations": [],
    }, groups=[{"name": "siblings", "unit": 0, "members": ["Ana", "Luis"]}])
    assert len(ledger["characters"]) == 2 and len(ledger["groups"]) == 1
    assert all(c["states"][0]["from_seconds"] == 0 for c in ledger["characters"])
    assert ledger["warnings"]


def test_place_change_replaces_old_structure_not_appends_it():
    ledger = _empty_continuity()
    source = units("A tower stood on the hill.", "The tower collapsed into rubble.")
    run(ledger, source, [roster("tower")], {
        "definitions": [{"name": "tower", "description": "Tall intact stone tower on a grassy hill"}],
        "changes": [{"name": "tower", "unit": 1, "description": "Pile of broken stone on a grassy hill",
                     "evidence": source[1]["text"]}], "revelations": [],
    }, location=True)
    record = ledger["locations"][0]
    prompt = compile_scene_prompt({"continuity": ledger},
        {"prompt": "Rubble lies on the hill.", "locations": [record["states"][1]["id"]]})
    assert "broken stone" in prompt and "intact" not in prompt


def test_manual_profile_and_serialization_are_preserved(tmp_path):
    from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state
    ledger = _empty_continuity()
    source = units("Ana arrives.", "Ana has blue eyes.")
    run(ledger, source, [roster()], {"definitions": [{"name": "Ana", "description": "Woman in a red coat"}],
                                   "changes": [], "revelations": []})
    ledger["characters"][0]["identity_description"] = "User's custom appearance"
    run(ledger, source, [roster(known_id="char_ana")], {
        "definitions": [], "changes": [],
        "revelations": [{"name": "Ana", "unit": 1, "description": "Woman with blue eyes", "evidence": source[1]["text"]}],
    })
    assert ledger["characters"][0]["identity_description"] == "User's custom appearance"
    save_storyboard_state(tmp_path, {"plan": {"continuity": ledger}, "source": {}, "scenes": []})
    assert load_storyboard_state(tmp_path)["plan"]["continuity"] == ledger


def test_compact_dispatch_is_unchanged_and_simple_is_never_called():
    config = {"continuity_analysis": {"process": "compact"}}
    expected = {"character_events": [], "location_events": [], "era_events": [], "groups": []}
    schema = _continuity_analyzer_schema()
    with patch("app.core.storyboard_simple_continuity.run_simple") as simple:
        with patch("app.core.video_storyboard_planner._request_plan", return_value=expected) as request:
            result = request_continuity(request, config, schema, "unused", "original compact input", context=([], {}, "", ""))
            assert result == expected
            args = request.call_args.args
            assert args[1] == schema and args[3] == "original compact input"
            assert args[2] == instruction(normalize(config["continuity_analysis"]), "structure") + "\n" + CONTRACT
        simple.assert_not_called()


def test_simple_schemas_are_closed():
    def check(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values(): check(child)
        if isinstance(value, list):
            for child in value: check(child)
    check(registry_schema())
    check(registry_schema(False))
    check(profiles_schema())

from copy import deepcopy
from app.core.storyboard_entity_names import clean_names, resolve, mentioned_records, resolve_references
from app.core.video_storyboard_comfyui import compile_effective_scene_prompt


def record(identifier, name):
    return {"id": identifier, "name": name, "aliases": [], "states": [
        {"id": identifier + "_state_1", "description": "yellow shirt and overalls",
         "from_seconds": 0, "to_seconds": 500}]}


def test_project3_qualified_name_and_existing_project_prompt():
    pig = record("pig1", "First Little Pig (Straw House)")
    assert resolve("First Little Pig", [pig]) is pig
    assert resolve("The FIRST LITTLE PIG", [pig]) is pig
    scene = {"prompt": "The First Little Pig kneels beside the Straw House.", "characters": [], "start_seconds": 316}
    plan = {"continuity": {"characters": [pig]}, "style": {}}
    before = deepcopy(plan)
    assert "yellow shirt and overalls" in compile_effective_scene_prompt(plan, scene)
    assert plan == before
    scene["generation_overrides"] = {"raw_prompt": "exact user text"}
    assert compile_effective_scene_prompt(plan, scene) == "exact user text"


def test_cleanup_preserves_ids_and_aliases():
    records = [record("pig1", "First Little Pig (Straw House)")]
    clean_names(records)
    assert records[0]["name"] == "First Little Pig"
    assert records[0]["aliases"] == ["First Little Pig (Straw House)"]
    assert records[0]["id"] == "pig1"


def test_ambiguous_names_are_never_merged_or_selected():
    records = [record("a", "John (father)"), record("b", "John (son)")]
    clean_names(records)
    assert [r["name"] for r in records] == ["John (father)", "John (son)"]
    assert resolve("John", records) is None
    assert mentioned_records("John walks.", records) == []
    assert resolve("John (son)", records) is records[1]


def test_no_substring_matching_and_longest_mention_wins():
    records = [record("a", "Maria"), record("b", "Maria Elena"), record("c", "First Little Pig")]
    assert resolve("Pig", records) is None
    assert mentioned_records("Maria Elena smiles.", records) == [records[1]]


def test_no_llm_for_normalized_names():
    records = [record("p", "First Little Pig (Straw House)")]
    visuals = [{"characters": ["First Little Pig"], "locations": []}]
    def request(*args):
        raise AssertionError("No LLM should be called")
    resolve_references(visuals, [{"narration": "The pig walks."}],
                       {"characters": records, "locations": []}, request, lambda _: None, lambda: None)
    assert visuals[0]["characters"] == ["p"]


def test_ambiguity_is_batched_and_cannot_create_aliases_or_ids():
    records = [record("a", "John (father)"), record("b", "John (son)")]
    visuals = [{"characters": ["John"], "locations": []}, {"characters": ["he"], "locations": []}]
    calls, warnings = [], []
    def request(schema, instruction, data, label):
        calls.append(data)
        return {"bindings": [{"position": 0, "entity_id": "b"}, {"position": 1, "entity_id": "invented"}]}
    resolve_references(visuals, [{"narration": "The son walks."}, {"narration": "He pauses."}],
                       {"characters": records, "locations": []}, request, warnings.append, lambda: None)
    assert len(calls) == 1 and len(calls[0]) == 2
    assert visuals[0]["characters"] == ["b"] and visuals[1]["characters"] == []
    assert all(not r["aliases"] for r in records)
    assert warnings

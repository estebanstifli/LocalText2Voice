from copy import deepcopy
from app.core.storyboard_character_changes import build_character_states
from app.core.storyboard_conversation import add_simple_profiles, APPEARANCE


TEXT = "Ana waits. Ana puts on a red coat. Ana walks away."
UNITS = [dict(id=1, text_start=0, text_end=11, start_seconds=2),
         dict(id=2, text_start=11, text_end=35, start_seconds=12),
         dict(id=3, text_start=35, text_end=len(TEXT), start_seconds=22)]


def run_changes(rows, portrait="Woman with short black hair, red coat"):
    records, warnings, calls = [], [], []
    add_simple_profiles(records, [{"character": "Ana", "visual_description":
                                  "Woman with short black hair, blue coat"}], "character", 30, warnings)
    reports = [dict(text=TEXT, characters="Ana changes her coat.", appearance="Short black hair.")]
    sections = {k: reports[0][k] for k in ("characters", "appearance")}
    def request(schema, instruction, payload, label):
        calls.append(deepcopy(payload))
        return {"changes": rows} if "changes" in schema["properties"] else {"visual_description": portrait}
    build_character_states(records, reports, sections, TEXT, UNITS, 30, request,
                           warnings.append, lambda: None)
    return records[0]["states"], warnings, calls


def test_explicit_change_uses_cue_offset_and_replaces_not_concatenates():
    states, warnings, calls = run_changes([dict(character="Ana", start_quote="Ana puts on a red coat.",
                                               visual_change="red coat", kind="clothing")])
    assert [(s["from_seconds"], s["to_seconds"]) for s in states] == [(0, 12), (12, 30)]
    assert states[1]["description"] == "Woman with short black hair, red coat"
    assert states[1]["source_unit_start"] == 2
    assert states[1]["evidence"] == "Ana puts on a red coat."
    assert calls[1]["previous_portrait"] == states[0]["description"]
    assert not warnings


def test_no_changes_no_extra_portrait_request():
    states, warnings, calls = run_changes([])
    assert len(states) == len(calls) == 1
    assert not warnings


def test_invalid_identity_quote_and_action_warn_without_new_states():
    rows = [dict(character="Unknown", start_quote="Ana waits.", visual_change="old", kind="age"),
            dict(character="Ana", start_quote="Invented quote", visual_change="old", kind="age"),
            dict(character="Ana", start_quote="Ana walks away.", visual_change="walks", kind="action")]
    states, warnings, calls = run_changes(rows)
    assert len(states) == len(calls) == 1
    assert len(warnings) == 3


def test_same_time_changes_share_one_state_and_empty_portrait_is_safe():
    rows = [dict(character="Ana", start_quote="Ana puts on a red coat.", visual_change="red coat", kind="clothing"),
            dict(character="Ana", start_quote="Ana puts on a red coat.", visual_change="short gray hair", kind="hair")]
    states, _, calls = run_changes(rows)
    assert len(states) == len(calls) == 2
    assert len(calls[1]["changes"]) == 2
    states, warnings, _ = run_changes(rows, portrait="")
    assert len(states) == 1 and warnings


def test_hair_instruction_is_human_only_and_allows_baldness():
    assert "For humans only" in APPEARANCE
    assert "hair length and color, or baldness" in APPEARANCE
    assert "choose them once" in APPEARANCE

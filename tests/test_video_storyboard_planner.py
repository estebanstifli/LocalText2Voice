from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from app.core.video_storyboard_planner import (
    VideoStoryboardPlanningError,
    _continuity_analyzer_validation_issues,
    _empty_continuity,
    _finalize_continuity_periods,
    _http_ollama_stream,
    _litellm_direct_completion,
    _litellm_direct_responses,
    _merge_continuity_events,
    _normalize_scene_continuity_references,
    _normalize_binder_era_transitions,
    _normalize_unit_binding_references,
    _repair_explicit_character_changes,
    plan_video_storyboard,
)


def _settings() -> dict:
    return {
        "llm_provider": "ollama",
        "scene": {
            "minimum_seconds": 4,
            "target_seconds": 8,
            "maximum_seconds": 20,
        },
        "image": {"style_prompt": "painted cinematic realism"},
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3:8b-storyboard",
            "context_length": 8192,
            "timeout_seconds": 300,
        },
    }


def _source() -> dict:
    return {
        "project_id": 7,
        "title": "Train journey",
        "text": "The train crossed the valley. Mara looked through the window.",
        "duration_seconds": 13.0,
        "narration_cues": [
            {
                "start_seconds": 0,
                "end_seconds": 6,
                "duration_seconds": 6,
                "text": "The train crossed the valley.",
            },
            {
                "start_seconds": 6,
                "end_seconds": 13,
                "duration_seconds": 7,
                "text": "Mara looked through the window.",
            },
        ],
    }


def _fake_ollama(_url, payload, _timeout, _headers=None):
    schema = payload["format"]
    if "character_events" in schema["properties"]:
        user = payload["messages"][1]["content"]
        units = json.loads(
            user.split("Narration units:\n", 1)[1].split(
                "\n\nNew IDs", 1
            )[0]
        )
        mara_units = [
            unit for unit in units if "Mara" in unit["narration"]
        ]
        first_unit = int(units[0]["unit_id"])
        value = {
            "character_events": (
                [
                    {
                        "id": "mara",
                        "name": "Mara",
                        "aliases": [],
                        "effective_unit_id": int(mara_units[0]["unit_id"]),
                        "event_type": "first_appearance",
                        "identity_description": "oval face and dark eyes",
                        "state_description": "young woman with short dark hair and a blue coat",
                        "evidence": mara_units[0]["narration"],
                    }
                ]
                if mara_units
                else []
            ),
            "location_events": [],
            "era_events": [
                {
                    "id": "late_nineteenth_century",
                    "effective_unit_id": first_unit,
                    "description": "late nineteenth century",
                    "material_culture": "period railway materials and clothing",
                    "evidence": units[0]["narration"],
                }
            ],
        }
        return {"message": {"content": json.dumps(value)}}
    if "unit_bindings" in schema["properties"]:
        user = payload["messages"][1]["content"]
        ledger = json.loads(
            user.split("Ledger (the only allowed IDs):\n", 1)[1].split(
                "\n\nConsecutive narration units", 1
            )[0]
        )
        units = json.loads(
            user.split("Consecutive narration units:\n", 1)[1].split(
                "\n\nReturn exactly", 1
            )[0]
        )
        character_ids = {item["id"] for item in ledger["characters"]}
        era_id = str(ledger.get("current_era", {}).get("id") or "")
        value = {
            "unit_bindings": [
                {
                    "unit_id": int(unit["unit_id"]),
                    "character_ids": (
                        ["char_mara"]
                        if "Mara" in unit["narration"] and "char_mara" in character_ids
                        else []
                    ),
                    "location_ids": [],
                    "era_id": era_id,
                }
                for unit in units
            ],
        }
        return {"message": {"content": json.dumps(value)}}
    count = schema["properties"]["scenes"]["minItems"]
    user = payload["messages"][1]["content"]
    assignments_text = user.split("Scenes:\n", 1)[1]
    assignments_text = assignments_text.split("\n\nChoose style_preset", 1)[0]
    assignments_text = assignments_text.split("\n\nReturn exactly", 1)[0]
    assignments = json.loads(
        assignments_text
    )
    value = {
        "scenes": [
            {
                "character_state_ids": (
                    [
                        value["state_id"]
                        for value in assignments[index]["available_continuity"]["characters"]
                    ]
                    if "Mara" in assignments[index]["narration"]
                    else []
                ),
                "location_state_ids": [
                    value["state_id"]
                    for value in assignments[index]["available_continuity"]["locations"]
                ],
                "era_state_id": next(
                    (
                        value["state_id"]
                        for value in assignments[index]["available_continuity"]["eras"]
                    ),
                    "",
                ),
                "visual": (
                    f"English image prompt {index + 1}: a railway carriage crosses a broad green valley while morning sunlight catches brass fittings and glass windows, distant mountain ridges fade into atmospheric haze, and wild grasses fill the foreground with believable period detail."
                ),
            }
            for index in range(count)
        ],
    }
    if "style_preset" in schema.get("required", []):
        value["style_preset"] = "illustration"
    return {"message": {"content": json.dumps(value)}}


def test_ollama_planner_returns_normalized_structured_scenes() -> None:
    updates: list[tuple[int, int]] = []
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        plan = plan_video_storyboard(
            _source(),
            _settings(),
            progress=lambda current, total: updates.append((current, total)),
        )

    assert request.call_count == 3
    assert updates == [(1, 3), (2, 3), (3, 3)]
    assert plan["title"] == "Train journey"
    assert plan["base_seed"] == int.from_bytes(
        hashlib.sha256(_source()["text"].encode("utf-8")).digest()[:6],
        "big",
    )
    assert len(plan["scenes"]) == 2
    assert sum(scene["duration"] for scene in plan["scenes"]) == 13.0
    assert plan["scenes"][0]["id"] == "001"
    assert plan["scenes"][1]["id"] == "002"
    assert plan["scenes"][0]["prompt"].startswith("English image prompt 1:")
    assert plan["scenes"][0]["image_path"] == ""
    assert plan["scenes"][0]["status"] == "planned"
    assert plan["scenes"][0]["characters"] == []
    assert plan["scenes"][1]["characters"] == ["char_mara_state_1"]
    assert plan["scenes"][0]["era"] == "late nineteenth century"
    assert plan["narrative_context"]["era"] == "late nineteenth century"
    assert plan["narrative_context"]["era_source"] == "detected"
    assert "continuity" not in plan["style"]
    assert plan["style"]["characters"] == [
        "char_mara_state_1: Mara, oval face and dark eyes, young woman with short dark hair and a blue coat"
    ]
    assert plan["continuity"]["characters"][0]["name"] == "Mara"
    assert plan["continuity"]["characters"][0]["states"][0]["to_seconds"] == 13.0
    request_payload = request.call_args.args[1]
    assert request_payload["options"]["num_predict"] >= 800
    assert request_payload["options"]["num_ctx"] == 8192
    assert request_payload["think"] is False
    assert request_payload["options"]["repeat_penalty"] > 1.0
    request_text = request_payload["messages"][1]["content"]
    assert "zoom_in" not in request_text
    assert "zoom_out" not in request_text
    assert "Crossfades" not in request_text
    assert "Atomic timed semantic units" not in request_text
    assert "Source text:" not in request_text
    assert "1280x720" not in request_text
    assert "baseSeed" not in request_text
    scene_schema = request_payload["format"]["properties"]["scenes"]["items"]
    assert "motion" not in scene_schema["properties"]
    assert "transition" not in scene_schema["properties"]
    assert set(scene_schema["required"]) == {
        "character_state_ids", "location_state_ids", "era_state_id", "visual"
    }


def test_ollama_trace_exposes_the_complete_raw_request_body() -> None:
    events: list[dict] = []

    def fake_stream(url, payload, timeout, trace, cancelled):
        return _fake_ollama(url, payload, timeout)

    with patch(
        "app.core.video_storyboard_planner._http_ollama_stream",
        side_effect=fake_stream,
    ):
        plan_video_storyboard(_source(), _settings(), trace=events.append)

    request = next(event for event in events if event.get("kind") == "request")
    assert request["endpoint"] == "http://127.0.0.1:11434/api/chat"
    assert request["attempt"] == 1
    raw = request["raw_request"]
    assert raw["model"] == "qwen3:8b-storyboard"
    assert raw["messages"][0]["role"] == "system"
    assert raw["messages"][1]["role"] == "user"
    assert raw["format"]["type"] == "object"
    assert raw["options"]["num_predict"] >= 800
    assert raw["think"] is False


def test_project_seed_override_replaces_the_automatic_audiobook_seed() -> None:
    settings = _settings()
    settings["seed_override"] = 987654321
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        result = plan_video_storyboard(_source(), settings)

    assert result["base_seed"] == 987654321
    assert "987654321" not in request.call_args.args[1]["messages"][1]["content"]


def test_planner_reports_invalid_llm_json() -> None:
    with (
        patch(
            "app.core.video_storyboard_planner._http_json",
            return_value={"message": {"content": "not-json"}},
        ),
        pytest.raises(VideoStoryboardPlanningError, match="structured JSON"),
    ):
        plan_video_storyboard(_source(), _settings())


def test_ollama_retries_once_with_compact_anti_repetition_settings() -> None:
    attempts = 0

    def invalid_then_valid(url, payload, timeout, headers=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {
                "message": {"content": '{"style": {'},
                "done_reason": "length",
            }
        if attempts == 2:
            assert "COMPACT RECOVERY" in payload["messages"][1]["content"]
            assert payload["options"]["repeat_penalty"] == 1.2
        return _fake_ollama(url, payload, timeout, headers)

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=invalid_then_valid,
    ):
        result = plan_video_storyboard(_source(), _settings())

    assert attempts == 4
    assert len(result["scenes"]) == 2


def test_planner_retries_a_batch_when_ollama_prompts_are_too_terse() -> None:
    attempts = 0

    def terse_then_detailed(url, payload, timeout, headers=None):
        nonlocal attempts
        response = _fake_ollama(url, payload, timeout, headers)
        if "scenes" not in payload["format"]["properties"]:
            return response
        attempts += 1
        if attempts == 1:
            decoded = json.loads(response["message"]["content"])
            for scene in decoded["scenes"]:
                scene["visual"] = "A train in a valley"
            response["message"]["content"] = json.dumps(decoded)
        else:
            assert "QUALITY RETRY" in payload["messages"][1]["content"]
        return response

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=terse_then_detailed,
    ):
        result = plan_video_storyboard(_source(), _settings())

    assert attempts == 2
    assert all(len(scene["prompt"].split()) >= 35 for scene in result["scenes"])


def test_complete_project_style_override_becomes_the_immutable_style_lock() -> None:
    settings = _settings()
    settings["style_override"] = {
        "medium": "charcoal concept art",
        "palette": "black, ivory and crimson",
        "lighting": "hard theatrical light",
        "continuity": "fixed costumes and angular architecture",
        "characters": ["Mara: short black hair, long crimson coat"],
        "negative": "text, watermark, photorealism",
    }
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ):
        result = plan_video_storyboard(_source(), settings)

    assert result["style"]["medium"] == "charcoal concept art"
    assert result["style"]["palette"] == "black, ivory and crimson"
    assert result["style"]["lighting"] == "hard theatrical light"
    assert result["style"]["negative"] == "text, watermark, photorealism"
    assert result["continuity"]["characters"][0]["user_authored"] is True


def test_explicit_global_style_preset_is_compiled_by_the_application() -> None:
    settings = _settings()
    settings["image"]["style_mode"] = "graphic_novel"
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        plan_video_storyboard(_source(), settings)

    prompt = request.call_args.args[1]["messages"][1]["content"]
    assert "Sophisticated graphic-novel art" not in prompt
    assert "style_preset" not in request.call_args.args[1]["format"]["required"]


def test_legacy_automatic_style_migrates_to_comic_book_without_llm_choice() -> None:
    source = {
        "title": "Two blocks",
        "text": "First chapter. Second chapter.",
        "duration_seconds": 90.0,
        "narration_cues": [
            {"start_seconds": 0.0, "end_seconds": 45.0, "text": "First chapter."},
            {"start_seconds": 45.0, "end_seconds": 90.0, "text": "Second chapter."},
        ],
    }
    settings = _settings()
    settings["image"]["style_prompt"] = ""
    settings["image"]["style_mode"] = "automatic"
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        result = plan_video_storyboard(source, settings)

    assert request.call_count == 6
    first_schema = request.call_args_list[2].args[1]["format"]
    second_schema = request.call_args_list[5].args[1]["format"]
    assert "style_preset" not in first_schema["properties"]
    assert "style_preset" not in second_schema["properties"]
    assert result["style"]["medium"].startswith(
        "comic book style"
    )
    assert result["style_mode"] == "comic_book"
    assert "split screen" in result["style"]["negative"]


def test_character_life_stages_are_stored_as_distinct_visual_states() -> None:
    source = {
        "title": "A lifetime",
        "text": "Stephen studied physics as a young man. Decades later Stephen lectured from his motorized wheelchair.",
        "duration_seconds": 14.0,
        "narration_cues": [
            {
                "start_seconds": 0.0,
                "end_seconds": 7.0,
                "text": "Stephen studied physics as a young man.",
            },
            {
                "start_seconds": 7.0,
                "end_seconds": 14.0,
                "text": "Decades later Stephen lectured from his motorized wheelchair.",
            },
        ],
    }

    def life_stage_response(url, payload, timeout, headers=None):
        response = _fake_ollama(url, payload, timeout, headers)
        value = json.loads(response["message"]["content"])
        if "character_events" in payload["format"]["properties"]:
            user = payload["messages"][1]["content"]
            units = json.loads(
                user.split("Narration units:\n", 1)[1].split("\n\nNew IDs", 1)[0]
            )
            unit_ids = [item["unit_id"] for item in units]
            value["character_events"] = [
                {
                    "id": "stephen",
                    "name": "Stephen",
                    "aliases": [],
                    "effective_unit_id": unit_ids[0],
                    "event_type": "first_appearance",
                    "identity_description": "slim build, long narrow face and glasses",
                    "state_description": "man in his early twenties with dark hair and a tweed jacket",
                    "evidence": "Stephen studied physics as a young man.",
                },
                {
                    "id": "stephen",
                    "name": "Stephen",
                    "aliases": [],
                    "effective_unit_id": unit_ids[1],
                    "event_type": "explicit_change",
                    "identity_description": "slim build, long narrow face and glasses",
                    "state_description": "older man with thinning gray-brown hair using a motorized wheelchair",
                    "evidence": "Decades later Stephen lectured from his motorized wheelchair.",
                },
            ]
        elif "unit_bindings" in payload["format"]["properties"]:
            for binding in value["unit_bindings"]:
                binding["character_ids"] = ["char_stephen"]
        else:
            value["scenes"][0]["character_state_ids"] = [
                "char_stephen_state_1"
            ]
            value["scenes"][1]["character_state_ids"] = [
                "char_stephen_state_2"
            ]
        response["message"]["content"] = json.dumps(value)
        return response

    settings = _settings()
    settings["scene"]["target_seconds"] = 20
    settings["scene"]["maximum_seconds"] = 20
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=life_stage_response,
    ):
        result = plan_video_storyboard(source, settings)

    assert result["scenes"][0]["characters"] == ["char_stephen_state_1"]
    assert result["scenes"][1]["characters"] == ["char_stephen_state_2"]
    states = result["continuity"]["characters"][0]["states"]
    assert len(states) == 2
    assert states[0]["to_seconds"] == 7.0
    assert states[1]["from_seconds"] == 7.0


def test_project_era_is_sent_as_guidance_and_saved_with_the_plan() -> None:
    settings = _settings()
    settings["narrative_context"] = {"era": "Ancient Rome, 1st century CE"}
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        result = plan_video_storyboard(_source(), settings)

    user_message = request.call_args.args[1]["messages"][1]["content"]
    assert "Era guidance: Ancient Rome, 1st century CE" in user_message
    assert result["narrative_context"] == {
        "era": "Ancient Rome, 1st century CE",
        "era_source": "user",
    }


def test_historical_era_periods_close_during_modern_gaps_and_can_recur() -> None:
    continuity = {
        "characters": [],
        "locations": [],
        "era_locked": False,
        "eras": [
            {
                "id": "era_rome",
                "description": "Ancient Rome",
                "material_culture": "tunics and stone streets",
            }
        ],
        "assignments": [
            {"unit_id": 0, "start_seconds": 0.0, "end_seconds": 5.0, "era_id": "era_rome"},
            {"unit_id": 1, "start_seconds": 5.0, "end_seconds": 10.0, "era_id": ""},
            {"unit_id": 2, "start_seconds": 10.0, "end_seconds": 15.0, "era_id": "era_rome"},
        ],
    }

    _finalize_continuity_periods(continuity, 15.0)

    assert [(era["from_seconds"], era["to_seconds"]) for era in continuity["eras"]] == [
        (0.0, 5.0),
        (10.0, 15.0),
    ]
    assert continuity["eras"][1]["id"] == "era_rome_period_2"
    assert continuity["assignments"][2]["era_id"] == "era_rome_period_2"


def test_qwen_state_like_entity_is_merged_into_the_existing_character() -> None:
    continuity = _empty_continuity()
    units = [
        {"id": 0, "start_seconds": 0.0, "end_seconds": 5.0},
        {"id": 1, "start_seconds": 5.0, "end_seconds": 10.0},
    ]
    _merge_continuity_events(
        continuity,
        {
            "character_events": [
                {
                    "id": "stephen_hawking",
                    "name": "Stephen Hawking",
                    "effective_unit_id": 0,
                    "event_type": "first_appearance",
                    "identity_description": "narrow face and dark hair",
                    "state_description": "young adult wearing a tweed jacket",
                },
                {
                    "id": "char_stephen_hawking_state_2",
                    "name": "Stephen Hawking state 2",
                    "effective_unit_id": 1,
                    "event_type": "stable_revelation",
                    "identity_description": "his body slowly weakened",
                    "state_description": "older adult using a wheelchair",
                },
            ],
            "location_events": [],
            "era_events": [],
        },
        units,
    )

    assert len(continuity["characters"]) == 1
    character = continuity["characters"][0]
    assert character["id"] == "char_stephen_hawking"
    assert character["identity_description"] == "narrow face and dark hair"
    assert [state["id"] for state in character["states"]] == [
        "char_stephen_hawking_state_1",
        "char_stephen_hawking_state_2",
    ]
    assert "char_stephen_hawking_state_2" not in character["aliases"]


def test_continuity_validation_rejects_missing_named_person_and_wrong_birth_age() -> None:
    units = [
        {
            "id": 0,
            "text": "Stephen Hawking was born in Oxford during the Second World War.",
            "start_seconds": 0.0,
            "end_seconds": 5.0,
        }
    ]
    empty_result = {
        "character_events": [],
        "location_events": [],
        "era_events": [
            {
                "id": "second_world_war",
                "effective_unit_id": 0,
                "description": "Second World War",
                "material_culture": "wartime Britain",
                "evidence": "during the Second World War",
            }
        ],
    }
    issues = _continuity_analyzer_validation_issues(
        empty_result, units, _empty_continuity()
    )
    assert any("Stephen Hawking" in issue for issue in issues)

    wrong_age = {
        **empty_result,
        "character_events": [
            {
                "id": "stephen_hawking",
                "name": "Stephen Hawking",
                "aliases": [],
                "effective_unit_id": 0,
                "event_type": "first_appearance",
                "identity_description": "narrow face and brown hair",
                "state_description": "young adult in academic clothing",
                "evidence": "Stephen Hawking was born",
            }
        ],
    }
    issues = _continuity_analyzer_validation_issues(
        wrong_age, units, _empty_continuity()
    )
    assert any("newborn" in issue for issue in issues)


def test_continuity_validation_detects_single_word_story_characters() -> None:
    units = [
        {
            "id": 0,
            "text": "In the Alps, Heidi ran toward the meadow.",
            "start_seconds": 0.0,
            "end_seconds": 4.0,
        },
        {
            "id": 1,
            "text": "Heidi and Peter found Snowflake, and Snowflake followed them.",
            "start_seconds": 4.0,
            "end_seconds": 8.0,
        },
    ]
    result = {
        "character_events": [], "location_events": [], "era_events": [],
    }

    issues = _continuity_analyzer_validation_issues(
        result, units, _empty_continuity()
    )

    issue = " ".join(issues)
    assert "Heidi" in issue
    assert "Peter" in issue
    assert "Snowflake" in issue
    assert "Alps" not in issue


def test_binder_normalizes_known_aliases_and_discards_unknown_locations() -> None:
    continuity = {
        "characters": [
            {
                "id": "char_heidi", "name": "Heidi", "aliases": [],
                "states": [{"source_unit_start": 0}],
            },
            {
                "id": "char_peter", "name": "Peter", "aliases": [],
                "states": [{"source_unit_start": 6}],
            },
        ],
        "locations": [
            {"id": "loc_meadow", "name": "the meadow", "aliases": ["meadow"]}
        ],
        "eras": [],
    }
    result = {
        "unit_bindings": [
            {
                "unit_id": 0,
                "character_ids": ["heidi", "peter"],
                "location_ids": ["meadow", "heidi's cabin"],
                "era_id": "current_era",
            }
        ]
    }

    _normalize_unit_binding_references(result, continuity)

    assert result["unit_bindings"][0] == {
        "unit_id": 0,
        "character_ids": ["char_heidi"],
        "location_ids": ["loc_meadow"],
        "era_id": "",
    }


def test_visual_reference_normalization_maps_entities_and_drops_hallucinations() -> None:
    continuity = {
        "characters": [
            {
                "id": "char_luis",
                "name": "Luis",
                "aliases": [],
                "states": [
                    {
                        "id": "char_luis_state_2",
                        "from_seconds": 0.0,
                        "to_seconds": 10.0,
                    }
                ],
            }
        ],
        "locations": [],
        "eras": [],
        "assignments": [
            {
                "unit_id": 7,
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "character_ids": ["char_luis"],
                "location_ids": [],
                "era_id": "",
            }
        ],
    }
    units = [{"id": 7, "text": "Luis returned.", "start_seconds": 0.0}]
    scenes = [
        {
            "character_state_ids": ["char_luis", "char_invented"],
            "location_state_ids": ["loc_invented"],
            "era_state_id": "era_invented",
        }
    ]

    _normalize_scene_continuity_references(
        scenes, units, [(0, 0)], continuity
    )

    assert scenes[0]["character_state_ids"] == ["char_luis_state_2"]
    assert scenes[0]["location_state_ids"] == []
    assert scenes[0]["era_state_id"] == ""


def test_deterministic_repair_adds_missing_synthetic_voice_state() -> None:
    continuity = {
        "characters": [
            {
                "id": "char_stephen_hawking",
                "name": "Stephen Hawking",
                "aliases": ["Hawking"],
                "identity_description": "thin face and dark hair",
                "states": [
                    {
                        "id": "char_stephen_hawking_state_1",
                        "description": "adult man using a wheelchair",
                    }
                ],
            }
        ]
    }
    units = [
        {
            "id": 9,
            "text": "With a synthetic voice, he spoke to the world.",
            "start_seconds": 40.0,
            "end_seconds": 45.0,
        }
    ]
    result = {
        "character_events": [], "location_events": [], "era_events": [],
    }

    assert _repair_explicit_character_changes(
        result, units, continuity, ["char_stephen_hawking"]
    )
    event = result["character_events"][0]
    assert event["id"] == "char_stephen_hawking"
    assert event["event_type"] == "explicit_change"
    assert "synthetic voice" in event["state_description"]
    assert "wheelchair" in event["state_description"]


def test_detected_era_stops_at_a_later_time_jump() -> None:
    continuity = {
        "era_locked": False,
        "eras": [{"id": "era_world_war_two", "source_unit_start": 0}],
        "assignments": [],
    }
    units = [
        {"id": 0, "text": "He was born during World War II."},
        {"id": 1, "text": "Years later, he studied at university."},
        {"id": 2, "text": "He became a scientist."},
    ]
    result = {
        "unit_bindings": [
            {"unit_id": value["id"], "era_id": "era_world_war_two"}
            for value in units
        ]
    }

    _normalize_binder_era_transitions(result, units, continuity)

    assert [value["era_id"] for value in result["unit_bindings"]] == [
        "era_world_war_two", "", "",
    ]


def test_app_owned_alignment_ignores_any_unrequested_llm_narration() -> None:
    def repeated_narration(url, payload, timeout, headers=None):
        response = _fake_ollama(url, payload, timeout, headers)
        decoded = json.loads(response["message"]["content"])
        for scene in decoded.get("scenes", []):
            scene["narration"] = "The same incorrect repeated paragraph"
        response["message"]["content"] = json.dumps(decoded)
        return response

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=repeated_narration,
    ) as request:
        result = plan_video_storyboard(_source(), _settings())

    assert request.call_count == 3
    assert [scene["narration"] for scene in result["scenes"]] == [
        "The train crossed the valley.",
        "Mara looked through the window.",
    ]
    assert all(scene["alignment_confidence"] == 1.0 for scene in result["scenes"])


def test_planner_publishes_each_completed_ollama_block_incrementally() -> None:
    source = _source()
    source["duration_seconds"] = 240.0
    source["narration_cues"] = [
        {
            "start_seconds": index * 80.0,
            "end_seconds": (index + 1) * 80.0,
            "duration_seconds": 80.0,
            "text": f"Long timed narration block {index}",
        }
        for index in range(3)
    ]
    partials: list[dict] = []
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ):
        result = plan_video_storyboard(
            source,
            _settings(),
            partial=partials.append,
        )

    assert len(partials) == 6
    assert [value["completed_blocks"] for value in partials] == [4, 5, 6, 7, 8, 9]
    assert all(value["total_blocks"] == 9 for value in partials)
    scene_partials = [
        value for value in partials if value.get("analysis_phase") == "scenes"
    ]
    assert len(scene_partials[0]["scenes"]) < len(scene_partials[1]["scenes"])
    assert len(scene_partials[1]["scenes"]) < len(scene_partials[2]["scenes"])
    assert scene_partials[-1]["scenes"] == result["scenes"]


def test_planner_uses_shorter_blocks_to_give_ollama_more_attention() -> None:
    source = {
        "title": "Four periods",
        "text": " ".join(f"Detailed historical passage {index}." for index in range(4)),
        "duration_seconds": 120.0,
        "narration_cues": [
            {
                "start_seconds": index * 30.0,
                "end_seconds": (index + 1) * 30.0,
                "duration_seconds": 30.0,
                "text": f"Detailed historical passage {index}.",
            }
            for index in range(4)
        ],
    }
    partials: list[dict] = []
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ) as request:
        plan_video_storyboard(source, _settings(), partial=partials.append)

    assert request.call_count == 12
    assert [value["completed_blocks"] for value in partials] == list(range(5, 13))


def test_continuity_pass_carries_pronoun_identity_across_blocks() -> None:
    source = {
        "title": "Luis returns",
        "text": (
            "Luis entered the old workshop wearing his blue shirt and glasses. "
            "He examined the silent machinery and closed the heavy wooden door."
        ),
        "duration_seconds": 75.0,
        "narration_cues": [
            {
                "start_seconds": 0.0,
                "end_seconds": 35.0,
                "text": "Luis entered the old workshop wearing his blue shirt and glasses.",
            },
            {
                "start_seconds": 35.0,
                "end_seconds": 75.0,
                "text": "He examined the silent machinery and closed the heavy wooden door.",
            },
        ],
    }

    def response(url, payload, timeout, headers=None):
        schema = payload["format"]
        user = payload["messages"][1]["content"]
        if "character_events" in schema["properties"]:
            units = json.loads(
                user.split("Narration units:\n", 1)[1].split(
                    "\n\nNew IDs", 1
                )[0]
            )
            first = any("Luis" in unit["narration"] for unit in units)
            if not first:
                assert '"id": "char_luis"' in user
                assert "recent_focus_ids" in user
            content = {
                "character_events": (
                    [
                        {
                            "id": "luis",
                            "name": "Luis",
                            "aliases": [],
                            "effective_unit_id": units[0]["unit_id"],
                            "event_type": "first_appearance",
                            "identity_description": "dark complexion, square face and brown eyes",
                            "state_description": "adult man with glasses, blue shirt and jeans",
                            "evidence": units[0]["narration"],
                        }
                    ]
                    if first
                    else []
                ),
                "location_events": [],
                "era_events": [],
            }
        elif "unit_bindings" in schema["properties"]:
            units = json.loads(
                user.split("Consecutive narration units:\n", 1)[1].split(
                    "\n\nReturn exactly", 1
                )[0]
            )
            if not any("Luis" in unit["narration"] for unit in units):
                assert '"id":"char_luis"' in user
                assert "recent_focus_ids" in user
            content = {
                "unit_bindings": [
                    {
                        "unit_id": unit["unit_id"],
                        "character_ids": ["char_luis"],
                        "location_ids": [],
                        "era_id": "",
                    }
                    for unit in units
                ],
            }
        else:
            assignments = json.loads(
                user.split("Scenes:\n", 1)[1].split("\n\nReturn exactly", 1)[0]
            )
            content = {
                "scenes": [
                    {
                        "character_state_ids": [
                            value["state_id"]
                            for value in assignment["available_continuity"]["characters"]
                        ],
                        "location_state_ids": [],
                        "era_state_id": "",
                        "visual": (
                            "Luis stands inside the old workshop beside silent iron machinery, "
                            "one hand resting near the heavy wooden door while dusty benches, "
                            "aged tools, broad floorboards and muted daylight establish a concrete, "
                            "single narrative moment without introducing another person."
                        ),
                    }
                    for assignment in assignments
                ]
            }
        return {"message": {"content": json.dumps(content)}}

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=response,
    ):
        result = plan_video_storyboard(source, _settings())

    assert len(result["continuity"]["characters"]) == 1
    assert all(
        scene["characters"] == ["char_luis_state_1"]
        for scene in result["scenes"]
    )


def test_fixed_assignments_keep_police_prompt_with_police_narration() -> None:
    source = {
        "title": "Apartment",
        "text": (
            "Marcos called emergency services. The police arrived minutes later. "
            "They found a man hidden under the bed."
        ),
        "duration_seconds": 18.0,
        "narration_cues": [
            {
                "start_seconds": 0.0,
                "end_seconds": 6.0,
                "text": "Marcos called emergency services.",
            },
            {
                "start_seconds": 6.0,
                "end_seconds": 11.0,
                "text": "The police arrived minutes later.",
            },
            {
                "start_seconds": 11.0,
                "end_seconds": 18.0,
                "text": "They found a man hidden under the bed.",
            },
        ],
    }

    def semantic_fake(url, payload, timeout, headers=None):
        response = _fake_ollama(url, payload, timeout, headers)
        decoded = json.loads(response["message"]["content"])
        if "scenes" not in decoded:
            return response
        user = payload["messages"][1]["content"]
        assignments_text = user.split("Scenes:\n", 1)[1]
        assignments_text = assignments_text.split("\n\nChoose style_preset", 1)[0]
        assignments_text = assignments_text.split("\n\nReturn exactly", 1)[0]
        assignments = json.loads(assignments_text)
        for index, scene in enumerate(decoded["scenes"]):
            narration = assignments[index]["narration"]
            scene["visual"] = (
                f"A detailed cinematic depiction of {narration} Uniformed police officers and the exact visible action occupy the foreground, with a realistic modern apartment entrance, emergency lighting, material textures, controlled shadows, spatial depth, and environmental details that remain strictly tied to this assigned narration without borrowing any event from adjacent scenes."
                if "police" in narration.lower()
                else scene["visual"]
            )
        response["message"]["content"] = json.dumps(decoded)
        return response

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=semantic_fake,
    ):
        result = plan_video_storyboard(source, _settings())

    police_scene = next(
        scene for scene in result["scenes"] if "police arrived" in scene["narration"]
    )
    assert "police officers" in police_scene["prompt"]
    assert "hidden under the bed" not in police_scene["prompt"]


def test_ollama_stream_forwards_reasoning_and_structured_content() -> None:
    lines = [
        b'{"message":{"thinking":"Checking fixed scenes...","content":""},"done":false}\n',
        b'{"message":{"thinking":"","content":"{\\"scenes\\":"},"done":false}\n',
        b'{"message":{"thinking":"","content":"[]}"},"done":true}\n',
    ]

    class FakeResponse:
        def __enter__(self):
            return iter(lines)

        def __exit__(self, *_args):
            return False

    events: list[dict] = []
    with patch(
        "app.core.video_storyboard_planner.urllib.request.urlopen",
        return_value=FakeResponse(),
    ):
        result = _http_ollama_stream(
            "http://127.0.0.1:11434/api/chat",
            {"model": "qwen3:8b", "stream": True},
            30,
            events.append,
            lambda: False,
        )

    assert result["message"]["content"] == '{"scenes":[]}'
    assert result["done_reason"] == ""
    assert "Checking fixed scenes" in "".join(
        event.get("text", "") for event in events if event.get("kind") == "thinking"
    )
    assert '{"scenes":[]}' == "".join(
        event.get("text", "") for event in events if event.get("kind") == "content"
    )


def test_litellm_known_provider_uses_direct_sdk_when_url_is_empty() -> None:
    settings = _settings()
    settings["llm_provider"] = "litellm"
    settings["litellm"] = {
        "base_url": "",
        "model": "openai/gpt-5-mini",
        "api_key": "test-secret",
        "timeout_seconds": 90,
    }
    events: list[dict] = []

    def direct_response(payload, *, api_key, timeout, trace, cancelled):
        assert payload["model"] == "openai/gpt-5-mini"
        assert payload["max_output_tokens"] == 16000
        assert payload["stream"] is True
        assert payload["text"]["format"]["type"] == "json_schema"
        assert api_key == "test-secret"
        assert timeout == 90
        schema = payload["text"]["format"]["schema"]
        fake = _fake_ollama(
            "",
            {
                "format": schema,
                "messages": [{"content": ""}, {"content": payload["input"]}],
            },
            timeout,
        )
        content = fake["message"]["content"]
        trace({"kind": "content", "text": content})
        return {
            "choices": [
                {
                    "message": {"content": content}
                }
            ]
        }

    with patch(
        "app.core.video_storyboard_planner._litellm_direct_responses",
        side_effect=direct_response,
    ) as direct, patch(
        "app.core.video_storyboard_planner._http_json"
    ) as http:
        result = plan_video_storyboard(_source(), settings, trace=events.append)

    assert direct.called
    http.assert_not_called()
    request_event = next(event for event in events if event.get("kind") == "request")
    assert request_event["transport"] == "sdk"
    assert request_event["endpoint"] == "LiteLLM Python SDK (direct provider)"
    assert request_event["output_tokens"] == 16000
    raw_response_event = next(
        event for event in events if event.get("kind") == "raw_response"
    )
    assert raw_response_event["provider"] == "litellm"
    assert any(event.get("kind") == "content" for event in events)
    assert result["scenes"]


def test_litellm_direct_failure_emits_raw_provider_error_for_audit_log() -> None:
    settings = _settings()
    settings["llm_provider"] = "litellm"
    settings["litellm"] = {
        "base_url": "",
        "model": "openai/gpt-5-mini",
        "api_key": "test-secret",
        "timeout_seconds": 90,
    }
    events: list[dict] = []
    failure = VideoStoryboardPlanningError(
        "LiteLLM direct request failed: unsupported parameter",
        details={
            "type": "BadRequestError",
            "status_code": 400,
            "body": {"message": "Unsupported response_format"},
        },
    )

    with patch(
        "app.core.video_storyboard_planner._litellm_direct_responses",
        side_effect=failure,
    ), pytest.raises(VideoStoryboardPlanningError):
        plan_video_storyboard(_source(), settings, trace=events.append)

    error = next(event for event in events if event.get("kind") == "error")
    assert error["provider"] == "litellm"
    assert error["raw_error"]["status_code"] == 400
    assert error["raw_error"]["body"]["message"] == "Unsupported response_format"


def test_litellm_direct_sdk_forwards_safe_arguments_and_normalizes_response() -> None:
    class Response:
        def model_dump(self):
            return {"choices": [{"message": {"content": '{"scenes": []}'}}]}

    with patch("litellm.completion", return_value=Response()) as completion:
        result = _litellm_direct_completion(
            {
                "model": "openai/gpt-5-mini",
                "messages": [{"role": "user", "content": "Return JSON"}],
            },
            api_key="secret",
            timeout=45,
        )

    assert result["choices"][0]["message"]["content"] == '{"scenes": []}'
    assert completion.call_args.kwargs["api_key"] == "secret"
    assert completion.call_args.kwargs["timeout"] == 45
    assert completion.call_args.kwargs["drop_params"] is True


def test_litellm_responses_stream_forwards_live_structured_text() -> None:
    class Event:
        def __init__(self, value):
            self.value = value

        def model_dump(self):
            return self.value

    events = [
        Event({"type": "response.created", "response": {"id": "resp-1"}}),
        Event({"type": "response.output_text.delta", "delta": '{"scenes":'}),
        Event({"type": "response.output_text.delta", "delta": "[]}"}),
        Event(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp-1",
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [],
                },
            }
        ),
    ]
    traced: list[dict] = []

    with patch("litellm.responses", return_value=iter(events)) as responses:
        result = _litellm_direct_responses(
            {
                "model": "openai/gpt-test",
                "input": "Return JSON",
                "instructions": "JSON only",
                "max_output_tokens": 16000,
                "stream": True,
            },
            api_key="secret",
            timeout=45,
            trace=traced.append,
        )

    assert result["choices"][0]["message"]["content"] == '{"scenes":[]}'
    assert "".join(event.get("text", "") for event in traced) == '{"scenes":[]}'
    assert responses.call_args.kwargs["stream"] is True
    assert responses.call_args.kwargs["max_output_tokens"] == 16000


def test_litellm_responses_stream_shows_terminal_text_when_no_deltas_arrive() -> None:
    final_text = '{"scenes":[]}'
    terminal = {
        "type": "response.completed",
        "response": {
            "id": "resp-final-only",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": final_text}],
                }
            ],
        },
    }
    traced: list[dict] = []

    with patch("litellm.responses", return_value=iter([terminal])):
        result = _litellm_direct_responses(
            {"model": "openai/gpt-test", "input": "JSON", "stream": True},
            api_key="secret",
            timeout=45,
            trace=traced.append,
        )

    assert result["choices"][0]["message"]["content"] == final_text
    assert [event["text"] for event in traced if event.get("kind") == "content"] == [final_text]


def test_litellm_explicit_url_keeps_openai_compatible_proxy_transport() -> None:
    settings = _settings()
    settings["llm_provider"] = "litellm"
    settings["litellm"] = {
        "base_url": "https://llm.example.test/v1/",
        "model": "storyboard-model",
        "api_key": "proxy-secret",
        "timeout_seconds": 90,
        "max_output_tokens": 32000,
    }

    def proxy_response(url, payload, timeout, headers=None):
        assert url == "https://llm.example.test/v1/responses"
        assert headers == {"Authorization": "Bearer proxy-secret"}
        assert payload["max_output_tokens"] == 32000
        schema = payload["text"]["format"]["schema"]
        fake = _fake_ollama(
            "",
            {
                "format": schema,
                "messages": [{"content": ""}, {"content": payload["input"]}],
            },
            timeout,
        )
        return {
            "id": "resp-test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                            {"type": "output_text", "text": fake["message"]["content"]}
                    ],
                }
            ]
        }

    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=proxy_response,
    ) as http, patch(
        "app.core.video_storyboard_planner._litellm_direct_responses"
    ) as direct:
        result = plan_video_storyboard(_source(), settings)

    assert http.called
    direct.assert_not_called()
    assert result["scenes"]


def test_long_semantic_scene_is_split_into_distinct_cinematic_coverage() -> None:
    source = {
        "title": "One continuous action",
        "text": "Mara silently watches the distant storm from the station platform.",
        "duration_seconds": 20.0,
        "narration_cues": [
            {
                "start_seconds": 0.0,
                "end_seconds": 20.0,
                "duration_seconds": 20.0,
                "text": "Mara silently watches the distant storm from the station platform.",
            }
        ],
    }
    with patch(
        "app.core.video_storyboard_planner._http_json",
        side_effect=_fake_ollama,
    ):
        result = plan_video_storyboard(source, _settings())

    scenes = result["scenes"]
    assert len(scenes) == 3
    assert sum(scene["duration"] for scene in scenes) == 20.0
    assert all(scene["duration"] <= 8.0 for scene in scenes)
    assert [scene["start_seconds"] for scene in scenes] == [0.0, 6.667, 13.334]
    assert scenes[0]["shot_strategy"] == "semantic_shot"
    assert len({scene["shot_strategy"] for scene in scenes[1:]}) == 2
    assert all(scene["semantic_scene_id"] == "001" for scene in scenes)
    assert [scene["coverage_index"] for scene in scenes] == [1, 2, 3]
    assert all(scene["coverage_count"] == 3 for scene in scenes)
    assert scenes[0]["prompt"].startswith("English image prompt 1:")
    assert len({scene["prompt"] for scene in scenes}) == 1
    assert len({scene["shot"] for scene in scenes}) == 3
    assert all(
        "Cinematic coverage frame" not in scene["prompt"]
        and "Preserve exactly" not in scene["prompt"]
        for scene in scenes
    )
    assert all(scene["coverage_prompt_version"] == 2 for scene in scenes)
    assert result["alignment_debug"]["maximum_coverage_frame_seconds"] == 8.0
    assert result["alignment_debug"]["semantic_scene_count"] == 1

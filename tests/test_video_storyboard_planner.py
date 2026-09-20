from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from app.core.video_storyboard_planner import VideoStoryboardPlanningError, _empty_continuity, _http_ollama_stream, _litellm_direct_completion, _litellm_direct_responses, _planning_batches


def test_analysis_block_size_can_send_more_timed_text_per_request() -> None:
    pieces = [f"Segment {index} " + ("detail " * 70) for index in range(8)]
    text = " ".join(pieces)
    cues = [
        {
            "text": piece,
            "start_seconds": index * 10.0,
            "end_seconds": (index + 1) * 10.0,
        }
        for index, piece in enumerate(pieces)
    ]
    normal = _planning_batches(text, cues, 80.0)
    large = _planning_batches(
        text,
        cues,
        80.0,
        {
            "analysis": {
                "max_block_characters": 12000,
                "max_block_seconds": 120,
            }
        },
    )

    assert len(normal) > 1
    assert len(large) == 1


def _assert_closed_openai_schema_objects(value: object) -> None:
    """OpenAI strict JSON schemas require every object to be closed."""
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for child in value.values():
            _assert_closed_openai_schema_objects(child)
    elif isinstance(value, list):
        for child in value:
            _assert_closed_openai_schema_objects(child)


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
    if "format" not in payload:
        return {
            "message": {
                "content": (
                    "CHARACTERS\nMara is a recurring woman seen at 6.00s; "
                    "her other visual traits are not stated.\n\n"
                    "PLACES\nA train and its valley route are visually important."
                )
            }
        }
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

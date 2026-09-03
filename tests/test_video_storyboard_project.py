from __future__ import annotations

import json
from pathlib import Path

from app.core.video_storyboard_project import (
    append_storyboard_analysis_request,
    append_storyboard_analysis_result,
    create_storyboard_analysis_request_log,
    finish_storyboard_analysis_request_log,
    load_storyboard_state,
    save_storyboard_state,
    storyboard_matches_source,
)


def test_analysis_request_log_preserves_complete_bodies_without_secrets(
    tmp_path: Path,
) -> None:
    target = create_storyboard_analysis_request_log(
        tmp_path,
        title="Police story",
        provider="ollama",
        model="qwen3:8b",
    )
    append_storyboard_analysis_request(
        target,
        {
            "kind": "request",
            "provider": "ollama",
            "endpoint": "http://127.0.0.1:11434/api/chat",
            "label": "block 1/2 compact retry",
            "attempt": 2,
            "raw_request": {
                "model": "qwen3:8b",
                "messages": [
                    {"role": "system", "content": "Return strict JSON"},
                    {"role": "user", "content": "The police arrived."},
                ],
                "format": {"type": "object"},
                "options": {"num_predict": 4096},
            },
        },
        1,
    )
    append_storyboard_analysis_result(
        target,
        {
            "kind": "raw_response",
            "provider": "litellm",
            "label": "block 1/2 compact retry",
            "raw_response": {
                "choices": [{"message": {"content": '{"scenes": []}'}}],
                "api_key": "must-not-be-written",
            },
        },
        1,
    )
    append_storyboard_analysis_result(
        target,
        {
            "kind": "error",
            "provider": "litellm",
            "label": "block 1/2 compact retry",
            "endpoint": "LiteLLM Python SDK (direct provider)",
            "raw_error": {
                "type": "BadRequestError",
                "status_code": 400,
                "body": {"message": "Unsupported response format"},
                "authorization": "Bearer secret",
            },
        },
        1,
    )
    finish_storyboard_analysis_request_log(
        target,
        status="completed",
        detail="2 frames planned and saved.",
    )

    content = target.read_text(encoding="utf-8")
    assert target.parent == tmp_path / "storyboard" / "debug"
    assert "REQUEST 1: block 1/2 compact retry" in content
    assert "POST http://127.0.0.1:11434/api/chat" in content
    assert '"system"' in content
    assert '"num_predict": 4096' in content
    assert "RAW RESPONSE FOR REQUEST 1" in content
    assert '"content": "{\\"scenes\\": []}"' in content
    assert "ERROR FOR REQUEST 1" in content
    assert '"status_code": 400' in content
    assert "Unsupported response format" in content
    assert "must-not-be-written" not in content
    assert "Bearer secret" not in content
    assert '"authorization": "[OMITTED]"' in content
    assert "ANALYSIS COMPLETED" in content
    assert "Bearer" not in content


def test_storyboard_state_is_atomic_portable_and_restores_project_paths(
    tmp_path: Path,
) -> None:
    frame = tmp_path / "storyboard" / "frames" / "scene-001.png"
    audio = tmp_path / "audio" / "narration.wav"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    state = {
        "source": {
            "project_id": 4,
            "title": "Journey",
            "text": "Saved audiobook text",
            "duration_seconds": 8.0,
            "audio_path": str(audio),
            "narration_cues": [
                {
                    "start_seconds": 0.0,
                    "duration_seconds": 8.0,
                    "text": "Saved audiobook text",
                }
            ],
        },
        "plan": {"base_seed": 72, "style": {"palette": "blue"}},
        "scenes": [
            {
                "scene_id": "001",
                "start_seconds": 0.0,
                "duration_seconds": 8.0,
                "prompt": "A blue landscape",
                "image_path": str(frame),
            }
        ],
    }

    target = save_storyboard_state(
        tmp_path,
        state,
        analysis_status="analyzing",
    )
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["plan"]["continuity"]["version"] == 1
    assert raw["source"]["narration_cues"][0]["duration_seconds"] == 8.0
    assert raw["source"]["audio_path"] == "audio/narration.wav"
    assert raw["scenes"][0]["image_path"] == "storyboard/frames/scene-001.png"
    assert raw["analysis_status"] == "analyzing"
    assert not list(target.parent.glob(".*.tmp"))

    restored = load_storyboard_state(tmp_path)
    assert restored is not None
    assert restored["scenes"][0]["image_path"] == str(frame)
    assert restored["source"]["audio_path"] == str(audio)
    assert storyboard_matches_source(restored, "Saved audiobook text")
    assert not storyboard_matches_source(restored, "Changed text")


def test_legacy_flat_character_and_era_metadata_is_migrated(tmp_path: Path) -> None:
    target = tmp_path / "storyboard" / "storyboard.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schema": "localtext2voice.video-storyboard",
                "version": 1,
                "source_hash": "",
                "source": {"text": "Luis entered the house."},
                "plan": {
                    "source_duration_seconds": 10.0,
                    "style": {
                        "characters": [
                            "luis_young: Luis, young man with glasses and a blue shirt"
                        ]
                    },
                },
                "scenes": [
                    {
                        "scene_id": "001",
                        "start_seconds": 0.0,
                        "duration_seconds": 10.0,
                        "characters": ["luis_young"],
                        "era": "1950s Spain",
                        "image_path": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    restored = load_storyboard_state(tmp_path)

    assert restored is not None
    assert restored["version"] == 2
    continuity = restored["plan"]["continuity"]
    assert continuity["characters"][0]["states"][0]["id"] == "luis_young"
    assert continuity["characters"][0]["states"][0]["to_seconds"] == 10.0
    assert continuity["eras"][0]["description"] == "1950s Spain"
    assert restored["scenes"][0]["era_state_id"] == "era_legacy_1"

"""Keep release-facing strings and interpolation fields consistent."""

import json
from pathlib import Path
from string import Formatter

import pytest


LOCALES = Path(__file__).resolve().parents[1] / "locales"
ENGLISH = json.loads((LOCALES / "en.json").read_text(encoding="utf-8-sig"))
NEW_KEYS = {
    key for key in ENGLISH
    if key.startswith(("indextts_", "h3_", "markup_emotion_", "runpod_metrics_"))
} | {"runpod_export_metrics", "runpod_job_timing", "generation_stage_time_status"}


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_release_strings_present_and_placeholders_match(path):
    messages = json.loads(path.read_text(encoding="utf-8-sig"))
    assert not NEW_KEYS - messages.keys()
    for key in NEW_KEYS:
        text = messages[key]
        assert text.strip() and "\ufffd" not in text, (path.name, key)
        fields = lambda value: {
            field for _, field, _, _ in Formatter().parse(value) if field is not None
        }
        assert fields(text) == fields(ENGLISH[key]), (path.name, key)


def test_example_chunk_limits_match_application_defaults():
    from app.core.settings_manager import DEFAULT_SETTINGS

    example = json.loads((LOCALES.parent / "config.example.json").read_text(encoding="utf-8"))
    assert example["engine_chunk_sizes"] == DEFAULT_SETTINGS["engine_chunk_sizes"]

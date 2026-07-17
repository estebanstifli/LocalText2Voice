from __future__ import annotations

import json

from app.core.settings_manager import DEFAULT_SETTINGS, SettingsManager
from app.core.text_processor import TextProcessor


RUSSIAN_SAMPLE = (
    "Тихий ветер гуляет по улицам старого города. "
    "Скоро наступит вечер, и в окнах зажгутся тёплые огни."
)


def test_invalid_partial_settings_fall_back_to_safe_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 11,
                "ui_language": "ar",
                "split_mode": "broken",
                "export_mode": None,
                "speed": "fast",
                "chunk_size": 1,
                "engine_chunk_sizes": {"omnivoice": 1, "qwen": "broken"},
                "review": [],
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsManager(path).settings

    assert settings["ui_language"] == DEFAULT_SETTINGS["ui_language"]
    assert settings["split_mode"] == DEFAULT_SETTINGS["split_mode"]
    assert settings["export_mode"] == DEFAULT_SETTINGS["export_mode"]
    assert settings["speed"] == DEFAULT_SETTINGS["speed"]
    assert settings["chunk_size"] == DEFAULT_SETTINGS["chunk_size"]
    assert settings["engine_chunk_sizes"]["omnivoice"] == 0
    assert settings["engine_chunk_sizes"]["qwen"] == 0
    assert settings["review"] == DEFAULT_SETTINGS["review"]
    assert len(TextProcessor.split_paragraph_chunks(RUSSIAN_SAMPLE, settings["chunk_size"])) == 1


def test_malformed_json_uses_complete_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"chunk_size":', encoding="utf-8")

    settings = SettingsManager(path).settings

    assert settings == DEFAULT_SETTINGS


def test_save_preserves_existing_settings_reference(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    held_reference = manager.settings
    held_reference["chunk_size"] = 1

    manager.save(held_reference)

    assert manager.settings is held_reference
    assert held_reference["chunk_size"] == DEFAULT_SETTINGS["chunk_size"]

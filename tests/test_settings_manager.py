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


def test_russian_ui_language_is_preserved(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    manager.settings["ui_language"] = "ru"

    manager.save()

    assert manager.settings["ui_language"] == "ru"
    assert SettingsManager(manager.path).settings["ui_language"] == "ru"


def test_audio_tail_review_defaults_off_and_sanitizes_threshold_order(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    assert manager.settings["review"]["tail_analysis_enabled"] is False
    assert manager.settings["review"]["tail_autocut_enabled"] is False

    manager.settings["review"].update(
        {
            "tail_analysis_enabled": True,
            "tail_autocut_enabled": True,
            "tail_safety_margin_seconds": -1,
            "tail_warning_threshold_seconds": 2.0,
            "tail_failure_threshold_seconds": 1.0,
        }
    )
    manager.save()

    review = manager.settings["review"]
    assert review["tail_analysis_enabled"] is True
    assert review["tail_autocut_enabled"] is True
    assert review["tail_safety_margin_seconds"] == 0.4
    assert review["tail_failure_threshold_seconds"] > review[
        "tail_warning_threshold_seconds"
    ]

    review["tail_analysis_enabled"] = False
    manager.save()
    assert manager.settings["review"]["tail_autocut_enabled"] is False


def test_text_normalization_accepts_builtin_and_custom_language_codes(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    manager.settings["text_normalization"] = {
        "enabled": True,
        "language": "pt_BR",
    }

    manager.save()

    normalization = manager.settings["text_normalization"]
    assert normalization["enabled"] is True
    assert normalization["language"] == "pt-br"
    assert normalization["rules"] == {
        "enabled": True,
        "numbers": True,
        "ordinals": True,
        "dates": True,
        "currencies": True,
        "percentages": True,
        "measurements": True,
        "roman_numerals": True,
    }

    normalization["rules"] = {"enabled": False, "dates": False}
    manager.save()
    assert manager.settings["text_normalization"]["rules"]["enabled"] is False
    assert manager.settings["text_normalization"]["rules"]["dates"] is False
    assert manager.settings["text_normalization"]["rules"]["numbers"] is True

    manager.settings["text_normalization"]["language"] = "not a valid code!"
    manager.save()
    assert manager.settings["text_normalization"]["language"] == "auto"

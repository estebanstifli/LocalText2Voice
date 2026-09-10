from __future__ import annotations

import json
from pathlib import Path

from app import __version__
from app.core.settings_manager import (
    CURRENT_SETTINGS_SCHEMA_VERSION,
    DEFAULT_SETTINGS,
    SettingsManager,
)
from app.core.text_processor import TextProcessor


RUSSIAN_SAMPLE = (
    "Тихий ветер гуляет по улицам старого города. "
    "Скоро наступит вечер, и в окнах зажгутся тёплые огни."
)


def test_distribution_metadata_matches_runtime_defaults() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    example = json.loads(
        (repository_root / "config.example.json").read_text(encoding="utf-8")
    )
    installer = (repository_root / "installer" / "LocalText2Voice.iss").read_text(
        encoding="utf-8"
    )

    assert example["settings_schema_version"] == CURRENT_SETTINGS_SCHEMA_VERSION
    assert (
        f'"settings_schema_version": {CURRENT_SETTINGS_SCHEMA_VERSION},'
        in installer
    )
    assert f'#define MyAppVersion "{__version__}"' in installer


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
    assert (
        settings["engine_chunk_sizes"]["omnivoice"]
        == DEFAULT_SETTINGS["engine_chunk_sizes"]["omnivoice"]
    )
    assert (
        settings["engine_chunk_sizes"]["qwen"]
        == DEFAULT_SETTINGS["engine_chunk_sizes"]["qwen"]
    )
    assert settings["review"] == DEFAULT_SETTINGS["review"]
    assert len(TextProcessor.split_paragraph_chunks(RUSSIAN_SAMPLE, settings["chunk_size"])) == 1


def test_malformed_json_uses_complete_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"chunk_size":', encoding="utf-8")

    settings = SettingsManager(path).settings

    assert settings == DEFAULT_SETTINGS


def test_chunk_defaults_migrate_to_explicit_safe_engine_limits(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 19,
                "split_mode": "chapters",
                "chunk_size": 2500,
                "engine_chunk_sizes": {
                    "piper": 0,
                    "kokoro": 0,
                    "chatterbox": 0,
                    "qwen": 0,
                    "omnivoice": 0,
                    "f5_russian": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsManager(path).settings

    assert settings["split_mode"] == "safe_chunks"
    assert settings["chunk_size"] == 300
    assert settings["engine_chunk_sizes"] == {
        "piper": 300,
        "kokoro": 300,
        "chatterbox": 300,
        "qwen": 520,
        "omnivoice": 300,
        "f5_russian": 300,
    }


def test_large_asset_storage_defaults_to_application_folder(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")

    assert manager.settings["storage"] == {"base_dir": ".", "previous_roots": []}

    manager.settings["storage"]["base_dir"] = "D:/LocalText2Voice"
    manager.save()

    assert SettingsManager(manager.path).settings["storage"]["base_dir"] == (
        "D:/LocalText2Voice"
    )


def test_schema_16_config_keeps_legacy_asset_location(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"settings_schema_version": 16, "ui_language": "en"}),
        encoding="utf-8",
    )

    storage = SettingsManager(path).settings["storage"]

    assert storage == {"base_dir": "", "previous_roots": []}


def test_schema_18_server_port_migrates_to_internal_engine_host_port(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 18,
                "local_server": {"port": 9123},
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsManager(path).settings

    assert settings["internal_engine_host_port"] == 9123
    assert settings["remote_mcp_port"] == 8766
    assert "port" not in settings["local_server"]


def test_internal_and_remote_ports_are_sanitized_and_kept_distinct(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    manager.settings["internal_engine_host_port"] = 9000
    manager.settings["remote_mcp_port"] = 9000

    manager.save()

    assert manager.settings["internal_engine_host_port"] == 9000
    assert manager.settings["remote_mcp_port"] == 9001


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


def test_gpu_device_index_is_persistent_and_sanitized(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    assert manager.settings["gpu_device_index"] == "auto"

    manager.settings["gpu_device_index"] = 1
    manager.save()
    assert SettingsManager(manager.path).settings["gpu_device_index"] == "1"

    manager.settings["gpu_device_index"] = "not-a-gpu"
    manager.save()
    assert manager.settings["gpu_device_index"] == "auto"


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


def test_video_storyboard_defaults_are_optional_and_sanitized(tmp_path):
    manager = SettingsManager(tmp_path / "config.json")
    defaults = manager.settings["video_storyboard"]

    assert defaults["enabled"] is False
    assert defaults["image_provider"] == "comfyui"
    assert defaults["llm_provider"] == "ollama"
    assert defaults["image"]["width"] == 1280
    assert defaults["image"]["height"] == 720
    assert defaults["image"]["batch_size"] == 1
    assert defaults["image"]["style_mode"] == "comic_book"
    assert defaults["litellm"]["base_url"] == ""
    assert defaults["litellm"]["max_output_tokens"] == 16000
    assert defaults["video"]["zoom_percent"] == 30.0
    assert defaults["comfyui_video"]["workflow_profile"] == "wan22_rapid"
    assert defaults["comfyui_video"]["auth_token"] == ""

    manager.settings["video_storyboard"] = {
        "enabled": True,
        "image_provider": "local_z_image",
        "llm_provider": "local_qwen3",
        "local_z_image": {"model_id": "obsolete"},
        "local_qwen": {"model_id": "obsolete"},
        "scene": {
            "minimum_seconds": 50,
            "target_seconds": 4,
            "maximum_seconds": 2,
        },
        "image": {
            "width": 1,
            "height": 9000,
            "batch_size": 99,
            "steps": 0,
        },
        "litellm_image": {"model": "openai/gpt-image-1", "timeout_seconds": 1},
        "litellm": {"max_output_tokens": 999999},
        "video": {"fps": 1000, "codec": "unsafe", "crf": 99},
    }
    manager.save()

    storyboard = manager.settings["video_storyboard"]
    assert storyboard["enabled"] is True
    assert storyboard["image_provider"] == "comfyui"
    assert storyboard["llm_provider"] == "ollama"
    assert "local_z_image" not in storyboard
    assert "local_qwen" not in storyboard
    assert storyboard["comfyui"]["diffusion_model"] == "z_image_turbo_bf16.safetensors"
    assert storyboard["comfyui_video"]["width"] == 640
    assert storyboard["comfyui_video"]["height"] == 360
    assert storyboard["comfyui_video"]["frames"] == 49
    assert storyboard["comfyui_video"]["steps"] == 4
    assert storyboard["comfyui_video"]["workflow_profile"] == "wan22_rapid"
    assert storyboard["comfyui_video"]["bindings"]["prompt"] == ""
    assert storyboard["ollama"]["context_length"] == 8192
    assert storyboard["scene"] == {
        "mode": "semantic_bounded",
        "minimum_seconds": 4,
        "target_seconds": 4,
        "maximum_seconds": 20,
    }
    assert storyboard["image"]["width"] == 1280
    assert storyboard["image"]["height"] == 720
    assert storyboard["image"]["batch_size"] == 1
    assert storyboard["image"]["steps"] == 8
    assert storyboard["litellm_image"]["model"] == "openai/gpt-image-1"
    assert storyboard["litellm_image"]["timeout_seconds"] == 300
    assert storyboard["litellm"]["max_output_tokens"] == 16000
    assert storyboard["video"]["fps"] == 30
    assert storyboard["video"]["codec"] == "libx264"
    assert storyboard["video"]["crf"] == 18

    manager.settings["video_storyboard"]["video"]["zoom_percent"] = 250.0
    manager.save()
    assert manager.settings["video_storyboard"]["video"]["zoom_percent"] == 250.0


def test_video_storyboard_legacy_zoom_is_migrated_to_stronger_motion(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 23,
                "video_storyboard": {"video": {"zoom_percent": 8.0}},
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsManager(path).settings

    assert settings["settings_schema_version"] == CURRENT_SETTINGS_SCHEMA_VERSION
    assert settings["video_storyboard"]["video"]["zoom_percent"] == 30.0


def test_litellm_provider_model_migrates_legacy_default_proxy_to_direct_sdk(
    tmp_path,
):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 25,
                "video_storyboard": {
                    "llm_provider": "litellm",
                    "litellm": {
                        "base_url": "http://127.0.0.1:4000/v1",
                        "model": "openai/gpt-5-mini",
                        "api_key": "secret",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsManager(path).settings

    assert settings["video_storyboard"]["litellm"]["base_url"] == ""


def test_custom_storyboard_image_provider_migrates_to_litellm_image(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": 26,
                "video_storyboard": {
                    "image_provider": "custom_http",
                    "custom_image": {
                        "url": "https://images.example.test/v1",
                        "api_key": "secret",
                        "timeout_seconds": 600,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    storyboard = SettingsManager(path).settings["video_storyboard"]

    assert storyboard["image_provider"] == "litellm_image"
    assert storyboard["litellm_image"]["base_url"] == "https://images.example.test/v1"
    assert storyboard["litellm_image"]["api_key"] == "secret"
    assert "custom_image" not in storyboard


def test_custom_comfyui_image_settings_persist(tmp_path):
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps({"settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION, "video_storyboard": {"image_provider": "custom_comfyui", "comfyui": {"auth_token": " remote-token ", "workflow_path": "custom.json", "bindings": {"prompt": " 7.text ", "seed": "8.seed"}}}}))
    settings = SettingsManager(config_path)
    storyboard = settings.get("video_storyboard")
    assert storyboard["image_provider"] == "custom_comfyui"
    assert storyboard["comfyui"]["auth_token"] == "remote-token"
    assert storyboard["comfyui"]["bindings"]["prompt"] == "7.text"

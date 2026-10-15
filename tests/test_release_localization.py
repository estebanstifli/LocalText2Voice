"""Keep release-facing strings and interpolation fields consistent."""

import json
import ast
from pathlib import Path
from string import Formatter

import pytest


LOCALES = Path(__file__).resolve().parents[1] / "locales"
ENGLISH = json.loads((LOCALES / "en.json").read_text(encoding="utf-8-sig"))
NEW_KEYS = {
    key for key in ENGLISH
    if key.startswith(("indextts_", "h3_", "markup_emotion_", "runpod_metrics_",
                       "audiocpp_", "higgs_", "fish_", "breeze_", "voxcpm_", "segment_", "voice_conflict_"))
} | {"runpod_export_metrics", "runpod_job_timing", "generation_stage_time_status",
     "project_rename_busy", "project_folder_renamed", "output_relative",
     "output_absolute", "output_folder_mode", "output_folder_help", "markup_vocal_help"}


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
    assert example["output_dir"] == DEFAULT_SETTINGS["output_dir"] == "exports"
    assert example["output_dir_mode"] == DEFAULT_SETTINGS["output_dir_mode"] == "relative"


@pytest.mark.parametrize("relative", ["app/ui/audiocpp_settings.py", "app/ui/segment_detail_dialog.py", "app/ui/breeze_voice_design_dialog.py", "app/ui/voxcpm_voice_design_dialog.py"])
def test_new_panels_do_not_rely_on_missing_literal_translation_keys(relative):
    tree = ast.parse((LOCALES.parent / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
        key = node.args[0]
        if name in {"tr", "tr_text", "translate"} and isinstance(key, ast.Constant):
            assert key.value in ENGLISH, (relative, key.value)


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_locale_keys_are_unique(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, (path.name, key)
            result[key] = value
        return result
    json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_voxcpm_language_names_and_breeze_styles_are_localized(path):
    from app.ui.voxcpm_voice_design_dialog import VoxCPMVoiceDesignDialog
    from app.tts.breeze_tags import STYLE_INSTRUCTIONS

    messages = json.loads(path.read_text(encoding="utf-8"))
    keys = [key for _, key, _ in VoxCPMVoiceDesignDialog.LANGUAGES]
    keys += ["breeze_style_" + name.lower() for name in STYLE_INSTRUCTIONS]
    assert all(messages.get(key, "").strip() for key in keys)

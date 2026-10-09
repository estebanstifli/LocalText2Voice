import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.audio_pipeline import AudioPipeline
from app.tts.voice_gallery_manager import VoiceGalleryManager


def manager_at(tmp_path):
    return VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "files")


def source(**extra):
    return {"id": "omnivoice_asun", "engine": "omnivoice", "name": "Asun",
            "language": "es", "language_name": "Spanish", "install_type": "reference_audio",
            "ref_audio": "https://example.test/asun.wav", "ref_text": "Referencia exacta.", **extra}


def test_cached_catalog_migrates_only_audio_with_transcript_and_keeps_presets(tmp_path):
    manager = manager_at(tmp_path)
    original = source()
    manager._replace_catalog([
        original, source(id="missing_transcript", ref_text=""),
        source(id="no_audio", ref_audio=""),
        source(id="qwen_serena", engine="qwen", name="Serena", install_type="engine_builtin", speaker_id="Serena"),
        source(id="indextts_asun", engine="indextts", compatible_source_id=original["id"]),
    ])
    manager = manager_at(tmp_path)
    clones = [v for v in manager.list_voices("qwen") if v.is_reference_audio]
    assert {v.voice_id for v in clones} == {"qwen_clone_asun", "qwen_clone_serena"}
    assert manager.get_voice("qwen_serena").speaker_id == "Serena"
    assert all(v.model_id == "base_1_7b" and not v.speaker_id for v in clones)
    manager = manager_at(tmp_path)
    assert len(manager.list_voices("qwen")) == 3


def test_synced_reference_survives_refresh_and_owns_its_audio(tmp_path):
    audio = tmp_path / "original.wav"
    audio.write_bytes(b"reference")
    doc = source(ref_audio=str(audio))
    (tmp_path / "voice.json").write_text(json.dumps(doc))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"voices": ["voice.json"]}))
    manager = manager_at(tmp_path)
    manager.sync(catalog)
    manager._mark_installed(doc["id"], str(audio))
    clone = manager.get_voice("qwen_clone_asun")
    installed = manager.ensure_voice_audio(clone)
    manager.sync(catalog)
    clone = manager.get_voice(clone.voice_id)
    assert clone.ref_text == doc["ref_text"]
    assert clone.installed_path == str(installed)
    assert installed != audio and installed.read_bytes() == audio.read_bytes()
    manager.uninstall(clone)
    assert audio.is_file() and not installed.exists()


def test_qwen_base_markup_selects_reference_instead_of_builtin_speaker(tmp_path):
    manager = manager_at(tmp_path)
    audio = tmp_path / "original.wav"
    audio.write_bytes(b"reference")
    manager._replace_catalog(manager._expand_compatible_documents([source(ref_audio=str(audio))]))
    pipeline = AudioPipeline(SimpleNamespace())
    pipeline._markup_voice_cache["qwen_gallery_manager"] = manager
    config = {"model": "base_1_7b", "speaker": "Serena"}
    pipeline._apply_qwen_voice(config, "Asun", "es")
    assert Path(config["reference_audio_path"]).read_bytes() == b"reference"
    assert config["reference_text"] == "Referencia exacta."
    assert config["language"] == "Spanish"
    assert config["speaker"] == "Serena"


@pytest.mark.parametrize("name,language", [("Mini", "es"), ("Mini - Spanish", "")])
def test_qwen_base_reference_respects_requested_language(tmp_path, name, language):
    manager = manager_at(tmp_path)
    english = tmp_path / "english.wav"
    spanish = tmp_path / "spanish.wav"
    english.write_bytes(b"english reference")
    spanish.write_bytes(b"spanish reference")
    manager._replace_catalog(manager._expand_compatible_documents([
        source(id="omnivoice_mini_en", name="Mini", language="en",
               language_name="English", ref_audio=str(english), ref_text="English reference."),
        source(id="omnivoice_mini_es", name="Mini", ref_audio=str(spanish)),
    ]))
    pipeline = AudioPipeline(SimpleNamespace())
    pipeline._markup_voice_cache["qwen_gallery_manager"] = manager
    config = {"model": "base_1_7b"}
    pipeline._apply_qwen_voice(config, name, language)
    assert Path(config["reference_audio_path"]).read_bytes() == b"spanish reference"
    assert config["reference_text"] == "Referencia exacta."
    assert config["language"] == "Spanish"

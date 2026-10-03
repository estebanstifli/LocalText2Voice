import json

from app.tts.voice_gallery_manager import VoiceGalleryManager


def test_cached_catalog_reuses_audio_without_sharing_uninstall_ownership(tmp_path):
    manager = VoiceGalleryManager(
        db_path=tmp_path / "voices.db", files_root=tmp_path / "files"
    )
    source = {
        "id": "omnivoice_es_asun",
        "engine": "omnivoice",
        "name": "Asun",
        "language": "es",
        "install_type": "engine_builtin",
        "preview_audio": "https://example.test/asun.wav",
        "engine_voice_id": "asun",
        "compatible_engines": ["chatterbox"],
    }
    # Simulate a catalog saved by an older release, including a compatibility alias.
    manager._replace_catalog(
        [
            source,
            {
                **source,
                "id": "chatterbox_es_asun",
                "engine": "chatterbox",
                "compatible_source_id": source["id"],
            },
            {"id": "qwen_builtin", "engine": "qwen", "name": "No reference"},
        ]
    )
    original = tmp_path / "cached.wav"
    original.write_bytes(b"cached reference")
    manager._mark_installed(source["id"], str(original))
    manager = VoiceGalleryManager(
        db_path=manager.db_path, files_root=manager.files_root
    )
    voices = manager.list_voices("indextts")
    assert len(voices) == 1
    voice = voices[0]
    assert voice.voice_id == "indextts_es_asun"
    assert voice.is_reference_audio and not voice.engine_voice_id
    audio = manager.ensure_voice_audio(voice)
    assert audio != original and audio.read_bytes() == original.read_bytes()
    manager.uninstall(manager.get_voice(voice.voice_id))
    assert original.is_file() and not audio.exists()


def test_sync_exposes_remote_references_and_preserves_installed_alias(tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps({"voices": ["voice.json"]}))
    (tmp_path / "reference.wav").write_bytes(b"audio")
    (tmp_path / "voice.json").write_text(
        json.dumps(
            {
                "id": "omnivoice_es_asun",
                "engine": "omnivoice",
                "name": "Asun",
                "preview_audio": "reference.wav",
                "install_type": "engine_builtin",
            }
        )
    )
    manager = VoiceGalleryManager(
        db_path=tmp_path / "voices.db", files_root=tmp_path / "files"
    )
    manager.sync(tmp_path / "catalog.json")
    voice = manager.list_voices("indextts")[0]
    path = manager.install(voice)
    manager.sync(tmp_path / "catalog.json")
    restored = manager.get_voice(voice.voice_id)
    assert restored.installed_path == str(path)
    assert manager.is_installed(restored)


def test_existing_indextts_import_is_not_overwritten(tmp_path):
    manager = VoiceGalleryManager(
        db_path=tmp_path / "voices.db", files_root=tmp_path / "files"
    )
    manager._replace_catalog(
        [
            {
                "id": "omnivoice_es_asun",
                "engine": "omnivoice",
                "name": "Asun",
                "preview_audio": "a.wav",
            },
            {
                "id": "indextts_es_asun",
                "engine": "indextts",
                "name": "Custom",
                "source": "user_import",
            },
        ]
    )
    manager = VoiceGalleryManager(
        db_path=manager.db_path, files_root=manager.files_root
    )
    assert manager.get_voice("indextts_es_asun").name == "Custom"

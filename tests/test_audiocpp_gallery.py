import json

from app.tts.voice_gallery_manager import VoiceGalleryManager


def test_cached_catalog_migrates_transcribed_audio_without_alias_duplicates(tmp_path):
    manager = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "files")
    source = {"id": "omnivoice_es_asun", "engine": "omnivoice", "name": "Asun",
              "preview_audio": "https://example.test/asun.wav", "ref_text": "Hola, soy Asun."}
    manager._replace_catalog([
        source,
        {**source, "id": "chatterbox_es_asun", "engine": "chatterbox",
         "compatible_source_id": source["id"]},
        {**source, "id": "no_transcript", "ref_text": ""},
        {"id": "no_audio", "engine": "qwen", "name": "No audio", "ref_text": "Hola."},
    ])
    original = tmp_path / "cached.wav"
    original.write_bytes(b"reference bytes")
    manager._mark_installed(source["id"], str(original))
    for _ in range(2):
        manager = VoiceGalleryManager(db_path=manager.db_path, files_root=manager.files_root)
    voices = manager.list_voices("audiocpp")
    assert len(voices) == 1
    voice = voices[0]
    assert voice.voice_id == "audiocpp_es_asun"
    assert voice.ref_text == "Hola, soy Asun." and voice.is_reference_audio
    path = manager.install(voice)
    assert path != original and path.read_bytes() == original.read_bytes()
    manager.uninstall(manager.get_voice(voice.voice_id))
    assert original.exists() and not path.exists()


def test_catalog_sync_and_service_resolve_reference_and_transcript(tmp_path):
    from app.core.settings_manager import SettingsManager
    from app.server.ltv_service import LocalText2VoiceService
    (tmp_path / "reference.wav").write_bytes(b"audio")
    (tmp_path / "voice.json").write_text(json.dumps({
        "id": "omnivoice_es_asun", "engine": "omnivoice", "name": "Asun",
        "language": "es", "preview_audio": "reference.wav", "ref_text": "Hola, soy Asun.",
    }))
    (tmp_path / "catalog.json").write_text(json.dumps({"voices": ["voice.json"]}))
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "files")
    gallery.sync(tmp_path / "catalog.json")
    service = LocalText2VoiceService(SettingsManager(tmp_path / "config.json"))
    service.voice_gallery_manager = gallery
    try:
        rows = service.list_voices("audiocpp", installed_only=False)
        assert len(rows) == 1 and not rows[0]["installed"]
        assert not service.list_voices("audiocpp", installed_only=True)
        for model in ("higgs_audio_tts_4b_q8_0", "fish_audio_s2_pro_q8_0"):
            config = service._voice_config("audiocpp", {"voice": rows[0]["id"], "model": model})
            assert config["reference_text"] == "Hola, soy Asun."
            assert config["model"] == model
            expected_chunk = 200 if model.startswith("higgs") else 400
            assert config["text_chunk_size"] == expected_chunk
            assert service._chunk_size("audiocpp", {"model": model}) == expected_chunk
        gallery.sync(tmp_path / "catalog.json")
        assert service.list_voices("audiocpp")[0]["installed"]
    finally:
        service.close()

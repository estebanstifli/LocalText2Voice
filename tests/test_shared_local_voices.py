import wave
from pathlib import Path

import pytest

from app.tts.voice_gallery_manager import VoiceGalleryManager
from app.core.settings_manager import SettingsManager
from app.server.ltv_service import LocalText2VoiceService


@pytest.mark.parametrize("source_engine", sorted(VoiceGalleryManager.REFERENCE_ENGINES))
@pytest.mark.parametrize("transcript", ["", "Exact sample text."])
def test_local_reference_shared_without_aliases(tmp_path, source_engine, transcript, monkeypatch):
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path / "assets"))
    source = tmp_path / "sample.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 24000 * 4)
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    voice = gallery.import_reference_voice(source_engine, source, name="Shared voice", ref_text=transcript)
    gallery = VoiceGalleryManager(db_path=gallery.db_path, files_root=gallery.files_root)
    for target in gallery.REFERENCE_ENGINES:
        shared, = gallery.list_voices(target)
        assert shared.voice_id == voice.voice_id
        assert shared.ref_text == transcript
        assert gallery.ensure_voice_audio(shared) == Path(voice.installed_path)
    assert len(gallery.list_voices()) == 1
    assert not gallery.list_voices("piper") and not gallery.list_voices("kokoro")
    updated = gallery.update_reference_voice(voice.voice_id, source=None, name="Edited", language="en",
        language_name="English", ref_text="Corrected transcript", short_description="", gender="",
        age_style="", voice_style="", tags=[])
    for target in gallery.REFERENCE_ENGINES:
        assert gallery.list_voices(target)[0].ref_text == updated.ref_text
    settings = SettingsManager(tmp_path / "settings.json")
    settings.settings["qwen"]["model"] = "base_1_7b"
    settings.save()
    service = LocalText2VoiceService(settings)
    service.voice_gallery_manager = gallery
    try:
        for target in gallery.REFERENCE_ENGINES:
            assert any(row["id"] == voice.voice_id for row in service.list_voices(target)), target
            config = service._voice_config(target, {"voice": voice.voice_id})
            assert Path(config["reference_audio_path"]) == Path(voice.installed_path)
    finally:
        service.close()
    gallery.delete_reference_voice(updated)
    assert not Path(voice.installed_path).exists()
    assert all(not gallery.list_voices(target) for target in gallery.REFERENCE_ENGINES)

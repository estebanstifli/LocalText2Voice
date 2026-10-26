import hashlib
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.tts.voice_gallery_manager import VoiceGalleryManager
from app.tts.audiocpp_engine import AudioCppTTSEngine
from tests.test_voice_gallery_manager import write_reference_wav


def manager_at(tmp_path):
    return VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "files")


def test_import_preserves_original_48k_and_same_transcript(tmp_path):
    source = tmp_path / "original.wav"
    write_reference_wav(source, sample_rate=48000)
    gallery = manager_at(tmp_path)
    voice = gallery.import_reference_voice("audiocpp", source, name="Story", ref_text="Exact words.")
    assert voice.name == "Story" and voice.ref_text == "Exact words."
    paths = {rate: Path(voice.installed_path).parent / name
             for rate, name in voice.metadata["audio_versions"].items()}
    assert paths["48000"].read_bytes() == source.read_bytes()
    for rate, path in paths.items():
        with wave.open(str(path)) as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (int(rate), 1, 2)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == voice.metadata["audio_sha256_by_rate"][rate]
    assert gallery.preview_source(voice) == str(paths["48000"])
    gallery.delete_reference_voice(voice)
    assert all(not path.exists() for path in paths.values())
    assert source.exists()


def test_failed_replacement_preserves_both_files_and_catalog(tmp_path, monkeypatch):
    source = tmp_path / "original.wav"
    replacement = tmp_path / "replacement.wav"
    write_reference_wav(source, sample_rate=48000)
    write_reference_wav(replacement, seconds=4.5, sample_rate=48000)
    gallery = manager_at(tmp_path)
    voice = gallery.import_reference_voice("audiocpp", source, name="Original", ref_text="Old words.")
    paths = [Path(voice.installed_path), gallery.high_rate_path(Path(voice.installed_path))]
    original_bytes = [p.read_bytes() for p in paths]
    replace = Path.replace
    def fail_second(src, dst):
        if src.name == "48000.wav":
            raise OSError("disk full")
        return replace(src, dst)
    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="disk full"):
        gallery.update_reference_voice(voice.voice_id, source=replacement, name="Changed",
            ref_text="New words.", language="en", language_name="English", short_description="",
            gender="", age_style="", voice_style="")
    assert [p.read_bytes() for p in paths] == original_bytes
    assert gallery.get_voice(voice.voice_id).name == "Original"
    assert gallery.get_voice(voice.voice_id).ref_text == "Old words."
    assert not list(paths[0].parent.glob("voice-rates-*"))


def test_audiocpp_selects_high_rate_and_keeps_legacy_fallback(tmp_path):
    source = tmp_path / "reference.wav"
    write_reference_wav(source)
    engine = AudioCppTTSEngine(manager=Mock())
    for model in ("breeze_tts_2_q8_0", "voxcpm2_orig", "voxcpm2_bf16", "voxcpm2_q8_0"):
        config = {"model": model, "reference_audio_path": str(source), "reference_text": "Words."}
        high = VoiceGalleryManager.high_rate_path(source)
        high.unlink(missing_ok=True)
        assert engine._config(config)["reference_audio_path"] == str(source)
        write_reference_wav(high, sample_rate=48000)
        assert engine._config(config)["reference_audio_path"] == str(high)
        high.write_bytes(b"invalid")
        assert engine._config(config)["reference_audio_path"] == str(source)

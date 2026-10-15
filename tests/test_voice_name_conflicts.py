import wave

import pytest
from PySide6.QtWidgets import QMessageBox

from app.tts.voice_gallery_manager import VoiceGalleryManager
from app.tts.audiocpp_config import resolve_voice
from app.ui.save_reference_voice import save_designed_voice


def sample(path, value):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes([value, 0]) * 24000 * 4)
    return path


@pytest.mark.parametrize("choice", ["Yes", "No", "Cancel"])
def test_design_duplicate_overwrite_rename_or_cancel(tmp_path, monkeypatch, choice):
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    old = sample(tmp_path / "old.wav", 1)
    new = sample(tmp_path / "new.wav", 2)
    original = gallery.import_reference_voice("audiocpp", old, name="voz4", ref_text="Old text")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: getattr(QMessageBox.StandardButton, choice))
    monkeypatch.setattr("app.ui.save_reference_voice.QInputDialog.getText", lambda *args, **kwargs: ("voz5", True))
    result = save_designed_voice(None, gallery, "audiocpp", new,
        name="voz4", language="en", language_name="English", ref_text="New text",
        short_description="New voice", voice_style="Calm", tags=["designed"],
        min_duration_seconds=1, max_duration_seconds=60)
    voices = gallery.list_voices("audiocpp")
    if choice == "Cancel":
        assert result is None and len(voices) == 1
        assert voices[0].ref_text == "Old text"
    elif choice == "Yes":
        assert len(voices) == 1 and result.voice_id == original.voice_id
        assert result.name == "voz4" and result.ref_text == "New text"
        assert result.installed_path == original.installed_path
        from pathlib import Path
        assert Path(result.installed_path).read_bytes() == new.read_bytes()
    else:
        assert len(voices) == 2 and result.name == "voz5"
        assert gallery.get_voice(original.voice_id).ref_text == "Old text"


def test_deleted_duplicate_does_not_shadow_remaining_voice(tmp_path):
    first = sample(tmp_path / "first.wav", 1)
    second = sample(tmp_path / "second.wav", 2)
    config = {"voices": {
        "old-id": {"name": "voz4", "reference_audio_path": str(first)},
        "new-id": {"name": "voz4", "reference_audio_path": str(second), "reference_text": "Second voice"},
    }}
    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_voice(config, "voz4")
    first.unlink()
    result = resolve_voice(config, "voz4")
    assert result["reference_audio_path"] == str(second)
    assert result["reference_text"] == "Second voice"


def test_conflict_dialog_uses_ui_translation(tmp_path, monkeypatch):
    from app.utils.i18n import Translator

    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    source = sample(tmp_path / "sample.wav", 1)
    gallery.import_reference_voice("audiocpp", source, name="Narradora", ref_text="Hola")
    captured = []
    def question(parent, title, message, *args):
        captured.append((title, message))
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", question)
    assert save_designed_voice(None, gallery, "audiocpp", source,
        name="Narradora", translate=Translator("es").text) is None
    assert captured[0][0] == "La voz ya existe"
    assert '"Narradora"' in captured[0][1] and "reemplazarla" in captured[0][1]

import os
import wave
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QMenu, QPlainTextEdit

from app.tts.voice_gallery_manager import VoiceGalleryManager
from app.ui.main_window import MainWindow


@pytest.mark.parametrize("engine", sorted(VoiceGalleryManager.REFERENCE_ENGINES))
def test_shared_reference_voice_menu_refresh_and_insertion(tmp_path, engine):
    app = QApplication.instance() or QApplication([])
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    source = tmp_path / "sample.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 24000 * 4)
    window = SimpleNamespace(
        voice_gallery_manager=gallery, tts_engine_combo=QComboBox(),
        markup_voice_menu=QMenu(), text_editor=QPlainTextEdit(),
        tr=lambda key, fallback: fallback, _update_markup_editor_assist=Mock(),
    )
    window.tts_engine_combo.addItem(engine, engine)
    window._voice_page_rows = lambda selected: [
        {"name": voice.name, "language": voice.language,
         "gallery_voice": voice, "installed": gallery.is_installed(voice)}
        for voice in gallery.list_voices(selected)
    ]
    for method in ("_local_markup_voice_choices", "_markup_voice_row_is_local",
                   "_populate_markup_voice_menu", "_insert_markup_voice"):
        setattr(window, method, getattr(MainWindow, method).__get__(window))
    window._populate_markup_voice_menu()
    assert not window.markup_voice_menu.actions()[0].isEnabled()
    voice = gallery.import_reference_voice(
        "audiocpp", source, name="Designed voice", language="es", ref_text="Hola.")
    window._populate_markup_voice_menu()
    action, = window.markup_voice_menu.actions()
    assert action.isEnabled() and action.text() == "Designed voice (es)"
    action.trigger()
    assert window.text_editor.toPlainText() == '{{voice "Designed voice"}}'
    gallery.delete_reference_voice(voice)
    window._populate_markup_voice_menu()
    assert not window.markup_voice_menu.actions()[0].isEnabled()


@pytest.mark.parametrize("engine", ["piper", "kokoro", "qwen"])
def test_installed_builtin_voice_menu(engine):
    app = QApplication.instance() or QApplication([])
    window = SimpleNamespace(
        tts_engine_combo=QComboBox(),
        kokoro_python_manager=Mock(is_installed=Mock(return_value=True)),
        qwen_manager=Mock(is_installed=Mock(return_value=True)),
        _voice_page_rows=lambda selected: [{"name": "Narrator", "language": "English"}],
    )
    window.tts_engine_combo.addItem(engine, engine)
    window._markup_voice_row_is_local = MainWindow._markup_voice_row_is_local.__get__(window)
    assert MainWindow._local_markup_voice_choices(window) == [("Narrator", "Narrator (English)")]

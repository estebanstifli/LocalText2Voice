from __future__ import annotations

import os
from unittest.mock import Mock
import wave
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from app.tts.audiocpp_config import VOXCPM_MODELS
from app.core.settings_manager import SettingsManager
from app.ui.audiocpp_settings import AudioCppInstallWorker
from app.ui.main_window import MainWindow
from app.ui.voxcpm_voice_design_dialog import VoxCPMVoiceDesignDialog


@pytest.mark.parametrize("model_id", ["higgs_audio_tts_4b_q8_0", "fish_audio_s2_pro_q8_0", "breeze_tts_2_q8_0", *sorted(VOXCPM_MODELS)])
def test_optional_engine_configuration_voice_selection_and_persistence(tmp_path, monkeypatch, model_id):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    manager = SettingsManager(tmp_path / "config.json")
    monkeypatch.setattr("app.ui.main_window.SettingsManager", lambda: manager)
    # The test exercises settings synchronously, without startup network timers.
    monkeypatch.setattr("app.ui.main_window.QTimer.singleShot", lambda *args: None)
    monkeypatch.setattr(MainWindow, "_refresh_wav_cache_stats", lambda *args, **kwargs: None)
    window = MainWindow()
    try:
        window.voice_gallery_manager._replace_catalog([])
        assert window._select_tts_engine("audiocpp")
        assert not window.audiocpp_manager.is_installed()
        assert window.audiocpp_buttons["install"].isEnabled()
        assert not window.audiocpp_buttons["load"].isEnabled()
        assert "model" not in window.audiocpp_fields
        assert window._select_tts_engine("audiocpp:" + model_id)
        reference = tmp_path / "voz ñ.wav"
        with wave.open(str(reference), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 240)
        window.audiocpp_fields["reference_audio_path"].set_path(str(reference))
        window.audiocpp_fields["reference_text"].setText("Esta es mi voz.")
        window.audiocpp_voice_name.setText("Narradora")
        window._audiocpp_save_voice()
        monkeypatch.setattr(window.audiocpp_manager, "is_installed", lambda *args: True)
        window._refresh_voices_page()
        assert window.voices_design_button.isHidden() == (model_id not in {"breeze_tts_2_q8_0", *VOXCPM_MODELS})
        assert window.voices_design_button.isEnabled() == (model_id in {"breeze_tts_2_q8_0", *VOXCPM_MODELS})
        if model_id in {"breeze_tts_2_q8_0", *VOXCPM_MODELS}:
            with monkeypatch.context() as scoped:
                designer = Mock()
                factory = Mock(return_value=designer)
                scoped.setattr("app.ui.voxcpm_voice_design_dialog.VoxCPMVoiceDesignDialog" if model_id in VOXCPM_MODELS else "app.ui.breeze_voice_design_dialog.BreezeVoiceDesignDialog", factory)
                window.voices_design_button.click()
                factory.assert_called_once()
                assert factory.call_args.args[2]["model"] == model_id
                designer.voice_saved.connect.assert_called_once_with(window._on_breeze_designed_voice_saved)
                designer.exec.assert_called_once()
        rows = window._voice_page_rows("audiocpp", engine_ready=True)
        assert len(rows) == 1
        window._select_voice_page_row_data(rows[0])
        assert window._current_voice_config()["reference_text"] == "Esta es mi voz."
        persisted = SettingsManager(manager.path).settings["audiocpp"]
        assert persisted["model"] == model_id
        assert persisted["voices"]["Narradora"]["reference_audio_path"] == str(reference)
        assert window.audiocpp_chunk_size_spin.value() == 200
        assert window.audiocpp_fields["text_chunk_size"].value() == (200 if model_id.startswith("higgs") else 600 if model_id.startswith("breeze") else 400)
        from app.tts.audiocpp_config import MODELS
        table_rows = window._engine_table_rows()
        ids = [row["engine_id"] for row in table_rows]
        start = ids.index("f5_russian") + 1
        assert ids[start:start + len(MODELS)] == ["audiocpp:" + key for key in MODELS]
        assert [row["name"] for row in table_rows[start:start + len(MODELS)]] == [spec.name for spec in MODELS.values()]
        window.audiocpp_fields["text_chunk_size"].setValue(275)
        other = next(key for key in MODELS if key != model_id)
        assert window._select_tts_engine("audiocpp:" + other)
        window.audiocpp_fields["text_chunk_size"].setValue(325)
        assert window._select_tts_engine("audiocpp:" + model_id)
        assert window.audiocpp_fields["text_chunk_size"].value() == 275
        assert MODELS[model_id].name in window.audiocpp_notice.text()
        assert "GGUF " + MODELS[model_id].precision in window.audiocpp_notice.text()
        if model_id in VOXCPM_MODELS:
            assert window._current_chunk_size() == 400
        assert window._active_tts_engine_row_id() == "audiocpp:" + model_id
        assert window.fish_chunk_size_spin.value() == 400
        # Row callbacks act on their own model even if another model is selected.
        with monkeypatch.context() as scoped:
            actions = []
            scoped.setattr(window, "_install_audiocpp", lambda: actions.append(("install", window._audiocpp_settings()["model"])))
            scoped.setattr(window, "_remove_audiocpp", lambda: actions.append(("remove", window._audiocpp_settings()["model"])))
            scoped.setattr(window, "_start_preload_tts_engine", lambda engine: actions.append(("load", window._audiocpp_settings()["model"])))
            for index, action in ((1, "install"), (2, "remove"), (3, "load")):
                window._select_tts_engine("audiocpp:" + model_id)
                widget = window._build_tts_engine_action_widget("audiocpp:" + other)
                widget.findChildren(QPushButton)[index].click()
                assert actions[-1] == (action, other)
            window.host_loaded_tts_engine_ids.add("audiocpp")
            window._audiocpp_loaded_model = other
            loaded_widget = window._build_tts_engine_action_widget("audiocpp:" + other)
            idle_widget = window._build_tts_engine_action_widget("audiocpp:" + model_id)
            assert loaded_widget.findChildren(QPushButton)[-1].text() == window.tr("unload_from_memory", "Unload from memory")
            assert idle_widget.findChildren(QPushButton)[-1].text() == window.tr("load_into_memory", "Load into memory")
            window.host_loaded_tts_engine_ids.clear()
        window._select_tts_engine("audiocpp:" + model_id)
        from app.tts.higgs_tags import TAG_GROUPS
        menus = window.markup_emotion_menu.actions()
        assert [action.text() for action in menus] == ["Chatterbox", "IndexTTS", "Higgs", "Fish Audio", "Breeze TTS 2", "VoxCPM 2"]
        for category_action, (category, _, tags) in zip(menus[2].menu().actions(), TAG_GROUPS):
            for action, (value, _, vocal_text) in zip(category_action.menu().actions(), tags):
                window.text_editor.setPlainText("Texto para narrar.")
                cursor = window.text_editor.textCursor()
                cursor.setPosition(0)
                window.text_editor.setTextCursor(cursor)
                action.trigger()
                expected = f"<|{category}:{value}|>" + vocal_text + (" " if vocal_text else "")
                assert window.text_editor.toPlainText() == expected + "Texto para narrar."
                assert window._current_voice_config()["model"] == model_id
        from app.tts.fish_tags import TAG_GROUPS as FISH_TAG_GROUPS
        for category_action, (_, _, tags) in zip(menus[3].menu().actions(), FISH_TAG_GROUPS):
            for action, tag in zip(category_action.menu().actions(), tags):
                window.text_editor.setPlainText("Texto.")
                action.trigger()
                assert window.text_editor.toPlainText() == "[" + tag + "]Texto."
                assert window._current_voice_config()["model"] == model_id
        from app.tts.breeze_tags import VOCAL_TAGS
        breeze_menus = menus[4].menu().actions()
        for action, tag in zip(breeze_menus[2].menu().actions(), VOCAL_TAGS):
            window.text_editor.setPlainText("Hello.")
            action.trigger()
            assert window.text_editor.toPlainText() == tag + "Hello."
            assert action.text() == tag[1:-1].capitalize()
        window.text_editor.setPlainText("Hello.")
        breeze_menus[0].menu().actions()[0].trigger()
        assert window.text_editor.toPlainText() == '{{emotion happy}}Hello.'
        assert window.breeze_chunk_size_spin.value() == 600
        assert window.voxcpm_chunk_size_spin.value() == 400
        assert window.audiocpp_fields["num_inference_steps"].isHidden() == (model_id not in VOXCPM_MODELS)
        assert window.audiocpp_fields["temperature"].isHidden() == (model_id in VOXCPM_MODELS)
        voxcpm_actions = menus[5].menu().actions()[0].menu().actions()
        for action, expected in ((voxcpm_actions[0], '{{emotion happy}}'), (voxcpm_actions[-1], '{{emotion off}}'), (voxcpm_actions[-2], '{{emotion custom ""}}')):
            window.text_editor.setPlainText("Hola.")
            action.trigger()
            assert window.text_editor.toPlainText() == expected + "Hola."

        if model_id == "breeze_tts_2_q8_0":
            window.audiocpp_fields["instruction"].setText("Speak with joy.")
            window.audiocpp_fields["top_k"].setValue(0)
            window._select_tts_engine("audiocpp:higgs_audio_tts_4b_q8_0")
            assert window.audiocpp_fields["instruction"].isHidden()
            window._select_tts_engine("audiocpp:" + model_id)
            assert not window.audiocpp_fields["instruction"].isHidden()
            assert window.audiocpp_fields["instruction"].text() == "Speak with joy."
            assert window.audiocpp_fields["top_k"].value() == 0
        gallery = window.voice_gallery_manager
        gallery._replace_catalog(gallery._expand_compatible_documents([{
            "id": "omnivoice_es_catalogo", "engine": "omnivoice", "name": "Voz del catálogo",
            "language": "es", "preview_audio": str(reference), "ref_text": "Texto del catálogo.",
        }]))
        rows = window._voice_page_rows("audiocpp", engine_ready=True)
        catalog_row = next(row for row in rows if row.get("gallery_voice"))
        assert catalog_row["name"] == "Voz del catálogo"
        assert not catalog_row["installed"]
        install = Mock()
        monkeypatch.setattr(window, "_start_voice_gallery_operation", install)
        window._select_voice_page_row_data(catalog_row)
        install.assert_called_once_with("install", catalog_row["gallery_voice"])
        assert window.pending_audiocpp_gallery_voice_id == catalog_row["id"]
        gallery.install(catalog_row["gallery_voice"])
        # Real worker completion reopens the catalog and applies the pending voice.
        window._on_voice_gallery_finished("Installed test voice")
        assert window.pending_audiocpp_gallery_voice_id is None
        config = window._current_voice_config()
        assert config["reference_text"] == "Texto del catálogo."
        assert config["model"] == model_id
        from app.tts.audiocpp_config import resolve_voice
        assert resolve_voice(config, "Voz del catálogo")["reference_text"] == "Texto del catálogo."
        rows = window._voice_page_rows("audiocpp", engine_ready=True)
        assert len(rows) == 2  # Manual voice plus one catalog entry, no alias duplicate.
        catalog_row = next(row for row in rows if row.get("gallery_voice"))
        assert catalog_row["selected"] and catalog_row["installed"]
        assert catalog_row["name"] == "Voz del catálogo"
        assert any(row["id"] == catalog_row["id"] for row in window._generation_voice_rows("audiocpp"))
        window._select_voice_page_row_data(catalog_row)
        assert window._current_voice_config()["reference_text"] == "Texto del catálogo."
        window._refresh_voices_page()
        assert window.voices_manage_button.text() == window.tr("clone_voice", "Clone Voice")
        assert window.voices_manage_button.isEnabled()
        clone_source = tmp_path / "clone.wav"
        with wave.open(str(clone_source), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 24000 * 4)
        values = dict(source=clone_source, name="Mi voz clonada", language="es",
                      language_name="Spanish", ref_text="Mi transcripción exacta.",
                      short_description="", gender="", age_style="", voice_style="", tags=[])
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = values
        dialog_factory = Mock(return_value=dialog)
        monkeypatch.setattr("app.ui.main_window.CloneVoiceDialog", dialog_factory)
        errors = Mock()
        monkeypatch.setattr(window, "_show_error", errors)
        window.voices_manage_button.click()
        dialog_factory.assert_called_once()
        errors.assert_not_called()
        clones = [v for v in window.voice_gallery_manager.list_voices("audiocpp") if v.is_user_import]
        assert len(clones) == 1 and clones[0].name == "Mi voz clonada"
        config = window._current_voice_config()
        assert config["model"] == model_id
        assert config["reference_text"] == values["ref_text"]
        assert config["reference_audio_path"] == clones[0].installed_path
        assert any(row["id"] == clones[0].voice_id for row in window._generation_voice_rows("audiocpp"))
        # A clone without its transcript is rejected before adding another voice.
        values["ref_text"] = ""
        window.voices_manage_button.click()
        errors.assert_called_once()
        assert len([v for v in window.voice_gallery_manager.list_voices("audiocpp") if v.is_user_import]) == 1
    finally:
        window.deleteLater()


def test_multi_gigabyte_progress_fits_qt_integer_signal():
    application = QApplication.instance() or QApplication([])
    worker = AudioCppInstallWorker(Mock(), {})
    values = []
    worker.progress.connect(lambda *args: values.append(args))
    worker._progress(5_000_000_000, 6_000_000_000, "download")
    assert values == [(833, 1000, "download")]

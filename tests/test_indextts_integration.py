from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QFont
from app.core.audio_pipeline import AudioPipeline
from app.core.ltv_markup import LTVMarkupCompiler
from app.core.settings_manager import SettingsManager
from app.core.text_processor import TextChunk
from app.server.ltv_service import LocalText2VoiceService
from app.tts.indextts_config import DEFAULTS, validated_config, inference_arguments
from app.tts.engine_registry import create_tts_engine, engine_ids
from app.tts.indextts_engine import IndexTTSTTSEngine
from app.tts.voice_gallery_manager import GalleryVoice
from app.ui.indextts_settings import IndexTTSSettingsMixin
from app.ui.engine_install_dialog import (
    EngineInstallDialog,
    ENGINE_INSTALL_REQUIREMENTS,
)
from app.utils.i18n import Translator


def test_registry_and_settings_round_trip(tmp_path):
    assert "indextts" in engine_ids()
    assert isinstance(
        create_tts_engine("indextts", tmp_path / "piper"), IndexTTSTTSEngine
    )
    manager = SettingsManager(tmp_path / "settings.json")
    config = {**copy.deepcopy(DEFAULTS), "temperature": 0.65, "duration_factor": 1.15}
    manager.settings["indextts"] = config
    manager.save()
    restored = SettingsManager(tmp_path / "settings.json").settings
    assert restored["indextts"] == config
    assert restored["engine_chunk_sizes"]["indextts"] == 400


def test_legacy_global_emotion_is_cleared_without_touching_runtime_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"indextts": {
        **DEFAULTS, "emotion_mode": "auto", "emo_text": "Sad",
        "emo_audio_prompt": "old.wav", "emo_alpha": 0.9,
        "emo_vector": [0.8] * 8, "use_random": True, "instruct": "Angry",
        "reference_audio_path": "narrator.wav", "language": "EN", "temperature": 0.65,
    }}), encoding="utf-8")
    manager = SettingsManager(path)
    config = manager.settings["indextts"]
    for key in ("emotion_mode", "emo_text", "emo_audio_prompt", "emo_alpha", "emo_vector", "use_random"):
        assert config[key] == DEFAULTS[key]
    assert "instruct" not in config
    assert config["reference_audio_path"] == "narrator.wav"
    assert config["temperature"] == 0.65
    assert config["language"] == "EN"
    assert "emo_vector" not in inference_arguments(config, "Unmarked text.", "out.wav")
    manager.save()
    assert SettingsManager(path).settings["indextts"] == config


def test_server_config_carries_emotion_parameters_and_zero_intensity():
    service = object.__new__(LocalText2VoiceService)
    service.settings_manager = SimpleNamespace(
        settings={"indextts": {**DEFAULTS, "emo_alpha": 0.8}, "speed": 1.1}
    )
    service._match_gallery_voice = Mock(
        return_value=SimpleNamespace(installed_path="voice.wav", ref_audio_path="")
    )
    service.voice_gallery_manager = SimpleNamespace(
        ensure_voice_audio=Mock(return_value=Path("voice.wav"))
    )
    config = service._voice_config(
        "indextts",
        {"voice": "narrator", "instruct": "angry", "emo_alpha": 0, "lang": "Spanish"},
    )
    assert config["reference_audio_path"] == "voice.wav"
    assert config["speed"] == 1.1
    assert validated_config(config)["emo_alpha"] == 0
    assert validated_config(config)["emotion_mode"] == "text"
    assert inference_arguments(config, "script", "output.wav")["emo_text"] == "angry"


def test_markup_instruction_changes_only_one_chunk():
    pipeline = AudioPipeline(Mock())
    base = {**DEFAULTS, "engine": "indextts", "reference_audio_path": "voice.wav"}
    chunk = TextChunk(
        text="Hola.",
        ends_paragraph=True,
        markup_state={
            "language": "English",
            "config_overrides": {"instruct": "Sad", "emo_alpha": 0},
        },
    )
    config = pipeline._voice_config_for_chunk(base, chunk)
    args = inference_arguments(config, chunk.text, "out.wav")
    assert args["emo_text"] == "Sad"
    assert args["lang"] == "EN"
    assert args["emo_alpha"] == 0
    next_config = pipeline._voice_config_for_chunk(
        base, TextChunk(text="Next.", ends_paragraph=True)
    )
    assert next_config["emotion_mode"] == "reference"
    assert "instruct" not in base


def test_documented_emotion_command_parses_without_being_spoken():
    result = LTVMarkupCompiler.compile(
        '{{cmd "instruct": "Estoy muy triste.", "emo_alpha": 0.6}}No esperaba que terminara así.',
        "indextts",
    )
    segment = result.sections[0].segments[0]
    assert segment.text == "No esperaba que terminara así."
    assert segment.state["config_overrides"] == {
        "instruct": "Estoy muy triste.",
        "emo_alpha": 0.6,
    }


def test_markup_voice_switch_preserves_emotion(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.touch()
    voice = SimpleNamespace(
        name="Narrador",
        voice_id="indextts_imported_1",
        engine_voice_id="",
        language="es",
        language_name="Spanish",
        short_description="",
        gender="",
        age_style="",
        voice_style="",
        tags=[],
        installed_path=str(audio),
        ref_audio_path=str(audio),
    )
    pipeline = AudioPipeline(Mock())
    pipeline._markup_voice_cache["indextts_gallery_manager"] = SimpleNamespace(
        list_voices=lambda engine: [voice], ensure_voice_audio=lambda item: audio
    )
    config = {"engine": "indextts", "emotion_mode": "text", "emo_text": "Happy"}
    pipeline._apply_markup_voice(config, "indextts", "Narrador", "Spanish")
    assert config["reference_audio_path"] == str(audio)
    assert config["emo_text"] == "Happy"
    assert config["language"] == "ES"


def test_selecting_remote_voice_applies_it_after_download(monkeypatch, tmp_path):
    from app.ui.main_window import MainWindow

    voice = GalleryVoice(
        voice_id="indextts_es_asun",
        engine="indextts",
        name="Asun",
        language="es",
        language_name="Español",
        voice_type="Reference voice",
        install_type="reference_audio",
        ref_audio_url="https://example.test/asun.wav",
    )
    gallery = SimpleNamespace(
        is_installed=lambda item: False,
        get_voice=lambda voice_id: voice,
    )
    window = SimpleNamespace(
        _voice_engine_is_ready=lambda engine: True,
        voice_gallery_manager=gallery,
        _start_voice_gallery_operation=Mock(),
        pending_indextts_gallery_voice_id=None,
        pending_f5_russian_gallery_voice_id=None,
        pending_qwen_gallery_voice_id=None,
        pending_removed_gallery_voice=None,
        voices_progress_bar=Mock(),
        settings={},
        settings_manager=Mock(),
        _apply_indextts_gallery_reference=Mock(return_value=True),
        _save_settings=Mock(),
        log_view=Mock(),
        _refresh_voices_page=Mock(),
    )
    MainWindow._select_voice_page_row_data(
        window, {"engine": "indextts", "gallery_voice": voice}
    )
    assert window.pending_indextts_gallery_voice_id == voice.voice_id
    window._start_voice_gallery_operation.assert_called_once_with("install", voice)
    monkeypatch.setattr(
        "app.ui.main_window.VoiceGalleryManager", lambda **kwargs: gallery
    )
    MainWindow._on_voice_gallery_finished(window, "Installed")
    window._apply_indextts_gallery_reference.assert_called_once_with(voice)
    window._save_settings.assert_called_once()
    assert window.pending_indextts_gallery_voice_id is None


def test_server_can_select_remote_voice_when_another_is_installed():
    service = object.__new__(LocalText2VoiceService)
    voices = [
        SimpleNamespace(
            name="Asun",
            voice_id="indextts_es_asun",
            language="es",
            language_name="Spanish",
        ),
        SimpleNamespace(
            name="Azahara",
            voice_id="indextts_es_azahara",
            language="es",
            language_name="Spanish",
        ),
    ]
    service.voice_gallery_manager = SimpleNamespace(
        list_voices=lambda engine: voices,
        is_installed=lambda voice: voice is voices[0],
    )
    assert service._match_gallery_voice("indextts", "Azahara", "es") is voices[1]


class PanelHost(IndexTTSSettingsMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.settings = {"indextts": copy.deepcopy(DEFAULTS)}
        self.translator = Translator("es")

    def tr(self, key, fallback, **kwargs):
        return self.translator.text(key, fallback, **kwargs)


def test_panel_saves_runtime_settings_and_leaves_emotions_to_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    host = PanelHost()
    host.settings["indextts"].update(emotion_mode="text", emo_text="Old instruction")
    panel = host._build_indextts_engine_panel()
    try:
        assert host._indextts_settings() == DEFAULTS
        assert not {"emotion_mode", "emo_text", "emo_audio_prompt", "emo_alpha", "emo_vector", "use_random"} & host.indextts_fields.keys()
        assert "preview" not in host.indextts_buttons
        assert not hasattr(host, "indextts_preview_text")
        host.indextts_fields["temperature"].setValue(0.65)
        host.indextts_fields["duration_factor"].setValue(1.15)
        config = host._indextts_settings()
        assert config["temperature"] == 0.65
        assert config["duration_factor"] == 1.15
        assert config["emotion_mode"] == "reference"
        host.settings["indextts"] = config
        host._load_indextts_settings()
        assert host._indextts_settings() == config
        panel.resize(950, 650)
        panel.show()
        app.processEvents()
        panel.grab().save(str(tmp_path / "indextts-panel.png"))
    finally:
        panel.close()
        host.close()


def test_precision_can_be_selected_before_engine_is_installed():
    app = QApplication.instance() or QApplication([])
    host = PanelHost()
    panel = host._build_indextts_engine_panel()
    dialog = EngineInstallDialog(
        "IndexTTS-2.5",
        ENGINE_INSTALL_REQUIREMENTS["indextts"],
        100,
        "C:",
        host.tr,
        host,
    )
    try:
        host._configure_indextts_install_dialog(dialog)
        dialog.indextts_runtime_combo.setCurrentIndex(2)
        assert host._indextts_settings()["device"] == "cpu"
        assert host._indextts_settings()["dtype"] == "float32"
        dialog.install_requested.emit()
        assert not dialog.indextts_runtime_combo.isEnabled()
    finally:
        dialog.close()
        panel.close()
        host.close()

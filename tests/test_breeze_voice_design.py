import os
import time
import threading
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from app.ui.breeze_voice_design_dialog import BreezeVoiceDesignDialog, BreezeDesignPreviewWorker, EXAMPLES
from app.tts.audiocpp_config import BREEZE_MODEL, model_config
from app.tts.voice_gallery_manager import VoiceGalleryManager


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def write_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, VoiceGalleryManager.REFERENCE_SAMPLE_RATE, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * VoiceGalleryManager.REFERENCE_SAMPLE_RATE * 2)


def wait_for(application, predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.01)
    assert predicate()


def make_dialog(tmp_path, monkeypatch, dialog_class=BreezeVoiceDesignDialog, model_id=BREEZE_MODEL):
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path / "assets"))
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    client = Mock()
    def synthesize(payload):
        write_wav(payload["output_wav"])
        return {"output_wav": payload["output_wav"]}
    client.synthesize_review_segment.side_effect = synthesize
    from types import SimpleNamespace
    from app.server.voice_design import VoiceDesignService
    host = SimpleNamespace(voice_gallery_manager=gallery, settings={},
                           faster_whisper_manager=Mock(), audiocpp_manager=Mock(), omnivoice_manager=Mock())
    verifier = Mock()
    def server_synthesize(text, path, config):
        verifier.transcribe.return_value = {"text": text}
        client.synthesize_review_segment({"text": text, "voice_config": config, "output_wav": str(path)})
    host.synthesize_segment = server_synthesize
    service = VoiceDesignService(host, lambda _: verifier)
    drafts = {}
    def http(method, path, payload=None):
        if path == "/voices/design/preview":
            result = service.run({**payload, "name": "Preview", "preview_only": True}, threading.Event())
            draft_id = str(len(drafts) + 1)
            drafts[draft_id] = result
            return {"job_id": draft_id, "status": "complete", "result": result}
        draft_id = path.split("/")[-2]
        return service.save_preview(drafts[draft_id], payload["name"],
                                    labels=payload["labels"], replace_voice_id=payload.get("replace_voice_id"), draft_id=draft_id)
    client.request_json.side_effect = http
    config = model_config({"model": model_id, "reference_audio_path": "existing.wav",
                           "reference_text": "Existing transcript", "voice": "old", "speed": 2,
                           "_postprocess_speed": 2, "backend": "cuda"})
    dialog = dialog_class(client, gallery, config, "ffmpeg", lambda key, default: default)
    monkeypatch.setattr(dialog, "play_preview", Mock())
    return dialog, client, gallery, config


def test_design_sample_save_and_reuse_exact_reference(application, tmp_path, monkeypatch):
    from app.core.settings_manager import SettingsManager
    from app.server.ltv_service import LocalText2VoiceService
    dialog, client, gallery, original = make_dialog(tmp_path, monkeypatch)
    saved = []
    dialog.voice_saved.connect(saved.append)
    dialog.name_edit.setText("My Breeze narrator")
    dialog.sample_text_edit.setPlainText("This is my exact sample text.")
    dialog.instruction_edit.setPlainText("An expressive mature voice.")
    dialog.generate_preview()
    wait_for(application, lambda: not dialog.busy)
    assert dialog.save_button.isEnabled()
    payload = client.synthesize_review_segment.call_args.args[0]
    config = payload["voice_config"]
    assert config["engine"] == "audiocpp" and config["model"] == BREEZE_MODEL
    assert config["backend"] == "cuda" and config["guidance_scale"] == 4
    assert config["reference_audio_path"] == config["reference_text"] == ""
    assert "voice" not in config and "_postprocess_speed" not in config
    assert config["instruction"] == "An expressive mature voice."
    assert original["reference_audio_path"] == "existing.wav" and original["speed"] == 2
    dialog.sample_text_edit.setPlainText("A different transcript.")
    assert not dialog.save_button.isEnabled()
    dialog.save_voice()
    assert not saved
    dialog.sample_text_edit.setPlainText(payload["text"])
    dialog.save_voice()
    assert len(saved) == 1
    voice = saved[0]
    assert voice.name == "My Breeze narrator" and voice.language == "en"
    assert voice.ref_text == payload["text"] and voice.voice_style == config["instruction"]
    assert Path(voice.installed_path).is_file() and "breeze" in voice.tags
    dialog.save_voice()
    assert len(saved) == 1  # Double-click cannot duplicate an import.
    preview = dialog.preview_path
    dialog.reject()
    assert preview.exists() and Path(voice.installed_path).is_file()  # Server owns the preview cache.
    # A fresh service/gallery instance finds the durable sample, not the temp preview.
    service = LocalText2VoiceService(SettingsManager(tmp_path / "settings.json"))
    service.voice_gallery_manager = VoiceGalleryManager(db_path=gallery.db_path, files_root=tmp_path / "voices")
    try:
        for model in (BREEZE_MODEL, "higgs_audio_tts_4b_q8_0", "fish_audio_s2_pro_q8_0"):
            reused = service._voice_config("audiocpp", {"model": model, "voice": voice.voice_id})
            assert reused["reference_text"] == payload["text"]
            assert Path(reused["reference_audio_path"]).is_file()
    finally:
        service.close()


def test_languages_and_invalid_inputs(application, tmp_path, monkeypatch):
    dialog, _, _, _ = make_dialog(tmp_path, monkeypatch)
    assert [dialog.language_combo.itemData(i) for i in range(dialog.language_combo.count())] == ["en", "zh"]
    dialog.language_combo.setCurrentIndex(1)
    assert dialog.sample_text_edit.toPlainText() == EXAMPLES["zh"][1]
    assert dialog.values()["config"]["instruction"] == EXAMPLES["zh"][0]
    dialog.sample_text_edit.setPlainText("Custom text retained when changing language.")
    dialog.language_combo.setCurrentIndex(0)
    assert dialog.sample_text_edit.toPlainText().startswith("Custom text")
    for text in ("", "x" * 601, "{{emotion happy}}Hello."):
        dialog.sample_text_edit.setPlainText(text)
        with pytest.raises(ValueError):
            dialog.values()
    dialog.reject()


@pytest.mark.parametrize("model_id", ["voxcpm2_q8_0", "voxcpm2_bf16", "voxcpm2_orig"])
def test_voxcpm_design_spanish_saved_clean_and_reusable(application, tmp_path, monkeypatch, model_id):
    from app.ui.voxcpm_voice_design_dialog import VoxCPMVoiceDesignDialog
    from app.tts.audiocpp_config import VOXCPM_MODEL
    dialog, client, gallery, original = make_dialog(tmp_path, monkeypatch, VoxCPMVoiceDesignDialog, model_id)
    assert dialog.language_combo.count() == 30
    assert "temperature" not in dialog.fields and "num_inference_steps" in dialog.fields
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("es"))
    assert dialog.sample_text_edit.toPlainText() == dialog.EXAMPLES["es"][1]
    # Languages without a stock sample require custom text, not an English sample.
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("ja"))
    assert not dialog.sample_text_edit.toPlainText()
    with pytest.raises(ValueError):
        dialog.values()
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("es"))
    dialog.name_edit.setText("Voz diseñada VoxCPM")
    saved = []
    dialog.voice_saved.connect(saved.append)
    dialog.generate_preview()
    wait_for(application, lambda: not dialog.busy)
    payload = client.synthesize_review_segment.call_args.args[0]
    assert payload["voice_config"]["model"] == model_id
    assert payload["voice_config"]["guidance_scale"] == 2
    assert payload["voice_config"]["num_inference_steps"] == 10
    assert payload["voice_config"]["reference_audio_path"] == ""
    assert "(" not in payload["text"] and payload["text"] == dialog.EXAMPLES["es"][1]
    dialog.save_voice()
    assert len(saved) == 1 and saved[0].language == "es"
    assert saved[0].ref_text == payload["text"]
    assert saved[0].voice_style == payload["voice_config"]["instruction"]
    assert "voxcpm2" in saved[0].tags
    from app.core.settings_manager import SettingsManager
    from app.server.ltv_service import LocalText2VoiceService
    service = LocalText2VoiceService(SettingsManager(tmp_path / "settings.json"))
    service.voice_gallery_manager = gallery
    try:
        reused = service._voice_config("audiocpp", {"model": model_id, "voice": saved[0].voice_id})
        assert reused["reference_text"] == payload["text"]
        assert reused["instruction"] == ""  # Voice identity is now in the saved WAV.
        assert Path(reused["reference_audio_path"]).is_file()
    finally:
        service.close()
    dialog.reject()
    assert Path(saved[0].installed_path).is_file()


def test_failure_allows_retry_and_close_waits_for_worker(application, tmp_path, monkeypatch):
    dialog, client, _, _ = make_dialog(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def fail(payload):
        entered.set()
        release.wait(5)
        raise RuntimeError("Native preview failed")
    client.synthesize_review_segment.side_effect = fail
    dialog.show()
    dialog.generate_preview()
    try:
        wait_for(application, entered.is_set)
        dialog.reject()
        assert dialog.isVisible() and dialog.busy
        assert not dialog.save_button.isEnabled() and not dialog.close_button.isEnabled()
    finally:
        release.set()
        wait_for(application, lambda: not dialog.busy)
    assert "Native preview failed" in dialog.status_label.text()
    assert dialog.generate_button.isEnabled() and dialog.close_button.isEnabled()
    assert not dialog.save_button.isEnabled()
    def success(payload):
        write_wav(payload["output_wav"])
        return {"output_wav": payload["output_wav"]}
    client.synthesize_review_segment.side_effect = success
    dialog.generate_preview()
    wait_for(application, lambda: not dialog.busy)
    assert dialog.save_button.isEnabled()
    dialog.reject()


def test_worker_reports_server_verification_error(tmp_path):
    client = Mock()
    client.request_json.return_value = {"job_id": "failed", "status": "failed", "result": {
        "code": "TRANSCRIPT_MISMATCH", "message": "Transcript differs", "asr_transcript": "wrong words"}}
    worker = BreezeDesignPreviewWorker(client, "Hello", {"engine": "audiocpp", "language": "en"}, tmp_path / "sample.wav")
    outcomes = []
    worker.completed.connect(lambda path, error: outcomes.append((path, error)))
    worker.run()
    assert outcomes[0][0] == "" and "wrong words" in outcomes[0][1]

import hashlib
import threading
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.server.voice_design import VoiceDesignService, VoiceDesignError, SAMPLES, normalize_transcript
from app.tts.voice_gallery_manager import VoiceGalleryManager
from app.server.voice_design import VoiceDesignCancelled


@pytest.fixture
def setup(tmp_path):
    gallery = VoiceGalleryManager(db_path=tmp_path / "voices.db", files_root=tmp_path / "voices")
    host = SimpleNamespace(voice_gallery_manager=gallery,
        settings={"audiocpp": {"model": "voxcpm2_q8_0", "reference_audio_path": "old.wav",
            "reference_text": "old", "_postprocess_speed": 2, "voice": "old"}},
        faster_whisper_manager=Mock(), audiocpp_manager=Mock(), omnivoice_manager=Mock())
    def synthesize(text, path, config):
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x01" * 48000)
    host.synthesize_segment = Mock(side_effect=synthesize)
    verifier = Mock()
    verifier.transcribe.return_value = {"text": SAMPLES["es"]}
    service = VoiceDesignService(host, lambda _: verifier)
    request = dict(name="Lucía", description="Voz cálida", language="es")
    return service, host, verifier, request


def test_save_verified_template_and_reload(setup):
    service, host, verifier, request = setup
    result = service.run(request, threading.Event())
    voice = service.get_voice(result["voice_id"])
    assert result["saved"] and voice["name"] == "Lucía"
    assert voice["ref_text"] == SAMPLES["es"]
    assert voice["metadata"]["sample_text_source"] == "template"
    assert voice["metadata"]["verification_status"] == "passed"
    wav = Path(voice["installed_path"])
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == voice["metadata"]["audio_sha256"]
    with wave.open(str(wav)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
    config = host.synthesize_segment.call_args.args[2]
    assert config["reference_audio_path"] == config["reference_text"] == ""
    assert "_postprocess_speed" not in config and "voice" not in config
    assert verifier.transcribe.call_args.kwargs["language"] == "es"
    reloaded = VoiceGalleryManager(db_path=service.gallery.db_path, files_root=service.gallery.files_root)
    assert reloaded.get_voice(result["voice_id"]).ref_text == SAMPLES["es"]


def test_mismatch_does_not_publish(setup):
    service, host, verifier, request = setup
    verifier.transcribe.return_value = {"text": "Otra frase"}
    with pytest.raises(VoiceDesignError) as error:
        service.run(request, threading.Event())
    assert error.value.payload["code"] == "TRANSCRIPT_MISMATCH"
    assert not service.gallery.list_voices("audiocpp")
    assert not list(service.gallery.files_root.rglob("*.wav"))


@pytest.mark.parametrize("changes,code", [
    ({"language": "fr"}, "SAMPLE_TEXT_REQUIRED"),
    ({"language": "auto"}, "UNSUPPORTED_LANGUAGE"),
    ({"model": "breeze_tts_2_q8_0"}, "UNSUPPORTED_LANGUAGE"),
    ({"name": " "}, "INVALID_REQUEST"),
    ({"engine_id": "qwen"}, "UNSUPPORTED_MODEL"),
])
def test_preflight_errors(setup, changes, code):
    service, host, verifier, request = setup
    with pytest.raises(VoiceDesignError) as error:
        service.run({**request, **changes}, threading.Event())
    assert error.value.payload["code"] == code
    host.synthesize_segment.assert_not_called()


def test_whisper_missing_before_generation(setup):
    service, host, verifier, request = setup
    host.faster_whisper_manager.is_installed.return_value = False
    with pytest.raises(VoiceDesignError, match="Install Faster Whisper"):
        service.run(request, threading.Event())
    host.synthesize_segment.assert_not_called()


def test_name_conflict_before_generation(setup):
    service, host, verifier, request = setup
    service.run(request, threading.Event())
    host.synthesize_segment.reset_mock()
    with pytest.raises(VoiceDesignError) as error:
        service.run({**request, "name": "LUCÍA"}, threading.Event())
    assert error.value.payload["code"] == "NAME_CONFLICT"
    host.synthesize_segment.assert_not_called()


def test_cancel_after_asr_does_not_publish(setup):
    service, host, verifier, request = setup
    cancel = threading.Event()
    def transcribe(*args, **kwargs):
        cancel.set()
        return {"text": SAMPLES["es"]}
    verifier.transcribe.side_effect = transcribe
    with pytest.raises(VoiceDesignCancelled):
        service.run(request, cancel)
    assert not service.gallery.list_voices("audiocpp")


def test_strict_comparison():
    assert normalize_transcript("¡Buenos días! ") == normalize_transcript("buenos días")
    assert normalize_transcript("Sí") != normalize_transcript("si")
    assert normalize_transcript("No abras") != normalize_transcript("abras")


def test_omnivoice_uses_design_mode(setup):
    service, host, verifier, request = setup
    service.run({**request, "engine_id": "omnivoice"}, threading.Event())
    config = host.synthesize_segment.call_args.args[2]
    assert config["mode"] == "design" and config["instruct"] == request["description"]


def test_copy_failure_rolls_back(setup, monkeypatch):
    service, host, verifier, request = setup
    # Fail publication of the second rate after the first file was installed.
    original = Path.replace
    def replace(src, dst):
        if Path(dst).name.endswith(".48k.wav"):
            raise OSError("disk full")
        return original(src, dst)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(VoiceDesignError) as error:
        service.run(request, threading.Event())
    assert error.value.payload["code"] == "SAVE_FAILED"
    assert not service.gallery.list_voices("audiocpp")
    assert not list(service.gallery.files_root.rglob("*.wav"))


def test_job_dispatch_idempotency(setup, tmp_path, monkeypatch):
    from app.server.job_manager import LocalServerJobManager
    service, host, verifier, request = setup
    original_run = VoiceDesignService.run
    monkeypatch.setattr(VoiceDesignService, "run", lambda self, *args: original_run(service, *args))
    manager = LocalServerJobManager(host, db_path=tmp_path / "jobs.db")
    try:
        payload = {**request, "job_kind": "voice_design", "idempotency_key": "one"}
        first = manager.submit(payload)
        second = manager.submit(payload)
        assert first.job_id == second.job_id
        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            manager.submit({**payload, "name": "Other"})
        import time
        deadline = time.monotonic() + 5
        while manager.get_job(first.job_id).status not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
        result = manager.get_job(first.job_id).to_dict()
        assert result["status"] == "complete"
        assert result["result"]["saved"]
        assert host.synthesize_segment.call_count == 1
        verifier.transcribe.return_value = {"text": "wrong"}
        failure = manager.submit({**payload, "name": "Second", "idempotency_key": "two"})
        while manager.get_job(failure.job_id).status not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
        result = manager.get_job(failure.job_id).to_dict()
        assert result["status"] == "failed"
        assert result["result"]["code"] == "TRANSCRIPT_MISMATCH"
    finally:
        manager.shutdown()


def test_http_design_and_voice_lookup(setup, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.settings_manager import SettingsManager
    from app.server.http_app import create_http_app
    from app.server.job_manager import LocalServerJobManager
    service, host, verifier, request = setup
    original_run = VoiceDesignService.run
    monkeypatch.setattr(VoiceDesignService, "run", lambda self, *args: original_run(service, *args))
    manager = LocalServerJobManager(host, db_path=tmp_path / "http-jobs.db")
    settings = SettingsManager(tmp_path / "config.json")
    settings.settings["local_server"] = {"auth_token": "test"}
    app = create_http_app(settings, job_manager=manager)
    app.state.service.voice_gallery_manager = service.gallery
    with TestClient(app) as client:
        assert client.post("/voices/design", json=request).status_code == 401
        headers = {"Authorization": "Bearer test"}
        response = client.post("/voices/design", headers=headers, json=request)
        assert response.status_code == 200
        import time
        job = response.json()
        deadline = time.monotonic() + 5
        while job["status"] not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
            job = client.get("/jobs/" + job["job_id"], headers=headers).json()
        assert job["status"] == "complete"
        voice = client.get("/voices/" + job["result"]["voice_id"], headers=headers)
        assert voice.status_code == 200
        assert voice.json()["metadata"]["verification_status"] == "passed"
        assert client.get("/voices/missing", headers=headers).status_code == 404
        preview_job = client.post("/voices/design/preview", headers=headers, json=request).json()
        while preview_job["status"] not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
            preview_job = client.get("/jobs/" + preview_job["job_id"], headers=headers).json()
        assert preview_job["result"]["status"] == "preview"
        calls = host.synthesize_segment.call_count
        saved = client.post("/voices/design/previews/" + preview_job["job_id"] + "/save",
                            headers=headers, json={"name": "Preview chosen"})
        assert saved.status_code == 200 and saved.json()["saved"]
        assert host.synthesize_segment.call_count == calls


def test_preview_preserves_parameters_and_saves_exact_verified_audio(setup):
    service, host, verifier, request = setup
    preview = service.run({**request, "preview_only": True,
                           "generation_parameters": {"guidance_scale": 7, "seed": 123}}, threading.Event())
    assert preview["status"] == "preview" and not preview["saved"]
    assert not service.gallery.list_voices("audiocpp")
    assert host.synthesize_segment.call_args.args[2]["guidance_scale"] == 7
    saved = service.save_preview(preview, "Chosen name", draft_id="draft1")
    assert saved["name"] == "Chosen name"
    assert Path(saved["voice"]["audio_versions"]["48000"]).read_bytes() == Path(preview["output_wav"]).read_bytes()
    again = service.save_preview(preview, "Chosen name", draft_id="draft1")
    assert again["voice_id"] == saved["voice_id"]
    assert host.synthesize_segment.call_count == verifier.transcribe.call_count == 1


def test_preview_hash_and_explicit_overwrite(setup):
    service, host, verifier, request = setup
    original = service.run(request, threading.Event())
    # A duplicate display name does not prevent previewing a replacement.
    preview = service.run({**request, "preview_only": True}, threading.Event())
    with pytest.raises(VoiceDesignError, match="already exists"):
        service.save_preview(preview, request["name"], draft_id="draft2")
    updated = service.save_preview(preview, request["name"], draft_id="draft2",
                                   replace_voice_id=original["voice_id"])
    assert updated["voice_id"] == original["voice_id"]
    Path(preview["output_wav"]).write_bytes(b"modified")
    with pytest.raises(VoiceDesignError, match="missing or changed"):
        service.save_preview(preview, "Another name", draft_id="draft3")


def test_omnivoice_preview_keeps_original_controls(setup):
    service, host, verifier, request = setup
    preview = service.run({**request, "engine_id": "omnivoice", "language": "Spanish",
        "preview_only": True, "generation_parameters": {"num_step": 42, "duration": 12, "speed": 0.9}}, threading.Event())
    config = host.synthesize_segment.call_args.args[2]
    assert config["num_step"] == 42 and config["duration"] == 12 and config["speed"] == 0.9
    assert preview["status"] == "preview" and not service.gallery.list_voices("omnivoice")


def test_design_keeps_native_48k_master_through_preview_and_save(setup):
    service, host, verifier, request = setup
    original = []
    def synthesize(text, path, config):
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x02\x03\x04" * 48000)
        original.append(path.read_bytes())
    host.synthesize_segment.side_effect = synthesize
    preview = service.run({**request, "preview_only": True}, threading.Event())
    assert Path(preview["output_wav"]).read_bytes() == original[0]
    saved = service.save_preview(preview, "Native high rate")
    voice = saved["voice"]
    assert Path(voice["audio_versions"]["48000"]).read_bytes() == original[0]
    assert voice["metadata"]["verified_audio_sha256"] == hashlib.sha256(original[0]).hexdigest()
    for rate, filename in voice["audio_versions"].items():
        with wave.open(filename) as audio:
            assert audio.getframerate() == int(rate)

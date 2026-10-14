from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.tts.audiocpp_config import DEFAULTS, MODELS, validated_config
from app.tts.audiocpp_engine import AudioCppTTSEngine
from app.tts.audiocpp_manager import AudioCppManager
from app.tts.base import TTSCancelled, TTSEngineError


@pytest.fixture
def download_server():
    content = b"verified-model-bytes" * 2000
    ranges = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            ranges.append(offset)
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(content)-1}/{len(content)}")
            self.send_header("Content-Length", str(len(content) - offset))
            self.end_headers()
            self.wfile.write(content[offset:])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/model", content, ranges
    server.shutdown()
    server.server_close()
    thread.join()


def test_download_resumes_checks_hash_and_publishes_atomically(tmp_path, download_server):
    url, content, ranges = download_server
    target = tmp_path / "weights.gguf"
    target.with_suffix(".gguf.part").write_bytes(content[:700])
    manager = AudioCppManager(tmp_path)
    manager.download(url, target, len(content), hashlib.sha256(content).hexdigest(), lambda *a: None)
    assert ranges == [700]
    assert target.read_bytes() == content
    assert not target.with_suffix(".gguf.part").exists()
    # A second install verifies and reuses bytes, with no network request.
    manager.download(url, target, len(content), hashlib.sha256(content).hexdigest(), lambda *a: None)
    assert ranges == [700]


def test_corrupt_download_never_replaces_existing_model(tmp_path, download_server):
    url, content, _ = download_server
    target = tmp_path / "model.gguf"
    target.write_bytes(b"previous-good-model")
    with pytest.raises(TTSEngineError, match="SHA-256"):
        AudioCppManager(tmp_path).download(url, target, len(content), "0" * 64, lambda *a: None)
    assert target.read_bytes() == b"previous-good-model"


def test_download_cancel_keeps_resume_file(tmp_path, download_server):
    url, content, _ = download_server
    manager = AudioCppManager(tmp_path)
    target = tmp_path / "model.gguf"
    def cancel(*args):
        manager.cancel()
    with pytest.raises(TTSCancelled):
        manager.download(url, target, len(content), hashlib.sha256(content).hexdigest(), cancel)
    assert not target.exists()
    assert target.with_suffix(".gguf.part").exists()


def test_runtime_zip_rejects_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "bad")
    with pytest.raises(TTSEngineError, match="Unsafe"):
        AudioCppManager(tmp_path)._extract(archive, tmp_path / "runtime")
    assert not (tmp_path / "escape.txt").exists()


@pytest.fixture
def native_engine(tmp_path, monkeypatch):
    """Real child process + HTTP transport, without multi-GB model downloads."""
    script = tmp_path / "native_fixture.py"
    script.write_text('''import io, json, sys, time, wave
from http.server import BaseHTTPRequestHandler, HTTPServer
config = json.load(open(sys.argv[1], encoding="utf-8"))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(sys.argv[2], "a", encoding="utf-8") as log: log.write(json.dumps(data) + "\\n")
        if data["input"] == "block": time.sleep(20)
        if (data["input"] == "eoc-always" or
            (data["input"] == "eoc-recover" and data["options"]["max_tokens"] < 4096)):
            self.send_response(500); self.end_headers()
            self.wfile.write(b'Higgs TTS generation reached max_tokens (1024) before EOC for this text chunk')
            return
        if data["input"] == "other-error":
            self.send_response(500); self.end_headers(); self.wfile.write(b'GPU out of memory')
            return
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b"\\0\\0"*240)
        self.send_response(200); self.send_header("Content-Type","audio/wav"); self.end_headers(); self.wfile.write(out.getvalue())
HTTPServer((config["host"], config["port"]), Handler).serve_forever()
''', encoding="utf-8")
    log = tmp_path / "requests.jsonl"
    manager = SimpleNamespace(is_installed=lambda *a: True, executable=lambda *a: script,
                              runtime_dir=lambda *a: tmp_path, model_path=lambda model: tmp_path / (model + ".gguf"))
    real_popen = subprocess.Popen
    def launch(args, **kwargs):
        return real_popen([sys.executable, str(script), args[2], str(log)], **kwargs)
    monkeypatch.setattr("app.tts.audiocpp_engine.subprocess.Popen", launch)
    engine = AudioCppTTSEngine(manager)
    config = {**DEFAULTS, "model": "fish_audio_s2_pro_q8_0", "timeout_seconds": 10}
    yield engine, config, log
    engine.close()


def test_native_process_reused_and_model_switch_releases_previous(native_engine, tmp_path):
    engine, config, log = native_engine
    engine.preload(config)
    first = engine._process
    engine.synthesize_to_wav("Hola [happy].", tmp_path / "first.wav", config)
    engine.synthesize_to_wav("Segundo fragmento.", tmp_path / "second.wav", config)
    assert engine._process is first
    assert len(log.read_text().splitlines()) == 2
    payload = json.loads(log.read_text().splitlines()[0])
    assert payload["input"] == "Hola [happy]."
    assert "speed" not in payload and "language" not in payload
    engine.preload({**config, "model": "higgs_audio_tts_4b_q8_0"})
    assert first.poll() is not None
    assert engine._process is not first
    assert engine.memory_status()["model"] == "higgs_audio_tts_4b_q8_0"
    engine.close()
    assert engine.memory_status()["loaded"] is False


def test_cancellation_terminates_native_inference(native_engine, tmp_path):
    engine, config, log = native_engine
    engine.preload(config)
    process = engine._process
    errors = []
    def generate():
        try:
            engine.synthesize_to_wav("block", tmp_path / "cancel.wav", config)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=generate)
    thread.start()
    deadline = time.monotonic() + 5
    while not log.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    engine.cancel_current()
    thread.join(5)
    assert not thread.is_alive()
    assert process.poll() is not None
    assert isinstance(errors[0], TTSCancelled)
    assert not (tmp_path / "cancel.wav").exists()


def test_reference_validation_and_transport(native_engine, tmp_path):
    engine, config, log = native_engine
    config = {**config, "model": "higgs_audio_tts_4b_q8_0"}
    with pytest.raises(TTSEngineError, match="requires a reference"):
        engine.validate(config)
    ref = tmp_path / "referencia ñ.wav"
    with wave.open(str(ref), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b"\0\0"*240)
    config.update(reference_audio_path=str(ref), reference_text="Una referencia.")
    text = "<|emotion:elation|><|style:whispering|>Hola <|prosody:pause|> <|sfx:laughter|>Haha."
    engine.synthesize_to_wav(text, tmp_path / "out.wav", config)
    payload = json.loads(log.read_text(encoding="utf-8"))
    assert payload["input"] == text
    assert payload["voice_ref"] == str(ref.resolve())
    assert payload["reference_text"] == "Una referencia."


def higgs_retry_config(config, tmp_path):
    reference = tmp_path / "reference.wav"
    with wave.open(str(reference), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 240)
    return {**config, "model": "higgs_audio_tts_4b_q8_0", "max_tokens": 1024,
            "seed": 42, "reference_audio_path": str(reference), "reference_text": "Referencia."}


def test_higgs_eoc_retries_reuse_process_without_mutating_config(native_engine, tmp_path):
    engine, config, log = native_engine
    config = higgs_retry_config(config, tmp_path)
    original = dict(config)
    engine.preload(config)
    process = engine._process
    engine.synthesize_to_wav("eoc-recover", tmp_path / "out.wav", config)
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["options"]["max_tokens"] for r in requests] == [1024, 2048, 4096]
    assert [r["options"]["text_chunk_size"] for r in requests] == [200, 100, 50]
    assert [r["options"]["seed"] for r in requests] == [42, 43, 44]
    assert all(r["input"] == "eoc-recover" and r["reference_text"] == "Referencia." for r in requests)
    assert engine._process is process and config == original
    with wave.open(str(tmp_path / "out.wav")) as audio:
        assert audio.getnframes() > 0


@pytest.mark.parametrize("text,attempts", [("eoc-always", 3), ("other-error", 1)])
def test_higgs_failures_are_bounded_and_do_not_publish_audio(native_engine, tmp_path, text, attempts):
    engine, config, log = native_engine
    config = higgs_retry_config(config, tmp_path)
    with pytest.raises(TTSEngineError):
        engine.synthesize_to_wav(text, tmp_path / "out.wav", config)
    assert len(log.read_text().splitlines()) == attempts
    assert not (tmp_path / "out.wav").exists()
    assert not engine.memory_status()["loaded"]


def test_higgs_retry_can_be_cancelled_before_next_request(native_engine, tmp_path):
    engine, config, log = native_engine
    config = higgs_retry_config(config, tmp_path)
    engine.set_log_callback(lambda message: engine.cancel_current() if "reintento" in message else None)
    with pytest.raises(TTSCancelled):
        engine.synthesize_to_wav("eoc-always", tmp_path / "out.wav", config)
    assert len(log.read_text().splitlines()) == 1
    assert not (tmp_path / "out.wav").exists()


def test_fish_does_not_use_higgs_recovery(native_engine, tmp_path):
    engine, config, log = native_engine
    with pytest.raises(TTSEngineError):
        engine.synthesize_to_wav("eoc-always", tmp_path / "out.wav", config)
    assert len(log.read_text().splitlines()) == 1


def test_catalog_registry_settings_service_and_markup(tmp_path):
    from app.core.settings_manager import SettingsManager
    from app.tts.engine_registry import create_tts_engine
    from app.server.ltv_service import LocalText2VoiceService
    from app.core.audio_pipeline import AudioPipeline
    from app.core.text_processor import TextChunk
    settings = SettingsManager(tmp_path / "config.json")
    settings.settings["audiocpp"]["voices"] = {"Ana": {"reference_audio_path": "ana.wav", "reference_text": "Hola."}}
    settings.save()
    service = LocalText2VoiceService(SettingsManager(tmp_path / "config.json"), keep_engines_alive=True)
    engine = create_tts_engine("audiocpp", tmp_path / "piper")
    service._engine_cache["audiocpp"] = engine
    assert service.engine_status()["audiocpp"]["loaded"] is False
    row = next(row for row in service.list_engines() if row["id"] == "audiocpp")
    assert {m["id"] for m in row["models"]} == set(MODELS)
    config = service._voice_config("audiocpp", {"model": "fish_audio_s2_pro_q8_0", "voice": "Ana", "speed": 1.2})
    assert config["reference_audio_path"] == "ana.wav"
    assert config["model"] == "fish_audio_s2_pro_q8_0"
    pipeline = AudioPipeline(Mock())
    chunk = TextChunk(text="Hello", ends_paragraph=True, markup_state={"voice": "Ana", "speed": 0.8})
    result = pipeline._voice_config_for_chunk(config, chunk)
    assert result["_postprocess_speed"] == 0.8
    assert config["speed"] == 1.2
    assert pipeline._voice_config_for_chunk(config, TextChunk(text="Hola", ends_paragraph=True))["_postprocess_speed"] == 1.2
    service.close()


@pytest.mark.parametrize("field,value", [("model", "unknown"), ("temperature", float("nan")), ("top_k", 1.5)])
def test_invalid_parameters_rejected(field, value):
    with pytest.raises(ValueError):
        validated_config({field: value})


def test_runtime_manifest_detects_missing_dll(tmp_path):
    manager = AudioCppManager(tmp_path)
    root = manager.runtime_dir("cpu")
    root.mkdir(parents=True)
    exe = root / ("audiocpp_server.exe" if sys.platform == "win32" else "audiocpp_server")
    exe.write_bytes(b"executable")
    dll = root / "ggml.dll"
    dll.write_bytes(b"dependency")
    (root / "installed.json").write_text(json.dumps({"files": {exe.name: 10, dll.name: 10}}))
    assert manager.has_runtime("cpu")
    dll.unlink()
    assert not manager.has_runtime("cpu")


def test_install_cancelled_before_worker_starts_does_not_download(tmp_path, monkeypatch):
    manager = AudioCppManager(tmp_path)
    download = Mock()
    monkeypatch.setattr(manager, "download", download)
    manager.cancel()
    with pytest.raises(TTSCancelled):
        manager.install(DEFAULTS["model"], "cpu", lambda *a: None)
    download.assert_not_called()


def test_install_publishes_complete_runtime_model_and_license(tmp_path, monkeypatch):
    import app.tts.audiocpp_manager as module
    manager = AudioCppManager(tmp_path)
    spec = replace(MODELS[DEFAULTS["model"]], size=5)
    monkeypatch.setitem(MODELS, spec.id, spec)
    asset = {"name": "windows-x64-cpu-portable.zip", "browser_download_url": "runtime",
             "size": 1, "digest": "sha256:unused"}
    monkeypatch.setattr(module, "ASSETS", [asset])
    monkeypatch.setattr(module, "runtime_key", lambda backend: "windows-x64-cpu-portable")
    exe = "audiocpp_server.exe" if sys.platform == "win32" else "audiocpp_server"
    runtime_downloads = []
    def download(url, destination, *args):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url == "runtime":
            runtime_downloads.append(url)
            with zipfile.ZipFile(destination, "w") as bundle:
                bundle.writestr("bin/" + exe, b"server")
                bundle.writestr("bin/ggml.dll", b"dependency")
        else:
            destination.write_bytes(b"model")
    monkeypatch.setattr(manager, "download", download)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"Upstream model terms"))
    manager.install(spec.id, "cpu", lambda *a: None)
    assert manager.is_installed(spec.id, "cpu")
    assert manager.executable("cpu").parent.name == "bin"
    path = manager.model_path(spec.id)
    assert (path.parent / "LICENSE.txt").read_text() == "Upstream model terms"
    assert "audio-cpp/audio.cpp-gguf" in (path.parent / "NOTICE.txt").read_text()
    other = replace(MODELS["fish_audio_s2_pro_q8_0"], size=5)
    monkeypatch.setitem(MODELS, other.id, other)
    manager.install(other.id, "cpu", lambda *a: None)
    assert manager.is_installed(other.id, "cpu")
    third = replace(MODELS["breeze_tts_2_q8_0"], size=5)
    monkeypatch.setitem(MODELS, third.id, third)
    manager.install(third.id, "cpu", lambda *a: None)
    assert manager.is_installed(third.id, "cpu")
    manager.remove_model(third.id)
    assert not manager.is_installed(third.id, "cpu")
    assert manager.is_installed(other.id, "cpu") and manager.is_installed(spec.id, "cpu")
    fourth = replace(MODELS["voxcpm2_q8_0"], size=5)
    monkeypatch.setitem(MODELS, fourth.id, fourth)
    manager.install(fourth.id, "cpu", lambda *a: None)
    assert manager.is_installed(fourth.id, "cpu")
    manager.remove_model(fourth.id)
    assert manager.is_installed(other.id, "cpu")
    for variant in ("voxcpm2_bf16", "voxcpm2_orig"):
        monkeypatch.setitem(MODELS, variant, replace(MODELS[variant], size=5))
        manager.install(variant, "cpu", lambda *a: None)
        assert manager.is_installed(variant, "cpu")
    manager.remove_model("voxcpm2_bf16")
    assert manager.is_installed("voxcpm2_orig", "cpu")
    assert len(runtime_downloads) == 1
    # A missing runtime dependency is repaired before installation is reported ready.
    (manager.executable("cpu").parent / "ggml.dll").unlink()
    assert not manager.is_installed(spec.id, "cpu")
    manager.install(spec.id, "cpu", lambda *a: None)
    assert manager.is_installed(spec.id, "cpu")
    manager.remove_model(spec.id)
    assert not manager.is_installed(spec.id, "cpu")
    assert manager.has_runtime("cpu")
    assert manager.is_installed(other.id, "cpu")


def test_breeze_instruction_and_vocal_transport(native_engine, tmp_path):
    from app.tts.audiocpp_config import BREEZE_MODEL, model_config
    engine, _, log = native_engine
    config = model_config({"model": BREEZE_MODEL, "instruction": "Speak softly and sadly.", "seed": 17})
    engine.synthesize_to_wav("Hello (clears throat).", tmp_path / "design.wav", config)
    payload = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["model"] == BREEZE_MODEL
    assert payload["input"] == "Hello (clears throat)."
    assert payload["options"]["instruction"] == "Speak softly and sadly."
    assert payload["options"]["depth_temperature"] == .9
    assert payload["options"]["guidance_scale"] == 1
    assert payload["options"]["max_tokens"] == 1500
    assert payload["options"]["text_chunk_size"] == 600
    assert payload["options"]["seed"] == 17
    assert "voice_ref" not in payload
    reference = tmp_path / "reference.wav"
    with wave.open(str(reference), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 240)
    config["reference_audio_path"] = str(reference)
    with pytest.raises(TTSEngineError, match="exact transcript"):
        engine.validate(config)
    config.update(reference_text="Reference transcript.", seed=-1)
    engine.synthesize_to_wav("Hello (sigh).", tmp_path / "clone.wav", config)
    payload = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["voice_ref"] == str(reference.resolve())
    assert payload["reference_text"] == "Reference transcript."
    assert 0 <= payload["options"]["seed"] <= 2147483647
    assert config["seed"] == -1


def test_breeze_model_settings_are_isolated():
    from app.tts.audiocpp_config import BREEZE_MODEL, model_config
    source = {"model": DEFAULTS["model"], "text_chunk_size": 275, "max_tokens": 4096,
              "instruction": "Unused Higgs instruction", "backend": "cuda",
              "reference_text": "Shared voice", "model_settings": {BREEZE_MODEL: {"guidance_scale": 1.5}}}
    breeze = model_config(source, BREEZE_MODEL)
    assert breeze["backend"] == "cuda" and breeze["reference_text"] == "Shared voice"
    assert (breeze["text_chunk_size"], breeze["max_tokens"], breeze["top_k"]) == (600, 1500, 50)
    assert breeze["instruction"] == "Speak clearly and naturally."
    assert breeze["guidance_scale"] == 1.5
    assert source["text_chunk_size"] == 275
    assert validated_config({"model": BREEZE_MODEL, "top_k": 0})["top_k"] == 0
    for invalid in ({"guidance_scale": -1}, {"depth_temperature": float("nan")}, {"instruction": 5}):
        with pytest.raises(ValueError):
            validated_config({"model": BREEZE_MODEL, **invalid})


@pytest.mark.parametrize("model_id", ["voxcpm2_q8_0", "voxcpm2_bf16", "voxcpm2_orig"])
def test_voxcpm_every_chunk_is_directed_and_only_native_options_sent(native_engine, tmp_path, model_id):
    from app.tts.audiocpp_config import VOXCPM_MODEL, model_config
    from app.core.text_processor import TextProcessor
    engine, _, log = native_engine
    config = model_config({"model": model_id, "instruction": "voz alegre", "text_chunk_size": 30, "seed": 42})
    source = "Hola a todos. Esta historia tiene varias frases. Vamos a empezar."
    out = tmp_path / "voxcpm.wav"
    engine.synthesize_to_wav(source, out, config)
    payloads = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    chunks = TextProcessor.split_safe_chunks(source, 30)
    assert len(payloads) == len(chunks) > 1
    for request, chunk in zip(payloads, chunks):
        assert request["model"] == model_id
        assert request["input"] == "(voz alegre) " + chunk
        assert set(request["options"]) == {"max_tokens", "guidance_scale", "num_inference_steps", "seed", "chunk_strategy", "text_chunk_mode", "text_chunk_size"}
        assert request["options"]["text_chunk_size"] > len(request["input"])
        assert request["options"]["guidance_scale"] == 2
        assert request["options"]["num_inference_steps"] == 10
        assert request["options"]["max_tokens"] == 4096
        assert request["options"]["seed"] == 42
        assert "voice_ref" not in request
    with wave.open(str(out), "rb") as wav:
        assert wav.getnframes() == len(chunks) * 240
    first_process = engine._process
    # Speaker-reference cloning works without a transcript; never send the
    # unsupported continuation field on the native speech endpoint.
    config.update(instruction="", reference_audio_path=str(out), reference_text="Catalog transcript", seed=-1)
    engine.synthesize_to_wav("Texto (un inciso normal).", tmp_path / "off.wav", config)
    request = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert request["input"] == "Texto (un inciso normal)."
    assert request["voice_ref"] == str(out.resolve())
    assert "reference_text" not in request and "prompt_text" not in request["options"]
    assert 0 <= request["options"]["seed"] <= 2147483647
    assert engine._process is first_process
    assert config["seed"] == -1


def test_voxcpm_failed_later_chunk_keeps_previous_audio(native_engine, tmp_path):
    engine, _, _ = native_engine
    destination = tmp_path / "old.wav"
    destination.write_bytes(b"previous audio")
    with pytest.raises(TTSEngineError, match="GPU out of memory"):
        engine.synthesize_to_wav("First sentence here. other-error", destination,
                                 {"model": "voxcpm2_q8_0", "text_chunk_size": 20})
    assert destination.read_bytes() == b"previous audio"
    assert not list(tmp_path.glob("*.pending.wav"))
    assert not engine.memory_status()["loaded"]


@pytest.mark.parametrize("values", [
    {"instruction": "happy (very)"}, {"instruction": "{{emotion happy}}"},
    {"instruction": None}, {"num_inference_steps": 0}, {"num_inference_steps": 1.5},
    {"guidance_scale": float("nan")}, {"guidance_scale": -1},
])
def test_voxcpm_invalid_controls(values):
    with pytest.raises((ValueError, TypeError)):
        validated_config({"model": "voxcpm2_q8_0", **values})

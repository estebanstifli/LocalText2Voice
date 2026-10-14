from __future__ import annotations

from collections import deque
import json
import os
import secrets
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import wave

from app.utils.paths import resolve_large_asset_path
from .audiocpp_config import MODELS, BREEZE_MODEL, BREEZE_FIELDS, VOXCPM_MODELS, validated_config
from .audiocpp_manager import AudioCppManager
from .api_engines import CustomHTTPTTSEngine
from .base import TTSCancelled, TTSEngineError


class AudioCppTTSEngine(CustomHTTPTTSEngine):
    """One owned native process/model per host adapter, reused between segments."""

    def __init__(self, manager=None):
        super().__init__()
        self.manager = manager or AudioCppManager()
        self._process = None
        self._identity = None
        self._base_url = ""
        self._temporary = None
        self._run_lock = threading.RLock()
        self._logs = deque(maxlen=30)
        self.log_callback = lambda message: None
        self._ready = False

    def set_log_callback(self, callback):
        self.log_callback = callback

    def memory_status(self):
        with self._lock:
            loaded = self._ready and self._process is not None and self._process.poll() is None
            return {"loaded": loaded, "model": self._identity[0] if loaded else None,
                    "backend": self._identity[1] if loaded else None}

    def _config(self, value):
        try:
            config = validated_config(value)
        except (ValueError, TypeError) as exc:
            raise TTSEngineError(str(exc)) from exc
        if not self.manager.is_installed(config["model"], config["backend"]):
            raise TTSEngineError("Install the selected audio.cpp runtime and model in Settings > TTS Engines.")
        return config

    def validate(self, voice_config):
        config = self._config(voice_config)
        reference = str(config.get("reference_audio_path", "")).strip()
        if MODELS[config["model"]].requires_reference and not reference:
            raise TTSEngineError(f"{MODELS[config['model']].name} requires a reference WAV. Choose one in the audio.cpp settings.")
        if reference:
            if config["model"] == BREEZE_MODEL and not str(config.get("reference_text", "")).strip():
                raise TTSEngineError("Breeze voice cloning requires the exact transcript of the reference WAV.")
            path = resolve_large_asset_path(reference)
            try:
                with wave.open(str(path), "rb") as audio:
                    if not audio.getnframes() or audio.getcomptype() != "NONE":
                        raise ValueError("Empty or compressed WAV")
            except (OSError, EOFError, ValueError, wave.Error) as exc:
                raise TTSEngineError("Choose a valid, non-empty PCM WAV as the reference audio.") from exc

    def preload(self, voice_config):
        with self._run_lock:
            self._ensure_server(self._config(voice_config))

    def _ensure_server(self, config):
        self._check_cancel()
        identity = (config["model"], config["backend"], config["device"])
        if identity == self._identity and self.memory_status()["loaded"]:
            return
        self.close()
        self._check_cancel()
        temporary = tempfile.TemporaryDirectory(prefix="ltv-audiocpp-")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        spec = MODELS[config["model"]]
        server_config = {
            "host": "127.0.0.1", "port": port, "backend": config["backend"],
            "device": config["device"], "threads": 1, "lazy_load": False,
            "max_loaded_models": 1, "log_request_body": False,
            "models": [{"id": spec.id, "family": spec.family,
                        "path": str(self.manager.model_path(spec.id).resolve()),
                        "task": "tts", "mode": "offline"}],
        }
        config_path = Path(temporary.name) / "server.json"
        config_path.write_text(json.dumps(server_config), encoding="utf-8")
        executable = self.manager.executable(config["backend"])
        env = os.environ.copy()
        dll_dirs = sorted({str(p.parent) for p in self.manager.runtime_dir(config["backend"]).rglob("*.dll")})
        env["PATH"] = os.pathsep.join([str(executable.parent), *dll_dirs, env.get("PATH", "")])
        try:
            with self._lock:
                self._check_cancel()
                self._process = subprocess.Popen(
                    [str(executable), "--config", str(config_path), "--no-ui"],
                    cwd=executable.parent, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                process = self._process
                self._temporary = temporary
                self._identity = identity
                self._base_url = f"http://127.0.0.1:{port}"
            self._logs.clear()
            threading.Thread(target=self._drain, args=(process,), daemon=True).start()
            deadline = time.monotonic() + config["timeout_seconds"]
            while time.monotonic() < deadline:
                self._check_cancel()
                if process.poll() is not None:
                    raise TTSEngineError("audio.cpp could not load the model. Check backend/driver and free memory.\n" + "\n".join(self._logs))
                try:
                    with urllib.request.urlopen(self._base_url + "/health", timeout=0.5) as response:
                        if response.status == 200:
                            with self._lock:
                                self._check_cancel()
                                self._ready = True
                            self.log_callback(f"audio.cpp loaded {spec.name} ({config['backend']}).")
                            return
                except (OSError, ValueError):
                    pass
                self._cancel_requested.wait(0.15)
            raise TTSEngineError("Timed out loading audio.cpp; check available memory and backend.")
        except Exception:
            self.close()
            temporary.cleanup()
            raise

    def _drain(self, process):
        try:
            for line in process.stdout:
                self._logs.append(line.rstrip())
        finally:
            process.stdout.close()

    def _check_cancel(self):
        if self._cancel_requested.is_set():
            raise TTSCancelled("audio.cpp generation cancelled.")

    def _request_speech(self, payload, config):
        # Retry only the native Higgs EOC failure, never generic HTTP/timeouts.
        # Keep caller settings and text intact, including native control tags.
        payload = {**payload, "options": dict(payload["options"])}
        for attempt in range(3):
            self._check_cancel()
            try:
                return self._request(
                    "POST", self._base_url + "/v1/audio/speech",
                    json.dumps(payload).encode("utf-8"),
                    {"Content-Type": "application/json"}, config["timeout_seconds"],
                )
            except TTSEngineError as exc:
                self._check_cancel()
                message = str(exc)
                is_eoc = (MODELS[config["model"]].family == "higgs_audio_tts"
                          and "HTTP 500:" in message
                          and "Higgs TTS generation reached max_tokens" in message
                          and "before EOC" in message)
                if not is_eoc:
                    raise
                if attempt == 2:
                    raise TTSEngineError(
                        "Higgs no ha marcado el final del audio tras 3 intentos. "
                        "No se ha guardado audio incompleto. Revisa el texto y la referencia "
                        "WAV con su transcripción, o divide el fragmento. " + message
                    ) from exc
                options = payload["options"]
                options["max_tokens"] = max(options["max_tokens"], min(4096, options["max_tokens"] * 2))
                options["text_chunk_size"] = max(20, options["text_chunk_size"] // 2)
                # A fixed seed must not reproduce the same stalled short utterance.
                if config["seed"] >= 0:
                    options["seed"] = (config["seed"] + attempt + 1) % 2147483648
                self.log_callback(
                    f"Higgs alcanzó el límite sin terminar; reintento {attempt + 1}/2 "
                    f"con max_tokens={options['max_tokens']}, "
                    f"text_chunk_size={options['text_chunk_size']}"
                    + (f", seed={options['seed']}" if "seed" in options else "") + "."
                )

    def synthesize_to_wav(self, text, output_wav, voice_config):
        with self._run_lock:
            self.validate(voice_config)
            config = self._config(voice_config)
            self._ensure_server(config)
            payload = {
                "model": config["model"], "input": text, "response_format": "wav",
                "options": {key: config[key] for key in ("temperature", "top_p", "top_k", "max_tokens", "text_chunk_size")},
            }
            if config["seed"] >= 0:
                payload["options"]["seed"] = config["seed"]
            if config["model"] == BREEZE_MODEL:
                payload["options"].update({key: config[key] for key in BREEZE_FIELDS})
                # Omitting the seed uses upstream's fixed seed 0, not random sampling.
                if config["seed"] < 0:
                    payload["options"]["seed"] = secrets.randbelow(2147483648)
            if config["reference_audio_path"]:
                payload["voice_ref"] = str(resolve_large_asset_path(config["reference_audio_path"]).resolve())
                if config["model"] not in VOXCPM_MODELS:
                    payload["reference_text"] = config["reference_text"]
            try:
                if config["model"] in VOXCPM_MODELS:
                    return self._synthesize_voxcpm(text, output_wav, payload, config)
                audio, _ = self._request_speech(payload, config)
                self._check_cancel()
                output_wav.parent.mkdir(parents=True, exist_ok=True)
                result = self._write_wav_bytes_compatible(audio, output_wav, config)
                with wave.open(str(result), "rb") as wav:
                    if wav.getnframes() == 0:
                        raise TTSEngineError("audio.cpp returned an empty WAV.")
                self._check_cancel()
                return result
            except Exception:
                # A timed-out request may still be executing in C++; release it.
                self.close()
                self._check_cancel()
                raise

    def _synthesize_voxcpm(self, text, output_wav, payload, config):
        from app.core.text_processor import TextProcessor
        from app.utils.atomic_audio import atomic_wav_output, validate_wav

        chunks = TextProcessor.split_safe_chunks(text, config["text_chunk_size"])
        if not chunks:
            raise TTSEngineError("VoxCPM 2 requires non-empty text.")
        instruction = config["instruction"]
        seed = config["seed"] if config["seed"] >= 0 else secrets.randbelow(2147483648)
        # Native tag-aware continuation only prefixes its FIRST internal chunk.
        # Submit independent chunks with the same reference and direction instead.
        # Keep a fixed seed across chunks to help reference-free voice consistency.
        options = {key: config[key] for key in ("max_tokens", "guidance_scale", "num_inference_steps")}
        options.update(seed=seed, chunk_strategy="stateless", text_chunk_mode="default")
        with atomic_wav_output(output_wav, self._check_cancel) as destination:
            with tempfile.TemporaryDirectory(prefix="ltv-voxcpm-chunks-") as temporary:
                part = Path(temporary) / "part.wav"
                expected = None
                with wave.open(str(destination), "wb") as combined:
                    # Allow clean closure if the first request fails or is cancelled.
                    combined.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                    for chunk in chunks:
                        self._check_cancel()
                        directed = f"({instruction}) {chunk}" if instruction else chunk
                        # Size covers the ENTIRE request, so C++ cannot split again.
                        request = {**payload, "input": directed,
                                   "options": {**options, "text_chunk_size": len(directed) + 1}}
                        audio, _ = self._request_speech(request, config)
                        self._check_cancel()
                        self._write_wav_bytes_compatible(audio, part, config)
                        validate_wav(part)
                        with wave.open(str(part), "rb") as wav:
                            fmt = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
                            if expected is None:
                                expected = fmt
                                combined.setparams(wav.getparams())
                            elif fmt != expected:
                                raise TTSEngineError("VoxCPM 2 changed WAV format between chunks.")
                            while frames := wav.readframes(65536):
                                self._check_cancel()
                                combined.writeframesraw(frames)
        return output_wav

    def cancel_current(self):
        super().cancel_current()
        self.close()

    def close(self):
        with self._lock:
            process, temporary = self._process, self._temporary
            self._process = self._temporary = self._identity = None
            self._ready = False
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if temporary is not None:
            temporary.cleanup()

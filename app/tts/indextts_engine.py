from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.utils.paths import resolve_large_asset_path

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError
from .indextts_config import inference_arguments, validated_config
from .indextts_manager import IndexTTSManager


class IndexTTSTTSEngine(BaseTTSEngine):
    def __init__(self, manager: IndexTTSManager | None = None) -> None:
        self.manager = manager or IndexTTSManager()
        self._process: subprocess.Popen[str] | None = None
        self._emotion_process: subprocess.Popen[str] | None = None
        self._emotion_cache: dict[tuple[str, str], list[float]] = {}
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._worker_config: tuple[str, str, str, str, str] | None = None
        self._request_index = 0
        self._lock = threading.RLock()
        self._cancel_requested = threading.Event()
        self.log_callback: Callable[[str], None] = lambda message: None

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        self.log_callback = callback

    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self.manager.is_installed():
            raise TTSEngineError(
                "IndexTTS-2.5 is not installed. Open Settings > TTS Engines and install it."
            )
        try:
            config = validated_config(voice_config)
        except (TypeError, ValueError) as exc:
            raise TTSEngineError(str(exc)) from exc
        for key in ("reference_audio_path", "emo_audio_prompt"):
            if key == "emo_audio_prompt" and config["emotion_mode"] != "audio":
                continue
            if not resolve_large_asset_path(str(config[key])).is_file():
                raise TTSEngineError(f"IndexTTS requires a valid audio file: {key}.")

    def synthesize_to_wav(
        self, text: str, output_wav: Path, voice_config: dict[str, Any]
    ) -> Path:
        self.validate(voice_config)
        config = self.prepare_emotions([voice_config], [text])[0]
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")
        for key in ("reference_audio_path", "emo_audio_prompt"):
            if config[key]:
                config[key] = str(resolve_large_asset_path(str(config[key])).resolve())
        output_wav = output_wav.resolve()
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.unlink(missing_ok=True)
        process = self._ensure_worker(config)
        request_id = self._next_request_id()
        self._send_request(
            process,
            {
                "type": "synthesize",
                "id": request_id,
                "arguments": inference_arguments(config, text, str(output_wav)),
            },
        )
        self._wait_for_response(process, request_id)
        import wave

        try:
            with wave.open(str(output_wav), "rb") as audio:
                if audio.getnframes() == 0:
                    raise ValueError("Empty WAV")
        except (OSError, EOFError, ValueError, wave.Error) as exc:
            raise TTSEngineError(
                "IndexTTS did not produce a valid, non-empty PCM WAV."
            ) from exc
        return output_wav

    def preload(self, voice_config: dict[str, Any]) -> None:
        # Loading weights needs no speaker sample or per-fragment instruction.
        # Those inputs are validated when synthesis actually starts.
        if not self.manager.is_installed():
            raise TTSEngineError(
                "IndexTTS-2.5 is not installed. Open Settings > TTS Engines and install it."
            )
        try:
            self._ensure_worker(voice_config)
        except (TypeError, ValueError) as exc:
            raise TTSEngineError(str(exc)) from exc

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            emotion_process = self._emotion_process
        if emotion_process is not None:
            self._terminate(emotion_process)
        self._close_worker(force=True)

    def close(self) -> None:
        with self._lock:
            emotion_process = self._emotion_process
        if emotion_process is not None:
            self._terminate(emotion_process)
        self._close_worker(force=self._cancel_requested.is_set())

    def prepare_emotions(self, configs: list[dict], texts: list[str]) -> list[dict]:
        """Resolve all free-form prompts once, before any IndexTTS synthesis.

        Returned vectors and provenance can be saved in each segment's state.
        Already-resolved vectors are reused directly during review/regeneration.
        """
        if len(configs) != len(texts):
            raise ValueError("Emotion preparation needs one config per segment.")
        normalized = [validated_config(config) for config in configs]
        revision = getattr(self.manager, "MODEL_REVISION", "unknown")
        prompts = []
        for config, text in zip(normalized, texts):
            mode = config["emotion_mode"]
            if mode == "auto" or (
                config.get("emotion_source") == "auto"
                and config.get("emotion_prompt") != text
            ):
                config["emotion_source"] = "auto"
                prompts.append(text)
            elif mode == "text":
                config.setdefault("emotion_source", "text")
                prompts.append(str(config["emo_text"]).strip())
            elif (
                config.get("emotion_prompt")
                and config.get("emotion_revision") != revision
            ):
                prompts.append(str(config["emotion_prompt"]))
            else:
                prompts.append(None)
        missing = list(
            dict.fromkeys(
                prompt
                for prompt in prompts
                if prompt is not None and (revision, prompt) not in self._emotion_cache
            )
        )
        if missing:
            vectors = self._calculate_emotions(missing, normalized[0]["device"])
            for prompt, vector in zip(missing, vectors):
                checked = validated_config({"emo_vector": vector})["emo_vector"]
                self._emotion_cache[(revision, prompt)] = checked
        for config, prompt in zip(normalized, prompts):
            if prompt is not None:
                config.pop("instruct", None)
                config.pop("instructions", None)
                config.update(
                    emotion_mode="vector",
                    emo_text="",
                    emo_audio_prompt="",
                    emo_vector=list(self._emotion_cache[(revision, prompt)]),
                    emotion_prompt=prompt,
                    emotion_revision=revision,
                )
        return normalized

    def remember_emotions(self, configs: list[dict]) -> None:
        """Reuse vectors saved in this project's segments, only for this revision."""
        revision = getattr(self.manager, "MODEL_REVISION", "unknown")
        for config in configs:
            if not isinstance(config, dict):
                continue
            prompt = config.get("emotion_prompt")
            if not prompt or config.get("emotion_revision") != revision:
                continue
            try:
                vector = validated_config({"emo_vector": config["emo_vector"]})[
                    "emo_vector"
                ]
            except (KeyError, TypeError, ValueError):
                continue
            self._emotion_cache[(revision, str(prompt))] = vector

    def _calculate_emotions(self, prompts: list[str], device: str) -> list[list[float]]:
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")
        self.log_callback(
            f"Preparing {len(prompts)} unique emotion instruction(s) with QwenEmotion."
        )
        # Never overlap the heavy TTS weights with the emotional language model.
        self._close_worker(force=False)
        command = [
            *self.manager.runtime_command(),
            "--emotion-batch",
            "--model-dir",
            str(self.manager.model_dir),
            "--cache-dir",
            str(self.manager.cache_dir),
            "--device",
            device,
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.manager.runtime_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )
        with self._lock:
            self._emotion_process = process
        payload = json.dumps({"prompts": prompts}, ensure_ascii=False) + "\n"
        try:
            while True:
                if self._cancel_requested.is_set():
                    raise TTSCancelled("Generation cancelled.")
                try:
                    stdout, stderr = process.communicate(input=payload, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if self._cancel_requested.is_set():
                raise TTSCancelled("Generation cancelled.")
            messages = []
            for line in stdout.splitlines():
                try:
                    messages.append(json.loads(line))
                except ValueError:
                    continue
            result = next(
                (
                    m
                    for m in messages
                    if isinstance(m, dict) and m.get("type") == "emotions"
                ),
                {},
            )
            vectors = result.get("vectors")
            if (
                process.returncode != 0
                or not isinstance(vectors, list)
                or len(vectors) != len(prompts)
            ):
                raise TTSEngineError(
                    "QwenEmotion preparation failed: " + stderr[-3000:]
                )
            return vectors
        finally:
            self._terminate(process)
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            with self._lock:
                self._emotion_process = None
            self.log_callback("QwenEmotion process stopped and memory released.")

    def _ensure_worker(
        self,
        voice_config: dict[str, Any],
    ) -> subprocess.Popen[str]:
        config_values = validated_config(voice_config)
        config = (
            str(self.manager.model_dir),
            config_values["device"],
            config_values["dtype"],
            str(self.manager.cache_dir),
            "0",
        )
        with self._lock:
            process = self._process
            if (
                process is not None
                and process.poll() is None
                and self._worker_config is not None
                and self._worker_config == config
            ):
                return process
        self._close_worker(force=False)
        return self._start_worker(config)

    def _start_worker(
        self,
        config: tuple[str, str, str, str, str],
    ) -> subprocess.Popen[str]:
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")

        model_dir, device, dtype, cache_dir, use_qwen_emo = config
        command = [
            *self.manager.runtime_command(),
            "--worker",
            "--model-dir",
            model_dir,
            "--device",
            device,
            "--dtype",
            dtype,
            "--cache-dir",
            cache_dir,
            "--use-qwen-emo",
            use_qwen_emo,
        ]
        self.log_callback("Starting IndexTTS persistent worker.")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.manager.runtime_environment(),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise TTSEngineError(f"Could not start IndexTTS: {exc}") from exc

        with self._lock:
            self._process = process
            self._worker_config = config
            self._messages = queue.Queue()
            self._stderr_lines = []
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                name="IndexTTSStdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                name="IndexTTSStderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

        self._wait_for_ready(process)
        return process

    def _wait_for_ready(self, process: subprocess.Popen[str]) -> None:
        while True:
            message = self._next_worker_message(process)
            message_type = str(message.get("type", ""))
            if message_type == "ready":
                device = str(message.get("device", "")).strip()
                dtype = str(message.get("dtype", "")).strip()
                backend = str(message.get("backend", "")).strip()
                suffix = ", ".join(part for part in (backend, device, dtype) if part)
                if suffix:
                    self.log_callback(f"IndexTTS worker ready ({suffix}).")
                else:
                    self.log_callback("IndexTTS worker ready.")
                return
            if message_type in {"timing", "info"}:
                self._log_worker_message(message)
                continue
            self._raise_for_worker_message(message)

    def _wait_for_response(
        self,
        process: subprocess.Popen[str],
        request_id: str,
    ) -> None:
        while True:
            message = self._next_worker_message(process)
            message_type = str(message.get("type", ""))
            if message_type in {"timing", "info"}:
                self._log_worker_message(message)
                continue
            if message_type == "result" and str(message.get("id", "")) == request_id:
                self.log_callback(
                    f"IndexTTS - created: {message.get('output', 'unknown output')}"
                )
                return
            if message_type == "error" and str(message.get("id", "")) == request_id:
                raise TTSEngineError(str(message.get("message", "Unknown error.")))
            self._raise_for_worker_message(message)

    def _next_worker_message(
        self,
        process: subprocess.Popen[str],
    ) -> dict[str, Any]:
        while True:
            if self._cancel_requested.is_set():
                self._close_worker(force=True)
                raise TTSCancelled("Generation cancelled.")
            try:
                message = self._messages.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    raise TTSEngineError(
                        "IndexTTS worker exited unexpectedly. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "stdout_closed":
                if process.poll() is not None:
                    raise TTSEngineError(
                        "IndexTTS worker stdout closed. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "raw":
                self.log_callback(f"IndexTTS - {message.get('message', '')}")
                continue
            return message

    def _send_request(
        self,
        process: subprocess.Popen[str],
        request: dict[str, Any],
    ) -> None:
        if process.stdin is None:
            raise TTSEngineError("IndexTTS worker stdin is not available.")
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise TTSEngineError(
                "Could not send request to IndexTTS worker. "
                + self._worker_error_details(process)
            ) from exc

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except json.JSONDecodeError:
                    message = {"type": "raw", "message": stripped}
                if isinstance(message, dict):
                    self._messages.put(message)
                else:
                    self._messages.put({"type": "raw", "message": stripped})
        finally:
            self._messages.put({"type": "stdout_closed"})

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stripped = line.strip()
            if not stripped:
                continue
            with self._lock:
                self._stderr_lines.append(stripped)
                self._stderr_lines = self._stderr_lines[-50:]

    def _close_worker(self, force: bool) -> None:
        with self._lock:
            process = self._process
            stdout_thread = self._stdout_thread
            stderr_thread = self._stderr_thread
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._worker_config = None
        if process is None:
            return
        if process.poll() is None and not force:
            try:
                if process.stdin is not None:
                    process.stdin.write(
                        json.dumps({"type": "shutdown"}, ensure_ascii=False) + "\n"
                    )
                    process.stdin.flush()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            self._terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
        current_thread = threading.current_thread()
        for thread in (stdout_thread, stderr_thread):
            if (
                thread is not None
                and thread is not current_thread
                and thread.is_alive()
            ):
                thread.join(timeout=1)

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_index += 1
            return str(self._request_index)

    def _log_worker_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "timing":
            label = str(message.get("label", "operation"))
            elapsed = float(message.get("elapsed", 0.0))
            self.log_callback(f"IndexTTS timing - {label}: {elapsed:.3f} s")
        elif message_type == "info":
            self.log_callback(f"IndexTTS - {message.get('message', '')}")

    def _raise_for_worker_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "fatal":
            raise TTSEngineError(str(message.get("message", "Unknown fatal error.")))
        if message_type == "error":
            raise TTSEngineError(str(message.get("message", "Unknown error.")))
        if message_type == "shutdown":
            raise TTSEngineError("IndexTTS worker shut down unexpectedly.")

    def _worker_error_details(self, process: subprocess.Popen[str]) -> str:
        with self._lock:
            stderr_text = "\n".join(self._stderr_lines).strip()
        if len(stderr_text) > 3000:
            stderr_text = stderr_text[-3000:]
        exit_code = process.poll()
        prefix = (
            f"Exit code: {exit_code}. "
            if exit_code is not None
            else "The worker is still running. "
        )
        return prefix + (stderr_text or "No error details were returned.")

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

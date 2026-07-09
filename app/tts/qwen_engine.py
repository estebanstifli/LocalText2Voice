from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError
from .qwen_manager import QwenManager


class QwenTTSEngine(BaseTTSEngine):
    def __init__(self, manager: QwenManager | None = None) -> None:
        self.manager = manager or QwenManager()
        self._process: subprocess.Popen[str] | None = None
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
                "Qwen3 TTS is selected, but its runtime dependencies or "
                "model cache are not installed. Open Settings > TTS Engines "
                "and click Install."
            )
        if not str(voice_config.get("speaker", "")).strip():
            raise TTSEngineError("Qwen3 TTS requires a speaker.")
        if not str(voice_config.get("language", "")).strip():
            raise TTSEngineError("Qwen3 TTS requires a language.")
        device = str(voice_config.get("device", "auto"))
        if device not in {"auto", "cuda", "cpu"}:
            raise TTSEngineError(f"Unsupported Qwen3 TTS device: {device}")

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        process = self._ensure_worker(voice_config)
        request_id = self._next_request_id()
        request = {
            "type": "synthesize",
            "id": request_id,
            "text": text,
            "output": str(output_wav),
            "language": str(voice_config.get("language", "Spanish")),
            "speaker": str(voice_config.get("speaker", "Serena")),
            "instruct": str(voice_config.get("instruct", "")).strip(),
        }
        for key in (
            "temperature",
            "top_k",
            "top_p",
            "repetition_penalty",
            "max_new_tokens",
        ):
            if key in voice_config and voice_config[key] is not None:
                request[key] = voice_config[key]
        self._send_request(process, request)
        self._wait_for_response(process, request_id)
        if not output_wav.is_file() or output_wav.stat().st_size == 0:
            raise TTSEngineError(
                "Qwen3 TTS completed but did not create a valid WAV file."
            )
        return output_wav

    def preload(self, voice_config: dict[str, Any]) -> None:
        self.validate(voice_config)
        self._ensure_worker(voice_config)

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        self._close_worker(force=True)

    def close(self) -> None:
        self._close_worker(force=self._cancel_requested.is_set())

    def _ensure_worker(self, voice_config: dict[str, Any]) -> subprocess.Popen[str]:
        model_id = str(voice_config.get("model", "custom_voice_0_6b"))
        model_repo = str(
            voice_config.get("model_repo") or self.manager.model_repo(model_id)
        )
        config = (
            model_repo,
            str(voice_config.get("device", "auto")),
            str(voice_config.get("dtype", "auto")),
            str(self.manager.cache_dir),
            str(self.manager.dependency_dir),
        )
        with self._lock:
            process = self._process
            if (
                process is not None
                and process.poll() is None
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

        model_repo, device, dtype, cache_dir, deps_dir = config
        command = [
            *self.manager.runtime_command(),
            "--worker",
            "--model-repo",
            model_repo,
            "--device",
            device,
            "--dtype",
            dtype,
            "--cache-dir",
            cache_dir,
            "--deps-dir",
            deps_dir,
        ]
        self.log_callback("Starting Qwen3 TTS persistent worker.")
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
            raise TTSEngineError(f"Could not start Qwen3 TTS: {exc}") from exc

        with self._lock:
            self._process = process
            self._worker_config = config
            self._messages = queue.Queue()
            self._stderr_lines = []
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                name="QwenStdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                name="QwenStderr",
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
                    self.log_callback(f"Qwen3 TTS worker ready ({suffix}).")
                else:
                    self.log_callback("Qwen3 TTS worker ready.")
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
                    "Qwen3 TTS - created: "
                    f"{message.get('output', 'unknown output')}"
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
                        "Qwen3 TTS worker exited unexpectedly. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "stdout_closed":
                if process.poll() is not None:
                    raise TTSEngineError(
                        "Qwen3 TTS worker stdout closed. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "raw":
                self.log_callback(f"Qwen3 TTS - {message.get('message', '')}")
                continue
            return message

    def _send_request(
        self,
        process: subprocess.Popen[str],
        request: dict[str, Any],
    ) -> None:
        if process.stdin is None:
            raise TTSEngineError("Qwen3 TTS worker stdin is not available.")
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise TTSEngineError(
                "Could not send request to Qwen3 TTS worker. "
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
            self.log_callback(f"Qwen3 TTS timing - {label}: {elapsed:.3f} s")
        elif message_type == "info":
            self.log_callback(f"Qwen3 TTS - {message.get('message', '')}")

    def _raise_for_worker_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "fatal":
            raise TTSEngineError(str(message.get("message", "Unknown fatal error.")))
        if message_type == "error":
            raise TTSEngineError(str(message.get("message", "Unknown error.")))
        if message_type == "shutdown":
            raise TTSEngineError("Qwen3 TTS worker shut down unexpectedly.")

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

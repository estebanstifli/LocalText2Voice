from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.tts.python_runtime_manager import (
    PythonRuntimeCancelled,
    PythonRuntimeError,
    PythonRuntimeManager,
)
from app.tts.model_cache import huggingface_model_is_cached
from app.utils.paths import app_data_root


class FasterWhisperError(RuntimeError):
    pass


class FasterWhisperCancelled(FasterWhisperError):
    pass


WhisperProgress = Callable[[int, int, str], None]


FASTER_WHISPER_CLI = r'''
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_timing(label: str, started: float, request_id: str | None = None) -> None:
    payload = {"type": "timing", "label": label, "elapsed": time.perf_counter() - started}
    if request_id is not None:
        payload["id"] = request_id
    emit(payload)


def configure_environment(cache_dir: str, deps_dir: str) -> None:
    deps_path = Path(deps_dir)
    if str(deps_path) not in sys.path:
        sys.path.insert(0, str(deps_path))
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_path)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_path / "hub")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if hasattr(os, "add_dll_directory") and deps_path.exists():
        try:
            os.add_dll_directory(str(deps_path))
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join([str(deps_path), os.environ.get("PATH", "")])


def load_model(model_name: str, device: str, compute_type: str, cache_dir: str):
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    kwargs = {
        "device": device,
        "compute_type": compute_type,
        "download_root": str(Path(cache_dir) / "models"),
    }
    model = WhisperModel(model_name, **kwargs)
    emit_timing("model load", started)
    return model


def transcribe_file(model, audio_path: Path, language: str, beam_size: int):
    kwargs = {
        "beam_size": beam_size,
        "vad_filter": False,
        "word_timestamps": True,
    }
    if language and language != "auto":
        kwargs["language"] = language
    segments, info = model.transcribe(str(audio_path), **kwargs)
    parts = []
    words = []
    for segment in segments:
        parts.append(str(segment.text).strip())
        for word in getattr(segment, "words", None) or []:
            text = str(getattr(word, "word", "") or "").strip()
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start": round(float(getattr(word, "start", 0.0) or 0.0), 3),
                    "end": round(float(getattr(word, "end", 0.0) or 0.0), 3),
                    "probability": round(
                        float(getattr(word, "probability", 0.0) or 0.0),
                        4,
                    ),
                }
            )
    return " ".join(part for part in parts if part), words, info


def main() -> int:
    total_started = time.perf_counter()
    parser = argparse.ArgumentParser(description="LocalText2Voice Faster Whisper worker")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--deps-dir", required=True)
    args = parser.parse_args()

    configure_environment(args.cache_dir, args.deps_dir)
    try:
        import_started = time.perf_counter()
        import faster_whisper
        import ctranslate2
        emit_timing("dependency import", import_started)
        emit({
            "type": "info",
            "message": (
                f"faster-whisper {getattr(faster_whisper, '__version__', 'unknown')}, "
                f"CTranslate2 {getattr(ctranslate2, '__version__', 'unknown')}"
            ),
        })
    except Exception as exc:
        emit({"type": "fatal", "message": f"Faster Whisper dependencies are missing: {exc}"})
        return 3

    try:
        model = load_model(args.model, args.device, args.compute_type, args.cache_dir)
    except Exception as exc:
        emit({"type": "fatal", "message": f"Faster Whisper model load failed: {exc}"})
        return 5

    emit_timing("worker startup", total_started)
    emit({
        "type": "ready",
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
    })
    if args.warmup and not args.worker:
        return 0

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"type": "error", "id": "", "message": f"Invalid JSON request: {exc}"})
            continue
        request_id = str(request.get("id", ""))
        if request.get("type") == "shutdown":
            emit({"type": "shutdown"})
            return 0
        if request.get("type") != "transcribe":
            emit({"type": "error", "id": request_id, "message": "Unknown request type."})
            continue
        started = time.perf_counter()
        try:
            audio_path = Path(str(request["audio"]))
            if not audio_path.is_file():
                raise FileNotFoundError(str(audio_path))
            text, words, info = transcribe_file(
                model,
                audio_path,
                str(request.get("language", "auto")),
                int(request.get("beam_size", 1)),
            )
            emit_timing("transcription", started, request_id)
            emit({
                "type": "result",
                "id": request_id,
                "text": text,
                "words": words,
                "language": str(getattr(info, "language", "")),
                "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            })
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"Transcription failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class FasterWhisperManager:
    VERSION = "faster-whisper-small-v1"
    RUNTIME_VERSION = "faster-whisper-runtime-v1"
    INSTALL_FILENAME = "faster-whisper-install.json"
    RUNTIME_INSTALL_FILENAME = "faster-whisper-runtime-install.json"
    CLI_FILENAME = "faster_whisper_worker.py"
    PACKAGE = "faster-whisper==1.2.1"
    MODEL_NAME = "small"
    MODEL_REPO = "Systran/faster-whisper-small"
    MODEL_REQUIRED_FILES = {
        "config.json": 1_000,
        "model.bin": 100 * 1024 * 1024,
        "tokenizer.json": 100 * 1024,
    }

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "faster-whisper"
        self.cache_dir = self.install_dir / "hf-cache"
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        return self.has_model_files() and self.has_runtime() and self.cli_path.is_file()

    def has_model_files(self) -> bool:
        return huggingface_model_is_cached(
            self.cache_dir,
            self.MODEL_REPO,
            self.MODEL_REQUIRED_FILES,
        )

    def has_runtime(self) -> bool:
        manifest = self._read_json(self.runtime_manifest_path)
        return (
            manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
            and (self.dependency_dir / "faster_whisper").is_dir()
            and (self.dependency_dir / "ctranslate2").is_dir()
        )

    def install(
        self,
        progress_callback: WhisperProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._install_runtime_dependencies(progress, cancel_token)
            self._write_cli()
            self._write_manifest("installing")
            progress(82, 100, "Downloading Faster Whisper small model...")
            self._run_runtime(["--warmup"], cancel_token)
            self._write_manifest("installed")
            progress(100, 100, "Faster Whisper small is ready.")
            return self.install_dir
        except (FasterWhisperCancelled, PythonRuntimeCancelled):
            self._write_manifest("cancelled")
            raise
        except Exception:
            self._write_manifest("failed")
            raise

    def uninstall(self) -> None:
        self.cancel()
        self._remove_path(self.install_dir)
        self._remove_path(self.runtime_manifest_path)
        self._remove_path(self.dependency_dir)

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.python_runtime.cancel()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def runtime_command(self) -> list[str]:
        self._write_cli()
        return [str(self.python_runtime.python_exe), str(self.cli_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(self.dependency_dir)
        env["HF_HOME"] = str(self.cache_dir)
        env["HUGGINGFACE_HUB_CACHE"] = str(self.cache_dir / "hub")
        env["PATH"] = os.pathsep.join(
            [str(self.dependency_dir), env.get("PATH", "")]
        )
        return env

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def runtime_manifest_path(self) -> Path:
        return (
            self.python_runtime.runtime_dir
            / "engine-deps"
            / self.RUNTIME_INSTALL_FILENAME
        )

    @property
    def dependency_dir(self) -> Path:
        return (
            self.python_runtime.runtime_dir
            / "engine-deps"
            / "faster-whisper"
            / "site-packages"
        )

    @property
    def cli_path(self) -> Path:
        return self.install_dir / self.CLI_FILENAME

    def _install_runtime_dependencies(
        self,
        progress: WhisperProgress,
        cancel_token: threading.Event | None,
    ) -> None:
        if not self.python_runtime.is_installed():
            self.python_runtime.install(
                lambda current, total, message: progress(
                    int((current / total) * 25) if total else 0,
                    100,
                    message,
                ),
                cancel_token,
            )
        if self.has_runtime():
            progress(75, 100, "Faster Whisper dependencies already installed.")
            return

        self._remove_path(self.dependency_dir)
        self.dependency_dir.mkdir(parents=True, exist_ok=True)
        progress(35, 100, "Installing Faster Whisper CPU runtime...")
        self.python_runtime.run_python(
            [
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--target",
                str(self.dependency_dir),
                "--no-warn-script-location",
                self.PACKAGE,
            ],
            cancel_token,
        )
        progress(72, 100, "Validating Faster Whisper runtime...")
        self._validate_runtime(cancel_token)
        self._write_runtime_manifest("installed", [self.PACKAGE])

    def _validate_runtime(self, cancel_token: threading.Event | None) -> None:
        code = (
            "import sys, json; "
            f"sys.path.insert(0, {str(self.dependency_dir)!r}); "
            "import faster_whisper, ctranslate2, av; "
            "print(json.dumps({'ok': True, 'ctranslate2': ctranslate2.__version__}))"
        )
        self.python_runtime.run_python(["-c", code], cancel_token)

    def _run_runtime(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> str:
        if not self.python_runtime.is_installed():
            raise FasterWhisperError("Embedded Python runtime is not installed.")
        command = [
            *self.runtime_command(),
            *args,
            "--model",
            self.MODEL_NAME,
            "--device",
            device,
            "--compute-type",
            compute_type,
            "--cache-dir",
            str(self.cache_dir),
            "--deps-dir",
            str(self.dependency_dir),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.runtime_environment(),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise FasterWhisperError(f"Could not start Faster Whisper: {exc}") from exc
        with self._lock:
            self._process = process
        stdout = b""
        stderr = b""
        try:
            while True:
                self._check_cancelled(cancel_token)
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise FasterWhisperError(
                "Faster Whisper failed with exit code "
                f"{process.returncode}: {self._runtime_json_error(stdout_text) or stderr_text}"
            )
        return stdout_text

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(FASTER_WHISPER_CLI, encoding="utf-8")

    def _write_manifest(self, state: str) -> None:
        manifest = {
            "engine": "faster-whisper",
            "version": self.VERSION,
            "state": state,
            "model": self.MODEL_NAME,
            "model_repo": self.MODEL_REPO,
            "updated_at": self._now(),
            "cache_dir": str(self.cache_dir),
            "runtime": "python",
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_json_atomic(self.manifest_path, manifest)

    def _write_runtime_manifest(self, state: str, requirements: list[str]) -> None:
        manifest = {
            "engine": "faster-whisper",
            "runtime_version": self.RUNTIME_VERSION,
            "state": state,
            "updated_at": self._now(),
            "requirements": requirements,
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_json_atomic(self.runtime_manifest_path, manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None = None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise FasterWhisperCancelled("Faster Whisper operation cancelled.")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    @staticmethod
    def _runtime_json_error(stdout: str) -> str:
        for line in reversed([line for line in stdout.splitlines() if line.strip()]):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") == "fatal":
                return str(data.get("message", ""))
        return ""

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FasterWhisperVerifier:
    def __init__(self, manager: FasterWhisperManager | None = None) -> None:
        self.manager = manager or FasterWhisperManager()
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._request_index = 0
        self._worker_config: tuple[str, str] | None = None
        self._cuda_fallback_reason = ""
        self._lock = threading.RLock()
        self._cancel_requested = threading.Event()
        self.log_callback: Callable[[str], None] = lambda message: None

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        self.log_callback = callback

    def preload(self, device: str = "cpu", compute_type: str = "int8") -> None:
        effective_device, effective_compute_type = self._effective_config(
            device,
            compute_type,
        )
        try:
            self._ensure_worker(effective_device, effective_compute_type)
        except FasterWhisperError as exc:
            if not self._should_fallback_to_cpu(effective_device, exc):
                raise
            self._activate_cpu_fallback(exc)
            self._ensure_worker("cpu", "int8")

    def transcribe(
        self,
        audio_path: Path,
        language: str = "auto",
        beam_size: int = 1,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> dict[str, Any]:
        if not self.manager.is_installed():
            raise FasterWhisperError("Faster Whisper small is not installed.")
        effective_device, effective_compute_type = self._effective_config(
            device,
            compute_type,
        )
        try:
            return self._transcribe_once(
                audio_path,
                language,
                beam_size,
                effective_device,
                effective_compute_type,
            )
        except FasterWhisperError as exc:
            if not self._should_fallback_to_cpu(effective_device, exc):
                raise
            self._activate_cpu_fallback(exc)
            return self._transcribe_once(
                audio_path,
                language,
                beam_size,
                "cpu",
                "int8",
            )

    def _transcribe_once(
        self,
        audio_path: Path,
        language: str,
        beam_size: int,
        device: str,
        compute_type: str,
    ) -> dict[str, Any]:
        process = self._ensure_worker(device, compute_type)
        request_id = self._next_request_id()
        self._send_request(
            process,
            {
                "type": "transcribe",
                "id": request_id,
                "audio": str(audio_path),
                "language": language,
                "beam_size": beam_size,
                "word_timestamps": True,
            },
        )
        return self._wait_for_response(process, request_id)

    def _effective_config(
        self,
        device: str,
        compute_type: str,
    ) -> tuple[str, str]:
        if self._cuda_fallback_reason and device in {"auto", "cuda"}:
            return "cpu", "int8"
        return device, compute_type

    def _activate_cpu_fallback(self, exc: FasterWhisperError) -> None:
        self._cuda_fallback_reason = str(exc)
        self.log_callback(
            "Faster Whisper CUDA libraries are unavailable; "
            "falling back to CPU (int8)."
        )
        self.close(force=True)

    @classmethod
    def _should_fallback_to_cpu(
        cls,
        device: str,
        exc: FasterWhisperError,
    ) -> bool:
        if device not in {"auto", "cuda"}:
            return False
        message = str(exc).casefold()
        library_markers = (
            "cublas",
            "cudnn",
            "cudart",
            "cuda runtime",
        )
        load_markers = (
            "not found",
            "cannot load",
            "cannot be loaded",
            "could not load",
            "could not be loaded",
            "could not locate",
            "failed to load",
            "missing",
            "unable to load",
        )
        return any(marker in message for marker in library_markers) and any(
            marker in message for marker in load_markers
        )

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        self.close(force=True)

    def close(self, force: bool = False) -> None:
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
                    process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                    process.stdin.flush()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        current = threading.current_thread()
        for thread in (stdout_thread, stderr_thread):
            if thread is not None and thread is not current and thread.is_alive():
                thread.join(timeout=1)

    def _ensure_worker(
        self,
        device: str,
        compute_type: str,
    ) -> subprocess.Popen[str]:
        config = (device, compute_type)
        with self._lock:
            process = self._process
            if (
                process is not None
                and process.poll() is None
                and self._worker_config == config
            ):
                return process
        self.close(force=False)
        return self._start_worker(config)

    def _start_worker(
        self,
        config: tuple[str, str],
    ) -> subprocess.Popen[str]:
        device, compute_type = config
        command = [
            *self.manager.runtime_command(),
            "--worker",
            "--model",
            self.manager.MODEL_NAME,
            "--device",
            device,
            "--compute-type",
            compute_type,
            "--cache-dir",
            str(self.manager.cache_dir),
            "--deps-dir",
            str(self.manager.dependency_dir),
        ]
        self.log_callback("Starting Faster Whisper persistent worker.")
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
            raise FasterWhisperError(f"Could not start Faster Whisper: {exc}") from exc
        with self._lock:
            self._process = process
            self._worker_config = config
            self._messages = queue.Queue()
            self._stderr_lines = []
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                daemon=True,
                name="FasterWhisperStdout",
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                daemon=True,
                name="FasterWhisperStderr",
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
        self._wait_for_ready(process)
        return process

    def _wait_for_ready(self, process: subprocess.Popen[str]) -> None:
        while True:
            message = self._next_message(process)
            message_type = str(message.get("type", ""))
            if message_type == "ready":
                self.log_callback(
                    "Faster Whisper worker ready "
                    f"({message.get('model')}, {message.get('device')}, "
                    f"{message.get('compute_type')})."
                )
                return
            if message_type in {"timing", "info"}:
                self._log_message(message)
                continue
            self._raise_for_message(message)

    def _wait_for_response(
        self,
        process: subprocess.Popen[str],
        request_id: str,
    ) -> dict[str, Any]:
        while True:
            message = self._next_message(process)
            message_type = str(message.get("type", ""))
            if message_type in {"timing", "info"}:
                self._log_message(message)
                continue
            if message_type == "result" and str(message.get("id", "")) == request_id:
                return message
            if message_type == "error" and str(message.get("id", "")) == request_id:
                raise FasterWhisperError(str(message.get("message", "Unknown error.")))
            self._raise_for_message(message)

    def _next_message(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        while True:
            if self._cancel_requested.is_set():
                self.close(force=True)
                raise FasterWhisperCancelled("Verification cancelled.")
            try:
                message = self._messages.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    raise FasterWhisperError(
                        "Faster Whisper worker exited unexpectedly. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "stdout_closed":
                if process.poll() is not None:
                    raise FasterWhisperError(
                        "Faster Whisper stdout closed. "
                        + self._worker_error_details(process)
                    )
                continue
            if message.get("type") == "raw":
                self.log_callback(f"Faster Whisper - {message.get('message', '')}")
                continue
            return message

    def _send_request(
        self,
        process: subprocess.Popen[str],
        request: dict[str, Any],
    ) -> None:
        if process.stdin is None:
            raise FasterWhisperError("Faster Whisper stdin is not available.")
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

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
                self._messages.put(message if isinstance(message, dict) else {"type": "raw", "message": stripped})
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

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_index += 1
            return str(self._request_index)

    def _log_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "timing":
            self.log_callback(
                "Faster Whisper timing - "
                f"{message.get('label', 'operation')}: "
                f"{float(message.get('elapsed', 0.0)):.3f} s"
            )
        elif message.get("type") == "info":
            self.log_callback(f"Faster Whisper - {message.get('message', '')}")

    def _raise_for_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "fatal":
            raise FasterWhisperError(str(message.get("message", "Unknown fatal error.")))
        if message.get("type") == "error":
            raise FasterWhisperError(str(message.get("message", "Unknown error.")))

    def _worker_error_details(self, process: subprocess.Popen[str]) -> str:
        with self._lock:
            stderr_text = "\n".join(self._stderr_lines).strip()
        if len(stderr_text) > 3000:
            stderr_text = stderr_text[-3000:]
        return f"Exit code: {process.poll()}. " + (
            stderr_text or "No error details were returned."
        )

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError


class PiperTTSEngine(BaseTTSEngine):
    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()

    def validate(self, voice_config: dict[str, Any]) -> None:
        missing: list[str] = []
        if not self.executable.is_file():
            missing.append(f"Piper executable: {self.executable}")

        model_path = Path(str(voice_config.get("model_path", "")))
        config_path = Path(str(voice_config.get("config_path", "")))
        if not model_path.is_file():
            missing.append(f"Voice model: {model_path}")
        if not config_path.is_file():
            missing.append(f"Voice configuration: {config_path}")

        if missing:
            details = "\n".join(f"- {item}" for item in missing)
            raise TTSEngineError(f"Required Piper files are missing:\n{details}")

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
        speed = max(0.25, float(voice_config.get("speed", 1.0)))
        command = [
            str(self.executable),
            "--model",
            str(voice_config["model_path"]),
            "--config",
            str(voice_config["config_path"]),
            "--output_file",
            str(output_wav),
            "--length_scale",
            f"{1.0 / speed:.4f}",
        ]

        startup_info = None
        creation_flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startup_info,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise TTSEngineError(f"Could not start Piper: {exc}") from exc

        with self._lock:
            self._process = process

        stdout = b""
        stderr = b""
        first_communication = True
        try:
            while True:
                if self._cancel_requested.is_set():
                    self._terminate(process)
                    raise TTSCancelled("Generation cancelled.")
                try:
                    if first_communication:
                        stdout, stderr = process.communicate(
                            input=text.encode("utf-8"),
                            timeout=0.2,
                        )
                        first_communication = False
                    else:
                        stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    first_communication = False
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            raise TTSEngineError(
                f"Piper failed with exit code {process.returncode}: "
                f"{error_text or 'No error details were returned.'}"
            )
        if not output_wav.is_file() or output_wav.stat().st_size == 0:
            raise TTSEngineError(
                "Piper completed but did not create a valid WAV file."
            )
        return output_wav

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

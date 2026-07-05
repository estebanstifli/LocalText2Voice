from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError
from .kokoro_manager import KokoroManager


class KokoroTTSEngine(BaseTTSEngine):
    def __init__(self, manager: KokoroManager | None = None) -> None:
        self.manager = manager or KokoroManager()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()

    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self.manager.is_installed():
            raise TTSEngineError(
                "Kokoro is selected but its model assets are not installed. "
                "Open Settings > General and click Install."
            )
        if not self._runtime_command_available():
            raise TTSEngineError(
                "Kokoro model files are installed, but kokoro_engine.exe was "
                f"not found at {self.manager.runtime_path}. Build or copy the "
                "separate Kokoro runtime into engines/kokoro/."
            )
        if not str(voice_config.get("voice", "")).strip():
            raise TTSEngineError("Kokoro requires a voice.")
        if not str(voice_config.get("lang", "")).strip():
            raise TTSEngineError("Kokoro requires a language.")

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
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".txt",
            delete=False,
        ) as input_file:
            input_file.write(text)
            input_path = Path(input_file.name)

        command = self._runtime_command(
            input_path,
            output_wav,
            voice_config,
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            input_path.unlink(missing_ok=True)
            raise TTSEngineError(f"Could not start Kokoro: {exc}") from exc

        with self._lock:
            self._process = process
        stdout = b""
        stderr = b""
        try:
            while True:
                if self._cancel_requested.is_set():
                    self._terminate(process)
                    raise TTSCancelled("Generation cancelled.")
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            input_path.unlink(missing_ok=True)
            with self._lock:
                if self._process is process:
                    self._process = None

        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            raise TTSEngineError(
                f"Kokoro failed with exit code {process.returncode}: "
                f"{details or 'No error details were returned.'}"
            )
        if not output_wav.is_file() or output_wav.stat().st_size == 0:
            raise TTSEngineError(
                "Kokoro completed but did not create a valid WAV file."
            )
        return output_wav

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._terminate(process)

    def _runtime_command_available(self) -> bool:
        return self.manager.runtime_path.is_file() or not getattr(sys, "frozen", False)

    def _runtime_command(
        self,
        input_path: Path,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> list[str]:
        base = (
            [str(self.manager.runtime_path)]
            if self.manager.runtime_path.is_file()
            else [sys.executable, "-m", "app.tts.kokoro_cli"]
        )
        return [
            *base,
            "--input",
            str(input_path),
            "--output",
            str(output_wav),
            "--voice",
            str(voice_config.get("voice", "af_heart")),
            "--lang",
            str(voice_config.get("lang", "en-us")),
            "--speed",
            f"{float(voice_config.get('speed', 1.0)):.4f}",
            "--provider",
            str(voice_config.get("provider", "cpu")),
            "--model",
            str(self.manager.model_path),
            "--voices",
            str(self.manager.voices_path),
        ]

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

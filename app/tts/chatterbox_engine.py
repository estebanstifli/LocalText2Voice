from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError
from .chatterbox_manager import ChatterboxManager


class ChatterboxTTSEngine(BaseTTSEngine):
    def __init__(self, manager: ChatterboxManager | None = None) -> None:
        self.manager = manager or ChatterboxManager()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()

    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self.manager.has_runtime():
            raise TTSEngineError(
                "Chatterbox is selected, but chatterbox_engine.exe was not "
                f"found at {self.manager.runtime_path}. Build it with "
                "build_chatterbox_engine.bat or install a runtime pack."
            )
        if not self.manager.runtime_is_current():
            raise TTSEngineError(
                "The installed Chatterbox runtime is outdated. Open Settings > "
                "General > Chatterbox and click Install to update the CUDA "
                "runtime before generating audio."
            )
        model = str(voice_config.get("model", "multilingual_v3"))
        reference = str(voice_config.get("reference_audio_path", "")).strip()
        if model == "turbo" and not reference:
            raise TTSEngineError(
                "Chatterbox Turbo requires a 5-20 second reference audio file."
            )
        if reference and not Path(reference).expanduser().is_file():
            raise TTSEngineError(f"Reference audio file not found: {reference}")
        device = str(voice_config.get("device", "auto"))
        if device not in {"auto", "cuda", "cpu", "mps"}:
            raise TTSEngineError(f"Unsupported Chatterbox device: {device}")

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

        synthesis_config = dict(voice_config)
        try:
            self._run_synthesis_process(input_path, output_wav, synthesis_config)
        except TTSEngineError as exc:
            if (
                str(synthesis_config.get("device", "")).lower() == "cuda"
                and self._is_cuda_unavailable_error(str(exc))
            ):
                output_wav.unlink(missing_ok=True)
                synthesis_config["device"] = "auto"
                self._run_synthesis_process(input_path, output_wav, synthesis_config)
            else:
                raise
        finally:
            input_path.unlink(missing_ok=True)

        if not output_wav.is_file() or output_wav.stat().st_size == 0:
            raise TTSEngineError(
                "Chatterbox completed but did not create a valid WAV file."
            )
        return output_wav

    def _run_synthesis_process(
        self,
        input_path: Path,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> None:
        command = self._runtime_command(input_path, output_wav, voice_config)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.manager.runtime_environment(),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise TTSEngineError(f"Could not start Chatterbox: {exc}") from exc

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
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            if not details:
                details = stdout.decode("utf-8", errors="replace").strip()
            raise TTSEngineError(
                f"Chatterbox failed with exit code {process.returncode}: "
                f"{details or 'No error details were returned.'}"
            )

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._terminate(process)

    def _runtime_command(
        self,
        input_path: Path,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> list[str]:
        command = [
            *self.manager.runtime_command(),
            "--input",
            str(input_path),
            "--output",
            str(output_wav),
            "--model",
            str(voice_config.get("model", "multilingual_v3")),
            "--language",
            str(voice_config.get("language", "en")),
            "--device",
            str(voice_config.get("device", "auto")),
            "--exaggeration",
            f"{float(voice_config.get('exaggeration', 0.5)):.4f}",
            "--cfg-weight",
            f"{float(voice_config.get('cfg_weight', 0.5)):.4f}",
            "--cache-dir",
            str(self.manager.cache_dir),
        ]
        reference = str(voice_config.get("reference_audio_path", "")).strip()
        if reference:
            command.extend(["--reference", reference])
        return command

    @staticmethod
    def _is_cuda_unavailable_error(message: str) -> bool:
        lowered = message.lower()
        return "cuda was requested" in lowered and (
            "cannot see a cuda gpu" in lowered
            or "not available" in lowered
            or "no cuda" in lowered
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

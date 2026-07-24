from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Sequence

from .paths import resolve_app_path


class FFmpegError(RuntimeError):
    pass


class FFmpegCancelled(FFmpegError):
    pass


def find_ffmpeg(configured_path: str | Path) -> Path:
    configured = resolve_app_path(configured_path)
    if configured.is_file():
        return configured

    # Windows configs usually say "ffmpeg/ffmpeg.exe"; on Linux/macOS the
    # same bundled folder would hold an extension-less binary.
    if configured.suffix.lower() == ".exe":
        sibling = configured.with_suffix("")
        if sibling.is_file():
            return sibling

    path_match = shutil.which("ffmpeg")
    if path_match:
        return Path(path_match)

    raise FFmpegError(
        "FFmpeg was not found. Place ffmpeg in the ffmpeg folder, "
        "set ffmpeg_path in config.json, or add FFmpeg to PATH."
    )


class FFmpegRunner:
    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()

    def run(self, arguments: Sequence[str]) -> None:
        if self._cancel_requested.is_set():
            raise FFmpegCancelled("Generation cancelled.")

        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        command = [str(self.executable), *arguments]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise FFmpegError(f"Could not start FFmpeg: {exc}") from exc

        with self._lock:
            self._process = process

        stdout = b""
        stderr = b""
        try:
            while True:
                if self._cancel_requested.is_set():
                    self._terminate(process)
                    raise FFmpegCancelled("Generation cancelled.")
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            if len(error_text) > 3000:
                error_text = error_text[-3000:]
            raise FFmpegError(
                f"FFmpeg failed with exit code {process.returncode}:\n"
                f"{error_text or 'No error details were returned.'}"
            )

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

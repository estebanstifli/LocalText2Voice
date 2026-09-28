from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

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
        self.progress_callback: Callable[[int], None] | None = None

    def run(self, arguments: Sequence[str]) -> None:
        if self._cancel_requested.is_set():
            raise FFmpegCancelled("Generation cancelled.")

        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        progress_args = (
            ["-progress", "pipe:1", "-nostats"] if self.progress_callback else []
        )
        command = [str(self.executable), *progress_args, *arguments]
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

        stderr = b""
        try:
            if self.progress_callback:
                stderr = self._communicate_progress(process)
            else:
                while True:
                    if self._cancel_requested.is_set():
                        self._terminate(process)
                        raise FFmpegCancelled("Generation cancelled.")
                    try:
                        _stdout, stderr = process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
        except BaseException:
            self._terminate(process)
            raise
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

        if self._cancel_requested.is_set():
            raise FFmpegCancelled("Generation cancelled.")
        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            if len(error_text) > 3000:
                error_text = error_text[-3000:]
            raise FFmpegError(
                f"FFmpeg failed with exit code {process.returncode}:\n"
                f"{error_text or 'No error details were returned.'}"
            )

    def _communicate_progress(self, process: subprocess.Popen[bytes]) -> bytes:
        """Drain both pipes concurrently on Windows; callbacks stay on the caller thread."""
        updates: queue.Queue[int] = queue.Queue(maxsize=1)
        errors: deque[bytes] = deque(maxlen=32)

        def read_progress() -> None:
            assert process.stdout is not None
            for line in iter(process.stdout.readline, b""):
                if not line.startswith(b"out_time_us="):
                    continue
                try:
                    milliseconds = max(0, int(line.partition(b"=")[2].strip()) // 1000)
                except ValueError:
                    continue
                try:
                    updates.get_nowait()
                except queue.Empty:
                    pass
                updates.put_nowait(milliseconds)

        def read_errors() -> None:
            assert process.stderr is not None
            for chunk in iter(lambda: process.stderr.read(4096), b""):
                errors.append(chunk)

        readers = [
            threading.Thread(target=read_progress, daemon=True),
            threading.Thread(target=read_errors, daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            while (
                process.poll() is None
                or any(reader.is_alive() for reader in readers)
                or not updates.empty()
            ):
                if self._cancel_requested.is_set():
                    self._terminate(process)
                    raise FFmpegCancelled("Generation cancelled.")
                try:
                    current_ms = updates.get(timeout=0.05)
                except queue.Empty:
                    continue
                if self.progress_callback:
                    self.progress_callback(current_ms)
        finally:
            if process.poll() is None:
                self._terminate(process)
            for reader in readers:
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        return b"".join(errors)

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

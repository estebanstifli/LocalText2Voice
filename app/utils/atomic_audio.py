"""Publish generated audio without deleting the last usable file first."""
from __future__ import annotations

import os
import tempfile
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class AudioFileInUseError(PermissionError):
    """A validated replacement is retained for recovery after a sharing violation."""


def replace_audio_file(
    source: Path,
    destination: Path,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    # Run in a generation worker, never on the GUI thread. Qt may release media
    # handles asynchronously; retry publication rather than expensive synthesis.
    for attempt in range(11):
        if check_cancelled is not None:
            check_cancelled()
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            # MoveFileEx may report ACCESS_DENIED (5), rather than 32, when an
            # existing destination is open without FILE_SHARE_DELETE.
            if getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            if attempt == 10:
                raise AudioFileInUseError(
                    f"Windows could not replace audio file '{destination}'. "
                    "Stop playback or close the application using it, check folder "
                    "write permissions, then retry. "
                    "The previous audio is unchanged."
                ) from exc
            time.sleep(0.1)


def validate_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        if frames <= 0:
            raise ValueError(f"Generated WAV is empty: {path}")
        audio.setpos(frames - 1)
        if len(audio.readframes(1)) != audio.getnchannels() * audio.getsampwidth():
            raise ValueError(f"Generated WAV is truncated: {path}")


@contextmanager
def atomic_wav_output(
    destination: Path,
    check_cancelled: Callable[[], None] | None = None,
) -> Iterator[Path]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".pending.wav", dir=destination.parent,
    )
    os.close(descriptor)  # Windows engines must be able to reopen/remove this file.
    temporary = Path(name)
    retain = False
    try:
        if check_cancelled is not None:
            check_cancelled()
        yield temporary
        validate_wav(temporary)
        try:
            replace_audio_file(temporary, destination, check_cancelled)
        except AudioFileInUseError as exc:
            retain = True
            raise AudioFileInUseError(
                f"{exc} New audio is saved at '{temporary}'."
            ) from exc
    finally:
        if not retain:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Never mask the synthesis/cancellation error with a cleanup error.
                pass

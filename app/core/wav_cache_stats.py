"""Read-only audio cache accounting, independent of the desktop widgets."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audiobook_store import AudiobookStore


@dataclass(frozen=True)
class WavCacheStats:
    current: tuple[int, int]
    all_projects: tuple[int, int]
    temporary: tuple[int, int]


def temporary_audio_directories(
    root: Path, active_dirs: tuple[Path, ...] = (),
) -> list[Path]:
    active = set()
    for path in active_dirs:
        try:
            active.add(path.resolve())
        except OSError:
            continue
    directories = []
    try:
        for child in root.iterdir():
            # Filter names before requesting filesystem metadata.
            if not child.name.startswith("local_text_2_voice_"):
                continue
            try:
                if child.is_dir() and child.resolve() not in active:
                    directories.append(child)
            except OSError:
                continue
    except OSError:
        pass
    return sorted(directories)


def directory_file_stats(directory: Path) -> tuple[int, int]:
    count = size = 0
    try:
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    size += path.stat().st_size
                    count += 1
            except OSError:
                continue
    except OSError:
        pass
    return count, size


def scan_wav_cache(
    store: AudiobookStore,
    current_id: int | None,
    temp_root: Path,
    active_dirs: tuple[Path, ...],
) -> WavCacheStats:
    current = store.get_audiobook(current_id) or store.latest_audiobook()
    current_stats = store.segment_wav_cache_stats(current.id) if current else (0, 0)
    all_stats = store.segment_wav_cache_stats()
    count = size = 0
    for directory in temporary_audio_directories(temp_root, active_dirs):
        directory_count, directory_size = directory_file_stats(directory)
        count += directory_count
        size += directory_size
    return WavCacheStats(current_stats, all_stats, (count, size))

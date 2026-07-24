from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def huggingface_repo_cache_name(repo_id: str) -> str:
    """Return the on-disk Hugging Face cache directory for a repository."""

    parts = [part for part in repo_id.strip().split("/") if part]
    return "models--" + "--".join(parts)


def huggingface_snapshot_dirs(cache_dir: Path, repo_id: str) -> tuple[Path, ...]:
    """Find snapshots without consulting an install manifest or network service."""

    repository_name = huggingface_repo_cache_name(repo_id)
    snapshots: list[Path] = []
    seen: set[Path] = set()
    for cache_root in (cache_dir / "hub", cache_dir / "models", cache_dir):
        snapshots_dir = cache_root / repository_name / "snapshots"
        if not snapshots_dir.is_dir():
            continue
        try:
            candidates = tuple(snapshots_dir.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved in seen:
                continue
            seen.add(resolved)
            snapshots.append(candidate)
    return tuple(snapshots)


def huggingface_model_is_cached(
    cache_dir: Path,
    repo_id: str,
    required_files: Mapping[str, int],
) -> bool:
    """Check that a physical snapshot contains all expected model assets.

    Minimum sizes keep interrupted downloads and empty placeholders from being
    treated as installed. Install manifests are deliberately ignored because
    they can become stale when the application is rebuilt or moved.
    """

    if not required_files:
        return False
    for snapshot_dir in huggingface_snapshot_dirs(cache_dir, repo_id):
        if all(
            _file_meets_minimum_size(snapshot_dir / relative_path, minimum_size)
            for relative_path, minimum_size in required_files.items()
        ):
            return True
    return False


def huggingface_cached_files(
    cache_dir: Path,
    repo_id: str,
) -> tuple[tuple[str, int], ...]:
    """Return readable model files and their physical sizes for progress logs."""

    rows: list[tuple[str, int]] = []
    seen: set[str] = set()
    for snapshot_dir in huggingface_snapshot_dirs(cache_dir, repo_id):
        try:
            candidates = sorted(snapshot_dir.rglob("*"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(snapshot_dir).as_posix()
                size = candidate.stat().st_size
            except (OSError, ValueError):
                continue
            if relative in seen:
                continue
            seen.add(relative)
            rows.append((relative, size))
    return tuple(rows)


def format_file_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(max(0, size))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{size} B"


def _file_meets_minimum_size(path: Path, minimum_size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= max(1, minimum_size)
    except OSError:
        return False

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.utils.paths import application_root


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
}

LIBRARY_SETTINGS = {
    "music": ("music_library_dir", "music/background"),
    "sfx": ("sfx_library_dir", "music/sfx"),
}


def library_directory(
    settings: Mapping[str, Any],
    library: str,
    *,
    root: Path | None = None,
    create: bool = False,
) -> Path:
    setting_key, default = LIBRARY_SETTINGS[library]
    raw = str(settings.get(setting_key, default) or default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (root or application_root()) / path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def audio_library_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
        ),
        key=lambda path: str(path.relative_to(directory)).casefold(),
    )


def resolve_audio_reference(
    file_reference: str,
    settings: Mapping[str, Any],
    *,
    project_dir: Path | None = None,
    root: Path | None = None,
) -> Path | None:
    """Resolve PLAY assets, recursively searching both libraries for bare names."""

    app_root = (root or application_root()).resolve()
    reference = Path(file_reference).expanduser()
    music_dir = library_directory(settings, "music", root=app_root)
    sfx_dir = library_directory(settings, "sfx", root=app_root)

    if reference.is_absolute():
        return reference.resolve() if reference.is_file() else None

    exact_roots = [path for path in (project_dir, app_root, sfx_dir, music_dir) if path]
    for candidate_root in exact_roots:
        candidate = candidate_root / reference
        if candidate.is_file():
            return candidate.resolve()

    # A path component means the author deliberately selected a location. Only
    # basename-only references receive the convenient recursive library search.
    if reference.parent != Path("."):
        return None

    wanted = reference.name.casefold()
    for directory in (sfx_dir, music_dir):
        for path in audio_library_files(directory):
            if path.name.casefold() == wanted:
                return path.resolve()
    return None

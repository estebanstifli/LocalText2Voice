from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ASSETS_DIRECTORY_NAME = "data"
ASSETS_LOCATION_FILENAME = "assets-location.txt"
ASSETS_MARKER_FILENAME = ".localtext2voice-assets.json"


def application_root() -> Path:
    """Return the portable application directory in source and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Return the directory containing bundled read-only resources."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root) if bundle_root else application_root()


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return application_root() / path


def relative_to_app(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(application_root()))
    except ValueError:
        return str(resolved)


def app_data_root() -> Path:
    """Return writable per-user app data for optional models and caches."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "LocalText2Voice"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LocalText2Voice"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "LocalText2Voice"


def configured_assets_base_dir() -> Path | None:
    """Return the user-selected storage folder without importing settings code."""
    override = os.environ.get("LOCALTEXT2VOICE_ASSETS_BASE_DIR", "").strip()
    if override:
        return resolve_app_path(override).resolve()

    config_path = application_root() / "config.json"
    if not config_path.exists():
        return application_root().resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return application_root().resolve()
    if not isinstance(config, dict):
        return None
    storage = config.get("storage")
    if not isinstance(storage, dict):
        return None
    value = str(storage.get("base_dir", "") or "").strip()
    return resolve_app_path(value).resolve() if value else None


def previous_assets_roots() -> tuple[Path, ...]:
    config_path = application_root() / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    storage = config.get("storage") if isinstance(config, dict) else None
    values = storage.get("previous_roots", []) if isinstance(storage, dict) else []
    if not isinstance(values, list):
        return ()
    return tuple(
        resolve_app_path(value).resolve()
        for value in values
        if isinstance(value, str) and value.strip()
    )


def large_assets_root() -> Path:
    """Return the root used by downloadable AI assets.

    Existing configurations can keep the historical LOCALAPPDATA location by
    storing an empty base directory. New configurations use the application
    folder and place managed assets in its ``data`` child, so choosing
    ``D:\\LocalText2Voice`` produces ``D:\\LocalText2Voice\\data``.
    """
    configured = configured_assets_base_dir()
    if configured is None:
        return app_data_root()
    return configured / ASSETS_DIRECTORY_NAME


def models_root() -> Path:
    return large_assets_root() / "models"


def voice_gallery_root() -> Path:
    return large_assets_root() / "voice-gallery"


def downloads_root() -> Path:
    return large_assets_root() / "downloads"


def resolve_large_asset_path(value: str | Path) -> Path:
    """Resolve a stored asset path after moving the managed data directory."""
    path = Path(value).expanduser()
    if path.exists():
        return path
    if configured_assets_base_dir() is None:
        return path
    candidates = (app_data_root().resolve(), *previous_assets_roots())
    for previous_root in candidates:
        try:
            relative = path.resolve().relative_to(previous_root)
        except (OSError, ValueError):
            continue
        relocated = large_assets_root() / relative
        if relocated.exists():
            return relocated
    return path


def legacy_engine_dependencies_root() -> Path:
    bundled = application_root() / "runtimes" / "python311"
    if (bundled / "python" / "python.exe").is_file():
        return bundled / "engine-deps"
    return app_data_root() / "runtimes" / "python311" / "engine-deps"


def engine_dependencies_root() -> Path:
    if configured_assets_base_dir() is None:
        return legacy_engine_dependencies_root()
    return large_assets_root() / "engine-deps"


def assets_location_file() -> Path:
    """A tiny locator used by the Windows uninstaller without parsing JSON."""
    return application_root() / ASSETS_LOCATION_FILENAME


def write_assets_location_file(root: Path | None = None) -> None:
    destination = assets_location_file()
    resolved = (root or large_assets_root()).resolve()
    try:
        temporary = destination.with_suffix(".txt.tmp")
        temporary.write_text(str(resolved), encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        # Settings remain authoritative. The locator only improves uninstall.
        return


def ensure_assets_marker(root: Path | None = None) -> Path:
    assets_root = (root or large_assets_root()).resolve()
    assets_root.mkdir(parents=True, exist_ok=True)
    marker = assets_root / ASSETS_MARKER_FILENAME
    if marker.is_file():
        return marker
    temporary = marker.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "application": "LocalText2Voice",
                "managed_assets": True,
                "schema_version": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(marker)
    return marker

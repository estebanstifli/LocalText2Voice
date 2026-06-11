from __future__ import annotations

import sys
from pathlib import Path


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

from __future__ import annotations

import json
from pathlib import Path

from app.utils.paths import application_root


def huggingface_runtime_environment(
    environment: dict[str, str], config_path: Path | None = None
) -> dict[str, str]:
    """Apply the saved token to a child process without changing global state.

    Read on every launch so an already-running Engine Host sees settings saved
    by the desktop UI. An empty setting preserves external HF authentication.
    """
    env = dict(environment)
    path = config_path if config_path is not None else application_root() / "config.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return env
    token = settings.get("hf_token") if isinstance(settings, dict) else None
    if isinstance(token, str) and token.strip():
        env["HF_TOKEN"] = token.strip()
    return env

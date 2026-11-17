"""Identify synthesis inputs without confusing export settings with speech inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def render_signature(text: str, voice_config: dict[str, Any]) -> str:
    def snapshot(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): snapshot(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [snapshot(item) for item in value]
        if isinstance(value, (str, Path)):
            # Reference recordings and local model files can change in place.
            try:
                path = Path(value)
                if path.is_file():
                    stat = path.stat()
                    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
            except (OSError, ValueError):
                pass
            return str(value)
        return value

    payload = json.dumps(
        [1, text, snapshot(voice_config)], sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

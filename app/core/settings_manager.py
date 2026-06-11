from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.utils.paths import application_root


DEFAULT_SETTINGS: dict[str, Any] = {
    "ui_language": "en",
    "output_dir": "output",
    "voice_id": "",
    "language": "",
    "speed": 1.0,
    "split_mode": "safe_chunks",
    "export_mode": "single",
    "piper_path": "engines/piper/piper.exe",
    "ffmpeg_path": "ffmpeg/ffmpeg.exe",
    "chunk_size": 2500,
    "pause_between_blocks_ms": 350,
    "pause_between_chapters_ms": 900,
    "paragraph_pause_min_ms": 450,
    "paragraph_pause_max_ms": 900,
    "adaptive_paragraph_pause": True,
    "paragraph_length_reference_chars": 600,
    "paragraph_length_extra_ms": 650,
    "periodic_pause_every_paragraphs": 5,
    "periodic_pause_min_ms": 350,
    "periodic_pause_max_ms": 750,
    "normalize_audio": False,
    "podcast_enabled": False,
    "intro_enabled": False,
    "intro_path": "",
    "background_enabled": False,
    "background_path": "",
    "background_loop": True,
    "background_volume_percent": 12,
    "outro_enabled": False,
    "outro_path": "",
    "music_fade_in_seconds": 1.5,
    "music_fade_out_seconds": 2.0,
    "podcast_gap_ms": 500,
    "podcast_normalize": True,
    "podcast_ducking": True,
    "open_output_on_finish": True,
    "mp3_bitrate": "128k",
    "metadata": {
        "title": "Course",
        "artist": "",
        "album": "LocalText2Voice",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class SettingsManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or application_root() / "config.json"
        self.settings = self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_SETTINGS)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("The configuration root must be a JSON object.")
            return _deep_merge(DEFAULT_SETTINGS, loaded)
        except (OSError, json.JSONDecodeError, ValueError):
            return deepcopy(DEFAULT_SETTINGS)

    def save(self, values: dict[str, Any] | None = None) -> None:
        if values is not None:
            self.settings = _deep_merge(DEFAULT_SETTINGS, values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def update(self, values: dict[str, Any]) -> None:
        self.settings = _deep_merge(self.settings, values)

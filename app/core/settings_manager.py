from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.utils.paths import application_root

CURRENT_SETTINGS_SCHEMA_VERSION = 11
MIN_CHUNK_SIZE = 1


DEFAULT_SETTINGS: dict[str, Any] = {
    "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
    "ui_language": "en",
    "current_project_id": None,
    "output_dir": "output",
    "voice_id": "",
    "language": "",
    "tts_engine": "piper",
    "speed": 1.0,
    "split_mode": "safe_chunks",
    "export_mode": "single",
    "piper_path": "engines/piper/piper.exe",
    "ffmpeg_path": "ffmpeg/ffmpeg.exe",
    "chunk_size": 2500,
    "engine_chunk_sizes": {
        "piper": 0,
        "kokoro": 0,
        "chatterbox": 0,
        "qwen": 0,
        "omnivoice": 0,
    },
    "editor_syntax_highlighting": True,
    "show_markup_toolbar": True,
    "markup_corrector_enabled": True,
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
    "background_enabled": True,
    "background_path": "music/background/relax1.mp3",
    "music_library_dir": "music/background",
    "sfx_library_dir": "music/sfx",
    "background_loop": True,
    "background_volume_percent": 45,
    "voice_volume_db": 0.0,
    "music_volume_db": -7.0,
    "voice_start_offset_ms": 2000,
    "music_tail_ms": 2000,
    "music_fade_in_seconds": 1.0,
    "music_fade_out_seconds": 1.0,
    "podcast_gap_ms": 500,
    "podcast_normalize": True,
    "podcast_ducking": True,
    "ducking_strength": "low",
    "markup_music_volume_db": 0.0,
    "ambient_volume_db": 0.0,
    "sfx_volume_db": 0.0,
    "voice_muted": False,
    "background_music_muted": False,
    "markup_music_muted": False,
    "ambient_muted": False,
    "sfx_muted": False,
    "markup_audio_solo_track": "",
    "auto_delete_segment_wavs_after_mix": False,
    "open_output_on_finish": False,
    "mp3_bitrate": "128k",
    "metadata": {
        "title": "Course",
        "artist": "",
        "album": "LocalText2Voice",
    },
    "kokoro": {
        "voice": "af_heart",
        "lang": "en-us",
        "provider": "auto",
    },
    "chatterbox": {
        "model": "multilingual_v3",
        "language": "en",
        "device": "auto",
        "reference_audio_path": "",
        "exaggeration": 0.5,
        "cfg_weight": 0.5,
    },
    "qwen": {
        "model": "custom_voice_0_6b",
        "language": "Spanish",
        "speaker": "Serena",
        "device": "auto",
        "dtype": "auto",
        "instruct": "",
    },
    "omnivoice": {
        "model": "omnivoice",
        "mode": "clone",
        "language": "auto",
        "device": "auto",
        "dtype": "auto",
        "instruct": "",
        "reference_audio_path": "",
        "reference_text": "",
        "num_step": 32,
        "speed": 1.0,
        "duration": 0.0,
    },
    "review": {
        "enabled": False,
        "auto_verify_after_generation": False,
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "language": "auto",
        "beam_size": 1,
        "approve_threshold": 92.0,
        "max_retries": 0,
        "preload_model": False,
    },
    "local_server": {
        "enabled": False,
        "auto_start": False,
        "host": "127.0.0.1",
        "port": 8765,
        "auth_token": "",
        "allow_lan": False,
        "serve_files": True,
        "max_parallel_jobs": 1,
    },
    "api_tts": {
        "openai": {
            "api_key": "",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "instructions": "",
            "timeout_seconds": 120,
        },
        "elevenlabs": {
            "api_key": "",
            "voice_id": "",
            "model_id": "eleven_flash_v2_5",
            "output_format": "pcm_24000",
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "timeout_seconds": 120,
        },
        "gemini": {
            "api_key": "",
            "model": "gemini-3.1-flash-tts-preview",
            "voice": "Kore",
            "prompt": "",
            "timeout_seconds": 180,
        },
        "azure": {
            "api_key": "",
            "region": "",
            "voice": "en-US-JennyNeural",
            "output_format": "riff-24khz-16bit-mono-pcm",
            "style": "",
            "timeout_seconds": 120,
        },
    },
    "custom_tts_engines": [],
    "voice_gallery": {
        "catalog_url": (
            "https://raw.githubusercontent.com/estebanstifli/"
            "LocalText2Voice-VoiceGallery/main/catalog.json"
        ),
        "local_catalog_path": "",
        "auto_sync": False,
        "last_sync_at": "",
    },
    "updates": {
        "auto_check": True,
        "last_checked_at": 0,
    },
    "installer_setup": {
        "profile": "",
        "pending_installs": [],
        "completed": False,
        "completed_at": "",
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


def _settings_version(settings: dict[str, Any]) -> int:
    try:
        return int(settings.get("settings_schema_version", 1))
    except (TypeError, ValueError):
        return 1


def _migrate_settings(
    settings: dict[str, Any],
    source_version: int | None = None,
) -> dict[str, Any]:
    result = deepcopy(settings)
    version = _settings_version(result) if source_version is None else source_version

    if version < 2:
        chatterbox = result.get("chatterbox")
        if isinstance(chatterbox, dict) and chatterbox.get("device") == "cuda":
            # Older builds defaulted to strict CUDA, which failed on normal PCs.
            # Auto still uses CUDA when available, but falls back to CPU.
            chatterbox["device"] = "auto"

    if version < 4:
        if not result.get("background_path"):
            result["background_path"] = "music/background/relax1.mp3"
            result["background_enabled"] = True
        if result.get("music_volume_db") == -18.0:
            result["music_volume_db"] = -7.0
        if result.get("background_volume_percent") == 12:
            result["background_volume_percent"] = 45
        if result.get("voice_start_offset_ms") == 0:
            result["voice_start_offset_ms"] = 2000
        if result.get("music_tail_ms") == 0:
            result["music_tail_ms"] = 2000
        if result.get("music_fade_in_seconds") == 1.5:
            result["music_fade_in_seconds"] = 1.0
        if result.get("music_fade_out_seconds") == 2.0:
            result["music_fade_out_seconds"] = 1.0
        if result.get("ducking_strength") == "medium":
            result["ducking_strength"] = "low"

    _sanitize_chunk_sizes(result)
    result["settings_schema_version"] = CURRENT_SETTINGS_SCHEMA_VERSION
    return result


def _sanitize_chunk_sizes(settings: dict[str, Any]) -> None:
    try:
        chunk_size = int(settings.get("chunk_size", DEFAULT_SETTINGS["chunk_size"]))
    except (TypeError, ValueError):
        chunk_size = int(DEFAULT_SETTINGS["chunk_size"])
    settings["chunk_size"] = max(MIN_CHUNK_SIZE, chunk_size)

    engine_sizes = settings.get("engine_chunk_sizes")
    if not isinstance(engine_sizes, dict):
        engine_sizes = {}
    sanitized: dict[str, int] = {}
    defaults = DEFAULT_SETTINGS.get("engine_chunk_sizes", {})
    if isinstance(defaults, dict):
        engines = set(defaults.keys()) | set(engine_sizes.keys())
    else:
        engines = set(engine_sizes.keys())
    for engine in engines:
        try:
            value = int(engine_sizes.get(engine, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        sanitized[str(engine)] = 0 if value <= 0 else max(MIN_CHUNK_SIZE, value)
    settings["engine_chunk_sizes"] = sanitized


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
            return _migrate_settings(
                _deep_merge(DEFAULT_SETTINGS, loaded),
                _settings_version(loaded),
            )
        except (OSError, json.JSONDecodeError, ValueError):
            return deepcopy(DEFAULT_SETTINGS)

    def save(self, values: dict[str, Any] | None = None) -> None:
        if values is not None:
            self.settings = _migrate_settings(_deep_merge(DEFAULT_SETTINGS, values))
        else:
            self.settings = _migrate_settings(self.settings)
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

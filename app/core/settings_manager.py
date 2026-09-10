from __future__ import annotations

from app.core.storyboard_profiles import RUNPOD_DEFAULTS, PROFILE_IDS, infer_profile

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.audio_formats import (
    AUDIO_FORMATS,
    legacy_mp3_bitrate_quality,
    normalize_audio_format,
    normalize_audio_quality,
)
from app.core.text_normalization import (
    DEFAULT_NORMALIZATION_RULES,
    normalization_rule_settings,
)
from app.utils.paths import (
    application_root,
    ensure_assets_marker,
    large_assets_root,
    write_assets_location_file,
)
from app.core.video_storyboard_styles import normalize_storyboard_style_id
from app.core.storyboard_analysis_settings import defaults as continuity_defaults, normalize as normalize_continuity_settings

CURRENT_SETTINGS_SCHEMA_VERSION = 30
MIN_CHUNK_SIZE = 50
MAX_CHUNK_SIZE = 5000

SUPPORTED_UI_LANGUAGES = {
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "it",
    "ja",
    "pt",
    "ru",
    "zh",
}
SUPPORTED_UI_THEMES = {"light", "dark"}
SUPPORTED_SPLIT_MODES = {"safe_chunks", "chapters"}
SUPPORTED_EXPORT_MODES = {"single"}


DEFAULT_SETTINGS: dict[str, Any] = {
    "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
    "ui_language": "en",
    "ui_theme": "light",
    "gpu_device_index": "auto",
    "current_project_id": None,
    "storage": {
        # New portable runs use <application>/data. Schema migration writes an
        # empty value for older configurations so their AppData models remain.
        "base_dir": ".",
        "previous_roots": [],
    },
    "output_dir": "output",
    "projects_dir": "projects",
    "voice_id": "",
    "language": "",
    "tts_engine": "piper",
    "speed": 1.0,
    "split_mode": "safe_chunks",
    "export_mode": "single",
    "audio_format": "mp3",
    "audio_quality": "standard",
    "piper_path": "engines/piper/piper.exe",
    "ffmpeg_path": "ffmpeg/ffmpeg.exe",
    "chunk_size": 300,
    "engine_chunk_sizes": {
        "piper": 300,
        "kokoro": 300,
        "chatterbox": 300,
        "qwen": 520,
        "omnivoice": 300,
        "f5_russian": 300,
    },
    "editor_syntax_highlighting": True,
    "show_markup_toolbar": True,
    "markup_corrector_enabled": True,
    "text_normalization": {
        "enabled": False,
        "language": "auto",
        "rules": dict(DEFAULT_NORMALIZATION_RULES),
        "russian_silero": {"enabled": False},
    },
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
        "title": "Project1",
        "artist": "",
        "album": "LocalText2Voice",
    },
    "book_metadata": {
        "title": "Project1",
        "title_follows_project": True,
        "subtitle": "",
        "author": "",
        "narrator": "",
        "series": "",
        "series_index": "",
        "language": "",
        "genre": "",
        "publisher": "",
        "publication_date": "",
        "description": "",
        "copyright": "",
        "isbn": "",
        "cover_mode": "auto",
        "cover_path": "assets/cover.jpg",
        "chapter_mode": "markup"
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
        "reference_audio_path": "",
        "reference_text": "",
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
    "f5_russian": {
        "model": "f5tts_v1_base_v2",
        "device": "auto",
        "reference_audio_path": "",
        "reference_text": "",
        "use_stress": True,
        "nfe_step": 32,
        "speed": 1.0,
        "remove_silence": True,
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
        "tail_analysis_enabled": False,
        "tail_autocut_enabled": False,
        "tail_safety_margin_seconds": 0.40,
        "tail_warning_threshold_seconds": 0.50,
        "tail_failure_threshold_seconds": 1.00,
    },
    "internal_engine_host_port": 8765,
    "remote_mcp_port": 8766,
    "local_server": {
        "auth_token": "",
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
    "video_storyboard": {
        "continuity_analysis": continuity_defaults(),
        "enabled": False,
        "installation": {"comfy_root": "", "comfy_python": ""},
        "active_profile": "local",
        "profiles": {},
        "video_provider": "comfyui",
        "runpod": deepcopy(RUNPOD_DEFAULTS),
        "image_provider": "comfyui",
        "image_edit_provider": "disabled",
        "llm_provider": "ollama",
        "scene": {
            "mode": "semantic_bounded",
            "minimum_seconds": 4,
            "target_seconds": 8,
            "maximum_seconds": 20,
        },
        "image": {
            "width": 1280,
            "height": 720,
            "batch_size": 1,
            "steps": 8,
            "cfg": 1.0,
            "sampler": "res_multistep",
            "scheduler": "simple",
            "denoise": 1.0,
            "auraflow_shift": 3.0,
            "style_mode": "comic_book",
            "style_prompt": "",
            "seed_mode": "audiobook_locked",
        },
        "comfyui": {
            "output_node": "",
            "auth_token": "",
            "bindings": {name: "" for name in ("prompt", "negative_prompt", "seed", "width", "height", "steps", "cfg", "output_prefix")},
            "base_url": "http://127.0.0.1:8188",
            "workflow_path": "",
            "diffusion_model": "z_image_turbo_bf16.safetensors",
            "text_encoder": "qwen_3_4b.safetensors",
            "vae_model": "ae.safetensors",
            "timeout_seconds": 900,
        },
        "comfyui_video": {
            "output_node": "",
            "base_url": "http://127.0.0.1:8188",
            "auth_token": "",
            "workflow_profile": "wan22_rapid",
            "workflow_path": "",
            "unet_model": "wan2.2-i2v-rapid-aio-v10-Q4_K.gguf",
            "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "vae_model": "wan_2.1_vae.safetensors",
            "clip_vision_model": "clip_vision_h.safetensors",
            "width": 640,
            "height": 360,
            "frames": 49,
            "wan_behavior": "split",
            "steps": 4,
            "fps": 24,
            "ltx_prompt_enhance": False,
            "bindings": {
                "image": "",
                "prompt": "",
                "seed": "",
                "width": "",
                "height": "",
                "duration": "",
                "fps": "",
                "frame_count": "",
                "output_prefix": "",
            },
            "timeout_seconds": 1800,
        },
        "litellm_image": {
            "base_url": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": 300,
        },
        "comfyui_image_edit": {
            "base_url": "http://127.0.0.1:8188",
            "auth_token": "",
            "workflow_path_1": "",
            "workflow_path_2": "",
            "workflow_path_3": "",
            "unet_model": "Qwen-Image-Edit-2509-Q3_K_S.gguf",
            "lora_model": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
            "camera_lora_model": "镜头转换.safetensors",
            "camera_lora_strength": 1.0,
            "text_encoder": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "vae_model": "qwen_image_vae.safetensors",
            "width": 1280,
            "height": 720,
            "steps": 4,
            "cfg": 1.0,
            "timeout_seconds": 1800,
        },
        "litellm_image_edit": {
            "base_url": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": 600,
        },
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3:8b",
            "context_length": 8192,
            "timeout_seconds": 300,
        },
        "litellm": {
            "base_url": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": 300,
            "max_output_tokens": 16000,
        },
        "video": {
            "fps": 30,
            "transition": "fade",
            "transition_seconds": 0.7,
            "zoom_percent": 30.0,
            "supersample": 4,
            "preset": "medium",
            "codec": "libx264",
            "crf": 18,
        },
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

    if version < 17:
        # Keep existing downloaded models visible until the user explicitly
        # moves them from Settings > General.
        result["storage"] = {
            "base_dir": "",
            "previous_roots": [],
        }

    if version < 19:
        legacy_server = result.get("local_server", {})
        legacy_port = (
            legacy_server.get("port", 8765)
            if isinstance(legacy_server, dict)
            else 8765
        )
        result["internal_engine_host_port"] = legacy_port
        result["remote_mcp_port"] = 8766

    if version < 20:
        # Safe chunks is now the only desktop splitting policy. Convert the
        # old implicit/default values to explicit engine limits.
        result["split_mode"] = "safe_chunks"
        if result.get("chunk_size") == 2500:
            result["chunk_size"] = 300
        engine_sizes = result.get("engine_chunk_sizes")
        if not isinstance(engine_sizes, dict):
            engine_sizes = {}
        for engine, size in DEFAULT_SETTINGS["engine_chunk_sizes"].items():
            if not engine_sizes.get(engine):
                engine_sizes[engine] = size
        result["engine_chunk_sizes"] = engine_sizes

    if version < 21:
        # Audio is now always exported as one file. Preserve export_mode as an
        # internal structure setting so old projects and API clients migrate
        # without losing a known key.
        result["export_mode"] = "single"
        result["audio_format"] = normalize_audio_format(
            result.get("audio_format", "mp3")
        )
        result["audio_quality"] = legacy_mp3_bitrate_quality(
            result.get("mp3_bitrate", "128k")
        )

    if version < 24:
        storyboard = result.get("video_storyboard")
        if isinstance(storyboard, dict):
            video = storyboard.get("video")
            if isinstance(video, dict) and video.get("zoom_percent") == 8.0:
                video["zoom_percent"] = 14.0

    if version < 25:
        storyboard = result.get("video_storyboard")
        if isinstance(storyboard, dict):
            video = storyboard.get("video")
            if isinstance(video, dict) and video.get("zoom_percent") == 14.0:
                video["zoom_percent"] = 30.0

    if version < 26:
        storyboard = result.get("video_storyboard")
        litellm = (
            storyboard.get("litellm", {})
            if isinstance(storyboard, dict)
            else {}
        )
        if (
            isinstance(litellm, dict)
            and str(litellm.get("base_url") or "").rstrip("/")
            == "http://127.0.0.1:4000/v1"
            and "/" in str(litellm.get("model") or "")
        ):
            # The old default looked like an explicit proxy selection. A
            # provider-prefixed model is intended for the direct LiteLLM SDK.
            litellm["base_url"] = ""

    if version < 27:
        storyboard = result.get("video_storyboard")
        if isinstance(storyboard, dict):
            legacy = storyboard.get("custom_image")
            if isinstance(legacy, dict):
                storyboard["litellm_image"] = {
                    "base_url": str(legacy.get("url") or ""),
                    "model": "",
                    "api_key": str(legacy.get("api_key") or ""),
                    "timeout_seconds": legacy.get("timeout_seconds", 300),
                }
            if storyboard.get("image_provider") == "custom_http":
                storyboard["image_provider"] = "litellm_image"

    if version < 28:
        storyboard = result.get("video_storyboard")
        if isinstance(storyboard, dict) and not isinstance(
            storyboard.get("comfyui_video"), dict
        ):
            storyboard["comfyui_video"] = deepcopy(
                DEFAULT_SETTINGS["video_storyboard"]["comfyui_video"]
            )

    if version < 29:
        storyboard = result.get("video_storyboard")
        if isinstance(storyboard, dict):
            storyboard.setdefault("image_edit_provider", "disabled")
            for section_name in ("comfyui_image_edit", "litellm_image_edit"):
                if not isinstance(storyboard.get(section_name), dict):
                    storyboard[section_name] = deepcopy(
                        DEFAULT_SETTINGS["video_storyboard"][section_name]
                    )

    invalid_legacy_chunk_size = not _valid_chunk_size(result.get("chunk_size"))
    _sanitize_core_settings(result)
    if (
        version < 12
        and invalid_legacy_chunk_size
        and result.get("ui_language") == "ar"
    ):
        # Builds before schema 12 could save the first UI option (Arabic) and a
        # one-character chunk size while the widgets were only half restored.
        result["ui_language"] = DEFAULT_SETTINGS["ui_language"]
    if version < 30:
        storyboard = result.get("video_storyboard", {})
        if isinstance(storyboard, dict):
            previous = {k: v for k, v in storyboard.items() if k != "active_profile"}
            storyboard["active_profile"] = infer_profile(previous)
    result["settings_schema_version"] = CURRENT_SETTINGS_SCHEMA_VERSION
    return result


def _valid_chunk_size(value: object) -> bool:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return False
    return MIN_CHUNK_SIZE <= parsed <= MAX_CHUNK_SIZE


def sanitize_chunk_size(value: object, fallback: int | None = None) -> int:
    """Return a safe chunk size, falling back instead of silently clamping."""
    if _valid_chunk_size(value):
        return int(value)
    if fallback is not None and _valid_chunk_size(fallback):
        return int(fallback)
    return int(DEFAULT_SETTINGS["chunk_size"])


def _sanitize_choice(
    settings: dict[str, Any],
    key: str,
    allowed: set[str],
) -> None:
    value = settings.get(key)
    if not isinstance(value, str) or value not in allowed:
        settings[key] = DEFAULT_SETTINGS[key]


def _sanitize_core_settings(settings: dict[str, Any]) -> None:
    _sanitize_choice(settings, "ui_language", SUPPORTED_UI_LANGUAGES)
    _sanitize_choice(settings, "ui_theme", SUPPORTED_UI_THEMES)
    _sanitize_choice(settings, "split_mode", SUPPORTED_SPLIT_MODES)
    settings["split_mode"] = "safe_chunks"
    settings["export_mode"] = "single"
    settings["audio_format"] = normalize_audio_format(
        settings.get("audio_format", DEFAULT_SETTINGS["audio_format"])
    )
    settings["audio_quality"] = normalize_audio_quality(
        settings["audio_format"],
        settings.get("audio_quality", DEFAULT_SETTINGS["audio_quality"]),
    )
    if settings["audio_format"] == "mp3":
        quality = AUDIO_FORMATS["mp3"].quality(settings["audio_quality"])
        settings["mp3_bitrate"] = quality.bitrate or "128k"

    gpu_device = str(settings.get("gpu_device_index", "auto")).strip().casefold()
    if gpu_device != "auto":
        try:
            gpu_index = int(gpu_device)
        except (TypeError, ValueError):
            gpu_index = -1
        gpu_device = str(gpu_index) if gpu_index >= 0 else "auto"
    settings["gpu_device_index"] = gpu_device

    def sanitized_port(key: str, fallback: int) -> int:
        try:
            port = int(settings.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return port if 1024 <= port <= 65535 else fallback

    internal_port = sanitized_port("internal_engine_host_port", 8765)
    remote_port = sanitized_port("remote_mcp_port", 8766)
    if remote_port == internal_port:
        remote_port = internal_port + 1 if internal_port < 65535 else internal_port - 1
    settings["internal_engine_host_port"] = internal_port
    settings["remote_mcp_port"] = remote_port

    engine = settings.get("tts_engine")
    if not isinstance(engine, str) or not engine.strip():
        settings["tts_engine"] = DEFAULT_SETTINGS["tts_engine"]

    try:
        speed = float(settings.get("speed", DEFAULT_SETTINGS["speed"]))
    except (TypeError, ValueError):
        speed = float(DEFAULT_SETTINGS["speed"])
    settings["speed"] = (
        speed if 0.5 <= speed <= 2.0 else float(DEFAULT_SETTINGS["speed"])
    )

    for section in (
        "metadata",
        "kokoro",
        "chatterbox",
        "qwen",
        "omnivoice",
        "f5_russian",
        "review",
        "local_server",
        "api_tts",
        "voice_gallery",
        "updates",
        "installer_setup",
        "text_normalization",
        "storage",
        "video_storyboard",
    ):
        if not isinstance(settings.get(section), dict):
            settings[section] = deepcopy(DEFAULT_SETTINGS[section])

    server = settings["local_server"]
    for obsolete_key in ("enabled", "auto_start", "host", "port", "allow_lan"):
        server.pop(obsolete_key, None)

    normalization = settings["text_normalization"]
    normalization["enabled"] = bool(normalization.get("enabled", False))
    language = str(normalization.get("language", "auto")).strip().casefold().replace("_", "-")
    normalization["language"] = (
        language
        if language == "auto"
        or re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*", language)
        else "auto"
    )
    normalization["rules"] = normalization_rule_settings(
        normalization.get("rules")
    )
    russian_silero = normalization.get("russian_silero")
    if not isinstance(russian_silero, dict):
        russian_silero = {}
    normalization["russian_silero"] = {
        "enabled": bool(russian_silero.get("enabled", False)),
    }

    review = settings["review"]
    review["tail_analysis_enabled"] = bool(
        review.get("tail_analysis_enabled", False)
    )
    review["tail_autocut_enabled"] = bool(
        review.get("tail_autocut_enabled", False)
        and review["tail_analysis_enabled"]
    )
    safety = _bounded_float(
        review.get("tail_safety_margin_seconds"),
        0.0,
        2.0,
        0.40,
    )
    warning = _bounded_float(
        review.get("tail_warning_threshold_seconds"),
        0.05,
        10.0,
        0.50,
    )
    failure = _bounded_float(
        review.get("tail_failure_threshold_seconds"),
        warning + 0.05,
        20.0,
        max(1.00, warning + 0.05),
    )
    review["tail_safety_margin_seconds"] = safety
    review["tail_warning_threshold_seconds"] = warning
    review["tail_failure_threshold_seconds"] = failure

    _sanitize_chunk_sizes(settings)

    storage = settings["storage"]
    base_dir = storage.get("base_dir", "")
    storage["base_dir"] = str(base_dir).strip() if base_dir is not None else ""
    previous_roots = storage.get("previous_roots", [])
    if not isinstance(previous_roots, list):
        previous_roots = []
    storage["previous_roots"] = list(
        dict.fromkeys(
            str(path).strip()
            for path in previous_roots
            if str(path).strip()
        )
    )[-8:]

    _sanitize_video_storyboard(settings["video_storyboard"])


def _sanitize_video_storyboard(storyboard: dict[str, Any]) -> None:
    storyboard["continuity_analysis"] = normalize_continuity_settings(storyboard.get("continuity_analysis"))
    defaults = DEFAULT_SETTINGS["video_storyboard"]
    storyboard["enabled"] = bool(storyboard.get("enabled", False))
    storyboard["active_profile"] = infer_profile(storyboard)
    snapshots = storyboard.get("profiles", {})
    storyboard["profiles"] = {k: v for k, v in snapshots.items() if k in PROFILE_IDS and isinstance(v, dict)} if isinstance(snapshots, dict) else {}
    storyboard["video_provider"] = _choice_value(storyboard.get("video_provider"), {"comfyui", "runpod", "disabled"}, "comfyui")
    runpod = storyboard.get("runpod", {})
    runpod = runpod if isinstance(runpod, dict) else {}
    storyboard["runpod"] = {key: str(runpod.get(key) or fallback).strip() if isinstance(fallback, str) else runpod.get(key, fallback) for key, fallback in RUNPOD_DEFAULTS.items()}
    storyboard["runpod"]["timeout_seconds"] = _bounded_int(runpod.get("timeout_seconds"), 60, 7200, 1800)
    storyboard["runpod"]["video_size"] = _choice_value(runpod.get("video_size"), {"1280*720", "1920*1080"}, "1280*720")
    storyboard["runpod"]["reference_storage"] = _choice_value(runpod.get("reference_storage"), {"auto", "disabled", "s3", "managed"}, "auto")
    storyboard["image_provider"] = _choice_value(
        storyboard.get("image_provider"),
        {"comfyui", "custom_comfyui", "litellm_image", "runpod"},
        str(defaults["image_provider"]),
    )
    storyboard["image_edit_provider"] = _choice_value(
        storyboard.get("image_edit_provider"),
        {"disabled", "comfyui", "litellm_image", "runpod"},
        str(defaults["image_edit_provider"]),
    )
    storyboard["llm_provider"] = _choice_value(
        storyboard.get("llm_provider"),
        {"ollama", "litellm"},
        str(defaults["llm_provider"]),
    )

    for section_name in (
        "scene",
        "image",
        "comfyui",
        "comfyui_video",
        "comfyui_image_edit",
        "litellm_image",
        "litellm_image_edit",
        "ollama",
        "litellm",
        "video",
    ):
        if not isinstance(storyboard.get(section_name), dict):
            storyboard[section_name] = deepcopy(defaults[section_name])

    scene = storyboard["scene"]
    scene["mode"] = _choice_value(
        scene.get("mode"),
        {"semantic_bounded", "fixed"},
        "semantic_bounded",
    )
    maximum = _bounded_int(scene.get("maximum_seconds"), 4, 60, 20)
    minimum = _bounded_int(scene.get("minimum_seconds"), 4, maximum, 4)
    target = _bounded_int(
        scene.get("target_seconds"), minimum, maximum, min(maximum, max(minimum, 8))
    )
    scene.update(
        {
            "minimum_seconds": minimum,
            "target_seconds": target,
            "maximum_seconds": maximum,
        }
    )

    image = storyboard["image"]
    image["width"] = _bounded_int(image.get("width"), 256, 4096, 1280)
    image["height"] = _bounded_int(image.get("height"), 256, 4096, 720)
    image["batch_size"] = 1
    image["steps"] = _bounded_int(image.get("steps"), 1, 50, 8)
    image["cfg"] = _bounded_float(image.get("cfg"), 0.0, 20.0, 1.0)
    image["denoise"] = _bounded_float(image.get("denoise"), 0.0, 1.0, 1.0)
    image["auraflow_shift"] = _bounded_float(
        image.get("auraflow_shift"), 0.0, 20.0, 3.0
    )
    image["sampler"] = str(image.get("sampler") or "res_multistep").strip()
    image["scheduler"] = str(image.get("scheduler") or "simple").strip()
    image["style_prompt"] = str(image.get("style_prompt") or "").strip()
    raw_style_mode = str(image.get("style_mode") or "").strip()
    image["style_mode"] = (
        "custom"
        if raw_style_mode == "automatic" and image["style_prompt"]
        else normalize_storyboard_style_id(raw_style_mode or "comic_book")
    )
    image["seed_mode"] = "audiobook_locked"

    comfyui = storyboard["comfyui"]
    comfyui["base_url"] = str(
        comfyui.get("base_url") or "http://127.0.0.1:8188"
    ).strip()
    comfyui["auth_token"] = str(comfyui.get("auth_token") or "").strip()
    bindings = comfyui.get("bindings")
    comfyui["bindings"] = {name: str((bindings if isinstance(bindings, dict) else {}).get(name) or "").strip() for name in defaults["comfyui"]["bindings"]}
    comfyui["workflow_path"] = str(comfyui.get("workflow_path") or "").strip()
    comfyui["diffusion_model"] = str(
        comfyui.get("diffusion_model") or "z_image_turbo_bf16.safetensors"
    ).strip()
    comfyui["text_encoder"] = str(
        comfyui.get("text_encoder") or "qwen_3_4b.safetensors"
    ).strip()
    comfyui["vae_model"] = str(
        comfyui.get("vae_model") or "ae.safetensors"
    ).strip()
    comfyui["timeout_seconds"] = _bounded_int(
        comfyui.get("timeout_seconds"), 30, 7200, 900
    )

    comfyui_video = storyboard["comfyui_video"]
    comfyui_video["base_url"] = str(
        comfyui_video.get("base_url")
        or comfyui.get("base_url")
        or "http://127.0.0.1:8188"
    ).strip()
    comfyui_video["auth_token"] = str(
        comfyui_video.get("auth_token") or ""
    ).strip()
    comfyui_video["workflow_profile"] = _choice_value(
        comfyui_video.get("workflow_profile"),
        {"wan22_rapid", "ltx23_i2v", "custom"},
        "wan22_rapid",
    )
    comfyui_video["workflow_path"] = str(
        comfyui_video.get("workflow_path") or ""
    ).strip()
    comfyui_video["unet_model"] = str(
        comfyui_video.get("unet_model")
        or "wan2.2-i2v-rapid-aio-v10-Q4_K.gguf"
    ).strip()
    comfyui_video["text_encoder"] = str(
        comfyui_video.get("text_encoder")
        or "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
    ).strip()
    comfyui_video["vae_model"] = str(
        comfyui_video.get("vae_model") or "wan_2.1_vae.safetensors"
    ).strip()
    comfyui_video["clip_vision_model"] = str(
        comfyui_video.get("clip_vision_model") or "clip_vision_h.safetensors"
    ).strip()
    comfyui_video["width"] = _bounded_int(
        comfyui_video.get("width"), 64, 4096, 640
    )
    comfyui_video["height"] = _bounded_int(
        comfyui_video.get("height"), 64, 4096, 360
    )
    if comfyui_video.get("wan_behavior") not in {"split", "stretch"}:
        comfyui_video["wan_behavior"] = "split"
    comfyui_video["frames"] = _bounded_int(
        comfyui_video.get("frames"), 1, 1000, 49
    )
    comfyui_video["steps"] = _bounded_int(
        comfyui_video.get("steps"), 1, 100, 4
    )
    comfyui_video["fps"] = _bounded_int(
        comfyui_video.get("fps"), 1, 120, 24
    )
    comfyui_video["ltx_prompt_enhance"] = bool(
        comfyui_video.get("ltx_prompt_enhance", False)
    )
    raw_bindings = comfyui_video.get("bindings")
    raw_bindings = raw_bindings if isinstance(raw_bindings, dict) else {}
    comfyui_video["bindings"] = {
        name: str(raw_bindings.get(name) or "").strip()
        for name in (
            "image",
            "prompt",
            "seed",
            "width",
            "height",
            "duration",
            "fps",
            "frame_count",
            "output_prefix",
        )
    }
    comfyui_video["timeout_seconds"] = _bounded_int(
        comfyui_video.get("timeout_seconds"), 30, 7200, 1800
    )

    litellm_image = storyboard["litellm_image"]
    litellm_image["base_url"] = str(
        litellm_image.get("base_url") or ""
    ).strip()
    litellm_image["model"] = str(litellm_image.get("model") or "").strip()
    litellm_image["api_key"] = str(
        litellm_image.get("api_key") or ""
    ).strip()
    litellm_image["timeout_seconds"] = _bounded_int(
        litellm_image.get("timeout_seconds"), 10, 3600, 300
    )

    comfyui_image_edit = storyboard["comfyui_image_edit"]
    comfyui_image_edit["base_url"] = str(
        comfyui_image_edit.get("base_url")
        or comfyui.get("base_url")
        or "http://127.0.0.1:8188"
    ).strip()
    comfyui_image_edit["auth_token"] = str(
        comfyui_image_edit.get("auth_token") or ""
    ).strip()
    for field in ("workflow_path_1", "workflow_path_2", "workflow_path_3"):
        comfyui_image_edit[field] = str(
            comfyui_image_edit.get(field) or ""
        ).strip()
    for field, fallback in (
        ("unet_model", "Qwen-Image-Edit-2509-Q3_K_S.gguf"),
        ("lora_model", "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors"),
        ("camera_lora_model", "镜头转换.safetensors"),
        ("text_encoder", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
        ("vae_model", "qwen_image_vae.safetensors"),
    ):
        comfyui_image_edit[field] = str(
            comfyui_image_edit.get(field) or fallback
        ).strip()
    comfyui_image_edit["camera_lora_strength"] = _bounded_float(
        comfyui_image_edit.get("camera_lora_strength"), 0.1, 2.0, 1.0
    )
    comfyui_image_edit["width"] = _bounded_int(
        comfyui_image_edit.get("width"), 256, 4096, 1280
    )
    comfyui_image_edit["height"] = _bounded_int(
        comfyui_image_edit.get("height"), 256, 4096, 720
    )
    comfyui_image_edit["steps"] = _bounded_int(
        comfyui_image_edit.get("steps"), 1, 100, 4
    )
    comfyui_image_edit["cfg"] = _bounded_float(
        comfyui_image_edit.get("cfg"), 0.0, 20.0, 1.0
    )
    comfyui_image_edit["timeout_seconds"] = _bounded_int(
        comfyui_image_edit.get("timeout_seconds"), 30, 7200, 1800
    )

    litellm_image_edit = storyboard["litellm_image_edit"]
    litellm_image_edit["base_url"] = str(
        litellm_image_edit.get("base_url") or ""
    ).strip()
    litellm_image_edit["model"] = str(
        litellm_image_edit.get("model") or ""
    ).strip()
    litellm_image_edit["api_key"] = str(
        litellm_image_edit.get("api_key") or ""
    ).strip()
    litellm_image_edit["timeout_seconds"] = _bounded_int(
        litellm_image_edit.get("timeout_seconds"), 10, 3600, 600
    )

    for section_name, fallback_url in (
        ("ollama", "http://127.0.0.1:11434"),
        ("litellm", ""),
    ):
        provider = storyboard[section_name]
        provider["base_url"] = str(provider.get("base_url") or fallback_url).strip()
        provider["model"] = str(provider.get("model") or "").strip()
        provider["timeout_seconds"] = _bounded_int(
            provider.get("timeout_seconds"), 10, 3600, 300
        )
    storyboard["litellm"]["api_key"] = str(
        storyboard["litellm"].get("api_key") or ""
    ).strip()
    storyboard["litellm"]["max_output_tokens"] = _bounded_int(
        storyboard["litellm"].get("max_output_tokens"), 512, 131072, 16000
    )
    storyboard["ollama"]["context_length"] = _bounded_int(
        storyboard["ollama"].get("context_length"), 2048, 131072, 8192
    )
    storyboard.pop("local_z_image", None)
    storyboard.pop("local_qwen", None)
    storyboard.pop("custom_image", None)

    video = storyboard["video"]
    video["fps"] = _bounded_int(video.get("fps"), 12, 60, 30)
    video["transition"] = _choice_value(
        video.get("transition"),
        {
            "fade", "dissolve", "wipeleft", "wiperight",
            "smoothleft", "smoothright", "circleopen", "circleclose",
        },
        "fade",
    )
    video["transition_seconds"] = _bounded_float(
        video.get("transition_seconds"), 0.0, 3.0, 0.7
    )
    video["zoom_percent"] = _bounded_float(
        video.get("zoom_percent"), 0.0, 1_000_000.0, 30.0
    )
    video["supersample"] = 4
    video["preset"] = "medium"
    video["codec"] = "libx264"
    video["crf"] = _bounded_int(video.get("crf"), 0, 51, 18)


def _choice_value(
    value: object,
    allowed: set[str],
    fallback: str,
    *,
    uppercase: bool = False,
) -> str:
    normalized = str(value or "").strip()
    normalized = normalized.upper() if uppercase else normalized.casefold()
    return normalized if normalized in allowed else fallback


def _bounded_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if minimum <= parsed <= maximum else fallback


def _bounded_float(
    value: object,
    minimum: float,
    maximum: float,
    fallback: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    if parsed != parsed or not minimum <= parsed <= maximum:
        return fallback
    return parsed


def _sanitize_chunk_sizes(settings: dict[str, Any]) -> None:
    settings["chunk_size"] = sanitize_chunk_size(settings.get("chunk_size"))

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
        raw_value = engine_sizes.get(engine, 0)
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            value = 0
        default_value = (
            int(defaults.get(engine, DEFAULT_SETTINGS["chunk_size"]))
            if isinstance(defaults, dict)
            else int(DEFAULT_SETTINGS["chunk_size"])
        )
        sanitized[str(engine)] = (
            value if _valid_chunk_size(value) else default_value
        )
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
            normalized = _migrate_settings(_deep_merge(DEFAULT_SETTINGS, values))
        else:
            normalized = _migrate_settings(self.settings)
        # Keep references held by the UI valid after every normalization/save.
        self.settings.clear()
        self.settings.update(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)
        if self.path.resolve() == (application_root() / "config.json").resolve():
            write_assets_location_file(large_assets_root())
            try:
                ensure_assets_marker(large_assets_root())
            except OSError:
                # A clear write error is reported when an install/download starts.
                pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def update(self, values: dict[str, Any]) -> None:
        self.settings = _deep_merge(self.settings, values)

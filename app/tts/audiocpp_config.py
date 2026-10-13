"""Versioned first-delivery catalog. Package IDs are upstream audio.cpp IDs."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

VERSION = "v0.9.1"
WEIGHTS_REVISION = "f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5"


@dataclass(frozen=True)
class AudioCppModel:
    id: str
    name: str
    family: str
    file: str
    size: int
    sha256: str
    license_url: str
    notice: str
    requires_reference: bool

    @property
    def url(self) -> str:
        return f"https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/{WEIGHTS_REVISION}/{self.file}"


MODELS = {
    "higgs_audio_tts_4b_q8_0": AudioCppModel(
        "higgs_audio_tts_4b_q8_0", "Higgs Audio v3 TTS 4B · Q8", "higgs_audio_tts",
        "Higgs-Audio-v3-TTS-4B-GGUF/higgs-audio-v3-tts-4b-q8_0.gguf",
        5095354048, "79746822045b5bf8f9ab2bda87b16cd3f8ea3d9e319cbcf887a87aa1b537a74a",
        "https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/LICENSE",
        "Boson Higgs TTS 3 Research and Non-Commercial License. The Creator Use Grant has specific conditions; it is not a general commercial license. Review the model license before use.", True,
    ),
    "fish_audio_s2_pro_q8_0": AudioCppModel(
        "fish_audio_s2_pro_q8_0", "Fish Audio S2 Pro · Q8", "fish_audio",
        "Fish-Audio-S2-Pro-GGUF/fish-audio-s2-pro-q8_0.gguf",
        6317911232, "4ffc169447b7a26df8bf49e8637adb4000bfa763a22c018b6c03968564259d0b",
        "https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md",
        "Built with Fish Audio. This model is licensed under the Fish Audio Research License, Copyright © 39 AI, INC. All Rights Reserved. Research and non-commercial use only; commercial use requires a separate agreement with Fish Audio.", False,
    ),
}

DEFAULTS = {
    "model": "higgs_audio_tts_4b_q8_0", "backend": "cpu", "device": 0,
    "reference_audio_path": "", "reference_text": "", "language": "auto",
    "temperature": 0.8, "top_p": 0.8, "top_k": 30, "seed": -1,
    "max_tokens": 2048, "text_chunk_size": 200, "timeout_seconds": 900,
    "voices": {},
}

MODEL_SETTING_KEYS = ("backend", "device", "temperature", "top_p", "top_k", "seed",
                      "max_tokens", "text_chunk_size")


def model_config(value: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Keep reference voices shared, but restore each model's generation options."""
    selected = model or value.get("model", DEFAULTS["model"])
    config = {**DEFAULTS, **value, "model": selected}
    if selected != value.get("model", DEFAULTS["model"]):
        for key in MODEL_SETTING_KEYS:
            if key not in {"backend", "device"}:
                config[key] = DEFAULTS[key]
        config["text_chunk_size"] = 200 if selected == DEFAULTS["model"] else 400
    elif "text_chunk_size" not in value:
        config["text_chunk_size"] = 200 if selected == DEFAULTS["model"] else 400
    saved = value.get("model_settings", {}).get(selected, {})
    if selected != value.get("model", DEFAULTS["model"]):
        config.update({key: saved[key] for key in MODEL_SETTING_KEYS if key in saved})
    return config


def validated_config(value: dict[str, Any]) -> dict[str, Any]:
    config = model_config(value)
    if config["model"] not in MODELS:
        raise ValueError("Select a supported audio.cpp model: Higgs Q8 or Fish S2 Pro Q8.")
    if config["backend"] not in {"cpu", "cuda", "vulkan", "metal"}:
        raise ValueError("Unsupported audio.cpp backend.")
    for key, low, high, integer in (
        ("device", 0, 64, True), ("temperature", 0.01, 1.99, False),
        ("top_p", 0.01, 1.0, False), ("top_k", 1, 1000, True),
        ("max_tokens", 1, 16384, True), ("text_chunk_size", 20, 2048, True),
        ("timeout_seconds", 10, 7200, True), ("seed", -1, 2147483647, True),
    ):
        number = float(config[key])
        if not math.isfinite(number) or not low <= number <= high or (integer and not number.is_integer()):
            raise ValueError(f"Invalid audio.cpp {key}: expected {low} to {high}.")
        config[key] = int(number) if integer else number
    return config


def resolve_voice(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Named references are shared by both models, including markup and MCP."""
    voice = config.get("voices", {}).get(name)
    if voice is None:
        matches = [value for value in config.get("voices", {}).values()
                   if isinstance(value, dict) and str(value.get("name", "")).casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous audio.cpp voice name; use its catalog ID: {name}")
        voice = matches[0] if matches else None
    if not isinstance(voice, dict) or not voice.get("reference_audio_path"):
        raise ValueError(f"Unknown audio.cpp reference voice: {name}")
    return {**config, "voice": name, "reference_audio_path": voice["reference_audio_path"],
            "reference_text": voice.get("reference_text", "")}

"""Versioned first-delivery catalog. Package IDs are upstream audio.cpp IDs."""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    precision: str = "Q8"

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
    "breeze_tts_2_q8_0": AudioCppModel(
        "breeze_tts_2_q8_0", "Breeze TTS 2 · Q8", "breeze_tts",
        "Breeze-TTS-2-GGUF/breeze-tts-2-q8_0.gguf",
        5079668352, "0de52d61560f9f6b2dfeca79f9100f8fce0c2b17c52ec30622e23e150df1ad88",
        "https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE",
        "BreezeBlue Research and Non-Commercial License. Model weights and self-hosted outputs are for research and non-commercial use. Languages: English and Chinese. Voice cloning requires a reference WAV and its exact transcript; voice design works without a reference.", False,
    ),
    "voxcpm2_q8_0": AudioCppModel(
        "voxcpm2_q8_0", "VoxCPM 2 · Q8", "voxcpm2",
        "VoxCPM2-GGUF/voxcpm2-q8_0.gguf",
        2955000480, "c8e01ab4416011e12a28f24ede298a1aa5ce64b43f8e8aaad53b1e2fe7c96432",
        "https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/LICENSE",
        "Apache-2.0. 2B parameters; 30 languages including Spanish; 48 kHz output. Natural-language voice design without a reference and controllable voice cloning from a WAV. The transcript is retained in the catalog; this integration uses speaker-reference cloning, not audio continuation.", False,
    ),
}

# Each precision owns its weights/profile while sharing the runtime and features.
for variant, precision, size, digest in (
    ("bf16", "BF16", 4772288288, "0c0cd2515c7a1145c0ddba06c871724656c36cb53ee3b0118116b34cb4153699"),
    ("orig", "Original", 4960716192, "13181e01e15d555c9eae7779626eb395c69334f15e948c9ea265ddf37ab50b16"),
):
    model_id = "voxcpm2_" + variant
    MODELS[model_id] = replace(
        MODELS["voxcpm2_q8_0"], id=model_id, name="VoxCPM 2 · " + precision,
        file=f"VoxCPM2-GGUF/voxcpm2-{variant}.gguf", size=size, sha256=digest,
        precision=precision,
    )

DEFAULTS = {
    "model": "higgs_audio_tts_4b_q8_0", "backend": "cpu", "device": 0,
    "reference_audio_path": "", "reference_text": "", "language": "auto",
    "temperature": 0.8, "top_p": 0.8, "top_k": 30, "seed": -1,
    "max_tokens": 2048, "text_chunk_size": 200, "timeout_seconds": 900,
    "voices": {},
    "instruction": "Speak clearly and naturally.", "guidance_scale": 1.0,
    "depth_temperature": 0.9,
    "num_inference_steps": 10,
}

BREEZE_MODEL = "breeze_tts_2_q8_0"
BREEZE_FIELDS = ("instruction", "guidance_scale", "depth_temperature")
VOXCPM_MODEL = "voxcpm2_q8_0"
VOXCPM_MODELS = frozenset(key for key, spec in MODELS.items() if spec.family == "voxcpm2")
VOXCPM_FIELDS = ("instruction", "guidance_scale", "num_inference_steps")


def model_field_visible(model: str, key: str) -> bool:
    if key in {*BREEZE_FIELDS, *VOXCPM_FIELDS}:
        return key in (VOXCPM_FIELDS if model in VOXCPM_MODELS else BREEZE_FIELDS if model == BREEZE_MODEL else ())
    return not (model in VOXCPM_MODELS and key in {"temperature", "top_p", "top_k"})


def model_defaults(model: str) -> dict[str, Any]:
    defaults = {**DEFAULTS, "model": model}
    if model != DEFAULTS["model"]:
        defaults["text_chunk_size"] = 400
    if model == BREEZE_MODEL:
        defaults.update(temperature=0.9, top_p=1.0, top_k=50, max_tokens=1500,
                        text_chunk_size=600)
    if model in VOXCPM_MODELS:
        defaults.update(instruction="", guidance_scale=2.0, max_tokens=4096,
                        text_chunk_size=400)
    return defaults


MODEL_SETTING_KEYS = ("backend", "device", "temperature", "top_p", "top_k", "seed",
                      "max_tokens", "text_chunk_size", *BREEZE_FIELDS, "num_inference_steps")


def model_config(value: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Keep reference voices shared, but restore each model's generation options."""
    selected = model or value.get("model", DEFAULTS["model"])
    defaults = model_defaults(selected)
    config = {**defaults, **value, "model": selected}
    if selected != value.get("model", DEFAULTS["model"]):
        for key in MODEL_SETTING_KEYS:
            if key not in {"backend", "device"}:
                config[key] = defaults[key]
    saved = value.get("model_settings", {}).get(selected, {})
    if selected != value.get("model", DEFAULTS["model"]):
        config.update({key: saved[key] for key in MODEL_SETTING_KEYS if key in saved})
    return config


def validated_config(value: dict[str, Any]) -> dict[str, Any]:
    config = model_config(value)
    if config["model"] not in MODELS:
        raise ValueError("Select a supported audio.cpp model: Higgs, Fish S2 Pro, Breeze TTS 2 or VoxCPM 2 (Q8, BF16, Original).")
    if config["backend"] not in {"cpu", "cuda", "vulkan", "metal"}:
        raise ValueError("Unsupported audio.cpp backend.")
    for key, low, high, integer in (
        ("device", 0, 64, True), ("temperature", 0.01, 1.99, False),
        ("top_p", 0.01, 1.0, False), ("top_k", 0 if config["model"] == BREEZE_MODEL else 1, 1000, True),
        ("max_tokens", 1, 16384, True), ("text_chunk_size", 20, 2048, True),
        ("timeout_seconds", 10, 7200, True), ("seed", -1, 2147483647, True),
    ):
        number = float(config[key])
        if not math.isfinite(number) or not low <= number <= high or (integer and not number.is_integer()):
            raise ValueError(f"Invalid audio.cpp {key}: expected {low} to {high}.")
        config[key] = int(number) if integer else number
    if config["model"] in VOXCPM_MODELS:
        from .voxcpm_tags import validate_instruction
        config["instruction"] = validate_instruction(config["instruction"])
        for key, low, high in (("guidance_scale", 0, 20), ("num_inference_steps", 1, 100)):
            number = float(config[key])
            if not math.isfinite(number) or not low <= number <= high or (key == "num_inference_steps" and not number.is_integer()):
                raise ValueError(f"Invalid VoxCPM 2 {key}: expected {low} to {high}.")
            config[key] = int(number) if key == "num_inference_steps" else number
    if config["model"] == BREEZE_MODEL:
        if not isinstance(config["instruction"], str):
            raise ValueError("Breeze instruction must be text.")
        for key, low, high in (("guidance_scale", 0, 20), ("depth_temperature", 0.01, 1.99)):
            number = float(config[key])
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError(f"Invalid Breeze {key}: expected {low} to {high}.")
            config[key] = number
    return config


def resolve_voice(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Named references are shared by the models, including markup and MCP."""
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

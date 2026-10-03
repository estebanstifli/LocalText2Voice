"""Public IndexTTS-2.5 parameters (indextts.infer_v2_5.IndexTTS2)."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

LANGUAGES = {
    "ZH": "Chinese",
    "EN": "English",
    "JA": "Japanese",
    "ES": "Spanish",
    "AR": "Arabic",
}
EMOTIONS = (
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
)
DEFAULTS = {
    "model": "indextts_2_5",
    "device": "cuda",
    "dtype": "bfloat16",
    "language": "ES",
    "reference_audio_path": "",
    "emotion_mode": "reference",
    "emo_text": "",
    "emo_audio_prompt": "",
    "emo_alpha": 0.6,
    "emo_vector": [0.0] * 8,
    "use_random": False,
    "duration_factor": 1.0,
    "temperature": 0.8,
    "top_p": 0.8,
    "top_k": 30,
    "num_beams": 3,
    "repetition_penalty": 10.0,
    "max_mel_tokens": 1500,
    "max_text_tokens_per_segment": 120,
    "interval_silence": 200,
}
LICENSE_URL = "https://github.com/index-tts/index-tts/blob/d9e41aac89fd00b3d71497fddb287b7f24613712/LICENSE"
LICENSE_NOTICE = (
    "bilibili Model Use License Agreement: conditional royalty-free use, including commercial use. "
    "A separate written license is required above 100 million monthly active users or RMB 1 billion "
    "annual revenue (including affiliates). Retain the license and notices; downstream and AI-training "
    "restrictions apply. See the complete license before installing."
)


def language_code(value: object) -> str:
    key = str(value).strip().upper().replace("_", "-")
    aliases = {name.upper(): code for code, name in LANGUAGES.items()}
    aliases.update(
        {
            "ESPAÑOL": "ES",
            "SPANISH": "ES",
            "CHINESE": "ZH",
            "JAPANESE": "JA",
            "ARABIC": "AR",
        }
    )
    return aliases.get(key, key.split("-", 1)[0])


def validated_config(
    config: dict[str, Any], *, require_inputs: bool = True
) -> dict[str, Any]:
    result = {**deepcopy(DEFAULTS), **config}
    result["language"] = language_code(result["language"])
    if result["language"] not in LANGUAGES:
        raise ValueError(
            "IndexTTS-2.5 supports ZH, EN, JA, ES and AR; select a language explicitly."
        )
    if result["dtype"] not in {"bfloat16", "float32"}:
        raise ValueError("IndexTTS-2.5 precision must be bfloat16 or float32.")
    if result["device"] not in {"cuda", "cpu"}:
        raise ValueError("IndexTTS-2.5 device must be cuda or cpu.")
    if result["device"] == "cpu" and result["dtype"] == "bfloat16":
        raise ValueError(
            "IndexTTS-2.5 BF16 requires a compatible NVIDIA GPU; CPU requires float32."
        )
    if result["emotion_mode"] not in {"reference", "text", "auto", "audio", "vector"}:
        raise ValueError("Unknown IndexTTS emotion mode.")
    # The generic markup instruction is an alias for the separate emotion prompt.
    if "instruct" in config:
        result["emo_text"] = str(config["instruct"])
        result["emotion_mode"] = "text" if result["emo_text"].strip() else "reference"
    if (
        require_inputs
        and result["emotion_mode"] == "text"
        and not str(result["emo_text"]).strip()
    ):
        # An unmarked fragment needs no emotional guidance. Preserve its voice
        # reference instead of rejecting an empty optional instruction.
        result["emotion_mode"] = "reference"
    for key, low, high in (
        ("emo_alpha", 0, 1),
        ("duration_factor", 0.5, 2),
        ("temperature", 0.05, 2),
        ("top_p", 0.01, 1),
        ("repetition_penalty", 0.1, 20),
        ("top_k", 0, 100),
        ("num_beams", 1, 10),
        ("max_mel_tokens", 50, 3000),
        ("max_text_tokens_per_segment", 20, 200),
        ("interval_silence", 0, 2000),
    ):
        value = float(result[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"IndexTTS {key} must be between {low} and {high}.")
        if isinstance(DEFAULTS[key], int):
            if not value.is_integer():
                raise ValueError(f"IndexTTS {key} must be an integer.")
            value = int(value)
        result[key] = value
    vector = result["emo_vector"]
    if not isinstance(vector, (list, tuple)) or len(vector) != 8:
        raise ValueError("IndexTTS emo_vector needs exactly eight values.")
    result["emo_vector"] = [float(value) for value in vector]
    # The official QwenEmotion interpreter clamps scores to 0–1.2.
    # Preserve those values when caching its output instead of rejecting them.
    if any(not math.isfinite(v) or not 0 <= v <= 1.2 for v in result["emo_vector"]):
        raise ValueError("IndexTTS emotion values must be between 0 and 1.2.")
    if not isinstance(result["use_random"], bool):
        raise ValueError("IndexTTS use_random must be a boolean.")
    return result


def inference_arguments(
    config: dict[str, Any], text: str, output: str
) -> dict[str, Any]:
    c = validated_config(config)
    result = {
        "spk_audio_prompt": c["reference_audio_path"],
        "text": text,
        "output_path": output,
        "lang": c["language"],
        **{
            key: c[key]
            for key in (
                "emo_alpha",
                "use_random",
                "duration_factor",
                "temperature",
                "top_p",
                "top_k",
                "num_beams",
                "repetition_penalty",
                "max_mel_tokens",
                "max_text_tokens_per_segment",
                "interval_silence",
            )
        },
    }
    mode = c["emotion_mode"]
    if mode in {"text", "auto"}:
        result.update(
            use_emo_text=True, emo_text=c["emo_text"] if mode == "text" else None
        )
    elif mode == "audio":
        result["emo_audio_prompt"] = c["emo_audio_prompt"]
    elif mode == "vector":
        result["emo_vector"] = c["emo_vector"]
    return result

"""Application presets, not official or universally calibrated IndexTTS voices.

Order: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm.
Intensity is applied once by upstream emo_alpha; vectors are not pre-scaled.
"""

import math
import unicodedata

EMOTION_PRESETS = {
    "happy": (0.65, 0, 0, 0, 0, 0, 0.1, 0.05),
    "sad": (0, 0, 0.6, 0, 0, 0.15, 0, 0.05),
    "angry": (0, 0.65, 0, 0, 0.1, 0, 0, 0.05),
    "disgust": (0, 0, 0, 0, 0.8, 0, 0, 0),
    "fear": (0, 0, 0.05, 0.65, 0, 0, 0.1, 0),
    "surprise": (0.1, 0, 0, 0, 0, 0, 0.7, 0),
    "calm": (0, 0, 0, 0, 0, 0, 0, 0.8),
    "melancholic": (0, 0, 0.1, 0, 0, 0.65, 0, 0.05),
}
ALIASES = {
    "very_happy": "happy",
    "deep_sadness": "sad",
    "restrained_anger": "angry",
    "asco": "disgust",
    "disgusted": "disgust",
    "alegria": "happy",
    "feliz": "happy",
    "muy_feliz": "happy",
    "mucha_alegria": "happy",
    "triste": "sad",
    "tristeza": "sad",
    "tristeza_profunda": "sad",
    "enfado": "angry",
    "ira": "angry",
    "enfado_contenido": "angry",
    "miedo": "fear",
    "afraid": "fear",
    "sorpresa": "surprise",
    "surprised": "surprise",
    "calma": "calm",
    "melancolia": "melancholic",
    "melancholy": "melancholic",
    "neutral": "off",
    "none": "off",
    "reference": "off",
    "normal": "off",
    "sin_emocion": "off",
}


def emotion_preset(name: str = "off", strength: str = "1") -> dict:
    normalized = unicodedata.normalize("NFKD", name.casefold())
    key = "".join(c for c in normalized if not unicodedata.combining(c))
    key = key.replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key, key)
    if key != "off" and key not in EMOTION_PRESETS:
        raise ValueError(
            "Unknown emotion. Use off, " + ", ".join(EMOTION_PRESETS) + "."
        )
    try:
        intensity = float(strength.rstrip("%")) / (100 if strength.endswith("%") else 1)
    except ValueError as exc:
        raise ValueError("Emotion intensity must be 0–1 or 0%–100%.") from exc
    if not math.isfinite(intensity) or not 0 <= intensity <= 1:
        raise ValueError("Emotion intensity must be 0–1 or 0%–100%.")
    return {
        "emotion_mode": "reference" if key == "off" or intensity == 0 else "vector",
        "emo_vector": list(EMOTION_PRESETS[key]) if key != "off" else [0.0] * 8,
        "emo_alpha": intensity,
        "emo_text": "",
        "emo_audio_prompt": "",
        "use_random": False,
    }

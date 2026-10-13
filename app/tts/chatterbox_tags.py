"""Native Turbo tags; no approximate mappings for unsupported emotions."""

import re
import unicodedata
from typing import Callable

from .indextts_emotions import ALIASES

EMOTION_TAGS = (
    "angry", "fear", "surprised", "whispering", "advertisement",
    "dramatic", "narration", "crying", "happy", "sarcastic",
)
VOCAL_TAGS = (
    "clear_throat", "sigh", "shush", "cough", "groan",
    "sniff", "gasp", "chuckle", "laugh",
)
_NATIVE_TAG = re.compile(
    r"\[(?:" + "|".join(re.escape(tag.replace("_", " ")) for tag in (*EMOTION_TAGS, *VOCAL_TAGS)) + r")\]"
)
NATIVE_TAG_PATTERN = _NATIVE_TAG.pattern
EMOTION_TAG_PATTERN = re.compile(r"\[(?:" + "|".join(EMOTION_TAGS) + r")\]")


def emotion_name(value: str) -> str:
    key = unicodedata.normalize("NFKD", value.casefold())
    key = "".join(char for char in key if not unicodedata.combining(char))
    key = key.replace("-", "_").replace(" ", "_")
    return ALIASES.get(key, key)


def turbo_emotion(name: str) -> str | None:
    name = {"surprise": "surprised"}.get(name, name)
    return name if name in EMOTION_TAGS or name == "off" else None


def vocal_tag(value: str) -> str:
    key = value.casefold().replace(" ", "_").replace("-", "_")
    if key not in VOCAL_TAGS:
        raise ValueError("Unknown vocal event. Available: " + ", ".join(VOCAL_TAGS))
    return "[" + key.replace("_", " ") + "]"


def prepare_text(text: str, config: dict, warn: Callable[[str], None]) -> str:
    """Render per-segment direction at the engine boundary, also for regeneration."""
    emotion = config.get("chatterbox_emotion", "off")
    if emotion not in (*EMOTION_TAGS, "off"):
        raise ValueError(f"Unsupported Chatterbox emotion: {emotion}")
    if config.get("model", "multilingual_v3") != "turbo":
        if emotion != "off" or _NATIVE_TAG.search(text):
            warn("Emotion and vocal tags require Chatterbox Turbo; ignored for this model.")
        return _NATIVE_TAG.sub("", text).strip()
    # Explicit, editable native direction takes precedence over legacy segment metadata.
    return f"[{emotion}] {text}" if emotion != "off" and not EMOTION_TAG_PATTERN.search(text) else text

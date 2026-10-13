"""Common native S2 Pro controls from https://huggingface.co/fishaudio/s2-pro.

These are menu shortcuts, not an exhaustive vocabulary: Fish supports free-form tags.
"""
import re

TAG_GROUPS = (
    ("emotion", "Emotions", (
        "excited", "excited tone", "angry", "surprised", "delight", "sad", "shocked",
    )),
    ("style", "Style", (
        "whisper", "singing", "laughing tone", "shouting", "screaming", "with strong accent",
    )),
    ("prosody", "Delivery and pauses", (
        "pause", "short pause", "emphasis", "interrupting", "volume up", "volume down",
        "low volume", "low voice", "loud", "echo",
    )),
    ("sfx", "Vocal sounds", (
        "laughing", "chuckle", "chuckling", "inhale", "exhale", "tsk", "sigh",
        "panting", "audience laughter", "clearing throat", "moaning",
    )),
)
TAG_PATTERN = r"\[(?:" + "|".join(re.escape(tag) for _, _, tags in TAG_GROUPS for tag in tags) + r")\]"

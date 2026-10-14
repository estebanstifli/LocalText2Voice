"""Application emotion presets translated into VoxCPM2's leading voice tag.

These are natural-language suggestions, not a finite native emotion vocabulary.
The adapter adds the parentheses after splitting so every request is directed.
"""

EMOTION_INSTRUCTIONS = {
    "happy": "happy and cheerful tone",
    "sad": "sad tone",
    "angry": "angry tone",
    "fear": "fearful tone",
    "surprise": "surprised tone",
    "calm": "calm and gentle tone",
    "melancholic": "melancholic tone",
    "disgust": "disgusted tone",
}


def validate_instruction(value):
    if not isinstance(value, str):
        raise ValueError("VoxCPM 2 instruction must be text.")
    value = value.strip()
    if any(token in value for token in ("(", ")", "{{", "}}")) or len(value) > 1000:
        raise ValueError("VoxCPM 2: enter up to 1000 characters of instruction without parentheses or markup; parentheses are added automatically.")
    return " ".join(value.split())


def emotion_instruction(name, value):
    if name == "off" or not value.get("emo_alpha", 1):
        return ""
    if value.get("emotion_source") == "custom":
        return value["emo_text"]
    return EMOTION_INSTRUCTIONS.get(name, name.replace("_", " ") + " tone")

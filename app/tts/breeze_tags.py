"""Breeze TTS 2 controls; instructions are application presets, not native tags.

Native vocal events documented by BreezeBlue/breeze-tts README (English/Chinese).
"""
import re

VOCAL_TAGS = ("(laugh)", "(cough)", "(clears throat)", "(sigh)")
CHINESE_VOCAL_TAGS = ("[笑]", "[咳嗽]", "[清嗓子]", "[叹气]")
TAG_PATTERN = "(?:" + "|".join(re.escape(tag) for tag in (*VOCAL_TAGS, *CHINESE_VOCAL_TAGS)) + ")"
EMOTION_INSTRUCTIONS = {
    "happy": "Speak with a happy and cheerful tone.",
    "sad": "Speak with a sad tone.",
    "angry": "Speak with an angry tone.",
    "fear": "Speak with a fearful tone.",
    "surprise": "Speak with a surprised tone.",
    "calm": "Speak calmly and gently.",
    "melancholic": "Speak with a melancholic tone.",
    "disgust": "Speak with a disgusted tone.",
}
STYLE_INSTRUCTIONS = {
    "Whispering": "Speak in a whisper.",
    "Narration": "Speak as a clear, natural audiobook narrator.",
    "Energetic": "Speak energetically and enthusiastically.",
}


def emotion_instruction(name, value):
    if name == "off" or not value.get("emo_alpha", 1):
        return "Speak clearly and naturally."
    if value.get("emotion_source") == "custom":
        return value["emo_text"]
    return EMOTION_INSTRUCTIONS.get(name, f"Speak with a {name.replace('_', ' ')} tone.")

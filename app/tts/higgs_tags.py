"""Curated native controls from Boson's Higgs v3 PROMPTING.md.

https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/PROMPTING.md
These are literal text insertions, not LocalText2Voice markup commands.
"""

# category, English menu label, (native value, English label, optional vocal text)
TAG_GROUPS = (
    ("emotion", "Emotions", (
        ("elation", "Joy", ""), ("amusement", "Amusement", ""),
        ("enthusiasm", "Enthusiasm", ""), ("contentment", "Contentment", ""),
        ("anger", "Anger", ""), ("sadness", "Sadness", ""),
        ("fear", "Fear", ""), ("surprise", "Surprise", ""),
    )),
    ("style", "Styles", (
        ("whispering", "Whispering", ""), ("shouting", "Shouting", ""),
        ("singing", "Singing", ""),
    )),
    ("prosody", "Prosody", (
        ("speed_slow", "Slow", ""), ("speed_fast", "Fast", ""),
        ("pitch_low", "Low pitch", ""), ("pitch_high", "High pitch", ""),
        ("expressive_high", "More expressive", ""), ("expressive_low", "Less expressive", ""),
        ("pause", "Pause", ""), ("long_pause", "Long pause", ""),
    )),
    ("sfx", "Vocal sounds", (
        ("laughter", "Laughter", "Haha"), ("sigh", "Sigh", "Ahh"),
        ("cough", "Cough", "Ahem"), ("sniff", "Sniff", "Sff"),
        ("crying", "Crying", "Boohoo"),
    )),
)

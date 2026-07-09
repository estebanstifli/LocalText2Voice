from __future__ import annotations


PREVIEW_TEXT_BY_LANGUAGE: dict[str, str] = {
    "en": "The moon looks beautiful tonight. This is a short voice preview.",
    "es": "La luna esta preciosa esta noche. Esta es una prueba breve de voz.",
    "fr": "La lune est magnifique ce soir. Ceci est un court test de voix.",
    "hi": "आज रात चाँद बहुत सुंदर है। यह आवाज़ की एक छोटी परीक्षा है।",
    "it": "La luna e bellissima stasera. Questa e una breve prova della voce.",
    "pt": "A lua esta linda esta noite. Este e um breve teste de voz.",
    "zh": "今晚的月亮很美。这是一段简短的语音试听。",
}


def kokoro_preview_text_for_language(language: str) -> str:
    normalized = language.strip().lower().replace("_", "-")
    if not normalized:
        return PREVIEW_TEXT_BY_LANGUAGE["en"]
    primary = normalized.split("-", 1)[0]
    return PREVIEW_TEXT_BY_LANGUAGE.get(primary, PREVIEW_TEXT_BY_LANGUAGE["en"])

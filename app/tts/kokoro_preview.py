from __future__ import annotations


PREVIEW_TEXT_BY_LANGUAGE: dict[str, str] = {
    "en": "The moon looks beautiful tonight.",
    "es": "La luna esta preciosa esta noche.",
    "fr": "La lune est magnifique ce soir.",
    "hi": "आज रात चाँद बहुत सुंदर है।",
    "it": "La luna e bellissima stasera.",
    "pt": "A lua esta linda esta noite.",
    "zh": "今晚的月亮很美。",
}


def kokoro_preview_text_for_language(language: str) -> str:
    normalized = language.strip().lower().replace("_", "-")
    if not normalized:
        return PREVIEW_TEXT_BY_LANGUAGE["en"]
    primary = normalized.split("-", 1)[0]
    return PREVIEW_TEXT_BY_LANGUAGE.get(primary, PREVIEW_TEXT_BY_LANGUAGE["en"])

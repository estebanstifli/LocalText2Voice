from __future__ import annotations

import json
from string import Formatter
import unittest
from pathlib import Path

from app.utils.i18n import Translator


class TranslationTests(unittest.TestCase):
    def test_all_locales_have_matching_keys_and_placeholders(self) -> None:
        root = Path(__file__).resolve().parents[1] / "locales"
        english = json.loads((root / "en.json").read_text(encoding="utf-8"))
        for path in root.glob("*.json"):
            with self.subTest(locale=path.stem):
                messages = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(english), set(messages))
                for key, source in english.items():
                    expected = {
                        name
                        for _, name, _, _ in Formatter().parse(source)
                        if name
                    }
                    actual = {
                        name
                        for _, name, _, _ in Formatter().parse(messages[key])
                        if name
                    }
                    self.assertEqual(expected, actual, key)

    def test_ten_languages_are_discovered(self) -> None:
        languages = Translator.available_languages()
        self.assertEqual(
            {language.code for language in languages},
            {"ar", "de", "en", "es", "fr", "hi", "it", "ja", "pt", "zh"},
        )
        self.assertEqual(len(languages), 10)

    def test_spanish_translation_formats_values(self) -> None:
        translator = Translator("es")
        self.assertEqual(translator.text("generate_audio"), "Generar audio")
        self.assertIn(
            "3",
            translator.text("generation_complete", count=3),
        )

    def test_arabic_uses_right_to_left_layout(self) -> None:
        translator = Translator("ar")
        self.assertEqual(translator.direction, "rtl")
        self.assertEqual(translator.text("generate_audio"), "إنشاء الصوت")

    def test_unknown_language_falls_back_to_english(self) -> None:
        translator = Translator("not-a-locale")
        self.assertEqual(translator.language, "en")
        self.assertEqual(translator.text("generate_audio"), "Generate Audio")


if __name__ == "__main__":
    unittest.main()

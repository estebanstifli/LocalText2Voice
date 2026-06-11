from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.utils.i18n import Translator


class TranslationTests(unittest.TestCase):
    def test_english_and_spanish_have_matching_keys(self) -> None:
        root = Path(__file__).resolve().parents[1] / "locales"
        english = json.loads((root / "en.json").read_text(encoding="utf-8"))
        spanish = json.loads((root / "es.json").read_text(encoding="utf-8"))
        self.assertEqual(set(english), set(spanish))

    def test_spanish_translation_formats_values(self) -> None:
        translator = Translator("es")
        self.assertEqual(translator.text("generate_audio"), "Generar audio")
        self.assertIn(
            "3",
            translator.text("generation_complete", count=3),
        )


if __name__ == "__main__":
    unittest.main()

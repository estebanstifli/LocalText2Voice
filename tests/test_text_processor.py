from __future__ import annotations

import unittest

from app.core.text_processor import TextProcessor


class TextProcessorTests(unittest.TestCase):
    def test_normalize_preserves_paragraphs(self) -> None:
        source = " First   paragraph.\r\n\r\nSecond\u00a0paragraph. \u200b"
        self.assertEqual(
            TextProcessor.normalize_text(source),
            "First paragraph.\n\nSecond paragraph.",
        )

    def test_safe_chunks_respect_limit_and_content(self) -> None:
        source = "\n\n".join(
            f"Paragraph {index}. " + ("word " * 40)
            for index in range(1, 8)
        )
        chunks = TextProcessor.split_safe_chunks(source, max_chars=300)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 300 for chunk in chunks))
        self.assertIn("Paragraph 1.", chunks[0])
        self.assertIn("Paragraph 7.", chunks[-1])

    def test_split_by_markdown_and_spanish_headings(self) -> None:
        source = """
Introductory note.

## Chapter 1
First chapter body.

CAPÍTULO 2: CONTINUACIÓN
Second chapter body.
"""
        sections = TextProcessor.split_by_headings(source)
        self.assertEqual(
            [section.title for section in sections],
            ["Introduction", "Chapter 1", "CAPÍTULO 2: CONTINUACIÓN"],
        )
        self.assertIn("First chapter body.", sections[1].text)

    def test_bare_numbered_heading_is_detected(self) -> None:
        sections = TextProcessor.split_by_headings(
            "Chapter 1\nOpening text.\n\nLección 2\nClosing text."
        )
        self.assertEqual(
            [section.title for section in sections],
            ["Chapter 1", "Lección 2"],
        )

    def test_long_unbroken_word_is_split(self) -> None:
        chunks = TextProcessor.split_safe_chunks("a" * 900, max_chars=250)
        self.assertEqual([len(chunk) for chunk in chunks], [250, 250, 250, 150])

    def test_paragraph_chunks_keep_boundary_information(self) -> None:
        chunks = TextProcessor.split_paragraph_chunks(
            ("First paragraph. " * 30) + "\n\nSecond paragraph.",
            max_chars=200,
        )
        self.assertGreater(len(chunks), 2)
        self.assertFalse(chunks[0].ends_paragraph)
        self.assertTrue(chunks[-2].ends_paragraph)
        self.assertTrue(chunks[-1].ends_paragraph)


if __name__ == "__main__":
    unittest.main()

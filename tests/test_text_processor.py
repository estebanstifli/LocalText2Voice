from __future__ import annotations

import unittest

from app.core.text_processor import TextProcessor


class TextProcessorTests(unittest.TestCase):
    def test_dialogue_grouping_can_be_disabled(self) -> None:
        turns = ['-Hola.', '-Hola, ¿cómo estás?', '-Estoy muy bien.', '-Genial, vámonos.']
        for splitter in (TextProcessor.split_paragraph_chunks, TextProcessor.split_short_sentence_chunks):
            for dash in ('-', '–', '—'):
                lines = [dash + t[1:] for t in turns]
                for separator in ('\n', '\n\n'):
                    chunks = splitter(separator.join(lines), max_chars=300)
                    self.assertEqual([c.text for c in chunks], ['\n'.join(lines)])
                chunks = splitter('\n\n'.join(lines), max_chars=300, group_short_dialogue=False)
                self.assertEqual([c.text for c in chunks], lines)

    def test_dialogue_packs_turns_and_keeps_group_pause_metadata(self) -> None:
        turn = '—Esta intervención contiene varias palabras.'
        source = '\n\n'.join([turn] * 9) + '\n\nNarración final.'
        for splitter in (TextProcessor.split_paragraph_chunks, TextProcessor.split_short_sentence_chunks):
            chunks = splitter(source, max_chars=300)
            self.assertEqual(chunks[0].text, '\n'.join([turn] * 6))
            self.assertEqual(chunks[1].text, '\n'.join([turn] * 3))
            self.assertEqual([c.ends_paragraph for c in chunks], [False, True, True])
            self.assertEqual([c.paragraph_number for c in chunks], [1, 1, 2])
            self.assertEqual(chunks[0].paragraph_length, len('\n'.join([turn] * 9)))
            self.assertTrue(all(len(c.text) <= 300 for c in chunks))

    def test_long_dialogue_turn_uses_safe_splits(self) -> None:
        source = '-Hola.\n\n-Primero, ' + 'palabra ' * 100 + 'fin.\n\n-Adiós.'
        for splitter in (TextProcessor.split_paragraph_chunks, TextProcessor.split_short_sentence_chunks):
            chunks = splitter(source, max_chars=300)
            self.assertTrue(all(0 < len(c.text) <= 300 for c in chunks))
            self.assertEqual(' '.join(' '.join(c.text.split()) for c in chunks), ' '.join(source.split()))
            self.assertTrue(chunks[-1].ends_paragraph)
            self.assertFalse(any(c.ends_paragraph for c in chunks[:-1]))

    def test_dialogue_does_not_merge_across_narration_headings_or_plain_lists(self) -> None:
        paragraphs = ['-Hola.', 'Narración.', '-Adiós.', 'Capítulo 2', '-Otra frase.', '- pan', '- leche']
        for splitter in (TextProcessor.split_paragraph_chunks, TextProcessor.split_short_sentence_chunks):
            chunks = splitter('\n\n'.join(paragraphs), max_chars=300)
            self.assertEqual([c.text for c in chunks], paragraphs)

    def chunk_variants(self, source: str, limit: int) -> list[list[str]]:
        return [
            TextProcessor.split_safe_chunks(source, limit),
            [c.text for c in TextProcessor.split_paragraph_chunks(source, limit)],
            [c.text for c in TextProcessor.split_short_sentence_chunks(
                source, target_chars=limit, max_chars=limit, min_chars=1
            )],
        ]

    def test_sentence_endings_take_priority_over_commas(self) -> None:
        for ending in ('.', '?', '!', '…', '."', '?”', '!»'):
            first = 'Una frase completa' + ending
            second = 'Otra frase, con una coma y varias palabras.'
            source = first + ' ' + second
            for chunks in self.chunk_variants(source, len(second)):
                with self.subTest(ending=ending, chunks=chunks):
                    self.assertEqual(chunks, [TextProcessor.normalize_text(first), second])

    def test_long_sentence_prefers_clause_boundary(self) -> None:
        for separator in (',', ';', ':'):
            first = 'Primera parte de la frase' + separator
            second = 'segunda parte con varias palabras.'
            for chunks in self.chunk_variants(first + ' ' + second, 45):
                self.assertEqual(chunks, [first, second])

    def test_oversized_clause_respects_limit_and_preserves_content(self) -> None:
        source = 'Inicio de la frase, ' + ('palabra ' * 45).strip() + '.'
        for chunks in self.chunk_variants(source, 100):
            self.assertEqual(chunks[0], 'Inicio de la frase,')
            self.assertTrue(all(0 < len(c) <= 100 for c in chunks))
            self.assertEqual(' '.join(chunks), source)

    def test_unbroken_word_after_sentence_keeps_order(self) -> None:
        source = 'Inicio. ' + 'x' * 120
        for chunks in self.chunk_variants(source, 40):
            self.assertTrue(all(0 < len(c) <= 40 for c in chunks))
            self.assertEqual(''.join(chunks), source.replace(' ', ''))

    def test_clause_splits_keep_paragraph_metadata(self) -> None:
        paragraph = 'Primera parte de la frase, segunda parte con varias palabras.'
        for chunks in (
            TextProcessor.split_paragraph_chunks(paragraph + '\n\nFinal.', 45),
            TextProcessor.split_short_sentence_chunks(
                paragraph + '\n\nFinal.', 45, 45, 1
            ),
        ):
            self.assertEqual([c.ends_paragraph for c in chunks], [False, True, True])
            self.assertEqual([c.paragraph_number for c in chunks], [1, 1, 2])
            self.assertEqual(chunks[0].paragraph_length, len(paragraph))

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

    def test_short_text_can_use_small_chunk_limit(self) -> None:
        source = (
            "At two seventeen in the morning, the hallway camera stopped "
            "recording. Three minutes later, the front door opened by itself."
        )
        chunks = TextProcessor.split_paragraph_chunks(source, max_chars=120)

        self.assertEqual(" ".join(chunk.text for chunk in chunks), source)
        self.assertTrue(all(0 < len(chunk.text) <= 120 for chunk in chunks))

    def test_paragraph_chunks_keep_boundary_information(self) -> None:
        chunks = TextProcessor.split_paragraph_chunks(
            ("First paragraph. " * 30) + "\n\nSecond paragraph.",
            max_chars=200,
        )
        self.assertGreater(len(chunks), 2)
        self.assertFalse(chunks[0].ends_paragraph)
        self.assertTrue(chunks[-2].ends_paragraph)
        self.assertTrue(chunks[-1].ends_paragraph)

    def test_short_sentence_chunks_group_small_sentences_safely(self) -> None:
        chunks = TextProcessor.split_short_sentence_chunks(
            "Yes. No. Hello. This longer sentence gives the model enough context. "
            "Another complete sentence follows naturally.",
            target_chars=90,
            max_chars=120,
            min_chars=35,
        )

        self.assertTrue(all(0 < len(chunk.text) <= 120 for chunk in chunks))
        self.assertNotIn("Yes.", [chunk.text for chunk in chunks])
        self.assertIn("Yes. No. Hello.", chunks[0].text)

    def test_short_sentence_chunks_split_long_sentence_by_clauses(self) -> None:
        source = (
            "This is a long sentence, with several natural clauses, designed to "
            "be split safely, before it becomes too large for a generative voice "
            "model, while still keeping readable speech units."
        )
        chunks = TextProcessor.split_short_sentence_chunks(
            source,
            target_chars=80,
            max_chars=100,
            min_chars=35,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 100 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextSection:
    title: str
    text: str


@dataclass(frozen=True)
class TextChunk:
    text: str
    ends_paragraph: bool
    paragraph_length: int = 0
    paragraph_number: int = 0
    markup_pause_before_ms: int = 0
    markup_pause_after_ms: int | None = None
    markup_state: dict[str, Any] = field(default_factory=dict)


class TextProcessor:
    _heading_pattern = re.compile(
        r"^(?:"
        r"(?:chapter|lesson|module|cap[ií]tulo|lecci[oó]n|m[oó]dulo)"
        r"\s+(?:\d+|[ivxlcdm]+)(?:\s*[:.\-]\s*.*|\s+.*)?$"
        r"|#{1,6}\s+\S.*$"
        r")",
        re.IGNORECASE,
    )
    _sentence_boundary = re.compile(r"(?<=[.!?;…。！？；])\s+")
    _sentence_piece = re.compile(r".+?(?:[.!?;…。！？；]+(?=\s+|$)|$)", re.DOTALL)
    _clause_boundary = re.compile(r"(?<=[,,:;…、，：；])\s+")
    _control_characters = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    _horizontal_space = re.compile(r"[^\S\n]+")
    _excess_newlines = re.compile(r"\n{3,}")

    @classmethod
    def normalize_text(cls, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\u00a0", " ").replace("\u200b", "")
        text = text.replace("\ufeff", "").replace("\ufffd", "")
        text = cls._control_characters.sub(" ", text)
        lines = [cls._horizontal_space.sub(" ", line).strip() for line in text.splitlines()]
        normalized = "\n".join(lines)
        normalized = cls._excess_newlines.sub("\n\n", normalized)
        return normalized.strip()

    @classmethod
    def is_heading(cls, line: str) -> bool:
        candidate = line.strip()
        if not candidate or len(candidate) > 120:
            return False
        if cls._heading_pattern.match(candidate):
            return True

        letters = [character for character in candidate if character.isalpha()]
        words = candidate.split()
        return (
            bool(letters)
            and len(words) <= 12
            and len(candidate) <= 90
            and all(not character.islower() for character in letters)
        )

    @classmethod
    def split_by_headings(cls, text: str) -> list[TextSection]:
        normalized = cls.normalize_text(text)
        if not normalized:
            return []

        sections: list[TextSection] = []
        current_title = "Introduction"
        current_lines: list[str] = []

        def flush() -> None:
            body = "\n".join(current_lines).strip()
            if body:
                spoken_text = (
                    body
                    if current_title == "Introduction"
                    else f"{cls.clean_heading(current_title)}.\n\n{body}"
                )
                sections.append(TextSection(current_title, spoken_text))

        for line in normalized.splitlines():
            if cls.is_heading(line):
                flush()
                current_title = cls.clean_heading(line)
                current_lines = []
            else:
                current_lines.append(line)

        flush()
        if not sections:
            return [TextSection("Course", normalized)]
        return sections

    @staticmethod
    def clean_heading(heading: str) -> str:
        return re.sub(r"^#{1,6}\s*", "", heading).strip().rstrip("#").strip()

    @classmethod
    def split_safe_chunks(cls, text: str, max_chars: int = 2500) -> list[str]:
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1.")

        normalized = cls.normalize_text(text)
        if not normalized:
            return []

        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", normalized)
            if paragraph.strip()
        ]
        chunks: list[str] = []
        current = ""

        def append_piece(piece: str) -> None:
            nonlocal current
            piece = piece.strip()
            if not piece:
                return
            separator = "\n\n" if current else ""
            if len(current) + len(separator) + len(piece) <= max_chars:
                current = f"{current}{separator}{piece}"
                return
            if current:
                chunks.append(current)
                current = ""
            if len(piece) <= max_chars:
                current = piece
                return
            for smaller_piece in cls._split_oversized_piece(piece, max_chars):
                if len(smaller_piece) == max_chars:
                    chunks.append(smaller_piece)
                elif current:
                    chunks.append(current)
                    current = smaller_piece
                else:
                    current = smaller_piece

        for paragraph in paragraphs:
            append_piece(paragraph)

        if current:
            chunks.append(current)
        return chunks

    @classmethod
    def split_paragraph_chunks(
        cls,
        text: str,
        max_chars: int = 2500,
    ) -> list[TextChunk]:
        """Split text safely while retaining real paragraph boundaries."""
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1.")

        normalized = cls.normalize_text(text)
        if not normalized:
            return []

        chunks: list[TextChunk] = []
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", normalized)
            if paragraph.strip()
        ]
        for paragraph_number, paragraph in enumerate(paragraphs, start=1):
            pieces = (
                [paragraph]
                if len(paragraph) <= max_chars
                else cls._split_oversized_piece(paragraph, max_chars)
            )
            for index, piece in enumerate(pieces):
                chunks.append(
                    TextChunk(
                        text=piece,
                        ends_paragraph=index == len(pieces) - 1,
                        paragraph_length=len(paragraph),
                        paragraph_number=paragraph_number,
                    )
                )
        return chunks

    @classmethod
    def split_short_sentence_chunks(
        cls,
        text: str,
        target_chars: int = 230,
        max_chars: int = 300,
        min_chars: int = 45,
    ) -> list[TextChunk]:
        """Split text into short sentence-safe chunks for generative TTS engines."""
        if min_chars < 1:
            raise ValueError("min_chars must be at least 1.")
        if target_chars < min_chars:
            raise ValueError("target_chars must be greater than min_chars.")
        if max_chars < target_chars:
            raise ValueError("max_chars must be greater than target_chars.")

        normalized = cls.normalize_text(text)
        if not normalized:
            return []

        chunks: list[TextChunk] = []
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", normalized)
            if paragraph.strip()
        ]
        for paragraph_number, paragraph in enumerate(paragraphs, start=1):
            pieces = cls._short_sentence_pieces(paragraph, max_chars)
            grouped = cls._group_short_pieces(
                pieces,
                target_chars=target_chars,
                max_chars=max_chars,
                min_chars=min_chars,
            )
            for index, piece in enumerate(grouped):
                chunks.append(
                    TextChunk(
                        text=piece,
                        ends_paragraph=index == len(grouped) - 1,
                        paragraph_length=len(paragraph),
                        paragraph_number=paragraph_number,
                    )
                )
        return chunks

    @classmethod
    def _split_oversized_piece(cls, text: str, max_chars: int) -> list[str]:
        sentences = [
            sentence.strip()
            for sentence in cls._sentence_boundary.split(text)
            if sentence.strip()
        ]
        pieces: list[str] = []
        current = ""

        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(cls._split_by_words(sentence, max_chars))
                continue

            separator = " " if current else ""
            if len(current) + len(separator) + len(sentence) <= max_chars:
                current = f"{current}{separator}{sentence}"
            else:
                pieces.append(current)
                current = sentence

        if current:
            pieces.append(current)
        return pieces

    @classmethod
    def _short_sentence_pieces(cls, paragraph: str, max_chars: int) -> list[str]:
        sentences = [
            match.group(0).strip()
            for match in cls._sentence_piece.finditer(paragraph)
            if match.group(0).strip()
        ]
        pieces: list[str] = []
        for sentence in sentences or [paragraph]:
            if len(sentence) <= max_chars:
                pieces.append(sentence)
                continue
            clauses = [
                clause.strip()
                for clause in cls._clause_boundary.split(sentence)
                if clause.strip()
            ]
            if len(clauses) <= 1:
                pieces.extend(cls._split_by_words(sentence, max_chars))
            else:
                pieces.extend(cls._group_short_pieces(clauses, max_chars, max_chars, 1))
        return pieces

    @staticmethod
    def _group_short_pieces(
        pieces: list[str],
        target_chars: int,
        max_chars: int,
        min_chars: int,
    ) -> list[str]:
        grouped: list[str] = []
        current = ""

        def length_with(piece: str) -> int:
            separator = " " if current else ""
            return len(current) + len(separator) + len(piece)

        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if not current:
                current = piece
                continue
            candidate_len = length_with(piece)
            if candidate_len <= target_chars or (
                len(current) < min_chars and candidate_len <= max_chars
            ):
                current = f"{current} {piece}"
                continue
            grouped.append(current)
            current = piece

        if current:
            grouped.append(current)

        merged: list[str] = []
        for piece in grouped:
            if (
                len(piece) < min_chars
                and merged
                and len(merged[-1]) + 1 + len(piece) <= max_chars
            ):
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)

        index = 0
        while index < len(merged) - 1:
            if (
                len(merged[index]) < min_chars
                and len(merged[index]) + 1 + len(merged[index + 1]) <= max_chars
            ):
                merged[index] = f"{merged[index]} {merged[index + 1]}"
                del merged[index + 1]
                continue
            index += 1
        return merged

    @staticmethod
    def _split_by_words(text: str, max_chars: int) -> list[str]:
        words = text.split()
        pieces: list[str] = []
        current = ""

        for word in words:
            if len(word) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(
                    word[index : index + max_chars]
                    for index in range(0, len(word), max_chars)
                )
                continue
            separator = " " if current else ""
            if len(current) + len(separator) + len(word) <= max_chars:
                current = f"{current}{separator}{word}"
            else:
                pieces.append(current)
                current = word

        if current:
            pieces.append(current)
        return pieces

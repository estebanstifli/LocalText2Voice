from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any


class DocumentImportError(RuntimeError):
    pass


class ProjectManager:
    SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".epub"}

    @classmethod
    def import_document(cls, path: Path) -> str:
        text, _ = cls.import_document_with_metadata(path)
        return text

    @classmethod
    def import_document_with_metadata(cls, path: Path) -> tuple[str, dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise DocumentImportError(f"Unsupported file type: {suffix or 'unknown'}")
        if not path.is_file():
            raise DocumentImportError(f"File not found: {path}")

        if suffix in {".txt", ".md"}:
            return cls._read_plain_text(path), {}
        elif suffix == ".docx":
            return cls._read_docx(path), {}
        elif suffix == ".epub":
            return cls._read_epub(path)
        return "", {}

    @staticmethod
    def _read_plain_text(path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                raise DocumentImportError(f"Could not read {path.name}: {exc}") from exc
        raise DocumentImportError(
            f"Could not decode {path.name}. Save it as UTF-8 and try again."
        )

    @staticmethod
    def _read_docx(path: Path) -> str:
        try:
            from docx import Document
        except ImportError as exc:
            raise DocumentImportError(
                "DOCX support requires python-docx. Install the project requirements."
            ) from exc

        try:
            document = Document(path)
            paragraphs = [
                paragraph.text.strip()
                for paragraph in document.paragraphs
                if paragraph.text.strip()
            ]
            return "\n\n".join(paragraphs)
        except Exception as exc:
            raise DocumentImportError(f"Could not import {path.name}: {exc}") from exc

    @staticmethod
    def _read_epub(path: Path) -> tuple[str, dict[str, Any]]:
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise DocumentImportError(
                "EPUB support requires ebooklib and beautifulsoup4. Install the project requirements."
            ) from exc

        try:
            # ebooklib uses warnings for missing things, silence them if needed
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                book = epub.read_epub(str(path))
                
            metadata: dict[str, Any] = {}
            
            def get_meta(key1: str, key2: str) -> str:
                items = book.get_metadata(key1, key2)
                return items[0][0] if items and isinstance(items[0], tuple) and items[0] else ""

            title = get_meta("DC", "title")
            if title: metadata["title"] = title
            
            creators = book.get_metadata("DC", "creator")
            if creators:
                metadata["author"] = ", ".join(c[0] for c in creators if isinstance(c, tuple) and c)
                
            language = get_meta("DC", "language")
            if language: metadata["language"] = language
            
            publisher = get_meta("DC", "publisher")
            if publisher: metadata["publisher"] = publisher
            
            description = get_meta("DC", "description")
            if description: metadata["description"] = description
            
            identifier = get_meta("DC", "identifier")
            if identifier: metadata["isbn"] = identifier

            for item in book.get_items_of_type(ebooklib.ITEM_COVER):
                content = item.get_content()
                if content:
                    temp_cover = Path(tempfile.gettempdir()) / f"ltv_cover_{uuid.uuid4().hex}.jpg"
                    temp_cover.write_bytes(content)
                    metadata["cover_source_path"] = str(temp_cover)
                    metadata["cover_mode"] = "custom"
                break

            text_blocks: list[str] = []
            
            for item_id, _ in book.spine:
                item = book.get_item_with_id(item_id)
                if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
                    continue
                
                soup = BeautifulSoup(item.get_content(), "html.parser")
                
                # Convert h1-h6 to markdown headings
                for i in range(1, 7):
                    for h in soup.find_all(f"h{i}"):
                        text = h.get_text(strip=True)
                        if text:
                            # Use new NavigableString to replace it correctly
                            h.string = f"{'#' * i} {text}"
                        
                paragraphs = [
                    p.get_text(strip=True)
                    for p in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"])
                    if p.get_text(strip=True)
                ]
                
                if paragraphs:
                    text_blocks.append("\n\n".join(paragraphs))

            return "\n\n\n".join(text_blocks), metadata
        except Exception as exc:
            raise DocumentImportError(f"Could not import EPUB {path.name}: {exc}") from exc

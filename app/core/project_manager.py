from __future__ import annotations

from pathlib import Path


class DocumentImportError(RuntimeError):
    pass


class ProjectManager:
    SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx"}

    @classmethod
    def import_document(cls, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise DocumentImportError(f"Unsupported file type: {suffix or 'unknown'}")
        if not path.is_file():
            raise DocumentImportError(f"File not found: {path}")

        if suffix in {".txt", ".md"}:
            return cls._read_plain_text(path)
        return cls._read_docx(path)

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

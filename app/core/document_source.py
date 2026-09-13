"""Project-owned import metadata and opt-in export location/name preferences."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.epub_import import ImportedDocument


def settings_for_document(settings: dict, document: ImportedDocument, source: Path,
                          project_title: str, project_dir: Path) -> dict:
    from app.core.book_metadata import book_metadata_from_settings, default_book_metadata, prepare_project_cover

    result = deepcopy(settings)
    # Preserve manually configured metadata on the existing plain-text import path,
    # but never let metadata extracted from an EPUB contaminate the next document.
    previous = settings.get("document_source", {})
    imported_metadata = source.suffix.casefold() == ".epub"
    reset = imported_metadata or (isinstance(previous, dict) and previous.get("metadata_imported"))
    book = default_book_metadata(project_title) if reset else book_metadata_from_settings(settings, project_title)
    book.update(document.metadata)
    if document.cover:
        with TemporaryDirectory(prefix="ltv_epub_cover_") as folder:
            cover = Path(folder) / "cover"
            cover.write_bytes(document.cover)
            book.update(cover_mode="custom", cover_source_path=str(cover))
            book, _ = prepare_project_cover(project_dir, book["title"], book)
    else:
        book, _ = prepare_project_cover(project_dir, book["title"], book)
    result["book_metadata"] = book
    result["document_source"] = {"path": str(source.resolve()), "metadata_imported": imported_metadata}
    return result


def imported_source(settings: dict) -> Path | None:
    data = settings.get("document_source", {})
    value = data.get("path", "") if isinstance(data, dict) else ""
    return Path(value) if isinstance(value, str) and value.strip() else None


def output_directory(settings: dict, fallback: Path) -> Path:
    source = imported_source(settings)
    if settings.get("save_next_to_source", False) and source and source.is_file():
        return source.parent
    return fallback


def output_stem(settings: dict) -> str:
    source = imported_source(settings)
    return source.stem if settings.get("use_source_filename", False) and source else ""

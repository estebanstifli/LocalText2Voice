from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen


BOOK_METADATA_KEY = "book_metadata"
DEFAULT_CHAPTER_MODE = "markup"
BOOK_METADATA_FIELDS = (
    "title",
    "subtitle",
    "author",
    "narrator",
    "series",
    "series_index",
    "language",
    "genre",
    "publisher",
    "publication_date",
    "description",
    "copyright",
    "isbn",
)


def default_book_metadata(project_title: str) -> dict[str, Any]:
    return {
        **{field: "" for field in BOOK_METADATA_FIELDS},
        "title": str(project_title or "Project").strip() or "Project",
        "title_follows_project": True,
        "cover_mode": "auto",
        "cover_path": "assets/cover.jpg",
        "chapter_mode": DEFAULT_CHAPTER_MODE,
    }


def book_metadata_from_settings(
    settings: dict[str, Any] | None,
    project_title: str,
) -> dict[str, Any]:
    result = default_book_metadata(project_title)
    raw = (settings or {}).get(BOOK_METADATA_KEY, {})
    if isinstance(raw, dict):
        result.update(raw)
    if bool(result.get("title_follows_project", True)):
        result["title"] = str(project_title).strip() or "Project"
    else:
        result["title"] = str(result.get("title", "")).strip() or project_title
    result["cover_mode"] = (
        "custom" if str(result.get("cover_mode", "auto")) == "custom" else "auto"
    )
    chapter_mode = str(result.get("chapter_mode", DEFAULT_CHAPTER_MODE)).casefold()
    result["chapter_mode"] = (
        chapter_mode if chapter_mode in {"markup", "headings", "none"} else DEFAULT_CHAPTER_MODE
    )
    return result


def settings_with_book_metadata(
    settings: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    result = dict(settings or {})
    result[BOOK_METADATA_KEY] = dict(metadata)
    return result


def _initials(title: str) -> str:
    words = [word for word in str(title).split() if any(ch.isalnum() for ch in word)]
    letters = "".join(next((ch for ch in word if ch.isalnum()), "") for word in words[:3])
    return letters.upper() or "P"


def default_cover_image(title: str, size: int = 1200) -> QImage:
    size = max(96, int(size))
    digest = hashlib.sha256(str(title).encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    background = QColor.fromHsv(hue, 150, 150)
    accent = QColor.fromHsv((hue + 32) % 360, 105, 235)
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(background)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = int(size * 0.17)
    book = QRect(margin, int(size * 0.13), size - margin * 2, int(size * 0.66))
    painter.setPen(QPen(QColor(255, 255, 255, 95), max(2, size // 180)))
    painter.setBrush(accent)
    painter.drawRoundedRect(book, size * 0.035, size * 0.035)
    painter.setPen(QPen(QColor(35, 45, 65, 95), max(2, size // 140)))
    painter.drawLine(book.left() + int(book.width() * 0.12), book.top(),
                     book.left() + int(book.width() * 0.12), book.bottom())

    # Font lookup requires a QGuiApplication. The engine host can generate audio
    # headlessly, so keep the geometric book cover valid there and add lettering
    # whenever the normal desktop application is present.
    if QGuiApplication.instance() is not None:
        painter.setPen(Qt.GlobalColor.white)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * (0.21 if len(_initials(title)) < 3 else 0.17)))
        painter.setFont(font)
        painter.drawText(book.adjusted(int(size * .08), 0, -int(size * .04), 0),
                         Qt.AlignmentFlag.AlignCenter, _initials(title))

        title_font = QFont()
        title_font.setBold(True)
        title_font.setPixelSize(int(size * 0.055))
        painter.setFont(title_font)
        painter.drawText(QRect(margin, int(size * .82), size - margin * 2, int(size * .12)),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop |
                         Qt.TextFlag.TextWordWrap, str(title).strip()[:80])
    painter.end()
    return image


def _save_jpeg_atomic(image: QImage, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.tmp.jpg")
    if not image.save(str(temporary), "JPEG", 92):
        raise OSError(f"Could not save cover image: {destination}")
    os.replace(temporary, destination)


def generate_default_cover(title: str, destination: Path, size: int = 1200) -> Path:
    _save_jpeg_atomic(default_cover_image(title, size), destination)
    return destination


def normalize_cover(source: Path, destination: Path, size: int = 1200) -> Path:
    source_image = QImage(str(source))
    if source_image.isNull():
        raise ValueError(f"Unsupported or invalid cover image: {source}")
    canvas = QImage(size, size, QImage.Format.Format_RGB32)
    canvas.fill(QColor("#111827"))
    scaled = source_image.convertToFormat(QImage.Format.Format_RGB32).scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    _save_jpeg_atomic(canvas, destination)
    return destination


def prepare_project_cover(
    project_dir: Path,
    project_title: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    result = dict(metadata)
    destination = project_dir / "assets" / "cover.jpg"
    source_text = str(result.get("cover_source_path", "")).strip()
    existing_text = str(result.get("cover_path", "")).strip()
    if result.get("cover_mode") == "custom":
        source = Path(source_text).expanduser() if source_text else None
        if source is None and existing_text:
            candidate = Path(existing_text)
            source = candidate if candidate.is_absolute() else project_dir / candidate
        if source is not None and source.is_file() and source.resolve() != destination.resolve():
            normalize_cover(source, destination)
        elif not destination.is_file():
            generate_default_cover(project_title, destination)
            result["cover_mode"] = "auto"
    else:
        generate_default_cover(project_title, destination)
        result["cover_mode"] = "auto"
    result["cover_path"] = "assets/cover.jpg"
    result.pop("cover_source_path", None)
    return result, destination


def project_cover_path(project_dir: Path, metadata: dict[str, Any]) -> Path | None:
    value = str(metadata.get("cover_path", "")).strip()
    if not value:
        return None
    path = Path(value)
    path = path if path.is_absolute() else project_dir / path
    return path if path.is_file() else None


def chapter_ranges(segments: Iterable[Any]) -> list[tuple[str, int, int]]:
    ordered = sorted(segments, key=lambda segment: int(segment.sequence_index))
    if not ordered:
        return []
    chapters: list[list[Any]] = []
    cursor = 0
    for segment in ordered:
        before = max(0, int(segment.resolved_pause_before_ms or segment.markup_pause_before_ms or 0))
        after_value = segment.resolved_pause_after_ms
        if after_value is None:
            after_value = segment.markup_pause_after_ms or 0
        duration = max(1, int(segment.duration_ms or 0))
        start = cursor
        cursor += before + duration + max(0, int(after_value))
        chapter_key = int(segment.chapter_index or 1)
        title = str(segment.chapter_title or f"Chapter {chapter_key}").strip()
        if not chapters or chapters[-1][0] != chapter_key:
            chapters.append([chapter_key, title, start, cursor])
        else:
            chapters[-1][3] = cursor
    return [(str(item[1]), int(item[2]), max(int(item[2]) + 1, int(item[3]))) for item in chapters]


def write_ffmetadata(path: Path, chapters: Iterable[tuple[str, int, int]]) -> Path:
    def escaped(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\n", " ")

    lines = [";FFMETADATA1"]
    for title, start, end in chapters:
        lines.extend(("[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={escaped(title)}"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def apply_m4b_tags(path: Path, metadata: dict[str, Any], cover: Path | None) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    assert tags is not None
    mappings = {
        "title": "\xa9nam", "author": "\xa9ART", "genre": "\xa9gen",
        "publication_date": "\xa9day", "description": "ldes", "copyright": "cprt",
    }
    for field, key in mappings.items():
        value = str(metadata.get(field, "")).strip()
        if value:
            tags[key] = [value]
        else:
            tags.pop(key, None)
    author = str(metadata.get("author", "")).strip()
    if author:
        tags["aART"] = [author]
    album = str(metadata.get("series", "")).strip() or str(metadata.get("title", "")).strip()
    if album:
        tags["\xa9alb"] = [album]
    for field, name in (("subtitle", "SUBTITLE"), ("narrator", "NARRATOR"),
                        ("series", "SERIES"), ("series_index", "SERIES-PART"),
                        ("language", "LANGUAGE"), ("publisher", "PUBLISHER"),
                        ("isbn", "ISBN")):
        key = f"----:com.apple.iTunes:{name}"
        value = str(metadata.get(field, "")).strip()
        if value:
            tags[key] = [value.encode("utf-8")]
        else:
            tags.pop(key, None)
    if cover and cover.is_file():
        tags["covr"] = [MP4Cover(cover.read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


def update_m4b_tags_atomic(path: Path, metadata: dict[str, Any], cover: Path | None) -> None:
    if not path.is_file() or path.suffix.casefold() != ".m4b":
        return
    with tempfile.TemporaryDirectory(prefix="ltv_m4b_tags_") as temp_name:
        temporary = Path(temp_name) / path.name
        shutil.copy2(path, temporary)
        apply_m4b_tags(temporary, metadata, cover)
        os.replace(temporary, path)

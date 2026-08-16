from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.book_metadata import BOOK_METADATA_FIELDS, default_cover_image
from app.ui.icons import ui_icon


class BookPropertiesDialog(QDialog):
    def __init__(
        self,
        metadata: dict[str, Any],
        project_title: str,
        tr: Callable[..., str],
        project_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.project_title = project_title
        self.project_dir = project_dir
        self._metadata = dict(metadata)
        self._cover_source = str(metadata.get("cover_source_path", ""))
        self._cover_mode = str(metadata.get("cover_mode", "auto"))
        self.setWindowTitle(tr("book_properties", "M4B book properties"))
        self.setMinimumSize(720, 650)

        root = QVBoxLayout(self)
        intro = QLabel(
            tr(
                "book_properties_help",
                "These values are embedded in the M4B file and saved with the project.",
            )
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        cover_row = QHBoxLayout()
        self.cover_preview = QLabel()
        self.cover_preview.setFixedSize(112, 112)
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview.setStyleSheet("border: 1px solid #94a3b8; border-radius: 6px;")
        cover_actions = QVBoxLayout()
        choose = QPushButton(tr("choose_cover", "Choose image…"))
        choose.setIcon(ui_icon("file"))
        choose.clicked.connect(self._choose_cover)
        automatic = QPushButton(tr("use_automatic_cover", "Use automatic cover"))
        automatic.setIcon(ui_icon("refresh"))
        automatic.clicked.connect(self._use_automatic_cover)
        cover_actions.addWidget(choose)
        cover_actions.addWidget(automatic)
        cover_actions.addStretch(1)
        cover_row.addWidget(self.cover_preview)
        cover_row.addLayout(cover_actions)
        form.addRow(tr("cover", "Cover"), cover_row)

        self.edits: dict[str, QLineEdit] = {}
        labels = {
            "title": tr("book_title", "Book title"),
            "subtitle": tr("subtitle", "Subtitle"),
            "author": tr("author", "Author"),
            "narrator": tr("narrator", "Narrator"),
            "series": tr("series", "Series"),
            "series_index": tr("series_index", "Series number"),
            "language": tr("language", "Language"),
            "genre": tr("genre", "Genre"),
            "publisher": tr("publisher", "Publisher"),
            "publication_date": tr("publication_date", "Publication date / year"),
            "copyright": tr("copyright", "Copyright"),
            "isbn": "ISBN",
        }
        for field in BOOK_METADATA_FIELDS:
            if field == "description":
                continue
            edit = QLineEdit(str(metadata.get(field, "")))
            self.edits[field] = edit
            form.addRow(labels[field], edit)

        self.description_edit = QTextEdit(str(metadata.get("description", "")))
        self.description_edit.setMaximumHeight(100)
        form.addRow(tr("description", "Description"), self.description_edit)

        self.chapter_mode_combo = QComboBox()
        self.chapter_mode_combo.addItem(
            tr("chapters_from_markup", "LTV chapter markers"), "markup"
        )
        self.chapter_mode_combo.addItem(
            tr("chapters_from_headings", "Detect text headings"), "headings"
        )
        self.chapter_mode_combo.addItem(tr("no_chapters", "No chapters"), "none")
        index = self.chapter_mode_combo.findData(metadata.get("chapter_mode", "markup"))
        self.chapter_mode_combo.setCurrentIndex(max(0, index))
        form.addRow(tr("chapter_mode", "Chapters"), self.chapter_mode_combo)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_cover_preview()

    def _choose_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_text("choose_cover", "Choose cover image"),
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp);;All files (*.*)",
        )
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self._cover_source = path
        self._cover_mode = "custom"
        self._refresh_cover_preview()

    def _use_automatic_cover(self) -> None:
        self._cover_source = ""
        self._cover_mode = "auto"
        self._refresh_cover_preview()

    def _refresh_cover_preview(self) -> None:
        pixmap = QPixmap()
        if self._cover_mode == "custom" and self._cover_source:
            pixmap.load(self._cover_source)
        if pixmap.isNull() and self.project_dir is not None:
            existing = str(self._metadata.get("cover_path", ""))
            if existing:
                path = Path(existing)
                pixmap.load(str(path if path.is_absolute() else self.project_dir / path))
        if pixmap.isNull():
            pixmap = QPixmap.fromImage(default_cover_image(self.project_title, 320))
        self.cover_preview.setPixmap(
            pixmap.scaled(
                QSize(108, 108),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def metadata(self) -> dict[str, Any]:
        result = dict(self._metadata)
        for field, edit in self.edits.items():
            result[field] = edit.text().strip()
        result["description"] = self.description_edit.toPlainText().strip()
        result["chapter_mode"] = str(self.chapter_mode_combo.currentData())
        result["cover_mode"] = self._cover_mode
        result["title_follows_project"] = (
            result.get("title", "").strip() == self.project_title.strip()
        )
        if self._cover_mode == "custom" and self._cover_source:
            result["cover_source_path"] = self._cover_source
        elif self._cover_mode == "auto":
            result.pop("cover_source_path", None)
        return result

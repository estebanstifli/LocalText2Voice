from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon

Translate = Callable[..., str]


class VideoStoryboardReferencePickerDialog(QDialog):
    def __init__(
        self,
        tr: Translate,
        plan: dict[str, Any],
        initial: list[dict[str, Any]] | None = None,
        *,
        maximum: int = 3,
        project_dir: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.maximum = max(1, int(maximum))
        self.project_dir = str(project_dir or "")
        self.setWindowTitle(self.tr_text("video_storyboard_reference_images", "Reference images"))
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        hint = QLabel(
            self.tr_text(
                "video_storyboard_reference_images_help",
                "Select up to {maximum} character or location references. Image order is preserved for the editing model.",
                maximum=self.maximum,
            )
        )
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.list_widget.setIconSize(QSize(128, 128))
        self.list_widget.setGridSize(QSize(165, 175))
        self.list_widget.itemSelectionChanged.connect(self._limit_selection)
        layout.addWidget(self.list_widget, 1)
        continuity = plan.get("continuity", {}) if isinstance(plan, dict) else {}
        if isinstance(continuity, dict):
            for collection, kind in (("characters", "Character"), ("locations", "Location")):
                for record in continuity.get(collection, []):
                    if not isinstance(record, dict):
                        continue
                    path = str(record.get("reference_image_path") or "")
                    if not path or not Path(path).is_file():
                        continue
                    label = str(record.get("name") or record.get("id") or Path(path).stem)
                    self._add_item(path, label, kind)
        for value in initial or []:
            path = str(value.get("path") or "") if isinstance(value, dict) else ""
            matched = self._item_for_path(path)
            if matched is not None:
                matched.setSelected(True)
            elif path and Path(path).is_file():
                self._add_item(path, str(value.get("label") or Path(path).stem), "External", selected=True)
        self.browse_button = QPushButton(
            self.tr_text("video_storyboard_browse_reference", "Browse another image...")
        )
        self.browse_button.setIcon(ui_icon("folder"))
        layout.addWidget(self.browse_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.browse_button.clicked.connect(self._browse)

    def selected_references(self) -> list[dict[str, str]]:
        selected = sorted(self.list_widget.selectedItems(), key=self.list_widget.row)
        return [dict(item.data(Qt.ItemDataRole.UserRole) or {}) for item in selected[: self.maximum]]

    def _add_item(self, path: str, label: str, kind: str, *, selected: bool = False) -> None:
        item = QListWidgetItem(QIcon(path), f"{label}\n{kind}")
        item.setData(Qt.ItemDataRole.UserRole, {"path": str(Path(path).resolve()), "label": label, "kind": kind.casefold()})
        item.setToolTip(str(Path(path).resolve()))
        self.list_widget.addItem(item)
        item.setSelected(selected)

    def _item_for_path(self, path: str) -> QListWidgetItem | None:
        normalized = str(Path(path).resolve()).casefold() if path else ""
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            value = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, dict) and str(value.get("path") or "").casefold() == normalized:
                return item
        return None

    def _limit_selection(self) -> None:
        selected = self.list_widget.selectedItems()
        if len(selected) <= self.maximum:
            return
        self.list_widget.blockSignals(True)
        selected[-1].setSelected(False)
        self.list_widget.blockSignals(False)
        QMessageBox.information(
            self,
            self.tr_text("video_storyboard_reference_limit", "Reference limit"),
            self.tr_text("video_storyboard_reference_limit_detail", "This editor supports a maximum of {maximum} reference images.", maximum=self.maximum),
        )

    def _browse(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            self.tr_text("video_storyboard_browse_reference", "Browse another image"),
            "",
            self.tr_text("video_storyboard_image_files", "Image files (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)"),
        )
        if not selected:
            return
        selected = self._persist_external_reference(selected)
        item = self._item_for_path(selected)
        if item is None:
            self._add_item(selected, Path(selected).stem, "External", selected=True)
        else:
            item.setSelected(True)
        self._limit_selection()

    def _persist_external_reference(self, source: str) -> str:
        path = Path(source)
        if not self.project_dir or not path.is_file():
            return source
        ascii_name = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-z0-9]+", "_", ascii_name.casefold()).strip("_") or "reference"
        target_dir = Path(self.project_dir) / "storyboard" / "references" / "external"
        target = target_dir / f"{normalized}.png"
        suffix = 2
        while target.exists() and target.resolve() != path.resolve():
            target = target_dir / f"{normalized}_{suffix}.png"
            suffix += 1
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            image = QImage(str(path))
            if image.isNull() or not image.save(str(target), "PNG"):
                return source
        except OSError:
            return source
        return str(target.resolve())

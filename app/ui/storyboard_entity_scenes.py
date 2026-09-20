"""Live, wrapping scene appearances using the project's thumbnail cache."""
from pathlib import Path

from PySide6.QtCore import QSize, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget,
    QListWidgetItem, QListView, QStyledItemDelegate, QVBoxLayout, QWidget, QSizePolicy)

from app.ui.storyboard_thumbnail_cache import StoryboardThumbnailCache


def matching_scenes(record, kind, scenes):
    identifiers = {str(record.get(k) or '').strip().casefold() for k in ('id', 'name')}
    identifiers.update(str(v).strip().casefold() for v in record.get('aliases', []))
    identifiers.update(str(v.get('id') or '').strip().casefold() for v in record.get('states', []))
    identifiers.discard('')
    result = []
    start = 0.0
    for number, scene in enumerate(scenes, 1):
        values = scene.get(kind + 's') or []
        if kind == 'era':
            values = [scene.get('era_state_id', '')]
        bindings = (scene.get('generation_overrides') or {}).get(kind + '_state_ids') or []
        values = [values] if isinstance(values, str) else values
        bindings = [bindings] if isinstance(bindings, str) else bindings
        seconds = float(scene.get('start_seconds', start))
        if identifiers.intersection(str(v).strip().casefold() for v in [*values, *bindings]):
            result.append((number, seconds, scene))
        start = seconds + float(scene.get('duration_seconds', scene.get('duration', 0)) or 0)
    return result


def timestamp(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class _SceneDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        painter.save()
        box = option.rect.adjusted(5, 5, -5, -5)
        painter.fillRect(box, option.palette.alternateBase())
        image_rect = QRect(box.left() + 4, box.top() + 4, box.width() - 8, 117)
        pixmap = self.panel.cache.request(str(row[2].get('image_path') or ''))
        if pixmap.isNull():
            painter.fillRect(image_rect, option.palette.base())
            painter.setPen(option.palette.text().color())
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter,
                             self.panel.tr('video_storyboard_frame_pending', 'Frame not generated')
                             if not Path(str(row[2].get('image_path') or '')).is_file() else '…')
        else:
            painter.drawPixmap(image_rect, pixmap)
        painter.setPen(option.palette.text().color())
        painter.drawText(box.adjusted(5, 125, -5, 0), Qt.AlignmentFlag.AlignTop,
                         self.panel.caption(row))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(230, 165)


class SceneImagePreview(QDialog):
    def __init__(self, panel, row, tr=None, caption=None):
        super().__init__(panel)
        self.translate = tr or panel.tr
        self.caption = caption or panel.caption
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.panel = panel
        self.scene_id = str(row[2].get('scene_id') or row[2].get('id') or '')
        self.pixmap = QPixmap()
        self._image_signature = None
        self.setWindowTitle(self.caption(row))
        layout = QVBoxLayout(self)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout.addWidget(self.image, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        available = self.screen().availableGeometry()
        self.resize(int(available.width() * .9), int(available.height() * .9))
        self.update_scene(row)

    def update_scene(self, row):
        self.setWindowTitle(self.caption(row))
        path = str(row[2].get('image_path') or '')
        try:
            stat = Path(path).stat()
            signature = (path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = (path, None, None)
        if signature != self._image_signature:
            self._image_signature = signature
            self.pixmap = QPixmap(path)
            self._fit()

    def _fit(self):
        if self.pixmap.isNull():
            self.image.setText(self.translate('video_storyboard_frame_pending', 'Frame not generated'))
        else:
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class EntityScenesPanel(QWidget):
    def __init__(self, tr, record, kind, page, parent=None):
        super().__init__(parent)
        self.tr, self.record, self.kind, self.page = tr, record, kind, page
        self.preview = None
        self.cache = StoryboardThumbnailCache(self)
        self.cache.set_project_dir(getattr(page, '_source_project_dir', ''))
        layout = QVBoxLayout(self)
        self.empty_label = QLabel(tr('entity_scenes_empty', 'No scenes assigned to this entity.'))
        layout.addWidget(self.empty_label)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWrapping(True)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(QSize(230, 165))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(_SceneDelegate(self))
        self.list.itemClicked.connect(self._open)
        self.list.itemActivated.connect(self._open)
        layout.addWidget(self.list)
        self.cache.changed.connect(self.list.viewport().update)
        if page is not None:
            page.scenesChanged.connect(self.refresh)
            page.projectChanged.connect(self.refresh)
        self.refresh()

    def caption(self, row):
        return self.tr('video_storyboard_scene_number', 'Scene {number}', number=row[0]) + ' · ' + timestamp(row[1])

    def refresh(self, *_):
        rows = matching_scenes(self.record, self.kind, self.page.scenes() if self.page else [])
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        self.empty_label.setVisible(not rows)
        for row in rows:
            item = QListWidgetItem(self.caption(row))
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(self.caption(row))
            self.list.addItem(item)
        self.list.doItemsLayout()
        self.list.verticalScrollBar().setValue(scroll)
        if self.preview:
            row = next((r for r in rows if str(r[2].get('scene_id') or r[2].get('id') or '') == self.preview.scene_id), None)
            if row:
                self.preview.update_scene(row)
            else:
                self.preview.close()

    def _open(self, item):
        if self.preview:
            self.preview.close()
        self.preview = SceneImagePreview(self, item.data(Qt.ItemDataRole.UserRole))
        self.preview.finished.connect(self._closed)
        self.preview.open()

    def _closed(self, *_):
        self.preview = None

# Compatibility for existing callers.
_ScenePreview = SceneImagePreview

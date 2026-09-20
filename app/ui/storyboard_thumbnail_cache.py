"""Asynchronous, project-local timeline thumbnails with a bounded GUI cache."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import uuid

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap


class _Result(QObject):
    ready = Signal(object, QImage)


class _Load(QRunnable):
    def __init__(self, key, source: str, destination: Path | None, size: QSize):
        super().__init__()
        self.key, self.source, self.destination, self.size = key, source, destination, size
        self.result = _Result()

    def run(self):
        image = QImage()
        try:
            if self.destination and self.destination.is_file():
                image.load(str(self.destination))
            if image.isNull():
                reader = QImageReader(self.source)
                reader.setAutoTransform(True)
                original = reader.size()
                if original.isValid():
                    reader.setScaledSize(original.scaled(self.size, Qt.AspectRatioMode.KeepAspectRatioByExpanding))
                image = reader.read()
                del reader  # Release the source file before encoding the thumbnail.
                if not image.isNull():
                    image = image.scaled(self.size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
                    image = image.copy((image.width() - self.size.width()) // 2,
                                       (image.height() - self.size.height()) // 2,
                                       self.size.width(), self.size.height())
                    if self.destination:
                        self.destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = self.destination.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                        try:
                            if image.save(str(temporary), 'PNG'):
                                os.replace(temporary, self.destination)
                        finally:
                            temporary.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass  # A read-only project can still use the in-memory thumbnail.
        self.result.ready.emit(self.key, image)


_POOL = QThreadPool()
_POOL.setMaxThreadCount(2)


class StoryboardThumbnailCache(QObject):
    changed = Signal()

    def __init__(self, parent=None, *, budget_bytes=64 * 1024 * 1024):
        super().__init__(parent)
        self.budget_bytes = budget_bytes
        self.memory_bytes = 0
        self.project_dir = ''
        self._generation = 0
        self._items = OrderedDict()
        self._pending = {}

    def set_project_dir(self, directory: str):
        if directory != self.project_dir:
            self.project_dir = directory
            self._generation += 1
            self._items.clear()
            self.memory_bytes = 0

    def invalidate(self, source: str):
        for key in list(self._items):
            if key[1] == source:
                self.memory_bytes -= self._items.pop(key)[1]

    def request(self, source: str, width=320, height=180) -> QPixmap:
        if not source:
            return QPixmap()
        try:
            stat = Path(source).stat()
        except OSError:
            return QPixmap()
        key = (self._generation, source, stat.st_mtime_ns, stat.st_size, width, height)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key][0]
        if key not in self._pending and len(self._pending) < 32:
            digest = hashlib.sha256(repr(key[1:]).encode()).hexdigest()
            destination = (Path(self.project_dir) / 'storyboard' / 'thumbnails' / f'{digest}.png'
                           if self.project_dir else None)
            task = _Load(key, source, destination, QSize(width, height))
            task.result.ready.connect(self._ready)
            self._pending[key] = task
            _POOL.start(task)
        return QPixmap()

    def _ready(self, key, image):
        self._pending.pop(key, None)
        if key[0] != self._generation:
            return
        pixmap = QPixmap.fromImage(image)
        cost = max(256, pixmap.width() * pixmap.height() * 4)
        if cost <= self.budget_bytes:
            while self._items and self.memory_bytes + cost > self.budget_bytes:
                self.memory_bytes -= self._items.popitem(last=False)[1][1]
            self._items[key] = (pixmap, cost)
            self.memory_bytes += cost
        self.changed.emit()

"""Background cache scans with coalescing and short-lived display caching."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot

from app.core.wav_cache_stats import scan_wav_cache


class _Result(QObject):
    ready = Signal(int, object, str)


class _Scan(QRunnable):
    def __init__(self, revision, args):
        super().__init__()
        self.revision = revision
        self.args = args
        self.result = _Result()

    def run(self):
        try:
            stats = scan_wav_cache(*self.args)
        except Exception as exc:
            self.result.ready.emit(self.revision, None, str(exc))
        else:
            self.result.ready.emit(self.revision, stats, "")


# Outlive individual windows: closing one must not destroy a running thread.
_POOL = QThreadPool()
_POOL.setMaxThreadCount(2)


class WavCacheStatsController(QObject):
    ready = Signal(object)
    failed = Signal(str)
    busy = Signal()

    def __init__(self, parent=None, *, ttl_seconds=30.0):
        super().__init__(parent)
        self.ttl_seconds = ttl_seconds
        self._revision = 0
        self._key = None
        self._pending = None
        self._task = None
        self._cached = None
        self._cached_at = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)

    def request(self, store, current_id, temp_root, active_dirs=(), *, force=False):
        key = (str(store.db_path), current_id, str(temp_root), tuple(active_dirs))
        if key == self._key and not force:
            if self._task is not None or self._pending is not None:
                return
            if self._cached is not None and time.monotonic() - self._cached_at < self.ttl_seconds:
                self.ready.emit(self._cached)
                return
        self._revision += 1
        self._key = key
        self._cached = None
        self._pending = (self._revision, (store, current_id, Path(temp_root), tuple(active_dirs)))
        self.busy.emit()
        if self._task is None:
            self._timer.start(0)

    @Slot()
    def _start(self):
        if self._task is not None or self._pending is None:
            return
        revision, args = self._pending
        self._pending = None
        self._task = _Scan(revision, args)
        self._task.result.ready.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        _POOL.start(self._task)

    @Slot(int, object, str)
    def _finished(self, revision, stats, error):
        self._task = None
        if revision == self._revision:
            if stats is not None:
                self._cached = stats
                self._cached_at = time.monotonic()
                self.ready.emit(stats)
            else:
                self.failed.emit(error)
        if self._pending is not None:
            self._timer.start(0)

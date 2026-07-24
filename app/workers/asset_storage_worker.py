from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.core.asset_storage import (
    AssetStorageCancelled,
    AssetStorageError,
    AssetStorageManager,
)


class AssetStorageMoveWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: AssetStorageManager, base_dir: Path) -> None:
        super().__init__()
        self.manager = manager
        self.base_dir = base_dir
        self._cancel_token = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self.manager.transfer(
                self.base_dir,
                self.progress.emit,
                self._cancel_token,
            )
            self.finished.emit(result)
        except AssetStorageCancelled:
            self.cancelled.emit()
        except AssetStorageError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected AI asset storage error: {exc}")

    def request_cancel(self) -> None:
        self._cancel_token.set()

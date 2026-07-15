from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.core.update_manager import UpdateCancelled, UpdateError, UpdateInfo, UpdateManager


class UpdateCheckWorker(QObject):
    update_available = Signal(object)
    no_update = Signal()
    failed = Signal(str)
    done = Signal()

    def __init__(self, manager: UpdateManager) -> None:
        super().__init__()
        self.manager = manager

    @Slot()
    def run(self) -> None:
        try:
            info = self.manager.check_for_update()
            if info is None:
                self.no_update.emit()
            else:
                self.update_available.emit(info)
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected update check error: {exc}")
        finally:
            self.done.emit()


class UpdateDownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()

    def __init__(self, manager: UpdateManager, info: UpdateInfo) -> None:
        super().__init__()
        self.manager = manager
        self.info = info
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            path = self.manager.download_update(
                self.info,
                progress_callback=self._report_progress,
                cancel_event=self.cancel_event,
            )
            self.finished.emit(path)
        except UpdateCancelled:
            self.cancelled.emit()
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected update download error: {exc}")
        finally:
            self.done.emit()

    def _report_progress(self, downloaded: int, total: int) -> None:
        percent = int(downloaded * 100 / total) if total > 0 else -1
        self.progress.emit(
            max(-1, min(100, percent)),
            f"{self._format_bytes(downloaded)} / {self._format_bytes(total)}"
            if total > 0
            else self._format_bytes(downloaded),
        )

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{int(size)} B"

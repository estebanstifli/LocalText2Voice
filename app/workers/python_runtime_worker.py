from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.python_runtime_manager import (
    PythonRuntimeCancelled,
    PythonRuntimeError,
    PythonRuntimeManager,
)


class PythonRuntimeWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: PythonRuntimeManager, operation: str) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.cancel_token = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install":
                destination = self.manager.install(
                    self.progress.emit,
                    self.cancel_token,
                )
                self.finished.emit(str(destination))
            elif self.operation == "remove":
                self.manager.uninstall()
                self.finished.emit(str(self.manager.runtime_dir))
            else:
                raise PythonRuntimeError("Unknown Python runtime operation.")
        except PythonRuntimeCancelled:
            self.cancelled.emit()
        except PythonRuntimeError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Python runtime error: {exc}")

    def request_cancel(self) -> None:
        self.cancel_token.set()
        self.manager.cancel()

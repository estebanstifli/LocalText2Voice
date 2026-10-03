from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot

from app.tts.base import TTSCancelled
from app.tts.indextts_engine import IndexTTSTTSEngine
from app.tts.indextts_manager import IndexTTSManager, IndexTTSCancelled
from app.tts.python_runtime_manager import PythonRuntimeCancelled


class IndexTTSWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: IndexTTSManager, operation: str, config: dict, text=""):
        super().__init__()
        self.manager, self.operation, self.config, self.text = (
            manager,
            operation,
            config,
            text,
        )
        self.engine = None

    @Slot()
    def run(self):
        try:
            if self.operation == "install":
                path = self.manager.install(
                    self.config["device"], self.config["dtype"], self.progress.emit
                )
            elif self.operation == "remove":
                self.manager.uninstall()
                path = self.manager.install_dir
            elif self.operation == "preview":
                path = (
                    Path(tempfile.gettempdir()) / f"ltv_indextts_{uuid.uuid4().hex}.wav"
                )
                self.engine = IndexTTSTTSEngine(self.manager)
                self.engine.set_log_callback(
                    lambda message: self.progress.emit(0, 0, message)
                )
                self.engine.synthesize_to_wav(self.text, path, self.config)
            else:
                raise ValueError("Unknown IndexTTS operation.")
            self.finished.emit(str(path))
        except (IndexTTSCancelled, PythonRuntimeCancelled, TTSCancelled):
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if self.engine is not None:
                self.engine.close()

    def request_cancel(self):
        self.manager.cancel()
        if self.engine is not None:
            self.engine.cancel_current()

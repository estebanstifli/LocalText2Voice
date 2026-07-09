from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.kokoro_manager import (
    KokoroDownloadCancelled,
    KokoroError,
    KokoroManager,
)
from app.tts.base import TTSEngineError
from app.tts.kokoro_preview import kokoro_preview_text_for_language
from app.tts.python_runtime_manager import PythonRuntimeCancelled, PythonRuntimeError


class KokoroInstallWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: KokoroManager, operation: str) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install":
                destination = self.manager.install(self.progress.emit)
                self.finished.emit(str(destination))
            elif self.operation == "remove":
                self.manager.uninstall()
                self.finished.emit(str(self.manager.install_dir))
            else:
                raise KokoroError("Unknown Kokoro operation.")
        except (KokoroDownloadCancelled, PythonRuntimeCancelled):
            self.cancelled.emit()
        except (KokoroError, PythonRuntimeError, TTSEngineError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Kokoro error: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()


class KokoroPreviewWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        manager: KokoroManager,
        voice: str,
        lang: str,
        speed: float,
        preview_text: str | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.voice = voice
        self.lang = lang
        self.speed = speed
        self.preview_text = preview_text or kokoro_preview_text_for_language(lang)

    @Slot()
    def run(self) -> None:
        try:
            output_path = Path(tempfile.gettempdir()) / "localtext2voice_kokoro_preview.wav"
            self.manager.synthesize(
                self.preview_text,
                self.voice,
                self.lang,
                self.speed,
                output_path,
                "auto",
            )
            self.finished.emit(str(output_path))
        except (KokoroError, PythonRuntimeError, TTSEngineError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Kokoro preview error: {exc}")

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.core.text_normalization import normalize_text_for_speech
from app.tts.russian_normalization_manager import (
    RussianNormalizationCancelled,
    RussianNormalizationError,
    RussianNormalizationManager,
)
from app.tts.python_runtime_manager import PythonRuntimeCancelled, PythonRuntimeError


class RussianNormalizationInstallWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: RussianNormalizationManager, operation: str) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install":
                path = self.manager.install(self.progress.emit)
                self.finished.emit(str(path))
            elif self.operation == "remove":
                self.manager.uninstall()
                self.finished.emit(str(self.manager.dependency_dir))
            else:
                raise RussianNormalizationError("Unknown Russian normalization operation.")
        except (RussianNormalizationCancelled, PythonRuntimeCancelled):
            self.cancelled.emit()
        except (RussianNormalizationError, PythonRuntimeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Russian normalization error: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()


class RussianNormalizationPreviewWorker(QObject):
    finished = Signal(str, str, bool)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        manager: RussianNormalizationManager,
        text: str,
        *,
        language: str,
        language_hint: str,
        rules: object,
        engine_id: str,
        db_path: Path | None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.text = text
        self.language = language
        self.language_hint = language_hint
        self.rules = rules
        self.engine_id = engine_id
        self.db_path = db_path

    @Slot()
    def run(self) -> None:
        try:
            result = normalize_text_for_speech(
                self.text,
                enabled=True,
                language=self.language,
                language_hint=self.language_hint,
                preserve_markup=True,
                rules=self.rules,
                db_path=self.db_path,
                russian_silero_enabled=True,
                engine_id=self.engine_id,
                russian_manager=self.manager,
            )
            self.finished.emit(
                result.text,
                str(result.language or ""),
                result.russian_silero_applied,
            )
        except RussianNormalizationCancelled:
            self.cancelled.emit()
        except (RussianNormalizationError, PythonRuntimeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Russian normalization preview failed: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()

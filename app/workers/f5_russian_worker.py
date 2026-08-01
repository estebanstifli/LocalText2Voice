from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.base import TTSEngineError
from app.tts.f5_russian_manager import (
    F5RussianCancelled,
    F5RussianError,
    F5RussianManager,
)
from app.tts.python_runtime_manager import PythonRuntimeCancelled, PythonRuntimeError
from app.utils.gpu_detection import (
    detect_gpus,
    format_gpu_detection,
    format_runtime_cuda_info,
)


class F5RussianInstallWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        manager: F5RussianManager,
        operation: str,
        model: str,
        device: str,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.model = model
        self.device = device

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install":
                destination = self.manager.install(
                    self.model,
                    self.device,
                    self.progress.emit,
                )
                self.finished.emit(str(destination))
            elif self.operation == "remove":
                self.manager.uninstall()
                self.manager.uninstall_runtime()
                self.finished.emit(str(self.manager.install_dir))
            else:
                raise F5RussianError("Unknown F5Russian operation.")
        except (F5RussianCancelled, PythonRuntimeCancelled):
            self.cancelled.emit()
        except (F5RussianError, PythonRuntimeError, TTSEngineError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected F5Russian error: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()


class F5RussianPreviewWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        manager: F5RussianManager,
        voice_config: dict[str, object],
        preview_text: str,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.voice_config = voice_config
        self.preview_text = preview_text

    @Slot()
    def run(self) -> None:
        try:
            output_path = (
                Path(tempfile.gettempdir()) / "localtext2voice_f5_russian_preview.wav"
            )
            self.manager.synthesize(
                self.preview_text,
                output_path,
                self.voice_config,
            )
            self.finished.emit(str(output_path))
        except (F5RussianError, TTSEngineError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected F5Russian preview error: {exc}")


class F5RussianHardwareWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        manager: F5RussianManager,
        include_runtime: bool,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.include_runtime = include_runtime

    @Slot()
    def run(self) -> None:
        try:
            system_text = format_gpu_detection(detect_gpus())
            runtime_text = ""
            if self.include_runtime:
                runtime_text = format_runtime_cuda_info(
                    self.manager.cuda_info(),
                    engine_name="F5Russian",
                )
            if runtime_text:
                self.finished.emit(f"{system_text}\n{runtime_text}")
            else:
                self.finished.emit(system_text)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"GPU detection failed: {exc}")

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.core.audio_pipeline import (
    AudioGenerationOptions,
    AudioPipeline,
    AudioPipelineError,
    GenerationCancelled,
)
from app.tts.piper_engine import PiperTTSEngine


class GenerationWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        text: str,
        options: AudioGenerationOptions,
        piper_path: Path,
    ) -> None:
        super().__init__()
        self.text = text
        self.options = options
        self.piper_path = piper_path
        self._pipeline: AudioPipeline | None = None
        self._cancel_requested = False

    @Slot()
    def run(self) -> None:
        try:
            engine = PiperTTSEngine(self.piper_path)
            pipeline = AudioPipeline(
                engine,
                progress_callback=self.progress.emit,
                log_callback=self.log.emit,
            )
            self._pipeline = pipeline
            if self._cancel_requested:
                pipeline.cancel()
            outputs = pipeline.generate(self.text, self.options)
            self.finished.emit([str(path) for path in outputs])
        except GenerationCancelled:
            self.cancelled.emit()
        except AudioPipelineError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected generation error: {exc}")
        finally:
            self._pipeline = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.cancel()

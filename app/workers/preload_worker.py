from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.base import BaseTTSEngine, TTSEngineError
from app.tts.engine_registry import create_tts_engine


class TTSEnginePreloadWorker(QObject):
    log = Signal(str)
    finished = Signal(str, object)
    failed = Signal(str)

    def __init__(
        self,
        engine_id: str,
        piper_path: Path,
        voice_config: dict[str, Any],
    ) -> None:
        super().__init__()
        self.engine_id = engine_id
        self.piper_path = piper_path
        self.voice_config = voice_config
        self.engine: BaseTTSEngine | None = None
        self._cancel_requested = False

    @Slot()
    def run(self) -> None:
        try:
            engine = create_tts_engine(self.engine_id, self.piper_path)
            self.engine = engine
            engine.set_log_callback(self.log.emit)
            if self._cancel_requested:
                engine.close()
                self.failed.emit("TTS engine preload cancelled.")
                return
            engine.preload(self.voice_config)
            if self._cancel_requested:
                engine.close()
                self.failed.emit("TTS engine preload cancelled.")
                return
            self.finished.emit(self.engine_id, engine)
        except TTSEngineError as exc:
            self._close_engine()
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self._close_engine()
            self.failed.emit(f"Unexpected preload error: {exc}")

    def request_cancel(self) -> None:
        self._cancel_requested = True
        if self.engine is not None:
            self.engine.cancel_current()
            self.engine.close()

    def _close_engine(self) -> None:
        if self.engine is None:
            return
        try:
            self.engine.close()
        finally:
            self.engine = None

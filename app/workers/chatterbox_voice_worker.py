from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.chatterbox_voice_manager import (
    ChatterboxReferenceVoice,
    ChatterboxReferenceVoiceManager,
    ChatterboxVoiceCancelled,
    ChatterboxVoiceError,
)


class ChatterboxVoiceWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        manager: ChatterboxReferenceVoiceManager,
        operation: str,
        voice: ChatterboxReferenceVoice | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.voice = voice

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install" and self.voice is not None:
                path = self.manager.install(self.voice, self.progress.emit)
                self.finished.emit(str(path))
            elif self.operation == "install_pack":
                paths = self.manager.install_default_pack(self.progress.emit)
                self.finished.emit(f"{len(paths)} Chatterbox reference voice(s)")
            elif self.operation == "remove" and self.voice is not None:
                self.manager.remove(self.voice)
                self.finished.emit(self.voice.display_name)
            else:
                raise ChatterboxVoiceError("Unknown Chatterbox voice operation.")
        except ChatterboxVoiceCancelled:
            self.cancelled.emit()
        except ChatterboxVoiceError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Chatterbox voice error: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()

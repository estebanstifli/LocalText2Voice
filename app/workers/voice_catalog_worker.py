from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.voice_catalog import (
    HuggingFaceVoiceCatalog,
    RemoteVoice,
    VoiceCatalogError,
    VoiceDownloadCancelled,
)


class VoiceCatalogWorker(QObject):
    catalog_ready = Signal(object)
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        catalog: HuggingFaceVoiceCatalog,
        operation: str,
        voice: RemoteVoice | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.operation = operation
        self.voice = voice

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "catalog":
                voices = self.catalog.fetch_catalog(
                    lambda page, message: self.progress.emit(page, 0, message)
                )
                self.catalog_ready.emit(voices)
            elif self.operation == "install" and self.voice is not None:
                self.catalog.install(self.voice, self.progress.emit)
                self.finished.emit(self.voice.display_name)
            elif self.operation == "remove" and self.voice is not None:
                self.catalog.remove(self.voice)
                self.finished.emit(self.voice.display_name)
            else:
                raise VoiceCatalogError("Unknown voice manager operation.")
        except VoiceDownloadCancelled:
            self.cancelled.emit()
        except VoiceCatalogError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected voice manager error: {exc}")

    def request_cancel(self) -> None:
        self.catalog.cancel()

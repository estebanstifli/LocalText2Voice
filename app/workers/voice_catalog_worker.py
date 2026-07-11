from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.tts.voice_catalog import (
    HuggingFaceVoiceCatalog,
    RemoteVoice,
    VoiceCatalogError,
    VoiceDownloadCancelled,
)
from app.tts.voice_gallery_manager import (
    GalleryVoice,
    VoiceGalleryCancelled,
    VoiceGalleryError,
    VoiceGalleryManager,
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


class VoiceGalleryWorker(QObject):
    catalog_ready = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        manager: VoiceGalleryManager,
        operation: str,
        voice: GalleryVoice | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.voice = voice

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "sync":
                count = self.manager.sync(progress_callback=self.progress.emit)
                self.catalog_ready.emit(count)
                self.finished.emit(f"Voice gallery synced: {count} voice(s).")
            elif self.operation == "install" and self.voice is not None:
                self.manager.install(self.voice, self.progress.emit)
                self.finished.emit(f"Installed voice: {self.voice.name}")
            elif self.operation == "remove" and self.voice is not None:
                self.manager.uninstall(self.voice)
                self.finished.emit(f"Removed voice: {self.voice.name}")
            else:
                raise VoiceGalleryError("Unknown voice gallery operation.")
        except VoiceGalleryCancelled:
            self.cancelled.emit()
        except VoiceGalleryError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected voice gallery error: {exc}")

    def request_cancel(self) -> None:
        self.manager.cancel()

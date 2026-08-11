from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot

from app.verification.faster_whisper_manager import (
    FasterWhisperCancelled,
    FasterWhisperError,
    FasterWhisperManager,
    FasterWhisperVerifier,
)


class VoiceTranscriptionWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        manager: FasterWhisperManager,
        audio_path: Path,
        *,
        language: str = "auto",
        device: str = "cpu",
        compute_type: str = "int8",
        translate: Callable[..., str] | None = None,
    ) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._translate = translate or (lambda _key, default, **_values: default)
        self.verifier = FasterWhisperVerifier(manager)

    @Slot()
    def run(self) -> None:
        try:
            result = self.verifier.transcribe(
                self.audio_path,
                language=self.language,
                beam_size=1,
                device=self.device,
                compute_type=self.compute_type,
            )
            self.finished.emit(str(result.get("text", "")).strip())
        except FasterWhisperCancelled:
            self.failed.emit(
                self._translate(
                    "clone_voice_transcription_cancelled",
                    "Transcription was cancelled.",
                )
            )
        except FasterWhisperError as exc:
            self.failed.emit(
                self._translate(
                    "clone_voice_transcription_failed",
                    "Automatic transcription failed: {message}",
                    message=str(exc),
                )
            )
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(
                self._translate(
                    "clone_voice_transcription_failed",
                    "Automatic transcription failed: {message}",
                    message=str(exc),
                )
            )
        finally:
            self.verifier.close(force=True)

    def request_cancel(self) -> None:
        self.verifier.cancel_current()

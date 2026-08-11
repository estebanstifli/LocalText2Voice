from __future__ import annotations

import threading
import traceback
import wave
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot

from app.core.audio_capture import CAPTURE_BLOCK_FRAMES, CAPTURE_SAMPLE_RATE


class AudioCaptureWorker(QObject):
    level_changed = Signal(float)
    finished = Signal(str, float)
    failed = Signal(str)

    def __init__(
        self,
        device_id: str,
        output_path: Path,
        *,
        loopback: bool,
        max_seconds: float,
        translate: Callable[..., str] | None = None,
    ) -> None:
        super().__init__()
        self.device_id = device_id
        self.output_path = output_path
        self.loopback = loopback
        self.max_seconds = max(1.0, float(max_seconds))
        self._translate = translate or (lambda _key, default, **_values: default)
        self._stop_requested = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            import numpy as np
            import soundcard as sc

            source = sc.get_microphone(
                self.device_id,
                include_loopback=self.loopback,
            )
            if source is None:
                raise RuntimeError(
                    self._translate(
                        "clone_voice_selected_source_missing",
                        "The selected audio source is no longer available.",
                    )
                )

            chunks: list[object] = []
            captured_frames = 0
            maximum_frames = round(CAPTURE_SAMPLE_RATE * self.max_seconds)
            # Keep the native channel count. WASAPI loopback is unreliable when
            # asked for a single channel; the completed recording is mixed to
            # mono after capture instead.
            with source.recorder(
                samplerate=CAPTURE_SAMPLE_RATE,
                channels=None,
                blocksize=CAPTURE_BLOCK_FRAMES * 2,
            ) as recorder:
                while not self._stop_requested.is_set() and captured_frames < maximum_frames:
                    requested = min(CAPTURE_BLOCK_FRAMES, maximum_frames - captured_frames)
                    block = np.asarray(
                        recorder.record(numframes=requested),
                        dtype=np.float32,
                    )
                    if not block.size:
                        continue
                    if block.ndim == 1:
                        mono = block
                    else:
                        mono = block.mean(axis=1, dtype=np.float32)
                    chunks.append(mono.copy())
                    captured_frames += int(mono.shape[0])
                    self.level_changed.emit(
                        min(1.0, float(np.max(np.abs(mono))) if mono.size else 0.0)
                    )

            if not chunks:
                raise RuntimeError(
                    self._translate(
                        "clone_voice_no_audio_received",
                        "No audio was received from the selected source.",
                    )
                )
            samples = np.concatenate(chunks)
            pcm = (
                np.clip(samples, -1.0, 1.0) * np.float32(32767.0)
            ).astype("<i2", copy=False)
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.output_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(CAPTURE_SAMPLE_RATE)
                output.writeframes(pcm.tobytes())
            duration = float(samples.shape[0]) / CAPTURE_SAMPLE_RATE
            self.finished.emit(str(self.output_path), duration)
        except Exception as exc:
            traceback.print_exc()
            try:
                self.output_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(
                self._translate(
                    "clone_voice_recording_failed",
                    "Audio recording failed: {message}",
                    message=str(exc),
                )
            )

    def request_stop(self) -> None:
        self._stop_requested.set()

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.core.audio_capture import list_audio_capture_devices
from app.workers.audio_capture_worker import AudioCaptureWorker


class _FakeSoundCard:
    def __init__(self) -> None:
        self.microphone = SimpleNamespace(
            id="mic-1",
            name="Studio microphone",
            isloopback=False,
        )
        self.speaker = SimpleNamespace(
            id="speaker-1",
            name="Main speakers",
            isloopback=True,
        )

    def all_microphones(self, include_loopback: bool = False):
        assert include_loopback
        return [self.speaker, self.microphone]

    def default_microphone(self):
        return self.microphone

    def default_speaker(self):
        return self.speaker


class _FakeRecorder:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def record(self, numframes: int):
        left = np.full(numframes, 0.25, dtype=np.float32)
        right = np.full(numframes, -0.05, dtype=np.float32)
        return np.column_stack((left, right))


class _FakeMicrophone:
    def recorder(self, **_kwargs):
        return _FakeRecorder()


class AudioCaptureTests(unittest.TestCase):
    def test_device_listing_separates_microphones_and_system_loopbacks(self) -> None:
        backend = _FakeSoundCard()

        microphones = list_audio_capture_devices(
            "microphone", soundcard_module=backend
        )
        loopbacks = list_audio_capture_devices("system", soundcard_module=backend)

        self.assertEqual([device.identifier for device in microphones], ["mic-1"])
        self.assertTrue(microphones[0].is_default)
        self.assertEqual([device.identifier for device in loopbacks], ["speaker-1"])
        self.assertTrue(loopbacks[0].is_default)

    def test_capture_worker_writes_mono_pcm_wav(self) -> None:
        fake_module = SimpleNamespace(
            get_microphone=lambda *_args, **_kwargs: _FakeMicrophone()
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "recording.wav"
            worker = AudioCaptureWorker(
                "speaker-1",
                output,
                loopback=True,
                max_seconds=1.0,
            )
            completed: list[tuple[str, float]] = []
            failures: list[str] = []
            worker.finished.connect(lambda path, duration: completed.append((path, duration)))
            worker.failed.connect(failures.append)
            with patch.dict("sys.modules", {"soundcard": fake_module}):
                worker.run()

            self.assertFalse(failures)
            self.assertEqual(len(completed), 1)
            self.assertAlmostEqual(completed[0][1], 1.0, places=2)
            with wave.open(str(output), "rb") as recording:
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(recording.getsampwidth(), 2)
                self.assertEqual(recording.getframerate(), 48_000)
                self.assertEqual(recording.getnframes(), 48_000)


if __name__ == "__main__":
    unittest.main()

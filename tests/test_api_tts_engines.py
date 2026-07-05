from __future__ import annotations

import io
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

from app.tts.api_engines import (
    AzureTTSEngine,
    ElevenLabsTTSEngine,
    OpenAITTSEngine,
)
from app.tts.base import TTSEngineError
from app.tts.engine_registry import create_tts_engine
from app.tts.piper_engine import PiperTTSEngine


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    sample_rate = 16000
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(sample_rate * 0.05)):
            value = int(2000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        audio.writeframes(bytes(frames))
    return buffer.getvalue()


class FakeOpenAIEngine(OpenAITTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.payload: dict[str, Any] = {}

    def _post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[bytes, str]:
        self.payload = json.loads(body.decode("utf-8"))
        self.headers = headers
        self.path = path
        return wav_bytes(), "audio/wav"


class FakeElevenLabsEngine(ElevenLabsTTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.path = ""
        self.payload: dict[str, Any] = {}

    def _post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[bytes, str]:
        self.path = path
        self.payload = json.loads(body.decode("utf-8"))
        pcm = struct.pack("<h", 0) * 2400
        return pcm, "application/octet-stream"


class ApiTTSEngineTests(unittest.TestCase):
    def test_openai_engine_builds_wav_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            engine = FakeOpenAIEngine()
            output = Path(temporary_name) / "openai.wav"

            result = engine.synthesize_to_wav(
                "Hello from LocalText2Voice.",
                output,
                {
                    "api_key": "test-key",
                    "model": "gpt-4o-mini-tts",
                    "voice": "marin",
                    "speed": 1.25,
                    "instructions": "Warm podcast narrator.",
                },
            )

            self.assertEqual(result, output)
            self.assertEqual(engine.path, "/v1/audio/speech")
            self.assertEqual(engine.payload["response_format"], "wav")
            self.assertEqual(engine.payload["voice"], "marin")
            self.assertEqual(engine.payload["speed"], 1.25)
            self.assertEqual(
                engine.payload["instructions"],
                "Warm podcast narrator.",
            )
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getframerate(), 16000)

    def test_openai_engine_requires_api_key(self) -> None:
        engine = OpenAITTSEngine()
        with self.assertRaisesRegex(TTSEngineError, "API key"):
            engine.validate({"model": "gpt-4o-mini-tts", "voice": "marin"})

    def test_elevenlabs_pcm_response_is_wrapped_as_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            engine = FakeElevenLabsEngine()
            output = Path(temporary_name) / "elevenlabs.wav"

            engine.synthesize_to_wav(
                "Hello from ElevenLabs.",
                output,
                {
                    "api_key": "test-key",
                    "voice_id": "voice-123",
                    "model_id": "eleven_flash_v2_5",
                    "output_format": "pcm_24000",
                    "stability": 0.4,
                    "similarity_boost": 0.8,
                    "style": 0.1,
                    "use_speaker_boost": True,
                },
            )

            self.assertIn("output_format=pcm_24000", engine.path)
            self.assertEqual(engine.payload["model_id"], "eleven_flash_v2_5")
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getframerate(), 24000)
                self.assertEqual(audio.getnchannels(), 1)

    def test_azure_ssml_uses_voice_style_and_speed(self) -> None:
        ssml = AzureTTSEngine._ssml(
            "Hello <world>.",
            {
                "voice": "en-US-JennyNeural",
                "style": "cheerful",
                "speed": 1.2,
            },
        )

        self.assertIn('xml:lang="en-US"', ssml)
        self.assertIn('name="en-US-JennyNeural"', ssml)
        self.assertIn('style="cheerful"', ssml)
        self.assertIn('rate="+20%"', ssml)
        self.assertIn("Hello &lt;world&gt;.", ssml)

    def test_registry_keeps_piper_and_api_engines_separate(self) -> None:
        self.assertIsInstance(
            create_tts_engine("piper", Path("piper.exe")),
            PiperTTSEngine,
        )
        self.assertIsInstance(
            create_tts_engine("openai", Path("piper.exe")),
            OpenAITTSEngine,
        )


if __name__ == "__main__":
    unittest.main()

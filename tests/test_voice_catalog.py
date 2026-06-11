from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.tts.voice_catalog import HuggingFaceVoiceCatalog, RemoteVoice


class LocalVoiceCatalog(HuggingFaceVoiceCatalog):
    MODEL_BYTES = b"test-model-content"
    CONFIG_BYTES = json.dumps(
        {"language": {"code": "en_TEST"}, "dataset": "test"}
    ).encode("utf-8")

    def _download_file(
        self,
        repository_path: str,
        destination: Path,
        expected_size: int,
        completed_before: int,
        total_size: int,
        progress,
        message: str,
    ) -> int:
        content = (
            self.CONFIG_BYTES
            if repository_path.endswith(".json")
            else self.MODEL_BYTES
        )
        self.assert_expected_size(content, expected_size)
        destination.write_bytes(content)
        progress(completed_before + len(content), total_size, message)
        return completed_before + len(content)

    @staticmethod
    def assert_expected_size(content: bytes, expected_size: int) -> None:
        if len(content) != expected_size:
            raise AssertionError("Test fixture size mismatch.")


class VoiceCatalogTests(unittest.TestCase):
    def _voice(self) -> RemoteVoice:
        model_hash = hashlib.sha256(LocalVoiceCatalog.MODEL_BYTES).hexdigest()
        return RemoteVoice(
            voice_id="testvoice",
            language="en_TEST",
            speaker="speaker",
            quality="medium",
            model_path="en/en_TEST/speaker/medium/en_TEST-speaker-medium.onnx",
            config_path=(
                "en/en_TEST/speaker/medium/en_TEST-speaker-medium.onnx.json"
            ),
            model_size=len(LocalVoiceCatalog.MODEL_BYTES),
            config_size=len(LocalVoiceCatalog.CONFIG_BYTES),
            model_sha256=model_hash,
        )

    def test_install_and_remove_voice_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name) / "voices"
            catalog = LocalVoiceCatalog(root)
            voice = self._voice()

            destination = catalog.install(voice)

            self.assertTrue(catalog.is_installed(voice))
            self.assertTrue((destination / Path(voice.model_path).name).is_file())
            self.assertFalse((root / ".downloads").exists())

            catalog.remove(voice)

            self.assertFalse(catalog.is_installed(voice))

    def test_parse_repository_entry(self) -> None:
        voice = HuggingFaceVoiceCatalog._parse_voice(
            "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
            {
                "size": 100,
                "lfs": {"size": 100, "oid": "a" * 64},
            },
            {"size": 10},
            {
                "path": (
                    "es/es_ES/davefx/medium/samples/speaker_0.mp3"
                ),
                "size": 1234,
            },
        )
        self.assertIsNotNone(voice)
        assert voice is not None
        self.assertEqual(voice.language, "es_ES")
        self.assertEqual(voice.speaker, "davefx")
        self.assertEqual(voice.quality, "medium")
        self.assertEqual(voice.model_sha256, "a" * 64)
        self.assertTrue(voice.has_sample)
        self.assertEqual(voice.sample_size, 1234)
        self.assertIn("speaker_0.mp3", HuggingFaceVoiceCatalog.sample_url(voice))


if __name__ == "__main__":
    unittest.main()

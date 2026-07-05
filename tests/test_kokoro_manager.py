from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.tts.engine_registry import create_tts_engine
from app.tts.kokoro_engine import KokoroTTSEngine
from app.tts.kokoro_manager import (
    KokoroAsset,
    KokoroDownloadCancelled,
    KokoroManager,
)
from app.tts.kokoro_preview import kokoro_preview_text_for_language


class TinyKokoroManager(KokoroManager):
    ASSETS = (
        KokoroAsset("Tiny model", "model.onnx", "https://example.test/model", 1, 4),
        KokoroAsset("Tiny voices", "voices.bin", "https://example.test/voices", 1, 5),
    )
    MODEL_FILENAME = "model.onnx"
    VOICES_FILENAME = "voices.bin"

    def _download_once(
        self,
        asset: KokoroAsset,
        temporary: Path,
        completed_before: int,
        total_size: int,
        progress,
        attempt: int,
    ) -> int:
        temporary.write_bytes(b"x" * asset.expected_size)
        progress(
            completed_before + asset.expected_size,
            total_size,
            f"Downloaded {asset.name}",
        )
        return completed_before + asset.expected_size


class CancellingKokoroManager(TinyKokoroManager):
    def _download_once(
        self,
        asset: KokoroAsset,
        temporary: Path,
        completed_before: int,
        total_size: int,
        progress,
        attempt: int,
    ) -> int:
        self.cancel()
        self._check_cancelled()
        return completed_before


class KokoroManagerTests(unittest.TestCase):
    def test_install_writes_manifest_and_assets_then_uninstalls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            manager = TinyKokoroManager(Path(temporary_name) / "kokoro")
            progress: list[tuple[int, int, str]] = []

            destination = manager.install(
                lambda current, total, message: progress.append(
                    (current, total, message)
                )
            )

            self.assertEqual(destination, manager.install_dir)
            self.assertTrue(manager.is_installed())
            self.assertTrue(manager.model_path.is_file())
            self.assertTrue(manager.voices_path.is_file())
            manifest = manager.install_manifest()
            self.assertEqual(manifest["state"], "installed")
            self.assertEqual(manifest["version"], manager.VERSION)
            self.assertEqual(len(manifest["files"]), 2)
            self.assertTrue(progress)

            manager.uninstall()
            self.assertFalse(manager.install_dir.exists())

    def test_cancel_before_install_stops_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            manager = CancellingKokoroManager(Path(temporary_name) / "kokoro")

            with self.assertRaises(KokoroDownloadCancelled):
                manager.install()
            self.assertEqual(manager.install_manifest()["state"], "cancelled")

    def test_registry_creates_kokoro_engine(self) -> None:
        self.assertIsInstance(
            create_tts_engine("kokoro", Path("piper.exe")),
            KokoroTTSEngine,
        )

    def test_preview_text_matches_voice_language(self) -> None:
        self.assertEqual(
            kokoro_preview_text_for_language("es"),
            "La luna esta preciosa esta noche.",
        )
        self.assertIn("moon", kokoro_preview_text_for_language("en-gb").lower())
        self.assertEqual(
            kokoro_preview_text_for_language("unknown"),
            kokoro_preview_text_for_language("en"),
        )


if __name__ == "__main__":
    unittest.main()

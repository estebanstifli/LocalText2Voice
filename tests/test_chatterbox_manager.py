from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core.settings_manager import DEFAULT_SETTINGS
from app.tts.chatterbox_engine import ChatterboxTTSEngine
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.engine_registry import create_tts_engine, engine_ids


class ChatterboxManagerTests(unittest.TestCase):
    def test_registry_creates_chatterbox_engine(self) -> None:
        self.assertIn("chatterbox", engine_ids())
        self.assertIsInstance(
            create_tts_engine("chatterbox", Path("piper.exe")),
            ChatterboxTTSEngine,
        )

    def test_models_and_languages_are_available(self) -> None:
        manager = ChatterboxManager()
        self.assertIn(
            "multilingual_v3",
            {model.model_id for model in manager.list_models()},
        )
        self.assertIn(
            "es",
            {language.language_id for language in manager.list_languages()},
        )

    def test_runtime_detection_supports_onedir_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            runtime = (
                Path(temporary_name)
                / "engines"
                / "chatterbox"
                / "chatterbox_engine"
                / "chatterbox_engine.exe"
            )
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")

            manager = ChatterboxManager(runtime_path=runtime)

            self.assertTrue(manager.has_runtime())
            self.assertEqual(manager.runtime_command(), [str(runtime)])

    def test_runtime_pack_is_downloaded_and_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            archive_path = root / "runtime.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "chatterbox_engine/chatterbox_engine.exe",
                    b"runtime",
                )

            manager = ChatterboxManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
                runtime_pack_url=archive_path.as_uri(),
            )
            manager.RUNTIME_PACK_MIN_SIZE = 1

            destination = manager.install_runtime()

            self.assertEqual(destination, manager.runtime_dir)
            self.assertTrue(manager.has_runtime())
            self.assertEqual(
                manager.runtime_manifest()["state"],
                "installed",
            )

    def test_default_settings_include_chatterbox(self) -> None:
        settings = DEFAULT_SETTINGS["chatterbox"]
        self.assertEqual(settings["model"], "multilingual_v3")
        self.assertEqual(settings["device"], "cuda")


if __name__ == "__main__":
    unittest.main()

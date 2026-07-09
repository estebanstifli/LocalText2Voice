from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from app.core.settings_manager import DEFAULT_SETTINGS
from app.verification.faster_whisper_manager import (
    FASTER_WHISPER_CLI,
    FasterWhisperManager,
)


class FasterWhisperManagerTests(unittest.TestCase):
    def test_default_settings_include_review(self) -> None:
        review = DEFAULT_SETTINGS["review"]

        self.assertFalse(review["enabled"])
        self.assertEqual(review["model"], "small")
        self.assertEqual(review["compute_type"], "int8")

    def test_runtime_detection_uses_isolated_dependency_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def cancel(self) -> None:
                    pass

            manager = FasterWhisperManager(
                install_dir=root / "whisper",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            (manager.dependency_dir / "faster_whisper").mkdir(parents=True)
            (manager.dependency_dir / "ctranslate2").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest("installed", ["faster-whisper==1.2.1"])
            manager._write_manifest("installed")

            self.assertTrue(manager.has_runtime())
            self.assertTrue(manager.is_installed())
            self.assertEqual(
                manager.runtime_command(),
                [sys.executable, str(manager.cli_path)],
            )

    def test_worker_cli_uses_persistent_json_protocol(self) -> None:
        self.assertIn("WhisperModel", FASTER_WHISPER_CLI)
        self.assertIn('"type": "ready"', FASTER_WHISPER_CLI)
        self.assertIn('"type": "result"', FASTER_WHISPER_CLI)


if __name__ == "__main__":
    unittest.main()

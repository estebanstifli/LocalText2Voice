from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from app.tts.voice_catalog import RemoteVoice
from app.ui.voice_manager_dialog import VoiceManagerDialog
from app.utils.i18n import Translator


class TestVoiceManagerDialog(VoiceManagerDialog):
    def _load_catalog(self) -> None:
        pass


class VoiceManagerUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_preview_column_contains_button_for_voice_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            dialog = TestVoiceManagerDialog(
                Path(temporary_name),
                Translator("en"),
            )
            self.addCleanup(dialog.deleteLater)
            dialog.remote_voices = [
                RemoteVoice(
                    voice_id="test",
                    language="en_TEST",
                    speaker="speaker",
                    quality="medium",
                    model_path="voice.onnx",
                    config_path="voice.onnx.json",
                    model_size=10,
                    config_size=5,
                    sample_path="samples/speaker_0.mp3",
                )
            ]

            dialog._populate_filters()
            dialog._apply_filters()

            self.assertEqual(dialog.table.columnCount(), 6)
            preview = dialog.table.cellWidget(0, 3)
            self.assertIsInstance(preview, QPushButton)
            self.assertTrue(preview.isEnabled())
            self.assertFalse(preview.icon().isNull())


if __name__ == "__main__":
    unittest.main()

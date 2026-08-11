from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ui.clone_voice_dialog import CloneVoiceDialog


class CloneVoiceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_initial_workflow_exposes_three_sources_and_whisper_guidance(self) -> None:
        manager = Mock()
        manager.is_installed.return_value = False
        dialog = CloneVoiceDialog(whisper_manager=manager)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(list(dialog.mode_buttons), ["upload", "record", "system"])
        self.assertTrue(dialog.mode_buttons["upload"].isChecked())
        self.assertEqual(dialog.clone_button.text(), "Save Voice")
        self.assertIn("Whisper is not installed", dialog.transcription_status_label.text())
        self.assertEqual(dialog.reference_text_edit.placeholderText()[:15], "Enter the exact")

    def test_selected_audio_and_manual_transcript_are_returned(self) -> None:
        manager = Mock()
        manager.is_installed.return_value = True
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sample.wav"
            source.write_bytes(b"sample")
            dialog = CloneVoiceDialog(whisper_manager=manager)
            self.addCleanup(dialog.deleteLater)
            dialog._set_audio_source(source, "upload", 12.0)
            dialog.name_edit.setText("Narrator")
            dialog.reference_text_edit.setPlainText("Exact spoken words.")

            values = dialog.values()

        self.assertEqual(values["source"], source)
        self.assertEqual(values["name"], "Narrator")
        self.assertEqual(values["ref_text"], "Exact spoken words.")
        self.assertTrue(dialog.transcribe_button.isEnabled())


if __name__ == "__main__":
    unittest.main()

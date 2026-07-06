from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from app.ui.main_window import MainWindow


class MainWindowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_generation_and_settings_views_are_separate(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        self.assertEqual(window.page_stack.count(), 2)
        self.assertEqual(window.page_stack.currentIndex(), 0)
        self.assertEqual(window.ui_language_combo.count(), 10)
        self.assertTrue(hasattr(window, "import_button"))
        self.assertFalse(hasattr(window, "refresh_voices_button"))
        self.assertFalse(window.import_button.icon().isNull())
        self.assertEqual(window.windowTitle(), "LocalText2Voice")
        logo = window.findChild(QLabel, "logoLabel")
        self.assertIsNotNone(logo)
        self.assertIsNotNone(logo.pixmap())
        self.assertFalse(logo.pixmap().isNull())
        self.assertFalse(window.time_label.isVisible())
        self.assertFalse(window.open_output_button.isVisible())
        self.assertEqual(window._format_duration(65), "01:05")
        author_credit = window.findChild(QLabel, "authorCreditLabel")
        self.assertIsNotNone(author_credit)
        self.assertTrue(author_credit.openExternalLinks())
        self.assertIn("https://andromedanova.com", author_credit.text())

        window.settings_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 1)
        self.assertEqual(window.settings_tabs.count(), 2)
        self.assertEqual(window.tts_engine_combo.count(), 6)
        self.assertEqual(window.tts_engine_combo.currentData(), "piper")
        self.assertEqual(window.engine_settings_stack.count(), 6)
        self.assertGreaterEqual(window.tts_engine_combo.findData("chatterbox"), 0)
        self.assertEqual(window.chatterbox_device_combo.currentData(), "auto")
        self.assertTrue(hasattr(window, "chatterbox_hardware_label"))
        self.assertTrue(hasattr(window, "chatterbox_detect_gpu_button"))
        self.assertFalse(window.chatterbox_detect_gpu_button.icon().isNull())
        self.assertTrue(window.language_combo.isEnabled())

        window._select_combo_data(window.tts_engine_combo, "openai")
        window._on_tts_engine_changed()
        self.assertFalse(window.language_combo.isEnabled())
        self.assertIn("General", window.voice_help_label.text())

        window.back_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 0)

        window.generation_started_at = time.monotonic() - 60
        window.progress_current = 1
        window.progress_total = 2
        window._update_generation_time()
        self.assertIn("01:00", window.time_label.text())

    def test_chatterbox_runtime_ready_is_shown_as_installed(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        class RuntimeReadyManager:
            cache_dir = Path("C:/temp/chatterbox-cache")
            runtime_path = Path("C:/temp/chatterbox_engine.exe")

            def is_installed(self) -> bool:
                return False

            def has_runtime(self) -> bool:
                return True

            def runtime_is_current(self) -> bool:
                return True

        window.chatterbox_manager = RuntimeReadyManager()
        window._refresh_chatterbox_status()

        self.assertNotIn("Not installed", window.chatterbox_status_label.text())
        self.assertNotIn("No instalado", window.chatterbox_status_label.text())
        self.assertTrue(window.chatterbox_remove_button.isEnabled())


if __name__ == "__main__":
    unittest.main()

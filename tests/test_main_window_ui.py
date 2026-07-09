from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from app.ui.main_window import MainWindow


class MainWindowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_generation_and_settings_views_are_separate(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        self.assertEqual(window.page_stack.count(), 6)
        window._select_tts_engine("piper")
        self.assertEqual(window.page_stack.currentIndex(), 0)
        self.assertEqual(window.ui_language_combo.count(), 10)
        self.assertTrue(hasattr(window, "import_button"))
        self.assertFalse(hasattr(window, "refresh_voices_button"))
        self.assertFalse(window.import_button.icon().isNull())
        self.assertEqual(window.windowTitle(), "LocalText2Voice")
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(hasattr(window, "app_menu_bar"))
        self.assertTrue(hasattr(window, "title_close_button"))
        self.assertTrue(hasattr(window, "resize_handles"))
        self.assertEqual(len(window.resize_handles), 8)
        self.assertTrue(all(handle.main_window is window for handle in window.resize_handles))
        self.assertTrue(all(handle.parent() is window.app_frame for handle in window.resize_handles))
        logo = window.findChild(QLabel, "logoLabel")
        self.assertIsNotNone(logo)
        self.assertIsNotNone(logo.pixmap())
        self.assertFalse(logo.pixmap().isNull())
        self.assertFalse(window.time_label.isVisible())
        self.assertFalse(window.open_output_button.isVisible())
        self.assertTrue(hasattr(window, "audio_mix_preview_panel"))
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "waveform_worker"))
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "render_worker"))
        self.assertEqual(window._format_duration(65), "01:05")
        author_credit = window.findChild(QLabel, "authorCreditLabel")
        self.assertIsNotNone(author_credit)
        self.assertTrue(author_credit.openExternalLinks())
        self.assertIn("https://andromedanova.com", author_credit.text())
        self.assertIn("Piper", window.header_engine_label.text())

        window._show_music_page()
        self.assertEqual(window.page_stack.currentIndex(), 2)
        self.assertTrue(hasattr(window, "music_table"))
        self.assertGreaterEqual(window.music_table.columnCount(), 5)
        self.assertFalse(window.import_music_button.icon().isNull())
        self.assertFalse(window.open_music_folder_button.icon().isNull())

        window._show_voices_page()
        self.assertEqual(window.page_stack.currentIndex(), 5)
        self.assertTrue(hasattr(window, "voices_table"))
        self.assertEqual(window.voices_table.columnCount(), 6)
        self.assertIn("Piper", window.voices_engine_label.text())

        window._show_review_page()
        self.assertEqual(window.page_stack.currentIndex(), 3)
        self.assertTrue(hasattr(window, "review_table"))
        self.assertEqual(window.review_table.columnCount(), 7)
        self.assertTrue(hasattr(window, "review_filter_combo"))
        self.assertGreaterEqual(window.review_filter_combo.count(), 7)
        self.assertTrue(hasattr(window, "review_rebuild_button"))
        self.assertTrue(hasattr(window, "review_source_detail"))
        self.assertTrue(hasattr(window, "review_transcript_detail"))
        self.assertFalse(window.review_verify_button.icon().isNull())

        window.settings_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 1)
        self.assertEqual(window.settings_tabs.count(), 4)
        self.assertEqual(window.tts_engine_combo.count(), 8)
        self.assertEqual(window.tts_engine_combo.currentData(), "piper")
        self.assertTrue(hasattr(window, "tts_engine_table"))
        self.assertEqual(window.tts_engine_table.rowCount(), 8)
        self.assertEqual(window.tts_engine_table.columnCount(), 8)
        self.assertIn("Piper", window.tts_engine_table.item(0, 1).text())
        self.assertIn(
            window.tr("selected", "Selected"),
            window.tts_engine_table.item(0, 6).text(),
        )
        self.assertFalse(hasattr(window, "python_runtime_status_label"))
        self.assertFalse(hasattr(window, "python_runtime_install_button"))
        self.assertEqual(window.engine_settings_stack.count(), 8)
        self.assertGreaterEqual(window.tts_engine_combo.findData("chatterbox"), 0)
        self.assertGreaterEqual(window.tts_engine_combo.findData("kokoro"), 0)
        self.assertGreaterEqual(window.tts_engine_combo.findData("qwen"), 0)
        self.assertGreaterEqual(window.tts_engine_combo.findData("gemini"), 0)
        self.assertEqual(window.gemini_model_combo.currentData(), "gemini-3.1-flash-tts-preview")
        self.assertEqual(window.gemini_voice_combo.currentData(), "Kore")
        self.assertTrue(hasattr(window, "kokoro_python_status_label"))
        self.assertEqual(window.chatterbox_device_combo.currentData(), "auto")
        self.assertTrue(hasattr(window, "chatterbox_hardware_label"))
        self.assertTrue(hasattr(window, "chatterbox_detect_gpu_button"))
        self.assertFalse(window.chatterbox_detect_gpu_button.icon().isNull())
        self.assertTrue(hasattr(window, "chatterbox_load_button"))
        self.assertFalse(window.chatterbox_load_button.isEnabled())
        self.assertEqual(window.qwen_device_combo.currentData(), "auto")
        self.assertTrue(hasattr(window, "qwen_hardware_label"))
        self.assertTrue(hasattr(window, "qwen_detect_gpu_button"))
        self.assertFalse(window.qwen_detect_gpu_button.icon().isNull())
        self.assertTrue(hasattr(window, "qwen_load_button"))
        self.assertFalse(window.qwen_load_button.isEnabled())
        self.assertTrue(hasattr(window, "review_enabled_checkbox"))
        self.assertTrue(hasattr(window, "whisper_install_button"))
        self.assertEqual(window.review_model_combo.currentData(), "small")
        self.assertTrue(window.language_combo.isEnabled())

        window._select_tts_engine("openai")
        self.assertFalse(window.language_combo.isEnabled())
        self.assertIn(
            window.tr("tts_models_tab", "TTS Engines"),
            window.voice_help_label.text(),
        )
        self.assertIn("OpenAI", window.header_engine_label.text())

        window.back_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 0)

        window.generation_started_at = time.monotonic() - 60
        window.progress_current = 1
        window.progress_total = 2
        window._update_generation_time()
        self.assertIn("01:00", window.time_label.text())

    def test_chatterbox_installed_state_is_shown(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        class RuntimeReadyManager:
            cache_dir = Path("C:/temp/chatterbox-cache")
            runtime_path = Path("C:/temp/python.exe")

            def is_installed(self) -> bool:
                return True

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

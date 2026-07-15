from __future__ import annotations

import os
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

from app.core.audio_mix import AudioMixSettings
from app.core.audiobook_store import StoredAudioEvent
from app.ui.audio_mix_preview_panel import AudioMixPreviewContext
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
        self.assertTrue(hasattr(window, "markup_toolbar"))
        self.assertTrue(hasattr(window, "markup_toolbar_action"))
        window._set_markup_toolbar_visible(True)
        self.assertFalse(window.markup_toolbar.isHidden())
        self.assertTrue(window.markup_toolbar_action.isChecked())
        markup_buttons = window.markup_toolbar.findChildren(
            QPushButton,
            "markupCommandButton",
        )
        self.assertGreaterEqual(len(markup_buttons), 6)
        self.assertIn("Play", [button.text() for button in markup_buttons])
        self.assertIn("Stop Audio", [button.text() for button in markup_buttons])
        window.text_editor.clear()
        markup_buttons[0].click()
        self.assertEqual(window.text_editor.toPlainText(), "{{pause }}")
        self.assertEqual(window.text_editor.textCursor().position(), len("{{pause "))
        window.markup_toolbar_action.setChecked(False)
        self.assertTrue(window.markup_toolbar.isHidden())
        window.markup_toolbar_action.setChecked(True)
        self.assertFalse(window.markup_toolbar.isHidden())
        self.assertEqual(window.windowTitle(), "LocalText2Voice")
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(hasattr(window, "app_menu_bar"))
        self.assertTrue(hasattr(window, "check_updates_action"))
        self.assertTrue(window.check_updates_action.isEnabled())
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
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "segment_text_view"))
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "segment_timeline_view"))
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "audio_event_list"))
        self.assertTrue(hasattr(window.audio_mix_preview_panel, "event_details_frame"))
        self.assertEqual(
            window.audio_mix_preview_panel.segment_text_view.lineWrapMode(),
            QPlainTextEdit.LineWrapMode.NoWrap,
        )
        self.assertEqual(
            set(window.audio_mix_preview_panel.track_volume_spins),
            {"voice", "background", "music", "ambient", "sfx"},
        )
        self.assertEqual(window.audio_mix_preview_panel.mix_tabs.count(), 2)
        self.assertFalse(hasattr(window.audio_mix_preview_panel, "advanced_toggle"))
        self.assertFalse(hasattr(window.audio_mix_preview_panel, "multitrack_graph"))
        window.audio_mix_preview_panel.total_duration_seconds = 10.0
        window.audio_mix_preview_panel._set_shared_cursor(4.0)
        window.audio_mix_preview_panel._on_media_status_changed(
            QMediaPlayer.MediaStatus.EndOfMedia
        )
        self.assertEqual(window.audio_mix_preview_panel.cursor_seconds, 0.0)
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
        self.assertFalse(window.download_remote_music_button.icon().isNull())
        self.assertFalse(window.open_music_folder_button.icon().isNull())
        self.assertEqual(window.audio_library_tabs.count(), 2)
        self.assertEqual(window.sfx_table.columnCount(), 4)
        self.assertFalse(window.import_sfx_button.icon().isNull())
        self.assertFalse(window.download_remote_sfx_button.icon().isNull())
        self.assertFalse(window.open_sfx_folder_button.icon().isNull())
        music_sources = window._remote_audio_sources("music")
        sfx_sources = window._remote_audio_sources("sfx")
        self.assertEqual(len(music_sources), 5)
        self.assertEqual(len(sfx_sources), 5)
        self.assertNotIn("Freesound", {source[0] for source in music_sources})
        self.assertIn("Freesound", {source[0] for source in sfx_sources})
        shown_dialogs: list[QDialog] = []
        with patch.object(
            QDialog,
            "exec",
            autospec=True,
            side_effect=lambda dialog: shown_dialogs.append(dialog) or 0,
        ) as show_dialog:
            window.download_remote_music_button.click()
            window.download_remote_sfx_button.click()
        self.assertEqual(show_dialog.call_count, 2)
        self.assertTrue(
            all(
                any(
                    button.text() == window.tr("close", "Close")
                    for button in dialog.findChildren(QPushButton)
                )
                for dialog in shown_dialogs
            )
        )

        window._show_voices_page()
        self.assertEqual(window.page_stack.currentIndex(), 5)
        self.assertTrue(hasattr(window, "voices_table"))
        self.assertEqual(window.voices_table.columnCount(), 10)
        self.assertTrue(hasattr(window, "voices_filter_edit"))
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
        self.assertEqual(window.settings_tabs.count(), 5)
        self.assertTrue(hasattr(window, "music_library_picker"))
        self.assertTrue(hasattr(window, "sfx_library_picker"))
        self.assertGreaterEqual(window.tts_engine_combo.count(), 9)
        self.assertEqual(window.tts_engine_combo.currentData(), "piper")
        self.assertTrue(hasattr(window, "tts_engine_table"))
        self.assertGreaterEqual(window.tts_engine_table.rowCount(), 9)
        self.assertEqual(window.tts_engine_table.columnCount(), 8)
        self.assertIn("Piper", window.tts_engine_table.item(0, 1).text())
        self.assertIn(
            window.tr("selected", "Selected"),
            window.tts_engine_table.item(0, 6).text(),
        )
        self.assertFalse(hasattr(window, "python_runtime_status_label"))
        self.assertFalse(hasattr(window, "python_runtime_install_button"))
        self.assertGreaterEqual(window.engine_settings_stack.count(), 10)
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
        self.assertTrue(hasattr(window, "markup_toolbar_checkbox"))
        self.assertTrue(window.markup_toolbar_checkbox.isChecked())
        self.assertIn("min-width", window._markup_help_card("{{pause}}", "Example."))
        codex_config = window._codex_mcp_config_text()
        parsed_codex = tomllib.loads(codex_config)
        self.assertIn("localtext2voice", parsed_codex["mcp_servers"])
        self.assertEqual(window.local_codex_toml_edit.toPlainText(), codex_config)
        self.assertTrue(hasattr(window, "copy_codex_toml_button"))
        self.assertTrue(hasattr(window, "open_codex_config_button"))

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

    def test_advanced_mix_groups_tracks_and_preserves_render_play_position(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        panel = window.audio_mix_preview_panel
        settings = AudioMixSettings(voice_start_offset_ms=0)

        def audio_event(uid: str, track: str, filename: str) -> StoredAudioEvent:
            return StoredAudioEvent(
                id=len(uid),
                audiobook_id=1,
                segment_id=1,
                event_uid=uid,
                event_id=uid,
                command_type="play",
                raw_command="",
                source_position=0,
                anchor_segment_sequence=0,
                anchor_source_word=0,
                anchor_mode="after",
                file_reference=filename,
                file_path=f"C:/missing/{filename}",
                track=track,
                duration_ms=2000,
                resolved_time_ms=1000,
                resolution_status="resolved",
            )

        music_event = audio_event("music-1", "music", "theme.mp3")
        sfx_event = audio_event("sfx-1", "sfx", "door.mp3")
        panel.context = AudioMixPreviewContext(
            voice_path=Path("C:/missing/voice.mp3"),
            output_dir=Path("C:/missing"),
            ffmpeg_path="ffmpeg",
            music_path=None,
            settings=settings,
            metadata={},
            audio_events=(music_event, sfx_event),
        )
        panel.editable_audio_events = [music_event, sfx_event]
        panel._apply_settings(settings)
        panel._refresh_audio_event_table()

        self.assertEqual(set(panel.audio_event_lists), {"music", "sfx"})
        panel.advanced_playback_active = True
        panel._sync_active_audio_events(1.5)
        self.assertEqual(
            set(panel.active_audio_event_uids),
            {"music-1", "sfx-1"},
        )
        self.assertEqual(panel.event_detail_tabs.count(), 2)

        window.page_stack.setCurrentWidget(panel)
        panel.mix_tabs.setCurrentWidget(panel.advanced_tab)
        panel.segment_text_view.setFixedWidth(180)
        panel.segment_text_view.setPlainText("palabra " * 300)
        panel.segment_line_by_sequence = {0: 0}
        panel.current_highlighted_segment = None
        panel.current_highlighted_word = None
        window.show()
        self.application.processEvents()
        horizontal_scroll = panel.segment_text_view.horizontalScrollBar()
        self.assertGreater(horizontal_scroll.maximum(), 0)
        horizontal_scroll.setValue(horizontal_scroll.maximum() // 2)
        fixed_horizontal_position = horizontal_scroll.value()
        panel._highlight_advanced_segment(0, (0, 120, 127))
        self.assertEqual(horizontal_scroll.value(), fixed_horizontal_position)

        panel.pending_advanced_full_play = True
        panel.pending_advanced_play_position_seconds = 4.25
        with patch.object(panel, "_play_advanced_cached") as play_cached:
            panel._on_advanced_full_preview_rendered("C:/missing/rendered.mp3")
        play_cached.assert_called_once_with(4.25)


if __name__ == "__main__":
    unittest.main()

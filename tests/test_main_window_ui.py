from __future__ import annotations

from dataclasses import replace
import os
import json
import tempfile
import time
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, Qt, QUrl, qInstallMessageHandler
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from app.core.audio_mix import AudioMixSettings
from app.core.audiobook_store import AudiobookStore, StoredAudioEvent, StoredSegment
from app.core.bulk_audiobook_store import BulkAudiobookStore
from app.core.settings_manager import DEFAULT_SETTINGS, SettingsManager
from app.core.text_normalization import TextNormalizationStore
from app.tts.voice_gallery_manager import GalleryVoice
from app.ui.audio_mix_preview_panel import AudioMixPreviewContext
from app.ui.main_window import MainWindow, MarkupAudioPickerDialog
from app.utils.gpu_detection import GPUDetectionResult, GPUInfo


class MainWindowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_review_marks_old_silero_f5_scores_as_pending_once(self) -> None:
        segment = StoredSegment(
            id=1,
            audiobook_id=1,
            sequence_index=1,
            chapter_index=1,
            chapter_title="Course",
            source_text="text",
            wav_path="segment.wav",
            status="rendered",
            similarity_score=32.5,
            verification_status="retry_needed",
            transcript_text="text",
            language="ru",
            engine_config_json=json.dumps(
                {
                    "engine": "f5_russian",
                    "language": "ru",
                    "russian_silero_preprocessed": True,
                }
            ),
        )

        self.assertFalse(MainWindow._russian_silero_stress_comparison_current(segment))

        current_segment = replace(
            segment,
            review_metrics_json=json.dumps(
                {"russian_silero_stress_comparison": {"enabled": True}}
            ),
        )
        self.assertTrue(
            MainWindow._russian_silero_stress_comparison_current(current_segment)
        )

    def test_markup_voice_menu_inserts_only_a_local_voice(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window._select_tts_engine("f5_russian")
        rows = [
            {"name": "Narrator", "language": "Russian", "installed": True},
            {
                "name": "Not downloaded",
                "language": "Russian",
                "installed": False,
            },
        ]
        with patch.object(window, "_voice_page_rows", return_value=rows):
            window._populate_markup_voice_menu()

        actions = window.markup_voice_menu.actions()
        self.assertEqual([action.text() for action in actions], ["Narrator (Russian)"])
        window.text_editor.clear()
        actions[0].trigger()
        self.assertEqual(window.text_editor.toPlainText(), '{{voice "Narrator"}}')

    def test_markup_play_menu_inserts_the_selected_library_asset(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            asset = directory / "intro.mp3"
            asset.touch()
            with (
                patch.object(
                    window,
                    "_markup_audio_library_directory",
                    return_value=directory,
                ),
                patch.object(
                    window,
                    "_markup_audio_library_files",
                    return_value=[asset],
                ),
            ):
                window._populate_markup_audio_menu("music")

            actions = window.markup_audio_menus["music"].actions()
            self.assertEqual([action.text() for action in actions], ["intro.mp3"])
            window.text_editor.clear()
            actions[0].trigger()
            self.assertEqual(
                window.text_editor.toPlainText(),
                '{{play "intro.mp3" track=music volume=1}}',
            )

    def test_markup_audio_picker_filters_a_large_library(self) -> None:
        files = [Path(f"effect-{index:03d}.wav") for index in range(25)]
        dialog = MarkupAudioPickerDialog(files, "sfx")
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.files_list.count(), 25)
        dialog.search_edit.setText("effect-017")
        self.assertEqual(dialog.files_list.count(), 1)
        dialog._accept_selection()
        self.assertEqual(dialog.selected_path, Path("effect-017.wav"))

    def test_external_voice_libraries_puts_yaph_first(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        sources = window._external_voice_libraries()

        self.assertEqual(len(sources), 6)
        self.assertEqual(sources[0][0], "yaph/tts-samples")
        self.assertEqual(
            sources[0][1],
            "https://github.com/yaph/tts-samples/tree/main/mp3",
        )
        self.assertEqual(
            window.voices_external_libraries_button.text(),
            "External Voice Libraries",
        )

    def test_selecting_another_engine_skips_pending_omnivoice_setup(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.settings["installer_setup"] = {
            "pending_installs": ["omnivoice"],
            "completed": False,
        }

        with patch.object(window.settings_manager, "save") as save:
            window._select_tts_engine("piper")

        self.assertEqual(window.settings["tts_engine"], "piper")
        self.assertEqual(
            window.settings["installer_setup"]["pending_installs"], []
        )
        self.assertTrue(window.settings["installer_setup"]["completed"])
        save.assert_called()

    def test_uninstalled_engine_has_no_generation_voice_choices(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        with (
            patch.object(window.f5_russian_manager, "is_installed", return_value=False),
            patch.object(window, "_voice_page_rows") as rows,
        ):
            self.assertEqual(window._generation_voice_rows("f5_russian"), [])
            rows.assert_not_called()

    def test_uninstalled_engine_cannot_become_active(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        original_engine = str(window.tts_engine_combo.currentData())

        with (
            patch.object(window.qwen_manager, "is_installed", return_value=False),
            patch.object(QMessageBox, "critical") as error,
        ):
            self.assertFalse(window._select_tts_engine("qwen"))

        self.assertEqual(str(window.tts_engine_combo.currentData()), original_engine)
        error.assert_called_once()

    def test_qwen_models_have_separate_engine_table_rows_and_status(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        with (
            patch.object(window.qwen_manager, "has_runtime", return_value=False),
            patch.object(
                window.qwen_manager,
                "has_model_files",
                side_effect=lambda model_id=None: model_id == "custom_voice_0_6b",
            ),
            patch.object(window.qwen_manager, "is_installed", return_value=False),
        ):
            rows = {
                row["engine_id"]: row
                for row in window._engine_table_rows()
                if row["engine_id"].startswith("qwen:")
            }

        self.assertEqual(
            set(rows),
            {"qwen:custom_voice_0_6b", "qwen:base_1_7b"},
        )
        self.assertIn("CustomVoice 0.6B", rows["qwen:custom_voice_0_6b"]["name"])
        self.assertIn("Base 1.7B", rows["qwen:base_1_7b"]["name"])
        self.assertIn("Model detected", rows["qwen:custom_voice_0_6b"]["installed"])
        self.assertEqual(rows["qwen:base_1_7b"]["installed"], "Not installed")

    def test_qwen_table_install_action_selects_the_requested_model(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        with patch.object(window, "_install_qwen") as install:
            window._select_and_install_engine("qwen:base_1_7b")

        self.assertEqual(window.qwen_model_combo.currentData(), "base_1_7b")
        install.assert_called_once_with()

    def test_qwen_base_reuses_omnivoice_default_reference(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        reference = Path(temporary.name) / "harold.wav"
        reference.write_bytes(b"RIFF")
        transcript = (
            "Sit by the fire, and I will tell you how the old road was found."
        )
        requested_voice_ids: list[str] = []
        voice = SimpleNamespace(name="Harold", ref_text=transcript)
        window.voice_gallery_manager = SimpleNamespace(
            get_voice=lambda voice_id: (
                requested_voice_ids.append(voice_id) or voice
            ),
            ensure_voice_audio=lambda selected: (
                reference if selected is voice else None
            ),
        )
        window.settings["omnivoice"] = {
            "reference_audio_path": "",
            "reference_text": "",
        }
        window.qwen_reference_picker.set_path(None)
        window.qwen_reference_text_edit.clear()

        base_index = window.qwen_model_combo.findData("base_1_7b")
        window.qwen_model_combo.setCurrentIndex(base_index)
        requested_voice_ids.clear()
        self.assertTrue(
            window._ensure_default_qwen_reference(allow_sync=True)
        )

        self.assertEqual(
            requested_voice_ids,
            ["omnivoice_en_harold_storyteller"],
        )
        self.assertEqual(window.qwen_reference_picker.path(), reference)
        self.assertEqual(
            window.qwen_reference_text_edit.toPlainText(),
            transcript,
        )

        requested_voice_ids.clear()
        custom_index = window.qwen_model_combo.findData("custom_voice_0_6b")
        window.qwen_model_combo.setCurrentIndex(custom_index)
        self.assertFalse(window._ensure_default_qwen_reference(allow_sync=False))
        self.assertEqual(requested_voice_ids, [])

        window.qwen_reference_picker.set_path(None)
        window.qwen_reference_text_edit.clear()
        window.settings["omnivoice"] = {
            "reference_audio_path": str(reference),
            "reference_text": transcript,
        }
        window.voice_gallery_manager = SimpleNamespace(
            get_voice=lambda _voice_id: self.fail(
                "The installed OmniVoice reference should be reused directly."
            )
        )
        window.qwen_model_combo.setCurrentIndex(base_index)
        self.assertEqual(window.qwen_reference_picker.path(), reference)
        self.assertEqual(
            window.qwen_reference_text_edit.toPlainText(),
            transcript,
        )

    def test_qwen_voices_refresh_checks_engine_readiness_only_once(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        custom_index = window.qwen_model_combo.findData("custom_voice_0_6b")
        window.qwen_model_combo.setCurrentIndex(custom_index)
        qwen_index = window.tts_engine_combo.findData("qwen")
        window.tts_engine_combo.blockSignals(True)
        window.tts_engine_combo.setCurrentIndex(qwen_index)
        window.tts_engine_combo.blockSignals(False)

        with patch.object(
            window.qwen_manager,
            "is_installed",
            return_value=True,
        ) as is_installed:
            window._refresh_voices_page()

        self.assertEqual(window.voices_table.rowCount(), 90)
        self.assertEqual(is_installed.call_count, 1)

    def test_qwen_base_voices_page_exposes_cloning_actions(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        base_index = window.qwen_model_combo.findData("base_1_7b")
        window.qwen_model_combo.setCurrentIndex(base_index)
        qwen_index = window.tts_engine_combo.findData("qwen")
        window.tts_engine_combo.blockSignals(True)
        window.tts_engine_combo.setCurrentIndex(qwen_index)
        window.tts_engine_combo.blockSignals(False)

        with patch.object(window.qwen_manager, "is_installed", return_value=True):
            window._refresh_voices_page()

        self.assertEqual(window.voices_manage_button.text(), "Clone Voice")
        self.assertTrue(window.voices_manage_button.isEnabled())
        self.assertFalse(window.voices_external_libraries_button.isHidden())
        self.assertNotIn(
            "Model speaker",
            {
                str(row.get("type", ""))
                for row in window.voice_page_rows
            },
        )
        with patch.object(window, "_import_gallery_reference_voice") as clone:
            window._voices_primary_manage_action()
        clone.assert_called_once_with("qwen")

    def test_bulk_create_only_makes_independent_editable_projects(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "First Book.txt"
        source.write_text("Chapter one.\n\nChapter two.", encoding="utf-8")
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.audiobook_store = AudiobookStore(root / "projects.sqlite3")
        window.bulk_audiobook_store = BulkAudiobookStore(root / "bulk.sqlite3")

        with (
            patch.object(window, "_save_settings"),
            patch.object(
                window,
                "_current_voice_config",
                return_value={"engine": "piper", "voice": "test", "speed": 1.0},
            ),
        ):
            window._create_bulk_audiobook_task(
                {
                    "title": "Test collection",
                    "output_parent": str(root / "collection"),
                    "create_only": True,
                    "sources": [
                        {
                            "source_path": str(source),
                            "title": "First Book",
                            "music_path": "",
                        }
                    ],
                }
            )

        batches = window.bulk_audiobook_store.list_batches()
        self.assertEqual(len(batches), 1)
        items = window.bulk_audiobook_store.list_items(batches[0].id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "complete")
        project = window.audiobook_store.get_audiobook(items[0].audiobook_id)
        self.assertIsNotNone(project)
        self.assertEqual(
            project.source_text.replace("\r\n", "\n"),  # type: ignore[union-attr]
            "Chapter one.\n\nChapter two.",
        )
        self.assertTrue(project.project_dir.is_dir())  # type: ignore[union-attr]

        with (
            patch.object(window, "_save_settings"),
            patch.object(
                window,
                "_current_voice_config",
                return_value={"engine": "piper", "voice": "test", "speed": 1.0},
            ),
            patch.object(window.faster_whisper_manager, "is_installed", return_value=True),
            patch.object(window, "_start_bulk_audiobook_task") as start_bulk,
        ):
            window._create_bulk_audiobook_task(
                {
                    "title": "Reviewed collection",
                    "output_parent": str(root / "collection"),
                    "create_only": False,
                    "creation_flow": "auto_review",
                    "creation_flow_label": (
                        "Generation → Review → Retry failed segments (Auto)"
                    ),
                    "flow_max_retries": 3,
                    "sources": [
                        {
                            "source_path": str(source),
                            "title": "First Book reviewed",
                            "music_path": "",
                        }
                    ],
                }
            )

        reviewed_batch = window.bulk_audiobook_store.list_batches()[0]
        reviewed_config = reviewed_batch.config()
        self.assertEqual(reviewed_config["review_policy"], "on")
        self.assertEqual(reviewed_config["creation_flow"], "auto_review")
        self.assertEqual(
            reviewed_config["generation_settings"]["review"]["max_retries"],
            3,
        )
        start_bulk.assert_called_once_with(reviewed_batch.id)

    def test_bulk_project_folder_names_are_windows_safe_and_unicode_normalized(self) -> None:
        self.assertEqual(
            MainWindow._safe_project_folder_name("La ma\u0301scara: roja?.txt"),
            "La máscara- roja-.txt",
        )
        self.assertEqual(
            MainWindow._safe_project_folder_name("CON"),
            "Audiobook - CON",
        )
        self.assertEqual(
            MainWindow._safe_project_folder_name("  ...  "),
            "Audiobook",
        )

    def test_bulk_worker_never_updates_qt_widgets_from_its_thread(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.audiobook_store = AudiobookStore(root / "projects.sqlite3")
        window.bulk_audiobook_store = BulkAudiobookStore(root / "bulk.sqlite3")
        project = window.audiobook_store.create_audiobook(
            "Thread-safe text",
            {"engine": "piper", "voice": "test"},
            root / "Book" / "exports",
            "safe_chunks",
            "single",
            "Book",
            {},
            root / "Book",
        )
        batch = window.bulk_audiobook_store.create_batch(
            "Thread safety",
            {"voice_config": {"engine": "piper", "voice": "test"}},
            [
                {
                    "source_path": str(root / "Book.txt"),
                    "title": "Book",
                    "audiobook_id": project.id,
                }
            ],
        )
        window.engine_host_client = SimpleNamespace(
            health=lambda timeout=0.75: False,
            submit_job=lambda _request: {"job_id": "thread-safe-job"},
            get_job=lambda _job_id: {
                "status": "complete",
                "result": {"audiobook_id": project.id, "clean_mp3": "book.mp3"},
                "progress": {"current": 1, "total": 1, "message": "Done"},
                "logs": [],
            },
            cancel_job=lambda _job_id: {"status": "cancelled"},
        )
        qt_messages: list[str] = []

        def capture_qt_message(_mode, _context, message: str) -> None:
            qt_messages.append(message)

        previous_handler = qInstallMessageHandler(capture_qt_message)
        try:
            window._start_bulk_audiobook_task(batch.id)
            deadline = time.monotonic() + 5.0
            while window.bulk_audiobook_thread is not None and time.monotonic() < deadline:
                self.application.processEvents()
                QTest.qWait(10)
            self.application.processEvents()
        finally:
            qInstallMessageHandler(previous_handler)

        self.assertIsNone(window.bulk_audiobook_thread)
        self.assertEqual(
            window.bulk_audiobook_store.list_items(batch.id)[0].status,
            "complete",
        )
        cross_thread_messages = [
            message
            for message in qt_messages
            if "different thread" in message.casefold()
        ]
        self.assertEqual(cross_thread_messages, [], qt_messages)

    def test_generation_and_settings_views_are_separate(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_path = Path(temporary.name) / "config.json"
        with patch(
            "app.ui.main_window.SettingsManager",
            return_value=SettingsManager(config_path),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)

        self.assertEqual(window.page_stack.count(), 7)
        window._select_tts_engine("piper")
        self.assertEqual(window.page_stack.currentIndex(), 0)
        self.assertEqual(window.ui_language_combo.count(), 11)
        self.assertEqual(window.ui_language_combo.maxVisibleItems(), 11)
        self.assertTrue(hasattr(window, "local_server_port_spin"))
        self.assertTrue(hasattr(window, "local_server_log_label"))
        self.assertFalse(hasattr(window, "local_server_enabled_checkbox"))
        self.assertFalse(hasattr(window, "local_server_auto_start_checkbox"))
        self.assertFalse(hasattr(window, "local_server_endpoint_label"))
        self.assertTrue(hasattr(window, "theme_button"))
        self.assertFalse(window.theme_button.icon().isNull())
        self.assertEqual(window.theme_button.objectName(), "themeToggleButton")
        appearance_layout = window.theme_button.parentWidget().layout()
        self.assertLess(
            appearance_layout.indexOf(window.theme_button),
            appearance_layout.indexOf(window.ui_language_combo),
        )
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
        self.assertIn("Play Music", [button.text() for button in markup_buttons])
        self.assertIn("Play SFX", [button.text() for button in markup_buttons])
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
        self.assertFalse(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(hasattr(window, "app_menu_bar"))
        self.assertIs(window.menuBar(), window.app_menu_bar)
        self.assertGreaterEqual(len(window.app_menu_bar.actions()), 5)
        self.assertTrue(hasattr(window, "check_updates_action"))
        self.assertTrue(window.check_updates_action.isEnabled())
        self.assertEqual(
            window.general_documentation_action.text(),
            "General Documentation",
        )
        self.assertEqual(window.markup_help_action.text(), "Markup Help")
        with patch("app.ui.main_window.QDesktopServices.openUrl") as open_url:
            window.general_documentation_action.trigger()
            window.markup_help_action.trigger()
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(
            open_url.call_args_list[0].args[0].toString(),
            "https://github.com/estebanstifli/LocalText2Voice",
        )
        self.assertEqual(
            open_url.call_args_list[1].args[0].toString(),
            "https://github.com/estebanstifli/LocalText2Voice/blob/main/docs/LTV_MARKUP.md",
        )
        self.assertFalse(hasattr(window, "title_close_button"))
        self.assertFalse(hasattr(window, "resize_handles"))
        logo = window.findChild(QLabel, "logoLabel")
        self.assertIsNotNone(logo)
        self.assertIsNotNone(logo.pixmap())
        self.assertFalse(logo.pixmap().isNull())
        self.assertIsNotNone(window.findChild(QWidget, "sidebarBrand"))
        self.assertIn(
            "QFrame#sidebar QLabel",
            window.styleSheet(),
        )
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
        self.assertEqual(window.review_table.columnCount(), 8)
        self.assertEqual(window.review_visible_row_count, 15)
        expected_review_height = (
            window.review_table.horizontalHeader().sizeHint().height()
            + window.review_table.verticalHeader().defaultSectionSize() * 15
            + window.review_table.frameWidth() * 2
            + 1
        )
        self.assertEqual(window.review_table.minimumHeight(), expected_review_height)
        self.assertEqual(window.review_table.maximumHeight(), expected_review_height)
        self.assertFalse(hasattr(window, "review_totals_label"))
        self.assertTrue(hasattr(window, "review_filter_combo"))
        self.assertGreaterEqual(window.review_filter_combo.count(), 7)
        self.assertTrue(hasattr(window, "review_rebuild_button"))
        self.assertTrue(hasattr(window, "review_source_detail"))
        self.assertTrue(hasattr(window, "review_transcript_detail"))
        self.assertTrue(hasattr(window, "review_tail_enabled_checkbox"))
        self.assertEqual(
            window.review_tail_enabled_checkbox.isChecked(),
            bool(window.settings.get("review", {}).get("tail_analysis_enabled", False)),
        )
        self.assertFalse(window.review_verify_button.icon().isNull())

        window.settings_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 1)
        self.assertEqual(window.settings_tabs.count(), 6)
        self.assertTrue(hasattr(window, "text_normalization_panel"))
        self.assertGreaterEqual(
            window.text_normalization_panel.entries_table.rowCount(),
            50,
        )
        self.assertGreaterEqual(window.text_normalization_panel.language_combo.count(), 11)
        self.assertGreaterEqual(
            window.text_normalization_panel.editor_language_combo.count(), 10
        )
        selected_normalization_language = window.settings.get(
            "text_normalization", {}
        ).get("language", "en")
        self.assertEqual(
            window.text_normalization_panel.editor_language_combo.currentData(),
            (
                selected_normalization_language
                if selected_normalization_language != "auto"
                else "en"
            ),
        )
        self.assertTrue(
            hasattr(window.text_normalization_panel, "create_dictionary_button")
        )
        self.assertTrue(hasattr(window.text_normalization_panel, "import_button"))
        self.assertTrue(hasattr(window.text_normalization_panel, "export_button"))
        self.assertTrue(
            window.text_normalization_panel.rules_enabled_checkbox.isChecked()
        )
        self.assertEqual(
            sum(
                window.text_normalization_panel.configuration()["rules"].values()
            ),
            8,
        )
        self.assertTrue(
            hasattr(window.text_normalization_panel, "rules_details_button")
        )
        self.assertTrue(hasattr(window, "music_library_picker"))
        self.assertTrue(hasattr(window, "sfx_library_picker"))
        self.assertGreaterEqual(window.tts_engine_combo.count(), 9)
        self.assertEqual(window.tts_engine_combo.currentData(), "piper")
        self.assertTrue(hasattr(window, "tts_engine_table"))
        self.assertTrue(hasattr(window, "bulk_audiobooks_page"))
        self.assertIn("bulk", window.nav_buttons)
        self.assertEqual(
            window.bulk_audiobooks_page.creation_flow_combo.currentData(),
            "settings",
        )
        self.assertEqual(window.bulk_audiobooks_page.creation_flow_combo.count(), 4)
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
        self.assertGreaterEqual(window.tts_engine_combo.findData("f5_russian"), 0)
        self.assertTrue(window.f5_russian_stress_checkbox.isChecked())
        self.assertIn("F5-TTS Russian", window._tts_engine_label("f5_russian"))

        # Rebuilding the engine table must keep a lower selected row in view.
        # Extra rows reproduce installations with several custom engines.
        engine_rows = window._engine_table_rows()
        azure_row = next(
            row for row in engine_rows if row["engine_id"] == "azure"
        )
        long_engine_rows = [
            row for row in engine_rows if row["engine_id"] != "azure"
        ]
        for index in range(12):
            long_engine_rows.append(
                {
                    **engine_rows[0],
                    "engine_id": f"test_engine_{index}",
                    "name": f"Test engine {index}",
                    "selected": "",
                }
            )
        long_engine_rows.append(azure_row)
        window.settings_tabs.setCurrentIndex(1)
        window.tts_engine_table.setFixedHeight(245)
        window.show()
        self.application.processEvents()
        with patch.object(
            window,
            "_engine_table_rows",
            return_value=long_engine_rows,
        ):
            window._select_tts_engine("azure")
            self.application.processEvents()
            selected_item = window.tts_engine_table.currentItem()
            self.assertIsNotNone(selected_item)
            self.assertEqual(
                selected_item.data(Qt.ItemDataRole.UserRole),
                "azure",
            )
            self.assertTrue(
                window.tts_engine_table.visualItemRect(selected_item).intersects(
                    window.tts_engine_table.viewport().rect()
                ),
                (
                    window.tts_engine_table.visualItemRect(selected_item),
                    window.tts_engine_table.viewport().rect(),
                    window.tts_engine_table.verticalScrollBar().value(),
                    window.tts_engine_table.verticalScrollBar().maximum(),
                ),
            )
            selected_scroll_position = (
                window.tts_engine_table.verticalScrollBar().value()
            )
            window._refresh_tts_engine_table()
            self.assertEqual(
                window.tts_engine_table.verticalScrollBar().value(),
                min(
                    selected_scroll_position,
                    window.tts_engine_table.verticalScrollBar().maximum(),
                ),
            )
        window._select_tts_engine("piper")
        window.hide()

        reference_temp = tempfile.TemporaryDirectory()
        self.addCleanup(reference_temp.cleanup)
        russian_reference = Path(reference_temp.name) / "russian_man.wav"
        russian_reference.write_bytes(b"RIFF")
        russian_transcript = (
            "Тихий ветер гуляет по улицам старого города. Скоро наступит вечер, "
            "и в окнах зажгутся тёплые огни."
        )
        requested_voice_ids: list[str] = []
        window.voice_gallery_manager = SimpleNamespace(
            get_voice=lambda voice_id: (
                requested_voice_ids.append(voice_id)
                or SimpleNamespace(
                    name="Russian Man",
                    ref_text=russian_transcript,
                )
            ),
            ensure_voice_audio=lambda _voice: russian_reference,
        )
        window.f5_russian_reference_picker.set_path(None)
        window.f5_russian_reference_text_edit.clear()
        self.assertTrue(
            window._ensure_default_f5_russian_reference(allow_sync=False)
        )
        self.assertEqual(requested_voice_ids, ["omnivoice_ru_russian_man"])
        self.assertEqual(
            window.f5_russian_reference_picker.path(),
            russian_reference,
        )
        self.assertEqual(
            window.f5_russian_reference_text_edit.toPlainText(),
            russian_transcript,
        )
        russian_man = GalleryVoice(
            voice_id="omnivoice_ru_russian_man",
            engine="omnivoice",
            name="Russian Man",
            language="ru",
            language_name="Russian",
            voice_type="Reference voice",
            install_type="reference_audio",
            ref_audio_path=str(russian_reference),
            ref_text=russian_transcript,
            installed_path=str(russian_reference),
        )
        russian_woman = GalleryVoice(
            voice_id="omnivoice_ru_russian_woman",
            engine="omnivoice",
            name="Russian Woman",
            language="ru",
            language_name="Russian",
            voice_type="Reference voice",
            install_type="reference_audio",
            ref_audio_url="https://example.invalid/russian_woman.wav",
            ref_text=russian_transcript,
        )
        window.voice_gallery_manager = SimpleNamespace(
            list_voices=lambda _engine: [russian_man, russian_woman],
            preview_source=lambda voice: (
                voice.installed_path or voice.ref_audio_path or voice.ref_audio_url
            ),
            is_installed=lambda voice: bool(voice.installed_path),
        )
        f5_voice_rows = window._voice_page_rows("f5_russian")
        self.assertEqual(
            {row["name"] for row in f5_voice_rows},
            {"Russian Man", "Russian Woman"},
        )
        self.assertEqual(len(f5_voice_rows), 2)
        self.assertTrue(
            next(row for row in f5_voice_rows if row["name"] == "Russian Man")[
                "selected"
            ]
        )
        russian_woman_row = next(
            row for row in f5_voice_rows if row["name"] == "Russian Woman"
        )
        with (
            patch.object(
                window.f5_russian_manager,
                "is_installed",
                return_value=True,
            ),
            patch.object(window, "_start_voice_gallery_operation") as start_install,
        ):
            window._select_voice_page_row_data(russian_woman_row)
        start_install.assert_called_once_with("install", russian_woman)
        self.assertEqual(
            window.pending_f5_russian_gallery_voice_id,
            russian_woman.voice_id,
        )
        completed_gallery = SimpleNamespace(
            get_voice=lambda voice_id: (
                russian_woman if voice_id == russian_woman.voice_id else None
            )
        )
        with (
            patch(
                "app.ui.main_window.VoiceGalleryManager",
                return_value=completed_gallery,
            ),
            patch.object(
                window,
                "_apply_f5_russian_gallery_reference",
                return_value=True,
            ) as apply_reference,
            patch.object(window, "_save_settings") as save_settings,
            patch.object(window, "_refresh_voices_page"),
        ):
            window._on_voice_gallery_finished("Installed Russian Woman.")
        apply_reference.assert_called_once_with(russian_woman)
        save_settings.assert_called_once_with()
        self.assertIsNone(window.pending_f5_russian_gallery_voice_id)
        self.assertGreaterEqual(window.tts_engine_combo.findData("gemini"), 0)
        self.assertEqual(window.gemini_model_combo.currentData(), "gemini-3.1-flash-tts-preview")
        self.assertEqual(window.gemini_voice_combo.currentData(), "Kore")
        self.assertTrue(hasattr(window, "kokoro_python_status_label"))
        self.assertEqual(window.chatterbox_device_combo.currentData(), "auto")
        self.assertTrue(hasattr(window, "chatterbox_hardware_label"))
        self.assertTrue(hasattr(window, "chatterbox_detect_gpu_button"))
        self.assertFalse(window.chatterbox_detect_gpu_button.icon().isNull())
        self.assertTrue(hasattr(window, "chatterbox_load_button"))
        self.assertEqual(
            window.chatterbox_load_button.isEnabled(),
            window.chatterbox_manager.is_installed(),
        )
        self.assertEqual(window.qwen_device_combo.currentData(), "auto")
        custom_index = window.qwen_model_combo.findData("custom_voice_0_6b")
        base_index = window.qwen_model_combo.findData("base_1_7b")
        self.assertGreaterEqual(custom_index, 0)
        self.assertGreaterEqual(base_index, 0)
        self.assertIn(
            "CustomVoice 0.6B",
            window.qwen_model_combo.itemText(custom_index),
        )
        window.qwen_model_combo.setCurrentIndex(base_index)
        self.assertTrue(window.qwen_speaker_combo.isHidden())
        self.assertFalse(window.qwen_reference_picker.isHidden())
        self.assertFalse(window.qwen_reference_text_edit.isHidden())
        self.assertFalse(window.qwen_instruct_edit.isHidden())
        self.assertIsNotNone(window.qwen_install_button.parentWidget())
        self.assertIsNotNone(window.qwen_test_button.parentWidget())
        window.qwen_model_combo.setCurrentIndex(custom_index)
        self.assertFalse(window.qwen_speaker_combo.isHidden())
        self.assertTrue(window.qwen_reference_picker.isHidden())
        self.assertTrue(window.qwen_instruct_edit.isHidden())
        self.assertTrue(hasattr(window, "qwen_hardware_label"))
        self.assertTrue(hasattr(window, "qwen_detect_gpu_button"))
        self.assertFalse(window.qwen_detect_gpu_button.icon().isNull())
        self.assertTrue(hasattr(window, "qwen_load_button"))
        self.assertEqual(
            window.qwen_load_button.isEnabled(),
            window.qwen_manager.is_installed(),
        )
        self.assertTrue(hasattr(window, "review_enabled_checkbox"))
        self.assertTrue(hasattr(window, "whisper_install_button"))
        self.assertEqual(window.review_model_combo.currentData(), "small")
        self.assertTrue(window.language_combo.isEnabled())
        self.assertTrue(hasattr(window, "markup_toolbar_checkbox"))
        self.assertTrue(hasattr(window, "reset_settings_button"))
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

    def test_restore_is_non_destructive_and_reset_uses_safe_defaults(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_path = Path(temporary.name) / "config.json"
        manager = SettingsManager(config_path)
        initial = deepcopy(manager.settings)
        initial["ui_language"] = "es"
        initial["chunk_size"] = 2500
        initial["review"]["enabled"] = True
        initial["review"]["auto_verify_after_generation"] = True
        manager.save(initial)

        with patch(
            "app.ui.main_window.SettingsManager",
            return_value=SettingsManager(config_path),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)

        restored_file = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(restored_file["ui_language"], "es")
        self.assertEqual(restored_file["chunk_size"], 2500)
        self.assertEqual(window.ui_language_combo.currentData(), "es")
        self.assertEqual(window.chunk_size_spin.value(), 2500)

        window.text_editor.setPlainText("Texto que no debe borrarse.")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window._reset_settings_to_defaults()

        reset_file = SettingsManager(config_path).settings
        self.assertEqual(reset_file["ui_language"], DEFAULT_SETTINGS["ui_language"])
        self.assertEqual(reset_file["chunk_size"], DEFAULT_SETTINGS["chunk_size"])
        self.assertFalse(reset_file["review"]["enabled"])
        self.assertEqual(window.text_editor.toPlainText(), "Texto que no debe borrarse.")
        self.assertTrue(hasattr(window, "reset_settings_button"))

    def test_switching_to_russian_keeps_russian_selected(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_path = Path(temporary.name) / "config.json"

        with patch(
            "app.ui.main_window.SettingsManager",
            return_value=SettingsManager(config_path),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)

        russian_index = window.ui_language_combo.findData("ru")
        self.assertGreaterEqual(russian_index, 0)
        window.ui_language_combo.blockSignals(True)
        window.ui_language_combo.setCurrentIndex(russian_index)
        window.ui_language_combo.blockSignals(False)

        window._change_ui_language()

        self.assertEqual(window.translator.language, "ru")
        self.assertEqual(window.ui_language_combo.currentData(), "ru")
        self.assertEqual(window.ui_language_combo.currentText(), "Русский")
        self.assertEqual(window.settings_button.text(), "Настройки")
        self.assertEqual(
            SettingsManager(config_path).settings["ui_language"],
            "ru",
        )

    def test_theme_toggle_is_accessible_persistent_and_preserves_page(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_path = Path(temporary.name) / "config.json"

        with patch(
            "app.ui.main_window.SettingsManager",
            return_value=SettingsManager(config_path),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)

        self.assertEqual(window.ui_theme, "light")
        self.assertEqual(window.theme_button.property("themeIcon"), "sun")
        self.assertEqual(
            window.theme_button.toolTip(),
            window.tr("switch_to_dark_theme", "Switch to dark theme"),
        )
        self.assertEqual(
            window.theme_button.accessibleName(),
            window.theme_button.toolTip(),
        )
        central_widget = window.centralWidget()
        settings_button = window.settings_button
        light_settings_icon = settings_button.icon().pixmap(20, 20).toImage()
        window.text_editor.setPlainText("Theme-safe text")
        window.log_view.append_event("Theme log survives")
        window._show_voices_page()

        window.theme_button.click()

        self.assertIsNotNone(window.theme_progress_dialog)
        self.assertTrue(window.theme_progress_dialog.isVisible())
        self.assertEqual(window.theme_progress_dialog.minimum(), 0)
        self.assertEqual(window.theme_progress_dialog.maximum(), 0)
        self.assertIn(
            "dark theme",
            window.theme_progress_dialog.labelText().casefold(),
        )
        deadline = time.monotonic() + 30
        while window.ui_theme != "dark" and time.monotonic() < deadline:
            QTest.qWait(50)

        self.assertEqual(window.ui_theme, "dark")
        self.assertIsNone(window.theme_progress_dialog)
        self.assertIs(window.centralWidget(), central_widget)
        self.assertIs(window.settings_button, settings_button)
        self.assertNotEqual(
            settings_button.icon().pixmap(20, 20).toImage(),
            light_settings_icon,
        )
        self.assertEqual(window.theme_button.property("themeIcon"), "moon")
        self.assertEqual(window.page_stack.currentIndex(), 5)
        self.assertEqual(window.text_editor.toPlainText(), "Theme-safe text")
        self.assertIn("Theme log survives", window.log_view.toPlainText())
        self.assertEqual(
            window.theme_button.toolTip(),
            window.tr("switch_to_light_theme", "Switch to light theme"),
        )
        self.assertIn("#0b1220", window.styleSheet())
        self.assertEqual(SettingsManager(config_path).settings["ui_theme"], "dark")

        window.theme_button.click()
        deadline = time.monotonic() + 30
        while window.ui_theme != "light" and time.monotonic() < deadline:
            QTest.qWait(50)

        self.assertEqual(window.ui_theme, "light")
        self.assertIsNone(window.theme_progress_dialog)
        self.assertEqual(window.theme_button.property("themeIcon"), "sun")
        self.assertEqual(
            settings_button.icon().pixmap(20, 20).toImage(),
            light_settings_icon,
        )
        self.assertNotIn("#0b1220", window.styleSheet())
        self.assertEqual(SettingsManager(config_path).settings["ui_theme"], "light")

    def test_multiple_nvidia_gpus_can_be_selected_from_general_settings(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_path = Path(temporary.name) / "config.json"
        detection = GPUDetectionResult(
            gpus=[
                GPUInfo(
                    name="NVIDIA RTX 3070",
                    index=0,
                    memory_total_mb=8192,
                    source="nvidia-smi",
                ),
                GPUInfo(
                    name="NVIDIA RTX 5090",
                    index=1,
                    memory_total_mb=32768,
                    source="nvidia-smi",
                ),
            ],
            method="nvidia-smi",
        )

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "app.ui.main_window.SettingsManager",
                return_value=SettingsManager(config_path),
            ),
            patch("app.ui.main_window.detect_gpus", return_value=detection),
        ):
            window = MainWindow()
            self.addCleanup(window.deleteLater)

            self.assertEqual(window.gpu_device_combo.count(), 3)
            self.assertFalse(window.sidebar_change_gpu_button.isHidden())
            window.sidebar_change_gpu_button.click()
            self.assertEqual(window.page_stack.currentIndex(), 1)
            self.assertEqual(window.settings_tabs.currentIndex(), 0)

            with (
                patch.object(
                    window.engine_host_client,
                    "health",
                    return_value=False,
                ),
                patch.object(window, "_unload_faster_whisper"),
                patch.object(window, "_unload_preloaded_tts_engine"),
                patch.object(window, "_refresh_all_engine_status"),
            ):
                window.gpu_device_combo.setCurrentIndex(
                    window.gpu_device_combo.findData("1")
                )

            self.assertEqual(window.gpu_device_selection, "1")
            self.assertEqual(
                SettingsManager(config_path).settings["gpu_device_index"],
                "1",
            )
            self.assertEqual(
                window.qwen_manager.runtime_environment()["CUDA_VISIBLE_DEVICES"],
                "1",
            )
            self.assertIn("RTX 5090", window._sidebar_hardware_summary())

    def test_verify_pending_button_starts_without_an_ffmpeg_path_widget(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.review_max_retries_spin.setValue(1)
        window.review_verify_button.setEnabled(True)

        with (
            patch.object(
                window,
                "_current_audiobook",
                return_value=SimpleNamespace(id=123),
            ),
            patch.object(window, "_show_review_page"),
            patch.object(window, "_current_voice_config", return_value=None),
            patch.object(
                window.faster_whisper_manager,
                "is_installed",
                return_value=True,
            ),
            patch("app.ui.main_window.AudiobookStore"),
            patch.object(QThread, "start", autospec=True) as start_thread,
        ):
            window.review_verify_button.click()

        start_thread.assert_called_once()
        self.assertIsNotNone(window.verification_worker)
        self.assertEqual(
            window.verification_worker.ffmpeg_path,
            window.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
        )
        worker = window.verification_worker
        thread = window.verification_thread
        window.verification_worker = None
        window.verification_thread = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()

    def test_normalized_editor_preview_keeps_original_read_only_and_refreshable(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manager = SettingsManager(root / "config.json")
        values = deepcopy(manager.settings)
        values["text_normalization"] = {"enabled": True, "language": "en"}
        manager.save(values)
        normalization_store = TextNormalizationStore(
            root / "text_normalization.sqlite3"
        )

        with (
            patch(
                "app.ui.main_window.SettingsManager",
                return_value=SettingsManager(root / "config.json"),
            ),
            patch(
                "app.ui.text_normalization_settings.TextNormalizationStore",
                return_value=normalization_store,
            ),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)

        source = "Dr. Smith paid $2 for 3 GB. {{pause 250}}"
        window.text_editor.setPlainText(source)
        self.assertEqual(window.editor_tabs.count(), 2)
        self.assertTrue(window.editor_tabs.isTabVisible(1))
        self.assertTrue(window.normalized_text_editor.isReadOnly())

        window.editor_tabs.setCurrentIndex(1)

        self.assertEqual(window.text_editor.toPlainText(), source)
        self.assertEqual(
            window.normalized_text_editor.toPlainText(),
            "Doctor Smith paid two dollars for three gigabytes. {{pause 250}}",
        )
        self.assertFalse(window.normalization_preview_stale)

        window.text_editor.setPlainText("The 21st chapter uses AI.")
        self.assertTrue(window.normalization_preview_stale)
        window.normalize_preview_button.click()
        self.assertEqual(
            window.normalized_text_editor.toPlainText(),
            "The twenty-first chapter uses A I.",
        )

        window.text_normalization_panel.enabled_checkbox.setChecked(False)
        self.assertEqual(window.editor_tabs.count(), 1)
        self.assertEqual(window.editor_tabs.indexOf(window.normalized_text_page), -1)
        self.assertEqual(window.editor_tabs.currentIndex(), 0)

        window.text_normalization_panel.enabled_checkbox.setChecked(True)
        self.assertEqual(window.editor_tabs.count(), 2)
        self.assertGreaterEqual(
            window.editor_tabs.indexOf(window.normalized_text_page),
            0,
        )

        # Returning to Generate repairs any stale Qt tab visibility state.
        normalized_index = window.editor_tabs.indexOf(window.normalized_text_page)
        window.editor_tabs.setTabVisible(normalized_index, False)
        self.assertFalse(window.editor_tabs.isTabVisible(normalized_index))
        window._show_generation()
        repaired_index = window.editor_tabs.indexOf(window.normalized_text_page)
        self.assertGreaterEqual(repaired_index, 0)
        self.assertTrue(window.editor_tabs.isTabVisible(repaired_index))

    def test_russian_silero_controls_are_explicit_and_only_visible_for_russian(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manager = SettingsManager(root / "config.json")
        values = deepcopy(manager.settings)
        values["text_normalization"] = {"enabled": False, "language": "auto"}
        manager.save(values)
        normalization_store = TextNormalizationStore(
            root / "text_normalization.sqlite3"
        )
        with (
            patch(
                "app.ui.main_window.SettingsManager",
                return_value=SettingsManager(root / "config.json"),
            ),
            patch(
                "app.ui.text_normalization_settings.TextNormalizationStore",
                return_value=normalization_store,
            ),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        panel = window.text_normalization_panel
        # Keep the test independent from optional assets installed on the host.
        silero_state = {"installed": False}
        window.russian_normalization_manager.is_installed = (
            lambda: silero_state["installed"]
        )
        panel.set_russian_silero_status(False)

        self.assertTrue(panel.russian_silero_frame.isHidden())
        panel.enabled_checkbox.setChecked(True)
        panel.language_combo.setCurrentIndex(panel.language_combo.findData("ru"))

        self.assertFalse(panel.russian_silero_frame.isHidden())
        self.assertFalse(panel.russian_silero_checkbox.isEnabled())
        self.assertTrue(panel.russian_silero_install_button.isEnabled())
        requested: list[bool] = []
        panel.russianSileroInstallRequested.connect(lambda: requested.append(True))
        panel.russian_silero_install_button.click()
        self.assertEqual(requested, [True])
        self.assertIsNotNone(window.russian_normalization_install_dialog)
        window.russian_normalization_install_dialog.reject()

        silero_state["installed"] = True
        panel.set_russian_silero_status(True)
        self.assertTrue(panel.russian_silero_checkbox.isEnabled())
        panel.russian_silero_checkbox.setChecked(True)
        config = panel.configuration()
        self.assertTrue(config["russian_silero"]["enabled"])

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

    def test_detected_model_is_shown_separately_from_missing_runtime(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        class ModelOnlyManager:
            cache_dir = Path("C:/temp/qwen-cache")
            install_dir = Path("C:/temp/qwen")

            def has_model_files(self) -> bool:
                return True

            def is_installed(self) -> bool:
                return False

            def has_runtime(self) -> bool:
                return False

        window.qwen_manager = ModelOnlyManager()
        window._refresh_qwen_status()

        self.assertIn("Model detected", window.qwen_status_label.text())
        self.assertEqual(window.qwen_install_button.text(), "Repair / Update")
        self.assertTrue(window.qwen_install_button.isEnabled())

    def test_tts_engine_install_uses_confirmation_and_progress_modal(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        with (
            patch(
                "app.ui.main_window.available_disk_space_gb",
                return_value=(40.0, "C:\\"),
            ),
            patch(
                "app.ui.main_window.EngineInstallDialog.open",
                autospec=True,
            ) as open_dialog,
            patch.object(window, "_start_omnivoice_operation") as start_install,
        ):
            window._install_omnivoice()

            dialog = window.engine_install_dialogs["omnivoice"]
            open_dialog.assert_called_once_with()
            self.assertIn(
                "30",
                " ".join(label.text() for label in dialog.findChildren(QLabel)),
            )
            dialog.install_button.click()
            start_install.assert_called_once_with("install")

            window._on_omnivoice_progress(35, 100, "Downloading OmniVoice...")
            self.assertEqual(dialog.progress_bar.value(), 35)
            self.assertEqual(dialog.progress_label.text(), "Downloading OmniVoice...")

            window._finish_tts_engine_install_dialog(
                "omnivoice",
                True,
                "OmniVoice installation completed.",
            )
            self.assertEqual(dialog.progress_bar.value(), 100)
            self.assertEqual(dialog.install_button.text(), window.tr("close", "Close"))
            dialog.install_button.click()
            self.assertNotIn("omnivoice", window.engine_install_dialogs)

    def test_pending_omnivoice_setup_never_overrides_another_selected_engine(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.settings = deepcopy(DEFAULT_SETTINGS)
        window.settings["tts_engine"] = "f5_russian"
        window.settings["installer_setup"] = {
            "profile": "gpu",
            "pending_installs": ["omnivoice", "faster_whisper"],
            "completed": False,
        }
        with (
            patch.object(window.settings_manager, "save") as save_settings,
            patch("app.ui.main_window.QMessageBox.information") as show_message,
        ):
            window._select_tts_engine("f5_russian")
            window._run_pending_installer_setup()

        self.assertEqual(window.tts_engine_combo.currentData(), "f5_russian")
        self.assertEqual(window.settings["tts_engine"], "f5_russian")
        self.assertTrue(window.settings["installer_setup"]["completed"])
        self.assertEqual(window.settings["installer_setup"]["pending_installs"], [])
        save_settings.assert_called()
        show_message.assert_not_called()

    def test_voice_preview_has_pause_stop_and_restart_controls(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.voices_player.setSource(QUrl.fromLocalFile("C:/voice-sample.mp3"))
        window._refresh_voice_preview_controls()

        self.assertTrue(window.voice_preview_restart_button.isEnabled())
        with (
            patch.object(window.voices_player, "stop") as stop,
            patch.object(window.voices_player, "setPosition") as set_position,
            patch.object(window.voices_player, "play") as play,
        ):
            window._stop_voice_preview()
            window._restart_voice_preview()

        stop.assert_called_once_with()
        self.assertEqual(set_position.call_args_list[0].args, (0,))
        self.assertEqual(set_position.call_args_list[1].args, (0,))
        play.assert_called_once_with()

    def test_recent_projects_menu_opens_a_stored_project(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with patch(
            "app.ui.main_window.SettingsManager",
            return_value=SettingsManager(root / "config.json"),
        ):
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        store = AudiobookStore(root / "projects.sqlite3")
        older = store.create_audiobook(
            "Primer texto",
            {"engine": "piper"},
            root / "output-1",
            "safe_chunks",
            "single",
            "Proyecto anterior",
            project_dir=root / "project-1",
        )
        recent = store.create_audiobook(
            "Segundo texto",
            {"engine": "piper"},
            root / "output-2",
            "safe_chunks",
            "single",
            "Proyecto MCP",
            project_dir=root / "project-2",
        )
        window.audiobook_store = store
        window.current_audiobook_id = None
        window.project_dirty = False

        window._populate_recent_projects_menu()

        actions = window.recent_projects_menu.actions()
        self.assertEqual(
            [action.text() for action in actions],
            ["Proyecto MCP", "Proyecto anterior"],
        )
        with patch.object(window, "_load_project") as load_project:
            actions[0].trigger()
        load_project.assert_called_once_with(recent.id)
        self.assertNotEqual(older.id, recent.id)

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

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget, QTextEdit

from app.ui.main_window import MainWindow
from app.ui.markup_highlighter import LTVMarkupHighlighter


class RecordingHighlighter(LTVMarkupHighlighter):
    def __init__(self, document):
        self.highlighted_text = []
        self.refreshes = 0
        super().__init__(document)

    def rehighlight(self):
        self.refreshes += 1
        super().rehighlight()

    def highlightBlock(self, text):
        if not self._suspension_depth:
            self.highlighted_text.append(text)
        super().highlightBlock(text)

    def reset_recording(self):
        self.highlighted_text.clear()
        self.refreshes = 0


class ProjectHighlightingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def make_editor(self, text=""):
        editor = QTextEdit()
        self.addCleanup(editor.deleteLater)
        editor.setPlainText(text)
        highlighter = RecordingHighlighter(editor.document())
        self.application.processEvents()
        highlighter.reset_recording()
        return editor, highlighter

    def test_unchanged_settings_do_not_rehighlight(self):
        editor, highlighter = self.make_editor('{{voice "Narrator"}} Text')
        before = editor.document().firstBlock().layout().formats()
        self.assertTrue(before)
        highlighter.set_enabled(True)
        highlighter.set_corrector_enabled(True)
        highlighter.set_dark_mode(False)
        self.assertEqual(highlighter.refreshes, 0)
        self.assertEqual(highlighter.highlighted_text, [])
        highlighter.set_enabled(False)
        self.assertFalse(editor.document().firstBlock().layout().formats())
        highlighter.reset_recording()
        highlighter.set_enabled(False)
        highlighter.set_corrector_enabled(False)
        highlighter.reset_recording()
        highlighter.set_corrector_enabled(False)
        self.assertEqual(highlighter.refreshes, 0)
        highlighter.set_enabled(True)
        self.assertTrue(editor.document().firstBlock().layout().formats())

    def test_nested_batch_only_highlights_final_text_with_final_settings(self):
        editor, highlighter = self.make_editor('{{voice "Old"}} old text')
        with highlighter.suspend_highlighting():
            highlighter.set_enabled(False)
            with highlighter.suspend_highlighting():
                highlighter.set_dark_mode(True)
                highlighter.set_corrector_enabled(False)
                editor.setPlainText('{{voice "New"}} new text')
            highlighter.set_enabled(True)
            self.assertEqual(highlighter.refreshes, 0)
            self.assertEqual(highlighter.highlighted_text, [])
        self.application.processEvents()
        self.assertEqual(highlighter.refreshes, 1)
        self.assertEqual(highlighter.highlighted_text, ['{{voice "New"}} new text'])
        self.assertTrue(editor.document().firstBlock().layout().formats())

    def test_corrector_setting_updates_error_formatting(self):
        editor, highlighter = self.make_editor("Text with {{ an unclosed command")
        self.assertTrue(editor.document().firstBlock().layout().formats())
        highlighter.set_corrector_enabled(False)
        self.assertFalse(editor.document().firstBlock().layout().formats())
        highlighter.set_corrector_enabled(True)
        self.assertTrue(editor.document().firstBlock().layout().formats())

    def make_project_window(self, source_text, settings):
        # Real Qt documents/signals, with storage and unrelated UI services isolated.
        old_text = ('{{voice "Old"}} OLD_PROJECT ' + 'Long paragraph. ' * 8 + '\n') * 2500
        editor, highlighter = self.make_editor(old_text)
        normalized, normalized_highlighter = self.make_editor(old_text)
        normalized_highlighter.set_corrector_enabled(False)
        normalized_highlighter.reset_recording()
        tabs = QTabWidget()
        self.addCleanup(tabs.deleteLater)
        tabs.addTab(editor, "Original")
        tabs.addTab(normalized, "Normalized")
        tabs.setCurrentWidget(normalized)
        audiobook = SimpleNamespace(
            project_dir=Path("C:/projects/new"), output_dir=Path("C:/projects/new/exports"),
            id=2, title="New project", source_text=source_text,
            project_settings_json=json.dumps(settings), clean_audio_path="",
        )
        holder = SimpleNamespace(
            audiobook_store=Mock(get_audiobook=Mock(return_value=audiobook)),
            settings={}, settings_manager=Mock(),
            text_editor=editor, normalized_text_editor=normalized,
            markup_highlighter=highlighter,
            normalized_markup_highlighter=normalized_highlighter,
            editor_tabs=tabs, original_text_page=editor,
            _loading_project=False, _restoring_settings=False, project_dirty=True,
            _set_current_project=Mock(), _refresh_project_title_header=Mock(),
            _mark_normalization_preview_stale=Mock(),
            _update_markup_editor_assist=Mock(),
            _update_text_normalization_editor_state=Mock(),
            audio_mix_preview_panel=Mock(), _refresh_review_page=Mock(),
            _show_generation=Mock(), log_view=Mock(),
            tr=lambda _key, default, **values: default.format(**values),
        )
        holder._show_original_text_tab = lambda: MainWindow._show_original_text_tab(holder)

        def restore_widgets():
            self.assertEqual(editor.toPlainText(), source_text)
            self.assertEqual(normalized.toPlainText(), "")
            self.assertTrue(holder._loading_project)
            MainWindow._set_editor_highlighting_enabled(
                holder, holder.settings.get("editor_syntax_highlighting", True)
            )
            MainWindow._set_markup_corrector_enabled(
                holder, holder.settings.get("markup_corrector_enabled", True)
            )

        holder._restore_settings_widgets = restore_widgets
        holder._restore_settings = lambda: MainWindow._restore_settings(holder)
        holder.editor_changes = Mock()
        editor.textChanged.connect(holder.editor_changes)
        editor.textChanged.connect(lambda: MainWindow._mark_project_dirty(holder))
        holder.tab_changes = Mock()
        tabs.currentChanged.connect(holder.tab_changes)
        return holder

    def test_large_project_switch_only_highlights_new_document(self):
        for source in ("", '{{voice "New"}} NEW_PROJECT'):
            for enabled, corrector in ((True, True), (False, True), (True, False), (False, False)):
                with self.subTest(source=source, enabled=enabled, corrector=corrector):
                    holder = self.make_project_window(source, {
                        "editor_syntax_highlighting": enabled,
                        "markup_corrector_enabled": corrector,
                    })
                    MainWindow._load_project(holder, 2)
                    self.application.processEvents()
                    holder.editor_changes.assert_not_called()
                    holder.tab_changes.assert_not_called()
                    self.assertFalse(holder.project_dirty)
                    self.assertFalse(holder._loading_project)
                    self.assertFalse(holder._restoring_settings)
                    self.assertEqual(holder.text_editor.toPlainText(), source)
                    self.assertEqual(holder.normalized_text_editor.toPlainText(), "")
                    self.assertIs(holder.editor_tabs.currentWidget(), holder.text_editor)
                    for highlighter in (holder.markup_highlighter, holder.normalized_markup_highlighter):
                        self.assertEqual(highlighter.refreshes, 1)
                        self.assertFalse(any("OLD_PROJECT" in text for text in highlighter.highlighted_text))
                    formats = holder.text_editor.document().firstBlock().layout().formats()
                    self.assertEqual(bool(formats), bool(source) and enabled)
                    holder.text_editor.insertPlainText("User edit")
                    self.assertTrue(holder.project_dirty)
                    self.application.processEvents()

    def test_failed_settings_restore_releases_guards_and_highlighters(self):
        holder = self.make_project_window('{{voice "New"}} NEW_PROJECT', {})
        holder._restore_settings_widgets = Mock(side_effect=RuntimeError("settings failed"))
        with self.assertRaisesRegex(RuntimeError, "settings failed"):
            MainWindow._load_project(holder, 2)
        self.assertFalse(holder._loading_project)
        self.assertFalse(holder._restoring_settings)
        for editor in (holder.text_editor, holder.normalized_text_editor, holder.editor_tabs):
            self.assertFalse(editor.signalsBlocked())
        self.assertEqual(holder.markup_highlighter._suspension_depth, 0)
        self.assertEqual(holder.normalized_markup_highlighter._suspension_depth, 0)
        holder.text_editor.setPlainText('{{voice "After failure"}}')
        self.application.processEvents()
        self.assertTrue(holder.text_editor.document().firstBlock().layout().formats())
        holder.editor_changes.assert_called()

    def test_project_load_preserves_existing_signal_and_loading_guards(self):
        holder = self.make_project_window("New text", {})
        holder._loading_project = True
        holder.text_editor.blockSignals(True)
        MainWindow._load_project(holder, 2)
        self.assertTrue(holder._loading_project)
        self.assertTrue(holder.text_editor.signalsBlocked())
        self.assertFalse(holder.normalized_text_editor.signalsBlocked())


if __name__ == "__main__":
    unittest.main()

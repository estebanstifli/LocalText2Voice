import os
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QWidget

from app.core.audiobook_store import AudiobookStore
from app.ui.main_window import MainWindow
from app.ui.widgets import FilePicker, PathPicker


@pytest.fixture
def rename_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    store = AudiobookStore(tmp_path / "books.sqlite3")
    old = tmp_path / "Project7"
    book = store.create_audiobook("Saved text", {}, old / "exports", "safe_chunks", "single", "Old title", {}, old)
    window.audiobook_store = store
    window.current_audiobook_id = book.id
    window.settings = {"output_dir": str(book.output_dir), "unsaved_setting": "keep"}
    window.settings_manager = Mock()
    window.project_title_edit = QLineEdit("New title", window)
    window.text_editor = QTextEdit(window)
    window.text_editor.setPlainText("Unsaved text")
    window.text_editor.insertPlainText("New ")
    window.normalized_text_editor = QTextEdit(window)
    window.output_picker = PathPicker("Browse", str(book.output_dir), window)
    window.reference_picker = FilePicker("Reference", "*.wav", str(old / "reference.wav"), window)
    window.last_output_folder = book.output_dir
    window.review_after_generation_outputs = [str(book.output_dir / "book.mp3")]
    window.audio_mix_preview_panel = SimpleNamespace(dirty_event_uids=set(), clear_context=Mock())
    window.video_storyboard_page = QWidget(window)
    window.video_storyboard_page.project_state = lambda: {
        "source": {"project_id": book.id, "text": "Saved text", "audio_path": str(old / "audio.wav")},
        "scenes": [{"image_path": str(old / "storyboard" / "image.png")}],
    }
    window.video_storyboard_page.set_audiobook_source = Mock()
    window.video_storyboard_page.restore_project_state = Mock()
    window.project_dirty = True
    window.log_view = SimpleNamespace(append_event=Mock())
    window.tr = lambda key, fallback, **kwargs: fallback.format(**kwargs)
    for name in ("_show_error", "_populate_recent_projects_menu", "_refresh_review_page",
                 "_refresh_wav_cache_stats", "_set_audio_mix_preview_context"):
        setattr(window, name, Mock())
    for name in ("_current_project_title", "_check_project_rename_idle", "_release_audio_previews",
                 "_project_audio_write_in_progress", "_refresh_renamed_project_paths"):
        setattr(window, name, MethodType(getattr(MainWindow, name), window))
    with patch("app.ui.main_window.prepare_project_cover", side_effect=lambda root, title, metadata: (metadata, None)):
        yield window, book
    window.deleteLater()
    app.processEvents()


def test_ui_rename_keeps_unsaved_text_and_updates_paths(rename_window):
    window, book = rename_window
    before = window.text_editor.toPlainText()
    position = window.text_editor.textCursor().position()
    MainWindow._commit_project_title(window)
    window._show_error.assert_not_called()
    renamed = window.audiobook_store.get_audiobook(book.id)
    assert renamed.project_dir.name == "New title"
    assert window.text_editor.toPlainText() == before
    assert window.text_editor.textCursor().position() == position
    assert window.text_editor.document().isUndoAvailable()
    assert window.project_dirty
    assert window.settings["unsaved_setting"] == "keep"
    assert window.output_picker.path() == renamed.output_dir
    assert window.reference_picker.path() == renamed.project_dir / "reference.wav"
    assert window.last_output_folder == renamed.output_dir
    assert window.settings["output_dir"] == str(renamed.output_dir)
    state = window.video_storyboard_page.restore_project_state.call_args.args[0]
    assert state["scenes"][0]["image_path"] == str(renamed.project_dir / "storyboard" / "image.png")


@pytest.mark.parametrize("busy", ["worker_thread", "verification_thread", "video_storyboard_frame_thread", "mix_edits", "review_edits"])
def test_ui_busy_does_not_change_settings_title_or_folder(rename_window, busy):
    window, book = rename_window
    original = dict(window.settings)
    if busy == "mix_edits":
        window.audio_mix_preview_panel.dirty_event_uids.add("event")
    elif busy == "review_edits":
        window.review_source_detail = QTextEdit(window)
        window.review_source_detail.setPlainText("Unsaved review")
        window._selected_review_segment = lambda: SimpleNamespace(source_text="Saved review")
    else:
        setattr(window, busy, object())
    MainWindow._commit_project_title(window)
    window._show_error.assert_called_once()
    assert window.audiobook_store.get_audiobook(book.id) == book
    assert window.settings == original
    assert window.project_title_edit.text() == book.title
    assert book.project_dir.is_dir()


def test_same_title_synchronizes_old_folder(rename_window):
    window, book = rename_window
    window.project_title_edit.setText(book.title)
    MainWindow._commit_project_title(window, sync_folder=True)
    window._show_error.assert_not_called()
    assert window.audiobook_store.get_audiobook(book.id).project_dir.name == book.title


def test_focus_change_alone_does_not_move_old_project(rename_window):
    window, book = rename_window
    window.project_title_edit.setText(book.title)
    MainWindow._commit_project_title(window)
    assert window.audiobook_store.get_audiobook(book.id) == book
    assert book.project_dir.is_dir()


def test_storyboard_output_helpers_follow_current_project_location(rename_window):
    window, book = rename_window
    original = MainWindow._video_storyboard_output_dir(window, candidate=False)
    assert original == book.project_dir / "storyboard" / "frames"
    renamed = window.audiobook_store.rename_audiobook(book.id, "Current location")
    assert MainWindow._video_storyboard_output_dir(window, candidate=True) == renamed.project_dir / "storyboard" / "candidates"
    assert MainWindow._video_storyboard_video_output_dir(window, candidate=False) == renamed.project_dir / "storyboard" / "video" / "clips"


def test_failed_move_leaves_ui_on_original_project(rename_window):
    window, book = rename_window
    with patch.object(Path, "rename", side_effect=PermissionError("file locked")):
        MainWindow._commit_project_title(window)
    window._show_error.assert_called_once()
    assert window.project_title_edit.text() == book.title
    assert window.audiobook_store.get_audiobook(book.id) == book
    assert window.output_picker.path() == book.output_dir


def test_committed_move_with_manifest_failure_refreshes_ui(rename_window):
    window, book = rename_window
    with patch.object(window.audiobook_store, "flush_project_manifest", side_effect=OSError("manifest locked")):
        MainWindow._commit_project_title(window)
    window._show_error.assert_called_once()
    actual = window.audiobook_store.get_audiobook(book.id)
    assert actual.title == "New title"
    assert window.output_picker.path() == actual.output_dir
    assert window.project_title_edit.text() == actual.title
    assert window.text_editor.toPlainText() == "New Unsaved text"
    restarted = AudiobookStore(window.audiobook_store.db_path)
    assert restarted.get_audiobook(book.id).project_dir == actual.project_dir

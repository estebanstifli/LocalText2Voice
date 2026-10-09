import json
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from app.core.audiobook_store import AudiobookStore
from app.core.bulk_audiobook_store import BulkAudiobookStore
from app.core.project_output import output_preferences, resolve_project_output
from app.core.settings_manager import SettingsManager
from app.ui.main_window import MainWindow
from app.ui.widgets import PathPicker


def test_default_and_legacy_output_preferences(tmp_path):
    assert SettingsManager(tmp_path / "new.json").settings["output_dir_mode"] == "relative"
    assert resolve_project_output({}, tmp_path) == tmp_path / "exports"
    assert output_preferences({"output_dir": str(tmp_path / "exports")}, tmp_path) == {
        "output_dir_mode": "relative", "output_dir": "exports"}
    external = tmp_path.parent / "external"
    assert output_preferences({}, tmp_path, external) == {
        "output_dir_mode": "absolute", "output_dir": str(external)}
    config = tmp_path / "legacy.json"
    config.write_text(json.dumps({"output_dir": str(external)}))
    manager = SettingsManager(config)
    assert manager.settings["output_dir_mode"] == "absolute"
    manager.save({"output_dir_mode": "relative", "output_dir": "audio/final"})
    assert SettingsManager(config).settings["output_dir"] == "audio/final"


@pytest.mark.parametrize("mode,value", [("relative", "../outside"), ("absolute", "exports"), ("relative", "C:/absolute" if os.name == "nt" else "/absolute")])
def test_invalid_mode_and_path_combinations_are_rejected(tmp_path, mode, value):
    with pytest.raises(ValueError):
        resolve_project_output({"output_dir_mode": mode, "output_dir": value}, tmp_path)


@pytest.mark.parametrize("mode", ["relative", "absolute"])
def test_rename_keeps_output_preference_and_correct_destination(tmp_path, mode):
    store = AudiobookStore(tmp_path / "books.sqlite3")
    root = tmp_path / "Old"
    value = "audio/final" if mode == "relative" else str(root / "fixed-output")
    settings = {"output_dir_mode": mode, "output_dir": value}
    output = resolve_project_output(settings, root)
    book = store.create_audiobook("Text", {}, output, "safe_chunks", "single", "Old", settings, root)
    new = store.rename_audiobook(book.id, "Renamed")
    saved = json.loads(new.project_settings_json)
    assert saved["output_dir"] == value
    assert new.output_dir == (new.project_dir / "audio/final" if mode == "relative" else output)


@pytest.fixture
def output_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.settings = {}
    window.current_audiobook_id = None
    window._restoring_settings = False
    window.output_mode_combo = QComboBox(window)
    window.output_mode_combo.addItem("Relative", "relative")
    window.output_mode_combo.addItem("Absolute", "absolute")
    window.output_picker = PathPicker("Browse", "exports", window)
    window._output_project_root = lambda: tmp_path / "Project"
    window._save_settings = Mock()
    window.tr = lambda key, fallback: fallback
    window._output_preferences = MethodType(MainWindow._output_preferences, window)
    window._commit_output_preferences = MethodType(MainWindow._commit_output_preferences, window)
    yield window
    window.deleteLater()
    app.processEvents()


def test_mode_changes_and_browse_preserve_valid_values(output_window, tmp_path):
    window = output_window
    window.output_mode_combo.setCurrentIndex(1)
    MainWindow._on_output_mode_changed(window)
    assert window.output_picker.path() == tmp_path / "Project" / "exports"
    window.output_mode_combo.setCurrentIndex(0)
    MainWindow._on_output_mode_changed(window)
    assert window.output_picker.path() == Path("exports")
    with patch("app.ui.main_window.QFileDialog.getExistingDirectory", return_value=str(tmp_path / "Project" / "audio")):
        MainWindow._browse_output_folder(window)
    assert window.output_picker.path() == Path("audio")
    assert window.output_mode_combo.currentData() == "relative"
    with patch("app.ui.main_window.QFileDialog.getExistingDirectory", return_value=str(tmp_path / "external")):
        MainWindow._browse_output_folder(window)
    assert window.output_picker.path() == tmp_path / "external"
    assert window.output_mode_combo.currentData() == "absolute"


def test_project_creation_uses_editable_relative_or_absolute_output(output_window, tmp_path):
    window = output_window
    window.output_picker.set_path("audio/final")
    assert MainWindow._project_output_dir(window, tmp_path / "One") == tmp_path / "One/audio/final"
    assert MainWindow._project_output_dir(window, tmp_path / "Two") == tmp_path / "Two/audio/final"
    window.output_mode_combo.setCurrentIndex(1)
    window.output_picker.set_path(tmp_path / "external")
    assert MainWindow._project_output_dir(window, tmp_path / "Three") == tmp_path / "external"


def test_setting_output_persists_without_saving_source_text(tmp_path):
    store = AudiobookStore(tmp_path / "books.sqlite3")
    root = tmp_path / "Project"
    book = store.create_audiobook("Original source", {}, root / "exports", "safe_chunks", "single", "Project", {"keep": 42}, root)
    updated = store.update_project_output(book.id, {"output_dir_mode": "relative", "output_dir": "audio/final"})
    assert updated.output_dir == root / "audio/final"
    assert updated.source_text == "Original source"
    assert json.loads(updated.project_settings_json)["keep"] == 42
    assert json.loads(updated.project_settings_json)["output_dir"] == "audio/final"
    with pytest.raises(ValueError):
        store.update_project_output(book.id, {"output_dir_mode": "relative", "output_dir": "../outside"})
    assert store.get_audiobook(book.id) == updated


def test_real_window_restores_each_projects_output_and_preserves_unsaved_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = AudiobookStore(tmp_path / "books.sqlite3")
    relative = store.create_audiobook("Relative source", {}, tmp_path / "One/audio/final", "safe_chunks", "single", "One",
                                    {"output_dir_mode": "relative", "output_dir": "audio/final"}, tmp_path / "One")
    external = store.create_audiobook("External source", {}, tmp_path / "External", "safe_chunks", "single", "Two", {}, tmp_path / "Two")
    with (
        patch("app.ui.main_window.SettingsManager", return_value=SettingsManager(tmp_path / "config.json")),
        patch("app.ui.main_window.AudiobookStore", return_value=store),
        patch("app.ui.main_window.BulkAudiobookStore", return_value=BulkAudiobookStore(tmp_path / "bulk.sqlite3")),
        patch.object(MainWindow, "_migrate_legacy_duplicate_asset_root"),
        patch.object(MainWindow, "_maybe_check_for_updates"),
        patch.object(MainWindow, "_sync_engine_host_memory_state"),
        patch.object(MainWindow, "_refresh_local_server_status"),
        patch.object(MainWindow, "_show_error") as error,
    ):
        window = MainWindow()
        try:
            for book in (relative, external, relative):
                window._load_project(book.id)
                assert window.output_mode_combo.currentData() == ("relative" if book.id == relative.id else "absolute")
                assert window.output_picker.path() == (Path("audio/final") if book.id == relative.id else external.output_dir)
            window.text_editor.setPlainText("Unsaved editor changes")
            window.output_picker.set_path("new-audio")
            window.output_picker.path_edit.editingFinished.emit()
            assert window.text_editor.toPlainText() == "Unsaved editor changes"
            assert store.get_audiobook(relative.id).source_text == "Relative source"
            assert store.get_audiobook(relative.id).output_dir == relative.project_dir / "new-audio"
            error.assert_not_called()
        finally:
            window.deleteLater()
            app.processEvents()

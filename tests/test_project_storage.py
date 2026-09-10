from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch

from app.core.settings_manager import SettingsManager
from app.ui.main_window import MainWindow


def project_window(parent):
    book = SimpleNamespace(id=1, title="My book")
    window = SimpleNamespace(
        settings={"projects_dir": str(parent)},
        current_audiobook_id=None,
        project_dirty=True,
        _restoring_settings=False,
        settings_manager=Mock(),
        text_editor=SimpleNamespace(toPlainText=lambda: "The story begins."),
        output_picker=SimpleNamespace(path=lambda: parent / "exports", set_path=Mock()),
        split_combo=SimpleNamespace(currentData=lambda: "safe_chunks"),
        audiobook_store=Mock(),
        _project_settings_snapshot=lambda: {},
        _project_voice_config_snapshot=lambda: {"engine": "piper"},
        _current_project_title=lambda: "My book",
        _materialize_project_book_assets=lambda audiobook, snapshot: audiobook,
        _set_current_project=Mock(),
        _load_project=Mock(),
        _confirm_project_switch=lambda: True,
        _show_error=Mock(),
        log_view=SimpleNamespace(append_event=Mock()),
        tr=lambda key, fallback, **kwargs: fallback.format(**kwargs),
    )
    window.audiobook_store.create_audiobook.return_value = book
    window.audiobook_store.save_audiobook_project.return_value = book
    window.audiobook_store.next_project_title.return_value = "My book"
    window._safe_project_folder_name = MainWindow._safe_project_folder_name
    for name in ("_default_project_parent", "_automatic_project_location",
                 "_unique_bulk_project_dir", "_project_output_dir", "_create_new_project"):
        setattr(window, name, MethodType(getattr(MainWindow, name), window))
    return window


def test_default_is_under_application_and_ignores_last_save_as(tmp_path):
    window = SimpleNamespace(settings={"last_project_parent": str(tmp_path / "elsewhere")})
    with patch("app.utils.paths.application_root", return_value=tmp_path):
        assert MainWindow._default_project_parent(window) == tmp_path / "projects"


def test_new_and_first_save_create_unique_folders_without_dialogs(tmp_path):
    window = project_window(tmp_path / "projects")
    with patch("app.ui.main_window.QFileDialog.getExistingDirectory", side_effect=AssertionError), \
         patch("app.ui.main_window.QInputDialog.getText", side_effect=AssertionError):
        MainWindow._new_project(window)
        first = window.audiobook_store.create_audiobook.call_args.args[-1]
        assert first == tmp_path / "projects" / "My book"
        (first / "keep.txt").write_text("Existing content")
        assert MainWindow._save_project(window)
        second = window.audiobook_store.create_audiobook.call_args.args[-1]
    assert second == tmp_path / "projects" / "My book (2)"
    assert (second / "exports").is_dir()
    assert (first / "keep.txt").read_text() == "Existing content"
    window._show_error.assert_not_called()


def test_existing_save_keeps_project_and_save_as_still_prompts(tmp_path):
    window = project_window(tmp_path / "projects")
    window.current_audiobook_id = 1
    assert MainWindow._save_project(window)
    window.audiobook_store.create_audiobook.assert_not_called()
    window.audiobook_store.save_audiobook_project.assert_called_once()
    window._prompt_project_location = Mock(return_value=None)
    assert not MainWindow._save_project_as(window)
    window._prompt_project_location.assert_called_once()


def test_unwritable_project_location_reports_failure_without_fallback(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("Keep")
    window = project_window(blocked)
    assert not MainWindow._save_project(window)
    window._show_error.assert_called_once()
    window.audiobook_store.create_audiobook.assert_not_called()
    assert blocked.read_text() == "Keep"


def test_project_directory_setting_persists_and_defaults_for_old_config(tmp_path):
    config = tmp_path / "config.json"
    config.write_text('{}')
    manager = SettingsManager(config)
    settings = manager.load()
    assert settings["projects_dir"] == "projects"
    settings["projects_dir"] = str(tmp_path / "custom")
    manager.save(settings)
    assert SettingsManager(config).load()["projects_dir"] == str(tmp_path / "custom")

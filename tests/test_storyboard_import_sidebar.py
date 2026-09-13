import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.core.settings_manager import SettingsManager
from app.ui.main_window import MainWindow
from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog
from app.workers.video_storyboard_video_import_worker import VideoStoryboardVideoImportWorker


def tr(key, default, **values):
    return default.format(**values)


def test_sidebar_collapse_preserves_navigation_and_expands_from_logo(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch("app.ui.main_window.SettingsManager", return_value=SettingsManager(tmp_path / "config.json")):
        window = MainWindow()
    original = {key: button.text() for key, button in window.nav_buttons.items()}
    window.sidebar_collapse_button.click()
    assert SettingsManager(tmp_path / "config.json").settings["sidebar_collapsed"] is True
    assert window.sidebar.width() == 72
    assert window.sidebar_engine_card.isHidden()
    assert all(not button.text() and button.toolTip() == original[key] for key, button in window.nav_buttons.items())
    window.nav_buttons["video_storyboard"].click()
    assert window.page_stack.currentWidget() is window.video_storyboard_page
    assert window.nav_buttons["video_storyboard"].property("active")
    window.sidebar_logo_button.click()
    assert SettingsManager(tmp_path / "config.json").settings["sidebar_collapsed"] is False
    assert window.sidebar.width() == 260
    assert {key: button.text() for key, button in window.nav_buttons.items()} == original
    window.deleteLater()
    app.processEvents()


def test_sidebar_restores_saved_preference_on_startup(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = SettingsManager(tmp_path / "config.json")
    manager.save({"sidebar_collapsed": True})
    with patch("app.ui.main_window.SettingsManager", return_value=manager):
        window = MainWindow()
    assert window.sidebar.width() == 72
    assert window.sidebar_title_widget.isHidden()
    assert all(not button.text() for button in window.nav_buttons.values())
    window.sidebar_logo_button.click()
    assert window.sidebar.width() == 260
    assert SettingsManager(tmp_path / "config.json").settings["sidebar_collapsed"] is False
    window.deleteLater()
    app.processEvents()


def test_import_dialog_uses_separate_signal_and_never_rejects_original(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "my-video.mov"
    source.write_bytes(b"source")
    dialog = VideoStoryboardVideoDialog(tr, {"id": "001", "_video_provider": "disabled"}, {})
    requested, rejected = [], []
    dialog.importRequested.connect(requested.append)
    dialog.candidateRejected.connect(lambda scene, path: rejected.append(path))
    with patch("app.ui.video_storyboard_video_dialog.QFileDialog.getOpenFileName", return_value=(str(source), "")):
        dialog.open_video_button.click()
    assert requested == [str(source)]
    assert dialog._generating
    assert not dialog.open_video_button.isEnabled()
    assert dialog._candidate_path == ""
    dialog.set_failed("Invalid video")
    assert dialog.open_video_button.isEnabled()
    dialog.reject()
    assert rejected == []
    assert source.read_bytes() == b"source"
    dialog.deleteLater()
    app.processEvents()


def test_import_worker_never_removes_existing_destination(tmp_path):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"untouched")
    worker = VideoStoryboardVideoImportWorker("001", source, source, "ffmpeg")
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors
    assert source.read_bytes() == b"untouched"

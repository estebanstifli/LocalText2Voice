import json
import os

import pytest

from app.core.settings_manager import SettingsManager
from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
from app.server.ltv_service import public_settings_snapshot
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.f5_russian_manager import F5RussianManager
from app.tts.indextts_manager import IndexTTSManager
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.qwen_manager import QwenManager
from app.utils.huggingface_auth import huggingface_runtime_environment
from app.verification.faster_whisper_manager import FasterWhisperManager


@pytest.mark.parametrize("manager_type", [
    ChatterboxManager, QwenManager, OmniVoiceManager, F5RussianManager,
    IndexTTSManager, FasterWhisperManager,
])
def test_existing_manager_reads_saved_token_changes_without_mutating_parent(
    tmp_path, monkeypatch, manager_type
):
    monkeypatch.setenv("HF_TOKEN", "hf_external_test")
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr("app.utils.huggingface_auth.application_root", lambda: tmp_path)
    settings = SettingsManager(tmp_path / "config.json")
    manager = manager_type(install_dir=tmp_path / "model")

    assert manager.runtime_environment()["HF_TOKEN"] == "hf_external_test"
    for token in ("hf_saved_test", "hf_updated_test", ""):
        settings.settings["hf_token"] = token
        settings.save()
        env = manager.runtime_environment()
        assert env["HF_TOKEN"] == (token or "hf_external_test")
        assert env["HF_HOME"] == str(manager.cache_dir)
        assert os.environ["HF_TOKEN"] == "hf_external_test"


@pytest.mark.parametrize("contents", [None, "{broken", "[]", '{"hf_token": null}',
                                     '{"hf_token": 123}', '{"hf_token": "  "}'])
def test_missing_or_invalid_config_preserves_external_auth(tmp_path, contents):
    path = tmp_path / "config.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    parent = {"HF_TOKEN": "hf_external_test", "HF_TOKEN_PATH": "external/token"}
    assert huggingface_runtime_environment(parent, path) == parent
    assert huggingface_runtime_environment({}, path) == {}


@pytest.mark.parametrize("token, expected", [
    ("  hf_saved_test\n", "hf_saved_test"), (None, ""), (123, ""), ("", ""),
])
def test_token_persistence_and_public_settings_redaction(tmp_path, token, expected):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hf_token": token}), encoding="utf-8")
    manager = SettingsManager(path)
    assert manager.settings["hf_token"] == expected
    manager.save()
    restored = SettingsManager(path).settings
    assert restored["hf_token"] == expected
    assert public_settings_snapshot(restored)["hf_token"] == ("***" if expected else "")
    assert restored["hf_token"] == expected


def test_download_token_is_not_persisted_in_portable_projects(tmp_path):
    store = AudiobookStore(tmp_path / "books.sqlite3")
    settings = {"hf_token": "hf_project_secret_test", "ui_language": "es"}
    book = store.create_audiobook(
        "Hello", {}, tmp_path / "exports", "safe_chunks", "single",
        "Token test", settings, tmp_path / "project",
    )

    def check(saved):
        assert "hf_token" not in json.loads(saved.project_settings_json)
        manifest = saved.project_dir / PROJECT_MANIFEST_NAME
        assert "hf_project_secret_test" not in manifest.read_text(encoding="utf-8")

    check(book)
    check(store.update_project_settings(book.id, settings))
    check(store.save_audiobook_project(
        book.id, "Updated", {}, tmp_path / "exports", "safe_chunks", "single",
        "Token test", settings,
    ))
    # An imported project must not carry download authentication to another PC.
    manifest = book.project_dir / PROJECT_MANIFEST_NAME
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["project_settings"]["hf_token"] = settings["hf_token"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    imported = AudiobookStore(tmp_path / "imported.sqlite3").import_project_manifest(manifest)
    check(imported)
    assert settings["hf_token"] == "hf_project_secret_test"


def test_advanced_settings_token_is_masked_saved_restored_and_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit
    from app.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("app.utils.huggingface_auth.application_root", lambda: tmp_path)
    manager = SettingsManager(tmp_path / "config.json")
    monkeypatch.setattr("app.ui.main_window.SettingsManager", lambda: manager)
    monkeypatch.setattr("app.ui.main_window.QTimer.singleShot", lambda *args: None)
    monkeypatch.setattr(MainWindow, "_refresh_wav_cache_stats", lambda *args, **kwargs: None)
    window = MainWindow()
    try:
        assert window.hf_token_edit.echoMode() == QLineEdit.EchoMode.Password
        assert window.hf_token_edit.text() == ""
        window.hf_token_edit.setText("  hf_ui_test  ")
        window.hf_token_edit.editingFinished.emit()
        assert SettingsManager(manager.path).settings["hf_token"] == "hf_ui_test"
        assert "hf_token" not in window._project_settings_snapshot()
        assert window.chatterbox_manager.runtime_environment()["HF_TOKEN"] == "hf_ui_test"
        window.hf_token_edit.clear()
        window._restore_settings()
        assert window.hf_token_edit.text() == "hf_ui_test"
        window.hf_token_edit.clear()
        window.hf_token_edit.editingFinished.emit()
        assert SettingsManager(manager.path).settings["hf_token"] == ""
    finally:
        window.deleteLater()
        application.sendPostedEvents()

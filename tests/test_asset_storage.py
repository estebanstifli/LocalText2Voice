from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.asset_storage import (
    AssetStorageCancelled,
    AssetStorageError,
    AssetStorageManager,
)
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.python_runtime_manager import PythonRuntimeManager
from app.tts.russian_normalization_manager import RussianNormalizationManager
from app.utils.paths import (
    engine_dependencies_root,
    large_assets_root,
    python_runtime_root,
    resolve_large_asset_path,
)


def test_configured_assets_base_creates_data_child(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path))

    assert large_assets_root() == tmp_path.resolve() / "data"


def test_legacy_assets_root_does_not_create_a_second_data_directory(
    monkeypatch, tmp_path
) -> None:
    install_root = tmp_path / "LocalText2Voice"
    assets_root = install_root / "data"
    voice_path = assets_root / "voice-gallery" / "imported" / "voice.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"voice")
    (assets_root / ".localtext2voice-assets.json").write_text("{}", encoding="utf-8")
    (install_root / "config.json").write_text(
        '{"storage": {"base_dir": "data"}}', encoding="utf-8"
    )

    with patch("app.utils.paths.application_root", return_value=install_root):
        assert large_assets_root() == assets_root
        assert resolve_large_asset_path(
            assets_root / "data" / "voice-gallery" / "imported" / "voice.wav"
        ) == voice_path


def test_legacy_duplicate_data_tree_is_copied_without_deleting_backup(tmp_path) -> None:
    assets_root = tmp_path / "LocalText2Voice" / "data"
    legacy_root = assets_root / "data"
    legacy_voice = legacy_root / "voice-gallery" / "imported" / "voice.wav"
    legacy_model = legacy_root / "models" / "omnivoice" / "model.bin"
    legacy_voice.parent.mkdir(parents=True)
    legacy_model.parent.mkdir(parents=True)
    legacy_voice.write_bytes(b"voice")
    legacy_model.write_bytes(b"model")

    result = AssetStorageManager.migrate_legacy_duplicate_root(assets_root)

    assert result is not None
    assert result.source_root == legacy_root.resolve()
    assert (assets_root / "voice-gallery" / "imported" / "voice.wav").read_bytes() == b"voice"
    assert (assets_root / "models" / "omnivoice" / "model.bin").read_bytes() == b"model"
    assert legacy_voice.is_file()
    assert legacy_model.is_file()
    assert AssetStorageManager.migrate_legacy_duplicate_root(assets_root) is None


def test_cancelled_legacy_migration_does_not_keep_partial_destination(tmp_path) -> None:
    assets_root = tmp_path / "LocalText2Voice" / "data"
    legacy_model = assets_root / "data" / "models" / "engine" / "model.bin"
    legacy_model.parent.mkdir(parents=True)
    legacy_model.write_bytes(b"model-data")
    cancel_token = threading.Event()

    def cancel_during_copy(_current: int, _total: int, message: str) -> None:
        if message.startswith("Migrating"):
            cancel_token.set()

    with pytest.raises(AssetStorageCancelled):
        AssetStorageManager.migrate_legacy_duplicate_root(
            assets_root,
            progress_callback=cancel_during_copy,
            cancel_token=cancel_token,
        )

    destination = assets_root / "models" / "engine" / "model.bin"
    assert not destination.exists()
    assert not destination.with_name("model.bin.migration.tmp").exists()
    assert legacy_model.read_bytes() == b"model-data"


def test_transfer_copies_known_assets_and_cleans_only_after_commit(tmp_path) -> None:
    current = tmp_path / "old-data"
    dependencies = tmp_path / "old-engine-deps"
    (current / "models" / "kokoro").mkdir(parents=True)
    (current / "models" / "kokoro" / "model.onnx").write_bytes(b"model")
    (current / "voice-gallery").mkdir(parents=True)
    (current / "voice-gallery" / "voice.wav").write_bytes(b"voice")
    (dependencies / "qwen").mkdir(parents=True)
    (dependencies / "qwen" / "package.pyd").write_bytes(b"dependency")

    manager = AssetStorageManager(current, dependencies)
    result = manager.transfer(tmp_path / "new-home")

    assert result.assets_root == (tmp_path / "new-home" / "data").resolve()
    assert (result.assets_root / "models" / "kokoro" / "model.onnx").is_file()
    assert (result.assets_root / "voice-gallery" / "voice.wav").is_file()
    assert (result.assets_root / "engine-deps" / "qwen" / "package.pyd").is_file()
    assert (result.assets_root / ".localtext2voice-assets.json").is_file()
    assert (current / "models" / "kokoro" / "model.onnx").is_file()
    assert (dependencies / "qwen" / "package.pyd").is_file()

    manager.cleanup_sources(result)

    assert not (current / "models").exists()
    assert not (current / "voice-gallery").exists()
    assert not dependencies.exists()


def test_transfer_rejects_non_empty_destination_without_touching_source(tmp_path) -> None:
    current = tmp_path / "old-data"
    (current / "models").mkdir(parents=True)
    source_file = current / "models" / "model.bin"
    source_file.write_bytes(b"model")
    destination = tmp_path / "new-home" / "data"
    destination.mkdir(parents=True)
    (destination / "unrelated.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(AssetStorageError, match="not empty"):
        AssetStorageManager(current, tmp_path / "missing-deps").transfer(
            tmp_path / "new-home"
        )

    assert source_file.is_file()
    assert (destination / "unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_external_install_keeps_runtime_and_model_assets_together(tmp_path) -> None:
    install_root = tmp_path / "LocalText2Voice-on-another-drive"
    install_root.mkdir()
    (install_root / "config.json").write_text(
        '{"storage": {"base_dir": "."}}',
        encoding="utf-8",
    )

    with (
        patch("app.utils.paths.application_root", return_value=install_root),
        patch(
            "app.tts.python_runtime_manager.application_root",
            return_value=install_root,
        ),
    ):
        assets_root = install_root / "data"
        runtime = PythonRuntimeManager()
        omnivoice = OmniVoiceManager()
        russian_normalization = RussianNormalizationManager()

        assert large_assets_root() == assets_root
        assert python_runtime_root() == assets_root / "runtimes" / "python311"
        assert runtime.runtime_dir == assets_root / "runtimes" / "python311"
        assert engine_dependencies_root() == assets_root / "engine-deps"
        assert omnivoice.install_dir == assets_root / "models" / "omnivoice"
        assert (
            russian_normalization.install_dir
            == assets_root / "models" / "silero-stress"
        )
        assert (
            omnivoice.dependency_dir
            == assets_root / "engine-deps" / "omnivoice" / "site-packages"
        )

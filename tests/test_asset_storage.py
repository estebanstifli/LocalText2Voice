from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.asset_storage import AssetStorageError, AssetStorageManager
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.python_runtime_manager import PythonRuntimeManager
from app.tts.russian_normalization_manager import RussianNormalizationManager
from app.utils.paths import (
    engine_dependencies_root,
    large_assets_root,
    python_runtime_root,
)


def test_configured_assets_base_creates_data_child(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path))

    assert large_assets_root() == tmp_path.resolve() / "data"


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

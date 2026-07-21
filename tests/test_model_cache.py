from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.model_cache import (
    huggingface_model_is_cached,
    huggingface_repo_cache_name,
)
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.qwen_manager import QwenManager
from app.verification.faster_whisper_manager import FasterWhisperManager


def create_snapshot(
    cache_dir: Path,
    repo_id: str,
    files: dict[str, bytes],
    *,
    layout: str = "hub",
) -> Path:
    snapshot = (
        cache_dir
        / layout
        / huggingface_repo_cache_name(repo_id)
        / "snapshots"
        / "revision"
    )
    for relative_path, contents in files.items():
        target = snapshot / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    return snapshot


class ModelCacheDiscoveryTests(unittest.TestCase):
    def test_huggingface_cache_is_found_in_both_supported_layouts(self) -> None:
        for layout in ("hub", "models"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as name:
                cache_dir = Path(name) / "hf-cache"
                create_snapshot(
                    cache_dir,
                    "Example/VoiceModel",
                    {"config.json": b"{}", "model.bin": b"weights"},
                    layout=layout,
                )

                self.assertTrue(
                    huggingface_model_is_cached(
                        cache_dir,
                        "Example/VoiceModel",
                        {"config.json": 1, "model.bin": 1},
                    )
                )

    def test_incomplete_snapshot_is_not_reported_as_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            cache_dir = Path(name) / "hf-cache"
            create_snapshot(
                cache_dir,
                "Example/VoiceModel",
                {"config.json": b"{}"},
            )

            self.assertFalse(
                huggingface_model_is_cached(
                    cache_dir,
                    "Example/VoiceModel",
                    {"config.json": 1, "model.bin": 1},
                )
            )

    def test_tts_managers_use_physical_models_without_install_manifests(self) -> None:
        cases = (
            (QwenManager, QwenManager.MODEL_REPO, "hub"),
            (OmniVoiceManager, OmniVoiceManager.MODEL_REPO, "hub"),
            (FasterWhisperManager, FasterWhisperManager.MODEL_REPO, "models"),
        )
        for manager_class, repo_id, layout in cases:
            with (
                self.subTest(manager=manager_class.__name__),
                tempfile.TemporaryDirectory() as name,
            ):
                manager = manager_class(install_dir=Path(name) / "engine")
                manager.MODEL_REQUIRED_FILES = {
                    "config.json": 1,
                    "model.bin": 1,
                }
                create_snapshot(
                    manager.cache_dir,
                    repo_id,
                    {"config.json": b"{}", "model.bin": b"weights"},
                    layout=layout,
                )

                self.assertFalse(manager.manifest_path.is_file())
                self.assertTrue(manager.has_model_files())
                if isinstance(manager, FasterWhisperManager):
                    manager.cli_path.parent.mkdir(parents=True, exist_ok=True)
                    manager.cli_path.write_text("# worker", encoding="utf-8")
                with patch.object(manager, "has_runtime", return_value=True):
                    self.assertTrue(manager.is_installed())

    def test_chatterbox_detects_any_complete_supported_model(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            manager = ChatterboxManager(install_dir=Path(name) / "chatterbox")
            manager.MODEL_REPOSITORIES = {
                "multilingual_v3": (
                    "ResembleAI/chatterbox",
                    {"model.safetensors": 1, "tokenizer.json": 1},
                ),
                "turbo": (
                    "ResembleAI/chatterbox-turbo",
                    {"turbo.safetensors": 1},
                ),
            }
            create_snapshot(
                manager.cache_dir,
                "ResembleAI/chatterbox",
                {
                    "model.safetensors": b"weights",
                    "tokenizer.json": b"{}",
                },
            )

            self.assertTrue(manager.has_model_files())
            self.assertTrue(manager.has_model_files("multilingual_v3"))
            self.assertFalse(manager.has_model_files("turbo"))
            with patch.object(manager, "has_runtime", return_value=True):
                self.assertTrue(manager.is_installed())


if __name__ == "__main__":
    unittest.main()

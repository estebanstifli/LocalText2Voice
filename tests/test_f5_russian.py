from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.settings_manager import DEFAULT_SETTINGS
from app.tts.base import TTSEngineError
from app.tts.engine_registry import create_tts_engine, engine_ids
from app.tts.f5_russian_engine import F5RussianTTSEngine
from app.tts.f5_russian_manager import F5_RUSSIAN_PYTHON_CLI, F5RussianManager


class _Runtime:
    def __init__(self, root: Path) -> None:
        self.runtime_dir = root / "python"
        self.python_exe = self.runtime_dir / "python.exe"
        self.python_exe.parent.mkdir(parents=True)
        self.python_exe.write_bytes(b"python")

    def is_installed(self) -> bool:
        return True

    def cancel(self) -> None:
        pass


def test_f5_russian_is_optional_and_pinned() -> None:
    assert "f5_russian" in engine_ids()
    assert DEFAULT_SETTINGS["f5_russian"]["use_stress"] is True
    assert DEFAULT_SETTINGS["f5_russian"]["remove_silence"] is True
    assert DEFAULT_SETTINGS["f5_russian"]["model"] == "f5tts_v1_base_v2"
    assert "f5_russian" in DEFAULT_SETTINGS["engine_chunk_sizes"]
    assert F5RussianManager.CHECKPOINT_FILE.endswith(
        "F5TTS_v1_Base_v2/model_last_inference.safetensors"
    )
    assert "silero-stress==1.4" in F5RussianManager.SUPPORT_REQUIREMENTS
    assert "load_accentor()" in F5_RUSSIAN_PYTHON_CLI
    assert "def get_accentor" in F5_RUSSIAN_PYTHON_CLI
    assert 'request.get("stress_text_preprocessed", False)' in F5_RUSSIAN_PYTHON_CLI
    assert 'request.get("remove_silence", True)' in F5_RUSSIAN_PYTHON_CLI


def test_f5_runtime_requires_both_f5_and_silero(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path)
    manager = F5RussianManager(
        install_dir=tmp_path / "model",
        python_runtime=runtime,  # type: ignore[arg-type]
        dependencies_root=tmp_path / "deps",
    )
    manager._write_cli()
    (manager.dependency_dir / "f5_tts").mkdir(parents=True)
    manager.runtime_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manager.runtime_manifest_path.write_text(
        json.dumps(
            {
                "state": "installed",
                "runtime_version": manager.RUNTIME_VERSION,
            }
        ),
        encoding="utf-8",
    )

    assert manager.has_runtime() is False
    (manager.dependency_dir / "silero_stress").mkdir()
    assert manager.has_runtime() is True


def test_f5_voice_validation_requires_audio_and_exact_transcript(
    tmp_path: Path,
) -> None:
    manager = F5RussianManager(install_dir=tmp_path / "model")
    manager.is_installed = lambda: True  # type: ignore[method-assign]
    engine = F5RussianTTSEngine(manager)
    audio = tmp_path / "reference.wav"
    audio.write_bytes(b"RIFF")
    config = {
        "reference_audio_path": str(audio),
        "reference_text": "Точная расшифровка.",
        "device": "auto",
    }

    engine.validate(config)
    with pytest.raises(TTSEngineError, match="exact transcript"):
        engine.validate({**config, "reference_text": ""})


def test_registry_creates_f5_engine(tmp_path: Path) -> None:
    engine = create_tts_engine("f5_russian", tmp_path / "piper")
    assert isinstance(engine, F5RussianTTSEngine)

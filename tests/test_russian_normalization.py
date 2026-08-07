from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
from app.core.text_normalization import (
    normalize_text_for_speech,
    strip_russian_stress_marks,
)
from app.tts.russian_normalization_manager import (
    RussianNormalizationError,
    RussianNormalizationManager,
)
from app.utils.paths import models_root


class _FakeRussianManager:
    def __init__(self, result: str) -> None:
        self.result = result
        self.inputs: list[str] = []

    def accentuate(self, text: str) -> str:
        self.inputs.append(text)
        return self.result


class _NoopEngine:
    def cancel_current(self) -> None:
        pass


def test_russian_silero_stage_runs_after_dictionaries_and_keeps_markup() -> None:
    manager = _FakeRussianManager("Л+ёва чит+ает. {{pause 250}} +ёлка растёт.")

    result = normalize_text_for_speech(
        "Лева читает. {{pause 250}} елка растет.",
        enabled=True,
        language="ru",
        russian_silero_enabled=True,
        engine_id="piper",
        russian_manager=manager,
    )

    assert manager.inputs == ["Лева читает. {{pause 250}} елка растет."]
    assert result.text == "Лёва читает. {{pause 250}} ёлка растёт."
    assert result.language == "ru"
    assert result.russian_silero_applied is True


def test_only_f5_receives_silero_stress_markers() -> None:
    generic_manager = _FakeRussianManager("Л+ёва: 2+2, +ёлка.")
    generic = normalize_text_for_speech(
        "Лева: 2+2, елка.",
        enabled=True,
        language="ru",
        russian_silero_enabled=True,
        engine_id="qwen",
        russian_manager=generic_manager,
    )
    assert generic.text == "Лёва: 2+2, ёлка."

    f5_manager = _FakeRussianManager("Л+ёва: 2+2, +ёлка.")
    f5 = normalize_text_for_speech(
        "Лева: 2+2, елка.",
        enabled=True,
        language="ru",
        russian_silero_enabled=True,
        engine_id="f5_russian",
        russian_manager=f5_manager,
    )
    assert f5.text == "Л+ёва: 2+2, +ёлка."


def test_stripping_russian_stress_marks_keeps_ordinary_plus_signs() -> None:
    assert strip_russian_stress_marks("Л+ёва: 2+2, +ёлка.") == "Лёва: 2+2, ёлка."


def test_silero_is_not_called_for_non_russian_normalization() -> None:
    manager = _FakeRussianManager("unexpected")

    result = normalize_text_for_speech(
        "Dr. Smith has 2 GPUs.",
        enabled=True,
        language="en",
        russian_silero_enabled=True,
        engine_id="piper",
        russian_manager=manager,
    )

    assert manager.inputs == []
    assert result.russian_silero_applied is False


def test_russian_manager_requires_an_explicit_install(tmp_path: Path) -> None:
    manager = RussianNormalizationManager(install_root=tmp_path / "models")

    with pytest.raises(RussianNormalizationError, match="not installed"):
        manager.accentuate("Лева")


def test_russian_manager_uses_the_configured_tts_models_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path))

    manager = RussianNormalizationManager()

    assert manager.install_dir == models_root() / "silero-stress"
    assert manager.dependency_dir == models_root() / "silero-stress" / "site-packages"


def test_install_migrates_legacy_silero_files_to_models_folder(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "data" / "engine-deps"
    models = tmp_path / "data" / "models"
    legacy_dir = legacy_root / "russian-normalizer" / "site-packages"
    (legacy_dir / "torch").mkdir(parents=True)
    (legacy_dir / "silero_stress").mkdir()
    (legacy_root / "russian-normalization-install.json").write_text(
        '{"state": "installed", "version": "russian-normalization-v1"}',
        encoding="utf-8",
    )

    class _InstalledRuntime:
        python_exe = tmp_path / "python.exe"

        def is_installed(self) -> bool:
            return True

    with (
        patch(
            "app.tts.russian_normalization_manager.engine_dependencies_root",
            return_value=legacy_root,
        ),
        patch(
            "app.tts.russian_normalization_manager.models_root",
            return_value=models,
        ),
        patch.object(RussianNormalizationManager, "_validate_runtime"),
    ):
        manager = RussianNormalizationManager(python_runtime=_InstalledRuntime())  # type: ignore[arg-type]
        installed_path = manager.install()

    assert installed_path == models / "silero-stress" / "site-packages"
    assert (installed_path / "torch").is_dir()
    assert (installed_path / "silero_stress").is_dir()
    assert not (legacy_root / "russian-normalizer").exists()
    assert not (legacy_root / "russian-normalization-install.json").exists()
    assert manager.manifest_path.is_file()


def test_pipeline_marks_f5_text_as_preprocessed_to_avoid_double_silero(
    tmp_path: Path,
) -> None:
    manager = _FakeRussianManager("Л+ёва.")
    options = AudioGenerationOptions(
        output_dir=tmp_path,
        voice_config={"engine": "f5_russian", "language": "ru"},
        ffmpeg_path="ffmpeg",
        text_normalization_enabled=True,
        text_normalization_language="ru",
        russian_silero_enabled=True,
    )

    with patch(
        "app.tts.russian_normalization_manager.RussianNormalizationManager",
        return_value=manager,
    ):
        groups = AudioPipeline(_NoopEngine())._prepare_groups("Лева.", options)  # type: ignore[arg-type]

    assert groups[0].chunks[0].text == "Л+ёва."
    assert options.voice_config["russian_silero_preprocessed"] is True

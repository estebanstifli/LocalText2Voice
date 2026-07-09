from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VoiceInfo:
    voice_id: str
    display_name: str
    language: str
    model_path: Path
    config_path: Path

    def as_config(self, speed: float = 1.0) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "voice": self.display_name,
            "language": self.language,
            "model_path": str(self.model_path),
            "config_path": str(self.config_path),
            "speed": speed,
        }


class VoiceManager:
    def __init__(self, voices_root: Path) -> None:
        self.voices_root = voices_root

    def discover(self) -> list[VoiceInfo]:
        if not self.voices_root.is_dir():
            return []

        voices: list[VoiceInfo] = []
        for model_path in sorted(self.voices_root.rglob("*.onnx")):
            config_path = Path(f"{model_path}.json")
            if not config_path.is_file():
                continue

            relative_model = model_path.relative_to(self.voices_root)
            language = self._detect_language(config_path, model_path.parent.name)
            display_name = self._detect_name(config_path, model_path.stem)
            voices.append(
                VoiceInfo(
                    voice_id=relative_model.as_posix(),
                    display_name=f"{language} - {display_name}",
                    language=language,
                    model_path=model_path,
                    config_path=config_path,
                )
            )
        return voices

    @staticmethod
    def _read_config(config_path: Path) -> dict[str, Any]:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _detect_language(cls, config_path: Path, fallback: str) -> str:
        data = cls._read_config(config_path)
        language = data.get("language")
        if isinstance(language, dict):
            return str(language.get("code") or language.get("name") or fallback)
        if language:
            return str(language)
        return fallback

    @classmethod
    def _detect_name(cls, config_path: Path, fallback: str) -> str:
        data = cls._read_config(config_path)
        dataset = data.get("dataset")
        quality = data.get("audio", {}).get("quality") if isinstance(data.get("audio"), dict) else None
        if dataset and quality:
            return f"{dataset} ({quality})"
        return str(dataset or fallback)

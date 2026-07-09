from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import resource_root


@dataclass(frozen=True)
class LocaleInfo:
    code: str
    name: str
    direction: str = "ltr"


class Translator:
    """Small JSON-backed translator that can later be replaced by Qt translations."""

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self._messages: dict[str, str] = {}
        self.direction = "ltr"
        self._load()

    @staticmethod
    def _read_locale(path: Path) -> dict[str, str]:
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _load(self) -> None:
        locales_root = resource_root() / "locales"
        fallback = self._read_locale(locales_root / "en.json")
        selected = self._read_locale(locales_root / f"{self.language}.json")
        if not selected:
            self.language = "en"
            selected = fallback
        self._messages = {**fallback, **selected}
        self.direction = self._messages.get("language_direction", "ltr")

    @classmethod
    def available_languages(cls) -> list[LocaleInfo]:
        locales_root = resource_root() / "locales"
        languages: list[LocaleInfo] = []
        for path in sorted(locales_root.glob("*.json")):
            messages = cls._read_locale(path)
            if not messages:
                continue
            languages.append(
                LocaleInfo(
                    code=path.stem,
                    name=messages.get("language_name", path.stem),
                    direction=messages.get("language_direction", "ltr"),
                )
            )
        return languages

    def set_language(self, language: str) -> None:
        self.language = language
        self._load()

    def text(self, key: str, default: str | None = None, **values: object) -> str:
        message = self._messages.get(key, default if default is not None else key)
        try:
            return message.format(**values)
        except (KeyError, ValueError):
            return message

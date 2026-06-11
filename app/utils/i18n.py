from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import resource_root


class Translator:
    """Small JSON-backed translator that can later be replaced by Qt translations."""

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self._messages: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        locale_path = resource_root() / "locales" / f"{self.language}.json"
        try:
            data: Any = json.loads(locale_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._messages = {
                    str(key): str(value) for key, value in data.items()
                }
        except (OSError, json.JSONDecodeError):
            self._messages = {}

    def set_language(self, language: str) -> None:
        self.language = language
        self._load()

    def text(self, key: str, default: str | None = None, **values: object) -> str:
        message = self._messages.get(key, default if default is not None else key)
        try:
            return message.format(**values)
        except (KeyError, ValueError):
            return message

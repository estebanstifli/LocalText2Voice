from __future__ import annotations

import re
from typing import Any

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from app.core.video_storyboard_prompt_entities import storyboard_prompt_entities


class VideoStoryboardPromptHighlighter(QSyntaxHighlighter):
    """Highlight UI-only character and location mentions in editable prompts."""

    def __init__(self, document, plan: dict[str, Any] | None = None) -> None:
        super().__init__(document)
        self._character_patterns: list[re.Pattern[str]] = []
        self._location_patterns: list[re.Pattern[str]] = []
        self._character_format = self._format("#6d28d9")
        self._location_format = self._format("#0f766e")
        self.set_plan(plan or {})

    def set_plan(self, plan: dict[str, Any]) -> None:
        self._character_patterns = self._patterns(plan, "characters")
        self._location_patterns = self._patterns(plan, "locations")
        self.rehighlight()

    @staticmethod
    def _patterns(
        plan: dict[str, Any],
        collection: str,
    ) -> list[re.Pattern[str]]:
        names = {
            str(value.get("name") or "").strip()
            for value in storyboard_prompt_entities(plan, collection)
            if str(value.get("name") or "").strip()
        }
        return [
            re.compile(rf"(?<!\w)@{re.escape(name)}(?!\w)", re.IGNORECASE)
            for name in sorted(names, key=len, reverse=True)
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API
        for pattern in self._location_patterns:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self._location_format)
        for pattern in self._character_patterns:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self._character_format)

    @staticmethod
    def _format(color: str) -> QTextCharFormat:
        value = QTextCharFormat()
        value.setForeground(QColor(color))
        value.setFontWeight(QFont.Weight.DemiBold)
        return value

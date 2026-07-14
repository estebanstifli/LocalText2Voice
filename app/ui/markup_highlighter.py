from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


class LTVMarkupHighlighter(QSyntaxHighlighter):
    """Soft syntax highlighting for LocalText2Voice source text commands."""

    COMMAND_PATTERN = re.compile(r"\{\{\s*([A-Za-z]+)(?:\.[A-Za-z]+)?(?:\s+.*?)?\}\}")
    OPENING_PATTERN = re.compile(r"\{\{")
    STRING_PATTERN = re.compile(r'"[^"\n]*"')
    BRACKET_COMMAND_PATTERN = re.compile(r"(?<!\[)\[[A-Za-z][A-Za-z0-9_.:\- ]{0,79}\](?!\])")
    KNOWN_COMMANDS = {
        "alias",
        "chapter",
        "cmd",
        "lang",
        "mark",
        "pause",
        "play",
        "preset",
        "reset",
        "sendcomand",
        "sendcommand",
        "speed",
        "stop",
        "voice",
        "volume",
    }

    COMMAND_COLORS = {
        "alias": "#7c3aed",
        "chapter": "#2563eb",
        "cmd": "#c2410c",
        "lang": "#0f766e",
        "mark": "#4b5563",
        "pause": "#92400e",
        "play": "#047857",
        "preset": "#b45309",
        "reset": "#64748b",
        "sendcomand": "#c2410c",
        "sendcommand": "#c2410c",
        "speed": "#7c2d12",
        "stop": "#be123c",
        "voice": "#6d28d9",
        "volume": "#0369a1",
    }

    def __init__(self, document) -> None:
        super().__init__(document)
        self._enabled = True
        self._corrector_enabled = True
        self._delimiter_format = self._format("#9a3412")
        self._delimiter_format.setBackground(QColor("#fff7ed"))
        self._string_format = self._format("#475569")
        self._string_format.setFontItalic(True)
        self._bracket_format = self._format("#166534")
        self._bracket_format.setBackground(QColor("#f0fdf4"))
        self._bracket_format.setFontWeight(QFont.Weight.DemiBold)
        self._error_format = self._format("#b91c1c")
        self._error_format.setBackground(QColor("#fef2f2"))
        self._error_format.setUnderlineColor(QColor("#dc2626"))
        self._error_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.rehighlight()

    def set_corrector_enabled(self, enabled: bool) -> None:
        self._corrector_enabled = enabled
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API name
        self.setCurrentBlockState(0)
        if not self._enabled:
            return

        for match in self.COMMAND_PATTERN.finditer(text):
            start, end = match.span()
            self.setFormat(start, end - start, self._delimiter_format)

            command = match.group(1)
            command_start = match.start(1)
            command_format = self._command_format(command)
            self.setFormat(command_start, len(command), command_format)
            if command.casefold() not in self.KNOWN_COMMANDS:
                self.setFormat(start, end - start, self._error_format)

            for string_match in self.STRING_PATTERN.finditer(match.group(0)):
                string_start = start + string_match.start()
                self.setFormat(
                    string_start,
                    string_match.end() - string_match.start(),
                    self._string_format,
                )

        for match in self.BRACKET_COMMAND_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._bracket_format)

        if self._corrector_enabled:
            self._highlight_unclosed_commands(text)

    def _highlight_unclosed_commands(self, text: str) -> None:
        cursor = 0
        if self.previousBlockState() == 1:
            closing = text.find("}}")
            if closing < 0:
                self.setCurrentBlockState(1)
                if not self.currentBlock().next().isValid():
                    self.setFormat(0, len(text), self._error_format)
                return
            cursor = closing + 2

        while True:
            opening = text.find("{{", cursor)
            if opening < 0:
                return
            closing = text.find("}}", opening + 2)
            next_opening = text.find("{{", opening + 2)
            if closing < 0:
                self.setCurrentBlockState(1)
                if not self.currentBlock().next().isValid():
                    self.setFormat(opening, len(text) - opening, self._error_format)
                return
            if next_opening >= 0 and next_opening < closing:
                self.setFormat(next_opening, len(text) - next_opening, self._error_format)
                return
            cursor = closing + 2

    @classmethod
    def _command_format(cls, command: str) -> QTextCharFormat:
        normalized = command.casefold()
        text_format = cls._format(cls.COMMAND_COLORS.get(normalized, "#9a3412"))
        text_format.setFontWeight(QFont.Weight.Bold)
        return text_format

    @staticmethod
    def _format(color: str) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        return text_format

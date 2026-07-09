from __future__ import annotations

import difflib
import random
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class LTVMarkupEvent:
    type: str
    raw: str = ""
    value: Any = None
    args: tuple[Any, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)
    position: int = 0


@dataclass(frozen=True)
class LTVMarkupParseResult:
    events: list[LTVMarkupEvent]
    warnings: list[str]
    unknown_commands: list[str]

    @property
    def commands_detected(self) -> list[str]:
        return [
            event.raw
            for event in self.events
            if event.type != "text" and event.raw
        ]

    @property
    def has_markup(self) -> bool:
        return any(event.type != "text" for event in self.events)


@dataclass(frozen=True)
class LTVNarrationSegment:
    text: str
    pause_before_ms: int = 0
    pause_after_ms: int | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LTVNarrationSection:
    title: str
    segments: list[LTVNarrationSegment]


@dataclass(frozen=True)
class LTVMarkupCompileResult:
    sections: list[LTVNarrationSection]
    warnings: list[str]
    ignored_commands: list[str]
    events: list[LTVMarkupEvent]


@dataclass(frozen=True)
class LTVMarkupValidationResult:
    commands_detected: list[str]
    warnings: list[str]
    unknown_commands: list[str]
    voices_not_found: list[str]
    audio_files_not_found: list[str]
    ignored_commands: list[str]
    events: list[LTVMarkupEvent]


class LTVMarkupParser:
    _command_pattern = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
    _known_commands = {
        "alias",
        "chapter",
        "cmd",
        "emotion",
        "lang",
        "mark",
        "music",
        "pause",
        "reset",
        "sendcomand",
        "sendcommand",
        "sfx",
        "speed",
        "voice",
        "volume",
    }
    _suggestion_aliases = {"voz": "voice", "pausa": "pause", "comando": "cmd"}

    @classmethod
    def contains_markup(cls, text: str) -> bool:
        return bool(cls._command_pattern.search(text))

    @classmethod
    def parse(cls, text: str) -> LTVMarkupParseResult:
        events: list[LTVMarkupEvent] = []
        warnings: list[str] = []
        unknown_commands: list[str] = []
        cursor = 0

        for match in cls._command_pattern.finditer(text):
            if match.start() > cursor:
                events.append(
                    LTVMarkupEvent(
                        "text",
                        value=text[cursor : match.start()],
                        position=cursor,
                    )
                )
            raw_command = match.group(0)
            content = match.group(1).strip()
            parsed_event = cls._parse_command(content, raw_command, match.start())
            if parsed_event is None:
                command = cls._command_name(content)
                unknown_commands.append(command)
                suggestion = cls._suggest_command(command)
                if suggestion:
                    warnings.append(
                        f"Unknown command: {raw_command}. Did you mean "
                        f"{{{{{suggestion}}}}}?"
                    )
                else:
                    warnings.append(f"Unknown command: {raw_command}.")
            else:
                event, event_warnings = parsed_event
                events.append(event)
                warnings.extend(event_warnings)
            cursor = match.end()

        if cursor < len(text):
            events.append(LTVMarkupEvent("text", value=text[cursor:], position=cursor))

        return LTVMarkupParseResult(events, warnings, unknown_commands)

    @classmethod
    def _parse_command(
        cls,
        content: str,
        raw: str,
        position: int,
    ) -> tuple[LTVMarkupEvent, list[str]] | None:
        warnings: list[str] = []
        content = cls._normalize_command_text(content)
        if not content:
            return None
        try:
            tokens = shlex.split(content, posix=True)
        except ValueError as exc:
            return (
                LTVMarkupEvent(
                    "warning",
                    raw=raw,
                    value=str(exc),
                    position=position,
                ),
                [f"Malformed command ignored: {raw} ({exc})."],
            )
        if not tokens:
            return None

        command_token = tokens[0].lower()
        command, _, modifier = command_token.partition(".")
        args = tokens[1:]
        if command not in cls._known_commands:
            return None

        if command == "pause":
            event = cls._parse_pause(modifier, args, raw, position)
        elif command == "voice":
            event = cls._parse_voice(modifier, args, raw, position)
        elif command == "speed":
            event = cls._parse_speed(modifier, args, raw, position)
        elif command == "volume":
            event = cls._parse_volume(modifier, args, raw, position)
        elif command == "emotion":
            event = cls._parse_emotion(modifier, args, raw, position)
        elif command in {"cmd", "sendcommand", "sendcomand"}:
            event = cls._parse_model_command(args, raw, position)
        elif command == "lang":
            event = cls._parse_language(modifier, args, raw, position)
        elif command == "alias":
            event = cls._parse_alias(args, raw, position)
        elif command == "sfx":
            event = cls._parse_single_value("sfx", "file", args, raw, position)
        elif command == "music":
            event = cls._parse_music(modifier, args, raw, position)
        elif command == "chapter":
            event = cls._parse_single_value("chapter", "title", args, raw, position)
        elif command == "mark":
            event = cls._parse_single_value("mark", "name", args, raw, position)
        elif command == "reset":
            event = LTVMarkupEvent(
                "reset",
                raw=raw,
                value=modifier or "all",
                position=position,
            )
        else:
            return None

        if event.type == "warning":
            warnings.append(str(event.value))
        return event, warnings

    @classmethod
    def _parse_pause(
        cls,
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        presets = {"short": 300, "medium": 700, "long": 1200}
        if modifier in presets and not args:
            return LTVMarkupEvent("pause", raw=raw, value=presets[modifier], position=position)
        if args and args[0].lower() == "random":
            if len(args) != 3:
                return cls._warning(raw, position, "Malformed pause random command ignored.")
            minimum = cls._parse_duration_ms(args[1])
            maximum = cls._parse_duration_ms(args[2])
            if minimum is None or maximum is None or maximum < minimum:
                return cls._warning(raw, position, "Invalid pause random range ignored.")
            return LTVMarkupEvent(
                "pause",
                raw=raw,
                attrs={"random_min_ms": minimum, "random_max_ms": maximum},
                position=position,
            )
        if len(args) == 1:
            duration = cls._parse_duration_ms(args[0])
            if duration is not None:
                return LTVMarkupEvent("pause", raw=raw, value=duration, position=position)
        return cls._warning(raw, position, "Malformed pause command ignored.")

    @staticmethod
    def _parse_voice(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if modifier in {"default", "narrator"} and not args:
            return LTVMarkupEvent(
                "voice",
                raw=raw,
                value=modifier,
                attrs={"role": modifier},
                position=position,
            )
        if modifier == "character" and args:
            return LTVMarkupEvent(
                "voice",
                raw=raw,
                value=" ".join(args),
                attrs={"role": "character"},
                position=position,
            )
        if not modifier and args:
            return LTVMarkupEvent(
                "voice",
                raw=raw,
                value=" ".join(args),
                position=position,
            )
        return LTVMarkupParser._warning(raw, position, "Malformed voice command ignored.")

    @staticmethod
    def _parse_speed(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        presets = {"slow": 0.85, "normal": 1.0, "fast": 1.15}
        if modifier in presets and not args:
            return LTVMarkupEvent("speed", raw=raw, value=presets[modifier], position=position)
        if not modifier and len(args) == 1:
            try:
                value = float(args[0])
            except ValueError:
                value = 0.0
            if 0.2 <= value <= 3.0:
                return LTVMarkupEvent("speed", raw=raw, value=value, position=position)
        return LTVMarkupParser._warning(raw, position, "Malformed speed command ignored.")

    @staticmethod
    def _parse_volume(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if modifier == "normal" and not args:
            return LTVMarkupEvent("volume", raw=raw, value=0.0, position=position)
        if not modifier and len(args) == 1:
            try:
                return LTVMarkupEvent(
                    "volume",
                    raw=raw,
                    value=float(args[0]),
                    position=position,
                )
            except ValueError:
                pass
        return LTVMarkupParser._warning(raw, position, "Malformed volume command ignored.")

    @staticmethod
    def _parse_emotion(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        allowed = {"happy", "sad", "angry", "scared", "whisper", "neutral"}
        value = modifier or (args[0].lower() if len(args) == 1 else "")
        if value in allowed:
            return LTVMarkupEvent("emotion", raw=raw, value=value, position=position)
        return LTVMarkupParser._warning(raw, position, "Malformed emotion command ignored.")

    @staticmethod
    def _parse_model_command(
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if args:
            return LTVMarkupEvent(
                "model_command",
                raw=raw,
                value=" ".join(args),
                position=position,
            )
        return LTVMarkupParser._warning(raw, position, "Malformed model command ignored.")

    @staticmethod
    def _parse_language(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        value = modifier or (args[0].lower() if len(args) == 1 else "")
        if value:
            return LTVMarkupEvent("language", raw=raw, value=value, position=position)
        return LTVMarkupParser._warning(raw, position, "Malformed language command ignored.")

    @staticmethod
    def _parse_alias(args: list[str], raw: str, position: int) -> LTVMarkupEvent:
        if len(args) == 2:
            return LTVMarkupEvent(
                "alias",
                raw=raw,
                attrs={"source": args[0], "replacement": args[1]},
                position=position,
            )
        return LTVMarkupParser._warning(raw, position, "Malformed alias command ignored.")

    @staticmethod
    def _parse_single_value(
        event_type: str,
        attr_name: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if args:
            value = " ".join(args)
            return LTVMarkupEvent(
                event_type,
                raw=raw,
                value=value,
                attrs={attr_name: value},
                position=position,
            )
        return LTVMarkupParser._warning(raw, position, f"Malformed {event_type} command ignored.")

    @staticmethod
    def _parse_music(
        modifier: str,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if modifier == "stop" and not args:
            return LTVMarkupEvent("music_stop", raw=raw, position=position)
        if modifier == "volume" and len(args) == 1:
            try:
                return LTVMarkupEvent(
                    "music_volume",
                    raw=raw,
                    value=float(args[0]),
                    attrs={"db": float(args[0])},
                    position=position,
                )
            except ValueError:
                pass
        if not modifier and args:
            value = " ".join(args)
            return LTVMarkupEvent(
                "music_start",
                raw=raw,
                value=value,
                attrs={"file": value},
                position=position,
            )
        return LTVMarkupParser._warning(raw, position, "Malformed music command ignored.")

    @staticmethod
    def _parse_duration_ms(value: str) -> int | None:
        token = value.strip().lower()
        multiplier = 1.0
        if token.endswith("ms"):
            token = token[:-2]
        elif token.endswith("s"):
            token = token[:-1]
            multiplier = 1000.0
        try:
            duration = float(token) * multiplier
        except ValueError:
            return None
        if duration < 0 or duration > 300000:
            return None
        return round(duration)

    @staticmethod
    def _warning(raw: str, position: int, message: str) -> LTVMarkupEvent:
        return LTVMarkupEvent("warning", raw=raw, value=f"{message} ({raw})", position=position)

    @staticmethod
    def _command_name(content: str) -> str:
        content = LTVMarkupParser._normalize_command_text(content)
        try:
            token = shlex.split(content, posix=True)[0]
        except (IndexError, ValueError):
            token = content.split(maxsplit=1)[0] if content.strip() else ""
        return token.lower().split(".", 1)[0]

    @staticmethod
    def _normalize_command_text(content: str) -> str:
        return content.translate(
            str.maketrans(
                {
                    "\u201c": '"',
                    "\u201d": '"',
                    "\u201e": '"',
                    "\u201f": '"',
                    "\u00ab": '"',
                    "\u00bb": '"',
                    "\u2018": "'",
                    "\u2019": "'",
                    "\u201a": "'",
                    "\u201b": "'",
                    "\u2010": "-",
                    "\u2011": "-",
                    "\u2012": "-",
                    "\u2013": "-",
                    "\u2014": "-",
                    "\u2212": "-",
                }
            )
        )

    @classmethod
    def _suggest_command(cls, command: str) -> str:
        if command in cls._suggestion_aliases:
            return cls._suggestion_aliases[command]
        matches = difflib.get_close_matches(command, sorted(cls._known_commands), n=1)
        return matches[0] if matches else ""


class LTVMarkupCompiler:
    _model_command_backends = {"chatterbox", "qwen", "orpheus", "omnivoice"}
    _direct_emotion_backends = {"chatterbox", "qwen", "orpheus", "omnivoice"}

    @classmethod
    def compile(
        cls,
        text: str,
        backend: str,
        randomizer: random.Random | None = None,
    ) -> LTVMarkupCompileResult:
        parsed = LTVMarkupParser.parse(text)
        return cls.compile_events(parsed, backend, randomizer=randomizer)

    @classmethod
    def compile_events(
        cls,
        parsed: LTVMarkupParseResult,
        backend: str,
        randomizer: random.Random | None = None,
    ) -> LTVMarkupCompileResult:
        rng = randomizer or random.Random()
        warnings = list(parsed.warnings)
        ignored_commands: list[str] = []
        sections: list[LTVNarrationSection] = [
            LTVNarrationSection("Course", [])
        ]
        state = cls._default_state()
        explicit_state: set[str] = set()
        aliases: dict[str, str] = {}
        pending_pause_before_ms = 0
        buffer: list[str] = []

        def current_section() -> LTVNarrationSection:
            return sections[-1]

        def flush_text(pause_after_ms: int | None = None) -> None:
            nonlocal pending_pause_before_ms
            text_value = cls._clean_segment_text("".join(buffer))
            buffer.clear()
            if not text_value:
                if pause_after_ms:
                    pending_pause_before_ms += pause_after_ms
                return
            current_section().segments.append(
                LTVNarrationSegment(
                    text=text_value,
                    pause_before_ms=pending_pause_before_ms,
                    pause_after_ms=pause_after_ms,
                    state={
                        key: state[key]
                        for key in explicit_state
                        if key in state
                    },
                )
            )
            pending_pause_before_ms = 0

        for event in parsed.events:
            if event.type == "text":
                buffer.append(cls._apply_aliases(str(event.value or ""), aliases))
            elif event.type == "pause":
                duration = cls._pause_duration(event, rng)
                flush_text(pause_after_ms=duration)
            elif event.type == "chapter":
                flush_text()
                title = str(event.value or "Chapter").strip() or "Chapter"
                sections.append(LTVNarrationSection(title, []))
            elif event.type == "voice":
                flush_text()
                state["voice"] = event.value
                explicit_state.add("voice")
            elif event.type == "speed":
                flush_text()
                state["speed"] = event.value
                explicit_state.add("speed")
            elif event.type == "volume":
                flush_text()
                state["volume_db"] = event.value
                explicit_state.add("volume_db")
                cls._ignore_backend_command(
                    event,
                    backend,
                    ignored_commands,
                    warnings,
                    "volume commands are reserved for postproduction",
                )
            elif event.type == "emotion":
                flush_text()
                state["emotion"] = event.value
                explicit_state.add("emotion")
                if backend not in cls._direct_emotion_backends:
                    cls._ignore_backend_command(
                        event,
                        backend,
                        ignored_commands,
                        warnings,
                        "emotion is not supported by this backend yet",
                    )
            elif event.type == "model_command":
                if backend == "qwen":
                    flush_text()
                    state["model_instruction"] = event.value
                    explicit_state.add("model_instruction")
                elif backend in cls._model_command_backends:
                    buffer.append(f" {event.value} ")
                else:
                    cls._ignore_backend_command(
                        event,
                        backend,
                        ignored_commands,
                        warnings,
                        "direct model commands are disabled for this backend",
                    )
            elif event.type == "language":
                flush_text()
                state["language"] = event.value
                explicit_state.add("language")
            elif event.type == "alias":
                source = str(event.attrs.get("source", ""))
                replacement = str(event.attrs.get("replacement", ""))
                if source:
                    aliases[source] = replacement
            elif event.type in {"sfx", "music_start", "music_stop", "music_volume", "mark"}:
                flush_text()
                cls._ignore_backend_command(
                    event,
                    backend,
                    ignored_commands,
                    warnings,
                    "event recorded for future postproduction",
                )
            elif event.type == "reset":
                flush_text()
                scope = str(event.value or "all")
                cls._reset_state(state, aliases, scope)
                if scope in {"all", ""}:
                    explicit_state.clear()
                elif scope == "voice":
                    explicit_state.discard("voice")
                elif scope == "audio":
                    explicit_state.discard("volume_db")
                elif scope == "emotion":
                    explicit_state.discard("emotion")
                elif scope == "language":
                    explicit_state.discard("language")
                elif scope == "speed":
                    explicit_state.discard("speed")
                elif scope in {"cmd", "command", "instruction", "instructions"}:
                    explicit_state.discard("model_instruction")
            elif event.type == "warning":
                continue

        flush_text()
        sections = [section for section in sections if section.segments]
        return LTVMarkupCompileResult(
            sections=sections,
            warnings=warnings,
            ignored_commands=ignored_commands,
            events=parsed.events,
        )

    @classmethod
    def validate(
        cls,
        text: str,
        backend: str,
        available_voices: Iterable[str] | None = None,
        audio_roots: Iterable[Path] | None = None,
    ) -> LTVMarkupValidationResult:
        parsed = LTVMarkupParser.parse(text)
        compiled = cls.compile_events(parsed, backend)
        voices = {voice.casefold() for voice in available_voices or []}
        voices_not_found: list[str] = []
        audio_files_not_found: list[str] = []
        roots = list(audio_roots or [])
        for event in parsed.events:
            if event.type == "voice" and voices:
                value = str(event.value or "")
                if value and value.casefold() not in voices:
                    voices_not_found.append(value)
            if event.type in {"sfx", "music_start"} and roots:
                filename = str(event.value or "")
                if filename and not any((root / filename).is_file() for root in roots):
                    audio_files_not_found.append(filename)
        warnings = [
            *compiled.warnings,
            *[f"Voice not found: {voice}" for voice in voices_not_found],
            *[f"Audio file not found: {path}" for path in audio_files_not_found],
        ]
        return LTVMarkupValidationResult(
            commands_detected=parsed.commands_detected,
            warnings=warnings,
            unknown_commands=parsed.unknown_commands,
            voices_not_found=voices_not_found,
            audio_files_not_found=audio_files_not_found,
            ignored_commands=compiled.ignored_commands,
            events=parsed.events,
        )

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "voice": "default",
            "speed": 1.0,
            "volume_db": 0.0,
            "emotion": "neutral",
            "language": "auto",
            "model_instruction": "",
        }

    @staticmethod
    def _pause_duration(event: LTVMarkupEvent, rng: random.Random) -> int:
        if "random_min_ms" in event.attrs and "random_max_ms" in event.attrs:
            return rng.randint(
                int(event.attrs["random_min_ms"]),
                int(event.attrs["random_max_ms"]),
            )
        return int(event.value or 0)

    @staticmethod
    def _clean_segment_text(text: str) -> str:
        lines = [" ".join(line.split()) for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    @staticmethod
    def _apply_aliases(text: str, aliases: dict[str, str]) -> str:
        for source, replacement in aliases.items():
            text = text.replace(source, replacement)
        return text

    @staticmethod
    def _ignore_backend_command(
        event: LTVMarkupEvent,
        backend: str,
        ignored_commands: list[str],
        warnings: list[str],
        reason: str,
    ) -> None:
        if event.raw:
            ignored_commands.append(event.raw)
            warnings.append(
                f"LTV Markup command ignored for {backend}: {event.raw} ({reason})."
            )

    @classmethod
    def _reset_state(
        cls,
        state: dict[str, Any],
        aliases: dict[str, str],
        scope: str,
    ) -> None:
        defaults = cls._default_state()
        if scope in {"all", ""}:
            state.update(defaults)
            aliases.clear()
        elif scope == "voice":
            state["voice"] = defaults["voice"]
        elif scope == "audio":
            state["volume_db"] = defaults["volume_db"]
        elif scope == "emotion":
            state["emotion"] = defaults["emotion"]
        elif scope in {"cmd", "command", "instruction", "instructions"}:
            state["model_instruction"] = defaults["model_instruction"]

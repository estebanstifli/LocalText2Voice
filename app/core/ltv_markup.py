from __future__ import annotations

import difflib
import json
import math
import random
import re
import shlex
from dataclasses import dataclass, field, replace
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
    audio_events: tuple["LTVAudioEvent", ...] = ()


@dataclass(frozen=True)
class LTVAudioEvent:
    event_uid: str
    event_id: str
    command_type: str
    raw_command: str
    source_position: int
    anchor_source_word: int
    anchor_mode: str = "word_boundary"
    anchor_pause_offset_ms: int = 0
    file_reference: str = ""
    track: str = "sfx"
    source_start_ms: int = 0
    duration_ms: int | None = None
    volume_db: float = 0.0
    loop: bool = False
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    pan: float = 0.0
    duck_db: float = 0.0
    trim_silence: bool = False
    target_event_uid: str = ""
    enabled: bool = True
    warnings: tuple[str, ...] = ()
    anchor_word_adjustment: int = 0


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
    audio_events: list[LTVAudioEvent]


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
                if event.type in {"play", "stop"}:
                    left_run, right_run = cls._mid_word_runs(text, match.start(), match.end())
                    if left_run and right_run:
                        boundary = "start" if left_run <= right_run else "end"
                        warning = (
                            f"{event.type.upper()} was inside a word; aligned to the "
                            f"nearest word {boundary}: {raw_command}."
                        )
                        attrs = dict(event.attrs)
                        attrs["anchor_word_adjustment"] = -1 if boundary == "start" else 0
                        attrs["warnings"] = tuple(attrs.get("warnings", ())) + (warning,)
                        event = replace(event, attrs=attrs)
                        event_warnings.append(warning)
                events.append(event)
                warnings.extend(event_warnings)
            cursor = match.end()

        if cursor < len(text):
            events.append(LTVMarkupEvent("text", value=text[cursor:], position=cursor))

        return LTVMarkupParseResult(events, warnings, unknown_commands)

    @staticmethod
    def _mid_word_runs(text: str, start: int, end: int) -> tuple[int, int]:
        if start <= 0 or end >= len(text):
            return 0, 0
        if not (text[start - 1].isalnum() and text[end].isalnum()):
            return 0, 0
        left = 0
        cursor = start - 1
        while cursor >= 0 and (text[cursor].isalnum() or text[cursor] == "_"):
            left += 1
            cursor -= 1
        right = 0
        cursor = end
        while cursor < len(text) and (text[cursor].isalnum() or text[cursor] == "_"):
            right += 1
            cursor += 1
        return left, right

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
        command_match = re.match(
            r"^\s*([A-Za-z][\w-]*)(?:\.([A-Za-z][\w-]*))?\b(?P<body>.*)$",
            content,
            re.DOTALL,
        )
        if command_match:
            raw_command = command_match.group(1).lower()
            if raw_command in {"cmd", "sendcommand", "sendcomand", "preset"}:
                if raw_command not in cls._known_commands:
                    return None
                event = cls._parse_model_command(
                    command_match.group("body").strip(),
                    raw,
                    position,
                    "config_preset" if raw_command == "preset" else "config_override",
                )
                if event.type == "warning":
                    warnings.append(str(event.value))
                return event, warnings

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
        if command == "music" and modifier == "volume":
            warning = (
                "music.volume is reserved for a future volume-automation version "
                f"and is ignored in V1: {raw}."
            )
            return LTVMarkupEvent(
                "warning",
                raw=raw,
                value=warning,
                position=position,
            ), [warning]
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
        elif command in {"cmd", "sendcommand", "sendcomand"}:
            event = cls._parse_model_command(
                " ".join(args),
                raw,
                position,
                "config_override",
            )
        elif command == "preset":
            event = cls._parse_model_command(
                " ".join(args),
                raw,
                position,
                "config_preset",
            )
        elif command == "lang":
            event = cls._parse_language(modifier, args, raw, position)
        elif command == "alias":
            event = cls._parse_alias(args, raw, position)
        elif command == "play":
            event = cls._parse_play(args, raw, position)
        elif command == "stop":
            event = cls._parse_stop(args, raw, position)
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
        extra_warnings = event.attrs.get("warnings", ())
        if isinstance(extra_warnings, (list, tuple)):
            warnings.extend(str(item) for item in extra_warnings)
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
                value=args[0] if len(args) == 2 else " ".join(args),
                attrs={
                    "role": "character",
                    "language": args[1] if len(args) == 2 else "",
                },
                position=position,
            )
        if not modifier and args:
            return LTVMarkupEvent(
                "voice",
                raw=raw,
                value=args[0] if len(args) == 2 else " ".join(args),
                attrs={"language": args[1] if len(args) == 2 else ""},
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
            return LTVMarkupEvent(
                "volume",
                raw=raw,
                value=0.0,
                attrs={"mode": "gain_db"},
                position=position,
            )
        if modifier in {"normalize", "lufs"} and len(args) == 1:
            try:
                target = float(str(args[0]).casefold().removesuffix("lufs"))
            except ValueError:
                target = 0.0
            if -40.0 <= target <= -6.0:
                return LTVMarkupEvent(
                    "volume",
                    raw=raw,
                    value=target,
                    attrs={"mode": "normalize_lufs"},
                    position=position,
                )
        if modifier == "db" and len(args) == 1:
            try:
                return LTVMarkupEvent(
                    "volume",
                    raw=raw,
                    value=float(str(args[0]).casefold().removesuffix("db")),
                    attrs={"mode": "gain_db"},
                    position=position,
                )
            except ValueError:
                pass
        if not modifier and len(args) == 1:
            raw_value = str(args[0]).strip().casefold()
            try:
                if raw_value.endswith("db"):
                    return LTVMarkupEvent(
                        "volume",
                        raw=raw,
                        value=float(raw_value.removesuffix("db")),
                        attrs={"mode": "gain_db"},
                        position=position,
                    )
                if raw_value.endswith("%"):
                    percent = float(raw_value.removesuffix("%"))
                    if 0.0 < percent <= 400.0:
                        return LTVMarkupEvent(
                            "volume",
                            raw=raw,
                            value=20.0 * math.log10(percent / 100.0),
                            attrs={"mode": "gain_db"},
                            position=position,
                        )
                value = float(raw_value)
                if 0.0 < value <= 4.0:
                    value = 20.0 * math.log10(value)
                return LTVMarkupEvent(
                    "volume",
                    raw=raw,
                    value=value,
                    attrs={"mode": "gain_db"},
                    position=position,
                )
            except ValueError:
                pass
        return LTVMarkupParser._warning(raw, position, "Malformed volume command ignored.")

    @classmethod
    def _parse_model_command(
        cls,
        body: str,
        raw: str,
        position: int,
        event_type: str = "config_override",
    ) -> LTVMarkupEvent:
        body = body.strip()
        if body:
            overrides = cls._parse_model_command_overrides(body)
            if overrides:
                return LTVMarkupEvent(
                    event_type,
                    raw=raw,
                    value=overrides,
                    position=position,
                )
        return LTVMarkupParser._warning(raw, position, "Malformed model command ignored.")

    @staticmethod
    def _parse_model_command_overrides(body: str) -> dict[str, Any]:
        candidates = [body]
        if not body.startswith("{"):
            candidates.append("{" + body + "}")
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return {str(key): value for key, value in parsed.items()}
            if isinstance(parsed, str) and parsed.strip():
                return {"instruct": parsed.strip()}
        try:
            tokens = shlex.split(body, posix=True)
        except ValueError:
            tokens = []
        cleaned = " ".join(tokens).strip() if tokens else body.strip()
        if cleaned:
            return {"instruct": cleaned}
        return {}

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

    @classmethod
    def _parse_play(
        cls,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        if not args or "=" in args[0]:
            return cls._warning(raw, position, "PLAY requires a quoted local audio path.")

        file_reference = args[0].strip()
        if not file_reference:
            return cls._warning(raw, position, "PLAY requires a local audio path.")
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", file_reference):
            return cls._warning(raw, position, "PLAY does not support remote URLs.")

        warnings: list[str] = []
        values: dict[str, str] = {}
        known = {
            "id",
            "track",
            "start",
            "duration",
            "volume",
            "loop",
            "fade_in",
            "fade_out",
            "pan",
            "duck_on_voice",
            "trim_silence",
        }
        for token in args[1:]:
            if "=" not in token:
                warnings.append(f"Unknown PLAY argument ignored: {token} ({raw}).")
                continue
            name, value = token.split("=", 1)
            normalized_name = name.strip().casefold()
            if normalized_name not in known:
                warnings.append(
                    f"Unknown PLAY parameter ignored: {name.strip() or token} ({raw})."
                )
                continue
            if normalized_name in values:
                warnings.append(
                    f"Duplicate PLAY parameter uses the last value: {name.strip()} ({raw})."
                )
            values[normalized_name] = value.strip()

        event_id = values.get("id", "").strip()
        track = values.get("track", "sfx").strip().casefold() or "sfx"
        if track not in {"sfx", "music", "ambient"}:
            warnings.append(f"Invalid PLAY track uses sfx: {track} ({raw}).")
            track = "sfx"

        start_seconds = cls._parse_nonnegative_seconds(
            values.get("start"),
            0.0,
            "start",
            raw,
            warnings,
        )
        duration_seconds: float | None = None
        if "duration" in values:
            duration_seconds = cls._parse_positive_seconds(
                values.get("duration"),
                None,
                "duration",
                raw,
                warnings,
            )
        fade_in_seconds = cls._parse_nonnegative_seconds(
            values.get("fade_in"),
            0.0,
            "fade_in",
            raw,
            warnings,
        )
        fade_out_seconds = cls._parse_nonnegative_seconds(
            values.get("fade_out"),
            0.0,
            "fade_out",
            raw,
            warnings,
        )
        volume_db = cls._parse_play_volume(values.get("volume"), raw, warnings)
        loop = cls._parse_boolean(
            values.get("loop"),
            False,
            "loop",
            raw,
            warnings,
        )
        trim_silence = cls._parse_boolean(
            values.get("trim_silence"),
            False,
            "trim_silence",
            raw,
            warnings,
        )
        pan = cls._parse_bounded_float(
            values.get("pan"),
            0.0,
            -1.0,
            1.0,
            "pan",
            raw,
            warnings,
        )
        duck_db = cls._parse_duck_db(values.get("duck_on_voice"), raw, warnings)

        return LTVMarkupEvent(
            "play",
            raw=raw,
            value=file_reference,
            attrs={
                "file": file_reference,
                "id": event_id,
                "track": track,
                "source_start_ms": round(start_seconds * 1000),
                "duration_ms": (
                    None if duration_seconds is None else round(duration_seconds * 1000)
                ),
                "volume_db": volume_db,
                "loop": loop,
                "fade_in_ms": round(fade_in_seconds * 1000),
                "fade_out_ms": round(fade_out_seconds * 1000),
                "pan": pan,
                "duck_db": duck_db,
                "trim_silence": trim_silence,
                "warnings": tuple(warnings),
            },
            position=position,
        )

    @classmethod
    def _parse_stop(
        cls,
        args: list[str],
        raw: str,
        position: int,
    ) -> LTVMarkupEvent:
        warnings: list[str] = []
        values: dict[str, str] = {}
        for token in args:
            if "=" not in token:
                warnings.append(f"Unknown STOP argument ignored: {token} ({raw}).")
                continue
            name, value = token.split("=", 1)
            normalized_name = name.strip().casefold()
            if normalized_name not in {"id", "fade_out"}:
                warnings.append(
                    f"Unknown STOP parameter ignored: {name.strip() or token} ({raw})."
                )
                continue
            values[normalized_name] = value.strip()

        event_id = values.get("id", "").strip()
        if not event_id:
            return cls._warning(raw, position, "STOP requires id=\"...\".")
        fade_out_seconds: float | None = None
        if "fade_out" in values:
            fade_out_seconds = cls._parse_nonnegative_seconds(
                values.get("fade_out"),
                0.0,
                "fade_out",
                raw,
                warnings,
            )
        return LTVMarkupEvent(
            "stop",
            raw=raw,
            value=event_id,
            attrs={
                "id": event_id,
                "fade_out_ms": (
                    None if fade_out_seconds is None else round(fade_out_seconds * 1000)
                ),
                "warnings": tuple(warnings),
            },
            position=position,
        )

    @staticmethod
    def _parse_boolean(
        value: str | None,
        default: bool,
        name: str,
        raw: str,
        warnings: list[str],
    ) -> bool:
        if value is None:
            return default
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
        warnings.append(f"Invalid PLAY {name} uses {str(default).lower()}: {value} ({raw}).")
        return default

    @staticmethod
    def _parse_bounded_float(
        value: str | None,
        default: float,
        minimum: float,
        maximum: float,
        name: str,
        raw: str,
        warnings: list[str],
    ) -> float:
        if value is None:
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = math.nan
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            warnings.append(f"Invalid PLAY {name} uses {default}: {value} ({raw}).")
            return default
        return parsed

    @classmethod
    def _parse_nonnegative_seconds(
        cls,
        value: str | None,
        default: float,
        name: str,
        raw: str,
        warnings: list[str],
    ) -> float:
        return cls._parse_bounded_float(
            value,
            default,
            0.0,
            86400.0,
            name,
            raw,
            warnings,
        )

    @classmethod
    def _parse_positive_seconds(
        cls,
        value: str | None,
        default: float | None,
        name: str,
        raw: str,
        warnings: list[str],
    ) -> float | None:
        if value is None:
            return default
        parsed = cls._parse_bounded_float(
            value,
            -1.0,
            0.001,
            86400.0,
            name,
            raw,
            warnings,
        )
        return default if parsed < 0 else parsed

    @staticmethod
    def _parse_play_volume(
        value: str | None,
        raw: str,
        warnings: list[str],
    ) -> float:
        if value is None:
            return 0.0
        token = value.strip().casefold()
        if token.endswith("db"):
            try:
                db_value = float(token[:-2])
            except ValueError:
                db_value = math.nan
        else:
            try:
                multiplier = float(token)
            except ValueError:
                multiplier = math.nan
            db_value = 20 * math.log10(multiplier) if multiplier > 0 else math.nan
        if not math.isfinite(db_value) or not -96.0 <= db_value <= 24.0:
            warnings.append(f"Invalid PLAY volume uses original volume: {value} ({raw}).")
            return 0.0
        return db_value

    @staticmethod
    def _parse_duck_db(
        value: str | None,
        raw: str,
        warnings: list[str],
    ) -> float:
        if value is None:
            return 0.0
        token = value.strip().casefold()
        if token.endswith("db"):
            token = token[:-2]
        try:
            db_value = float(token)
        except ValueError:
            db_value = math.nan
        if not math.isfinite(db_value) or not 0.0 <= db_value <= 36.0:
            warnings.append(f"Invalid PLAY duck_on_voice uses 0 dB: {value} ({raw}).")
            return 0.0
        return db_value

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
        pending_config_overrides: dict[str, Any] = {}
        persistent_config_overrides: dict[str, Any] = {}
        buffer: list[str] = []
        pending_audio_events: list[tuple[LTVAudioEvent, int, int]] = []
        compiled_audio_events: list[LTVAudioEvent] = []
        active_audio_ids: dict[str, str] = {}
        audio_event_ordinal = 0
        generated_play_ordinal = 0

        def current_section() -> LTVNarrationSection:
            return sections[-1]

        def flush_text(pause_after_ms: int | None = None) -> None:
            nonlocal pending_pause_before_ms, pending_audio_events
            raw_text = "".join(buffer)
            text_value = cls._clean_segment_text(raw_text)
            if not text_value:
                buffer.clear()
                pending_audio_events = [
                    (audio_event, 0, pause_baseline)
                    for audio_event, _offset, pause_baseline in pending_audio_events
                ]
                if pause_after_ms:
                    pending_pause_before_ms += pause_after_ms
                return
            anchored_audio_events: list[LTVAudioEvent] = []
            for audio_event, char_offset, pause_baseline in pending_audio_events:
                prefix = cls._clean_segment_text(raw_text[:char_offset])
                anchored = replace(
                    audio_event,
                    anchor_source_word=max(
                        0,
                        cls._word_count(prefix) + audio_event.anchor_word_adjustment,
                    ),
                    anchor_pause_offset_ms=max(
                        0,
                        pending_pause_before_ms - pause_baseline,
                    ),
                )
                anchored_audio_events.append(anchored)
                compiled_audio_events.append(anchored)
            segment_state = {
                key: state[key]
                for key in explicit_state
                if key in state
            }
            if persistent_config_overrides:
                segment_state["config_overrides"] = dict(persistent_config_overrides)
            if pending_config_overrides:
                merged_overrides = dict(segment_state.get("config_overrides", {}))
                merged_overrides.update(pending_config_overrides)
                segment_state["config_overrides"] = merged_overrides
                pending_config_overrides.clear()
            current_section().segments.append(
                LTVNarrationSegment(
                    text=text_value,
                    pause_before_ms=pending_pause_before_ms,
                    pause_after_ms=pause_after_ms,
                    state=segment_state,
                    audio_events=tuple(anchored_audio_events),
                )
            )
            buffer.clear()
            pending_audio_events.clear()
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
                language = str(event.attrs.get("language", "")).strip()
                voice_value = str(event.value or "")
                if not language:
                    voice_value, language = cls._split_voice_language_suffix(voice_value)
                state["voice"] = voice_value
                if language:
                    state["voice_language"] = language
                    explicit_state.add("voice_language")
                explicit_state.add("voice")
            elif event.type == "speed":
                flush_text()
                state["speed"] = event.value
                explicit_state.add("speed")
            elif event.type == "volume":
                flush_text()
                if event.attrs.get("mode") == "normalize_lufs":
                    state["normalize_lufs"] = event.value
                    explicit_state.add("normalize_lufs")
                else:
                    state["volume_db"] = event.value
                    explicit_state.add("volume_db")
                cls._ignore_backend_command(
                    event,
                    backend,
                    ignored_commands,
                    warnings,
                    "volume commands are reserved for postproduction",
                )
            elif event.type == "config_override":
                flush_text()
                if isinstance(event.value, dict):
                    pending_config_overrides.update(event.value)
            elif event.type == "config_preset":
                flush_text()
                if isinstance(event.value, dict):
                    persistent_config_overrides.update(event.value)
            elif event.type == "language":
                flush_text()
                state["language"] = event.value
                explicit_state.add("language")
            elif event.type == "alias":
                source = str(event.attrs.get("source", ""))
                replacement = str(event.attrs.get("replacement", ""))
                if source:
                    aliases[source] = replacement
            elif event.type in {"play", "stop"}:
                audio_event_ordinal += 1
                event_uid = f"audio-event-{audio_event_ordinal:04d}"
                event_warnings = [
                    str(item) for item in event.attrs.get("warnings", ())
                ]
                enabled = True
                target_event_uid = ""
                if event.type == "play":
                    generated_play_ordinal += 1
                    event_id = str(event.attrs.get("id", "")).strip()
                    if not event_id:
                        event_id = f"play-{generated_play_ordinal:04d}"
                    identifier_key = event_id.casefold()
                    if identifier_key in active_audio_ids:
                        warning = (
                            f"Duplicate active PLAY id ignored: {event_id} ({event.raw})."
                        )
                        warnings.append(warning)
                        event_warnings.append(warning)
                        enabled = False
                    else:
                        active_audio_ids[identifier_key] = event_uid
                    audio_event = LTVAudioEvent(
                        event_uid=event_uid,
                        event_id=event_id,
                        command_type="play",
                        raw_command=event.raw,
                        source_position=event.position,
                        anchor_source_word=0,
                        file_reference=str(event.attrs.get("file", event.value or "")),
                        track=str(event.attrs.get("track", "sfx")),
                        source_start_ms=int(event.attrs.get("source_start_ms", 0) or 0),
                        duration_ms=event.attrs.get("duration_ms"),
                        volume_db=float(event.attrs.get("volume_db", 0.0) or 0.0),
                        loop=bool(event.attrs.get("loop", False)),
                        fade_in_ms=int(event.attrs.get("fade_in_ms", 0) or 0),
                        fade_out_ms=int(event.attrs.get("fade_out_ms", 0) or 0),
                        pan=float(event.attrs.get("pan", 0.0) or 0.0),
                        duck_db=float(event.attrs.get("duck_db", 0.0) or 0.0),
                        trim_silence=bool(event.attrs.get("trim_silence", False)),
                        enabled=enabled,
                        warnings=tuple(event_warnings),
                        anchor_word_adjustment=int(
                            event.attrs.get("anchor_word_adjustment", 0) or 0
                        ),
                    )
                else:
                    event_id = str(event.attrs.get("id", event.value or "")).strip()
                    identifier_key = event_id.casefold()
                    target_event_uid = active_audio_ids.pop(identifier_key, "")
                    if not target_event_uid:
                        warning = f"STOP id is not active: {event_id} ({event.raw})."
                        warnings.append(warning)
                        event_warnings.append(warning)
                        enabled = False
                    fade_out_value = event.attrs.get("fade_out_ms")
                    audio_event = LTVAudioEvent(
                        event_uid=event_uid,
                        event_id=event_id,
                        command_type="stop",
                        raw_command=event.raw,
                        source_position=event.position,
                        anchor_source_word=0,
                        fade_out_ms=(
                            -1 if fade_out_value is None else int(fade_out_value)
                        ),
                        target_event_uid=target_event_uid,
                        enabled=enabled,
                        warnings=tuple(event_warnings),
                        anchor_word_adjustment=int(
                            event.attrs.get("anchor_word_adjustment", 0) or 0
                        ),
                    )
                pending_audio_events.append(
                    (
                        audio_event,
                        len("".join(buffer)),
                        pending_pause_before_ms,
                    )
                )
            elif event.type == "mark":
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
                    pending_config_overrides.clear()
                    persistent_config_overrides.clear()
                elif scope == "voice":
                    explicit_state.discard("voice")
                    explicit_state.discard("voice_language")
                elif scope == "audio":
                    explicit_state.discard("volume_db")
                    explicit_state.discard("normalize_lufs")
                elif scope == "language":
                    explicit_state.discard("language")
                elif scope == "speed":
                    explicit_state.discard("speed")
                elif scope in {"cmd", "command", "instruction", "instructions"}:
                    pending_config_overrides.clear()
                elif scope in {"preset", "presets", "default", "defaults"}:
                    persistent_config_overrides.clear()
            elif event.type == "warning":
                continue

        flush_text()
        if pending_audio_events:
            target_section = next(
                (section for section in reversed(sections) if section.segments),
                None,
            )
            if target_section is not None:
                last_segment = target_section.segments[-1]
                trailing_events = tuple(
                    replace(
                        audio_event,
                        anchor_source_word=cls._word_count(last_segment.text),
                        anchor_mode="timeline_end",
                        anchor_pause_offset_ms=max(
                            0,
                            pending_pause_before_ms - pause_baseline,
                        ),
                    )
                    for audio_event, _offset, pause_baseline in pending_audio_events
                )
                target_section.segments[-1] = replace(
                    last_segment,
                    audio_events=last_segment.audio_events + trailing_events,
                )
                compiled_audio_events.extend(trailing_events)
            pending_audio_events.clear()
        sections = [section for section in sections if section.segments]
        return LTVMarkupCompileResult(
            sections=sections,
            warnings=warnings,
            ignored_commands=ignored_commands,
            events=parsed.events,
            audio_events=compiled_audio_events,
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
            if event.type == "play" and roots:
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
            "language": "auto",
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
    def _word_count(text: str) -> int:
        return len(re.findall(r"\w+(?:['’-]\w+)*", text, flags=re.UNICODE))

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
            state.pop("voice_language", None)
            state.pop("normalize_lufs", None)
            aliases.clear()
        elif scope == "voice":
            state["voice"] = defaults["voice"]
            state.pop("voice_language", None)
        elif scope == "audio":
            state["volume_db"] = defaults["volume_db"]
            state.pop("normalize_lufs", None)
        elif scope == "language":
            state["language"] = defaults["language"]
        elif scope == "speed":
            state["speed"] = defaults["speed"]

    @staticmethod
    def _split_voice_language_suffix(value: str) -> tuple[str, str]:
        parts = re.split(r"\s*[-\u2010-\u2015\u2212]\s*", value.strip(), maxsplit=1)
        if len(parts) != 2:
            return value.strip(), ""
        voice, language = (part.strip(" -\u2010\u2011\u2012\u2013\u2014\u2015\u2212") for part in parts)
        language_key = language.casefold()
        known_languages = {
            "en",
            "eng",
            "english",
            "es",
            "spa",
            "spanish",
            "fr",
            "fre",
            "french",
            "de",
            "ger",
            "german",
            "it",
            "ita",
            "italian",
            "pt",
            "por",
            "portuguese",
            "zh",
            "chinese",
            "ja",
            "japanese",
            "ko",
            "korean",
            "ru",
            "russian",
        }
        if voice and language_key in known_languages:
            return voice, language
        return value.strip(), ""

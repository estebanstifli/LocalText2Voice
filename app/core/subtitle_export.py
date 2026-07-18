from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_CHAPTER_STEM_RE = re.compile(
    r"^chapter_(?P<chapter>\d{3})(?:_\d+)?(?:_podcast)?$",
    re.IGNORECASE,
)
_CUE_END_RE = re.compile(r"[.!?\u2026\u3002\uff01\uff1f][\"'\u2019\u201d)\]}]*$")
_NO_SPACE_BEFORE = frozenset(",.!?;:%\u2026\u3002\uff0c\uff01\uff1f:;)]}\u00bb\u201d\u2019")
_NO_SPACE_AFTER = frozenset("([{\u00ab\u201c\u2018\u00bf\u00a1")


@dataclass(frozen=True)
class SubtitleExportResult:
    files: tuple[Path, ...] = ()
    skipped_reason: str = ""


@dataclass(frozen=True)
class TimedWord:
    text: str
    start_ms: int
    end_ms: int
    segment_sequence: int


@dataclass(frozen=True)
class SubtitleCue:
    words: tuple[TimedWord, ...]

    @property
    def start_ms(self) -> int:
        return self.words[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.words[-1].end_ms

    @property
    def text(self) -> str:
        return _join_words(word.text for word in self.words)


def export_audiobook_subtitles(
    store: Any,
    audiobook_id: int,
    output_paths: Iterable[Path] | None = None,
) -> SubtitleExportResult:
    """Create SRT and word-karaoke ASS sidecars for an audiobook's MP3 files."""
    audiobook = store.get_audiobook(audiobook_id)
    if audiobook is None:
        return SubtitleExportResult(skipped_reason="audiobook_not_found")

    segments = store.list_segments(audiobook_id)
    if not segments:
        return SubtitleExportResult(skipped_reason="no_segments")
    if any(segment.needs_rebuild for segment in segments):
        return SubtitleExportResult(skipped_reason="needs_rebuild")

    candidates = list(output_paths or store.list_audiobook_output_paths(audiobook_id))
    if not candidates:
        clean_path, mix_path = store.audiobook_output_paths(audiobook_id)
        candidates = [Path(value) for value in (clean_path, mix_path) if value]
    mp3_paths = _unique_existing_mp3_paths(candidates)
    if not mp3_paths:
        return SubtitleExportResult(skipped_reason="no_mp3_outputs")

    settings = _json_dict(audiobook.project_settings_json)
    voice_offset_ms = _integer(settings.get("voice_start_offset_ms"), 2000)
    created: list[Path] = []
    found_timestamps = False

    for mp3_path in mp3_paths:
        chapter_index = _chapter_index_for_path(mp3_path)
        selected_segments = [
            segment
            for segment in segments
            if chapter_index is None or segment.chapter_index == chapter_index
        ]
        words = _timed_words(selected_segments)
        if words:
            found_timestamps = True
        if _is_mix_path(mp3_path):
            words = _offset_words(words, voice_offset_ms)
        cues = _group_words(words)
        if not cues:
            _remove_sidecars(mp3_path)
            continue

        srt_path = mp3_path.with_suffix(".srt")
        ass_path = mp3_path.with_suffix(".ass")
        _write_atomic(srt_path, _render_srt(cues))
        _write_atomic(ass_path, _render_ass(cues))
        created.extend((srt_path, ass_path))

    if created:
        return SubtitleExportResult(files=tuple(created))
    reason = "no_word_timestamps" if not found_timestamps else "no_usable_cues"
    return SubtitleExportResult(skipped_reason=reason)


def _unique_existing_mp3_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for value in paths:
        path = Path(value)
        key = str(path.resolve()).casefold()
        if path.suffix.casefold() != ".mp3" or key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(path)
    return result


def _chapter_index_for_path(path: Path) -> int | None:
    match = _CHAPTER_STEM_RE.fullmatch(path.stem)
    if match is None:
        return None
    return int(match.group("chapter"))


def _is_mix_path(path: Path) -> bool:
    stem = path.stem.casefold()
    return stem.endswith("_mix") or stem.endswith("_podcast")


def _timed_words(segments: list[Any]) -> list[TimedWord]:
    words: list[TimedWord] = []
    cursor_ms = 0
    for segment in segments:
        before_ms = (
            segment.resolved_pause_before_ms
            if segment.resolved_pause_before_ms is not None
            else segment.markup_pause_before_ms
        )
        cursor_ms += max(0, _integer(before_ms, 0))
        duration_ms = max(0, _integer(segment.duration_ms, 0))
        for value in _json_list(segment.word_timestamps_json):
            if not isinstance(value, dict):
                continue
            text = str(value.get("word", "")).replace("\r", " ").replace("\n", " ")
            if not text.strip():
                continue
            start_seconds = _finite_float(value.get("start"))
            end_seconds = _finite_float(value.get("end"))
            if start_seconds is None or end_seconds is None:
                continue
            local_start = max(0, round(start_seconds * 1000))
            local_end = max(local_start + 10, round(end_seconds * 1000))
            if duration_ms > 0:
                if local_start >= duration_ms:
                    continue
                local_end = min(duration_ms, local_end)
            if local_end <= local_start:
                continue
            words.append(
                TimedWord(
                    text=text,
                    start_ms=cursor_ms + local_start,
                    end_ms=cursor_ms + local_end,
                    segment_sequence=segment.sequence_index,
                )
            )
        cursor_ms += duration_ms
        after_ms = (
            segment.resolved_pause_after_ms
            if segment.resolved_pause_after_ms is not None
            else segment.markup_pause_after_ms
        )
        cursor_ms += max(0, _integer(after_ms, 0))
    words.sort(key=lambda word: (word.start_ms, word.end_ms))
    return words


def _offset_words(words: list[TimedWord], offset_ms: int) -> list[TimedWord]:
    shifted: list[TimedWord] = []
    for word in words:
        end_ms = word.end_ms + offset_ms
        if end_ms <= 0:
            continue
        shifted.append(
            TimedWord(
                text=word.text,
                start_ms=max(0, word.start_ms + offset_ms),
                end_ms=end_ms,
                segment_sequence=word.segment_sequence,
            )
        )
    return shifted


def _group_words(
    words: list[TimedWord],
    max_words: int = 10,
    max_chars: int = 52,
    max_duration_ms: int = 5000,
    max_gap_ms: int = 900,
) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    current: list[TimedWord] = []
    for word in words:
        if current:
            candidate_text = _join_words(item.text for item in (*current, word))
            must_break = (
                word.segment_sequence != current[-1].segment_sequence
                or word.start_ms - current[-1].end_ms > max_gap_ms
                or len(current) >= max_words
                or len(candidate_text) > max_chars
                or word.end_ms - current[0].start_ms > max_duration_ms
            )
            if must_break:
                cues.append(SubtitleCue(tuple(current)))
                current = []
        current.append(word)
        if len(current) >= 2 and _CUE_END_RE.search(word.text.strip()):
            cues.append(SubtitleCue(tuple(current)))
            current = []
    if current:
        cues.append(SubtitleCue(tuple(current)))
    return cues


def _join_words(values: Iterable[str]) -> str:
    result = ""
    for value in values:
        raw = str(value).replace("\r", " ").replace("\n", " ")
        token = raw.strip()
        if not token:
            continue
        if not result:
            result = token
            continue
        if (
            token[0] in _NO_SPACE_BEFORE
            or result[-1] in _NO_SPACE_AFTER
            or (_is_cjk(result[-1]) and _is_cjk(token[0]))
        ):
            result += token
        else:
            result += " " + token
    return result.strip()


def _render_srt(cues: list[SubtitleCue]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_srt_time(cue.start_ms)} --> {_srt_time(cue.end_ms)}\n{cue.text}"
        )
    return "\n\n".join(blocks) + "\n"


def _render_ass(cues: list[SubtitleCue]) -> str:
    header = """[Script Info]
; Generated by LocalText2Voice
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,54,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,80,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        karaoke_parts: list[str] = []
        for index, word in enumerate(cue.words):
            next_start = (
                cue.words[index + 1].start_ms
                if index + 1 < len(cue.words)
                else cue.end_ms
            )
            duration_cs = max(1, round((next_start - word.start_ms) / 10))
            display = _ass_escape(_word_with_spacing(karaoke_parts, word.text))
            karaoke_parts.append(f"{{\\kf{duration_cs}}}{display}")
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(cue.start_ms)},{_ass_time(cue.end_ms)},"
            f"Karaoke,,0,0,0,,{''.join(karaoke_parts)}"
        )
    return header + "\n".join(events) + "\n"


def _word_with_spacing(previous_parts: list[str], value: str) -> str:
    token = value.strip()
    if not previous_parts or token[:1] in _NO_SPACE_BEFORE:
        return token
    return " " + token


def _srt_time(milliseconds: int) -> str:
    value = max(0, milliseconds)
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, round(milliseconds / 10))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cents:02d}"


def _ass_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _remove_sidecars(mp3_path: Path) -> None:
    for suffix in (".srt", ".ass"):
        mp3_path.with_suffix(suffix).unlink(missing_ok=True)


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_cjk(value: str) -> bool:
    codepoint = ord(value)
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
    )

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

from .audiobook_store import (
    AudiobookStore,
    StoredAudioEvent,
    StoredSegment,
    normalize_for_similarity,
)


@dataclass(frozen=True)
class AudioEventResolutionSummary:
    total: int
    resolved: int
    pending: int
    missing: int
    invalid: int


@dataclass(frozen=True)
class SpeechInterval:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ResolvedAudioClip:
    event_uid: str
    event_id: str
    track: str
    file_path: str
    timeline_start_ms: int
    source_start_ms: int
    playback_duration_ms: int | None
    volume_db: float
    loop: bool
    fade_in_ms: int
    fade_out_ms: int
    pan: float
    duck_db: float
    trim_silence: bool


def resolved_audio_clips(
    store: AudiobookStore,
    audiobook_id: int,
    project_duration_ms: int,
) -> list[ResolvedAudioClip]:
    events = store.list_audio_events(audiobook_id)
    stops = {
        event.target_event_uid: event
        for event in events
        if event.command_type == "stop"
        and event.enabled
        and event.resolution_status == "resolved"
        and event.resolved_time_ms is not None
        and event.target_event_uid
    }
    clips: list[ResolvedAudioClip] = []
    for event in events:
        if (
            event.command_type != "play"
            or not event.enabled
            or event.resolution_status != "resolved"
            or event.resolved_time_ms is None
            or not event.file_path
        ):
            continue
        stop = stops.get(event.event_uid)
        fade_out_ms = event.fade_out_ms
        stop_duration_ms: int | None = None
        if stop is not None and stop.resolved_time_ms is not None:
            if stop.fade_out_ms >= 0:
                fade_out_ms = stop.fade_out_ms
            stop_duration_ms = max(
                1,
                stop.resolved_time_ms - event.resolved_time_ms + fade_out_ms,
            )
        playback_duration_ms = event.duration_ms
        if stop_duration_ms is not None:
            playback_duration_ms = (
                stop_duration_ms
                if playback_duration_ms is None
                else min(playback_duration_ms, stop_duration_ms)
            )
        if event.loop and playback_duration_ms is None:
            playback_duration_ms = max(
                1,
                min(24 * 60 * 60 * 1000, project_duration_ms - event.resolved_time_ms),
            )
        clips.append(
            ResolvedAudioClip(
                event_uid=event.event_uid,
                event_id=event.event_id,
                track=event.track,
                file_path=event.file_path,
                timeline_start_ms=event.resolved_time_ms,
                source_start_ms=event.source_start_ms,
                playback_duration_ms=playback_duration_ms,
                volume_db=event.volume_db,
                loop=event.loop,
                fade_in_ms=event.fade_in_ms,
                fade_out_ms=fade_out_ms,
                pan=event.pan,
                duck_db=event.duck_db,
                trim_silence=event.trim_silence,
            )
        )
    return clips


def resolve_audio_event_timeline(
    store: AudiobookStore,
    audiobook_id: int,
) -> AudioEventResolutionSummary:
    segments = store.list_segments(audiobook_id)
    events = store.list_audio_events(audiobook_id)
    if not events:
        return AudioEventResolutionSummary(0, 0, 0, 0, 0)

    segment_starts, segment_ends, project_end_ms = _segment_clock(segments)
    segments_by_sequence = {segment.sequence_index: segment for segment in segments}
    resolutions: dict[str, tuple[int | None, str, float | None]] = {}

    for event in events:
        if not event.enabled or event.resolution_status == "invalid":
            resolutions[event.event_uid] = (None, "invalid", None)
            continue
        if event.command_type == "play" and (
            not event.file_path
            or event.resolution_status == "missing"
        ):
            resolutions[event.event_uid] = (None, "missing", None)
            continue
        segment = segments_by_sequence.get(event.anchor_segment_sequence)
        if segment is None:
            resolutions[event.event_uid] = (None, "unresolved", 0.0)
            continue
        words = _whisper_words(segment)
        if not words:
            resolutions[event.event_uid] = (None, "pending_whisper", None)
            continue

        if event.anchor_mode == "timeline_end":
            resolved_time = max(
                0,
                project_end_ms - event.anchor_pause_offset_ms,
            )
            confidence = _segment_confidence(segment)
        else:
            local_time, boundary_confidence = _resolve_word_boundary(
                segment.source_text,
                words,
                event.anchor_source_word,
            )
            if local_time is None:
                resolutions[event.event_uid] = (None, "unresolved", 0.0)
                continue
            if event.anchor_pause_offset_ms > 0 and event.anchor_source_word == 0:
                local_time = 0
            segment_start = segment_starts.get(segment.sequence_index, 0)
            resolved_time = (
                segment_start
                + local_time
                - event.anchor_pause_offset_ms
            )
            confidence = boundary_confidence * _segment_confidence(segment)
            resolved_time = min(
                max(
                    0,
                    segment_start - event.anchor_pause_offset_ms,
                    resolved_time,
                ),
                segment_ends.get(segment.sequence_index, resolved_time),
            )
        if confidence < 0.60:
            resolutions[event.event_uid] = (None, "unresolved", round(confidence, 4))
        else:
            resolutions[event.event_uid] = (
                round(resolved_time),
                "resolved",
                round(confidence, 4),
            )

    store.update_audio_event_resolutions(audiobook_id, resolutions)
    statuses = [status for _time, status, _confidence in resolutions.values()]
    return AudioEventResolutionSummary(
        total=len(events),
        resolved=statuses.count("resolved"),
        pending=statuses.count("pending_whisper") + statuses.count("unresolved"),
        missing=statuses.count("missing"),
        invalid=statuses.count("invalid"),
    )


def speech_intervals_for_audiobook(
    store: AudiobookStore,
    audiobook_id: int,
    merge_gap_ms: int = 220,
    attack_ms: int = 80,
    release_ms: int = 260,
) -> list[SpeechInterval]:
    segments = store.list_segments(audiobook_id)
    segment_starts, _segment_ends, _project_end = _segment_clock(segments)
    intervals: list[SpeechInterval] = []
    for segment in segments:
        base = segment_starts.get(segment.sequence_index, 0)
        for word in _whisper_words(segment):
            start_ms = base + round(float(word.get("start", 0.0)) * 1000)
            end_ms = base + round(float(word.get("end", 0.0)) * 1000)
            if end_ms <= start_ms:
                continue
            intervals.append(
                SpeechInterval(
                    max(0, start_ms - attack_ms),
                    end_ms + release_ms,
                )
            )
    intervals.sort(key=lambda interval: interval.start_ms)
    merged: list[SpeechInterval] = []
    for interval in intervals:
        if not merged or interval.start_ms > merged[-1].end_ms + merge_gap_ms:
            merged.append(interval)
            continue
        previous = merged[-1]
        merged[-1] = SpeechInterval(
            previous.start_ms,
            max(previous.end_ms, interval.end_ms),
        )
    return merged


def _segment_clock(
    segments: list[StoredSegment],
) -> tuple[dict[int, int], dict[int, int], int]:
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    cursor_ms = 0
    for segment in segments:
        before_ms = (
            segment.resolved_pause_before_ms
            if segment.resolved_pause_before_ms is not None
            else segment.markup_pause_before_ms
        )
        cursor_ms += max(0, int(before_ms or 0))
        starts[segment.sequence_index] = cursor_ms
        cursor_ms += max(0, segment.duration_ms)
        ends[segment.sequence_index] = cursor_ms
        after_ms = (
            segment.resolved_pause_after_ms
            if segment.resolved_pause_after_ms is not None
            else segment.markup_pause_after_ms
        )
        cursor_ms += max(0, int(after_ms or 0))
    return starts, ends, cursor_ms


def _whisper_words(segment: StoredSegment) -> list[dict[str, Any]]:
    try:
        value = json.loads(segment.word_timestamps_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _resolve_word_boundary(
    source_text: str,
    whisper_words: list[dict[str, Any]],
    source_word_before: int,
) -> tuple[int | None, float]:
    source_words = normalize_for_similarity(source_text).split()
    transcript_tokens: list[str] = []
    transcript_timings: list[tuple[int, int]] = []
    for word in whisper_words:
        normalized = normalize_for_similarity(str(word.get("word", ""))).split()
        if not normalized:
            continue
        start_ms = round(float(word.get("start", 0.0) or 0.0) * 1000)
        end_ms = round(float(word.get("end", 0.0) or 0.0) * 1000)
        for token in normalized:
            transcript_tokens.append(token)
            transcript_timings.append((start_ms, end_ms))
    if not source_words or not transcript_tokens:
        return None, 0.0

    mapping = _align_words(source_words, transcript_tokens)
    boundary = max(0, min(source_word_before, len(source_words)))
    if boundary > 0:
        for source_index in range(boundary - 1, -1, -1):
            transcript_index = mapping.get(source_index)
            if transcript_index is None:
                continue
            distance = boundary - 1 - source_index
            similarity = difflib.SequenceMatcher(
                None,
                source_words[source_index],
                transcript_tokens[transcript_index],
            ).ratio()
            confidence = max(0.55, 1.0 - distance * 0.12) * similarity
            return transcript_timings[transcript_index][1], confidence
    for source_index in range(boundary, len(source_words)):
        transcript_index = mapping.get(source_index)
        if transcript_index is None:
            continue
        distance = source_index - boundary
        similarity = difflib.SequenceMatcher(
            None,
            source_words[source_index],
            transcript_tokens[transcript_index],
        ).ratio()
        confidence = max(0.55, 1.0 - distance * 0.12) * similarity
        return transcript_timings[transcript_index][0], confidence
    return None, 0.0


def _align_words(source: list[str], transcript: list[str]) -> dict[int, int]:
    rows = len(source) + 1
    columns = len(transcript) + 1
    costs = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        costs[row][0] = row
    for column in range(columns):
        costs[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            substitution = costs[row - 1][column - 1] + (
                0 if source[row - 1] == transcript[column - 1] else 1
            )
            costs[row][column] = min(
                costs[row - 1][column] + 1,
                costs[row][column - 1] + 1,
                substitution,
            )

    mapping: dict[int, int] = {}
    row = len(source)
    column = len(transcript)
    while row > 0 or column > 0:
        if row > 0 and column > 0:
            substitution_cost = 0 if source[row - 1] == transcript[column - 1] else 1
            if costs[row][column] == costs[row - 1][column - 1] + substitution_cost:
                similarity = difflib.SequenceMatcher(
                    None,
                    source[row - 1],
                    transcript[column - 1],
                ).ratio()
                if similarity >= 0.72:
                    mapping[row - 1] = column - 1
                row -= 1
                column -= 1
                continue
        if row > 0 and costs[row][column] == costs[row - 1][column] + 1:
            row -= 1
        elif column > 0:
            column -= 1
        else:
            break
    return mapping


def _segment_confidence(segment: StoredSegment) -> float:
    if segment.similarity_score is None:
        return 0.5
    return max(0.0, min(1.0, segment.similarity_score / 100.0))

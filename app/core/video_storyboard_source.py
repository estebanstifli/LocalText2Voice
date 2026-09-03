from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.core.audiobook_store import StoredSegment


@dataclass(frozen=True)
class StoryboardNarrationCue:
    segment_id: int
    sequence_index: int
    start_seconds: float
    duration_seconds: float
    text: str
    timing_ready: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "sequence_index": self.sequence_index,
            "start_seconds": self.start_seconds,
            "end_seconds": self.start_seconds + self.duration_seconds,
            "duration_seconds": self.duration_seconds,
            "text": self.text,
            "timing_ready": self.timing_ready,
        }


def build_storyboard_narration_timeline(
    segments: Iterable[StoredSegment],
    voice_start_offset_ms: int = 0,
) -> tuple[list[StoryboardNarrationCue], float]:
    """Build the narration clock used by the storyboard without running AI."""
    cues: list[StoryboardNarrationCue] = []
    offset_ms = max(0, int(voice_start_offset_ms or 0))
    cursor_ms = offset_ms
    for segment in sorted(segments, key=lambda item: item.sequence_index):
        before_ms = (
            segment.resolved_pause_before_ms
            if segment.resolved_pause_before_ms is not None
            else segment.markup_pause_before_ms
        )
        cursor_ms += max(0, int(before_ms or 0))
        duration_ms = max(0, int(segment.duration_ms or 0))
        cues.append(
            StoryboardNarrationCue(
                segment_id=segment.id,
                sequence_index=segment.sequence_index,
                start_seconds=cursor_ms / 1000,
                duration_seconds=duration_ms / 1000,
                text=segment.source_text,
                timing_ready=duration_ms > 0,
            )
        )
        cursor_ms += duration_ms
        after_ms = (
            segment.resolved_pause_after_ms
            if segment.resolved_pause_after_ms is not None
            else segment.markup_pause_after_ms
        )
        cursor_ms += max(0, int(after_ms or 0))
    return cues, cursor_ms / 1000

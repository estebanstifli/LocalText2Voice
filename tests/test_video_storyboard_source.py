from __future__ import annotations

from app.core.audiobook_store import StoredSegment
from app.core.video_storyboard_source import (
    build_storyboard_narration_timeline,
)


def _segment(
    segment_id: int,
    sequence: int,
    text: str,
    duration_ms: int,
    *,
    before_ms: int = 0,
    after_ms: int = 0,
    resolved_before_ms: int | None = None,
) -> StoredSegment:
    return StoredSegment(
        id=segment_id,
        audiobook_id=7,
        sequence_index=sequence,
        chapter_index=0,
        chapter_title="",
        source_text=text,
        wav_path="",
        status="rendered",
        similarity_score=None,
        verification_status="",
        transcript_text="",
        duration_ms=duration_ms,
        markup_pause_before_ms=before_ms,
        markup_pause_after_ms=after_ms,
        resolved_pause_before_ms=resolved_before_ms,
    )


def test_storyboard_narration_timeline_preserves_audio_clock() -> None:
    cues, duration = build_storyboard_narration_timeline(
        [
            _segment(2, 1, "Second", 3000, resolved_before_ms=300),
            _segment(1, 0, "First", 5000, before_ms=1000, after_ms=200),
        ]
    )

    assert [cue.text for cue in cues] == ["First", "Second"]
    assert cues[0].start_seconds == 1.0
    assert cues[0].duration_seconds == 5.0
    assert cues[1].start_seconds == 6.5
    assert duration == 9.5
    assert all(cue.timing_ready for cue in cues)


def test_storyboard_narration_timeline_applies_voice_start_offset() -> None:
    cues, duration = build_storyboard_narration_timeline(
        [_segment(1, 0, "First", 5000)],
        voice_start_offset_ms=2000,
    )

    assert cues[0].start_seconds == 2.0
    assert cues[0].duration_seconds == 5.0
    assert duration == 7.0

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.audio_event_timeline import (
    resolve_audio_event_timeline,
    resolved_audio_clips,
    speech_intervals_for_audiobook,
)
from app.core.audio_pipeline import AudioGroup
from app.core.audiobook_store import PROJECT_MANIFEST_NAME, AudiobookStore
from app.core.ltv_markup import LTVMarkupCompiler
from app.core.text_processor import TextChunk


class AudioEventTimelineTests(unittest.TestCase):
    def test_event_order_around_explicit_pause_changes_global_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audio_source = root / "intro.mp3"
            audio_source.write_bytes(b"fake")
            source = (
                f'{{{{play "{audio_source.as_posix()}" id="before"}}}}'
                "{{pause 2s}}"
                f'{{{{play "{audio_source.as_posix()}" id="after"}}}}'
                "Hola."
            )
            compiled = LTVMarkupCompiler.compile(source, "piper")
            narration = compiled.sections[0].segments[0]
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                source,
                {},
                root / "output",
                "safe_chunks",
                "single",
                project_dir=root / "project",
            )
            store.replace_segments(
                audiobook,
                [
                    AudioGroup(
                        "Course",
                        (
                            TextChunk(
                                text=narration.text,
                                ends_paragraph=True,
                                markup_pause_before_ms=narration.pause_before_ms,
                                markup_audio_events=narration.audio_events,
                            ),
                        ),
                    )
                ],
            )
            segment = store.list_segments(audiobook.id)[0]
            store.mark_segment_rendered(segment.id, root / "voice.wav", 1000, 10)
            store.update_segment_verification(
                segment.id,
                "Hola.",
                100.0,
                0.0,
                0.0,
                "approved",
                10,
                json.dumps(
                    [{"word": "Hola", "start": 0.1, "end": 0.5}]
                ),
            )

            summary = resolve_audio_event_timeline(store, audiobook.id)
            events = store.list_audio_events(audiobook.id)

            self.assertEqual(summary.resolved, 2)
            self.assertEqual(events[0].resolved_time_ms, 0)
            self.assertEqual(events[1].resolved_time_ms, 2100)

    def test_whisper_alignment_resolves_play_stop_and_invalidates_after_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audio_source = root / "rain sample.mp3"
            audio_source.write_bytes(b"fake local audio")
            source = (
                "Hola "
                f'{{{{play "{audio_source.as_posix()}" id="Rain" loop=true '
                'track=ambient volume=-18db duck_on_voice=6db}}}}'
                "mundo "
                '{{stop id="rain" fade_out=0.5}}'
                "final."
            )
            compiled = LTVMarkupCompiler.compile(source, "piper")
            narration = compiled.sections[0].segments[0]
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                source,
                {"engine": "piper"},
                root / "output",
                "safe_chunks",
                "single",
                project_dir=root / "project",
            )
            store.replace_segments(
                audiobook,
                [
                    AudioGroup(
                        "Course",
                        (
                            TextChunk(
                                text=narration.text,
                                ends_paragraph=True,
                                markup_pause_before_ms=250,
                                markup_pause_after_ms=300,
                                markup_audio_events=narration.audio_events,
                            ),
                        ),
                    )
                ],
            )
            segment = store.list_segments(audiobook.id)[0]
            store.mark_segment_rendered(segment.id, root / "voice.wav", 2000, 10)
            words = [
                {"word": "Hola", "start": 0.0, "end": 0.4, "probability": 0.99},
                {"word": "mundo", "start": 0.5, "end": 0.9, "probability": 0.98},
                {"word": "final", "start": 1.0, "end": 1.4, "probability": 0.97},
            ]
            store.update_segment_verification(
                segment.id,
                "Hola mundo final.",
                100.0,
                0.0,
                0.0,
                "approved",
                20,
                json.dumps(words),
            )

            summary = resolve_audio_event_timeline(store, audiobook.id)
            events = store.list_audio_events(audiobook.id)

            self.assertEqual(summary.total, 2)
            self.assertEqual(summary.resolved, 2)
            self.assertEqual(events[0].resolved_time_ms, 650)
            self.assertEqual(events[1].resolved_time_ms, 1150)
            self.assertTrue(Path(events[0].file_path).is_file())
            self.assertEqual(Path(events[0].file_path).parent.name, "audio")

            clips = resolved_audio_clips(store, audiobook.id, 2550)
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0].timeline_start_ms, 650)
            self.assertEqual(clips[0].playback_duration_ms, 1000)
            self.assertEqual(clips[0].fade_out_ms, 500)
            self.assertEqual(clips[0].duck_db, 6.0)
            self.assertEqual(clips[0].track, "ambient")

            intervals = speech_intervals_for_audiobook(store, audiobook.id)
            self.assertEqual(intervals[0].start_ms, 170)
            self.assertGreaterEqual(intervals[-1].end_ms, 1910)

            manifest = json.loads(
                (audiobook.project_dir / PROJECT_MANIFEST_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["version"], 2)
            self.assertEqual(len(manifest["audio_events"]), 2)
            self.assertTrue(
                manifest["audio_events"][0]["file_path"].startswith("assets/audio/")
            )

            imported_store = AudiobookStore(root / "imported.sqlite3")
            imported = imported_store.import_project_manifest(
                audiobook.project_dir / PROJECT_MANIFEST_NAME
            )
            imported_events = imported_store.list_audio_events(imported.id)
            self.assertEqual(len(imported_events), 2)
            self.assertEqual(imported_events[0].resolved_time_ms, 650)
            self.assertTrue(Path(imported_events[0].file_path).is_file())

            clone = store.clone_audiobook(
                audiobook.id,
                "Clone",
                source,
                {"engine": "piper"},
                root / "clone-output",
                "safe_chunks",
                "single",
                target_project_dir=root / "clone-project",
            )
            cloned_events = store.list_audio_events(clone.id)
            self.assertEqual(len(cloned_events), 2)
            self.assertTrue(Path(cloned_events[0].file_path).is_file())
            self.assertTrue(
                Path(cloned_events[0].file_path).is_relative_to(clone.project_dir)
            )

            store.update_segment_pause(segment.id, 500, 300)
            invalidated = store.list_audio_events(audiobook.id)
            self.assertTrue(
                all(event.resolution_status == "pending_whisper" for event in invalidated)
            )
            self.assertTrue(all(event.resolved_time_ms is None for event in invalidated))

    def test_missing_word_uses_nearest_aligned_boundary(self) -> None:
        words = [
            {"word": "uno", "start": 0.0, "end": 0.3},
            {"word": "tres", "start": 0.7, "end": 1.0},
        ]
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audio_source = root / "click.wav"
            audio_source.write_bytes(b"fake")
            source = f'Uno dos {{{{play "{audio_source.as_posix()}"}}}} tres.'
            compiled = LTVMarkupCompiler.compile(source, "piper")
            narration = compiled.sections[0].segments[0]
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                source,
                {},
                root / "output",
                "safe_chunks",
                "single",
                project_dir=root / "project",
            )
            store.replace_segments(
                audiobook,
                [
                    AudioGroup(
                        "Course",
                        (
                            TextChunk(
                                text=narration.text,
                                ends_paragraph=True,
                                markup_audio_events=narration.audio_events,
                            ),
                        ),
                    )
                ],
            )
            segment = store.list_segments(audiobook.id)[0]
            store.mark_segment_rendered(segment.id, root / "voice.wav", 1200, 10)
            store.update_segment_verification(
                segment.id,
                "uno tres",
                100.0,
                0.0,
                0.0,
                "approved",
                10,
                json.dumps(words),
            )

            summary = resolve_audio_event_timeline(store, audiobook.id)
            event = store.list_audio_events(audiobook.id)[0]

            self.assertEqual(summary.resolved, 1)
            self.assertEqual(event.resolved_time_ms, 300)
            self.assertGreater(event.resolution_confidence or 0.0, 0.6)


if __name__ == "__main__":
    unittest.main()

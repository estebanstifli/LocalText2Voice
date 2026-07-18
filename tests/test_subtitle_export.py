from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.audio_pipeline import AudioGroup
from app.core.audiobook_store import AudiobookStore
from app.core.subtitle_export import export_audiobook_subtitles
from app.core.text_processor import TextChunk


class SubtitleExportTests(unittest.TestCase):
    def test_exports_srt_and_word_karaoke_ass_next_to_single_and_mix_mp3s(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            store, audiobook_id, segments = self._project(
                root,
                [
                    (
                        "Chapter 1",
                        "Hello world.",
                        [
                            {"word": "Hello", "start": 0.1, "end": 0.4},
                            {"word": " world.", "start": 0.5, "end": 0.9},
                        ],
                    ),
                    (
                        "Chapter 2",
                        "Next line.",
                        [
                            {"word": "Next", "start": 0.1, "end": 0.3},
                            {"word": " line.", "start": 0.35, "end": 0.6},
                        ],
                    ),
                ],
                voice_offset_ms=2000,
            )
            store.update_segment_pause(segments[0].id, 100, 200)
            store.update_segment_pause(segments[1].id, 0, 0)
            clean = root / "output" / "podcast1.mp3"
            mix = root / "output" / "podcast1_mix.mp3"
            self._touch_outputs(clean, mix)
            store.complete_audiobook(audiobook_id, [clean, mix])

            result = export_audiobook_subtitles(store, audiobook_id)

            self.assertEqual(
                result.files,
                (
                    clean.with_suffix(".srt"),
                    clean.with_suffix(".ass"),
                    mix.with_suffix(".srt"),
                    mix.with_suffix(".ass"),
                ),
            )
            clean_srt = clean.with_suffix(".srt").read_text(encoding="utf-8")
            mix_srt = mix.with_suffix(".srt").read_text(encoding="utf-8")
            ass = clean.with_suffix(".ass").read_text(encoding="utf-8")
            self.assertIn("00:00:00,200 --> 00:00:01,000", clean_srt)
            self.assertIn("Hello world.", clean_srt)
            self.assertIn("00:00:01,400 --> 00:00:01,900", clean_srt)
            self.assertIn("00:00:02,200 --> 00:00:03,000", mix_srt)
            self.assertIn("Style: Karaoke", ass)
            self.assertIn(r"{\kf40}Hello{\kf40} world.", ass)
            self.assertEqual(
                store.list_audiobook_output_paths(audiobook_id),
                [clean, mix],
            )

    def test_chapter_sidecars_use_chapter_local_timing_and_mix_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            store, audiobook_id, segments = self._project(
                root,
                [
                    (
                        "One",
                        "First.",
                        [{"word": "First.", "start": 0.2, "end": 0.6}],
                    ),
                    (
                        "Two",
                        "Second.",
                        [{"word": "Second.", "start": 0.1, "end": 0.5}],
                    ),
                ],
                voice_offset_ms=1500,
            )
            store.update_segment_pause(segments[0].id, 0, 900)
            store.update_segment_pause(segments[1].id, 250, 0)
            first = root / "output" / "chapter_001.mp3"
            second_mix = root / "output" / "chapter_002_podcast.mp3"
            self._touch_outputs(first, second_mix)
            store.complete_audiobook(audiobook_id, [first, second_mix])

            result = export_audiobook_subtitles(store, audiobook_id)

            self.assertEqual(len(result.files), 4)
            first_srt = first.with_suffix(".srt").read_text(encoding="utf-8")
            second_srt = second_mix.with_suffix(".srt").read_text(encoding="utf-8")
            self.assertIn("00:00:00,200 --> 00:00:00,600", first_srt)
            self.assertNotIn("Second.", first_srt)
            self.assertIn("00:00:01,850 --> 00:00:02,250", second_srt)
            self.assertNotIn("First.", second_srt)

    def test_waits_for_rebuild_instead_of_writing_desynchronized_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            store, audiobook_id, segments = self._project(
                root,
                [
                    (
                        "One",
                        "Changed.",
                        [{"word": "Changed.", "start": 0.1, "end": 0.5}],
                    )
                ],
            )
            output = root / "output" / "podcast1.mp3"
            self._touch_outputs(output)
            store.complete_audiobook(audiobook_id, [output])
            store.mark_segment_rendered(
                segments[0].id,
                root / "replacement.wav",
                1000,
                100,
            )
            store.update_segment_verification(
                segments[0].id,
                "Changed.",
                100.0,
                0.0,
                0.0,
                "approved",
                20,
                json.dumps([{"word": "Changed.", "start": 0.1, "end": 0.5}]),
            )

            result = export_audiobook_subtitles(store, audiobook_id)

            self.assertEqual(result.skipped_reason, "needs_rebuild")
            self.assertFalse(output.with_suffix(".srt").exists())
            self.assertFalse(output.with_suffix(".ass").exists())

    def _project(
        self,
        root: Path,
        definitions: list[tuple[str, str, list[dict[str, object]]]],
        voice_offset_ms: int = 0,
    ):
        store = AudiobookStore(root / "projects.sqlite3")
        audiobook = store.create_audiobook(
            source_text="\n".join(item[1] for item in definitions),
            voice_config={"engine": "fake"},
            output_dir=root / "output",
            split_mode="chapters",
            export_mode="single",
            title="Test",
            project_settings={"voice_start_offset_ms": voice_offset_ms},
            project_dir=root / "projects" / "test-project",
        )
        groups = [
            AudioGroup(
                title=title,
                chunks=(TextChunk(text=text, ends_paragraph=True),),
            )
            for title, text, _words in definitions
        ]
        store.replace_segments(audiobook, groups)
        segments = store.list_segments(audiobook.id)
        for segment, (_title, text, words) in zip(segments, definitions, strict=True):
            wav = root / f"segment_{segment.sequence_index}.wav"
            store.mark_segment_rendered(segment.id, wav, 1000, 100)
            store.update_segment_verification(
                segment.id,
                text,
                100.0,
                0.0,
                0.0,
                "approved",
                20,
                json.dumps(words),
            )
        return store, audiobook.id, store.list_segments(audiobook.id)

    @staticmethod
    def _touch_outputs(*paths: Path) -> None:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"mp3")


if __name__ == "__main__":
    unittest.main()

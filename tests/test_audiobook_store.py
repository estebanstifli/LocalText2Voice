from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.audiobook_store import (
    PROJECT_MANIFEST_NAME,
    AudiobookStore,
    normalize_for_similarity,
)
from app.core.audio_pipeline import AudioGroup
from app.core.text_processor import TextChunk
from app.core.transcript_similarity import similarity_metrics, verification_status


class AudiobookStoreTests(unittest.TestCase):
    def test_audio_assets_are_deduplicated_by_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            first = root / "first.mp3"
            second = root / "renamed.mp3"
            first.write_bytes(b"identical audio bytes")
            second.write_bytes(b"identical audio bytes")
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                "Text.",
                {},
                root / "output",
                "safe_chunks",
                "single",
                project_dir=root / "project",
            )

            first_asset, first_warning = store._embed_audio_asset(
                audiobook,
                str(first),
            )
            second_asset, second_warning = store._embed_audio_asset(
                audiobook,
                str(second),
            )

            self.assertEqual(first_warning, "")
            self.assertEqual(second_warning, "")
            self.assertEqual(first_asset, second_asset)
            self.assertEqual(len(list((audiobook.project_dir / "assets/audio").iterdir())), 1)

    def test_store_creates_audiobook_and_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                "Hello world.",
                {"engine": "piper"},
                root / "output",
                "safe_chunks",
                "single",
                "Demo",
            )
            groups = [
                AudioGroup(
                    "Course",
                    (
                        TextChunk(
                            text="Hello world.",
                            ends_paragraph=True,
                            paragraph_length=128,
                            paragraph_number=1,
                            markup_pause_before_ms=250,
                            markup_pause_after_ms=700,
                            markup_state={"voice": "Serena"},
                        ),
                    ),
                )
            ]
            mapping = store.replace_segments(audiobook, groups)

            self.assertEqual(len(mapping), 1)
            segments = store.list_segments(audiobook.id)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].source_text, "Hello world.")
            self.assertEqual(segments[0].status, "pending")
            self.assertTrue(segments[0].wav_path.endswith(".wav"))
            self.assertEqual(segments[0].paragraph_length, 128)
            self.assertTrue(segments[0].ends_paragraph)
            self.assertEqual(segments[0].markup_pause_before_ms, 250)
            self.assertEqual(segments[0].markup_pause_after_ms, 700)
            self.assertFalse(segments[0].needs_rebuild)

            wav_path = root / "segment.wav"
            store.mark_segment_rendered(segments[0].id, wav_path, 1000, 250)
            words = [
                {
                    "word": "Hello",
                    "start": 0.0,
                    "end": 0.42,
                    "probability": 0.99,
                }
            ]
            store.update_segment_verification(
                segments[0].id,
                "Hello world.",
                100.0,
                0.0,
                0.0,
                "approved",
                123,
                json.dumps(words),
            )
            rendered = store.get_segment(segments[0].id)
            self.assertIsNotNone(rendered)
            assert rendered is not None
            self.assertTrue(rendered.needs_rebuild)
            self.assertEqual(json.loads(rendered.word_timestamps_json), words)

            store.complete_audiobook(audiobook.id, [root / "output" / "podcast1.mp3"])
            clean_path, mix_path = store.audiobook_output_paths(audiobook.id)
            self.assertTrue(clean_path.endswith("podcast1.mp3"))
            self.assertEqual(mix_path, "")
            completed = store.get_segment(segments[0].id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertFalse(completed.needs_rebuild)

            store.update_segment_pause(segments[0].id, 250, 700)
            store.update_segment_text(segments[0].id, "Hello brave world.")
            updated = store.get_segment(segments[0].id)

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.source_text, "Hello brave world.")
            self.assertEqual(updated.status, "edited")
            self.assertTrue(updated.needs_rebuild)
            self.assertEqual(updated.resolved_pause_before_ms, 250)
            self.assertEqual(updated.resolved_pause_after_ms, 700)

    def test_normalization_and_similarity(self) -> None:
        self.assertEqual(
            normalize_for_similarity("¡Hola, mundo!"),
            "hola mundo",
        )
        metrics = similarity_metrics("Hello, world.", "hello world")

        self.assertGreaterEqual(metrics["similarity_score"], 99.0)
        self.assertEqual(verification_status(metrics["similarity_score"]), "approved")

    def test_project_save_and_clone_preserve_associated_segment_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            store = AudiobookStore(root / "projects.sqlite3")
            audiobook = store.create_audiobook(
                "Original text.",
                {"engine": "piper"},
                root / "output",
                "safe_chunks",
                "single",
                "Original",
                {"tts_engine": "piper"},
            )
            groups = [
                AudioGroup(
                    "Course",
                    (
                        TextChunk(
                            text="Original text.",
                            ends_paragraph=True,
                            paragraph_length=14,
                            paragraph_number=1,
                        ),
                    ),
                )
            ]
            store.replace_segments(audiobook, groups)
            segment = store.list_segments(audiobook.id)[0]
            segment_wav = Path(segment.wav_path)
            segment_wav.parent.mkdir(parents=True, exist_ok=True)
            segment_wav.write_bytes(b"fake wav")
            store.mark_segment_rendered(segment.id, segment_wav, 1000, 250)
            words = [
                {
                    "word": "Original",
                    "start": 0.0,
                    "end": 0.5,
                    "probability": 0.98,
                }
            ]
            store.update_segment_verification(
                segment.id,
                "Original text.",
                100.0,
                0.0,
                0.0,
                "approved",
                120,
                json.dumps(words),
            )

            saved = store.save_audiobook_project(
                audiobook.id,
                "Updated text.",
                {"engine": "qwen"},
                root / "new-output",
                "chapters",
                "single",
                "Updated",
                {"tts_engine": "qwen"},
            )

            self.assertEqual(saved.title, "Updated")
            self.assertEqual(saved.source_text, "Updated text.")
            self.assertEqual(saved.split_mode, "chapters")
            self.assertTrue((saved.project_dir / PROJECT_MANIFEST_NAME).is_file())
            self.assertTrue((saved.project_dir / "project.json").is_file())
            self.assertEqual((saved.project_dir / "source.txt").read_text(encoding="utf-8"), "Updated text.")
            manifest = json.loads(
                (saved.project_dir / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], "localtext2voice.project")
            self.assertEqual(manifest["title"], "Updated")
            self.assertEqual(len(manifest["segments"]), 1)
            self.assertEqual(manifest["segments"][0]["word_timestamps"], words)

            clone = store.clone_audiobook(
                saved.id,
                "Updated Copy",
                saved.source_text,
                {"engine": "qwen"},
                root / "clone-output",
                saved.split_mode,
                saved.export_mode,
                {"tts_engine": "qwen"},
            )
            cloned_segments = store.list_segments(clone.id)

            self.assertEqual(len(cloned_segments), 1)
            self.assertNotEqual(cloned_segments[0].wav_path, str(segment_wav))
            self.assertTrue(Path(cloned_segments[0].wav_path).is_file())
            self.assertEqual(json.loads(cloned_segments[0].word_timestamps_json), words)
            self.assertEqual(
                Path(cloned_segments[0].wav_path).read_bytes(),
                b"fake wav",
            )

            imported = store.import_project_manifest(
                clone.project_dir / PROJECT_MANIFEST_NAME
            )
            imported_segments = store.list_segments(imported.id)

            self.assertEqual(imported.title, "Updated Copy")
            self.assertEqual(imported.source_text, "Updated text.")
            self.assertEqual(len(imported_segments), 1)
            self.assertEqual(imported_segments[0].source_text, "Original text.")
            self.assertTrue(Path(imported_segments[0].wav_path).is_file())
            self.assertEqual(json.loads(imported_segments[0].word_timestamps_json), words)


if __name__ == "__main__":
    unittest.main()

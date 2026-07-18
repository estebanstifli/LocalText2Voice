from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from app.core.audio_tail_cut import (
    automatic_tail_cut_seconds,
    full_tail_cut_seconds,
    removable_tail_seconds,
)

from app.core.audio_tail_review import (
    analyze_audio_tail,
    candidate_is_better,
    combined_review_status,
    tail_analysis_is_current,
)
from app.core.audiobook_store import StoredSegment
from app.workers.verification_worker import SegmentVerificationWorker


WORDS = [
    {"word": "Hello", "start": 0.0, "end": 0.45, "probability": 0.99},
    {"word": "world", "start": 0.5, "end": 1.2, "probability": 0.99},
]


class AudioTailReviewTests(unittest.TestCase):
    def test_trailing_whisper_insertion_is_not_the_last_valid_word(self) -> None:
        analysis = analyze_audio_tail(
            "Hello world.",
            [*WORDS, {"word": "uh", "start": 2.0, "end": 2.2}],
            2.3,
        )

        self.assertEqual(analysis["last_valid_word"], "world")
        self.assertEqual(analysis["last_valid_word_end_seconds"], 1.2)
        self.assertEqual(analysis["raw_tail_seconds"], 1.1)
        self.assertEqual(analysis["excess_tail_seconds"], 0.7)
        self.assertEqual(analysis["status"], "review")

    def test_default_threshold_bands_apply_after_safety_margin(self) -> None:
        safe = analyze_audio_tail("Hello world", WORDS, 2.09)
        possible = analyze_audio_tail("Hello world", WORDS, 2.11)
        retry = analyze_audio_tail("Hello world", WORDS, 2.61)

        self.assertEqual(safe["status"], "safe")
        self.assertEqual(possible["status"], "review")
        self.assertEqual(retry["status"], "retry_needed")
        self.assertLess(float(safe["risk_percent"]), 30.0)
        self.assertGreaterEqual(float(retry["risk_percent"]), 70.0)

    def test_tail_cut_points_distinguish_conservative_auto_and_full_cut(self) -> None:
        analysis = analyze_audio_tail("Hello world", WORDS, 2.61)

        automatic = automatic_tail_cut_seconds(analysis)
        full = full_tail_cut_seconds(analysis)

        self.assertAlmostEqual(automatic or 0.0, 2.10)
        self.assertAlmostEqual(full or 0.0, 1.60)
        self.assertAlmostEqual(
            removable_tail_seconds(analysis, automatic or 0.0),
            0.51,
        )
        self.assertAlmostEqual(
            removable_tail_seconds(analysis, full or 0.0),
            1.01,
        )

    def test_missing_alignment_is_unavailable_and_requires_review(self) -> None:
        analysis = analyze_audio_tail("Expected words", [], 2.0)

        self.assertEqual(analysis["status"], "unavailable")
        self.assertEqual(
            combined_review_status("approved", "unavailable"),
            "review",
        )

    def test_combined_status_and_candidate_choice_use_both_layers(self) -> None:
        self.assertEqual(
            combined_review_status("approved", "retry_needed"),
            "retry_needed",
        )
        self.assertEqual(combined_review_status("review", "safe"), "review")
        self.assertEqual(combined_review_status("approved", "safe"), "approved")
        current = {"status": "retry_needed", "selection_score": 90.0, "score": 100.0}
        candidate = {"status": "review", "selection_score": 82.0, "score": 92.0}
        self.assertTrue(candidate_is_better(candidate, current))

    def test_tail_metrics_are_current_only_for_the_same_configuration(self) -> None:
        tail = analyze_audio_tail("Hello world", WORDS, 1.8)
        metrics = json.dumps({"tail_analysis": tail})

        self.assertTrue(
            tail_analysis_is_current(
                metrics,
                enabled=True,
                safety_margin_seconds=0.4,
                warning_threshold_seconds=0.5,
                failure_threshold_seconds=1.0,
            )
        )
        self.assertFalse(
            tail_analysis_is_current(
                metrics,
                enabled=True,
                safety_margin_seconds=0.4,
                warning_threshold_seconds=0.6,
                failure_threshold_seconds=1.0,
            )
        )

    def test_enabling_autocut_requeues_an_excessive_tail_only_once(self) -> None:
        tail = analyze_audio_tail("Hello world", WORDS, 2.61)
        metrics = {"tail_analysis": tail}
        segment = StoredSegment(
            id=1,
            audiobook_id=1,
            sequence_index=1,
            chapter_index=1,
            chapter_title="Chapter",
            source_text="Hello world",
            wav_path="segment.wav",
            status="rendered",
            similarity_score=100.0,
            verification_status="retry_needed",
            transcript_text="Hello world",
            review_metrics_json=json.dumps(metrics),
        )
        worker = SegmentVerificationWorker(
            Mock(),
            1,
            Mock(),
            "cpu",
            "int8",
            "en",
            1,
            92.0,
            tail_analysis_enabled=True,
            tail_autocut_enabled=True,
        )

        self.assertTrue(worker._segment_needs_review(segment))

        metrics["audio_tail_cut"] = {
            "mode": "automatic",
            "whisper_rechecked": True,
            "accepted": False,
        }
        attempted = replace(segment, review_metrics_json=json.dumps(metrics))
        self.assertFalse(worker._segment_needs_review(attempted))

    def test_worker_can_fail_tail_review_with_perfect_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wav_path = Path(temporary) / "segment.wav"
            with wave.open(str(wav_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\0\0" * 2610)
            verifier = Mock()
            verifier.transcribe.return_value = {
                "text": "Hello world",
                "words": WORDS,
            }
            worker = SegmentVerificationWorker(
                Mock(),
                1,
                verifier,
                "cpu",
                "int8",
                "en",
                1,
                92.0,
                tail_analysis_enabled=True,
            )
            segment = StoredSegment(
                id=1,
                audiobook_id=1,
                sequence_index=1,
                chapter_index=1,
                chapter_title="Chapter",
                source_text="Hello world",
                wav_path=str(wav_path),
                status="rendered",
                similarity_score=None,
                verification_status="not_verified",
                transcript_text="",
            )

            result = worker._transcribe_and_score(segment, wav_path, "en")

        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["transcript_status"], "approved")
        self.assertEqual(result["status"], "retry_needed")

    def test_worker_autocut_retranscribes_the_trimmed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav_path = root / "segment.wav"
            with wave.open(str(wav_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\0\0" * 2610)
            verifier = Mock()
            verifier.transcribe.return_value = {
                "text": "Hello world",
                "words": WORDS,
            }
            worker = SegmentVerificationWorker(
                Mock(),
                1,
                verifier,
                "cpu",
                "int8",
                "en",
                1,
                92.0,
                tail_analysis_enabled=True,
                tail_autocut_enabled=True,
            )
            segment = StoredSegment(
                id=1,
                audiobook_id=1,
                sequence_index=1,
                chapter_index=1,
                chapter_title="Chapter",
                source_text="Hello world",
                wav_path=str(wav_path),
                status="rendered",
                similarity_score=None,
                verification_status="not_verified",
                transcript_text="",
            )
            initial = {
                "tail_analysis": analyze_audio_tail(
                    "Hello world",
                    WORDS,
                    2.61,
                )
            }

            def write_trimmed(
                _source: Path,
                output: Path,
                cut_seconds: float,
                _ffmpeg_path: object,
                runner: object,
            ) -> None:
                self.assertIsNotNone(runner)
                with wave.open(str(output), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(1000)
                    audio.writeframes(b"\0\0" * round(cut_seconds * 1000))

            with (
                patch(
                    "app.workers.verification_worker.find_ffmpeg",
                    return_value=Path("ffmpeg.exe"),
                ),
                patch(
                    "app.workers.verification_worker.trim_wav_at",
                    side_effect=write_trimmed,
                ),
            ):
                result = worker._autocut_and_review(
                    segment,
                    wav_path,
                    "en",
                    initial,
                )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(verifier.transcribe.call_count, 1)
        self.assertEqual(result["status"], "approved")
        metrics = json.loads(str(result["review_metrics_json"]))
        self.assertEqual(metrics["audio_tail_cut"]["mode"], "automatic")
        self.assertAlmostEqual(metrics["audio_tail_cut"]["removed_seconds"], 0.51)

    def test_worker_normalizes_only_comparison_and_keeps_whisper_output_raw(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav_path = root / "segment.wav"
            with wave.open(str(wav_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\0\0" * 1500)
            words = [
                {"word": "Pay", "start": 0.0, "end": 0.2},
                {"word": "$19.99", "start": 0.3, "end": 1.0},
                {"word": "at", "start": 1.01, "end": 1.1},
                {"word": "50%", "start": 1.11, "end": 1.2},
            ]
            raw_transcript = "Pay $19.99 at 50%."
            verifier = Mock()
            verifier.transcribe.return_value = {
                "text": raw_transcript,
                "words": words,
                "language": "en",
            }
            worker = SegmentVerificationWorker(
                Mock(),
                1,
                verifier,
                "cpu",
                "int8",
                "auto",
                1,
                92.0,
                tail_analysis_enabled=True,
                comparison_normalization_enabled=True,
                comparison_normalization_language="en",
                comparison_normalization_db_path=root / "normalization.sqlite3",
            )
            segment = StoredSegment(
                id=1,
                audiobook_id=1,
                sequence_index=1,
                chapter_index=1,
                chapter_title="Chapter",
                source_text=(
                    "Pay nineteen dollars and ninety-nine cents at fifty percent."
                ),
                wav_path=str(wav_path),
                status="rendered",
                similarity_score=None,
                verification_status="not_verified",
                transcript_text="",
            )

            result = worker._transcribe_and_score(segment, wav_path, "auto")

        self.assertEqual(result["score"], 100.0)
        self.assertLess(float(result["raw_score"]), 60.0)
        self.assertEqual(result["transcript"], raw_transcript)
        self.assertEqual(json.loads(str(result["word_timestamps_json"])), words)
        self.assertEqual(result["tail_analysis"]["status"], "safe")
        metrics = json.loads(str(result["review_metrics_json"]))
        self.assertTrue(metrics["comparison_normalization_applied"])
        self.assertTrue(metrics["comparison_normalization"]["enabled"])
        self.assertEqual(metrics["comparison_normalization"]["language"], "en")
        self.assertTrue(
            all(metrics["comparison_normalization"]["rules"].values())
        )
        previously_reviewed = replace(
            segment,
            similarity_score=49.0,
            verification_status="retry_needed",
            review_metrics_json="{}",
        )
        self.assertTrue(worker._segment_needs_review(previously_reviewed))
        current_review = replace(
            previously_reviewed,
            similarity_score=100.0,
            verification_status="approved",
            review_metrics_json=str(result["review_metrics_json"]),
        )
        self.assertFalse(worker._segment_needs_review(current_review))

    def test_user_currency_percent_and_date_example_improves_substantially(
        self,
    ) -> None:
        source = (
            "Buy two items for nineteen dollars and ninety-nine cents each and "
            "get the third one at fifty percent off! Offer valid from "
            "seven/eighteen/two thousand twenty-six to seven/thirty-one/2026. "
            "Terms and conditions apply."
        )
        transcript = (
            "Buy two items for $19.99 each and get the third one at 50% off. "
            "Offer valid from 7.18.2026 to 7.30.126.10s and conditions apply."
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav_path = root / "segment.wav"
            with wave.open(str(wav_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\0\0" * 2000)
            verifier = Mock()
            verifier.transcribe.return_value = {
                "text": transcript,
                "words": [],
                "language": "en",
            }
            worker = SegmentVerificationWorker(
                Mock(),
                1,
                verifier,
                "cpu",
                "int8",
                "en",
                1,
                92.0,
                comparison_normalization_enabled=True,
                comparison_normalization_language="en",
                comparison_normalization_db_path=root / "normalization.sqlite3",
            )
            segment = StoredSegment(
                id=1,
                audiobook_id=1,
                sequence_index=1,
                chapter_index=1,
                chapter_title="Chapter",
                source_text=source,
                wav_path=str(wav_path),
                status="rendered",
                similarity_score=None,
                verification_status="not_verified",
                transcript_text="",
            )

            result = worker._transcribe_and_score(segment, wav_path, "en")

        self.assertLess(float(result["raw_score"]), 55.0)
        self.assertGreater(float(result["score"]), 85.0)
        self.assertEqual(result["transcript"], transcript)


if __name__ == "__main__":
    unittest.main()

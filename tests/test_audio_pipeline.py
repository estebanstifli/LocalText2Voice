from __future__ import annotations

import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
from app.tts.base import BaseTTSEngine


class FakeTTSEngine(BaseTTSEngine):
    def __init__(self) -> None:
        self.synthesized_texts: list[str] = []

    def validate(self, voice_config: dict[str, Any]) -> None:
        return

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.synthesized_texts.append(text)
        sample_rate = 16000
        duration = 0.08
        with wave.open(str(output_wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            frames = bytearray()
            for index in range(int(sample_rate * duration)):
                value = int(2500 * math.sin(2 * math.pi * 220 * index / sample_rate))
                frames.extend(struct.pack("<h", value))
            audio.writeframes(bytes(frames))
        return output_wav

    def cancel_current(self) -> None:
        return


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for this test")
class AudioPipelineTests(unittest.TestCase):
    def test_exports_single_mp3_with_real_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            output_dir = Path(temporary_name) / "output"
            options = AudioGenerationOptions(
                output_dir=output_dir,
                voice_config={"speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                chunk_size=200,
                pause_between_blocks_ms=10,
            )
            output_paths = AudioPipeline(FakeTTSEngine()).generate(
                "First sentence. " * 40,
                options,
            )

            self.assertEqual(len(output_paths), 1)
            self.assertEqual(output_paths[0].name, "course_full.mp3")
            self.assertGreater(output_paths[0].stat().st_size, 0)

    def test_exports_numbered_chapter_mp3_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            output_dir = Path(temporary_name) / "output"
            options = AudioGenerationOptions(
                output_dir=output_dir,
                voice_config={"speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                split_mode="chapters",
                export_mode="chapters",
                chunk_size=200,
                pause_between_blocks_ms=0,
            )
            output_paths = AudioPipeline(FakeTTSEngine()).generate(
                "Chapter 1\nFirst body.\n\nChapter 2\nSecond body.",
                options,
            )

            self.assertEqual(
                [path.name for path in output_paths],
                ["chapter_001.mp3", "chapter_002.mp3"],
            )
            self.assertTrue(all(path.stat().st_size > 0 for path in output_paths))

    def test_fixed_paragraph_pause_is_inserted_between_paragraphs(self) -> None:
        class RecordingPipeline(AudioPipeline):
            def __init__(self, engine: BaseTTSEngine) -> None:
                super().__init__(engine)
                self.silence_durations: list[int] = []

            def _create_silence(
                self,
                reference: Path,
                output: Path,
                duration_ms: int,
            ) -> None:
                self.silence_durations.append(duration_ms)
                super()._create_silence(reference, output, duration_ms)

        with tempfile.TemporaryDirectory() as temporary_name:
            engine = FakeTTSEngine()
            pipeline = RecordingPipeline(engine)
            options = AudioGenerationOptions(
                output_dir=Path(temporary_name) / "output",
                voice_config={"speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                paragraph_pause_min_ms=725,
                paragraph_pause_max_ms=725,
                adaptive_paragraph_pause=False,
            )
            pipeline.generate("First paragraph.\n\nSecond paragraph.", options)

            self.assertEqual(engine.synthesized_texts, [
                "First paragraph.",
                "Second paragraph.",
            ])
            self.assertEqual(pipeline.silence_durations, [725])

    def test_mp3_per_block_still_groups_short_paragraphs(self) -> None:
        options = AudioGenerationOptions(
            output_dir=Path("unused"),
            voice_config={"speed": 1.0},
            ffmpeg_path="ffmpeg",
            export_mode="chapters",
            chunk_size=200,
        )
        groups = AudioPipeline(FakeTTSEngine())._prepare_groups(
            "First short paragraph.\n\nSecond short paragraph.",
            options,
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].chunks), 2)

    def test_adaptive_pause_uses_length_and_periodic_rhythm(self) -> None:
        from app.core.text_processor import TextChunk

        options = AudioGenerationOptions(
            output_dir=Path("unused"),
            voice_config={"speed": 1.0},
            ffmpeg_path="ffmpeg",
            paragraph_pause_min_ms=500,
            paragraph_pause_max_ms=500,
            paragraph_length_reference_chars=400,
            paragraph_length_extra_ms=600,
            periodic_pause_every_paragraphs=5,
            periodic_pause_min_ms=300,
            periodic_pause_max_ms=300,
        )
        duration = AudioPipeline._chunk_pause_ms(
            TextChunk(
                text="Paragraph",
                ends_paragraph=True,
                paragraph_length=400,
                paragraph_number=5,
            ),
            False,
            options,
            __import__("random").Random(1),
        )
        self.assertEqual(duration, 1400)

    def test_podcast_mix_keeps_clean_output(self) -> None:
        def create_tone(path: Path, seconds: float, frequency: int) -> None:
            sample_rate = 16000
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                frames = bytearray()
                for index in range(int(sample_rate * seconds)):
                    value = int(
                        1800
                        * math.sin(
                            2 * math.pi * frequency * index / sample_rate
                        )
                    )
                    frames.extend(struct.pack("<h", value))
                audio.writeframes(bytes(frames))

        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            intro = root / "intro.wav"
            background = root / "background.wav"
            outro = root / "outro.wav"
            create_tone(intro, 0.20, 330)
            create_tone(background, 0.12, 110)
            create_tone(outro, 0.20, 440)
            options = AudioGenerationOptions(
                output_dir=root / "output",
                voice_config={"speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                podcast_enabled=True,
                intro_enabled=True,
                intro_path=intro,
                background_enabled=True,
                background_path=background,
                background_loop=True,
                background_volume_percent=12,
                outro_enabled=True,
                outro_path=outro,
                podcast_gap_ms=100,
                podcast_normalize=True,
                podcast_ducking=True,
            )
            outputs = AudioPipeline(FakeTTSEngine()).generate(
                "Podcast narration.",
                options,
            )

            self.assertEqual(
                [path.name for path in outputs],
                ["course_full.mp3", "course_podcast.mp3"],
            )
            self.assertTrue(all(path.stat().st_size > 0 for path in outputs))

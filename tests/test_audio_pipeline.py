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
        self.voice_configs: list[dict[str, Any]] = []

    def validate(self, voice_config: dict[str, Any]) -> None:
        return

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.synthesized_texts.append(text)
        self.voice_configs.append(dict(voice_config))
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


class AudioPipelineMarkupConfigTests(unittest.TestCase):
    def test_qwen_display_voice_sets_speaker_and_language(self) -> None:
        from app.core.text_processor import TextChunk

        pipeline = AudioPipeline(FakeTTSEngine())
        config = pipeline._voice_config_for_chunk(
            {
                "engine": "qwen",
                "speaker": "Sohee",
                "language": "English",
                "instruct": "",
            },
            TextChunk(
                text="Hola.",
                ends_paragraph=True,
                markup_state={"voice": "Serena - Spanish"},
            ),
        )

        self.assertEqual(config["speaker"], "Serena")
        self.assertEqual(config["language"], "Spanish")

    def test_qwen_markup_instruction_uses_instruct_not_text_tag(self) -> None:
        from app.core.text_processor import TextChunk

        pipeline = AudioPipeline(FakeTTSEngine())
        config = pipeline._voice_config_for_chunk(
            {
                "engine": "qwen",
                "speaker": "Serena",
                "language": "Spanish",
                "instruct": "Narrate clearly.",
            },
            TextChunk(
                text="Hola.",
                ends_paragraph=True,
                markup_state={
                    "emotion": "happy",
                    "model_instruction": "[smiling]",
                },
            ),
        )

        self.assertIn("Narrate clearly.", config["instruct"])
        self.assertIn("happy", config["instruct"])
        self.assertIn("smiling", config["instruct"])


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
            self.assertEqual(output_paths[0].name, "podcast1.mp3")
            self.assertGreater(output_paths[0].stat().st_size, 0)

    def test_single_export_uses_next_available_podcast_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            output_dir = Path(temporary_name) / "output"
            output_dir.mkdir()
            (output_dir / "podcast1.mp3").write_bytes(b"existing")
            options = AudioGenerationOptions(
                output_dir=output_dir,
                voice_config={"speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                chunk_size=300,
            )

            output_paths = AudioPipeline(FakeTTSEngine()).generate(
                "A short paragraph for the second podcast.",
                options,
            )

            self.assertEqual(output_paths[0].name, "podcast2.mp3")
            self.assertEqual(
                (output_dir / "podcast1.mp3").read_bytes(),
                b"existing",
            )

    def test_podcast_mix_reserves_matching_consecutive_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            output_dir = Path(temporary_name)
            (output_dir / "podcast1_mix.mp3").write_bytes(b"existing mix")

            names = AudioPipeline._next_single_filenames(output_dir, True)

            self.assertEqual(names, ("podcast2.mp3", "podcast2_mix.mp3"))

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

    def test_ltv_markup_pause_and_commands_are_not_sent_to_piper(self) -> None:
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
                voice_config={"engine": "piper", "speed": 1.0},
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                paragraph_pause_min_ms=0,
                paragraph_pause_max_ms=0,
                adaptive_paragraph_pause=False,
            )
            pipeline.generate(
                'First. {{pause 700}} Second. {{cmd "[laugh]"}}',
                options,
            )

            self.assertEqual(engine.synthesized_texts, ["First.", "Second."])
            self.assertEqual(pipeline.silence_durations, [700])

    def test_ltv_markup_model_command_is_sent_to_qwen(self) -> None:
        options = AudioGenerationOptions(
            output_dir=Path("unused"),
            voice_config={"engine": "qwen", "speed": 1.0},
            ffmpeg_path="ffmpeg",
        )
        groups = AudioPipeline(FakeTTSEngine())._prepare_groups(
            'Text before. {{cmd "[laugh]"}} Text after.',
            options,
        )

        chunks = [chunk for group in groups for chunk in group.chunks]
        combined = " ".join(chunk.text for chunk in chunks)
        self.assertNotIn("[laugh]", combined)
        self.assertNotIn("{{cmd", combined)
        self.assertEqual(chunks[1].markup_state["model_instruction"], "[laugh]")

    def test_ltv_markup_voice_and_language_state_are_applied_per_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            engine = FakeTTSEngine()
            options = AudioGenerationOptions(
                output_dir=Path(temporary_name) / "output",
                voice_config={
                    "engine": "qwen",
                    "speaker": "Serena",
                    "language": "English",
                    "speed": 1.0,
                },
                ffmpeg_path=Path(shutil.which("ffmpeg") or "ffmpeg"),
                paragraph_pause_min_ms=0,
                paragraph_pause_max_ms=0,
                adaptive_paragraph_pause=False,
            )
            AudioPipeline(engine).generate(
                '{{VOICE "Ryan"}}Hello in English. '
                '{{Lang es}}{{Voice "SERENA"}}Hola en espanol.',
                options,
            )

            self.assertEqual(engine.voice_configs[0]["speaker"], "Ryan")
            self.assertEqual(engine.voice_configs[0]["language"], "English")
            self.assertEqual(engine.voice_configs[1]["speaker"], "Serena")
            self.assertEqual(engine.voice_configs[1]["language"], "Spanish")

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

    def test_qwen_uses_short_sentence_chunk_policy(self) -> None:
        options = AudioGenerationOptions(
            output_dir=Path("unused"),
            voice_config={"engine": "qwen", "speed": 1.0},
            ffmpeg_path="ffmpeg",
            chunk_size=2500,
        )
        sentence = (
            "This is one complete sentence designed for Qwen with enough detail "
            "to exercise the faster chunking policy."
        )
        text = " ".join(sentence for _ in range(12))
        groups = AudioPipeline(FakeTTSEngine())._prepare_groups(text, options)

        self.assertEqual(len(groups), 1)
        self.assertGreater(len(groups[0].chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 520 for chunk in groups[0].chunks))

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
                ["podcast1.mp3", "podcast1_mix.mp3"],
            )
            self.assertTrue(all(path.stat().st_size > 0 for path in outputs))

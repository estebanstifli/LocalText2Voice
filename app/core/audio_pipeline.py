from __future__ import annotations

import tempfile
import threading
import wave
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.tts.base import BaseTTSEngine, TTSCancelled, TTSEngineError
from app.utils.ffmpeg_utils import (
    FFmpegCancelled,
    FFmpegError,
    FFmpegRunner,
    find_ffmpeg,
)

from .text_processor import TextChunk, TextProcessor

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


class AudioPipelineError(RuntimeError):
    pass


class GenerationCancelled(AudioPipelineError):
    pass


@dataclass
class AudioGenerationOptions:
    output_dir: Path
    voice_config: dict[str, Any]
    ffmpeg_path: str | Path
    split_mode: str = "safe_chunks"
    export_mode: str = "single"
    chunk_size: int = 2500
    pause_between_blocks_ms: int = 350
    pause_between_chapters_ms: int = 900
    paragraph_pause_min_ms: int = 450
    paragraph_pause_max_ms: int = 900
    adaptive_paragraph_pause: bool = True
    paragraph_length_reference_chars: int = 600
    paragraph_length_extra_ms: int = 650
    periodic_pause_every_paragraphs: int = 5
    periodic_pause_min_ms: int = 350
    periodic_pause_max_ms: int = 750
    normalize_audio: bool = False
    podcast_enabled: bool = False
    intro_enabled: bool = False
    intro_path: Path | None = None
    background_enabled: bool = False
    background_path: Path | None = None
    background_loop: bool = True
    background_volume_percent: int = 12
    outro_enabled: bool = False
    outro_path: Path | None = None
    music_fade_in_seconds: float = 1.5
    music_fade_out_seconds: float = 2.0
    podcast_gap_ms: int = 500
    podcast_normalize: bool = True
    podcast_ducking: bool = True
    mp3_bitrate: str = "128k"
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioGroup:
    title: str
    chunks: tuple[TextChunk, ...]


@dataclass(frozen=True)
class NarrationArtifact:
    wav_path: Path
    mp3_path: Path
    title: str
    podcast_filename: str


class AudioPipeline:
    def __init__(
        self,
        tts_engine: BaseTTSEngine,
        progress_callback: ProgressCallback | None = None,
        log_callback: LogCallback | None = None,
    ) -> None:
        self.tts_engine = tts_engine
        self.progress_callback = progress_callback or (lambda current, total, text: None)
        self.log_callback = log_callback or (lambda message: None)
        self._cancel_requested = threading.Event()
        self._ffmpeg_runner: FFmpegRunner | None = None
        self._runner_lock = threading.Lock()

    def generate(self, text: str, options: AudioGenerationOptions) -> list[Path]:
        runner: FFmpegRunner | None = None
        try:
            self._validate_options(options)
            self.tts_engine.validate(options.voice_config)
            groups = self._prepare_groups(text, options)
            total_chunks = sum(len(group.chunks) for group in groups)
            if total_chunks == 0:
                raise AudioPipelineError(
                    "The text does not contain anything to synthesize."
                )

            options.output_dir.mkdir(parents=True, exist_ok=True)
            ffmpeg_executable = find_ffmpeg(options.ffmpeg_path)
            runner = FFmpegRunner(ffmpeg_executable)
            with self._runner_lock:
                self._ffmpeg_runner = runner

            self.log_callback(
                f"Found {len(groups)} audio group(s) and {total_chunks} block(s)."
            )
            self.log_callback(f"Using FFmpeg: {ffmpeg_executable}")
            self.log_callback(
                "Paragraph pause range: "
                f"{options.paragraph_pause_min_ms}-"
                f"{options.paragraph_pause_max_ms} ms"
            )
            export_steps = len(groups) if options.export_mode == "chapters" else 1
            podcast_steps = export_steps if options.podcast_enabled else 0
            total_steps = total_chunks + export_steps + podcast_steps

            with tempfile.TemporaryDirectory(prefix="local_text_2_voice_") as temp_name:
                temp_dir = Path(temp_name)
                pause_random = random.Random()
                rendered_groups = self._render_groups(
                    groups,
                    options,
                    temp_dir,
                    total_chunks,
                    total_steps,
                )
                self._check_cancelled()
                self.progress_callback(
                    total_chunks,
                    total_steps,
                    "Encoding MP3 output...",
                )
                if options.export_mode == "chapters":
                    artifacts = self._export_chapters(
                        groups,
                        rendered_groups,
                        options,
                        temp_dir,
                        runner,
                        pause_random,
                    )
                else:
                    artifacts = [
                        self._export_single(
                            groups,
                            rendered_groups,
                            options,
                            temp_dir,
                            runner,
                            pause_random,
                        )
                    ]
                completed_steps = total_chunks + export_steps
                self.progress_callback(
                    completed_steps,
                    total_steps,
                    "MP3 narration created.",
                )
                outputs = [artifact.mp3_path for artifact in artifacts]
                if options.podcast_enabled:
                    for artifact in artifacts:
                        self._check_cancelled()
                        podcast_path = self._export_podcast_mix(
                            artifact,
                            options,
                            temp_dir,
                            runner,
                        )
                        outputs.append(podcast_path)
                        completed_steps += 1
                        self.progress_callback(
                            completed_steps,
                            total_steps,
                            "Podcast mix created.",
                        )
                self._check_cancelled()
                return outputs
        except (TTSCancelled, FFmpegCancelled) as exc:
            raise GenerationCancelled("Generation cancelled.") from exc
        except GenerationCancelled:
            raise
        except (TTSEngineError, FFmpegError, OSError, wave.Error) as exc:
            raise AudioPipelineError(str(exc)) from exc
        finally:
            with self._runner_lock:
                self._ffmpeg_runner = None

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.tts_engine.cancel_current()
        with self._runner_lock:
            runner = self._ffmpeg_runner
        if runner is not None:
            runner.cancel_current()

    def _prepare_groups(
        self,
        text: str,
        options: AudioGenerationOptions,
    ) -> list[AudioGroup]:
        if options.split_mode == "chapters":
            sections = TextProcessor.split_by_headings(text)
            return [
                AudioGroup(
                    title=section.title,
                    chunks=tuple(
                        TextProcessor.split_paragraph_chunks(
                            section.text,
                            options.chunk_size,
                        )
                    ),
                )
                for section in sections
                if section.text.strip()
            ]

        chunks = TextProcessor.split_paragraph_chunks(text, options.chunk_size)
        if options.export_mode == "chapters":
            return [
                AudioGroup(title=f"Block {index}", chunks=tuple(group_chunks))
                for index, group_chunks in enumerate(
                    self._group_safe_chunks(chunks, options.chunk_size),
                    start=1,
                )
            ]
        return [AudioGroup(title="Course", chunks=tuple(chunks))]

    @staticmethod
    def _group_safe_chunks(
        chunks: list[TextChunk],
        max_chars: int,
    ) -> list[list[TextChunk]]:
        groups: list[list[TextChunk]] = []
        current: list[TextChunk] = []
        current_size = 0
        for chunk in chunks:
            separator_size = 2 if current else 0
            if current and current_size + separator_size + len(chunk.text) > max_chars:
                groups.append(current)
                current = []
                current_size = 0
                separator_size = 0
            current.append(chunk)
            current_size += separator_size + len(chunk.text)
        if current:
            groups.append(current)
        return groups

    def _render_groups(
        self,
        groups: list[AudioGroup],
        options: AudioGenerationOptions,
        temp_dir: Path,
        total_chunks: int,
        total_steps: int,
    ) -> list[list[Path]]:
        rendered_groups: list[list[Path]] = []
        completed = 0

        for group_index, group in enumerate(groups, start=1):
            rendered_chunks: list[Path] = []
            self.log_callback(
                f"Rendering group {group_index}/{len(groups)}: {group.title}"
            )
            for chunk_index, chunk in enumerate(group.chunks, start=1):
                self._check_cancelled()
                completed += 1
                status = (
                    f"Generating block {completed}/{total_chunks}: "
                    f"{group.title}"
                )
                self.progress_callback(completed - 1, total_steps, status)
                self.log_callback(status)

                output_wav = temp_dir / (
                    f"group_{group_index:03d}_block_{chunk_index:04d}.wav"
                )
                self.tts_engine.synthesize_to_wav(
                    chunk.text,
                    output_wav,
                    options.voice_config,
                )
                rendered_chunks.append(output_wav)
                self.progress_callback(completed, total_steps, status)
            rendered_groups.append(rendered_chunks)
        return rendered_groups

    def _export_single(
        self,
        groups: list[AudioGroup],
        rendered_groups: list[list[Path]],
        options: AudioGenerationOptions,
        temp_dir: Path,
        runner: FFmpegRunner,
        pause_random: random.Random,
    ) -> NarrationArtifact:
        timeline: list[Path] = []
        pause_index = 0

        for group_index, rendered_chunks in enumerate(rendered_groups):
            for chunk_index, wav_path in enumerate(rendered_chunks):
                timeline.append(wav_path)
                is_last_chunk = chunk_index == len(rendered_chunks) - 1
                is_last_group = group_index == len(rendered_groups) - 1
                if is_last_chunk and not is_last_group:
                    duration = options.pause_between_chapters_ms
                else:
                    duration = self._chunk_pause_ms(
                        groups[group_index].chunks[chunk_index],
                        is_last_chunk and is_last_group,
                        options,
                        pause_random,
                    )
                if duration > 0:
                    pause_index += 1
                    silence = temp_dir / f"pause_{pause_index:04d}.wav"
                    self._create_silence(wav_path, silence, duration)
                    timeline.append(silence)

        filename, podcast_filename = self._next_single_filenames(
            options.output_dir,
            options.podcast_enabled,
        )
        joined_wav = self._join_wavs(timeline, temp_dir / "podcast.wav", runner)
        temporary_mp3 = temp_dir / filename
        self.log_callback(f"Encoding {filename}")
        self._encode_mp3(
            joined_wav,
            temporary_mp3,
            options,
            runner,
            options.metadata,
        )
        final_path = options.output_dir / filename
        temporary_mp3.replace(final_path)
        self.log_callback(f"Saved: {final_path}")
        return NarrationArtifact(
            wav_path=joined_wav,
            mp3_path=final_path,
            title=str(options.metadata.get("title", "Course")),
            podcast_filename=podcast_filename,
        )

    def _export_chapters(
        self,
        groups: list[AudioGroup],
        rendered_groups: list[list[Path]],
        options: AudioGenerationOptions,
        temp_dir: Path,
        runner: FFmpegRunner,
        pause_random: random.Random,
    ) -> list[NarrationArtifact]:
        outputs: list[NarrationArtifact] = []
        for group_index, (group, rendered_chunks) in enumerate(
            zip(groups, rendered_groups, strict=True),
            start=1,
        ):
            self._check_cancelled()
            timeline: list[Path] = []
            for chunk_index, wav_path in enumerate(rendered_chunks):
                timeline.append(wav_path)
                if chunk_index < len(rendered_chunks) - 1:
                    duration = self._chunk_pause_ms(
                        group.chunks[chunk_index],
                        False,
                        options,
                        pause_random,
                    )
                else:
                    duration = 0
                if duration > 0:
                    silence = temp_dir / (
                        f"chapter_{group_index:03d}_pause_{chunk_index:04d}.wav"
                    )
                    self._create_silence(
                        wav_path,
                        silence,
                        duration,
                    )
                    timeline.append(silence)

            joined_wav = self._join_wavs(
                timeline,
                temp_dir / f"chapter_{group_index:03d}.wav",
                runner,
            )
            filename, podcast_filename = self._next_chapter_filenames(
                options.output_dir,
                group_index,
                options.podcast_enabled,
            )
            temporary_mp3 = temp_dir / filename
            metadata = dict(options.metadata)
            metadata["title"] = group.title
            self.log_callback(f"Encoding {filename}: {group.title}")
            self._encode_mp3(
                joined_wav,
                temporary_mp3,
                options,
                runner,
                metadata,
            )
            final_path = options.output_dir / filename
            temporary_mp3.replace(final_path)
            outputs.append(
                NarrationArtifact(
                    wav_path=joined_wav,
                    mp3_path=final_path,
                    title=group.title,
                    podcast_filename=podcast_filename,
                )
            )
            self.log_callback(f"Saved: {final_path}")
        return outputs

    @staticmethod
    def _next_single_filenames(
        output_dir: Path,
        include_podcast_mix: bool,
    ) -> tuple[str, str]:
        index = 1
        while True:
            narration = f"podcast{index}.mp3"
            podcast_mix = f"podcast{index}_mix.mp3"
            if not (output_dir / narration).exists() and (
                not include_podcast_mix
                or not (output_dir / podcast_mix).exists()
            ):
                return narration, podcast_mix
            index += 1

    @staticmethod
    def _next_chapter_filenames(
        output_dir: Path,
        group_index: int,
        include_podcast_mix: bool,
    ) -> tuple[str, str]:
        suffix = 0
        while True:
            numbered_suffix = f"_{suffix}" if suffix else ""
            stem = f"chapter_{group_index:03d}{numbered_suffix}"
            narration = f"{stem}.mp3"
            podcast_mix = f"{stem}_podcast.mp3"
            if not (output_dir / narration).exists() and (
                not include_podcast_mix
                or not (output_dir / podcast_mix).exists()
            ):
                return narration, podcast_mix
            suffix += 1

    def _export_podcast_mix(
        self,
        artifact: NarrationArtifact,
        options: AudioGenerationOptions,
        temp_dir: Path,
        runner: FFmpegRunner,
    ) -> Path:
        self.log_callback(f"Creating podcast mix: {artifact.podcast_filename}")
        temporary_output = temp_dir / artifact.podcast_filename
        arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(artifact.wav_path),
        ]
        input_indexes: dict[str, int] = {}
        next_index = 1

        if options.background_enabled and options.background_path is not None:
            if options.background_loop:
                arguments.extend(["-stream_loop", "-1"])
            arguments.extend(["-i", str(options.background_path)])
            input_indexes["background"] = next_index
            next_index += 1
        if options.intro_enabled and options.intro_path is not None:
            arguments.extend(["-i", str(options.intro_path)])
            input_indexes["intro"] = next_index
            next_index += 1
        if options.outro_enabled and options.outro_path is not None:
            arguments.extend(["-i", str(options.outro_path)])
            input_indexes["outro"] = next_index

        narration_duration = self._wav_duration_seconds(artifact.wav_path)
        audio_format = (
            "aresample=44100,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
        )
        filters: list[str] = []

        if "background" in input_indexes:
            background_index = input_indexes["background"]
            volume = options.background_volume_percent / 100
            background_filters = [
                audio_format,
                f"apad=whole_dur={narration_duration:.3f}",
                f"atrim=0:{narration_duration:.3f}",
                "asetpts=N/SR/TB",
                f"volume={volume:.4f}",
            ]
            if options.music_fade_in_seconds > 0:
                background_filters.append(
                    f"afade=t=in:st=0:d={options.music_fade_in_seconds:.3f}"
                )
            if options.music_fade_out_seconds > 0:
                background_filters.extend(
                    [
                        "areverse",
                        (
                            "afade=t=in:st=0:d="
                            f"{options.music_fade_out_seconds:.3f}"
                        ),
                        "areverse",
                    ]
                )
            filters.append(
                f"[{background_index}:a]"
                + ",".join(background_filters)
                + "[background]"
            )
            if options.podcast_ducking:
                filters.append(f"[0:a]{audio_format},asplit=2[voice_mix][voice_side]")
                filters.append(
                    "[background][voice_side]"
                    "sidechaincompress="
                    "threshold=0.025:ratio=8:attack=20:release=500"
                    "[ducked_background]"
                )
                filters.append(
                    "[voice_mix][ducked_background]"
                    "amix=inputs=2:duration=first:normalize=0[body]"
                )
            else:
                filters.append(f"[0:a]{audio_format}[voice]")
                filters.append(
                    "[voice][background]"
                    "amix=inputs=2:duration=first:normalize=0[body]"
                )
        else:
            filters.append(f"[0:a]{audio_format}[body]")

        segments: list[str] = []
        if "intro" in input_indexes:
            filters.append(
                self._music_segment_filter(
                    input_indexes["intro"],
                    "intro",
                    audio_format,
                    options.music_fade_in_seconds,
                    options.music_fade_out_seconds,
                )
            )
            segments.append("[intro]")
        if segments and options.podcast_gap_ms > 0:
            filters.append(self._gap_filter("gap_before_body", options.podcast_gap_ms))
            segments.append("[gap_before_body]")
        segments.append("[body]")
        if "outro" in input_indexes:
            if options.podcast_gap_ms > 0:
                filters.append(
                    self._gap_filter("gap_before_outro", options.podcast_gap_ms)
                )
                segments.append("[gap_before_outro]")
            filters.append(
                self._music_segment_filter(
                    input_indexes["outro"],
                    "outro",
                    audio_format,
                    options.music_fade_in_seconds,
                    options.music_fade_out_seconds,
                )
            )
            segments.append("[outro]")

        if len(segments) > 1:
            filters.append(
                "".join(segments)
                + f"concat=n={len(segments)}:v=0:a=1[podcast_joined]"
            )
            final_label = "podcast_joined"
        else:
            final_label = "body"

        if options.podcast_normalize:
            filters.append(
                f"[{final_label}]"
                "loudnorm=I=-16:LRA=11:TP=-1.5[podcast_final]"
            )
            final_label = "podcast_final"

        arguments.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{final_label}]",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                options.mp3_bitrate,
            ]
        )
        podcast_metadata = dict(options.metadata)
        podcast_metadata["title"] = artifact.title
        for key in ("title", "artist", "album"):
            value = str(podcast_metadata.get(key, "")).strip()
            if value:
                arguments.extend(["-metadata", f"{key}={value}"])
        arguments.append(str(temporary_output))
        runner.run(arguments)

        final_path = options.output_dir / artifact.podcast_filename
        temporary_output.replace(final_path)
        self.log_callback(f"Saved: {final_path}")
        return final_path

    @staticmethod
    def _music_segment_filter(
        input_index: int,
        label: str,
        audio_format: str,
        fade_in_seconds: float,
        fade_out_seconds: float,
    ) -> str:
        filters = [audio_format]
        if fade_in_seconds > 0:
            filters.append(f"afade=t=in:st=0:d={fade_in_seconds:.3f}")
        if fade_out_seconds > 0:
            filters.extend(
                [
                    "areverse",
                    f"afade=t=in:st=0:d={fade_out_seconds:.3f}",
                    "areverse",
                ]
            )
        return f"[{input_index}:a]" + ",".join(filters) + f"[{label}]"

    @staticmethod
    def _gap_filter(label: str, duration_ms: int) -> str:
        return (
            "anullsrc=channel_layout=stereo:sample_rate=44100:"
            f"d={duration_ms / 1000:.3f}[{label}]"
        )

    @staticmethod
    def _wav_duration_seconds(path: Path) -> float:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()

    def _join_wavs(
        self,
        wav_paths: list[Path],
        output_path: Path,
        runner: FFmpegRunner,
    ) -> Path:
        if not wav_paths:
            raise AudioPipelineError("No WAV files were generated.")
        if len(wav_paths) == 1:
            return wav_paths[0]

        concat_file = output_path.with_suffix(".concat.txt")
        concat_lines = [
            f"file '{self._escape_concat_path(path)}'" for path in wav_paths
        ]
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
        runner.run(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        return output_path

    @staticmethod
    def _escape_concat_path(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "'\\''")

    @staticmethod
    def _create_silence(reference: Path, output: Path, duration_ms: int) -> None:
        with wave.open(str(reference), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_rate = source.getframerate()
            compression_type = source.getcomptype()
            compression_name = source.getcompname()

        frame_count = max(1, int(frame_rate * duration_ms / 1000))
        bytes_per_frame = channels * sample_width
        zero_chunk = b"\x00" * (bytes_per_frame * min(frame_count, 4096))

        with wave.open(str(output), "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(sample_width)
            target.setframerate(frame_rate)
            target.setcomptype(compression_type, compression_name)
            remaining = frame_count
            while remaining > 0:
                frames = min(remaining, 4096)
                target.writeframesraw(zero_chunk[: frames * bytes_per_frame])
                remaining -= frames
            target.writeframes(b"")

    @staticmethod
    def _encode_mp3(
        input_wav: Path,
        output_mp3: Path,
        options: AudioGenerationOptions,
        runner: FFmpegRunner,
        metadata: dict[str, str],
    ) -> None:
        arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_wav),
        ]
        if options.normalize_audio:
            arguments.extend(["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"])
        arguments.extend(["-codec:a", "libmp3lame", "-b:a", options.mp3_bitrate])
        for key in ("title", "artist", "album"):
            value = str(metadata.get(key, "")).strip()
            if value:
                arguments.extend(["-metadata", f"{key}={value}"])
        arguments.append(str(output_mp3))
        runner.run(arguments)

    @staticmethod
    def _chunk_pause_ms(
        chunk: TextChunk,
        is_final_chunk: bool,
        options: AudioGenerationOptions,
        pause_random: random.Random,
    ) -> int:
        if is_final_chunk:
            return 0
        if chunk.ends_paragraph:
            duration = pause_random.randint(
                options.paragraph_pause_min_ms,
                options.paragraph_pause_max_ms,
            )
            if options.adaptive_paragraph_pause:
                length_ratio = min(
                    1.0,
                    chunk.paragraph_length
                    / max(1, options.paragraph_length_reference_chars),
                )
                duration += round(length_ratio * options.paragraph_length_extra_ms)
                if (
                    options.periodic_pause_every_paragraphs > 0
                    and chunk.paragraph_number
                    % options.periodic_pause_every_paragraphs
                    == 0
                ):
                    duration += pause_random.randint(
                        options.periodic_pause_min_ms,
                        options.periodic_pause_max_ms,
                    )
            return duration
        return options.pause_between_blocks_ms

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise GenerationCancelled("Generation cancelled.")

    @staticmethod
    def _validate_options(options: AudioGenerationOptions) -> None:
        if options.split_mode not in {"safe_chunks", "chapters"}:
            raise AudioPipelineError(f"Unknown split mode: {options.split_mode}")
        if options.export_mode not in {"single", "chapters"}:
            raise AudioPipelineError(f"Unknown export mode: {options.export_mode}")
        if options.chunk_size < 200:
            raise AudioPipelineError("Chunk size must be at least 200 characters.")
        if options.paragraph_pause_min_ms < 0:
            raise AudioPipelineError("Minimum paragraph pause cannot be negative.")
        if options.paragraph_pause_max_ms < options.paragraph_pause_min_ms:
            raise AudioPipelineError(
                "Maximum paragraph pause must be greater than or equal to the minimum."
            )
        if options.paragraph_pause_max_ms > 30000:
            raise AudioPipelineError(
                "Paragraph pauses cannot be longer than 30 seconds."
            )
        if options.paragraph_length_reference_chars < 1:
            raise AudioPipelineError(
                "Paragraph length reference must be at least one character."
            )
        if options.paragraph_length_extra_ms < 0:
            raise AudioPipelineError("Paragraph length pause cannot be negative.")
        if options.periodic_pause_every_paragraphs < 0:
            raise AudioPipelineError("Periodic paragraph count cannot be negative.")
        if options.periodic_pause_min_ms < 0:
            raise AudioPipelineError("Periodic pause cannot be negative.")
        if options.periodic_pause_max_ms < options.periodic_pause_min_ms:
            raise AudioPipelineError(
                "Maximum periodic pause must be greater than or equal to the minimum."
            )
        if not 0 <= options.background_volume_percent <= 100:
            raise AudioPipelineError("Background music volume must be from 0 to 100.")
        if options.music_fade_in_seconds < 0 or options.music_fade_out_seconds < 0:
            raise AudioPipelineError("Music fade duration cannot be negative.")
        if options.podcast_gap_ms < 0:
            raise AudioPipelineError("Podcast gap cannot be negative.")
        if options.podcast_enabled:
            for enabled, path, label in (
                (options.intro_enabled, options.intro_path, "intro"),
                (
                    options.background_enabled,
                    options.background_path,
                    "background music",
                ),
                (options.outro_enabled, options.outro_path, "outro"),
            ):
                if enabled and (path is None or not path.is_file()):
                    raise AudioPipelineError(
                        f"The selected {label} audio file does not exist."
                    )
        if not 0.25 <= float(options.voice_config.get("speed", 1.0)) <= 4.0:
            raise AudioPipelineError("Voice speed must be between 0.25 and 4.0.")

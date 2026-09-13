from __future__ import annotations

import difflib
import errno
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import wave
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from app.core.audiobook_store import AudiobookStore, StoredAudiobook
from app.core.audio_formats import (
    AUDIO_FORMATS,
    audio_format_spec,
    encoding_arguments,
    normalize_audio_format,
    normalize_audio_quality,
)
from app.core.audio_mix import ducking_filter
from app.core.book_metadata import (
    apply_m4b_tags,
    book_metadata_from_settings,
    chapter_ranges,
    prepare_project_cover,
    project_cover_path,
    settings_with_book_metadata,
    write_ffmetadata,
)
from app.tts.base import BaseTTSEngine, TTSCancelled, TTSEngineError
from app.utils.ffmpeg_utils import (
    FFmpegCancelled,
    FFmpegError,
    FFmpegRunner,
    find_ffmpeg,
)

from .ltv_markup import LTVMarkupCompiler, LTVMarkupParser, LTVNarrationSection
from .subtitle_export import export_audiobook_subtitles
from .text_processor import TextChunk, TextProcessor
from .text_normalization import normalize_text_for_speech


def _move_file(src: Path, dst: Path) -> None:
    """Move a file even across filesystems."""
    try:
        os.replace(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(src), str(dst))


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
    audio_format: str = "mp3"
    audio_quality: str = "standard"
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
    background_enabled: bool = False
    background_path: Path | None = None
    background_loop: bool = True
    background_volume_percent: int = 45
    voice_volume_db: float = 0.0
    music_volume_db: float = -7.0
    voice_start_offset_ms: int = 2000
    music_tail_ms: int = 2000
    music_fade_in_seconds: float = 1.0
    music_fade_out_seconds: float = 1.0
    podcast_gap_ms: int = 500
    podcast_normalize: bool = True
    podcast_ducking: bool = True
    ducking_strength: str = "low"
    mp3_bitrate: str = "128k"
    metadata: dict[str, str] = field(default_factory=dict)
    text_normalization_enabled: bool = False
    text_normalization_language: str = "auto"
    text_normalization_language_hint: str = ""
    text_normalization_db_path: Path | None = None
    text_normalization_rules: dict[str, bool] = field(default_factory=dict)
    russian_silero_enabled: bool = False
    project_audiobook_id: int | None = None
    project_settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioGroup:
    title: str
    chunks: tuple[TextChunk, ...]


@dataclass(frozen=True)
class NarrationArtifact:
    wav_path: Path
    audio_path: Path
    title: str
    podcast_filename: str

    @property
    def mp3_path(self) -> Path:
        """Deprecated compatibility alias; the file may not be MP3."""

        return self.audio_path


@dataclass(frozen=True)
class VoiceMatch:
    item: Any
    matched_name: str
    exact: bool


class AudioPipeline:
    def __init__(
        self,
        tts_engine: BaseTTSEngine,
        progress_callback: ProgressCallback | None = None,
        log_callback: LogCallback | None = None,
        close_engine_on_finish: bool = True,
        audiobook_store: AudiobookStore | None = None,
    ) -> None:
        self.tts_engine = tts_engine
        self.progress_callback = progress_callback or (lambda current, total, text: None)
        self.log_callback = log_callback or (lambda message: None)
        self.close_engine_on_finish = close_engine_on_finish
        self.audiobook_store = audiobook_store
        self._cancel_requested = threading.Event()
        self._ffmpeg_runner: FFmpegRunner | None = None
        self._runner_lock = threading.Lock()
        self._markup_runtime_warning_keys: set[str] = set()
        self._markup_voice_cache: dict[str, Any] = {}
        self._active_audiobook: StoredAudiobook | None = None
        self._segment_ids: dict[tuple[int, int], int] = {}

    def generate(self, text: str, options: AudioGenerationOptions) -> list[Path]:
        generation_started = time.perf_counter()
        runner: FFmpegRunner | None = None
        try:
            validation_started = time.perf_counter()
            self._validate_options(options)
            self.tts_engine.validate(options.voice_config)
            self._log_tts_engine(options.voice_config)
            self.log_callback(
                "Validation completed in "
                f"{self._format_duration(time.perf_counter() - validation_started)}."
            )

            prepare_started = time.perf_counter()
            groups = self._prepare_groups(text, options)
            total_chunks = sum(len(group.chunks) for group in groups)
            if total_chunks == 0:
                raise AudioPipelineError(
                    "The text does not contain anything to synthesize."
                )
            total_chars = sum(
                len(chunk.text) for group in groups for chunk in group.chunks
            )
            self.log_callback(
                "Text processing completed in "
                f"{self._format_duration(time.perf_counter() - prepare_started)} "
                f"({total_chars:,} chars, {total_chunks} block(s))."
            )
            if self.audiobook_store is not None:
                project_title = (
                    str(options.metadata.get("title", "")).strip()
                    or "Audiobook"
                )
                existing = self.audiobook_store.get_audiobook(
                    options.project_audiobook_id
                )
                if existing is None:
                    self._active_audiobook = self.audiobook_store.create_audiobook(
                        source_text=text,
                        voice_config=options.voice_config,
                        output_dir=options.output_dir,
                        split_mode=options.split_mode,
                        export_mode=options.export_mode,
                        title=project_title,
                        project_settings=options.project_settings,
                    )
                else:
                    self._active_audiobook = self.audiobook_store.save_audiobook_project(
                        existing.id,
                        source_text=text,
                        voice_config=options.voice_config,
                        output_dir=options.output_dir,
                        split_mode=options.split_mode,
                        export_mode=options.export_mode,
                        title=project_title,
                        project_settings=options.project_settings,
                    )
                if audio_format_spec(options.audio_format).id == "m4b":
                    book = book_metadata_from_settings(
                        options.project_settings, self._active_audiobook.title
                    )
                    book, _cover = prepare_project_cover(
                        self._active_audiobook.project_dir,
                        str(book.get("title") or self._active_audiobook.title),
                        book,
                    )
                    options.project_settings = settings_with_book_metadata(
                        options.project_settings, book
                    )
                    self._active_audiobook = self.audiobook_store.update_project_settings(
                        self._active_audiobook.id, options.project_settings
                    )
                self._segment_ids = self.audiobook_store.replace_segments(
                    self._active_audiobook,
                    groups,
                )
                for audio_event in self.audiobook_store.list_audio_events(
                    self._active_audiobook.id
                ):
                    try:
                        event_warnings = json.loads(audio_event.warnings_json or "[]")
                    except json.JSONDecodeError:
                        event_warnings = []
                    for warning in event_warnings:
                        if str(warning).startswith(
                            ("Audio file not found:", "Unsupported local audio file:")
                        ):
                            self.log_callback(f"LTV Markup audio warning: {warning}")
                self.log_callback(
                    "Audiobook project saved: "
                    f"{self._active_audiobook.project_dir}"
                )

            options.output_dir.mkdir(parents=True, exist_ok=True)
            ffmpeg_started = time.perf_counter()
            ffmpeg_executable = find_ffmpeg(options.ffmpeg_path)
            runner = FFmpegRunner(ffmpeg_executable)
            with self._runner_lock:
                self._ffmpeg_runner = runner
            self.log_callback(
                "FFmpeg resolved in "
                f"{self._format_duration(time.perf_counter() - ffmpeg_started)}."
            )

            self.log_callback(
                f"Found {len(groups)} audio group(s) and {total_chunks} block(s)."
            )
            self.log_callback(f"Using FFmpeg: {ffmpeg_executable}")
            self.log_callback(
                "Paragraph pause range: "
                f"{options.paragraph_pause_min_ms}-"
                f"{options.paragraph_pause_max_ms} ms"
            )
            output_spec = audio_format_spec(options.audio_format)
            export_steps = 1
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
                    runner,
                )
                self._check_cancelled()
                self.progress_callback(
                    total_chunks,
                    total_steps,
                    f"Encoding {output_spec.label} output...",
                )
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
                    f"{output_spec.label} narration created.",
                )
                outputs = [artifact.audio_path for artifact in artifacts]
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
                if self.audiobook_store is not None and self._active_audiobook is not None:
                    self.audiobook_store.complete_audiobook(
                        self._active_audiobook.id,
                        outputs,
                    )
                    subtitle_result = export_audiobook_subtitles(
                        self.audiobook_store,
                        self._active_audiobook.id,
                        outputs,
                    )
                    if subtitle_result.files:
                        self.log_callback(
                            f"Created {len(subtitle_result.files)} subtitle file(s)."
                        )
                self.log_callback(
                    "Generation pipeline completed in "
                    f"{self._format_duration(time.perf_counter() - generation_started)}."
                )
                return outputs
        except (TTSCancelled, FFmpegCancelled) as exc:
            if self.audiobook_store is not None and self._active_audiobook is not None:
                self.audiobook_store.fail_audiobook(
                    self._active_audiobook.id,
                    "cancelled",
                )
            raise GenerationCancelled("Generation cancelled.") from exc
        except GenerationCancelled:
            if self.audiobook_store is not None and self._active_audiobook is not None:
                self.audiobook_store.fail_audiobook(
                    self._active_audiobook.id,
                    "cancelled",
                )
            raise
        except (TTSEngineError, FFmpegError, OSError, wave.Error) as exc:
            if self.audiobook_store is not None and self._active_audiobook is not None:
                self.audiobook_store.fail_audiobook(self._active_audiobook.id)
            raise AudioPipelineError(str(exc)) from exc
        finally:
            with self._runner_lock:
                self._ffmpeg_runner = None
            if self.close_engine_on_finish:
                try:
                    self.tts_engine.close()
                except Exception as exc:
                    self.log_callback(f"TTS engine cleanup warning: {exc}")

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.tts_engine.cancel_current()
        with self._runner_lock:
            runner = self._ffmpeg_runner
        if runner is not None:
            runner.cancel_current()

    def _log_tts_engine(self, voice_config: dict[str, Any]) -> None:
        engine = str(voice_config.get("engine", "piper"))
        if engine in {"kokoro", "kokoro_python"}:
            self.log_callback("Using TTS engine: Kokoro")
            self.log_callback(f"Kokoro voice: {voice_config.get('voice', 'unknown')}")
            self.log_callback(
                f"Kokoro backend: {voice_config.get('provider', 'auto')}"
            )
            model_path = voice_config.get("model_path")
            if model_path:
                self.log_callback(f"Kokoro model: {model_path}")
        elif engine == "chatterbox":
            self.log_callback("Using TTS engine: Chatterbox")
            self.log_callback(
                f"Chatterbox model: {voice_config.get('model', 'unknown')}"
            )
            self.log_callback(
                f"Chatterbox device: {voice_config.get('device', 'auto')}"
            )
            self.log_callback(
                f"Chatterbox language: {voice_config.get('language', 'en')}"
            )
        elif engine == "qwen":
            self.log_callback("Using TTS engine: Qwen3 TTS")
            self.log_callback(f"Qwen3 model: {voice_config.get('model', 'unknown')}")
            self.log_callback(
                f"Qwen3 speaker: {voice_config.get('speaker', 'unknown')}"
            )
            self.log_callback(
                f"Qwen3 language: {voice_config.get('language', 'Spanish')}"
            )
            self.log_callback(f"Qwen3 device: {voice_config.get('device', 'auto')}")
            self.log_callback(f"Qwen3 dtype: {voice_config.get('dtype', 'auto')}")
        elif engine == "omnivoice":
            self.log_callback("Using TTS engine: OmniVoice")
            self.log_callback(
                f"OmniVoice model: {voice_config.get('model', 'unknown')}"
            )
            self.log_callback(
                f"OmniVoice mode: {voice_config.get('mode', 'clone')}"
            )
            self.log_callback(
                f"OmniVoice language: {voice_config.get('language', 'auto')}"
            )
            self.log_callback(
                f"OmniVoice device: {voice_config.get('device', 'auto')}"
            )
            self.log_callback(f"OmniVoice dtype: {voice_config.get('dtype', 'auto')}")
        elif engine == "f5_russian":
            self.log_callback("Using TTS engine: F5-TTS Russian (non-commercial)")
            self.log_callback(
                f"F5-TTS Russian model: {voice_config.get('model', 'f5tts_v1_base_v2')}"
            )
            self.log_callback(
                "Silero Stress: "
                + ("enabled" if voice_config.get("use_stress", True) else "disabled")
            )
        elif engine == "gemini":
            self.log_callback("Using TTS engine: Google Gemini TTS")
            self.log_callback(f"Gemini model: {voice_config.get('model', 'unknown')}")
            self.log_callback(f"Gemini voice: {voice_config.get('voice', 'unknown')}")
        elif engine in {"openai", "elevenlabs", "azure"}:
            self.log_callback(f"Using TTS engine: {engine}")
            model = voice_config.get("model") or voice_config.get("model_id")
            if model:
                self.log_callback(f"API model: {model}")
        elif engine.startswith("custom:"):
            self.log_callback(
                "Using TTS engine: "
                f"Custom HTTP - {voice_config.get('name', engine)}"
            )
            self.log_callback(
                f"Custom endpoint: {voice_config.get('url', 'unknown')}"
            )
            response_mode = voice_config.get("response_mode")
            if response_mode:
                self.log_callback(f"Custom response mode: {response_mode}")
        elif engine != "piper":
            self.log_callback(f"Using TTS engine: {engine}")

    def _prepare_groups(
        self,
        text: str,
        options: AudioGenerationOptions,
    ) -> list[AudioGroup]:
        text = self._normalize_text_for_tts(text, options)
        if LTVMarkupParser.contains_markup(text):
            backend = str(options.voice_config.get("engine", "piper"))
            markup = LTVMarkupCompiler.compile(text, backend)
            command_count = len(
                [event for event in markup.events if event.type != "text"]
            )
            self.log_callback(
                f"LTV Markup detected: {command_count} command(s), "
                f"{len(markup.warnings)} warning(s)."
            )
            for warning in markup.warnings:
                self.log_callback(f"LTV Markup warning: {warning}")
            groups = self._prepare_markup_groups(markup.sections, options)
            book = book_metadata_from_settings(
                options.project_settings,
                str(options.metadata.get("title", "Audiobook")),
            )
            if len(groups) == 1 and groups[0].title == "Course":
                groups[0] = replace(
                    groups[0], title=str(book.get("title") or "Audiobook")
                )
            return groups

        short_policy = self._short_chunk_policy(options)
        if short_policy:
            target_chars, max_chars, min_chars = short_policy
            self.log_callback(
                "Short sentence chunk policy enabled "
                f"(target {target_chars} chars, max {max_chars}, min {min_chars})."
            )
        book = book_metadata_from_settings(
            options.project_settings,
            str(options.metadata.get("title", "Audiobook")),
        )
        if options.split_mode == "chapters" or (
            audio_format_spec(options.audio_format).id == "m4b"
            and book.get("chapter_mode") == "headings"
        ):
            sections = TextProcessor.split_by_headings(text)
            return [
                AudioGroup(
                    title=section.title,
                    chunks=tuple(self._split_tts_chunks(section.text, options)),
                )
                for section in sections
                if section.text.strip()
            ]

        chunks = self._split_tts_chunks(text, options)
        return [
            AudioGroup(
                title=str(book.get("title") or options.metadata.get("title") or "Audiobook"),
                chunks=tuple(chunks),
            )
        ]

    def _normalize_text_for_tts(
        self,
        text: str,
        options: AudioGenerationOptions,
    ) -> str:
        language_hint = options.text_normalization_language_hint or str(
            options.voice_config.get("language")
            or options.voice_config.get("lang")
            or options.voice_config.get("locale")
            or ""
        )
        result = normalize_text_for_speech(
            text,
            enabled=options.text_normalization_enabled,
            language=options.text_normalization_language,
            language_hint=language_hint,
            preserve_markup=True,
            rules=options.text_normalization_rules,
            db_path=options.text_normalization_db_path,
            russian_silero_enabled=options.russian_silero_enabled,
            engine_id=str(options.voice_config.get("engine", "")),
        )
        if not result.dictionary_applied:
            if not options.text_normalization_enabled:
                return text
            self.log_callback(
                "Text normalization skipped: no dictionary matches the selected language."
            )
            return text
        self.log_callback(
            f"Text normalization applied with the {result.language} dictionary."
        )
        if result.russian_silero_applied:
            self.log_callback("Russian Silero normalization applied after dictionaries.")
            if str(options.voice_config.get("engine", "")) == "f5_russian":
                options.voice_config["russian_silero_preprocessed"] = True
        else:
            options.voice_config.pop("russian_silero_preprocessed", None)
        return result.text

    def _prepare_markup_groups(
        self,
        sections: list[LTVNarrationSection],
        options: AudioGenerationOptions,
    ) -> list[AudioGroup]:
        groups: list[AudioGroup] = []
        for section in sections:
            chunks: list[TextChunk] = []
            for segment in section.segments:
                segment_chunks = self._split_tts_chunks(segment.text, options)
                if not segment_chunks:
                    continue
                audio_events_by_chunk: list[list[Any]] = [
                    [] for _chunk in segment_chunks
                ]
                cumulative_words = 0
                for chunk_position, segment_chunk in enumerate(segment_chunks):
                    chunk_words = LTVMarkupCompiler._word_count(segment_chunk.text)
                    upper_boundary = cumulative_words + chunk_words
                    for audio_event in segment.audio_events:
                        if any(
                            existing.event_uid == audio_event.event_uid
                            for bucket in audio_events_by_chunk
                            for existing in bucket
                        ):
                            continue
                        if (
                            audio_event.anchor_source_word <= upper_boundary
                            or chunk_position == len(segment_chunks) - 1
                        ):
                            audio_events_by_chunk[chunk_position].append(
                                replace(
                                    audio_event,
                                    anchor_source_word=max(
                                        0,
                                        audio_event.anchor_source_word
                                        - cumulative_words,
                                    ),
                                )
                            )
                    cumulative_words = upper_boundary
                for index, chunk in enumerate(segment_chunks):
                    chunks.append(
                        replace(
                            chunk,
                            markup_pause_before_ms=(
                                segment.pause_before_ms if index == 0 else 0
                            ),
                            markup_pause_after_ms=(
                                segment.pause_after_ms
                                if index == len(segment_chunks) - 1
                                else None
                            ),
                            markup_state=dict(segment.state),
                            markup_audio_events=tuple(audio_events_by_chunk[index]),
                        )
                    )
            if chunks:
                groups.append(AudioGroup(title=section.title, chunks=tuple(chunks)))

        return groups

    @staticmethod
    def _short_chunk_policy(
        options: AudioGenerationOptions,
    ) -> tuple[int, int, int] | None:
        engine = str(options.voice_config.get("engine", "piper"))
        if engine == "qwen":
            return (420, 520, 80)
        if engine == "chatterbox":
            return (230, 300, 45)
        return None

    @classmethod
    def _split_tts_chunks(
        cls,
        text: str,
        options: AudioGenerationOptions,
    ) -> list[TextChunk]:
        short_policy = cls._short_chunk_policy(options)
        if short_policy:
            target_chars, max_chars, min_chars = short_policy
            return TextProcessor.split_short_sentence_chunks(
                text,
                target_chars=target_chars,
                max_chars=max_chars,
                min_chars=min_chars,
            )
        return TextProcessor.split_paragraph_chunks(text, options.chunk_size)

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
        runner: FFmpegRunner,
    ) -> list[list[Path]]:
        rendered_groups: list[list[Path]] = []
        completed = 0

        for group_index, group in enumerate(groups, start=1):
            group_started = time.perf_counter()
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

                output_wav = self._segment_output_wav(
                    temp_dir,
                    group_index,
                    chunk_index,
                )
                chunk_voice_config = self._voice_config_for_chunk(
                    options.voice_config,
                    chunk,
                )
                segment_id = self._segment_ids.get((group_index, chunk_index))
                if (
                    segment_id is not None
                    and self.audiobook_store is not None
                ):
                    self.audiobook_store.mark_segment_rendering(
                        segment_id,
                        chunk_voice_config,
                    )
                block_started = time.perf_counter()
                try:
                    self.tts_engine.synthesize_to_wav(
                        chunk.text,
                        output_wav,
                        chunk_voice_config,
                    )
                    self._postprocess_chunk_wav(
                        output_wav,
                        chunk_voice_config,
                        runner,
                        temp_dir,
                        group_index,
                        chunk_index,
                    )
                except Exception as exc:
                    if (
                        segment_id is not None
                        and self.audiobook_store is not None
                    ):
                        self.audiobook_store.mark_segment_failed(
                            segment_id,
                            str(exc),
                        )
                    raise
                block_duration = time.perf_counter() - block_started
                if (
                    segment_id is not None
                    and self.audiobook_store is not None
                ):
                    self.audiobook_store.mark_segment_rendered(
                        segment_id,
                        output_wav,
                        round(self._wav_duration_seconds(output_wav) * 1000),
                        round(block_duration * 1000),
                    )
                self.log_callback(
                    f"Generated block {completed}/{total_chunks} in "
                    f"{self._format_duration(block_duration)} "
                    f"({len(chunk.text):,} chars, "
                    f"{self._format_file_size(output_wav)} WAV)."
                )
                rendered_chunks.append(output_wav)
                self.progress_callback(completed, total_steps, status)
            self.log_callback(
                f"Rendered group {group_index}/{len(groups)} in "
                f"{self._format_duration(time.perf_counter() - group_started)}."
            )
            rendered_groups.append(rendered_chunks)
        return rendered_groups

    def _postprocess_chunk_wav(
        self,
        output_wav: Path,
        voice_config: dict[str, Any],
        runner: FFmpegRunner,
        temp_dir: Path,
        group_index: int,
        chunk_index: int,
    ) -> None:
        filters: list[str] = []
        speed = float(voice_config.get("_postprocess_speed", 1.0) or 1.0)
        if abs(speed - 1.0) > 0.001:
            filters.extend(self._atempo_filters(speed))

        if "_postprocess_volume_db" in voice_config:
            volume_db = float(voice_config.get("_postprocess_volume_db", 0.0) or 0.0)
            if abs(volume_db) > 0.001:
                filters.append(f"volume={volume_db:.3f}dB")

        if "_postprocess_normalize_lufs" in voice_config:
            target_lufs = float(
                voice_config.get("_postprocess_normalize_lufs", -16.0) or -16.0
            )
            filters.append(f"loudnorm=I={target_lufs:.1f}:LRA=11:TP=-1.5")

        if not filters:
            return

        self._check_cancelled()
        processed = temp_dir / (
            f"group_{group_index:03d}_block_{chunk_index:04d}_processed.wav"
        )
        arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(output_wav),
            "-filter:a",
            ",".join(filters),
            "-codec:a",
            "pcm_s16le",
            str(processed),
        ]
        started = time.perf_counter()
        runner.run(arguments)
        _move_file(processed, output_wav)
        self.log_callback(
            "LTV Markup postprocess applied in "
            f"{self._format_duration(time.perf_counter() - started)} "
            f"({', '.join(filters)})."
        )

    @staticmethod
    def _atempo_filters(speed: float) -> list[str]:
        remaining = max(0.25, min(4.0, speed))
        filters: list[str] = []
        while remaining > 2.0:
            filters.append("atempo=2.000")
            remaining /= 2.0
        while remaining < 0.5:
            filters.append("atempo=0.500")
            remaining /= 0.5
        filters.append(f"atempo={remaining:.3f}")
        return filters

    def _segment_output_wav(
        self,
        temp_dir: Path,
        group_index: int,
        chunk_index: int,
    ) -> Path:
        if self._active_audiobook is not None and self.audiobook_store is not None:
            return self.audiobook_store.segment_wav_path(
                self._active_audiobook,
                group_index,
                chunk_index,
            )
        return temp_dir / f"group_{group_index:03d}_block_{chunk_index:04d}.wav"

    def _voice_config_for_chunk(
        self,
        base_config: dict[str, Any],
        chunk: TextChunk,
    ) -> dict[str, Any]:
        state = chunk.markup_state or {}
        if not state:
            return base_config

        config = dict(base_config)
        engine = str(config.get("engine", "piper")).casefold()

        voice_value = str(state.get("voice", "")).strip()
        if voice_value:
            self._apply_markup_voice(
                config,
                engine,
                voice_value,
                str(state.get("voice_language", "")).strip(),
            )

        language_value = str(state.get("language", "")).strip()
        if language_value:
            self._apply_markup_language(config, engine, language_value)

        overrides = state.get("config_overrides")
        if isinstance(overrides, dict) and overrides:
            self._apply_config_overrides(config, overrides)

        if "speed" in state:
            try:
                config["_postprocess_speed"] = max(0.25, min(4.0, float(state["speed"])))
            except (TypeError, ValueError):
                self._log_markup_runtime_warning(
                    "speed:invalid",
                    f"LTV Markup warning: invalid speed value ignored: {state['speed']}",
                )

        if engine == "qwen":
            self._apply_qwen_style_instruction(config, state)
        if "volume_db" in state:
            config["_postprocess_volume_db"] = state["volume_db"]
        if "normalize_lufs" in state:
            config["_postprocess_normalize_lufs"] = state["normalize_lufs"]
        return config

    def _apply_markup_voice(
        self,
        config: dict[str, Any],
        engine: str,
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        if self._is_default_marker(voice_value):
            return
        if engine == "chatterbox":
            self._apply_chatterbox_voice(config, voice_value, voice_language)
        elif engine in {"kokoro", "kokoro_python"}:
            self._apply_kokoro_voice(config, voice_value, voice_language)
        elif engine == "qwen":
            self._apply_qwen_voice(config, voice_value, voice_language)
        elif engine == "omnivoice":
            self._apply_omnivoice_voice(config, voice_value, voice_language)
        elif engine == "f5_russian":
            self._apply_f5_russian_voice(config, voice_value)
        elif engine == "piper":
            self._apply_piper_voice(config, voice_value, voice_language)
        elif engine.startswith("custom:"):
            config["voice"] = voice_value
            if voice_language:
                config["language"] = voice_language
                config["lang"] = voice_language
            self._log_markup_voice_once(
                str(config.get("name", engine)),
                voice_value,
            )
        else:
            self._log_markup_runtime_warning(
                f"voice:unsupported:{engine}",
                f"LTV Markup warning: voice switching is not supported for {engine}.",
            )

    def _apply_markup_language(
        self,
        config: dict[str, Any],
        engine: str,
        language_value: str,
    ) -> None:
        if self._is_default_marker(language_value):
            return
        if engine == "qwen":
            language = self._qwen_language_name(language_value)
            if language:
                config["language"] = language
            else:
                self._warn_unknown_language(engine, language_value)
            return
        if engine in {"kokoro", "kokoro_python"}:
            language = self._kokoro_language_code(language_value)
            if language:
                config["lang"] = language
            else:
                self._warn_unknown_language(engine, language_value)
            return
        if engine == "chatterbox":
            language = self._language_code(language_value)
            if language:
                config["language"] = language
            else:
                self._warn_unknown_language(engine, language_value)
            return
        if engine == "omnivoice":
            language = self._omnivoice_language_name(language_value)
            if language:
                config["language"] = language
            else:
                self._warn_unknown_language(engine, language_value)
            return
        if engine == "piper":
            self._log_markup_runtime_warning(
                "language:piper",
                "LTV Markup warning: {{lang}} does not change Piper language "
                "by itself. Use {{voice \"...\"}} with a Piper voice instead.",
            )
            return
        if engine.startswith("custom:"):
            config["language"] = language_value
            config["lang"] = language_value
            return
        self._log_markup_runtime_warning(
            f"language:unsupported:{engine}",
            f"LTV Markup warning: language switching is not supported for {engine}.",
        )

    def _apply_config_overrides(
        self,
        config: dict[str, Any],
        overrides: dict[str, Any],
    ) -> None:
        reserved = {
            "engine",
            "piper_path",
            "model_path",
            "config_path",
            "ffmpeg_path",
            "cache_dir",
            "install_dir",
            "runtime_dir",
        }
        applied: list[str] = []
        ignored: list[str] = []
        for raw_key, value in overrides.items():
            key = str(raw_key).strip()
            if not key:
                continue
            normalized = key.casefold()
            if normalized in reserved or normalized.startswith("_"):
                ignored.append(key)
                continue
            if key in {"instruct", "instructions"}:
                existing = str(config.get(key, "") or "").strip()
                incoming = str(value or "").strip()
                config[key] = f"{existing} {incoming}".strip() if existing else value
                alias = "instructions" if key == "instruct" else "instruct"
                alias_existing = str(config.get(alias, "") or "").strip()
                if alias_existing and incoming:
                    config[alias] = f"{alias_existing} {incoming}".strip()
                else:
                    config[alias] = config[key]
            else:
                config[key] = value
            applied.append(key)

        if applied:
            self._log_markup_runtime_warning(
                f"cmd:overrides:{','.join(sorted(applied)).casefold()}",
                "LTV Markup: applied next-segment TTS parameter(s): "
                + ", ".join(applied),
            )
        if ignored:
            self._log_markup_runtime_warning(
                f"cmd:reserved:{','.join(sorted(ignored)).casefold()}",
                "LTV Markup warning: reserved TTS parameter(s) ignored: "
                + ", ".join(ignored),
            )

    def _apply_chatterbox_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        try:
            from app.tts.voice_gallery_manager import GalleryVoice, VoiceGalleryManager
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:chatterbox:import",
                f"LTV Markup warning: could not load Chatterbox gallery voices: {exc}",
            )
            return

        cache_key = "chatterbox_gallery"
        voices = self._markup_voice_cache.get(cache_key)
        manager = self._markup_voice_cache.get("chatterbox_gallery_manager")
        if voices is None or not isinstance(manager, VoiceGalleryManager):
            manager = VoiceGalleryManager()
            manager.ensure_seed_loaded()
            voices = [
                voice
                for voice in manager.list_voices("chatterbox")
                if manager.preview_source(voice)
            ]
            if not voices:
                try:
                    manager.sync()
                    voices = [
                        voice
                        for voice in manager.list_voices("chatterbox")
                        if manager.preview_source(voice)
                    ]
                except Exception as exc:
                    self._log_markup_runtime_warning(
                        "voice:chatterbox:sync",
                        f"LTV Markup warning: could not sync Chatterbox voices: {exc}",
                    )
            self._markup_voice_cache[cache_key] = voices
            self._markup_voice_cache["chatterbox_gallery_manager"] = manager

        voice_name, requested_language = self._split_omnivoice_voice_language(
            voice_value,
            voice_language,
        )
        language_name = self._omnivoice_language_name(requested_language)
        candidate_voices = list(voices)
        if language_name:
            filtered = [
                voice
                for voice in candidate_voices
                if self._gallery_voice_matches_language(voice, language_name)
            ]
            if filtered:
                candidate_voices = filtered
            else:
                self._log_markup_runtime_warning(
                    f"voice:chatterbox:language:{self._lookup_key(requested_language)}",
                    "LTV Markup warning: no Chatterbox gallery voices found for "
                    f'language "{requested_language}". Searching all languages.',
                )

        match = self._match_named_item(
            voice_name,
            candidate_voices,
            self._omnivoice_gallery_voice_names,
        )
        if match is None:
            self._warn_unknown_voice("Chatterbox", voice_value)
            return
        matched = match.item
        if not isinstance(matched, GalleryVoice):
            self._warn_unknown_voice("Chatterbox", voice_value)
            return
        try:
            path = manager.ensure_voice_audio(matched)
        except Exception as exc:
            self._log_markup_runtime_warning(
                f"voice:chatterbox:download:{self._lookup_key(voice_value)}",
                f"LTV Markup warning: could not prepare Chatterbox reference "
                f'voice "{matched.name}": {exc}',
            )
            return
        if path is None or not path.is_file():
            self._log_markup_runtime_warning(
                f"voice:chatterbox:missing:{self._lookup_key(voice_value)}",
                f'LTV Markup warning: Chatterbox voice "{matched.name}" does '
                "not provide reference audio.",
            )
            return
        config["reference_audio_path"] = str(path)
        if language_name:
            config["language"] = self._language_code(language_name) or language_name
        elif matched.language:
            config["language"] = self._language_code(matched.language) or matched.language
        if not match.exact:
            self._warn_fuzzy_voice_match("Chatterbox", voice_value, matched.name)
        self._log_markup_voice_once("Chatterbox", matched.name)

    def _apply_kokoro_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        try:
            from app.tts.kokoro_python_manager import KokoroPythonManager
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:kokoro:import",
                f"LTV Markup warning: could not load Kokoro voices: {exc}",
            )
            return

        cache_key = "kokoro"
        voices = self._markup_voice_cache.get(cache_key)
        if voices is None:
            voices = KokoroPythonManager().list_voices()
            self._markup_voice_cache[cache_key] = voices
        candidate_voices = list(voices)
        requested_language = self._kokoro_language_code(voice_language)
        if requested_language:
            filtered = [
                voice
                for voice in candidate_voices
                if str(getattr(voice, "language", "")).casefold()
                == requested_language.casefold()
            ]
            if filtered:
                candidate_voices = filtered
            else:
                self._warn_unknown_language("Kokoro", voice_language)
        match = self._match_named_item(
            voice_value,
            candidate_voices,
            lambda voice: self._kokoro_voice_names(voice),
        )
        if match is None:
            self._warn_unknown_voice("Kokoro", voice_value)
            return
        matched = match.item
        config["voice"] = matched.voice_id
        config["lang"] = matched.language
        if not match.exact:
            self._warn_fuzzy_voice_match("Kokoro", voice_value, matched.display_name)
        self._log_markup_voice_once("Kokoro", matched.display_name)

    def _apply_qwen_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        try:
            from app.tts.qwen_manager import QwenManager
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:qwen:import",
                f"LTV Markup warning: could not load Qwen voices: {exc}",
            )
            return

        cache_key = "qwen"
        voices = self._markup_voice_cache.get(cache_key)
        languages = self._markup_voice_cache.get("qwen_languages")
        if voices is None:
            manager = QwenManager()
            voices = manager.list_voices()
            languages = manager.list_languages()
            self._markup_voice_cache[cache_key] = voices
            self._markup_voice_cache["qwen_languages"] = languages
        if languages is None:
            languages = QwenManager().list_languages()
            self._markup_voice_cache["qwen_languages"] = languages

        matched_pair = self._match_qwen_voice_language(
            voice_value,
            voices,
            languages,
            voice_language,
        )
        if matched_pair is not None:
            matched_voice, matched_language, exact = matched_pair
            config["speaker"] = matched_voice.voice_id
            config["language"] = matched_language.language_id
            display_name = (
                f"{matched_voice.display_name} - {matched_language.display_name}"
            )
            if not exact:
                self._warn_fuzzy_voice_match(
                    "Qwen3 TTS",
                    voice_value,
                    display_name,
                )
            self._log_markup_voice_once(
                "Qwen3 TTS",
                display_name,
            )
            return

        match = self._match_named_item(
            voice_value,
            voices,
            lambda voice: (voice.voice_id, voice.display_name),
        )
        if match is None:
            self._warn_unknown_voice("Qwen3 TTS", voice_value)
            return
        matched = match.item
        config["speaker"] = matched.voice_id
        if not match.exact:
            self._warn_fuzzy_voice_match("Qwen3 TTS", voice_value, matched.display_name)
        self._log_markup_voice_once("Qwen3 TTS", matched.display_name)

    def _apply_qwen_style_instruction(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        _ = state
        base_instruction = str(config.get("instruct", "")).strip()
        if base_instruction:
            config["instruct"] = base_instruction

    def _apply_omnivoice_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        try:
            from app.tts.voice_gallery_manager import GalleryVoice, VoiceGalleryManager
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:omnivoice:import",
                f"LTV Markup warning: could not load OmniVoice gallery voices: {exc}",
            )
            return

        cache_key = "omnivoice_gallery"
        voices = self._markup_voice_cache.get(cache_key)
        manager = self._markup_voice_cache.get("omnivoice_gallery_manager")
        if voices is None or not isinstance(manager, VoiceGalleryManager):
            manager = VoiceGalleryManager()
            manager.ensure_seed_loaded()
            voices = [
                voice
                for voice in manager.list_voices("omnivoice")
                if manager.preview_source(voice)
            ]
            if not voices:
                try:
                    manager.sync()
                    voices = [
                        voice
                        for voice in manager.list_voices("omnivoice")
                        if manager.preview_source(voice)
                    ]
                except Exception as exc:
                    self._log_markup_runtime_warning(
                        "voice:omnivoice:sync",
                        f"LTV Markup warning: could not sync OmniVoice voices: {exc}",
                    )
            self._markup_voice_cache[cache_key] = voices
            self._markup_voice_cache["omnivoice_gallery_manager"] = manager

        voice_name, requested_language = self._split_omnivoice_voice_language(
            voice_value,
            voice_language,
        )
        language_name = self._omnivoice_language_name(requested_language)
        candidate_voices = list(voices)
        if language_name:
            filtered = [
                voice
                for voice in candidate_voices
                if self._gallery_voice_matches_language(voice, language_name)
            ]
            if filtered:
                candidate_voices = filtered
            else:
                self._log_markup_runtime_warning(
                    f"voice:omnivoice:language:{self._lookup_key(requested_language)}",
                    "LTV Markup warning: no OmniVoice gallery voices found for "
                    f'language "{requested_language}". Searching all languages.',
                )

        match = self._match_named_item(
            voice_name,
            candidate_voices,
            self._omnivoice_gallery_voice_names,
        )
        if match is None:
            self._warn_unknown_voice("OmniVoice", voice_value)
            return

        matched = match.item
        if not isinstance(matched, GalleryVoice):
            self._warn_unknown_voice("OmniVoice", voice_value)
            return

        try:
            reference_path = manager.ensure_voice_audio(matched)
        except Exception as exc:
            self._log_markup_runtime_warning(
                f"voice:omnivoice:download:{self._lookup_key(voice_value)}",
                f"LTV Markup warning: could not prepare OmniVoice reference "
                f'voice "{matched.name}": {exc}',
            )
            return
        if reference_path is None or not reference_path.is_file():
            self._log_markup_runtime_warning(
                f"voice:omnivoice:missing:{self._lookup_key(voice_value)}",
                f'LTV Markup warning: OmniVoice voice "{matched.name}" does '
                "not provide reference audio.",
            )
            return

        config["mode"] = "clone"
        config["reference_audio_path"] = str(reference_path)
        config["reference_text"] = matched.ref_text
        config["instruct"] = ""
        if language_name:
            config["language"] = language_name
        elif matched.language_name:
            config["language"] = matched.language_name
        if not match.exact:
            self._warn_fuzzy_voice_match("OmniVoice", voice_value, matched.name)
        self._log_markup_voice_once("OmniVoice", matched.name)

    def _apply_f5_russian_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
    ) -> None:
        try:
            from app.tts.voice_gallery_manager import GalleryVoice, VoiceGalleryManager
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:f5_russian:import",
                f"LTV Markup warning: could not load F5 Russian gallery voices: {exc}",
            )
            return
        manager = self._markup_voice_cache.get("f5_russian_gallery_manager")
        voices = self._markup_voice_cache.get("f5_russian_gallery")
        if voices is None or not isinstance(manager, VoiceGalleryManager):
            manager = VoiceGalleryManager()
            manager.ensure_seed_loaded()
            voices = list(manager.list_voices("f5_russian"))
            self._markup_voice_cache["f5_russian_gallery_manager"] = manager
            self._markup_voice_cache["f5_russian_gallery"] = voices
        match = self._match_named_item(
            voice_value,
            voices,
            self._omnivoice_gallery_voice_names,
        )
        if match is None or not isinstance(match.item, GalleryVoice):
            self._warn_unknown_voice("F5-TTS Russian", voice_value)
            return
        matched = match.item
        try:
            reference_path = manager.ensure_voice_audio(matched)
        except Exception as exc:
            self._log_markup_runtime_warning(
                f"voice:f5_russian:download:{self._lookup_key(voice_value)}",
                f"LTV Markup warning: could not prepare F5 Russian reference voice: {exc}",
            )
            return
        if reference_path is None or not reference_path.is_file() or not matched.ref_text:
            self._log_markup_runtime_warning(
                f"voice:f5_russian:missing:{self._lookup_key(voice_value)}",
                "LTV Markup warning: the F5 Russian voice needs reference audio and transcript.",
            )
            return
        config["reference_audio_path"] = str(reference_path)
        config["reference_text"] = matched.ref_text
        if not match.exact:
            self._warn_fuzzy_voice_match("F5-TTS Russian", voice_value, matched.name)
        self._log_markup_voice_once("F5-TTS Russian", matched.name)

    @classmethod
    def _match_qwen_voice_language(
        cls,
        requested: str,
        voices: Any,
        languages: Any,
        requested_language: str = "",
    ) -> tuple[Any, Any, bool] | None:
        explicit_language = cls._qwen_language_name(requested_language)
        if explicit_language:
            voice_match = cls._match_named_item(
                requested,
                voices,
                lambda voice: (voice.voice_id, voice.display_name),
            )
            language_match = cls._match_named_item(
                explicit_language,
                languages,
                lambda language: (language.language_id, language.display_name),
            )
            if voice_match is not None and language_match is not None:
                return (
                    voice_match.item,
                    language_match.item,
                    voice_match.exact and language_match.exact,
                )
            return None

        pairs = [
            (voice, language)
            for voice in voices
            for language in languages
        ]
        match = cls._match_named_item(
            requested,
            pairs,
            lambda pair: (
                f"{pair[0].display_name} - {pair[1].display_name}",
                f"{pair[0].display_name} {pair[1].display_name}",
                f"{pair[0].voice_id} - {pair[1].language_id}",
                f"{pair[0].voice_id} {pair[1].language_id}",
            ),
            allow_fuzzy=False,
        )
        if match is None and len(cls._lookup_key(requested).split()) >= 2:
            match = cls._match_named_item(
                requested,
                pairs,
                lambda pair: (
                    f"{pair[0].display_name} - {pair[1].display_name}",
                    f"{pair[0].display_name} {pair[1].display_name}",
                    f"{pair[0].voice_id} - {pair[1].language_id}",
                    f"{pair[0].voice_id} {pair[1].language_id}",
                ),
                allow_fuzzy=True,
            )
        if match is None:
            return None
        voice, language = match.item
        return voice, language, match.exact

    def _apply_piper_voice(
        self,
        config: dict[str, Any],
        voice_value: str,
        voice_language: str = "",
    ) -> None:
        try:
            from app.tts.voice_manager import VoiceManager
            from app.utils.paths import application_root
        except Exception as exc:
            self._log_markup_runtime_warning(
                "voice:piper:import",
                f"LTV Markup warning: could not load Piper voices: {exc}",
            )
            return

        cache_key = "piper"
        voices = self._markup_voice_cache.get(cache_key)
        if voices is None:
            voices = VoiceManager(application_root() / "voices").discover()
            self._markup_voice_cache[cache_key] = voices
        candidate_voices = list(voices)
        requested_language = self._language_code(voice_language)
        if requested_language:
            filtered = [
                voice
                for voice in candidate_voices
                if self._piper_voice_matches_language(voice, requested_language)
            ]
            if filtered:
                candidate_voices = filtered
            else:
                self._warn_unknown_language("Piper", voice_language)
        match = self._match_named_item(
            voice_value,
            candidate_voices,
            lambda voice: (
                voice.voice_id,
                voice.display_name,
                voice.language,
                Path(voice.voice_id).stem,
                Path(voice.voice_id).parent.as_posix(),
            ),
        )
        if match is None:
            self._warn_unknown_voice("Piper", voice_value)
            return
        matched = match.item
        speed = float(config.get("speed", 1.0))
        config.update(matched.as_config(speed))
        config["engine"] = "piper"
        if not match.exact:
            self._warn_fuzzy_voice_match("Piper", voice_value, matched.display_name)
        self._log_markup_voice_once("Piper", matched.display_name)

    @classmethod
    def _piper_voice_matches_language(cls, voice: Any, requested_language: str) -> bool:
        candidates = (
            str(getattr(voice, "language", "")),
            str(getattr(voice, "voice_id", "")),
            str(getattr(voice, "display_name", "")),
        )
        target = cls._lookup_key(requested_language)
        for candidate in candidates:
            key = cls._lookup_key(candidate.replace("_", " "))
            if key == target or key.startswith(target):
                return True
        return False

    @staticmethod
    def _chatterbox_voice_names(voice: Any) -> tuple[str, ...]:
        stem = Path(str(voice.file_name)).stem
        voice_id = str(voice.voice_id)
        return (
            str(voice.display_name),
            str(voice.file_name),
            stem,
            voice_id,
            voice_id.removeprefix("chatterbox_"),
            voice_id.removeprefix("local_"),
        )

    @staticmethod
    def _kokoro_voice_names(voice: Any) -> tuple[str, ...]:
        display_name = str(voice.display_name)
        short_name = display_name.split(" - ", 1)[-1]
        voice_id = str(voice.voice_id)
        return (
            voice_id,
            display_name,
            short_name,
            voice_id.split("_", 1)[-1],
        )

    @classmethod
    def _split_omnivoice_voice_language(
        cls,
        voice_value: str,
        voice_language: str = "",
    ) -> tuple[str, str]:
        explicit_language = voice_language.strip()
        if explicit_language:
            return voice_value.strip(), explicit_language

        parts = re.split(r"\s*[-\u2010-\u2015\u2212]\s*", voice_value.strip(), maxsplit=1)
        if len(parts) == 2 and cls._omnivoice_language_name(parts[1]):
            return parts[0].strip(), parts[1].strip()
        return voice_value.strip(), ""

    @classmethod
    def _gallery_voice_matches_language(cls, voice: Any, language_name: str) -> bool:
        target = cls._lookup_key(language_name)
        if not target:
            return True
        candidates = [
            str(getattr(voice, "language_name", "")),
            str(getattr(voice, "language", "")),
            *[str(tag) for tag in getattr(voice, "tags", ()) or ()],
        ]
        for candidate in candidates:
            key = cls._lookup_key(candidate)
            if not key:
                continue
            if key == target:
                return True
            candidate_language = cls._omnivoice_language_name(candidate)
            if candidate_language and cls._lookup_key(candidate_language) == target:
                return True
        return False

    @staticmethod
    def _omnivoice_gallery_voice_names(voice: Any) -> tuple[str, ...]:
        tags = tuple(str(tag) for tag in getattr(voice, "tags", ()) or ())
        return (
            str(getattr(voice, "voice_id", "")),
            str(getattr(voice, "name", "")),
            str(getattr(voice, "short_description", "")),
            str(getattr(voice, "voice_style", "")),
            str(getattr(voice, "age_style", "")),
            *tags,
        )

    @classmethod
    def _match_named_item(
        cls,
        requested: str,
        items: Any,
        names_callback: Callable[[Any], tuple[str, ...]],
        allow_fuzzy: bool = True,
    ) -> VoiceMatch | None:
        requested_key = cls._lookup_key(requested)
        if not requested_key:
            return None
        names_by_item: list[tuple[Any, tuple[str, ...]]] = [
            (item, tuple(names_callback(item))) for item in items
        ]
        # Search each name field across the complete collection before moving
        # to the next field. IDs and display names therefore take precedence
        # over generic descriptions, styles and tags such as "teacher".
        max_names = max((len(names) for _item, names in names_by_item), default=0)
        for name_index in range(max_names):
            for item, names in names_by_item:
                if name_index >= len(names):
                    continue
                name = names[name_index]
                if requested_key == cls._lookup_key(name):
                    return VoiceMatch(item, str(name), True)

        candidates: list[tuple[Any, str, str]] = []
        for item, names in names_by_item:
            for name in names:
                candidate_key = cls._lookup_key(name)
                if not candidate_key:
                    continue
                candidates.append((item, str(name), candidate_key))

        if not allow_fuzzy:
            return None
        fuzzy = cls._best_fuzzy_voice_match(requested_key, candidates)
        if fuzzy is None:
            return None
        item, name, _score = fuzzy
        return VoiceMatch(item, name, False)

    @classmethod
    def _best_fuzzy_voice_match(
        cls,
        requested_key: str,
        candidates: list[tuple[Any, str, str]],
    ) -> tuple[Any, str, float] | None:
        if len(requested_key) < 2:
            return None

        best: tuple[Any, str, float] | None = None
        requested_tokens = requested_key.split()
        for item, name, candidate_key in candidates:
            candidate_tokens = candidate_key.split()
            score = 0.0
            if candidate_key.startswith(requested_key):
                score = 0.98
            elif any(token.startswith(requested_key) for token in candidate_tokens):
                score = 0.95
            elif requested_key in candidate_key:
                score = 0.90
            elif requested_tokens and all(
                any(
                    token.startswith(requested_token)
                    for token in candidate_tokens
                )
                for requested_token in requested_tokens
            ):
                score = 0.88
            elif len(requested_key) >= 5:
                score = difflib.SequenceMatcher(
                    None,
                    requested_key,
                    candidate_key,
                ).ratio()

            if score > 0 and (best is None or score > best[2]):
                best = (item, name, score)

        if best is None:
            return None
        minimum_score = 0.72 if len(requested_key) >= 5 else 0.88
        if best[2] < minimum_score:
            return None
        return best

    @classmethod
    def _language_code(cls, value: str) -> str:
        aliases = {
            "en": "en",
            "eng": "en",
            "english": "en",
            "ingles": "en",
            "es": "es",
            "spa": "es",
            "spanish": "es",
            "espanol": "es",
            "fr": "fr",
            "fre": "fr",
            "french": "fr",
            "frances": "fr",
            "de": "de",
            "ger": "de",
            "german": "de",
            "aleman": "de",
            "it": "it",
            "ita": "it",
            "italian": "it",
            "italiano": "it",
            "pt": "pt",
            "por": "pt",
            "portuguese": "pt",
            "portugues": "pt",
            "zh": "zh",
            "cn": "zh",
            "chinese": "zh",
            "chino": "zh",
            "ja": "ja",
            "jp": "ja",
            "japanese": "ja",
            "japones": "ja",
            "ko": "ko",
            "korean": "ko",
            "coreano": "ko",
            "ru": "ru",
            "russian": "ru",
            "ruso": "ru",
            "hi": "hi",
            "hin": "hi",
            "hindi": "hi",
        }
        key = cls._lookup_key(value)
        if key in aliases:
            return aliases[key]
        compact = value.strip().casefold()
        if len(compact) in {2, 3} and compact.isalpha():
            return compact[:2]
        return ""

    @classmethod
    def _kokoro_language_code(cls, value: str) -> str:
        key = cls._lookup_key(value)
        aliases = {
            "en": "en-us",
            "en us": "en-us",
            "american english": "en-us",
            "english": "en-us",
            "ingles": "en-us",
            "en gb": "en-gb",
            "british english": "en-gb",
            "es": "es",
            "spanish": "es",
            "espanol": "es",
            "fr": "fr-fr",
            "fr fr": "fr-fr",
            "french": "fr-fr",
            "frances": "fr-fr",
            "it": "it",
            "italian": "it",
            "italiano": "it",
            "pt": "pt-br",
            "pt br": "pt-br",
            "portuguese": "pt-br",
            "portugues": "pt-br",
            "zh": "zh",
            "chinese": "zh",
            "chino": "zh",
            "hi": "hi",
            "hindi": "hi",
        }
        if key in aliases:
            return aliases[key]
        raw = value.strip().casefold().replace("_", "-")
        if raw in {"en-us", "en-gb", "es", "fr-fr", "it", "pt-br", "zh", "hi"}:
            return raw
        return ""

    @classmethod
    def _qwen_language_name(cls, value: str) -> str:
        code = cls._language_code(value)
        key = cls._lookup_key(value)
        aliases = {
            "zh": "Chinese",
            "chinese": "Chinese",
            "chino": "Chinese",
            "en": "English",
            "english": "English",
            "ingles": "English",
            "ja": "Japanese",
            "japanese": "Japanese",
            "japones": "Japanese",
            "ko": "Korean",
            "korean": "Korean",
            "coreano": "Korean",
            "de": "German",
            "german": "German",
            "aleman": "German",
            "fr": "French",
            "french": "French",
            "frances": "French",
            "ru": "Russian",
            "russian": "Russian",
            "ruso": "Russian",
            "pt": "Portuguese",
            "portuguese": "Portuguese",
            "portugues": "Portuguese",
            "es": "Spanish",
            "spanish": "Spanish",
            "espanol": "Spanish",
            "it": "Italian",
            "italian": "Italian",
            "italiano": "Italian",
            "hi": "Hindi",
            "hin": "Hindi",
            "hindi": "Hindi",
        }
        return aliases.get(key) or aliases.get(code, "")

    @classmethod
    def _omnivoice_language_name(cls, value: str) -> str:
        code = cls._language_code(value)
        key = cls._lookup_key(value)
        aliases = {
            "zh": "Chinese",
            "chinese": "Chinese",
            "chino": "Chinese",
            "en": "English",
            "english": "English",
            "ingles": "English",
            "ja": "Japanese",
            "japanese": "Japanese",
            "japones": "Japanese",
            "ko": "Korean",
            "korean": "Korean",
            "coreano": "Korean",
            "de": "German",
            "german": "German",
            "aleman": "German",
            "fr": "French",
            "french": "French",
            "frances": "French",
            "ru": "Russian",
            "russian": "Russian",
            "ruso": "Russian",
            "pt": "Portuguese",
            "portuguese": "Portuguese",
            "portugues": "Portuguese",
            "es": "Spanish",
            "spanish": "Spanish",
            "espanol": "Spanish",
            "it": "Italian",
            "italian": "Italian",
            "italiano": "Italian",
            "hi": "Hindi",
            "hin": "Hindi",
            "hindi": "Hindi",
        }
        # OmniVoice accepts both a language name and an ISO language ID and
        # ships its own 600+ language registry. Keep common aliases friendly,
        # then let OmniVoice validate every other non-empty value itself.
        return aliases.get(key) or aliases.get(code) or value.strip()

    @staticmethod
    def _is_default_marker(value: str) -> bool:
        return value.strip().casefold() in {"", "auto", "default", "narrator"}

    @classmethod
    def _lookup_key(cls, value: object) -> str:
        text = str(value).strip().translate(
            str.maketrans(
                {
                    "_": " ",
                    "-": " ",
                    "\u2010": " ",
                    "\u2011": " ",
                    "\u2012": " ",
                    "\u2013": " ",
                    "\u2014": " ",
                    "\u2212": " ",
                    "\u201c": "",
                    "\u201d": "",
                    "\u2018": "",
                    "\u2019": "",
                }
            )
        )
        text = "".join(
            character
            for character in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(character)
        )
        return " ".join(text.split()).casefold()

    def _warn_unknown_voice(self, engine_name: str, voice_value: str) -> None:
        self._log_markup_runtime_warning(
            f"voice:{engine_name.casefold()}:{self._lookup_key(voice_value)}",
            f'LTV Markup warning: {engine_name} voice not found: "{voice_value}". '
            "Using the selected default voice.",
        )

    def _warn_fuzzy_voice_match(
        self,
        engine_name: str,
        requested: str,
        selected: str,
    ) -> None:
        self._log_markup_runtime_warning(
            (
                f"voice:fuzzy:{engine_name.casefold()}:"
                f"{self._lookup_key(requested)}:{self._lookup_key(selected)}"
            ),
            f'LTV Markup warning: voice "{requested}" was not found exactly. '
            f"Closest {engine_name} voice selected: {selected}",
        )

    def _warn_unknown_language(self, engine: str, language_value: str) -> None:
        self._log_markup_runtime_warning(
            f"language:{engine}:{self._lookup_key(language_value)}",
            f'LTV Markup warning: language not recognized for {engine}: '
            f'"{language_value}". Using the selected default language.',
        )

    def _log_markup_voice_once(self, engine_name: str, display_name: str) -> None:
        key = f"voice:selected:{engine_name.casefold()}:{self._lookup_key(display_name)}"
        if key in self._markup_runtime_warning_keys:
            return
        self._markup_runtime_warning_keys.add(key)
        self.log_callback(f"LTV Markup: using {engine_name} voice: {display_name}")

    def _log_markup_runtime_warning(self, key: str, message: str) -> None:
        if key in self._markup_runtime_warning_keys:
            return
        self._markup_runtime_warning_keys.add(key)
        self.log_callback(message)

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
        total_pause_ms = 0
        timeline_ms = 0
        chapter_ranges: list[tuple[str, int, int]] = []

        for group_index, rendered_chunks in enumerate(rendered_groups):
            chapter_start_ms = timeline_ms
            for chunk_index, wav_path in enumerate(rendered_chunks):
                chunk = groups[group_index].chunks[chunk_index]
                segment_id = self._segment_ids.get((group_index + 1, chunk_index + 1))
                before_duration = max(0, chunk.markup_pause_before_ms)
                if chunk.markup_pause_before_ms > 0:
                    pause_index += 1
                    total_pause_ms += chunk.markup_pause_before_ms
                    silence = temp_dir / f"pause_{pause_index:04d}_before.wav"
                    self._create_silence(
                        wav_path,
                        silence,
                        chunk.markup_pause_before_ms,
                    )
                    timeline.append(silence)
                    timeline_ms += chunk.markup_pause_before_ms
                timeline.append(wav_path)
                timeline_ms += max(1, round(self._wav_duration_seconds(wav_path) * 1000))
                is_last_chunk = chunk_index == len(rendered_chunks) - 1
                is_last_group = group_index == len(rendered_groups) - 1
                if chunk.markup_pause_after_ms is not None:
                    duration = chunk.markup_pause_after_ms
                elif is_last_chunk and not is_last_group:
                    duration = options.pause_between_chapters_ms
                else:
                    duration = self._chunk_pause_ms(
                        chunk,
                        is_last_chunk and is_last_group,
                        options,
                        pause_random,
                    )
                if duration > 0:
                    pause_index += 1
                    total_pause_ms += duration
                    silence = temp_dir / f"pause_{pause_index:04d}.wav"
                    self._create_silence(wav_path, silence, duration)
                    timeline.append(silence)
                    timeline_ms += duration
                if segment_id is not None and self.audiobook_store is not None:
                    self.audiobook_store.update_segment_pause(
                        segment_id,
                        before_duration,
                        duration,
                    )
            chapter_ranges.append(
                (groups[group_index].title, chapter_start_ms, max(chapter_start_ms + 1, timeline_ms))
            )

        filename, podcast_filename = self._next_single_filenames(
            options.output_dir,
            options.podcast_enabled,
            audio_format_spec(options.audio_format).extension,
            self._project_output_title(options),
        )
        self.log_callback(
            f"Prepared narration timeline with {len(timeline)} segment(s), "
            f"{pause_index} pause(s), "
            f"{self._format_duration(total_pause_ms / 1000)} total silence."
        )
        join_started = time.perf_counter()
        joined_wav = self._join_wavs(timeline, temp_dir / "podcast.wav", runner)
        self.log_callback(
            "Joined WAV narration in "
            f"{self._format_duration(time.perf_counter() - join_started)} "
            f"({self._format_file_size(joined_wav)})."
        )
        temporary_audio = temp_dir / filename
        self.log_callback(f"Encoding {filename}")
        encode_started = time.perf_counter()
        output_spec = audio_format_spec(options.audio_format)
        book = book_metadata_from_settings(
            options.project_settings,
            str(options.metadata.get("title", "Audiobook")),
        )
        chapter_metadata = None
        if output_spec.id == "m4b" and book.get("chapter_mode") != "none":
            chapter_metadata = write_ffmetadata(temp_dir / "chapters.ffmetadata", chapter_ranges)
        self._encode_audio(
            joined_wav,
            temporary_audio,
            options,
            runner,
            options.metadata,
            chapter_metadata,
        )
        if output_spec.id == "m4b":
            cover = (
                project_cover_path(self._active_audiobook.project_dir, book)
                if self._active_audiobook is not None
                else None
            )
            apply_m4b_tags(temporary_audio, book, cover)
        self.log_callback(
            f"Encoded {filename} in "
            f"{self._format_duration(time.perf_counter() - encode_started)} "
            f"({self._format_file_size(temporary_audio)})."
        )
        final_path = options.output_dir / filename
        _move_file(temporary_audio, final_path)
        self.log_callback(f"Saved: {final_path}")
        return NarrationArtifact(
            wav_path=joined_wav,
            audio_path=final_path,
            title=str(options.metadata.get("title", "Audiobook")),
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
            pause_count = 0
            total_pause_ms = 0
            for chunk_index, wav_path in enumerate(rendered_chunks):
                chunk = group.chunks[chunk_index]
                segment_id = self._segment_ids.get((group_index, chunk_index + 1))
                before_duration = max(0, chunk.markup_pause_before_ms)
                if chunk.markup_pause_before_ms > 0:
                    pause_count += 1
                    total_pause_ms += chunk.markup_pause_before_ms
                    silence = temp_dir / (
                        f"chapter_{group_index:03d}_pause_{chunk_index:04d}_before.wav"
                    )
                    self._create_silence(
                        wav_path,
                        silence,
                        chunk.markup_pause_before_ms,
                    )
                    timeline.append(silence)
                timeline.append(wav_path)
                if chunk.markup_pause_after_ms is not None:
                    duration = chunk.markup_pause_after_ms
                elif chunk_index < len(rendered_chunks) - 1:
                    duration = self._chunk_pause_ms(
                        chunk,
                        False,
                        options,
                        pause_random,
                    )
                else:
                    duration = 0
                if duration > 0:
                    pause_count += 1
                    total_pause_ms += duration
                    silence = temp_dir / (
                        f"chapter_{group_index:03d}_pause_{chunk_index:04d}.wav"
                    )
                    self._create_silence(
                        wav_path,
                        silence,
                        duration,
                    )
                    timeline.append(silence)
                if segment_id is not None and self.audiobook_store is not None:
                    self.audiobook_store.update_segment_pause(
                        segment_id,
                        before_duration,
                        duration,
                    )

            self.log_callback(
                f"Prepared chapter {group_index:03d} timeline with "
                f"{len(timeline)} segment(s), {pause_count} pause(s), "
                f"{self._format_duration(total_pause_ms / 1000)} total silence."
            )
            join_started = time.perf_counter()
            joined_wav = self._join_wavs(
                timeline,
                temp_dir / f"chapter_{group_index:03d}.wav",
                runner,
            )
            self.log_callback(
                f"Joined chapter {group_index:03d} WAV in "
                f"{self._format_duration(time.perf_counter() - join_started)} "
                f"({self._format_file_size(joined_wav)})."
            )
            filename, podcast_filename = self._next_chapter_filenames(
                options.output_dir,
                group_index,
                options.podcast_enabled,
                audio_format_spec(options.audio_format).extension,
                self._project_output_title(options),
            )
            temporary_audio = temp_dir / filename
            metadata = dict(options.metadata)
            metadata["title"] = group.title
            self.log_callback(f"Encoding {filename}: {group.title}")
            encode_started = time.perf_counter()
            self._encode_audio(
                joined_wav,
                temporary_audio,
                options,
                runner,
                metadata,
            )
            self.log_callback(
                f"Encoded {filename} in "
                f"{self._format_duration(time.perf_counter() - encode_started)} "
                f"({self._format_file_size(temporary_audio)})."
            )
            final_path = options.output_dir / filename
            _move_file(temporary_audio, final_path)
            outputs.append(
                NarrationArtifact(
                    wav_path=joined_wav,
                    audio_path=final_path,
                    title=group.title,
                    podcast_filename=podcast_filename,
                )
            )
            self.log_callback(f"Saved: {final_path}")
        return outputs

    def _project_output_title(self, options: AudioGenerationOptions) -> str:
        from app.core.document_source import output_stem
        imported_stem = output_stem(options.project_settings)
        if imported_stem:
            return self._safe_output_filename_stem(imported_stem)
        if self._active_audiobook is not None:
            return self._safe_output_filename_stem(self._active_audiobook.title)
        return self._safe_output_filename_stem(
            str(options.metadata.get("title", "") or "")
        )

    @staticmethod
    def _safe_output_filename_stem(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        normalized = "".join(
            character
            for character in normalized
            if not unicodedata.category(character).startswith("C")
        )
        stem = re.sub(r'[<>:"/\\|?*]+', "-", normalized)
        stem = re.sub(r"\s+", " ", stem).strip(" .-")
        return stem[:100].rstrip(" .-")

    @staticmethod
    def _next_single_filenames(
        output_dir: Path,
        include_podcast_mix: bool,
        extension: str = ".mp3",
        project_title: str = "",
    ) -> tuple[str, str]:
        stem = AudioPipeline._safe_output_filename_stem(project_title)
        if stem:
            index = 0
            while True:
                numbered_stem = f"{stem}_{index + 1}" if index else stem
                narration = f"{numbered_stem}_voice{extension}"
                podcast_mix = f"{numbered_stem}_mix{extension}"
                if not (output_dir / narration).exists() and (
                    not include_podcast_mix
                    or not (output_dir / podcast_mix).exists()
                ):
                    return narration, podcast_mix
                index += 1
        index = 1
        while True:
            narration = f"podcast{index}{extension}"
            podcast_mix = f"podcast{index}_mix{extension}"
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
        extension: str = ".mp3",
        project_title: str = "",
    ) -> tuple[str, str]:
        project_stem = AudioPipeline._safe_output_filename_stem(project_title)
        suffix = 0
        while True:
            numbered_suffix = f"_{suffix}" if suffix else ""
            chapter_stem = f"chapter_{group_index:03d}{numbered_suffix}"
            stem = (
                f"{project_stem}_{chapter_stem}"
                if project_stem
                else chapter_stem
            )
            narration = (
                f"{stem}_voice{extension}" if project_stem else f"{stem}{extension}"
            )
            podcast_mix = (
                f"{stem}_mix{extension}"
                if project_stem
                else f"{stem}_podcast{extension}"
            )
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
        mix_started = time.perf_counter()
        self.log_callback(f"Creating podcast mix: {artifact.podcast_filename}")
        self.log_callback(
            "Podcast mix options: "
            f"background={'on' if options.background_enabled else 'off'}, "
            f"ducking={'on' if options.podcast_ducking else 'off'}, "
            f"normalization={'on' if options.podcast_normalize else 'off'}, "
            f"voice={options.voice_volume_db:.1f} dB, "
            f"music={options.music_volume_db:.1f} dB, "
            f"voice offset={options.voice_start_offset_ms} ms, "
            f"music tail={options.music_tail_ms} ms."
        )
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

        output_spec = audio_format_spec(options.audio_format)
        book = book_metadata_from_settings(
            options.project_settings,
            str(options.metadata.get("title", "Audiobook")),
        )
        chapter_metadata_index: int | None = None
        if (
            output_spec.id == "m4b"
            and book.get("chapter_mode") != "none"
            and self.audiobook_store is not None
            and self._active_audiobook is not None
        ):
            offset = int(options.voice_start_offset_ms)
            ranges = [
                (title, max(0, start + offset), max(1, end + offset))
                for title, start, end in chapter_ranges(
                    self.audiobook_store.list_segments(self._active_audiobook.id)
                )
                if end + offset > 0
            ]
            if ranges:
                chapter_file = write_ffmetadata(
                    temp_dir / "podcast_mix_chapters.ffmetadata", ranges
                )
                arguments.extend(["-i", str(chapter_file)])
                chapter_metadata_index = next_index
                next_index += 1

        narration_duration = self._wav_duration_seconds(artifact.wav_path)
        voice_offset_seconds = options.voice_start_offset_ms / 1000
        voice_trim_seconds = max(0.0, -voice_offset_seconds)
        voice_delay_seconds = max(0.0, voice_offset_seconds)
        body_duration = max(
            0.1,
            voice_delay_seconds
            + max(0.01, narration_duration - voice_trim_seconds)
            + max(0, options.music_tail_ms) / 1000,
        )
        audio_format = (
            "aresample=44100,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
        )
        filters: list[str] = []

        if "background" in input_indexes:
            background_index = input_indexes["background"]
            background_filters = [
                audio_format,
                f"apad=whole_dur={body_duration:.3f}",
                f"atrim=0:{body_duration:.3f}",
                "asetpts=N/SR/TB",
                f"volume={options.music_volume_db:.2f}dB",
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
                voice_filters = [
                    audio_format,
                ]
                if voice_trim_seconds > 0:
                    voice_filters.extend(
                        [
                            f"atrim=start={voice_trim_seconds:.3f}",
                            "asetpts=N/SR/TB",
                        ]
                    )
                voice_filters.append(f"volume={options.voice_volume_db:.2f}dB")
                if voice_delay_seconds > 0:
                    voice_filters.append(
                        f"adelay={round(voice_delay_seconds * 1000)}:all=1"
                    )
                voice_filters.extend(
                    [
                        f"apad=whole_dur={body_duration:.3f}",
                        f"atrim=0:{body_duration:.3f}",
                        "asetpts=N/SR/TB",
                    ]
                )
                filters.append(
                    "[0:a]" + ",".join(voice_filters) + ","
                    "asplit=2[voice_mix][voice_side]"
                )
                filters.append(
                    "[background][voice_side]"
                    + ducking_filter(options.ducking_strength)
                    + "[ducked_background]"
                )
                filters.append(
                    "[voice_mix][ducked_background]"
                    "amix=inputs=2:duration=first:normalize=0[body]"
                )
            else:
                voice_filters = [
                    audio_format,
                ]
                if voice_trim_seconds > 0:
                    voice_filters.extend(
                        [
                            f"atrim=start={voice_trim_seconds:.3f}",
                            "asetpts=N/SR/TB",
                        ]
                    )
                voice_filters.append(f"volume={options.voice_volume_db:.2f}dB")
                if voice_delay_seconds > 0:
                    voice_filters.append(
                        f"adelay={round(voice_delay_seconds * 1000)}:all=1"
                    )
                voice_filters.extend(
                    [
                        f"apad=whole_dur={body_duration:.3f}",
                        f"atrim=0:{body_duration:.3f}",
                        "asetpts=N/SR/TB",
                    ]
                )
                filters.append(
                    "[0:a]" + ",".join(voice_filters) + "[voice]"
                )
                filters.append(
                    "[voice][background]"
                    "amix=inputs=2:duration=first:normalize=0[body]"
                )
        else:
            voice_filters = [
                audio_format,
            ]
            if voice_trim_seconds > 0:
                voice_filters.extend(
                    [
                        f"atrim=start={voice_trim_seconds:.3f}",
                        "asetpts=N/SR/TB",
                    ]
                )
            voice_filters.append(f"volume={options.voice_volume_db:.2f}dB")
            if voice_delay_seconds > 0:
                voice_filters.append(
                    f"adelay={round(voice_delay_seconds * 1000)}:all=1"
                )
            voice_filters.extend(
                [
                    f"apad=whole_dur={body_duration:.3f}",
                    f"atrim=0:{body_duration:.3f}",
                    "asetpts=N/SR/TB",
                ]
            )
            filters.append(
                "[0:a]" + ",".join(voice_filters) + "[body]"
            )

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
            ]
        )
        arguments.extend(
            encoding_arguments(options.audio_format, options.audio_quality)
        )
        if chapter_metadata_index is not None:
            arguments.extend(
                [
                    "-map_metadata",
                    str(chapter_metadata_index),
                    "-map_chapters",
                    str(chapter_metadata_index),
                ]
            )
        podcast_metadata = dict(options.metadata)
        podcast_metadata["title"] = artifact.title
        for key in ("title", "artist", "album"):
            value = str(podcast_metadata.get(key, "")).strip()
            if value:
                arguments.extend(["-metadata", f"{key}={value}"])
        arguments.append(str(temporary_output))
        encode_started = time.perf_counter()
        runner.run(arguments)
        if output_spec.id == "m4b":
            cover = (
                project_cover_path(self._active_audiobook.project_dir, book)
                if self._active_audiobook is not None
                else None
            )
            apply_m4b_tags(temporary_output, book, cover)
        self.log_callback(
            f"Podcast mix FFmpeg render completed in "
            f"{self._format_duration(time.perf_counter() - encode_started)} "
            f"({self._format_file_size(temporary_output)})."
        )

        final_path = options.output_dir / artifact.podcast_filename
        _move_file(temporary_output, final_path)
        self.log_callback(f"Saved: {final_path}")
        self.log_callback(
            f"Podcast mix completed in "
            f"{self._format_duration(time.perf_counter() - mix_started)}."
        )
        return final_path

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
    def _encode_audio(
        input_wav: Path,
        output_audio: Path,
        options: AudioGenerationOptions,
        runner: FFmpegRunner,
        metadata: dict[str, str],
        chapter_metadata: Path | None = None,
    ) -> None:
        arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_wav),
        ]
        if chapter_metadata is not None:
            arguments.extend(
                ["-i", str(chapter_metadata), "-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1"]
            )
        if options.normalize_audio:
            arguments.extend(["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"])
        arguments.extend(
            encoding_arguments(options.audio_format, options.audio_quality)
        )
        for key in ("title", "artist", "album"):
            value = str(metadata.get(key, "")).strip()
            if value:
                arguments.extend(["-metadata", f"{key}={value}"])
        arguments.append(str(output_audio))
        runner.run(arguments)

    # Kept for integrations that called the previous private helper directly.
    _encode_mp3 = _encode_audio

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
    def _format_duration(seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.0f} ms"
        if seconds < 60:
            return f"{seconds:.2f} s"
        minutes, remaining_seconds = divmod(seconds, 60)
        return f"{int(minutes)} min {remaining_seconds:.1f} s"

    @staticmethod
    def _format_file_size(path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return "unknown size"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @staticmethod
    def _validate_options(options: AudioGenerationOptions) -> None:
        if options.split_mode not in {"safe_chunks", "chapters"}:
            raise AudioPipelineError(f"Unknown split mode: {options.split_mode}")
        if options.export_mode not in {"single", "chapters"}:
            raise AudioPipelineError(f"Unknown export mode: {options.export_mode}")
        normalized_format = normalize_audio_format(options.audio_format)
        if str(options.audio_format).strip().casefold().lstrip(".") not in AUDIO_FORMATS:
            raise AudioPipelineError(f"Unknown audio format: {options.audio_format}")
        normalized_quality = normalize_audio_quality(
            normalized_format,
            options.audio_quality,
        )
        if normalized_quality != str(options.audio_quality).strip().casefold():
            raise AudioPipelineError(
                f"Unknown {normalized_format} quality: {options.audio_quality}"
            )
        if options.chunk_size < 1:
            raise AudioPipelineError("Chunk size must be at least 1 character.")
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
        if not -12 <= options.voice_volume_db <= 6:
            raise AudioPipelineError("Voice mix volume must be from -12 dB to +6 dB.")
        if not -36 <= options.music_volume_db <= 0:
            raise AudioPipelineError("Music mix volume must be from -36 dB to 0 dB.")
        if not -300000 <= options.voice_start_offset_ms <= 300000:
            raise AudioPipelineError("Voice start offset must be within 5 minutes.")
        if not 0 <= options.music_tail_ms <= 600000:
            raise AudioPipelineError("Music tail must be from 0 to 10 minutes.")
        if options.ducking_strength not in {"low", "medium", "high"}:
            raise AudioPipelineError("Ducking strength must be low, medium, or high.")
        if options.music_fade_in_seconds < 0 or options.music_fade_out_seconds < 0:
            raise AudioPipelineError("Music fade duration cannot be negative.")
        if options.podcast_gap_ms < 0:
            raise AudioPipelineError("Podcast gap cannot be negative.")
        if options.podcast_enabled:
            if options.background_enabled and (
                options.background_path is None
                or not options.background_path.is_file()
            ):
                raise AudioPipelineError(
                    "The selected background music audio file does not exist."
                )
        if not 0.25 <= float(options.voice_config.get("speed", 1.0)) <= 4.0:
            raise AudioPipelineError("Voice speed must be between 0.25 and 4.0.")

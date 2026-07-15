from __future__ import annotations

import json
import tempfile
import threading
import time
import traceback
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.core.audio_pipeline import AudioGenerationOptions
from app.core.audiobook_store import AudiobookStore, StoredSegment
from app.core.transcript_similarity import similarity_metrics, verification_status
from app.core.audio_event_timeline import resolve_audio_event_timeline
from app.tts.base import BaseTTSEngine, TTSCancelled, TTSEngineError
from app.tts.engine_registry import create_tts_engine
from app.tts.python_runtime_manager import PythonRuntimeCancelled, PythonRuntimeError
from app.utils.ffmpeg_utils import FFmpegCancelled, FFmpegError, FFmpegRunner, find_ffmpeg
from app.verification.faster_whisper_manager import (
    FasterWhisperCancelled,
    FasterWhisperError,
    FasterWhisperManager,
    FasterWhisperVerifier,
)


class FasterWhisperInstallWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, manager: FasterWhisperManager, operation: str) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.cancel_token = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            if self.operation == "install":
                path = self.manager.install(self.progress.emit, self.cancel_token)
                self.finished.emit(str(path))
            elif self.operation == "remove":
                self.manager.uninstall()
                self.finished.emit(str(self.manager.install_dir))
            else:
                raise FasterWhisperError("Unknown Faster Whisper operation.")
        except (FasterWhisperCancelled, PythonRuntimeCancelled):
            self.cancelled.emit()
        except (FasterWhisperError, PythonRuntimeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Faster Whisper error: {exc}")

    def request_cancel(self) -> None:
        self.cancel_token.set()
        self.manager.cancel()


class FasterWhisperPreloadWorker(QObject):
    log = Signal(str)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        verifier: FasterWhisperVerifier,
        device: str,
        compute_type: str,
    ) -> None:
        super().__init__()
        self.verifier = verifier
        self.device = device
        self.compute_type = compute_type

    @Slot()
    def run(self) -> None:
        try:
            self.verifier.set_log_callback(self.log.emit)
            self.verifier.preload(self.device, self.compute_type)
            self.finished.emit()
        except (FasterWhisperError, FasterWhisperCancelled) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected Faster Whisper preload error: {exc}")


class SegmentVerificationWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        store: AudiobookStore,
        audiobook_id: int,
        verifier: FasterWhisperVerifier | None,
        device: str,
        compute_type: str,
        language: str,
        beam_size: int,
        approve_threshold: float,
        max_retries: int = 0,
        piper_path: Path | None = None,
        fallback_voice_config: dict | None = None,
        only_unverified: bool = True,
        shared_tts_engines: dict[str, BaseTTSEngine] | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.audiobook_id = audiobook_id
        self.verifier = verifier or FasterWhisperVerifier()
        self.owns_verifier = verifier is None
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.approve_threshold = approve_threshold
        self.max_retries = max(0, max_retries)
        self.piper_path = piper_path or Path("engines/piper/piper.exe")
        self.fallback_voice_config = fallback_voice_config or {}
        self.only_unverified = only_unverified
        self._cancel_requested = threading.Event()
        self._tts_engines = shared_tts_engines if shared_tts_engines is not None else {}
        self._owns_tts_engines = shared_tts_engines is None
        self._active_tts_engine: BaseTTSEngine | None = None

    @Slot()
    def run(self) -> None:
        try:
            self.verifier.set_log_callback(self.log.emit)
            segments = [
                segment
                for segment in self.store.list_segments(self.audiobook_id)
                if segment.status in {"rendered", "verified"}
                and segment.wav_path
                and Path(segment.wav_path).is_file()
                and (
                    not self.only_unverified
                    or segment.verification_status in {"not_verified", ""}
                    or segment.similarity_score is None
                )
            ]
            total = len(segments)
            if total == 0:
                self.log.emit(
                    "No pending rendered segments found for review."
                    if self.only_unverified
                    else "No rendered segments found for review."
                )
                summary = resolve_audio_event_timeline(
                    self.store,
                    self.audiobook_id,
                )
                if summary.total:
                    self.log.emit(
                        "PLAY/STOP timeline resolved: "
                        f"{summary.resolved}/{summary.total} event(s) ready."
                    )
                self.finished.emit()
                return
            for index, segment in enumerate(segments, start=1):
                if self._cancel_requested.is_set():
                    raise FasterWhisperCancelled("Verification cancelled.")
                self.progress.emit(
                    index - 1,
                    total,
                    f"Reviewing segment {index}/{total}...",
                )
                self._review_segment(segment)
                self.progress.emit(index, total, f"Reviewed {index}/{total}.")
            summary = resolve_audio_event_timeline(
                self.store,
                self.audiobook_id,
            )
            if summary.total:
                self.log.emit(
                    "PLAY/STOP timeline resolved: "
                    f"{summary.resolved}/{summary.total} ready, "
                    f"{summary.pending} pending, {summary.missing} missing."
                )
            self.finished.emit()
        except FasterWhisperCancelled:
            self.cancelled.emit()
        except (FasterWhisperError, PythonRuntimeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected verification error: {exc}")
        finally:
            if self._owns_tts_engines:
                for engine in self._tts_engines.values():
                    engine.close()
                self._tts_engines.clear()
            self._active_tts_engine = None
            if self.owns_verifier:
                self.verifier.close()

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        self.verifier.cancel_current()
        if self._active_tts_engine is not None:
            self._active_tts_engine.cancel_current()

    def _review_segment(self, segment: StoredSegment) -> None:
        source_wav = Path(segment.wav_path)
        language = self._language_for_segment(segment)
        self.log.emit(
            f"Segment {segment.sequence_index}: Whisper language "
            f"{language if language != 'auto' else 'auto-detect'}."
        )
        best = self._transcribe_and_score(segment, source_wav, language)
        self._save_verification(segment, best)
        if best["status"] == "approved" or self.max_retries <= 0:
            return

        self.log.emit(
            f"Segment {segment.sequence_index}: below threshold "
            f"({best['score']:.1f}% < {self.approve_threshold:.1f}%). "
            f"Starting up to {self.max_retries} automatic retry attempt(s)."
        )
        candidate_paths: list[Path] = []
        for attempt in range(1, self.max_retries + 1):
            if self._cancel_requested.is_set():
                raise FasterWhisperCancelled("Verification cancelled.")
            candidate = self._candidate_wav_path(segment, attempt)
            candidate_paths.append(candidate)
            synthesis_ms = self._regenerate_candidate(segment, candidate, attempt)
            attempt_result = self._transcribe_and_score(segment, candidate, language)
            attempt_result["synthesis_ms"] = synthesis_ms
            self.log.emit(
                f"Segment {segment.sequence_index} retry {attempt}/{self.max_retries}: "
                f"{attempt_result['score']:.1f}% similarity "
                f"({attempt_result['status']})."
            )
            if float(attempt_result["score"]) > float(best["score"]):
                best = attempt_result
                self.log.emit(
                    f"Segment {segment.sequence_index}: retry {attempt} is the new best."
                )
            if attempt_result["status"] == "approved":
                break

        original_path = source_wav.resolve()
        best_path = Path(str(best["wav_path"]))
        if best_path.resolve() != original_path:
            duration_ms = round(_wav_duration_seconds(best_path) * 1000)
            self.store.mark_segment_rendered(
                segment.id,
                best_path,
                duration_ms,
                int(best.get("synthesis_ms", 0)),
            )
            self.log.emit(
                f"Segment {segment.sequence_index}: keeping best audio "
                f"({float(best['score']):.1f}%) -> {best_path}"
            )
        else:
            self.log.emit(
                f"Segment {segment.sequence_index}: original audio remains best "
                f"({float(best['score']):.1f}%)."
            )
        self._save_verification(segment, best)
        for candidate in candidate_paths:
            if candidate.resolve() == best_path.resolve():
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                self.log.emit(f"Could not delete retry candidate {candidate}: {exc}")

    def _transcribe_and_score(
        self,
        segment: StoredSegment,
        wav_path: Path,
        language: str,
    ) -> dict[str, object]:
        started = time.perf_counter()
        result = self.verifier.transcribe(
            wav_path,
            language=language,
            beam_size=self.beam_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        transcript = str(result.get("text", "")).strip()
        word_timestamps = result.get("words", [])
        if not isinstance(word_timestamps, list):
            word_timestamps = []
        metrics = similarity_metrics(segment.source_text, transcript)
        score = float(metrics["similarity_score"])
        status = verification_status(score, self.approve_threshold)
        return {
            "wav_path": wav_path,
            "transcript": transcript,
            "score": score,
            "wer": float(metrics["wer"]),
            "cer": float(metrics["cer"]),
            "status": status,
            "word_timestamps_json": json.dumps(
                word_timestamps,
                ensure_ascii=False,
            ),
            "transcription_ms": round((time.perf_counter() - started) * 1000),
        }

    def _save_verification(
        self,
        segment: StoredSegment,
        result: dict[str, object],
    ) -> None:
        self.store.update_segment_verification(
            segment.id,
            str(result["transcript"]),
            float(result["score"]),
            float(result["wer"]),
            float(result["cer"]),
            str(result["status"]),
            int(result["transcription_ms"]),
            str(result.get("word_timestamps_json", "[]")),
        )
        self.log.emit(
            f"Segment {segment.sequence_index}: {float(result['score']):.1f}% "
            f"similarity ({result['status']})."
        )

    def _regenerate_candidate(
        self,
        segment: StoredSegment,
        output_wav: Path,
        attempt: int,
    ) -> int:
        voice_config = self._voice_config_for_segment(segment)
        engine_id = str(voice_config.get("engine", "piper"))
        engine = self._tts_engines.get(engine_id)
        if engine is None:
            engine = create_tts_engine(engine_id, self.piper_path)
            engine.set_log_callback(self.log.emit)
            self._tts_engines[engine_id] = engine
        self._active_tts_engine = engine
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        temporary_wav = output_wav.with_name(output_wav.stem + ".tmp.wav")
        started = time.perf_counter()
        self.log.emit(
            f"Segment {segment.sequence_index}: generating retry candidate "
            f"{attempt}/{self.max_retries}."
        )
        try:
            engine.synthesize_to_wav(segment.source_text, temporary_wav, voice_config)
            temporary_wav.replace(output_wav)
        finally:
            self._active_tts_engine = None
        return round((time.perf_counter() - started) * 1000)

    def _voice_config_for_segment(self, segment: StoredSegment) -> dict:
        try:
            stored_config = json.loads(segment.engine_config_json or "{}")
        except json.JSONDecodeError:
            stored_config = {}
        if isinstance(stored_config, dict) and stored_config.get("engine"):
            return stored_config
        return dict(self.fallback_voice_config)

    def _language_for_segment(self, segment: StoredSegment) -> str:
        if self.language and self.language != "auto":
            return self.language
        candidates = [segment.language]
        try:
            config = json.loads(segment.engine_config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        if isinstance(config, dict):
            candidates.extend(
                str(config.get(key, "")).strip()
                for key in ("language", "lang", "locale")
            )
        for candidate in candidates:
            normalized = self._normalize_whisper_language(candidate)
            if normalized:
                return normalized
        return "auto"

    @staticmethod
    def _normalize_whisper_language(language: str) -> str:
        value = language.strip().casefold().replace("_", "-")
        if not value or value == "auto":
            return ""
        names = {
            "english": "en",
            "spanish": "es",
            "espanol": "es",
            "español": "es",
            "french": "fr",
            "german": "de",
            "italian": "it",
            "portuguese": "pt",
            "chinese": "zh",
            "japanese": "ja",
            "korean": "ko",
            "russian": "ru",
            "arabic": "ar",
            "hindi": "hi",
        }
        if value in names:
            return names[value]
        code = value.split("-", 1)[0]
        return code if 2 <= len(code) <= 3 else ""

    @staticmethod
    def _candidate_wav_path(segment: StoredSegment, attempt: int) -> Path:
        current = Path(segment.wav_path)
        base_dir = current.parent if current.parent.name == "candidates" else current.parent / "candidates"
        base_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return base_dir / f"segment_{segment.sequence_index:04d}_retry_{attempt}_{stamp}.wav"


class SegmentRegenerationWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(int, str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        store: AudiobookStore,
        segment_id: int,
        piper_path: Path,
        fallback_voice_config: dict,
        tts_engine: BaseTTSEngine | None = None,
        candidate_wav: Path | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.segment_id = segment_id
        self.piper_path = piper_path
        self.fallback_voice_config = fallback_voice_config
        self.tts_engine = tts_engine
        self.candidate_wav = candidate_wav
        self._engine: BaseTTSEngine | None = None
        self._cancel_requested = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            segment = self.store.get_segment(self.segment_id)
            if segment is None:
                raise TTSEngineError("Segment not found.")
            voice_config = self._voice_config_for_segment(segment)
            engine_id = str(voice_config.get("engine", "piper"))
            engine = self.tts_engine or create_tts_engine(engine_id, self.piper_path)
            self._engine = engine
            engine.set_log_callback(self.log.emit)
            engine.validate(voice_config)

            current_wav = Path(segment.wav_path)
            if not current_wav.name:
                raise TTSEngineError("Segment does not have a WAV output path.")
            output_wav = self.candidate_wav or current_wav
            output_wav.parent.mkdir(parents=True, exist_ok=True)
            temporary_wav = output_wav.with_name(output_wav.stem + ".tmp.wav")

            self.progress.emit(0, 1, f"Regenerating segment {segment.sequence_index}...")
            self.log.emit(f"Regenerating segment {segment.sequence_index}: {output_wav}")
            started = time.perf_counter()
            if self._cancel_requested.is_set():
                raise TTSCancelled("Segment regeneration cancelled.")
            engine.synthesize_to_wav(segment.source_text, temporary_wav, voice_config)
            if self._cancel_requested.is_set():
                raise TTSCancelled("Segment regeneration cancelled.")
            temporary_wav.replace(output_wav)
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.log.emit(
                f"Segment {segment.sequence_index} regenerated in {elapsed_ms / 1000:.2f} s."
            )
            self.progress.emit(1, 1, "Segment regenerated.")
            self.finished.emit(segment.id, str(output_wav))
        except TTSCancelled:
            self.cancelled.emit()
        except TTSEngineError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected segment regeneration error: {exc}")
        finally:
            if self.tts_engine is None and self._engine is not None:
                self._engine.close()
            self._engine = None

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        if self._engine is not None:
            self._engine.cancel_current()

    def _voice_config_for_segment(self, segment: StoredSegment) -> dict:
        try:
            stored_config = json.loads(segment.engine_config_json or "{}")
        except json.JSONDecodeError:
            stored_config = {}
        if isinstance(stored_config, dict) and stored_config.get("engine"):
            return stored_config
        return dict(self.fallback_voice_config)


class AudiobookRebuildWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        store: AudiobookStore,
        audiobook_id: int,
        output_dir: Path,
        ffmpeg_path: str | Path,
        options: AudioGenerationOptions,
    ) -> None:
        super().__init__()
        self.store = store
        self.audiobook_id = audiobook_id
        self.output_dir = output_dir
        self.ffmpeg_path = ffmpeg_path
        self.options = options
        self._cancel_requested = threading.Event()
        self._runner: FFmpegRunner | None = None

    @Slot()
    def run(self) -> None:
        try:
            segments = self.store.list_segments(self.audiobook_id)
            if not segments:
                raise FFmpegError("No segments found for rebuild.")
            edited = [segment for segment in segments if segment.status == "edited"]
            if edited:
                raise FFmpegError(
                    "There are edited segments that must be regenerated before rebuilding."
                )
            missing = [
                segment.sequence_index
                for segment in segments
                if not segment.wav_path or not Path(segment.wav_path).is_file()
            ]
            if missing:
                raise FFmpegError(
                    "Cannot rebuild because these segments have no WAV: "
                    + ", ".join(str(index) for index in missing[:12])
                )

            self.output_dir.mkdir(parents=True, exist_ok=True)
            runner = FFmpegRunner(find_ffmpeg(self.ffmpeg_path))
            self._runner = runner
            with tempfile.TemporaryDirectory(prefix="local_text_2_voice_rebuild_") as temp_name:
                temp_dir = Path(temp_name)
                timeline = self._build_timeline(segments, temp_dir)
                self._check_cancelled()
                joined_wav = self._join_wavs(timeline, temp_dir / "review_rebuild.wav", runner)
                self._check_cancelled()
                output_mp3 = self._next_review_filename(self.output_dir)
                self._encode_mp3(joined_wav, output_mp3, runner)
                self.store.complete_audiobook(self.audiobook_id, [output_mp3])
            self.progress.emit(1, 1, "Audiobook rebuilt.")
            self.log.emit(f"Rebuilt audiobook: {output_mp3}")
            self.finished.emit(str(output_mp3))
        except (FFmpegCancelled, TTSCancelled):
            self.cancelled.emit()
        except (FFmpegError, OSError, wave.Error) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected audiobook rebuild error: {exc}")
        finally:
            self._runner = None

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        if self._runner is not None:
            self._runner.cancel_current()

    def _build_timeline(self, segments: list[StoredSegment], temp_dir: Path) -> list[Path]:
        timeline: list[Path] = []
        total_pause_ms = 0
        pause_count = 0
        for index, segment in enumerate(segments):
            self._check_cancelled()
            wav_path = Path(segment.wav_path)
            before_ms = (
                segment.resolved_pause_before_ms
                if segment.resolved_pause_before_ms is not None
                else segment.markup_pause_before_ms
            )
            if before_ms > 0:
                pause_count += 1
                total_pause_ms += before_ms
                silence = temp_dir / f"pause_{pause_count:04d}_before.wav"
                _create_silence(wav_path, silence, before_ms)
                timeline.append(silence)
            timeline.append(wav_path)

            next_segment = segments[index + 1] if index + 1 < len(segments) else None
            after_ms = (
                segment.resolved_pause_after_ms
                if segment.resolved_pause_after_ms is not None
                else self._fallback_pause_after(segment, next_segment)
            )
            if after_ms > 0:
                pause_count += 1
                total_pause_ms += after_ms
                silence = temp_dir / f"pause_{pause_count:04d}.wav"
                _create_silence(wav_path, silence, after_ms)
                timeline.append(silence)
            self.progress.emit(index + 1, len(segments), f"Prepared {index + 1}/{len(segments)} segments.")
        self.log.emit(
            f"Prepared review timeline with {len(timeline)} item(s), "
            f"{pause_count} pause(s), {total_pause_ms / 1000:.2f} s silence."
        )
        return timeline

    def _fallback_pause_after(
        self,
        segment: StoredSegment,
        next_segment: StoredSegment | None,
    ) -> int:
        if next_segment is None:
            return 0
        if segment.markup_pause_after_ms is not None:
            return segment.markup_pause_after_ms
        if next_segment.chapter_index != segment.chapter_index:
            return self.options.pause_between_chapters_ms
        if segment.ends_paragraph:
            duration = round(
                (self.options.paragraph_pause_min_ms + self.options.paragraph_pause_max_ms)
                / 2
            )
            if self.options.adaptive_paragraph_pause:
                length_ratio = min(
                    1.0,
                    segment.paragraph_length
                    / max(1, self.options.paragraph_length_reference_chars),
                )
                duration += round(length_ratio * self.options.paragraph_length_extra_ms)
                if (
                    self.options.periodic_pause_every_paragraphs > 0
                    and segment.paragraph_index
                    % self.options.periodic_pause_every_paragraphs
                    == 0
                ):
                    duration += round(
                        (
                            self.options.periodic_pause_min_ms
                            + self.options.periodic_pause_max_ms
                        )
                        / 2
                    )
            return duration
        return self.options.pause_between_blocks_ms

    def _join_wavs(
        self,
        wav_paths: list[Path],
        output_wav: Path,
        runner: FFmpegRunner,
    ) -> Path:
        if not wav_paths:
            raise FFmpegError("No WAV files were available for rebuild.")
        if len(wav_paths) == 1:
            return wav_paths[0]
        concat_file = output_wav.with_suffix(".concat.txt")
        concat_file.write_text(
            "\n".join(f"file '{_escape_concat_path(path)}'" for path in wav_paths),
            encoding="utf-8",
        )
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
                str(output_wav),
            ]
        )
        return output_wav

    def _encode_mp3(self, input_wav: Path, output_mp3: Path, runner: FFmpegRunner) -> None:
        arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_wav),
        ]
        if self.options.normalize_audio:
            arguments.extend(["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"])
        arguments.extend(
            [
                "-codec:a",
                "libmp3lame",
                "-b:a",
                self.options.mp3_bitrate,
                str(output_mp3),
            ]
        )
        runner.run(arguments)

    @staticmethod
    def _next_review_filename(output_dir: Path) -> Path:
        index = 1
        while True:
            candidate = output_dir / f"podcast_review{index}.mp3"
            if not candidate.exists():
                return candidate
            index += 1

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise FFmpegCancelled("Audiobook rebuild cancelled.")


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


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


def _escape_concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")

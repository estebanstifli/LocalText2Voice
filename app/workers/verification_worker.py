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
from app.core.audio_tail_review import (
    analyze_audio_tail,
    candidate_is_better,
    candidate_selection_score,
    comparison_normalization_is_current,
    combined_review_status,
    parse_review_metrics,
    tail_analysis_is_current,
)
from app.core.audio_tail_cut import (
    automatic_tail_cut_seconds,
    removable_tail_seconds,
    trim_wav_at,
)
from app.core.transcript_similarity import similarity_metrics, verification_status
from app.core.audio_event_timeline import resolve_audio_event_timeline
from app.core.subtitle_export import export_audiobook_subtitles
from app.core.text_normalization import TextNormalizer, normalization_rule_settings
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
        tail_analysis_enabled: bool = False,
        tail_safety_margin_seconds: float = 0.40,
        tail_warning_threshold_seconds: float = 0.50,
        tail_failure_threshold_seconds: float = 1.00,
        comparison_normalization_enabled: bool = False,
        comparison_normalization_language: str = "auto",
        comparison_normalization_db_path: Path | None = None,
        tail_autocut_enabled: bool = False,
        ffmpeg_path: str | Path = "ffmpeg/ffmpeg.exe",
        comparison_normalization_rules: object = None,
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
        self.tail_analysis_enabled = bool(tail_analysis_enabled)
        self.tail_safety_margin_seconds = max(0.0, tail_safety_margin_seconds)
        self.tail_warning_threshold_seconds = max(
            0.01,
            tail_warning_threshold_seconds,
        )
        self.tail_failure_threshold_seconds = max(
            self.tail_warning_threshold_seconds + 0.01,
            tail_failure_threshold_seconds,
        )
        self.tail_autocut_enabled = bool(
            tail_autocut_enabled and self.tail_analysis_enabled
        )
        self.ffmpeg_path = ffmpeg_path
        self.comparison_normalization_enabled = bool(
            comparison_normalization_enabled
        )
        self.comparison_normalization_language = str(
            comparison_normalization_language or "auto"
        )
        self.comparison_normalization_rules = normalization_rule_settings(
            comparison_normalization_rules
        )
        self.comparison_normalizer = (
            TextNormalizer(db_path=comparison_normalization_db_path)
            if self.comparison_normalization_enabled
            else None
        )
        self._cancel_requested = threading.Event()
        self._tts_engines = shared_tts_engines if shared_tts_engines is not None else {}
        self._owns_tts_engines = shared_tts_engines is None
        self._active_tts_engine: BaseTTSEngine | None = None
        self._active_ffmpeg_runner: FFmpegRunner | None = None

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
                and self._segment_needs_review(segment)
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
                self._export_subtitles()
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
            self._export_subtitles()
            self.finished.emit()
        except (FasterWhisperCancelled, FFmpegCancelled):
            self.cancelled.emit()
        except (FasterWhisperError, PythonRuntimeError, FFmpegError) as exc:
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
        if self._active_ffmpeg_runner is not None:
            self._active_ffmpeg_runner.cancel_current()

    def _export_subtitles(self) -> None:
        result = export_audiobook_subtitles(self.store, self.audiobook_id)
        if result.files:
            self.log.emit(
                f"Created {len(result.files)} subtitle file(s) next to the MP3 output(s)."
            )
        elif result.skipped_reason == "needs_rebuild":
            self.log.emit(
                "Subtitle export is waiting for the audiobook to be rebuilt."
            )
        elif result.skipped_reason == "no_word_timestamps":
            self.log.emit("No Whisper word timestamps are available for subtitles.")

    def _review_segment(self, segment: StoredSegment) -> None:
        source_wav = Path(segment.wav_path)
        language = self._language_for_segment(segment)
        self.log.emit(
            f"Segment {segment.sequence_index}: Whisper language "
            f"{language if language != 'auto' else 'auto-detect'}."
        )
        best = self._transcribe_and_score(segment, source_wav, language)
        self._save_verification(segment, best)
        candidate_paths: list[Path] = []
        if self.tail_autocut_enabled:
            autocut_result = self._autocut_and_review(
                segment,
                source_wav,
                language,
                best,
            )
            if autocut_result is not None:
                candidate_paths.append(Path(str(autocut_result["wav_path"])))
                if candidate_is_better(autocut_result, best):
                    self._annotate_audio_tail_cut(
                        autocut_result,
                        mode="automatic",
                        cut_seconds=float(autocut_result["tail_cut_seconds"]),
                        removed_seconds=float(
                            autocut_result["tail_removed_seconds"]
                        ),
                        accepted=True,
                    )
                    best = autocut_result
                    self.log.emit(
                        f"Segment {segment.sequence_index}: the automatically "
                        "trimmed and re-reviewed audio is the new best."
                    )
                else:
                    self._annotate_audio_tail_cut(
                        best,
                        mode="automatic",
                        cut_seconds=float(autocut_result["tail_cut_seconds"]),
                        removed_seconds=float(
                            autocut_result["tail_removed_seconds"]
                        ),
                        accepted=False,
                    )
                    self.log.emit(
                        f"Segment {segment.sequence_index}: the automatically "
                        "trimmed audio did not review better; keeping the original."
                    )

        if not candidate_paths and (
            best["status"] == "approved" or self.max_retries <= 0
        ):
            return

        if best["status"] != "approved" and self.max_retries > 0:
            self.log.emit(
                f"Segment {segment.sequence_index}: review did not pass "
                f"({self._result_summary(best)}). Starting up to "
                f"{self.max_retries} automatic retry attempt(s)."
            )
            for attempt in range(1, self.max_retries + 1):
                if self._cancel_requested.is_set():
                    raise FasterWhisperCancelled("Verification cancelled.")
                candidate = self._candidate_wav_path(segment, attempt)
                candidate_paths.append(candidate)
                synthesis_ms = self._regenerate_candidate(segment, candidate, attempt)
                attempt_result = self._transcribe_and_score(segment, candidate, language)
                attempt_result["synthesis_ms"] = synthesis_ms
                self.log.emit(
                    f"Segment {segment.sequence_index} retry "
                    f"{attempt}/{self.max_retries}: "
                    f"{self._result_summary(attempt_result)}."
                )
                if candidate_is_better(attempt_result, best):
                    best = attempt_result
                    self.log.emit(
                        f"Segment {segment.sequence_index}: retry {attempt} "
                        "is the new best."
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
                f"({self._result_summary(best)}) -> {best_path}"
            )
        else:
            self.log.emit(
                f"Segment {segment.sequence_index}: original audio remains best "
                f"({self._result_summary(best)})."
            )
        self._save_verification(segment, best)
        for candidate in candidate_paths:
            if candidate.resolve() == best_path.resolve():
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                self.log.emit(f"Could not delete retry candidate {candidate}: {exc}")

    def _autocut_and_review(
        self,
        segment: StoredSegment,
        source_wav: Path,
        language: str,
        initial: dict[str, object],
    ) -> dict[str, object] | None:
        tail = initial.get("tail_analysis")
        if not isinstance(tail, dict):
            return None
        cut_seconds = automatic_tail_cut_seconds(tail)
        if cut_seconds is None:
            return None
        removed_seconds = removable_tail_seconds(tail, cut_seconds)
        if removed_seconds <= 0.01:
            return None

        candidate = self._autocut_wav_path(segment)
        ffmpeg = FFmpegRunner(find_ffmpeg(self.ffmpeg_path))
        self._active_ffmpeg_runner = ffmpeg
        self.log.emit(
            f"Segment {segment.sequence_index}: audio tail exceeds the possible-"
            f"artifact threshold; trimming {removed_seconds:.2f}s at "
            f"{cut_seconds:.2f}s and sending the candidate back to Whisper."
        )
        try:
            trim_wav_at(
                source_wav,
                candidate,
                cut_seconds,
                self.ffmpeg_path,
                runner=ffmpeg,
            )
        finally:
            self._active_ffmpeg_runner = None
        result = self._transcribe_and_score(segment, candidate, language)
        self._annotate_audio_tail_cut(
            result,
            mode="automatic",
            cut_seconds=cut_seconds,
            removed_seconds=removed_seconds,
            accepted=None,
        )
        result["tail_cut_seconds"] = cut_seconds
        result["tail_removed_seconds"] = removed_seconds
        self.log.emit(
            f"Segment {segment.sequence_index} automatic tail cut: "
            f"{self._result_summary(result)}."
        )
        return result

    @staticmethod
    def _annotate_audio_tail_cut(
        result: dict[str, object],
        *,
        mode: str,
        cut_seconds: float,
        removed_seconds: float,
        accepted: bool | None,
    ) -> None:
        try:
            metrics = json.loads(str(result.get("review_metrics_json", "{}")))
        except json.JSONDecodeError:
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {}
        metrics["audio_tail_cut"] = {
            "mode": mode,
            "cut_seconds": round(cut_seconds, 3),
            "removed_seconds": round(removed_seconds, 3),
            "whisper_rechecked": True,
            "accepted": accepted,
        }
        result["review_metrics_json"] = json.dumps(metrics, ensure_ascii=False)

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
        raw_metrics = similarity_metrics(segment.source_text, transcript)
        comparison_language_hint = str(result.get("language") or language)
        comparison_language = self._comparison_language(
            comparison_language_hint
        )
        comparison_source, comparison_transcript, comparison_words = (
            self._comparison_values(
                segment.source_text,
                transcript,
                word_timestamps,
                comparison_language_hint,
            )
        )
        metrics = similarity_metrics(comparison_source, comparison_transcript)
        score = float(metrics["similarity_score"])
        transcript_status = verification_status(score, self.approve_threshold)
        tail_analysis: dict[str, object] = {"enabled": False, "status": "disabled"}
        if self.tail_analysis_enabled:
            tail_analysis = analyze_audio_tail(
                comparison_source,
                comparison_words,
                _wav_duration_seconds(wav_path),
                safety_margin_seconds=self.tail_safety_margin_seconds,
                warning_threshold_seconds=self.tail_warning_threshold_seconds,
                failure_threshold_seconds=self.tail_failure_threshold_seconds,
            )
        status = combined_review_status(
            transcript_status,
            str(tail_analysis.get("status", "disabled")),
        )
        selection_score = candidate_selection_score(score, tail_analysis)
        review_metrics = {
            "transcript_status": transcript_status,
            "tail_analysis": tail_analysis,
            "selection_score": round(selection_score, 3),
            "comparison_normalization_applied": (
                comparison_language is not None
                and (
                    comparison_source != segment.source_text
                    or comparison_transcript != transcript
                )
            ),
            "comparison_normalization": {
                "enabled": comparison_language is not None,
                "language": comparison_language or "",
                "rules": dict(self.comparison_normalization_rules),
            },
            "raw_similarity_score": float(raw_metrics["similarity_score"]),
        }
        return {
            "wav_path": wav_path,
            "transcript": transcript,
            "score": score,
            "raw_score": float(raw_metrics["similarity_score"]),
            "wer": float(metrics["wer"]),
            "cer": float(metrics["cer"]),
            "status": status,
            "transcript_status": transcript_status,
            "tail_analysis": tail_analysis,
            "selection_score": selection_score,
            "review_metrics_json": json.dumps(review_metrics, ensure_ascii=False),
            "word_timestamps_json": json.dumps(
                word_timestamps,
                ensure_ascii=False,
            ),
            "transcription_ms": round((time.perf_counter() - started) * 1000),
        }

    def _comparison_values(
        self,
        source_text: str,
        transcript: str,
        word_timestamps: list[object],
        language_hint: str,
    ) -> tuple[str, str, list[object]]:
        normalizer = self.comparison_normalizer
        if normalizer is None:
            return source_text, transcript, word_timestamps
        resolved_language = self._comparison_language(language_hint)
        if resolved_language is None:
            return source_text, transcript, word_timestamps
        normalized_source = normalizer.normalize(
            source_text,
            language=resolved_language,
            preserve_markup=False,
            rules=self.comparison_normalization_rules,
        )
        normalized_transcript = normalizer.normalize(
            transcript,
            language=resolved_language,
            preserve_markup=False,
            rules=self.comparison_normalization_rules,
        )
        normalized_words: list[object] = []
        for value in word_timestamps:
            if not isinstance(value, dict):
                normalized_words.append(value)
                continue
            normalized_value = dict(value)
            normalized_value["word"] = normalizer.normalize(
                str(value.get("word", "")),
                language=resolved_language,
                preserve_markup=False,
                rules=self.comparison_normalization_rules,
            )
            normalized_words.append(normalized_value)
        return normalized_source, normalized_transcript, normalized_words

    def _comparison_language(self, language_hint: str) -> str | None:
        if self.comparison_normalizer is None:
            return None
        return TextNormalizer.resolve_language(
            self.comparison_normalization_language,
            language_hint,
        )

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
            str(result.get("review_metrics_json", "{}")),
        )
        self.log.emit(
            f"Segment {segment.sequence_index}: {self._result_summary(result)}."
        )

    def _segment_needs_review(self, segment: StoredSegment) -> bool:
        if not self.only_unverified:
            return True
        transcript_pending = (
            segment.verification_status in {"not_verified", ""}
            or segment.similarity_score is None
        )
        if transcript_pending:
            return True
        language = self._language_for_segment(segment)
        comparison_language = self._comparison_language(language)
        normalization_current = comparison_normalization_is_current(
            segment.review_metrics_json,
            enabled=comparison_language is not None,
            language=comparison_language or "",
            rules=self.comparison_normalization_rules,
        )
        tail_current = tail_analysis_is_current(
            segment.review_metrics_json,
            enabled=self.tail_analysis_enabled,
            safety_margin_seconds=self.tail_safety_margin_seconds,
            warning_threshold_seconds=self.tail_warning_threshold_seconds,
            failure_threshold_seconds=self.tail_failure_threshold_seconds,
        )
        if not normalization_current or not tail_current:
            return True
        if self.tail_autocut_enabled:
            metrics = parse_review_metrics(segment.review_metrics_json)
            tail = metrics.get("tail_analysis")
            autocut = metrics.get("audio_tail_cut")
            if isinstance(tail, dict):
                try:
                    excess = float(tail.get("excess_tail_seconds"))
                except (TypeError, ValueError):
                    excess = 0.0
                already_rechecked = isinstance(autocut, dict) and bool(
                    autocut.get("whisper_rechecked", False)
                )
                if (
                    excess > self.tail_warning_threshold_seconds
                    and not already_rechecked
                ):
                    return True
        return False

    @staticmethod
    def _result_summary(result: dict[str, object]) -> str:
        summary = (
            f"{float(result['score']):.1f}% similarity, "
            f"status {result['status']}"
        )
        raw_score = result.get("raw_score")
        if (
            raw_score is not None
            and abs(float(raw_score) - float(result["score"])) >= 0.1
        ):
            summary += f" (raw Whisper comparison {float(raw_score):.1f}%)"
        tail = result.get("tail_analysis")
        if isinstance(tail, dict) and bool(tail.get("enabled", False)):
            excess = tail.get("excess_tail_seconds")
            excess_label = "unavailable" if excess is None else f"{float(excess):.2f}s"
            summary += (
                f", tail {excess_label} after margin / "
                f"{float(tail.get('risk_percent', 100.0)):.0f}% risk "
                f"({tail.get('status', 'unavailable')})"
            )
        return summary

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

    @staticmethod
    def _autocut_wav_path(segment: StoredSegment) -> Path:
        current = Path(segment.wav_path)
        base_dir = (
            current.parent
            if current.parent.name == "candidates"
            else current.parent / "candidates"
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return base_dir / f"segment_{segment.sequence_index:04d}_autocut_{stamp}.wav"


class AudioTailCutWorker(QObject):
    log = Signal(str)
    finished = Signal(int, str, float, float)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        store: AudiobookStore,
        segment_id: int,
        output_wav: Path,
        cut_seconds: float,
        ffmpeg_path: str | Path,
    ) -> None:
        super().__init__()
        self.store = store
        self.segment_id = segment_id
        self.output_wav = Path(output_wav)
        self.cut_seconds = float(cut_seconds)
        self.ffmpeg_path = ffmpeg_path
        self._runner: FFmpegRunner | None = None
        self._cancel_requested = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            segment = self.store.get_segment(self.segment_id)
            if segment is None:
                raise FFmpegError("Segment not found.")
            source_wav = Path(segment.wav_path)
            duration_seconds = _wav_duration_seconds(source_wav)
            if not 0.01 < self.cut_seconds < duration_seconds - 0.01:
                raise FFmpegError(
                    "The selected cut point must be inside the audio clip."
                )
            if self._cancel_requested.is_set():
                raise FFmpegCancelled("Audio tail cut cancelled.")
            removed_seconds = duration_seconds - self.cut_seconds
            self.log.emit(
                f"Segment {segment.sequence_index}: cutting {removed_seconds:.2f}s "
                f"of audio at {self.cut_seconds:.2f}s."
            )
            self._runner = FFmpegRunner(find_ffmpeg(self.ffmpeg_path))
            trim_wav_at(
                source_wav,
                self.output_wav,
                self.cut_seconds,
                self.ffmpeg_path,
                runner=self._runner,
            )
            if self._cancel_requested.is_set():
                raise FFmpegCancelled("Audio tail cut cancelled.")
            output_duration = _wav_duration_seconds(self.output_wav)
            self.store.mark_segment_rendered(
                segment.id,
                self.output_wav,
                round(output_duration * 1000),
                0,
            )
            self.finished.emit(
                segment.id,
                str(self.output_wav),
                self.cut_seconds,
                removed_seconds,
            )
        except FFmpegCancelled:
            self.output_wav.unlink(missing_ok=True)
            self.cancelled.emit()
        except (FFmpegError, FileNotFoundError, OSError, ValueError, wave.Error) as exc:
            self.output_wav.unlink(missing_ok=True)
            self.failed.emit(str(exc))
        except Exception as exc:
            self.output_wav.unlink(missing_ok=True)
            traceback.print_exc()
            self.failed.emit(f"Unexpected audio tail cut error: {exc}")
        finally:
            self._runner = None

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        if self._runner is not None:
            self._runner.cancel_current()


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
                subtitle_result = export_audiobook_subtitles(
                    self.store,
                    self.audiobook_id,
                    [output_mp3],
                )
            self.progress.emit(1, 1, "Audiobook rebuilt.")
            self.log.emit(f"Rebuilt audiobook: {output_mp3}")
            if subtitle_result.files:
                self.log.emit(
                    f"Created {len(subtitle_result.files)} subtitle file(s)."
                )
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

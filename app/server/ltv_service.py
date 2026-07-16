from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from app.core.audio_mix import AudioMixSettings, render_audio_mix
from app.core.audio_event_timeline import (
    resolved_audio_clips,
    speech_intervals_for_audiobook,
)
from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
from app.core.audio_library import audio_library_files, library_directory
from app.core.ltv_markup import LTVMarkupParser
from app.core.waveform_preview import probe_audio_duration
from app.core.settings_manager import SettingsManager
from app.tts.base import BaseTTSEngine
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.engine_registry import TTS_ENGINES, create_tts_engine
from app.tts.kokoro_python_manager import KokoroPythonManager
from app.tts.omnivoice_manager import OmniVoiceManager
from app.tts.qwen_manager import QwenManager
from app.tts.voice_gallery_manager import (
    DEFAULT_GALLERY_CATALOG_URL,
    GalleryVoice,
    VoiceGalleryManager,
)
from app.tts.voice_manager import VoiceInfo, VoiceManager
from app.utils.paths import application_root, resolve_app_path
from app.verification.faster_whisper_manager import (
    FasterWhisperManager,
    FasterWhisperVerifier,
)
from app.workers.verification_worker import (
    AudiobookRebuildWorker,
    SegmentVerificationWorker,
)

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]
PipelineCallback = Callable[[AudioPipeline], None]


class LocalText2VoiceService:
    """Headless facade over the same generation pipeline used by the UI."""

    def __init__(
        self,
        settings_manager: SettingsManager | None = None,
        keep_engines_alive: bool = False,
    ) -> None:
        self.settings_manager = settings_manager or SettingsManager()
        self.keep_engines_alive = keep_engines_alive
        self._engine_cache: dict[str, BaseTTSEngine] = {}
        self._engine_lock = threading.RLock()
        self.kokoro_manager = KokoroPythonManager()
        self.chatterbox_manager = ChatterboxManager()
        self.qwen_manager = QwenManager()
        self.omnivoice_manager = OmniVoiceManager()
        self.faster_whisper_manager = FasterWhisperManager()
        self._whisper_verifier: FasterWhisperVerifier | None = None
        gallery_settings = self._settings_dict("voice_gallery")
        self.voice_gallery_manager = VoiceGalleryManager(
            catalog_url=str(
                gallery_settings.get("catalog_url", DEFAULT_GALLERY_CATALOG_URL)
            ),
            local_catalog_path=str(gallery_settings.get("local_catalog_path", "")),
        )
        self.voice_gallery_manager.ensure_seed_loaded()

    @property
    def settings(self) -> dict[str, Any]:
        return self.settings_manager.settings

    def refresh_settings(self) -> dict[str, Any]:
        """Reload UI-owned defaults before serving a new external request."""

        self.settings_manager.settings = self.settings_manager.load()
        return self.settings_manager.settings

    def server_settings(self) -> dict[str, Any]:
        value = self.settings.get("local_server", {})
        return dict(value) if isinstance(value, dict) else {}

    def server_info(self) -> dict[str, Any]:
        self.refresh_settings()
        server_settings = self.server_settings()
        return {
            "name": "LocalText2Voice",
            "version": "1.0.0",
            "description": "Local audiobook and podcast generation server.",
            "host": server_settings.get("host", "127.0.0.1"),
            "port": int(server_settings.get("port", 8765)),
            "engines": self.list_engines(),
            "engine_memory": self.engine_status(),
        }

    def close(self) -> None:
        with self._engine_lock:
            engines = list(self._engine_cache.values())
            self._engine_cache.clear()
        for engine in engines:
            try:
                engine.close()
            except Exception:
                pass
        verifier = self._whisper_verifier
        self._whisper_verifier = None
        if verifier is not None:
            try:
                verifier.close(force=False)
            except Exception:
                pass

    def list_engines(self) -> list[dict[str, Any]]:
        self.refresh_settings()
        memory_status = self.engine_status()
        engines = [
            {
                "id": engine.engine_id,
                "name": engine.display_name,
                "type": "local" if engine.is_local else "remote",
                "installed": self._engine_installed(engine.engine_id),
                "loaded_in_memory": bool(
                    memory_status.get(engine.engine_id, {}).get("loaded", False)
                ),
            }
            for engine in TTS_ENGINES
        ]
        for engine in self._custom_tts_engines():
            key = f"custom:{engine.get('id', '')}"
            engines.append(
                {
                    "id": key,
                    "name": str(engine.get("name") or engine.get("id") or key),
                    "type": str(engine.get("location", "local_http")),
                    "installed": True,
                    "loaded_in_memory": bool(
                        memory_status.get(key, {}).get("loaded", False)
                    ),
                }
            )
        return engines

    def engine_status(self) -> dict[str, dict[str, Any]]:
        with self._engine_lock:
            return {
                engine_id: {
                    "loaded": True,
                    "engine_class": engine.__class__.__name__,
                }
                for engine_id, engine in self._engine_cache.items()
            }

    def preload_engine(
        self,
        engine_id: str | None = None,
        request: dict[str, Any] | None = None,
        log_callback: LogCallback | None = None,
    ) -> dict[str, Any]:
        self.refresh_settings()
        request = dict(request or {})
        engine = str(engine_id or request.get("engine_id") or self.settings.get("tts_engine", "piper"))
        if engine == "kokoro_python":
            engine = "kokoro"
        request["engine_id"] = engine
        voice_config = self._voice_config(engine, request)
        tts_engine = self._get_tts_engine(engine, log_callback or (lambda message: None))
        tts_engine.preload(voice_config)
        return {
            "engine_id": engine,
            "loaded": True,
            "engine_class": tts_engine.__class__.__name__,
        }

    def unload_engine(self, engine_id: str | None = None) -> dict[str, Any]:
        engine = str(engine_id or self.settings.get("tts_engine", "piper"))
        if engine == "kokoro_python":
            engine = "kokoro"
        with self._engine_lock:
            tts_engine = self._engine_cache.pop(engine, None)
        if tts_engine is None:
            return {"engine_id": engine, "loaded": False, "unloaded": False}
        tts_engine.close()
        return {"engine_id": engine, "loaded": False, "unloaded": True}

    def list_background_music(self) -> list[dict[str, Any]]:
        self.refresh_settings()
        selected = self._resolve_music_path("")
        return self._list_audio_library("music", selected=selected)

    def list_sfx(self) -> list[dict[str, Any]]:
        self.refresh_settings()
        return self._list_audio_library("sfx")

    def _list_audio_library(
        self,
        library: str,
        *,
        selected: Path | None = None,
    ) -> list[dict[str, Any]]:
        library_dir = library_directory(self.settings, library)
        rows: list[dict[str, Any]] = []
        for path in audio_library_files(library_dir):
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            relative = path.relative_to(library_dir)
            rows.append(
                {
                    "id": relative.with_suffix("").as_posix(),
                    "name": path.stem,
                    "filename": path.name,
                    "relative_path": relative.as_posix(),
                    "path": str(path),
                    "size_bytes": size,
                    "selected": bool(selected and path.resolve() == selected.resolve()),
                }
            )
        return rows

    def list_voices(
        self,
        engine_id: str | None = None,
        installed_only: bool = True,
    ) -> list[dict[str, Any]]:
        self.refresh_settings()
        engine = engine_id or str(self.settings.get("tts_engine", "piper") or "piper")
        if engine == "kokoro_python":
            engine = "kokoro"
        if engine == "piper":
            return [
                {
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": voice.language,
                    "type": "Piper voice",
                    "installed": True,
                }
                for voice in self._piper_voices()
            ]
        if engine == "kokoro":
            return [
                {
                    "id": voice.voice_id,
                    "name": voice.display_name,
                    "language": voice.lang,
                    "type": "Kokoro voice",
                    "installed": self.kokoro_manager.is_installed(),
                }
                for voice in self.kokoro_manager.list_voices()
            ]
        if engine == "qwen":
            languages = [language.display_name for language in self.qwen_manager.list_languages()]
            return [
                {
                    "id": voice.voice_id,
                    "name": f"{voice.display_name} - {language}",
                    "speaker": voice.voice_id,
                    "language": language,
                    "type": "Qwen speaker",
                    "installed": self.qwen_manager.is_installed(),
                }
                for voice in self.qwen_manager.list_voices()
                for language in languages
            ]
        if engine in {"chatterbox", "omnivoice"}:
            rows = []
            for voice in self.voice_gallery_manager.list_voices(engine):
                installed = self.voice_gallery_manager.is_installed(voice)
                if installed_only and not installed:
                    continue
                rows.append(
                    {
                        "id": voice.voice_id,
                        "name": voice.name,
                        "language": voice.language_name or voice.language,
                        "type": voice.voice_type,
                        "installed": installed,
                        "gender": voice.gender,
                        "age_style": voice.age_style,
                        "voice_style": voice.voice_style,
                        "short_description": voice.short_description,
                    }
                )
            return rows
        if engine.startswith("custom:"):
            custom = self._custom_engine_by_key(engine)
            if custom is None:
                return []
            voice = str(custom.get("voice", "")).strip()
            return [
                {
                    "id": voice or engine,
                    "name": voice or str(custom.get("name") or engine),
                    "language": str(custom.get("language", "")),
                    "type": "Custom engine voice",
                    "installed": True,
                }
            ]
        return []

    def generate_audio(
        self,
        request: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
        log_callback: LogCallback | None = None,
        on_pipeline: PipelineCallback | None = None,
    ) -> dict[str, Any]:
        self.refresh_settings()
        progress = progress_callback or (lambda current, total, message: None)
        log = log_callback or (lambda message: None)
        text = str(request.get("text", "")).strip()
        if not text:
            raise ValueError("Text is required.")

        engine_id = str(request.get("engine_id") or self.settings.get("tts_engine", "piper"))
        if engine_id == "kokoro_python":
            engine_id = "kokoro"
        voice_config = self._voice_config(engine_id, request)
        requested_options = self._generation_options(request, voice_config)
        play_required = any(
            event.type == "play" for event in LTVMarkupParser.parse(text).events
        )
        desktop_ui = str(request.get("client", "")).casefold() == "desktop_ui"
        review_enabled = self._review_enabled(request)
        if play_required and not desktop_ui:
            if not self.faster_whisper_manager.is_installed():
                raise ValueError(
                    "PLAY markup requires Faster Whisper small for mandatory word "
                    "timestamp alignment. Install it from Settings > Review."
                )
            review_enabled = True
        options = (
            replace(
                requested_options,
                podcast_enabled=False,
                background_enabled=False,
            )
            if review_enabled and requested_options.podcast_enabled
            else requested_options
        )
        engine = self._get_tts_engine(engine_id, log)
        pipeline = AudioPipeline(
            engine,
            progress_callback=progress,
            log_callback=log,
            close_engine_on_finish=not self.keep_engines_alive,
            audiobook_store=AudiobookStore(),
        )
        if on_pipeline is not None:
            on_pipeline(pipeline)
        outputs = pipeline.generate(text, options)
        active_audiobook = getattr(pipeline, "_active_audiobook", None)
        audiobook_id = getattr(active_audiobook, "id", None)
        review_summary: dict[str, Any] | None = None
        if review_enabled and audiobook_id is not None:
            review_summary, rebuilt_clean = self._run_automatic_review(
                audiobook_id,
                requested_options,
                voice_config,
                progress,
                log,
                on_pipeline,
            )
            if rebuilt_clean is not None:
                outputs = [rebuilt_clean] + [
                    path for path in outputs if path.stem.endswith("_mix")
                ]
        if (
            review_enabled
            and (requested_options.podcast_enabled or play_required)
            and audiobook_id is not None
        ):
            clean_path = next(
                (path for path in outputs if not path.stem.endswith("_mix")),
                None,
            )
            if clean_path is not None:
                mix_path = self._render_reviewed_mix(
                    audiobook_id,
                    clean_path,
                    requested_options,
                    log,
                )
                outputs = [clean_path, mix_path]
        clean_outputs = [path for path in outputs if not path.stem.endswith("_mix")]
        mix_outputs = [path for path in outputs if path.stem.endswith("_mix")]
        project: dict[str, Any] = {}
        project_dir = getattr(active_audiobook, "project_dir", None)
        if audiobook_id is not None and project_dir is not None:
            resolved_project_dir = Path(project_dir).resolve()
            project = {
                "audiobook_id": audiobook_id,
                "uuid": str(getattr(active_audiobook, "uuid", "") or ""),
                "title": str(
                    getattr(active_audiobook, "title", "")
                    or requested_options.metadata.get("title", "Audiobook")
                ),
                "project_dir": str(resolved_project_dir),
                "manifest_path": str(
                    resolved_project_dir / PROJECT_MANIFEST_NAME
                ),
            }
        return {
            "audiobook_id": audiobook_id,
            "outputs": [str(path) for path in outputs],
            "clean_mp3": str(clean_outputs[0]) if clean_outputs else "",
            "mix_mp3": str(mix_outputs[0]) if mix_outputs else "",
            "review": review_summary or {"enabled": False},
            "project": project,
            "project_edit_message": (
                "Puedes editar los parametros de este audiolibro desde "
                f"LocalText2Voice abriendo el proyecto '{project['title']}'."
                if project
                else ""
            ),
        }

    def _review_enabled(self, request: dict[str, Any]) -> bool:
        policy = str(request.get("review_policy", "default") or "default").casefold()
        if policy in {"off", "none", "disabled", "false", "0"}:
            return False
        review = self._settings_dict("review")
        if policy in {"on", "enabled", "true", "1", "required"}:
            enabled = True
        else:
            enabled = bool(review.get("enabled", False)) and bool(
                review.get("auto_verify_after_generation", False)
            )
        if enabled and not self.faster_whisper_manager.is_installed():
            raise ValueError(
                "Automatic review is enabled, but Faster Whisper small is not installed. "
                "Install it from Settings > Review or use review_policy='off'."
            )
        return enabled

    def _run_automatic_review(
        self,
        audiobook_id: int,
        options: AudioGenerationOptions,
        voice_config: dict[str, Any],
        progress: ProgressCallback,
        log: LogCallback,
        on_operation: PipelineCallback | None,
    ) -> tuple[dict[str, Any], Path | None]:
        review = self._settings_dict("review")
        verifier: FasterWhisperVerifier | None = None
        if self.keep_engines_alive:
            if self._whisper_verifier is None:
                self._whisper_verifier = FasterWhisperVerifier(
                    self.faster_whisper_manager
                )
            verifier = self._whisper_verifier
        engine_id = str(voice_config.get("engine", "piper"))
        shared_tts_engines = (
            {engine_id: self._get_tts_engine(engine_id, log)}
            if self.keep_engines_alive
            else None
        )
        worker = SegmentVerificationWorker(
            AudiobookStore(),
            audiobook_id,
            verifier,
            str(review.get("device", "cpu")),
            str(review.get("compute_type", "int8")),
            str(review.get("language", "auto")),
            int(review.get("beam_size", 1)),
            float(review.get("approve_threshold", 92.0)),
            int(review.get("max_retries", 0)),
            resolve_app_path(
                self.settings.get("piper_path", "engines/piper/piper.exe")
            ),
            voice_config,
            True,
            shared_tts_engines,
        )
        errors: list[str] = []
        cancelled: list[bool] = []
        worker.log.connect(log)
        worker.progress.connect(progress)
        worker.failed.connect(errors.append)
        worker.cancelled.connect(lambda: cancelled.append(True))
        if on_operation is not None:
            on_operation(worker)  # type: ignore[arg-type]
        log("Starting automatic Faster Whisper review.")
        worker.run()
        if cancelled:
            raise RuntimeError("Automatic review cancelled.")
        if errors:
            raise RuntimeError(errors[-1])

        store = AudiobookStore()
        segments = store.list_segments(audiobook_id)
        approved = sum(
            1 for segment in segments if segment.verification_status == "approved"
        )
        attention = sum(
            1
            for segment in segments
            if segment.verification_status in {"retry_needed", "review"}
        )
        dirty = sum(1 for segment in segments if segment.needs_rebuild)
        rebuilt_path: Path | None = None
        if dirty:
            log(f"Review changed {dirty} segment(s); rebuilding clean narration.")
            rebuilt_path = self._run_review_rebuild(
                audiobook_id,
                options,
                progress,
                log,
                on_operation,
            )
        summary = {
            "enabled": True,
            "segments": len(segments),
            "approved": approved,
            "needs_attention": attention,
            "rebuilt": rebuilt_path is not None,
        }
        log(
            "Automatic review complete: "
            f"{approved}/{len(segments)} approved, {attention} need attention."
        )
        return summary, rebuilt_path

    def _run_review_rebuild(
        self,
        audiobook_id: int,
        options: AudioGenerationOptions,
        progress: ProgressCallback,
        log: LogCallback,
        on_operation: PipelineCallback | None,
    ) -> Path:
        worker = AudiobookRebuildWorker(
            AudiobookStore(),
            audiobook_id,
            options.output_dir,
            options.ffmpeg_path,
            options,
        )
        outputs: list[str] = []
        errors: list[str] = []
        cancelled: list[bool] = []
        worker.log.connect(log)
        worker.progress.connect(progress)
        worker.finished.connect(outputs.append)
        worker.failed.connect(errors.append)
        worker.cancelled.connect(lambda: cancelled.append(True))
        if on_operation is not None:
            on_operation(worker)  # type: ignore[arg-type]
        worker.run()
        if cancelled:
            raise RuntimeError("Audiobook rebuild cancelled.")
        if errors:
            raise RuntimeError(errors[-1])
        if not outputs:
            raise RuntimeError("Audiobook rebuild did not create an output file.")
        return Path(outputs[-1])

    def _render_reviewed_mix(
        self,
        audiobook_id: int,
        clean_path: Path,
        options: AudioGenerationOptions,
        log: LogCallback,
    ) -> Path:
        mix_path = self._next_mix_path(clean_path)
        settings = AudioMixSettings(
            voice_volume_db=options.voice_volume_db,
            music_volume_db=options.music_volume_db,
            voice_start_offset_ms=options.voice_start_offset_ms,
            music_tail_ms=options.music_tail_ms,
            music_fade_in_seconds=options.music_fade_in_seconds,
            music_fade_out_seconds=options.music_fade_out_seconds,
            ducking_enabled=options.podcast_ducking,
            ducking_strength=options.ducking_strength,
            loop_background=options.background_loop,
            normalize=options.podcast_normalize,
            mp3_bitrate=options.mp3_bitrate,
            markup_music_volume_db=float(
                self.settings.get("markup_music_volume_db", 0.0)
            ),
            ambient_volume_db=float(self.settings.get("ambient_volume_db", 0.0)),
            sfx_volume_db=float(self.settings.get("sfx_volume_db", 0.0)),
            voice_muted=bool(self.settings.get("voice_muted", False)),
            background_music_muted=bool(
                self.settings.get("background_music_muted", False)
            ),
            markup_music_muted=bool(
                self.settings.get("markup_music_muted", False)
            ),
            ambient_muted=bool(self.settings.get("ambient_muted", False)),
            sfx_muted=bool(self.settings.get("sfx_muted", False)),
            solo_track=str(self.settings.get("markup_audio_solo_track", "")),
        )
        music_path = options.background_path if options.background_enabled else None
        store = AudiobookStore()
        audiobook = store.get_audiobook(audiobook_id)
        voice_duration = probe_audio_duration(clean_path, options.ffmpeg_path)
        timeline_clips = resolved_audio_clips(
            store,
            audiobook_id,
            project_duration_ms=max(1, round(voice_duration * 1000)),
        )
        speech_intervals = speech_intervals_for_audiobook(store, audiobook_id)
        log(f"Rendering reviewed podcast mix: {mix_path.name}")
        render_audio_mix(
            clean_path,
            mix_path,
            options.ffmpeg_path,
            settings,
            music_path=music_path,
            voice_duration_seconds=voice_duration,
            metadata=options.metadata,
            timeline_clips=timeline_clips,
            speech_intervals=speech_intervals,
            stem_cache_dir=(
                audiobook.project_dir / "cache" / "stems"
                if audiobook is not None
                else None
            ),
        )
        store.complete_audiobook(audiobook_id, [clean_path, mix_path])
        log(f"Reviewed podcast mix created: {mix_path}")
        return mix_path

    @staticmethod
    def _next_mix_path(clean_path: Path) -> Path:
        candidate = clean_path.with_name(f"{clean_path.stem}_mix.mp3")
        index = 2
        while candidate.exists():
            candidate = clean_path.with_name(f"{clean_path.stem}_{index}_mix.mp3")
            index += 1
        return candidate

    def _get_tts_engine(
        self,
        engine_id: str,
        log_callback: LogCallback,
    ) -> BaseTTSEngine:
        piper_path = resolve_app_path(self.settings.get("piper_path", "engines/piper/piper.exe"))
        if not self.keep_engines_alive:
            engine = create_tts_engine(engine_id, piper_path)
            engine.set_log_callback(log_callback)
            return engine
        with self._engine_lock:
            engine = self._engine_cache.get(engine_id)
            if engine is None:
                engine = create_tts_engine(engine_id, piper_path)
                self._engine_cache[engine_id] = engine
                log_callback(f"Engine host loaded {engine_id} engine into memory.")
            else:
                log_callback(f"Engine host reusing {engine_id} engine from memory.")
            engine.set_log_callback(log_callback)
            return engine

    def _generation_options(
        self,
        request: dict[str, Any],
        voice_config: dict[str, Any],
    ) -> AudioGenerationOptions:
        engine_id = str(voice_config.get("engine", "piper"))
        output_dir = self._resolve_output_dir(request.get("output_dir"))
        metadata = dict(self.settings.get("metadata", {}))
        title = str(request.get("title") or metadata.get("title") or "Audiobook").strip()
        metadata["title"] = title or "Audiobook"
        mix_policy = str(request.get("mix_policy", "always")).strip().casefold()
        render_mix = mix_policy not in {"off", "none", "clean_only", "clean-only"}
        background_path = self._resolve_music_path(request.get("background_music", ""))
        project_settings = copy.deepcopy(self.settings)
        project_settings["server_request"] = {
            key: value
            for key, value in request.items()
            if key not in {"text"}
        }
        return AudioGenerationOptions(
            output_dir=output_dir,
            voice_config=voice_config,
            ffmpeg_path=self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"),
            split_mode=str(request.get("split_mode") or self.settings.get("split_mode", "safe_chunks")),
            export_mode=str(request.get("export_mode") or self.settings.get("export_mode", "single")),
            chunk_size=self._chunk_size(engine_id, request),
            pause_between_blocks_ms=int(self.settings.get("pause_between_blocks_ms", 350)),
            pause_between_chapters_ms=int(self.settings.get("pause_between_chapters_ms", 900)),
            paragraph_pause_min_ms=int(self.settings.get("paragraph_pause_min_ms", 450)),
            paragraph_pause_max_ms=int(self.settings.get("paragraph_pause_max_ms", 900)),
            adaptive_paragraph_pause=bool(self.settings.get("adaptive_paragraph_pause", True)),
            paragraph_length_reference_chars=int(self.settings.get("paragraph_length_reference_chars", 600)),
            paragraph_length_extra_ms=int(self.settings.get("paragraph_length_extra_ms", 650)),
            periodic_pause_every_paragraphs=int(self.settings.get("periodic_pause_every_paragraphs", 5)),
            periodic_pause_min_ms=int(self.settings.get("periodic_pause_min_ms", 350)),
            periodic_pause_max_ms=int(self.settings.get("periodic_pause_max_ms", 750)),
            normalize_audio=bool(self.settings.get("normalize_audio", False)),
            podcast_enabled=render_mix,
            background_enabled=render_mix and bool(self.settings.get("background_enabled", True)) and background_path is not None,
            background_path=background_path,
            background_loop=bool(self.settings.get("background_loop", True)),
            background_volume_percent=int(self.settings.get("background_volume_percent", 45)),
            voice_volume_db=float(self.settings.get("voice_volume_db", 0.0)),
            music_volume_db=float(self.settings.get("music_volume_db", -7.0)),
            voice_start_offset_ms=int(self.settings.get("voice_start_offset_ms", 2000)),
            music_tail_ms=int(self.settings.get("music_tail_ms", 2000)),
            music_fade_in_seconds=float(self.settings.get("music_fade_in_seconds", 1.0)),
            music_fade_out_seconds=float(self.settings.get("music_fade_out_seconds", 1.0)),
            podcast_gap_ms=int(self.settings.get("podcast_gap_ms", 500)),
            podcast_normalize=bool(self.settings.get("podcast_normalize", True)),
            podcast_ducking=bool(self.settings.get("podcast_ducking", True)),
            ducking_strength=str(self.settings.get("ducking_strength", "low")),
            mp3_bitrate=str(self.settings.get("mp3_bitrate", "128k")),
            metadata=metadata,
            project_audiobook_id=self._project_audiobook_id(request),
            project_settings=project_settings,
        )

    @staticmethod
    def _project_audiobook_id(request: dict[str, Any]) -> int | None:
        value = request.get("project_audiobook_id")
        if value in {None, ""}:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError("project_audiobook_id must be an integer.")

    def _voice_config(self, engine_id: str, request: dict[str, Any]) -> dict[str, Any]:
        speed = float(request.get("speed") or self.settings.get("speed", 1.0))
        voice_hint = str(request.get("voice") or request.get("voice_id") or "").strip()
        language_hint = str(request.get("language") or request.get("lang") or "").strip()
        if engine_id == "piper":
            selected = self._match_piper_voice(voice_hint or str(self.settings.get("voice_id", "")))
            config = selected.as_config(speed) if selected else {"speed": speed}
            config["engine"] = "piper"
            return config
        if engine_id == "kokoro":
            kokoro = self._settings_dict("kokoro")
            selected = self._match_named(
                voice_hint or str(kokoro.get("voice", "af_heart")),
                [
                    {
                        "id": voice.voice_id,
                        "name": voice.display_name,
                        "language": voice.lang,
                    }
                    for voice in self.kokoro_manager.list_voices()
                ],
            )
            voice_id = str(selected.get("id") if selected else kokoro.get("voice", "af_heart"))
            return {
                "engine": "kokoro",
                "speed": speed,
                "voice": voice_id,
                "lang": language_hint or str(selected.get("language") if selected else kokoro.get("lang", "en-us")),
                "provider": str(kokoro.get("provider", "auto")),
                "model_path": str(self.kokoro_manager.model_path_for_provider("auto")),
            }
        if engine_id == "chatterbox":
            chatterbox = self._settings_dict("chatterbox")
            gallery_voice = self._match_gallery_voice("chatterbox", voice_hint, language_hint)
            reference_audio = str(
                request.get("reference_audio_path")
                or (gallery_voice.installed_path if gallery_voice else "")
                or chatterbox.get("reference_audio_path", "")
            )
            return {
                "engine": "chatterbox",
                "speed": speed,
                "model": str(chatterbox.get("model", "multilingual_v3")),
                "language": language_hint or str(chatterbox.get("language", "en")),
                "device": str(chatterbox.get("device", "auto")),
                "reference_audio_path": reference_audio,
                "exaggeration": float(chatterbox.get("exaggeration", 0.5)),
                "cfg_weight": float(chatterbox.get("cfg_weight", 0.5)),
                "voice": gallery_voice.name if gallery_voice else voice_hint,
            }
        if engine_id == "qwen":
            qwen = self._settings_dict("qwen")
            speaker = str(qwen.get("speaker", "Serena"))
            language = str(qwen.get("language", "Spanish"))
            if voice_hint:
                parsed_speaker, parsed_language = self._parse_qwen_voice_hint(voice_hint)
                speaker = parsed_speaker or speaker
                language = language_hint or parsed_language or language
            elif language_hint:
                language = language_hint
            return {
                "engine": "qwen",
                "speed": speed,
                "model": str(qwen.get("model", "custom_voice_0_6b")),
                "language": language,
                "speaker": speaker,
                "device": str(qwen.get("device", "auto")),
                "dtype": str(qwen.get("dtype", "auto")),
                "instruct": str(request.get("instruct") or qwen.get("instruct", "")),
            }
        if engine_id == "omnivoice":
            omnivoice = self._settings_dict("omnivoice")
            gallery_voice = self._match_gallery_voice("omnivoice", voice_hint, language_hint)
            reference_audio = str(
                request.get("reference_audio_path")
                or (gallery_voice.installed_path if gallery_voice else "")
                or omnivoice.get("reference_audio_path", "")
            )
            reference_text = str(
                request.get("reference_text")
                or (gallery_voice.ref_text if gallery_voice else "")
                or omnivoice.get("reference_text", "")
            )
            return {
                "engine": "omnivoice",
                "speed": speed,
                "model": str(omnivoice.get("model", "omnivoice")),
                "mode": "clone",
                "language": language_hint or str(omnivoice.get("language", "auto")),
                "device": str(omnivoice.get("device", "auto")),
                "dtype": str(omnivoice.get("dtype", "auto")),
                "instruct": "",
                "reference_audio_path": reference_audio,
                "reference_text": reference_text,
                "num_step": int(omnivoice.get("num_step", 32)),
                "duration": float(omnivoice.get("duration", 0.0)),
                "voice": gallery_voice.name if gallery_voice else voice_hint,
            }
        if engine_id in {"openai", "elevenlabs", "gemini", "azure"}:
            config = dict(self._settings_dict("api_tts").get(engine_id, {}))
            config["engine"] = engine_id
            config["speed"] = speed
            if voice_hint:
                config["voice"] = voice_hint
            if language_hint:
                config["language"] = language_hint
            return config
        if engine_id.startswith("custom:"):
            engine = self._custom_engine_by_key(engine_id)
            config = dict(engine or {})
            config["engine"] = engine_id
            config["speed"] = speed
            config["voice"] = voice_hint or str(config.get("voice", ""))
            config["language"] = language_hint or str(config.get("language", ""))
            config["ffmpeg_path"] = self.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe")
            config.setdefault("name", engine_id)
            return config
        raise ValueError(f"Unknown TTS engine: {engine_id}")

    def _piper_voices(self) -> list[VoiceInfo]:
        return VoiceManager(resolve_app_path("voices")).discover()

    def _match_piper_voice(self, hint: str) -> VoiceInfo | None:
        voices = self._piper_voices()
        if not voices:
            return None
        if not hint:
            return voices[0]
        wanted = hint.casefold()
        for voice in voices:
            if voice.voice_id.casefold() == wanted or voice.display_name.casefold() == wanted:
                return voice
        return self._closest_item(
            hint,
            voices,
            lambda voice: f"{voice.voice_id} {voice.display_name} {voice.language}",
        )

    def _match_gallery_voice(
        self,
        engine: str,
        voice_hint: str,
        language_hint: str = "",
    ) -> GalleryVoice | None:
        voices = self.voice_gallery_manager.list_voices(engine)
        installed = [voice for voice in voices if self.voice_gallery_manager.is_installed(voice)]
        candidates = installed or voices
        if not candidates or not voice_hint:
            return None
        language_hint = self._normalize_token(language_hint)
        filtered = candidates
        if language_hint:
            language_filtered = [
                voice
                for voice in candidates
                if language_hint
                in self._normalize_token(f"{voice.language} {voice.language_name}")
            ]
            if language_filtered:
                filtered = language_filtered
        return self._closest_item(
            voice_hint,
            filtered,
            lambda voice: f"{voice.name} {voice.voice_id} {voice.language} {voice.language_name}",
        )

    def _match_named(
        self,
        hint: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not rows:
            return None
        if not hint:
            return rows[0]
        return self._closest_item(
            hint,
            rows,
            lambda row: f"{row.get('id', '')} {row.get('name', '')} {row.get('language', '')}",
        )

    @classmethod
    def _closest_item(cls, hint: str, items: list[Any], label: Callable[[Any], str]) -> Any | None:
        if not items:
            return None
        wanted = cls._normalize_token(hint)
        exact = [item for item in items if wanted == cls._normalize_token(label(item))]
        if exact:
            return exact[0]
        contains = [item for item in items if wanted and wanted in cls._normalize_token(label(item))]
        if contains:
            return contains[0]
        wanted_parts = [part for part in wanted.split() if part]
        if wanted_parts:
            all_parts = [
                item
                for item in items
                if all(part in cls._normalize_token(label(item)) for part in wanted_parts)
            ]
            if all_parts:
                return all_parts[0]
        return max(items, key=lambda item: cls._similarity(wanted, cls._normalize_token(label(item))))

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        left_parts = set(left.split())
        right_parts = set(right.split())
        overlap = len(left_parts & right_parts) / max(1, len(left_parts | right_parts))
        if left in right:
            overlap += 0.5
        return overlap

    @staticmethod
    def _normalize_token(value: object) -> str:
        text = str(value or "").casefold()
        text = re.sub(r"[\u2010-\u2015_-]+", " ", text)
        text = re.sub(r"[^a-z0-9áéíóúüñçàèìòùäöß\u4e00-\u9fff]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _parse_qwen_voice_hint(self, voice_hint: str) -> tuple[str, str]:
        language_names = {language.display_name.casefold(): language.display_name for language in self.qwen_manager.list_languages()}
        speaker_names = {
            voice.display_name.casefold(): voice.voice_id
            for voice in self.qwen_manager.list_voices()
        }
        normalized = voice_hint.replace("–", "-").replace("—", "-")
        parts = [part.strip() for part in normalized.split("-") if part.strip()]
        speaker_hint = parts[0] if parts else voice_hint
        language_hint = parts[1] if len(parts) > 1 else ""
        speaker = self._closest_item(
            speaker_hint,
            list(speaker_names.items()),
            lambda item: item[0],
        )
        language = self._closest_item(
            language_hint,
            list(language_names.items()),
            lambda item: item[0],
        ) if language_hint else None
        return (
            str(speaker[1]) if speaker else "",
            str(language[1]) if language else "",
        )

    def _resolve_output_dir(self, value: object) -> Path:
        raw = str(value or self.settings.get("output_dir", "output") or "output")
        path = Path(raw)
        if not path.is_absolute():
            path = resolve_app_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_music_path(self, value: object) -> Path | None:
        raw = str(value or "").strip()
        if not raw:
            raw = str(self.settings.get("background_path", "") or "music/background/relax1.mp3")
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = resolve_app_path(candidate)
            if candidate.is_file():
                return candidate
        music_dir = library_directory(self.settings, "music")
        wanted = self._normalize_token(raw)
        for path in audio_library_files(music_dir):
            if wanted in {self._normalize_token(path.stem), self._normalize_token(path.name)}:
                return path
        return None

    def _chunk_size(self, engine_id: str, request: dict[str, Any]) -> int:
        try:
            requested = int(request.get("chunk_size", 0) or 0)
            if requested > 0:
                return requested
        except (TypeError, ValueError):
            pass
        engine_sizes = self.settings.get("engine_chunk_sizes", {})
        if isinstance(engine_sizes, dict):
            try:
                value = int(engine_sizes.get(engine_id, 0) or 0)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        try:
            return max(1, int(self.settings.get("chunk_size", 2500)))
        except (TypeError, ValueError):
            return 2500

    def _engine_installed(self, engine_id: str) -> bool | None:
        if engine_id == "piper":
            return resolve_app_path(self.settings.get("piper_path", "engines/piper/piper.exe")).is_file()
        if engine_id == "kokoro":
            return self.kokoro_manager.is_installed()
        if engine_id == "chatterbox":
            return self.chatterbox_manager.is_installed()
        if engine_id == "qwen":
            return self.qwen_manager.is_installed()
        if engine_id == "omnivoice":
            return self.omnivoice_manager.is_installed()
        if engine_id in {"openai", "elevenlabs", "gemini", "azure"}:
            return None
        return None

    def _settings_dict(self, key: str) -> dict[str, Any]:
        value = self.settings.get(key, {})
        return dict(value) if isinstance(value, dict) else {}

    def _custom_tts_engines(self) -> list[dict[str, Any]]:
        value = self.settings.get("custom_tts_engines", [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _custom_engine_by_key(self, engine_key: str) -> dict[str, Any] | None:
        if not engine_key.startswith("custom:"):
            return None
        wanted = engine_key.split(":", 1)[1]
        for engine in self._custom_tts_engines():
            if str(engine.get("id", "")) == wanted:
                return engine
        return None


def public_settings_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    """Return settings with secrets removed for tool responses."""

    snapshot = copy.deepcopy(settings)
    server = snapshot.get("local_server")
    if isinstance(server, dict) and server.get("auth_token"):
        server["auth_token"] = "***"
    api_tts = snapshot.get("api_tts")
    if isinstance(api_tts, dict):
        for provider in api_tts.values():
            if isinstance(provider, dict) and provider.get("api_key"):
                provider["api_key"] = "***"
    for custom in snapshot.get("custom_tts_engines", []) or []:
        if isinstance(custom, dict) and custom.get("api_key"):
            custom["api_key"] = "***"
    return json.loads(json.dumps(snapshot, default=str))

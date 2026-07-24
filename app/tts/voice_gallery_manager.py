from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from app.utils.paths import application_root, resolve_large_asset_path, voice_gallery_root
from app.utils.ffmpeg_utils import FFmpegError, FFmpegRunner, find_ffmpeg


class VoiceGalleryError(RuntimeError):
    pass


class VoiceGalleryCancelled(VoiceGalleryError):
    pass


VoiceGalleryProgress = Callable[[int, int, str], None]


DEFAULT_GALLERY_CATALOG_URL = (
    "https://raw.githubusercontent.com/estebanstifli/"
    "LocalText2Voice-VoiceGallery/main/catalog.json"
)


@dataclass(frozen=True)
class GalleryVoice:
    voice_id: str
    engine: str
    name: str
    language: str
    language_name: str
    voice_type: str
    install_type: str
    preview_url: str = ""
    preview_path: str = ""
    ref_audio_url: str = ""
    ref_audio_path: str = ""
    ref_text: str = ""
    engine_voice_id: str = ""
    speaker_id: str = ""
    model_id: str = ""
    short_description: str = ""
    gender: str = ""
    age_style: str = ""
    voice_style: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None
    installed_path: str = ""
    installed_at: str = ""

    @property
    def is_reference_audio(self) -> bool:
        return self.install_type == "reference_audio"

    @property
    def is_builtin(self) -> bool:
        return self.install_type == "engine_builtin"


class VoiceGalleryManager:
    """SQLite-backed catalog for previewable/installable voices."""

    DB_FILENAME = "voice-gallery.sqlite3"
    REFERENCE_MIN_SECONDS = 3.0
    REFERENCE_MAX_SECONDS = 20.0
    REFERENCE_SAMPLE_RATE = 24000

    def __init__(
        self,
        db_path: Path | None = None,
        files_root: Path | None = None,
        catalog_url: str = DEFAULT_GALLERY_CATALOG_URL,
        local_catalog_path: str | Path = "",
        timeout_seconds: int = 30,
    ) -> None:
        self.files_root = files_root or voice_gallery_root()
        self.db_path = db_path or self.files_root / self.DB_FILENAME
        self.catalog_url = catalog_url
        self.local_catalog_path = Path(local_catalog_path) if local_catalog_path else None
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._ensure_schema()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def ensure_seed_loaded(self) -> None:
        if self.count_voices() > 0:
            return
        source = self._preferred_catalog_source(allow_remote=False)
        if source is None:
            return
        try:
            self.sync(source=source)
        except VoiceGalleryError:
            return

    def count_voices(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM voice_gallery_voices").fetchone()[0]
            )

    def sync(
        self,
        source: str | Path | None = None,
        progress_callback: VoiceGalleryProgress | None = None,
    ) -> int:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        resolved_source = source or self._preferred_catalog_source(allow_remote=True)
        if resolved_source is None:
            raise VoiceGalleryError("No voice gallery catalog source is configured.")
        progress(0, 1, "Loading voice gallery catalog...")
        catalog, catalog_base = self._load_json_with_base(resolved_source)
        index_paths = catalog.get("indexes", [])
        direct_voice_paths = catalog.get("voices", [])
        if not isinstance(index_paths, list) or not isinstance(direct_voice_paths, list):
            raise VoiceGalleryError("Voice gallery catalog has an invalid format.")

        voice_documents: list[dict[str, Any]] = []
        work_items = list(index_paths)
        total = max(1, len(work_items) + len(direct_voice_paths))
        for index, index_path in enumerate(work_items, start=1):
            self._check_cancelled()
            progress(index, total, f"Loading voice gallery index: {index_path}")
            index_doc, index_base = self._load_json_with_base(index_path, catalog_base)
            paths = index_doc.get("voices", [])
            if not isinstance(paths, list):
                raise VoiceGalleryError(f"Voice gallery index has invalid voices: {index_path}")
            index_compatible_engines = index_doc.get("compatible_engines", [])
            if not isinstance(index_compatible_engines, list):
                index_compatible_engines = []
            for voice_path in paths:
                self._check_cancelled()
                voice_doc, voice_base = self._load_json_with_base(voice_path, index_base)
                voice_doc["_source_base"] = str(voice_base)
                if (
                    index_compatible_engines
                    and "compatible_engines" not in voice_doc
                ):
                    voice_doc["compatible_engines"] = list(index_compatible_engines)
                voice_documents.append(voice_doc)

        for voice_path in direct_voice_paths:
            self._check_cancelled()
            voice_doc, voice_base = self._load_json_with_base(voice_path, catalog_base)
            voice_doc["_source_base"] = str(voice_base)
            voice_documents.append(voice_doc)

        progress(total, total, f"Saving {len(voice_documents)} gallery voice(s)...")
        self._replace_catalog(self._expand_compatible_documents(voice_documents))
        self._set_meta("last_sync_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        return len(voice_documents)

    def list_voices(self, engine: str | None = None) -> list[GalleryVoice]:
        query = "SELECT * FROM voice_gallery_voices"
        params: tuple[object, ...] = ()
        if engine:
            query += " WHERE engine = ?"
            params = (engine,)
        query += " ORDER BY name COLLATE NOCASE, language_name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_voice(row) for row in rows]

    def get_voice(self, voice_id: str) -> GalleryVoice | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_gallery_voices WHERE id = ?",
                (voice_id,),
            ).fetchone()
        return self._row_to_voice(row) if row is not None else None

    def is_installed(self, voice: GalleryVoice) -> bool:
        if voice.is_builtin:
            return True
        if voice.installed_path:
            return Path(voice.installed_path).is_file()
        return False

    def preview_source(self, voice: GalleryVoice) -> str:
        for candidate in (voice.preview_path, voice.installed_path, voice.ref_audio_path):
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return voice.preview_url or voice.ref_audio_url

    def install(
        self,
        voice: GalleryVoice,
        progress_callback: VoiceGalleryProgress | None = None,
    ) -> Path | None:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        if voice.is_builtin:
            audio_path = self.ensure_voice_audio(voice, progress_callback)
            if audio_path is None:
                self._mark_installed(voice.voice_id, "")
            progress(1, 1, f"{voice.name} is built into the selected engine.")
            return audio_path
        if not voice.is_reference_audio:
            raise VoiceGalleryError(f"Unsupported gallery install type: {voice.install_type}")

        return self.ensure_voice_audio(voice, progress_callback)

    def ensure_voice_audio(
        self,
        voice: GalleryVoice,
        progress_callback: VoiceGalleryProgress | None = None,
    ) -> Path | None:
        """Materialize a gallery voice audio asset locally.

        Built-in voices can still provide a remote preview file. Optional engines
        such as OmniVoice can reuse that preview as a cloning reference without
        making the whole voice an installable model.
        """
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        if voice.installed_path and Path(voice.installed_path).is_file():
            return Path(voice.installed_path)
        source = voice.ref_audio_url or voice.ref_audio_path or voice.preview_url or voice.preview_path
        if not source:
            if voice.is_builtin:
                return None
            raise VoiceGalleryError(f"{voice.name} does not define a downloadable reference audio.")
        extension = Path(urllib.parse.urlparse(source).path).suffix.lower() or ".wav"
        destination_dir = self.files_root / "installed" / self._safe_part(voice.engine) / self._safe_part(voice.voice_id)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"reference{extension}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        self._copy_or_download(source, temporary, progress, f"Downloading {voice.name}...")
        temporary.replace(destination)
        self._mark_installed(voice.voice_id, str(destination))
        progress(1, 1, f"Installed {voice.name}.")
        return destination

    def uninstall(self, voice: GalleryVoice) -> None:
        if voice.installed_path:
            path = Path(voice.installed_path)
            if path.is_file():
                path.unlink()
            self._prune_empty(path.parent)
        self._mark_installed(voice.voice_id, "")

    def import_reference_voice(
        self,
        engine: str,
        source: Path,
        name: str = "",
        language: str = "reference",
        language_name: str = "",
        ref_text: str = "",
        short_description: str = "",
        gender: str = "",
        age_style: str = "",
        voice_style: str = "",
        tags: list[str] | tuple[str, ...] | None = None,
        ffmpeg_path: str | Path = "ffmpeg/ffmpeg.exe",
        min_duration_seconds: float | None = None,
        max_duration_seconds: float | None = None,
    ) -> GalleryVoice:
        if not source.is_file():
            raise VoiceGalleryError(f"Reference audio file not found: {source}")
        if source.suffix.lower() not in {".wav", ".mp3"}:
            raise VoiceGalleryError("Reference audio must be a WAV or MP3 file.")
        display_name = name.strip() or source.stem
        digest = hashlib.sha1(
            f"{engine}:{display_name}:{source.stat().st_size}:{source.name}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:12]
        voice_id = f"{engine}_imported_{digest}"
        destination_dir = self.files_root / "imported" / self._safe_part(engine)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = self._unique_destination(
            destination_dir / f"{self._safe_part(display_name)}.wav"
        )
        temporary = destination.with_suffix(destination.suffix + ".tmp.wav")
        minimum = (
            self.REFERENCE_MIN_SECONDS
            if min_duration_seconds is None
            else float(min_duration_seconds)
        )
        maximum = (
            self.REFERENCE_MAX_SECONDS
            if max_duration_seconds is None
            else float(max_duration_seconds)
        )
        duration_seconds = self._normalize_reference_audio(
            source,
            temporary,
            ffmpeg_path,
            minimum,
            maximum,
        )
        temporary.replace(destination)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        normalized_language = language.strip() or "reference"
        normalized_language_name = (
            language_name.strip()
            or (normalized_language.title() if normalized_language != "reference" else "Reference")
        )
        normalized_tags = [
            str(tag).strip()
            for tag in (tags or ["imported", engine, "reference"])
            if str(tag).strip()
        ]
        metadata = {
            "source": "user_import",
            "original_file": str(source),
            "normalized_format": (
                f"wav pcm_s16le mono {self.REFERENCE_SAMPLE_RATE}hz"
            ),
            "duration_seconds": duration_seconds,
            "duration_min_seconds": minimum,
            "duration_max_seconds": maximum,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO voice_gallery_voices (
                    id, engine, name, language, language_name, voice_type,
                    install_type, preview_url, preview_path, ref_audio_url,
                    ref_audio_path, ref_text, engine_voice_id, speaker_id,
                    model_id, short_description, gender, age_style, voice_style,
                    tags_json, metadata_json, source_base,
                    installed_path, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    voice_id,
                    engine,
                    display_name,
                    normalized_language,
                    normalized_language_name,
                    "Imported reference",
                    "reference_audio",
                    str(destination),
                    str(destination),
                    "",
                    str(destination),
                    ref_text,
                    "",
                    "",
                    "",
                    short_description.strip(),
                    gender.strip(),
                    age_style.strip(),
                    voice_style.strip(),
                    json.dumps(normalized_tags, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    "",
                    str(destination),
                    now,
                    now,
                ),
            )
        voice = self.get_voice(voice_id)
        if voice is None:
            raise VoiceGalleryError("Imported voice could not be saved.")
        return voice

    def _normalize_reference_audio(
        self,
        source: Path,
        destination: Path,
        ffmpeg_path: str | Path,
        minimum_seconds: float,
        maximum_seconds: float,
    ) -> float:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._remove_path(destination)
        if source.suffix.lower() == ".wav" and self._wav_matches_reference_format(source):
            shutil.copy2(source, destination)
        else:
            try:
                runner = FFmpegRunner(find_ffmpeg(ffmpeg_path))
                runner.run(
                    [
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        str(self.REFERENCE_SAMPLE_RATE),
                        "-sample_fmt",
                        "s16",
                        "-acodec",
                        "pcm_s16le",
                        str(destination),
                    ]
                )
            except FFmpegError as exc:
                self._remove_path(destination)
                raise VoiceGalleryError(
                    "Could not normalize reference audio with FFmpeg: "
                    f"{exc}"
                ) from exc

        duration = self._wav_duration_seconds(destination)
        if duration < minimum_seconds or duration > maximum_seconds:
            self._remove_path(destination)
            raise VoiceGalleryError(
                "Reference audio duration must be between "
                f"{minimum_seconds:.0f} and {maximum_seconds:.0f} seconds. "
                f"Selected audio is {duration:.1f} seconds."
            )
        return round(duration, 3)

    @classmethod
    def _wav_matches_reference_format(cls, path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as audio:
                return (
                    audio.getnchannels() == 1
                    and audio.getsampwidth() == 2
                    and audio.getframerate() == cls.REFERENCE_SAMPLE_RATE
                    and audio.getnframes() > 0
                )
        except (OSError, wave.Error):
            return False

    @staticmethod
    def _wav_duration_seconds(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as audio:
                frame_rate = audio.getframerate()
                if frame_rate <= 0:
                    raise VoiceGalleryError("Reference WAV has an invalid sample rate.")
                return audio.getnframes() / frame_rate
        except (OSError, wave.Error) as exc:
            raise VoiceGalleryError(f"Reference audio is not a valid WAV file: {exc}") from exc

    def _replace_catalog(self, documents: list[dict[str, Any]]) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._connect() as connection:
            existing = {
                row["id"]: (row["installed_path"], row["installed_at"])
                for row in connection.execute(
                    "SELECT id, installed_path, installed_at FROM voice_gallery_voices"
                ).fetchall()
            }
            incoming_ids = [
                self._required_string(document, "id")
                for document in documents
            ]
            if incoming_ids:
                placeholders = ", ".join("?" for _ in incoming_ids)
                connection.execute(
                    "DELETE FROM voice_gallery_voices "
                    f"WHERE id NOT IN ({placeholders}) "
                    "AND metadata_json NOT LIKE '%\"source\": \"user_import\"%'",
                    tuple(incoming_ids),
                )
            else:
                connection.execute(
                    "DELETE FROM voice_gallery_voices "
                    "WHERE metadata_json NOT LIKE '%\"source\": \"user_import\"%'"
                )
            for doc in documents:
                voice_id = self._required_string(doc, "id")
                installed_path, installed_at = existing.get(voice_id, ("", ""))
                engine = self._required_string(doc, "engine")
                source_base = str(doc.get("_source_base", ""))
                preview_url, preview_path = self._resolve_asset(doc.get("preview_audio", ""), source_base)
                ref_audio_url, ref_audio_path = self._resolve_asset(doc.get("ref_audio", ""), source_base)
                tags = doc.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                metadata = {
                    key: value
                    for key, value in doc.items()
                    if key
                    not in {
                        "id",
                        "engine",
                        "name",
                        "language",
                        "language_name",
                        "type",
                        "install_type",
                        "preview_audio",
                        "ref_audio",
                        "ref_text",
                        "engine_voice_id",
                        "speaker_id",
                        "model_id",
                        "short_description",
                        "gender",
                        "age_style",
                        "voice_style",
                        "tags",
                        "compatible_engines",
                        "_source_base",
                    }
                }
                connection.execute(
                    """
                    INSERT OR REPLACE INTO voice_gallery_voices (
                        id, engine, name, language, language_name, voice_type,
                        install_type, preview_url, preview_path, ref_audio_url,
                        ref_audio_path, ref_text, engine_voice_id, speaker_id,
                        model_id, short_description, gender, age_style, voice_style,
                        tags_json, metadata_json, source_base,
                        installed_path, installed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        voice_id,
                        engine,
                        str(doc.get("name", voice_id)),
                        str(doc.get("language", "")),
                        str(doc.get("language_name", doc.get("language", ""))),
                        str(doc.get("type", "Gallery voice")),
                        str(doc.get("install_type", "engine_builtin")),
                        preview_url,
                        preview_path,
                        ref_audio_url,
                        ref_audio_path,
                        str(doc.get("ref_text", "")),
                        str(doc.get("engine_voice_id", "")),
                        str(doc.get("speaker_id", "")),
                        str(doc.get("model_id", "")),
                        str(doc.get("short_description", "")),
                        str(doc.get("gender", "")),
                        str(doc.get("age_style", "")),
                        str(doc.get("voice_style", "")),
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False),
                        source_base,
                        installed_path,
                        installed_at,
                        now,
                    ),
                )

    def _expand_compatible_documents(
        self,
        documents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        expanded: list[dict[str, Any]] = []
        for document in documents:
            expanded.append(document)
            engine = str(document.get("engine", "")).strip()
            voice_id = str(document.get("id", "")).strip()
            compatible_engines = document.get("compatible_engines", [])
            if not isinstance(compatible_engines, list):
                continue
            for compatible_engine_value in compatible_engines:
                compatible_engine = str(compatible_engine_value).strip()
                if not compatible_engine or compatible_engine == engine:
                    continue
                alias = dict(document)
                alias["id"] = self._compatible_voice_id(
                    compatible_engine,
                    voice_id,
                )
                alias["engine"] = compatible_engine
                alias["compatible_source_engine"] = engine
                alias["compatible_source_id"] = voice_id
                if compatible_engine == "chatterbox":
                    alias["install_type"] = "reference_audio"
                    alias["type"] = "Reference voice"
                    if not str(alias.get("ref_audio", "")).strip():
                        alias["ref_audio"] = str(alias.get("preview_audio", ""))
                    tags = alias.get("tags", [])
                    if isinstance(tags, list) and "chatterbox-compatible" not in tags:
                        alias["tags"] = [*tags, "chatterbox-compatible"]
                expanded.append(alias)
        return expanded

    def _load_json_with_base(
        self,
        source: str | Path,
        base: str | Path | None = None,
    ) -> tuple[dict[str, Any], str]:
        resolved = self._resolve_source(source, base)
        try:
            if self._is_http(resolved):
                request = urllib.request.Request(
                    str(resolved),
                    headers={"Accept": "application/json", "User-Agent": "LocalText2Voice/1.0"},
                )
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.load(response)
                parsed = urllib.parse.urlparse(str(resolved))
                base_url = urllib.parse.urlunparse(parsed._replace(path=str(PurePosixPath(parsed.path).parent)))
                return self._ensure_object(payload), base_url.rstrip("/") + "/"
            path = Path(str(resolved))
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self._ensure_object(payload), str(path.parent)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise VoiceGalleryError(f"Could not load voice gallery JSON: {resolved} ({exc})") from exc

    def _preferred_catalog_source(self, allow_remote: bool) -> str | Path | None:
        candidates: list[Path] = []
        if self.local_catalog_path:
            candidates.append(self.local_catalog_path)
        env_path = Path(str(Path.home()))
        env_value = ""
        try:
            import os

            env_value = os.environ.get("LOCALTEXT2VOICE_VOICE_GALLERY", "")
        except Exception:
            env_value = ""
        if env_value:
            candidates.append(Path(env_value))
        candidates.extend(
            [
                application_root() / "voice_gallery" / "catalog.json",
                application_root().parent / "LocalText2Voice-VoiceGallery" / "catalog.json",
                Path.cwd().parent / "LocalText2Voice-VoiceGallery" / "catalog.json",
            ]
        )
        for candidate in candidates:
            path = candidate / "catalog.json" if candidate.is_dir() else candidate
            if path.is_file():
                return path
        if allow_remote and self.catalog_url:
            return self.catalog_url
        return None

    def _resolve_source(self, source: str | Path, base: str | Path | None) -> str:
        source_text = str(source)
        if self._is_http(source_text) or Path(source_text).is_absolute():
            return source_text
        if base is None:
            return source_text
        base_text = str(base)
        if self._is_http(base_text):
            return urllib.parse.urljoin(base_text.rstrip("/") + "/", source_text)
        return str((Path(base_text) / source_text).resolve())

    def _resolve_asset(self, value: object, source_base: str) -> tuple[str, str]:
        if not isinstance(value, str) or not value.strip():
            return "", ""
        resolved = self._resolve_source(value.strip(), source_base)
        if self._is_http(resolved):
            return resolved, ""
        return "", resolved

    def _copy_or_download(
        self,
        source: str,
        destination: Path,
        progress: VoiceGalleryProgress,
        message: str,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self._is_http(source):
            request = urllib.request.Request(source, headers={"User-Agent": "LocalText2Voice/1.0"})
            downloaded = 0
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    total = int(response.headers.get("Content-Length") or 0)
                    with destination.open("wb") as output:
                        while True:
                            self._check_cancelled()
                            chunk = response.read(1024 * 128)
                            if not chunk:
                                break
                            output.write(chunk)
                            downloaded += len(chunk)
                            progress(downloaded, total or downloaded or 1, message)
            except VoiceGalleryCancelled:
                self._remove_path(destination)
                raise
            except (OSError, urllib.error.URLError) as exc:
                self._remove_path(destination)
                raise VoiceGalleryError(f"Could not download gallery voice: {exc}") from exc
            return

        source_path = Path(source)
        if not source_path.is_file():
            raise VoiceGalleryError(f"Gallery voice file not found: {source_path}")
        total = source_path.stat().st_size
        shutil.copy2(source_path, destination)
        progress(total, total or 1, message)

    def _mark_installed(self, voice_id: str, installed_path: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S") if installed_path else ""
        with self._connect() as connection:
            connection.execute(
                "UPDATE voice_gallery_voices SET installed_path = ?, installed_at = ? WHERE id = ?",
                (installed_path, now, voice_id),
            )

    def _set_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO voice_gallery_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def _ensure_schema(self) -> None:
        self.files_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_gallery_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_gallery_voices (
                    id TEXT PRIMARY KEY,
                    engine TEXT NOT NULL,
                    name TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT '',
                    language_name TEXT NOT NULL DEFAULT '',
                    voice_type TEXT NOT NULL DEFAULT '',
                    install_type TEXT NOT NULL DEFAULT 'engine_builtin',
                    preview_url TEXT NOT NULL DEFAULT '',
                    preview_path TEXT NOT NULL DEFAULT '',
                    ref_audio_url TEXT NOT NULL DEFAULT '',
                    ref_audio_path TEXT NOT NULL DEFAULT '',
                    ref_text TEXT NOT NULL DEFAULT '',
                    engine_voice_id TEXT NOT NULL DEFAULT '',
                    speaker_id TEXT NOT NULL DEFAULT '',
                    model_id TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    source_base TEXT NOT NULL DEFAULT '',
                    installed_path TEXT NOT NULL DEFAULT '',
                    installed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_voice_gallery_engine ON voice_gallery_voices(engine)"
            )
            existing_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(voice_gallery_voices)")
            }
            for column_name in (
                "short_description",
                "gender",
                "age_style",
                "voice_style",
            ):
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE voice_gallery_voices ADD COLUMN {column_name} "
                        "TEXT NOT NULL DEFAULT ''"
                    )

    @contextmanager
    def _connect(self) -> Any:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _row_to_voice(self, row: sqlite3.Row) -> GalleryVoice:
        return GalleryVoice(
            voice_id=str(row["id"]),
            engine=str(row["engine"]),
            name=str(row["name"]),
            language=str(row["language"]),
            language_name=str(row["language_name"]),
            voice_type=str(row["voice_type"]),
            install_type=str(row["install_type"]),
            preview_url=str(row["preview_url"]),
            preview_path=self._relocated_gallery_path(str(row["preview_path"])),
            ref_audio_url=str(row["ref_audio_url"]),
            ref_audio_path=self._relocated_gallery_path(str(row["ref_audio_path"])),
            ref_text=str(row["ref_text"]),
            engine_voice_id=str(row["engine_voice_id"]),
            speaker_id=str(row["speaker_id"]),
            model_id=str(row["model_id"]),
            short_description=str(row["short_description"]),
            gender=str(row["gender"]),
            age_style=str(row["age_style"]),
            voice_style=str(row["voice_style"]),
            tags=tuple(json.loads(row["tags_json"] or "[]")),
            metadata=json.loads(row["metadata_json"] or "{}"),
            installed_path=self._relocated_gallery_path(str(row["installed_path"])),
            installed_at=str(row["installed_at"]),
        )

    def _relocated_gallery_path(self, value: str) -> str:
        if not value:
            return ""
        return str(resolve_large_asset_path(value))

    @staticmethod
    def _ensure_object(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise VoiceGalleryError("Voice gallery JSON root must be an object.")
        return payload

    @staticmethod
    def _required_string(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise VoiceGalleryError(f"Voice gallery entry is missing required field: {key}")
        return value.strip()

    @staticmethod
    def _is_http(value: str) -> bool:
        return value.startswith("http://") or value.startswith("https://")

    @staticmethod
    def _safe_part(value: str) -> str:
        return re_sub_non_safe(value)

    @staticmethod
    def _compatible_voice_id(engine: str, voice_id: str) -> str:
        for prefix in ("omnivoice_", "chatterbox_", "kokoro_", "qwen_", "piper_"):
            if voice_id.startswith(prefix):
                voice_id = voice_id[len(prefix) :]
                break
        return f"{engine}_{voice_id}"

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while True:
            candidate = destination.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.exists():
            path.unlink()

    def _prune_empty(self, start: Path) -> None:
        root = self.files_root.resolve()
        current = start.resolve()
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise VoiceGalleryCancelled("Voice gallery operation cancelled.")


def re_sub_non_safe(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "voice"

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.utils.paths import app_data_root

from .audio_library import SUPPORTED_AUDIO_EXTENSIONS, resolve_audio_reference
from .text_processor import TextChunk


CURRENT_DB_SCHEMA_VERSION = 2
PROJECT_MANIFEST_NAME = "project.localtext2voice.json"
LEGACY_PROJECT_MANIFEST_NAME = "project.json"


@dataclass(frozen=True)
class StoredAudiobook:
    id: int
    uuid: str
    title: str
    project_dir: Path
    source_text: str = ""
    output_dir: Path = Path()
    split_mode: str = ""
    export_mode: str = ""
    engine_config_json: str = "{}"
    project_settings_json: str = "{}"
    clean_mp3_path: str = ""
    mix_mp3_path: str = ""


@dataclass(frozen=True)
class StoredSegment:
    id: int
    audiobook_id: int
    sequence_index: int
    chapter_index: int
    chapter_title: str
    source_text: str
    wav_path: str
    status: str
    similarity_score: float | None
    verification_status: str
    transcript_text: str
    word_timestamps_json: str = "[]"
    voice: str = ""
    language: str = ""
    paragraph_index: int = 0
    paragraph_length: int = 0
    ends_paragraph: bool = False
    markup_pause_before_ms: int = 0
    markup_pause_after_ms: int | None = None
    resolved_pause_before_ms: int | None = None
    resolved_pause_after_ms: int | None = None
    markup_state_json: str = "{}"
    engine_config_json: str = "{}"
    duration_ms: int = 0
    attempt_count: int = 0
    error_message: str = ""
    needs_rebuild: bool = False


@dataclass(frozen=True)
class StoredAudioEvent:
    id: int
    audiobook_id: int
    segment_id: int
    event_uid: str
    event_id: str
    command_type: str
    raw_command: str
    source_position: int
    anchor_segment_sequence: int
    anchor_source_word: int
    anchor_mode: str
    anchor_pause_offset_ms: int = 0
    file_reference: str = ""
    file_path: str = ""
    track: str = "sfx"
    source_start_ms: int = 0
    duration_ms: int | None = None
    volume_db: float = 0.0
    loop: bool = False
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    pan: float = 0.0
    duck_db: float = 0.0
    trim_silence: bool = False
    target_event_uid: str = ""
    enabled: bool = True
    resolved_time_ms: int | None = None
    resolution_status: str = "pending_whisper"
    resolution_confidence: float | None = None
    warnings_json: str = "[]"


class AudiobookStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.root_dir = app_data_root() / "projects"
        self.db_path = db_path or self.root_dir / "localtext2voice.sqlite3"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def create_audiobook(
        self,
        source_text: str,
        voice_config: dict[str, Any],
        output_dir: Path,
        split_mode: str,
        export_mode: str,
        title: str = "Audiobook",
        project_settings: dict[str, Any] | None = None,
        project_dir: Path | None = None,
    ) -> StoredAudiobook:
        audiobook_uuid = str(uuid.uuid4())
        project_dir = project_dir or self.root_dir / audiobook_uuid
        project_dir.mkdir(parents=True, exist_ok=True)
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audiobooks (
                    uuid, title, source_text, source_hash, status,
                    tts_engine, engine_config_json, split_mode, export_mode,
                    output_dir, project_dir, project_settings_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audiobook_uuid,
                    title,
                    source_text,
                    self._hash_text(source_text),
                    "draft",
                    str(voice_config.get("engine", "piper")),
                    json.dumps(voice_config, ensure_ascii=False),
                    split_mode,
                    export_mode,
                    str(output_dir),
                    str(project_dir),
                    json.dumps(project_settings or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            audiobook_id = int(cursor.lastrowid)
        audiobook = StoredAudiobook(
            audiobook_id,
            audiobook_uuid,
            title,
            project_dir,
            source_text=source_text,
            output_dir=output_dir,
            split_mode=split_mode,
            export_mode=export_mode,
            engine_config_json=json.dumps(voice_config, ensure_ascii=False),
            project_settings_json=json.dumps(project_settings or {}, ensure_ascii=False),
        )
        self._write_project_manifest(audiobook)
        return audiobook

    def get_audiobook(self, audiobook_id: int | None) -> StoredAudiobook | None:
        if audiobook_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, uuid, title, source_text, project_dir, output_dir,
                       split_mode, export_mode, engine_config_json,
                       project_settings_json, clean_mp3_path, mix_mp3_path
                FROM audiobooks
                WHERE id = ?
                """,
                (audiobook_id,),
            ).fetchone()
        return self._row_to_audiobook(row) if row is not None else None

    def get_audiobook_by_uuid(self, audiobook_uuid: str) -> StoredAudiobook | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, uuid, title, source_text, project_dir, output_dir,
                       split_mode, export_mode, engine_config_json,
                       project_settings_json, clean_mp3_path, mix_mp3_path
                FROM audiobooks
                WHERE uuid = ?
                """,
                (audiobook_uuid,),
            ).fetchone()
        return self._row_to_audiobook(row) if row is not None else None

    def list_audiobooks(self) -> list[StoredAudiobook]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, uuid, title, source_text, project_dir, output_dir,
                       split_mode, export_mode, engine_config_json,
                       project_settings_json, clean_mp3_path, mix_mp3_path
                FROM audiobooks
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [self._row_to_audiobook(row) for row in rows]

    def save_audiobook_project(
        self,
        audiobook_id: int,
        source_text: str,
        voice_config: dict[str, Any],
        output_dir: Path,
        split_mode: str,
        export_mode: str,
        title: str,
        project_settings: dict[str, Any] | None = None,
    ) -> StoredAudiobook:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobooks
                SET title = ?, source_text = ?, source_hash = ?,
                    tts_engine = ?, engine_config_json = ?, split_mode = ?,
                    export_mode = ?, output_dir = ?, project_settings_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    source_text,
                    self._hash_text(source_text),
                    str(voice_config.get("engine", "piper")),
                    json.dumps(voice_config, ensure_ascii=False),
                    split_mode,
                    export_mode,
                    str(output_dir),
                    json.dumps(project_settings or {}, ensure_ascii=False),
                    now,
                    audiobook_id,
                ),
            )
        audiobook = self.get_audiobook(audiobook_id)
        if audiobook is None:
            raise ValueError(f"Audiobook project not found: {audiobook_id}")
        self._write_project_manifest(audiobook)
        return audiobook

    def clone_audiobook(
        self,
        source_id: int,
        title: str,
        source_text: str,
        voice_config: dict[str, Any],
        output_dir: Path,
        split_mode: str,
        export_mode: str,
        project_settings: dict[str, Any] | None = None,
        target_project_dir: Path | None = None,
    ) -> StoredAudiobook:
        source = self.get_audiobook(source_id)
        if source is None:
            raise ValueError(f"Audiobook project not found: {source_id}")
        clone = self.create_audiobook(
            source_text,
            voice_config,
            output_dir,
            split_mode,
            export_mode,
            title,
            project_settings,
            target_project_dir,
        )
        if source.project_dir.exists():
            for item in source.project_dir.iterdir():
                destination = clone.project_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, destination)
        now = self._now()
        with self._connect() as connection:
            source_rows = connection.execute(
                """
                SELECT sequence_index, chapter_index, chapter_title,
                       paragraph_index, paragraph_length, ends_paragraph,
                       markup_pause_before_ms, markup_pause_after_ms,
                       resolved_pause_before_ms, resolved_pause_after_ms,
                       source_text, normalized_source_text, markup_state_json,
                       voice, language, status, wav_path, duration_ms,
                       attempt_count, transcript_text, normalized_transcript_text,
                       word_timestamps_json,
                       similarity_score, wer, cer, verification_status,
                       synthesis_ms, transcription_ms, engine_config_json,
                       error_message, needs_rebuild
                FROM audiobook_segments
                WHERE audiobook_id = ?
                ORDER BY sequence_index
                """,
                (source_id,),
            ).fetchall()
            cloned_segment_ids: dict[int, int] = {}
            for row in source_rows:
                wav_path = self._clone_project_path(
                    Path(str(row["wav_path"] or "")),
                    source.project_dir,
                    clone.project_dir,
                )
                cursor = connection.execute(
                    """
                    INSERT INTO audiobook_segments (
                        audiobook_id, sequence_index, chapter_index,
                        chapter_title, paragraph_index, paragraph_length,
                        ends_paragraph, markup_pause_before_ms,
                        markup_pause_after_ms, resolved_pause_before_ms,
                        resolved_pause_after_ms, source_text,
                        normalized_source_text, markup_state_json, voice,
                        language, status, wav_path, duration_ms, attempt_count,
                        transcript_text, normalized_transcript_text,
                        word_timestamps_json,
                        similarity_score, wer, cer, verification_status,
                        synthesis_ms, transcription_ms, engine_config_json,
                        error_message, needs_rebuild, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clone.id,
                        row["sequence_index"],
                        row["chapter_index"],
                        row["chapter_title"],
                        row["paragraph_index"],
                        row["paragraph_length"],
                        row["ends_paragraph"],
                        row["markup_pause_before_ms"],
                        row["markup_pause_after_ms"],
                        row["resolved_pause_before_ms"],
                        row["resolved_pause_after_ms"],
                        row["source_text"],
                        row["normalized_source_text"],
                        row["markup_state_json"],
                        row["voice"],
                        row["language"],
                        row["status"],
                        str(wav_path),
                        row["duration_ms"],
                        row["attempt_count"],
                        row["transcript_text"],
                        row["normalized_transcript_text"],
                        row["word_timestamps_json"],
                        row["similarity_score"],
                        row["wer"],
                        row["cer"],
                        row["verification_status"],
                        row["synthesis_ms"],
                        row["transcription_ms"],
                        row["engine_config_json"],
                        row["error_message"],
                        row["needs_rebuild"],
                        now,
                        now,
                    ),
                )
                cloned_segment_ids[int(row["sequence_index"])] = int(
                    cursor.lastrowid
                )
            source_audio_events = connection.execute(
                """
                SELECT event_uid, event_id, command_type, raw_command,
                       source_position, anchor_segment_sequence,
                       anchor_source_word, anchor_mode, anchor_pause_offset_ms,
                       file_reference,
                       file_path, track, source_start_ms, duration_ms,
                       volume_db, loop, fade_in_ms, fade_out_ms, pan,
                       duck_db, trim_silence, target_event_uid, enabled,
                       resolved_time_ms, resolution_status,
                       resolution_confidence, warnings_json
                FROM audio_events
                WHERE audiobook_id = ?
                ORDER BY source_position, id
                """,
                (source_id,),
            ).fetchall()
            for row in source_audio_events:
                anchor_sequence = int(row["anchor_segment_sequence"] or 0)
                segment_id = cloned_segment_ids.get(anchor_sequence)
                if segment_id is None:
                    continue
                value = dict(row)
                value["file_path"] = str(
                    self._clone_project_path(
                        Path(str(row["file_path"] or "")),
                        source.project_dir,
                        clone.project_dir,
                    )
                )
                value["warnings"] = self._json_to_list(row["warnings_json"])
                self._insert_manifest_audio_event(
                    connection,
                    clone.id,
                    segment_id,
                    clone.project_dir,
                    value,
                    now,
                )
        self._write_project_manifest(clone)
        return clone

    def import_project_manifest(self, manifest_path: Path) -> StoredAudiobook:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Project file is not a valid LocalText2Voice project.")

        project_dir = manifest_path.parent
        source_file = project_dir / "source.txt"
        source_text = str(data.get("source_text") or "")
        if not source_text and source_file.is_file():
            source_text = source_file.read_text(encoding="utf-8-sig")
        audiobook_uuid = str(data.get("uuid") or uuid.uuid4())
        title = str(data.get("title") or "Audiobook")
        engine_config = self._json_to_dict(data.get("engine_config", {}))
        project_settings = self._json_to_dict(data.get("project_settings", {}))
        output_dir = self._path_from_manifest(data.get("output_dir"), project_dir)
        if not str(output_dir) or str(output_dir) == ".":
            output_dir = project_dir / "exports"
        clean_mp3_path = self._path_from_manifest(data.get("clean_mp3_path"), project_dir)
        mix_mp3_path = self._path_from_manifest(data.get("mix_mp3_path"), project_dir)
        now = self._now()

        existing = self.get_audiobook_by_uuid(audiobook_uuid)
        with self._connect() as connection:
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO audiobooks (
                        uuid, title, source_text, source_hash, status,
                        tts_engine, engine_config_json, split_mode, export_mode,
                        output_dir, project_dir, project_settings_json,
                        clean_mp3_path, mix_mp3_path, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audiobook_uuid,
                        title,
                        source_text,
                        self._hash_text(source_text),
                        "draft",
                        str(engine_config.get("engine", "")),
                        json.dumps(engine_config, ensure_ascii=False),
                        str(data.get("split_mode") or ""),
                        str(data.get("export_mode") or ""),
                        str(output_dir),
                        str(project_dir),
                        json.dumps(project_settings, ensure_ascii=False),
                        str(clean_mp3_path) if str(clean_mp3_path) != "." else "",
                        str(mix_mp3_path) if str(mix_mp3_path) != "." else "",
                        now,
                        now,
                    ),
                )
                audiobook_id = int(cursor.lastrowid)
            else:
                audiobook_id = existing.id
                connection.execute(
                    """
                    UPDATE audiobooks
                    SET title = ?, source_text = ?, source_hash = ?,
                        status = ?, tts_engine = ?, engine_config_json = ?,
                        split_mode = ?, export_mode = ?, output_dir = ?,
                        project_dir = ?, project_settings_json = ?,
                        clean_mp3_path = ?, mix_mp3_path = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        source_text,
                        self._hash_text(source_text),
                        "draft",
                        str(engine_config.get("engine", "")),
                        json.dumps(engine_config, ensure_ascii=False),
                        str(data.get("split_mode") or ""),
                        str(data.get("export_mode") or ""),
                        str(output_dir),
                        str(project_dir),
                        json.dumps(project_settings, ensure_ascii=False),
                        str(clean_mp3_path) if str(clean_mp3_path) != "." else "",
                        str(mix_mp3_path) if str(mix_mp3_path) != "." else "",
                        now,
                        audiobook_id,
                    ),
                )
            connection.execute(
                "DELETE FROM audio_events WHERE audiobook_id = ?",
                (audiobook_id,),
            )
            connection.execute(
                "DELETE FROM audiobook_segments WHERE audiobook_id = ?",
                (audiobook_id,),
            )
            imported_segment_ids: dict[int, int] = {}
            segments = data.get("segments", [])
            if isinstance(segments, list):
                for segment in segments:
                    if not isinstance(segment, dict):
                        continue
                    segment_text = str(segment.get("source_text") or "")
                    wav_path = self._path_from_manifest(
                        segment.get("wav_path"),
                        project_dir,
                    )
                    segment_sequence = self._manifest_int(
                        segment.get("sequence_index")
                    )
                    cursor = connection.execute(
                        """
                        INSERT INTO audiobook_segments (
                            audiobook_id, sequence_index, chapter_index,
                            chapter_title, paragraph_index, paragraph_length,
                            ends_paragraph, markup_pause_before_ms,
                            markup_pause_after_ms, resolved_pause_before_ms,
                            resolved_pause_after_ms, source_text,
                            normalized_source_text, markup_state_json, voice,
                            language, status, wav_path, duration_ms, attempt_count,
                            transcript_text, normalized_transcript_text,
                            word_timestamps_json,
                            similarity_score, wer, cer, verification_status,
                            synthesis_ms, transcription_ms, engine_config_json,
                            error_message, needs_rebuild, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audiobook_id,
                            segment_sequence,
                            self._manifest_int(segment.get("chapter_index"), 1),
                            str(segment.get("chapter_title") or ""),
                            self._manifest_int(segment.get("paragraph_index")),
                            self._manifest_int(segment.get("paragraph_length")),
                            1 if bool(segment.get("ends_paragraph")) else 0,
                            self._manifest_int(
                                segment.get("markup_pause_before_ms")
                            ),
                            segment.get("markup_pause_after_ms"),
                            segment.get("resolved_pause_before_ms"),
                            segment.get("resolved_pause_after_ms"),
                            segment_text,
                            str(
                                segment.get("normalized_source_text")
                                or normalize_for_similarity(segment_text)
                            ),
                            json.dumps(
                                self._json_to_dict(segment.get("markup_state", {})),
                                ensure_ascii=False,
                            ),
                            str(segment.get("voice") or ""),
                            str(segment.get("language") or ""),
                            str(segment.get("status") or "pending"),
                            str(wav_path) if str(wav_path) != "." else "",
                            self._manifest_int(segment.get("duration_ms")),
                            self._manifest_int(segment.get("attempt_count")),
                            str(segment.get("transcript_text") or ""),
                            str(segment.get("normalized_transcript_text") or ""),
                            json.dumps(
                                self._json_to_list(segment.get("word_timestamps", [])),
                                ensure_ascii=False,
                            ),
                            self._manifest_float(segment.get("similarity_score")),
                            self._manifest_float(segment.get("wer")),
                            self._manifest_float(segment.get("cer")),
                            str(
                                segment.get("verification_status")
                                or "not_verified"
                            ),
                            self._manifest_int(segment.get("synthesis_ms")),
                            self._manifest_int(segment.get("transcription_ms")),
                            json.dumps(
                                self._json_to_dict(segment.get("engine_config", {})),
                                ensure_ascii=False,
                            ),
                            str(segment.get("error_message") or ""),
                            1 if bool(segment.get("needs_rebuild")) else 0,
                            now,
                            now,
                        ),
                    )
                    imported_segment_ids[segment_sequence] = int(cursor.lastrowid)
            audio_events = data.get("audio_events", [])
            if isinstance(audio_events, list):
                for audio_event in audio_events:
                    if not isinstance(audio_event, dict):
                        continue
                    anchor_sequence = self._manifest_int(
                        audio_event.get("anchor_segment_sequence")
                    )
                    segment_id = imported_segment_ids.get(anchor_sequence)
                    if segment_id is None:
                        continue
                    self._insert_manifest_audio_event(
                        connection,
                        audiobook_id,
                        segment_id,
                        project_dir,
                        audio_event,
                        now,
                    )
        audiobook = self.get_audiobook_by_uuid(audiobook_uuid)
        if audiobook is None:
            raise ValueError("Project was imported but could not be loaded.")
        self._write_project_manifest(audiobook)
        return audiobook

    def replace_segments(
        self,
        audiobook: StoredAudiobook,
        groups: list[Any],
    ) -> dict[tuple[int, int], int]:
        mapping: dict[tuple[int, int], int] = {}
        now = self._now()
        sequence = 0
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM audio_events WHERE audiobook_id = ?",
                (audiobook.id,),
            )
            connection.execute(
                "DELETE FROM audiobook_segments WHERE audiobook_id = ?",
                (audiobook.id,),
            )
            for chapter_index, group in enumerate(groups, start=1):
                for chunk_index, chunk in enumerate(group.chunks, start=1):
                    sequence += 1
                    wav_path = self.segment_wav_path(
                        audiobook,
                        chapter_index,
                        chunk_index,
                    )
                    cursor = connection.execute(
                        """
                        INSERT INTO audiobook_segments (
                            audiobook_id, sequence_index, chapter_index,
                            chapter_title, paragraph_index, paragraph_length,
                            ends_paragraph, markup_pause_before_ms,
                            markup_pause_after_ms, source_text,
                            normalized_source_text, markup_state_json,
                            voice, language, status, wav_path,
                            attempt_count, verification_status, needs_rebuild,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audiobook.id,
                            sequence,
                            chapter_index,
                            str(group.title),
                            int(chunk.paragraph_number),
                            int(chunk.paragraph_length),
                            1 if chunk.ends_paragraph else 0,
                            int(chunk.markup_pause_before_ms),
                            chunk.markup_pause_after_ms,
                            chunk.text,
                            normalize_for_similarity(chunk.text),
                            json.dumps(chunk.markup_state, ensure_ascii=False),
                            str(chunk.markup_state.get("voice", "")),
                            str(chunk.markup_state.get("language", "")),
                            "pending",
                            str(wav_path),
                            0,
                            "not_verified",
                            0,
                            now,
                            now,
                        ),
                    )
                    segment_id = int(cursor.lastrowid)
                    mapping[(chapter_index, chunk_index)] = segment_id
                    for audio_event in getattr(chunk, "markup_audio_events", ()):
                        self._insert_audio_event(
                            connection,
                            audiobook,
                            segment_id,
                            sequence,
                            audio_event,
                            now,
                        )
            connection.execute(
                "UPDATE audiobooks SET status = ?, mix_mp3_path = '', "
                "updated_at = ? WHERE id = ?",
                ("rendering", now, audiobook.id),
            )
        refreshed = self.get_audiobook(audiobook.id)
        if refreshed is not None:
            self._write_project_manifest(refreshed)
        return mapping

    def segment_wav_path(
        self,
        audiobook: StoredAudiobook,
        chapter_index: int,
        chunk_index: int,
    ) -> Path:
        directory = audiobook.project_dir / "segments"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"group_{chapter_index:03d}_block_{chunk_index:04d}.wav"

    def _insert_audio_event(
        self,
        connection: sqlite3.Connection,
        audiobook: StoredAudiobook,
        segment_id: int,
        segment_sequence: int,
        audio_event: Any,
        now: str,
    ) -> None:
        warnings = [str(item) for item in getattr(audio_event, "warnings", ())]
        file_path = Path()
        status = "pending_whisper"
        if not bool(getattr(audio_event, "enabled", True)):
            status = "invalid"
        elif str(getattr(audio_event, "command_type", "")) == "play":
            file_path, asset_warning = self._embed_audio_asset(
                audiobook,
                str(getattr(audio_event, "file_reference", "")),
            )
            if asset_warning:
                warnings.append(asset_warning)
            if not str(file_path) or str(file_path) == ".":
                status = "missing"
        connection.execute(
            """
            INSERT INTO audio_events (
                audiobook_id, segment_id, event_uid, event_id, command_type,
                raw_command, source_position, anchor_segment_sequence,
                anchor_source_word, anchor_mode, anchor_pause_offset_ms,
                file_reference, file_path,
                track, source_start_ms, duration_ms, volume_db, loop,
                fade_in_ms, fade_out_ms, pan, duck_db, trim_silence,
                target_event_uid, enabled, resolved_time_ms,
                resolution_status, resolution_confidence, warnings_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audiobook.id,
                segment_id,
                str(audio_event.event_uid),
                str(audio_event.event_id),
                str(audio_event.command_type),
                str(audio_event.raw_command),
                int(audio_event.source_position),
                segment_sequence,
                int(audio_event.anchor_source_word),
                str(getattr(audio_event, "anchor_mode", "word_boundary")),
                int(getattr(audio_event, "anchor_pause_offset_ms", 0) or 0),
                str(getattr(audio_event, "file_reference", "")),
                str(file_path) if str(file_path) != "." else "",
                str(getattr(audio_event, "track", "sfx")),
                int(getattr(audio_event, "source_start_ms", 0) or 0),
                getattr(audio_event, "duration_ms", None),
                float(getattr(audio_event, "volume_db", 0.0) or 0.0),
                1 if bool(getattr(audio_event, "loop", False)) else 0,
                int(getattr(audio_event, "fade_in_ms", 0) or 0),
                int(getattr(audio_event, "fade_out_ms", 0) or 0),
                float(getattr(audio_event, "pan", 0.0) or 0.0),
                float(getattr(audio_event, "duck_db", 0.0) or 0.0),
                1 if bool(getattr(audio_event, "trim_silence", False)) else 0,
                str(getattr(audio_event, "target_event_uid", "")),
                1 if bool(getattr(audio_event, "enabled", True)) else 0,
                None,
                status,
                None,
                json.dumps(warnings, ensure_ascii=False),
                now,
                now,
            ),
        )

    def _embed_audio_asset(
        self,
        audiobook: StoredAudiobook,
        file_reference: str,
    ) -> tuple[Path, str]:
        try:
            project_settings = json.loads(audiobook.project_settings_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            project_settings = {}
        if not isinstance(project_settings, dict):
            project_settings = {}
        source = resolve_audio_reference(
            file_reference,
            project_settings,
            project_dir=audiobook.project_dir,
        )
        if source is None:
            return Path(), f"Audio file not found: {file_reference}"
        if source.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
            return Path(), f"Unsupported local audio file: {file_reference}"

        source = source.resolve()
        assets_dir = audiobook.project_dir / "assets" / "audio"
        assets_dir.mkdir(parents=True, exist_ok=True)
        try:
            source.relative_to(assets_dir.resolve())
            return source, ""
        except ValueError:
            pass

        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        content_hash = digest.hexdigest()
        existing_asset = next(
            (
                candidate
                for candidate in assets_dir.glob(f"{content_hash}.*")
                if candidate.is_file()
            ),
            None,
        )
        if existing_asset is not None:
            return existing_asset.resolve(), ""
        destination = assets_dir / f"{content_hash}{source.suffix.casefold()}"
        if not destination.is_file():
            shutil.copy2(source, destination)
        return destination.resolve(), ""

    def _insert_manifest_audio_event(
        self,
        connection: sqlite3.Connection,
        audiobook_id: int,
        segment_id: int,
        project_dir: Path,
        value: dict[str, Any],
        now: str,
    ) -> None:
        file_path = self._path_from_manifest(value.get("file_path"), project_dir)
        connection.execute(
            """
            INSERT INTO audio_events (
                audiobook_id, segment_id, event_uid, event_id, command_type,
                raw_command, source_position, anchor_segment_sequence,
                anchor_source_word, anchor_mode, anchor_pause_offset_ms,
                file_reference, file_path,
                track, source_start_ms, duration_ms, volume_db, loop,
                fade_in_ms, fade_out_ms, pan, duck_db, trim_silence,
                target_event_uid, enabled, resolved_time_ms,
                resolution_status, resolution_confidence, warnings_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audiobook_id,
                segment_id,
                str(value.get("event_uid") or ""),
                str(value.get("event_id") or ""),
                str(value.get("command_type") or "play"),
                str(value.get("raw_command") or ""),
                self._manifest_int(value.get("source_position")),
                self._manifest_int(value.get("anchor_segment_sequence")),
                self._manifest_int(value.get("anchor_source_word")),
                str(value.get("anchor_mode") or "word_boundary"),
                self._manifest_int(value.get("anchor_pause_offset_ms")),
                str(value.get("file_reference") or ""),
                str(file_path) if str(file_path) != "." else "",
                str(value.get("track") or "sfx"),
                self._manifest_int(value.get("source_start_ms")),
                value.get("duration_ms"),
                float(value.get("volume_db") or 0.0),
                1 if bool(value.get("loop")) else 0,
                self._manifest_int(value.get("fade_in_ms")),
                self._manifest_int(value.get("fade_out_ms")),
                float(value.get("pan") or 0.0),
                float(value.get("duck_db") or 0.0),
                1 if bool(value.get("trim_silence")) else 0,
                str(value.get("target_event_uid") or ""),
                1 if bool(value.get("enabled", True)) else 0,
                value.get("resolved_time_ms"),
                str(value.get("resolution_status") or "pending_whisper"),
                self._manifest_float(value.get("resolution_confidence")),
                json.dumps(
                    self._json_to_list(value.get("warnings", [])),
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )

    def mark_segment_rendering(self, segment_id: int, voice_config: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET status = ?, engine_config_json = ?, voice = ?,
                    language = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "rendering",
                    json.dumps(voice_config, ensure_ascii=False),
                    self._voice_from_config(voice_config),
                    self._language_from_config(voice_config),
                    self._now(),
                    segment_id,
                ),
            )

    def mark_segment_rendered(
        self,
        segment_id: int,
        wav_path: Path,
        duration_ms: int,
        synthesis_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET status = ?, wav_path = ?, duration_ms = ?,
                    synthesis_ms = ?, attempt_count = attempt_count + 1,
                    transcript_text = '', normalized_transcript_text = '',
                    word_timestamps_json = '[]',
                    similarity_score = NULL, wer = NULL, cer = NULL,
                    verification_status = 'not_verified',
                    error_message = '', needs_rebuild = 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    "rendered",
                    str(wav_path),
                    duration_ms,
                    synthesis_ms,
                    self._now(),
                    segment_id,
                ),
            )
            self._invalidate_audio_events_from_segment(connection, segment_id)
        self._write_project_manifest_for_segment(segment_id)

    def update_segment_pause(
        self,
        segment_id: int,
        before_ms: int | None = None,
        after_ms: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET resolved_pause_before_ms = ?,
                    resolved_pause_after_ms = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (before_ms, after_ms, self._now(), segment_id),
            )
            self._invalidate_audio_events_from_segment(connection, segment_id)
        self._write_project_manifest_for_segment(segment_id)

    def update_segment_text(self, segment_id: int, source_text: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET source_text = ?, normalized_source_text = ?,
                    status = ?, transcript_text = '',
                    normalized_transcript_text = '', word_timestamps_json = '[]',
                    similarity_score = NULL,
                    wer = NULL, cer = NULL,
                    verification_status = 'not_verified',
                    error_message = '', needs_rebuild = 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    source_text,
                    normalize_for_similarity(source_text),
                    "edited",
                    self._now(),
                    segment_id,
                ),
            )
            self._invalidate_audio_events_from_segment(connection, segment_id)
        self._write_project_manifest_for_segment(segment_id)

    @staticmethod
    def _invalidate_audio_events_from_segment(
        connection: sqlite3.Connection,
        segment_id: int,
    ) -> None:
        row = connection.execute(
            "SELECT audiobook_id, sequence_index FROM audiobook_segments WHERE id = ?",
            (segment_id,),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            """
            UPDATE audio_events
            SET resolved_time_ms = NULL,
                resolution_confidence = NULL,
                resolution_status = CASE
                    WHEN enabled = 0 THEN 'invalid'
                    WHEN resolution_status = 'missing' THEN 'missing'
                    ELSE 'pending_whisper'
                END,
                updated_at = ?
            WHERE audiobook_id = ? AND anchor_segment_sequence >= ?
            """,
            (
                AudiobookStore._now(),
                int(row["audiobook_id"]),
                int(row["sequence_index"]),
            ),
        )
        connection.execute(
            "UPDATE audiobooks SET mix_mp3_path = '', updated_at = ? WHERE id = ?",
            (AudiobookStore._now(), int(row["audiobook_id"])),
        )

    def get_segment(self, segment_id: int) -> StoredSegment | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, audiobook_id, sequence_index, chapter_index,
                       chapter_title, paragraph_index, paragraph_length,
                       ends_paragraph, markup_pause_before_ms,
                       markup_pause_after_ms, resolved_pause_before_ms,
                       resolved_pause_after_ms, source_text, wav_path, status,
                       similarity_score, verification_status, transcript_text,
                       word_timestamps_json,
                       voice, language,
                       markup_state_json, engine_config_json, duration_ms,
                       attempt_count, error_message, needs_rebuild
                FROM audiobook_segments
                WHERE id = ?
                """,
                (segment_id,),
            ).fetchone()
        return self._row_to_segment(row) if row is not None else None

    def mark_segment_failed(self, segment_id: int, error_message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                ("failed", error_message, self._now(), segment_id),
            )

    def complete_audiobook(self, audiobook_id: int, output_paths: list[Path]) -> None:
        clean_mp3 = ""
        mix_mp3 = ""
        for output_path in output_paths:
            if output_path.stem.lower().endswith("_mix"):
                mix_mp3 = str(output_path)
            elif not clean_mp3:
                clean_mp3 = str(output_path)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobooks
                SET status = ?, clean_mp3_path = ?, mix_mp3_path = ?, updated_at = ?
                WHERE id = ?
                """,
                ("completed", clean_mp3, mix_mp3, self._now(), audiobook_id),
            )
            connection.execute(
                """
                UPDATE audiobook_segments
                SET needs_rebuild = 0, updated_at = ?
                WHERE audiobook_id = ?
                """,
                (self._now(), audiobook_id),
            )
        audiobook = self.get_audiobook(audiobook_id)
        if audiobook is not None:
            self._write_project_manifest(audiobook)

    def audiobook_output_paths(self, audiobook_id: int) -> tuple[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT clean_mp3_path, mix_mp3_path
                FROM audiobooks
                WHERE id = ?
                """,
                (audiobook_id,),
            ).fetchone()
        if row is None:
            return "", ""
        return str(row["clean_mp3_path"] or ""), str(row["mix_mp3_path"] or "")

    def fail_audiobook(self, audiobook_id: int, status: str = "error") -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE audiobooks SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._now(), audiobook_id),
            )

    def latest_audiobook(self) -> StoredAudiobook | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, uuid, title, source_text, project_dir, output_dir,
                       split_mode, export_mode, engine_config_json,
                       project_settings_json, clean_mp3_path, mix_mp3_path
                FROM audiobooks
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_audiobook(row) if row is not None else None

    def list_segments(self, audiobook_id: int) -> list[StoredSegment]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, audiobook_id, sequence_index, chapter_index,
                       chapter_title, paragraph_index, paragraph_length,
                       ends_paragraph, markup_pause_before_ms,
                       markup_pause_after_ms, resolved_pause_before_ms,
                       resolved_pause_after_ms, source_text, wav_path, status,
                       similarity_score, verification_status, transcript_text,
                       word_timestamps_json,
                       voice, language,
                       markup_state_json, engine_config_json, duration_ms,
                       attempt_count, error_message, needs_rebuild
                FROM audiobook_segments
                WHERE audiobook_id = ?
                ORDER BY sequence_index
                """,
                (audiobook_id,),
            ).fetchall()
        return [self._row_to_segment(row) for row in rows]

    def list_audio_events(self, audiobook_id: int) -> list[StoredAudioEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, audiobook_id, segment_id, event_uid, event_id,
                       command_type, raw_command, source_position,
                       anchor_segment_sequence, anchor_source_word, anchor_mode,
                       anchor_pause_offset_ms, file_reference, file_path,
                       track, source_start_ms,
                       duration_ms, volume_db, loop, fade_in_ms, fade_out_ms,
                       pan, duck_db, trim_silence, target_event_uid, enabled,
                       resolved_time_ms, resolution_status,
                       resolution_confidence, warnings_json
                FROM audio_events
                WHERE audiobook_id = ?
                ORDER BY source_position, id
                """,
                (audiobook_id,),
            ).fetchall()
        return [self._row_to_audio_event(row) for row in rows]

    def update_audio_event_resolutions(
        self,
        audiobook_id: int,
        resolutions: dict[str, tuple[int | None, str, float | None]],
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            for event_uid, (resolved_time_ms, status, confidence) in resolutions.items():
                connection.execute(
                    """
                    UPDATE audio_events
                    SET resolved_time_ms = ?, resolution_status = ?,
                        resolution_confidence = ?, updated_at = ?
                    WHERE audiobook_id = ? AND event_uid = ?
                    """,
                    (
                        resolved_time_ms,
                        status,
                        confidence,
                        now,
                        audiobook_id,
                        event_uid,
                    ),
                )
            if resolutions:
                connection.execute(
                    "UPDATE audiobooks SET mix_mp3_path = '', updated_at = ? WHERE id = ?",
                    (now, audiobook_id),
                )
        audiobook = self.get_audiobook(audiobook_id)
        if audiobook is not None:
            self._write_project_manifest(audiobook)

    def segment_wav_cache_stats(self, audiobook_id: int | None = None) -> tuple[int, int]:
        paths = self._segment_wav_cache_paths(audiobook_id)
        total_bytes = 0
        existing_count = 0
        for path in paths:
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            existing_count += 1
        return existing_count, total_bytes

    def cleanup_segment_wav_cache(self, audiobook_id: int | None = None) -> tuple[int, int]:
        paths = self._segment_wav_cache_paths(audiobook_id)
        deleted_count = 0
        deleted_bytes = 0
        for path in paths:
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            deleted_count += 1
            deleted_bytes += size
        with self._connect() as connection:
            if audiobook_id is None:
                connection.execute("UPDATE audiobook_segments SET wav_path = ''")
                connection.execute("UPDATE segment_attempts SET wav_path = ''")
            else:
                connection.execute(
                    "UPDATE audiobook_segments SET wav_path = '' WHERE audiobook_id = ?",
                    (audiobook_id,),
                )
                connection.execute(
                    """
                    UPDATE segment_attempts
                    SET wav_path = ''
                    WHERE segment_id IN (
                        SELECT id FROM audiobook_segments WHERE audiobook_id = ?
                    )
                    """,
                    (audiobook_id,),
                )
        if audiobook_id is None:
            for audiobook in self.list_audiobooks():
                self._write_project_manifest(audiobook)
        else:
            audiobook = self.get_audiobook(audiobook_id)
            if audiobook is not None:
                self._write_project_manifest(audiobook)
        return deleted_count, deleted_bytes

    def update_segment_verification(
        self,
        segment_id: int,
        transcript_text: str,
        similarity_score: float,
        wer: float,
        cer: float,
        verification_status: str,
        transcription_ms: int,
        word_timestamps_json: str = "[]",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET transcript_text = ?, normalized_transcript_text = ?,
                    word_timestamps_json = ?,
                    similarity_score = ?, wer = ?, cer = ?,
                    verification_status = ?, transcription_ms = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    transcript_text,
                    normalize_for_similarity(transcript_text),
                    word_timestamps_json,
                    similarity_score,
                    wer,
                    cer,
                    verification_status,
                    transcription_ms,
                    self._now(),
                    segment_id,
                ),
            )
        self._write_project_manifest_for_segment(segment_id)

    def _segment_wav_cache_paths(self, audiobook_id: int | None = None) -> list[Path]:
        paths: set[Path] = set()
        if audiobook_id is None:
            audiobooks = self.list_audiobooks()
        else:
            audiobook = self.get_audiobook(audiobook_id)
            audiobooks = [audiobook] if audiobook is not None else []
        for audiobook in audiobooks:
            segments_dir = audiobook.project_dir / "segments"
            if segments_dir.is_dir():
                paths.update(
                    path for path in segments_dir.glob("*.wav") if path.is_file()
                )
        with self._connect() as connection:
            if audiobook_id is None:
                rows = connection.execute(
                    """
                    SELECT wav_path FROM audiobook_segments WHERE wav_path != ''
                    UNION
                    SELECT wav_path FROM segment_attempts WHERE wav_path != ''
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT wav_path FROM audiobook_segments
                    WHERE audiobook_id = ? AND wav_path != ''
                    UNION
                    SELECT segment_attempts.wav_path
                    FROM segment_attempts
                    JOIN audiobook_segments
                        ON audiobook_segments.id = segment_attempts.segment_id
                    WHERE audiobook_segments.audiobook_id = ?
                        AND segment_attempts.wav_path != ''
                    """,
                    (audiobook_id, audiobook_id),
                ).fetchall()
        for row in rows:
            path = Path(str(row["wav_path"] or ""))
            if path.suffix.lower() == ".wav" and path.is_file():
                paths.add(path)
        return sorted(paths)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_info (key, value)
                VALUES ('schema_version', ?)
                """,
                (str(CURRENT_DB_SCHEMA_VERSION),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audiobooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tts_engine TEXT NOT NULL,
                    engine_config_json TEXT NOT NULL,
                    split_mode TEXT NOT NULL,
                    export_mode TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    project_dir TEXT NOT NULL,
                    project_settings_json TEXT DEFAULT '{}',
                    clean_mp3_path TEXT DEFAULT '',
                    mix_mp3_path TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    app_version TEXT DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audiobook_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audiobook_id INTEGER NOT NULL REFERENCES audiobooks(id)
                        ON DELETE CASCADE,
                    sequence_index INTEGER NOT NULL,
                    chapter_index INTEGER NOT NULL,
                    chapter_title TEXT NOT NULL,
                    paragraph_index INTEGER DEFAULT 0,
                    source_text TEXT NOT NULL,
                    normalized_source_text TEXT NOT NULL,
                    markup_state_json TEXT DEFAULT '{}',
                    voice TEXT DEFAULT '',
                    language TEXT DEFAULT '',
                    status TEXT NOT NULL,
                    wav_path TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    attempt_count INTEGER DEFAULT 0,
                    transcript_text TEXT DEFAULT '',
                    normalized_transcript_text TEXT DEFAULT '',
                    word_timestamps_json TEXT DEFAULT '[]',
                    similarity_score REAL,
                    wer REAL,
                    cer REAL,
                    verification_status TEXT DEFAULT 'not_verified',
                    synthesis_ms INTEGER DEFAULT 0,
                    transcription_ms INTEGER DEFAULT 0,
                    engine_config_json TEXT DEFAULT '{}',
                    error_message TEXT DEFAULT '',
                    needs_rebuild INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_segment_columns(connection)
            self._ensure_audiobook_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audio_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audiobook_id INTEGER NOT NULL REFERENCES audiobooks(id)
                        ON DELETE CASCADE,
                    segment_id INTEGER NOT NULL REFERENCES audiobook_segments(id)
                        ON DELETE CASCADE,
                    event_uid TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    raw_command TEXT NOT NULL,
                    source_position INTEGER NOT NULL,
                    anchor_segment_sequence INTEGER NOT NULL,
                    anchor_source_word INTEGER NOT NULL,
                    anchor_mode TEXT DEFAULT 'word_boundary',
                    anchor_pause_offset_ms INTEGER DEFAULT 0,
                    file_reference TEXT DEFAULT '',
                    file_path TEXT DEFAULT '',
                    track TEXT DEFAULT 'sfx',
                    source_start_ms INTEGER DEFAULT 0,
                    duration_ms INTEGER,
                    volume_db REAL DEFAULT 0,
                    loop INTEGER DEFAULT 0,
                    fade_in_ms INTEGER DEFAULT 0,
                    fade_out_ms INTEGER DEFAULT 0,
                    pan REAL DEFAULT 0,
                    duck_db REAL DEFAULT 0,
                    trim_silence INTEGER DEFAULT 0,
                    target_event_uid TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    resolved_time_ms INTEGER,
                    resolution_status TEXT DEFAULT 'pending_whisper',
                    resolution_confidence REAL,
                    warnings_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (audiobook_id, event_uid)
                )
                """
            )
            self._ensure_audio_event_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS segment_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    segment_id INTEGER NOT NULL REFERENCES audiobook_segments(id)
                        ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    engine_config_json TEXT DEFAULT '{}',
                    wav_path TEXT DEFAULT '',
                    transcript_text TEXT DEFAULT '',
                    similarity_score REAL,
                    status TEXT NOT NULL,
                    error_message TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_segments_audiobook_sequence
                ON audiobook_segments(audiobook_id, sequence_index)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audio_events_audiobook_position
                ON audio_events(audiobook_id, source_position)
                """
            )

    def _ensure_segment_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(audiobook_segments)")
        }
        columns: dict[str, str] = {
            "paragraph_length": "INTEGER DEFAULT 0",
            "ends_paragraph": "INTEGER DEFAULT 0",
            "markup_pause_before_ms": "INTEGER DEFAULT 0",
            "markup_pause_after_ms": "INTEGER",
            "resolved_pause_before_ms": "INTEGER",
            "resolved_pause_after_ms": "INTEGER",
            "needs_rebuild": "INTEGER DEFAULT 0",
            "word_timestamps_json": "TEXT DEFAULT '[]'",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE audiobook_segments ADD COLUMN {name} {definition}"
                )

    def _ensure_audiobook_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(audiobooks)")
        }
        columns: dict[str, str] = {
            "project_settings_json": "TEXT DEFAULT '{}'",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE audiobooks ADD COLUMN {name} {definition}"
                )

    def _ensure_audio_event_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(audio_events)")
        }
        if "anchor_pause_offset_ms" not in existing:
            connection.execute(
                "ALTER TABLE audio_events ADD COLUMN "
                "anchor_pause_offset_ms INTEGER DEFAULT 0"
            )

    @staticmethod
    def _row_to_segment(row: sqlite3.Row) -> StoredSegment:
        return StoredSegment(
            id=int(row["id"]),
            audiobook_id=int(row["audiobook_id"]),
            sequence_index=int(row["sequence_index"]),
            chapter_index=int(row["chapter_index"]),
            chapter_title=str(row["chapter_title"]),
            source_text=str(row["source_text"]),
            wav_path=str(row["wav_path"] or ""),
            status=str(row["status"]),
            similarity_score=(
                None
                if row["similarity_score"] is None
                else float(row["similarity_score"])
            ),
            verification_status=str(row["verification_status"] or "not_verified"),
            transcript_text=str(row["transcript_text"] or ""),
            word_timestamps_json=str(row["word_timestamps_json"] or "[]"),
            voice=str(row["voice"] or ""),
            language=str(row["language"] or ""),
            paragraph_index=int(row["paragraph_index"] or 0),
            paragraph_length=int(row["paragraph_length"] or 0),
            ends_paragraph=bool(row["ends_paragraph"]),
            markup_pause_before_ms=int(row["markup_pause_before_ms"] or 0),
            markup_pause_after_ms=(
                None
                if row["markup_pause_after_ms"] is None
                else int(row["markup_pause_after_ms"])
            ),
            resolved_pause_before_ms=(
                None
                if row["resolved_pause_before_ms"] is None
                else int(row["resolved_pause_before_ms"])
            ),
            resolved_pause_after_ms=(
                None
                if row["resolved_pause_after_ms"] is None
                else int(row["resolved_pause_after_ms"])
            ),
            markup_state_json=str(row["markup_state_json"] or "{}"),
            engine_config_json=str(row["engine_config_json"] or "{}"),
            duration_ms=int(row["duration_ms"] or 0),
            attempt_count=int(row["attempt_count"] or 0),
            error_message=str(row["error_message"] or ""),
            needs_rebuild=bool(row["needs_rebuild"]),
        )

    @staticmethod
    def _row_to_audio_event(row: sqlite3.Row) -> StoredAudioEvent:
        return StoredAudioEvent(
            id=int(row["id"]),
            audiobook_id=int(row["audiobook_id"]),
            segment_id=int(row["segment_id"]),
            event_uid=str(row["event_uid"]),
            event_id=str(row["event_id"]),
            command_type=str(row["command_type"]),
            raw_command=str(row["raw_command"]),
            source_position=int(row["source_position"] or 0),
            anchor_segment_sequence=int(row["anchor_segment_sequence"] or 0),
            anchor_source_word=int(row["anchor_source_word"] or 0),
            anchor_mode=str(row["anchor_mode"] or "word_boundary"),
            anchor_pause_offset_ms=int(row["anchor_pause_offset_ms"] or 0),
            file_reference=str(row["file_reference"] or ""),
            file_path=str(row["file_path"] or ""),
            track=str(row["track"] or "sfx"),
            source_start_ms=int(row["source_start_ms"] or 0),
            duration_ms=(
                None if row["duration_ms"] is None else int(row["duration_ms"])
            ),
            volume_db=float(row["volume_db"] or 0.0),
            loop=bool(row["loop"]),
            fade_in_ms=int(row["fade_in_ms"] or 0),
            fade_out_ms=int(row["fade_out_ms"] or 0),
            pan=float(row["pan"] or 0.0),
            duck_db=float(row["duck_db"] or 0.0),
            trim_silence=bool(row["trim_silence"]),
            target_event_uid=str(row["target_event_uid"] or ""),
            enabled=bool(row["enabled"]),
            resolved_time_ms=(
                None
                if row["resolved_time_ms"] is None
                else int(row["resolved_time_ms"])
            ),
            resolution_status=str(row["resolution_status"] or "pending_whisper"),
            resolution_confidence=(
                None
                if row["resolution_confidence"] is None
                else float(row["resolution_confidence"])
            ),
            warnings_json=str(row["warnings_json"] or "[]"),
        )

    @staticmethod
    def _row_to_audiobook(row: sqlite3.Row) -> StoredAudiobook:
        return StoredAudiobook(
            id=int(row["id"]),
            uuid=str(row["uuid"]),
            title=str(row["title"]),
            project_dir=Path(str(row["project_dir"])),
            source_text=str(row["source_text"] or ""),
            output_dir=Path(str(row["output_dir"] or "")),
            split_mode=str(row["split_mode"] or ""),
            export_mode=str(row["export_mode"] or ""),
            engine_config_json=str(row["engine_config_json"] or "{}"),
            project_settings_json=str(row["project_settings_json"] or "{}"),
            clean_mp3_path=str(row["clean_mp3_path"] or ""),
            mix_mp3_path=str(row["mix_mp3_path"] or ""),
        )

    @staticmethod
    def _clone_project_path(path: Path, source_root: Path, clone_root: Path) -> Path:
        if not str(path):
            return path
        try:
            relative = path.resolve().relative_to(source_root.resolve())
        except (OSError, ValueError):
            return path
        return clone_root / relative

    def _write_project_manifest_for_segment(self, segment_id: int) -> None:
        segment = self.get_segment(segment_id)
        if segment is None:
            return
        audiobook = self.get_audiobook(segment.audiobook_id)
        if audiobook is not None:
            self._write_project_manifest(audiobook)

    def _write_project_manifest(self, audiobook: StoredAudiobook) -> None:
        project_dir = audiobook.project_dir
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence_index, chapter_index, chapter_title,
                       paragraph_index, paragraph_length, ends_paragraph,
                       markup_pause_before_ms, markup_pause_after_ms,
                       resolved_pause_before_ms, resolved_pause_after_ms,
                       source_text, normalized_source_text, markup_state_json,
                       voice, language, status, wav_path, duration_ms,
                       attempt_count, transcript_text, normalized_transcript_text,
                       word_timestamps_json,
                       similarity_score, wer, cer, verification_status,
                       synthesis_ms, transcription_ms, engine_config_json,
                       error_message, needs_rebuild
                FROM audiobook_segments
                WHERE audiobook_id = ?
                ORDER BY sequence_index
                """,
                (audiobook.id,),
            ).fetchall()
            audio_event_rows = connection.execute(
                """
                SELECT event_uid, event_id, command_type, raw_command,
                       source_position, anchor_segment_sequence,
                       anchor_source_word, anchor_mode, anchor_pause_offset_ms,
                       file_reference,
                       file_path, track, source_start_ms, duration_ms,
                       volume_db, loop, fade_in_ms, fade_out_ms, pan,
                       duck_db, trim_silence, target_event_uid, enabled,
                       resolved_time_ms, resolution_status,
                       resolution_confidence, warnings_json
                FROM audio_events
                WHERE audiobook_id = ?
                ORDER BY source_position, id
                """,
                (audiobook.id,),
            ).fetchall()
        segments = []
        for row in rows:
            segments.append(
                {
                    "sequence_index": int(row["sequence_index"] or 0),
                    "chapter_index": int(row["chapter_index"] or 0),
                    "chapter_title": str(row["chapter_title"] or ""),
                    "paragraph_index": int(row["paragraph_index"] or 0),
                    "paragraph_length": int(row["paragraph_length"] or 0),
                    "ends_paragraph": bool(row["ends_paragraph"]),
                    "markup_pause_before_ms": int(row["markup_pause_before_ms"] or 0),
                    "markup_pause_after_ms": row["markup_pause_after_ms"],
                    "resolved_pause_before_ms": row["resolved_pause_before_ms"],
                    "resolved_pause_after_ms": row["resolved_pause_after_ms"],
                    "source_text": str(row["source_text"] or ""),
                    "normalized_source_text": str(row["normalized_source_text"] or ""),
                    "markup_state": self._json_to_dict(row["markup_state_json"]),
                    "voice": str(row["voice"] or ""),
                    "language": str(row["language"] or ""),
                    "status": str(row["status"] or ""),
                    "wav_path": self._path_for_manifest(row["wav_path"], project_dir),
                    "duration_ms": int(row["duration_ms"] or 0),
                    "attempt_count": int(row["attempt_count"] or 0),
                    "transcript_text": str(row["transcript_text"] or ""),
                    "normalized_transcript_text": str(
                        row["normalized_transcript_text"] or ""
                    ),
                    "word_timestamps": self._json_to_list(
                        row["word_timestamps_json"]
                    ),
                    "similarity_score": row["similarity_score"],
                    "wer": row["wer"],
                    "cer": row["cer"],
                    "verification_status": str(
                        row["verification_status"] or "not_verified"
                    ),
                    "synthesis_ms": int(row["synthesis_ms"] or 0),
                    "transcription_ms": int(row["transcription_ms"] or 0),
                    "engine_config": self._json_to_dict(row["engine_config_json"]),
                    "error_message": str(row["error_message"] or ""),
                    "needs_rebuild": bool(row["needs_rebuild"]),
                }
            )
        audio_events = [
            {
                "event_uid": str(row["event_uid"]),
                "event_id": str(row["event_id"]),
                "command_type": str(row["command_type"]),
                "raw_command": str(row["raw_command"]),
                "source_position": int(row["source_position"] or 0),
                "anchor_segment_sequence": int(
                    row["anchor_segment_sequence"] or 0
                ),
                "anchor_source_word": int(row["anchor_source_word"] or 0),
                "anchor_mode": str(row["anchor_mode"] or "word_boundary"),
                "anchor_pause_offset_ms": int(
                    row["anchor_pause_offset_ms"] or 0
                ),
                "file_reference": str(row["file_reference"] or ""),
                "file_path": self._path_for_manifest(row["file_path"], project_dir),
                "track": str(row["track"] or "sfx"),
                "source_start_ms": int(row["source_start_ms"] or 0),
                "duration_ms": row["duration_ms"],
                "volume_db": float(row["volume_db"] or 0.0),
                "loop": bool(row["loop"]),
                "fade_in_ms": int(row["fade_in_ms"] or 0),
                "fade_out_ms": int(row["fade_out_ms"] or 0),
                "pan": float(row["pan"] or 0.0),
                "duck_db": float(row["duck_db"] or 0.0),
                "trim_silence": bool(row["trim_silence"]),
                "target_event_uid": str(row["target_event_uid"] or ""),
                "enabled": bool(row["enabled"]),
                "resolved_time_ms": row["resolved_time_ms"],
                "resolution_status": str(
                    row["resolution_status"] or "pending_whisper"
                ),
                "resolution_confidence": row["resolution_confidence"],
                "warnings": self._json_to_list(row["warnings_json"]),
            }
            for row in audio_event_rows
        ]
        project_settings = self._json_to_dict(audiobook.project_settings_json)
        project_settings.pop("current_project_id", None)
        manifest = {
            "schema": "localtext2voice.project",
            "version": 2,
            "uuid": audiobook.uuid,
            "title": audiobook.title,
            "source_text": audiobook.source_text,
            "source_hash": self._hash_text(audiobook.source_text),
            "output_dir": self._path_for_manifest(audiobook.output_dir, project_dir),
            "split_mode": audiobook.split_mode,
            "export_mode": audiobook.export_mode,
            "engine_config": self._json_to_dict(audiobook.engine_config_json),
            "project_settings": project_settings,
            "clean_mp3_path": self._path_for_manifest(
                audiobook.clean_mp3_path,
                project_dir,
            ),
            "mix_mp3_path": self._path_for_manifest(
                audiobook.mix_mp3_path,
                project_dir,
            ),
            "segments": segments,
            "audio_events": audio_events,
            "updated_at": self._now(),
        }
        project_dir.mkdir(parents=True, exist_ok=True)
        source_tmp = project_dir / "source.txt.tmp"
        source_tmp.write_text(audiobook.source_text, encoding="utf-8")
        source_tmp.replace(project_dir / "source.txt")
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
        for filename in (PROJECT_MANIFEST_NAME, LEGACY_PROJECT_MANIFEST_NAME):
            temporary_path = project_dir / f"{filename}.tmp"
            temporary_path.write_text(manifest_text, encoding="utf-8")
            temporary_path.replace(project_dir / filename)

    @staticmethod
    def _json_to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_to_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _manifest_int(value: Any, default: int = 0) -> int:
        try:
            if value is None or value == "":
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _manifest_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _path_for_manifest(cls, path_value: Any, project_dir: Path) -> str:
        text = str(path_value or "").strip()
        if not text:
            return ""
        path = Path(text)
        if not path.is_absolute():
            return cls._path_to_manifest_text(path)
        try:
            relative = path.resolve().relative_to(project_dir.resolve())
            return cls._path_to_manifest_text(relative)
        except (OSError, ValueError):
            return str(path)

    @staticmethod
    def _path_to_manifest_text(path: Path) -> str:
        return path.as_posix()

    @staticmethod
    def _path_from_manifest(path_value: Any, project_dir: Path) -> Path:
        text = str(path_value or "").strip()
        if not text:
            return Path()
        path = Path(text)
        return path if path.is_absolute() else project_dir / path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _voice_from_config(voice_config: dict[str, Any]) -> str:
        for key in ("voice", "speaker", "voice_id", "voice_id_or_path", "voice_id_or_name"):
            value = str(voice_config.get(key, "")).strip()
            if value:
                return value
        return ""

    @staticmethod
    def _language_from_config(voice_config: dict[str, Any]) -> str:
        for key in ("language", "lang", "locale"):
            value = str(voice_config.get(key, "")).strip()
            if value:
                return value
        voice_id = str(voice_config.get("voice_id", "")).strip()
        if voice_id:
            return voice_id.split("/", 1)[0].replace("_", "-")
        model_path = str(voice_config.get("model_path", "")).strip()
        if model_path:
            parts = Path(model_path).parts
            for part in reversed(parts):
                if "_" in part and len(part) <= 8:
                    return part.replace("_", "-")
        return ""

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_for_similarity(text: str) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()

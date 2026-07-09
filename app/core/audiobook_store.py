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

from .text_processor import TextChunk


CURRENT_DB_SCHEMA_VERSION = 1
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
                       similarity_score, wer, cer, verification_status,
                       synthesis_ms, transcription_ms, engine_config_json,
                       error_message, needs_rebuild
                FROM audiobook_segments
                WHERE audiobook_id = ?
                ORDER BY sequence_index
                """,
                (source_id,),
            ).fetchall()
            for row in source_rows:
                wav_path = self._clone_project_path(
                    Path(str(row["wav_path"] or "")),
                    source.project_dir,
                    clone.project_dir,
                )
                connection.execute(
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
                        similarity_score, wer, cer, verification_status,
                        synthesis_ms, transcription_ms, engine_config_json,
                        error_message, needs_rebuild, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "DELETE FROM audiobook_segments WHERE audiobook_id = ?",
                (audiobook_id,),
            )
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
                    connection.execute(
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
                            similarity_score, wer, cer, verification_status,
                            synthesis_ms, transcription_ms, engine_config_json,
                            error_message, needs_rebuild, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audiobook_id,
                            self._manifest_int(segment.get("sequence_index")),
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
                    mapping[(chapter_index, chunk_index)] = int(cursor.lastrowid)
            connection.execute(
                "UPDATE audiobooks SET status = ?, updated_at = ? WHERE id = ?",
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
        self._write_project_manifest_for_segment(segment_id)

    def update_segment_text(self, segment_id: int, source_text: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET source_text = ?, normalized_source_text = ?,
                    status = ?, transcript_text = '',
                    normalized_transcript_text = '', similarity_score = NULL,
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
        self._write_project_manifest_for_segment(segment_id)

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

    def update_segment_verification(
        self,
        segment_id: int,
        transcript_text: str,
        similarity_score: float,
        wer: float,
        cer: float,
        verification_status: str,
        transcription_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE audiobook_segments
                SET transcript_text = ?, normalized_transcript_text = ?,
                    similarity_score = ?, wer = ?, cer = ?,
                    verification_status = ?, transcription_ms = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    transcript_text,
                    normalize_for_similarity(transcript_text),
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
                       similarity_score, wer, cer, verification_status,
                       synthesis_ms, transcription_ms, engine_config_json,
                       error_message, needs_rebuild
                FROM audiobook_segments
                WHERE audiobook_id = ?
                ORDER BY sequence_index
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
        project_settings = self._json_to_dict(audiobook.project_settings_json)
        project_settings.pop("current_project_id", None)
        manifest = {
            "schema": "localtext2voice.project",
            "version": 1,
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

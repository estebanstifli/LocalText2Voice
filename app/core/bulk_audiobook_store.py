from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.utils.paths import app_data_root


@dataclass(frozen=True)
class BulkAudiobookBatch:
    id: int
    uuid: str
    title: str
    status: str
    task_type: str
    config_json: str
    created_at: str
    updated_at: str
    started_at: str = ""
    finished_at: str = ""

    def config(self) -> dict[str, Any]:
        try:
            value = json.loads(self.config_json or "{}")
        except json.JSONDecodeError:
            value = {}
        return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class BulkAudiobookItem:
    id: int
    batch_id: int
    position: int
    source_path: str
    source_sha256: str
    title: str
    music_path: str
    status: str
    audiobook_id: int | None
    engine_job_id: str
    progress_current: int
    progress_total: int
    message: str
    error_message: str
    clean_audio_path: str
    mix_audio_path: str
    metrics_json: str
    created_at: str
    updated_at: str

    @property
    def clean_mp3_path(self) -> str:
        return self.clean_audio_path

    @property
    def mix_mp3_path(self) -> str:
        return self.mix_audio_path

    def metrics(self) -> dict[str, Any]:
        try:
            value = json.loads(self.metrics_json or "{}")
        except json.JSONDecodeError:
            value = {}
        return dict(value) if isinstance(value, dict) else {}


class BulkAudiobookStore:
    """Persistent desktop queue whose items point at normal audiobook projects."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (
            app_data_root() / "projects" / "bulk_audiobooks.sqlite3"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self.recover_interrupted()

    def create_batch(
        self,
        title: str,
        config: dict[str, Any],
        items: list[dict[str, Any]],
        *,
        task_type: str = "txt_to_audiobooks",
    ) -> BulkAudiobookBatch:
        if not items:
            raise ValueError("A bulk audiobook task requires at least one item.")
        now = self._now()
        batch_uuid = str(uuid.uuid4())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO bulk_audiobook_batches (
                    uuid, title, status, task_type, config_json,
                    created_at, updated_at
                )
                VALUES (?, ?, 'paused', ?, ?, ?, ?)
                """,
                (
                    batch_uuid,
                    title.strip() or "Bulk Audiobooks",
                    task_type,
                    json.dumps(config, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )
            batch_id = int(cursor.lastrowid)
            for position, item in enumerate(items):
                connection.execute(
                    """
                    INSERT INTO bulk_audiobook_items (
                        batch_id, position, source_path, source_sha256,
                        title, music_path, status, audiobook_id,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (
                        batch_id,
                        position,
                        str(item.get("source_path", "")),
                        str(item.get("source_sha256", "")),
                        str(item.get("title", "Audiobook")),
                        str(item.get("music_path", "")),
                        item.get("audiobook_id"),
                        now,
                        now,
                    ),
                )
        batch = self.get_batch(batch_id)
        if batch is None:  # pragma: no cover - defensive database guard
            raise RuntimeError("Bulk audiobook task was not created.")
        return batch

    def get_batch(self, batch_id: int | None) -> BulkAudiobookBatch | None:
        if batch_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM bulk_audiobook_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
        return self._batch_from_row(row) if row is not None else None

    def list_batches(self, limit: int = 100) -> list[BulkAudiobookBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM bulk_audiobook_batches
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [self._batch_from_row(row) for row in rows]

    def list_items(self, batch_id: int) -> list[BulkAudiobookItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM bulk_audiobook_items
                WHERE batch_id = ?
                ORDER BY position, id
                """,
                (batch_id,),
            ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def next_queued_item(self, batch_id: int) -> BulkAudiobookItem | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM bulk_audiobook_items
                WHERE batch_id = ? AND status = 'queued'
                ORDER BY position, id
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
        return self._item_from_row(row) if row is not None else None

    def set_batch_status(self, batch_id: int, status: str) -> None:
        now = self._now()
        started_at = now if status == "running" else ""
        finished_at = (
            now if status in {"complete", "complete_with_errors", "cancelled"} else ""
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE bulk_audiobook_batches
                SET status = ?, updated_at = ?,
                    started_at = CASE
                        WHEN ? <> '' AND started_at = '' THEN ?
                        ELSE started_at
                    END,
                    finished_at = CASE
                        WHEN ? <> '' THEN ?
                        WHEN ? IN ('queued', 'running', 'paused', 'pausing') THEN ''
                        ELSE finished_at
                    END
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    started_at,
                    started_at,
                    finished_at,
                    finished_at,
                    status,
                    batch_id,
                ),
            )

    def update_item(self, item_id: int, **changes: Any) -> None:
        allowed = {
            "status",
            "engine_job_id",
            "progress_current",
            "progress_total",
            "message",
            "error_message",
            "clean_audio_path",
            "mix_audio_path",
            "clean_mp3_path",
            "mix_mp3_path",
            "metrics_json",
            "audiobook_id",
            "music_path",
            "title",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if "clean_audio_path" in values:
            values.setdefault("clean_mp3_path", values["clean_audio_path"])
        if "mix_audio_path" in values:
            values.setdefault("mix_mp3_path", values["mix_audio_path"])
        if not values:
            return
        values["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE bulk_audiobook_items SET {assignments} WHERE id = ?",
                (*values.values(), item_id),
            )

    def retry_failed(self, batch_id: int) -> int:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE bulk_audiobook_items
                SET status = 'queued', engine_job_id = '',
                    progress_current = 0, progress_total = 0,
                    message = 'Queued for retry.', error_message = '',
                    clean_audio_path = '', mix_audio_path = '',
                    clean_mp3_path = '', mix_mp3_path = '', metrics_json = '{}',
                    updated_at = ?
                WHERE batch_id = ? AND status IN ('failed', 'cancelled')
                """,
                (now, batch_id),
            )
            connection.execute(
                """
                UPDATE bulk_audiobook_batches
                SET status = 'paused', finished_at = '', updated_at = ?
                WHERE id = ?
                """,
                (now, batch_id),
            )
        return int(cursor.rowcount)

    def cancel_pending(self, batch_id: int) -> int:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE bulk_audiobook_items
                SET status = 'cancelled', message = 'Cancelled.', updated_at = ?
                WHERE batch_id = ? AND status = 'queued'
                """,
                (now, batch_id),
            )
        return int(cursor.rowcount)

    def finish_batch_from_items(self, batch_id: int) -> str:
        items = self.list_items(batch_id)
        if any(item.status == "queued" for item in items):
            status = "paused"
        elif any(item.status == "running" for item in items):
            status = "running"
        elif any(item.status == "failed" for item in items):
            status = "complete_with_errors"
        elif items and all(item.status == "cancelled" for item in items):
            status = "cancelled"
        elif any(item.status == "cancelled" for item in items):
            status = "cancelled"
        else:
            status = "complete"
        self.set_batch_status(batch_id, status)
        return status

    def recover_interrupted(self) -> int:
        """Turn an uncleanly stopped desktop run into an explicitly resumable batch."""

        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE bulk_audiobook_items
                SET status = 'queued', engine_job_id = '',
                    message = 'Interrupted; ready to resume.', updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE bulk_audiobook_batches
                SET status = 'paused', updated_at = ?
                WHERE status IN ('queued', 'running', 'pausing')
                """,
                (now,),
            )
        return int(cursor.rowcount)

    def counts(self, batch_id: int) -> dict[str, int]:
        result = {
            "total": 0,
            "queued": 0,
            "running": 0,
            "complete": 0,
            "failed": 0,
            "cancelled": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM bulk_audiobook_items
                WHERE batch_id = ?
                GROUP BY status
                """,
                (batch_id,),
            ).fetchall()
        for row in rows:
            status = str(row["status"])
            count = int(row["count"])
            result[status] = count
            result["total"] += count
        return result

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bulk_audiobook_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS bulk_audiobook_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    music_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    audiobook_id INTEGER,
                    engine_job_id TEXT NOT NULL DEFAULT '',
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    clean_audio_path TEXT NOT NULL DEFAULT '',
                    mix_audio_path TEXT NOT NULL DEFAULT '',
                    clean_mp3_path TEXT NOT NULL DEFAULT '',
                    mix_mp3_path TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id)
                        REFERENCES bulk_audiobook_batches(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_bulk_items_batch_position
                ON bulk_audiobook_items(batch_id, position);

                CREATE INDEX IF NOT EXISTS idx_bulk_items_status
                ON bulk_audiobook_items(status);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(bulk_audiobook_items)"
                ).fetchall()
            }
            missing_columns = {
                "metrics_json": "TEXT NOT NULL DEFAULT '{}'",
                "clean_audio_path": "TEXT NOT NULL DEFAULT ''",
                "mix_audio_path": "TEXT NOT NULL DEFAULT ''",
                "clean_mp3_path": "TEXT NOT NULL DEFAULT ''",
                "mix_mp3_path": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in missing_columns.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE bulk_audiobook_items ADD COLUMN "
                        f"{name} {definition}"
                    )
            connection.execute(
                """
                UPDATE bulk_audiobook_items
                SET clean_audio_path = CASE
                        WHEN clean_audio_path = '' THEN clean_mp3_path
                        ELSE clean_audio_path
                    END,
                    mix_audio_path = CASE
                        WHEN mix_audio_path = '' THEN mix_mp3_path
                        ELSE mix_audio_path
                    END
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> BulkAudiobookBatch:
        return BulkAudiobookBatch(
            id=int(row["id"]),
            uuid=str(row["uuid"]),
            title=str(row["title"]),
            status=str(row["status"]),
            task_type=str(row["task_type"]),
            config_json=str(row["config_json"] or "{}"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=str(row["started_at"] or ""),
            finished_at=str(row["finished_at"] or ""),
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> BulkAudiobookItem:
        return BulkAudiobookItem(
            id=int(row["id"]),
            batch_id=int(row["batch_id"]),
            position=int(row["position"]),
            source_path=str(row["source_path"]),
            source_sha256=str(row["source_sha256"] or ""),
            title=str(row["title"]),
            music_path=str(row["music_path"] or ""),
            status=str(row["status"]),
            audiobook_id=(
                int(row["audiobook_id"])
                if row["audiobook_id"] is not None
                else None
            ),
            engine_job_id=str(row["engine_job_id"] or ""),
            progress_current=int(row["progress_current"] or 0),
            progress_total=int(row["progress_total"] or 0),
            message=str(row["message"] or ""),
            error_message=str(row["error_message"] or ""),
            clean_audio_path=str(row["clean_audio_path"] or ""),
            mix_audio_path=str(row["mix_audio_path"] or ""),
            metrics_json=str(row["metrics_json"] or "{}"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

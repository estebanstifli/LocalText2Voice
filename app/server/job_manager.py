from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.core.audio_pipeline import AudioPipeline, AudioPipelineError, GenerationCancelled
from app.server.ltv_service import LocalText2VoiceService
from app.utils.paths import app_data_root


@dataclass(frozen=True)
class ServerJob:
    job_id: str
    status: str
    title: str
    created_at: str
    updated_at: str
    started_at: str = ""
    finished_at: str = ""
    progress_current: int = 0
    progress_total: int = 0
    progress_percent: float = 0.0
    message: str = ""
    clean_mp3_path: str = ""
    mix_mp3_path: str = ""
    audiobook_id: int | None = None
    error_message: str = ""
    request_json: str = "{}"
    result_json: str = "{}"
    logs_json: str = "[]"
    cancel_requested: bool = False

    def to_dict(self, include_logs: bool = True) -> dict[str, Any]:
        try:
            request = json.loads(self.request_json)
        except json.JSONDecodeError:
            request = {}
        request.pop("text", None)
        try:
            result = json.loads(self.result_json)
        except json.JSONDecodeError:
            result = {}
        try:
            logs = json.loads(self.logs_json)
        except json.JSONDecodeError:
            logs = []
        payload = {
            "job_id": self.job_id,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": {
                "current": self.progress_current,
                "total": self.progress_total,
                "percent": self.progress_percent,
                "message": self.message,
            },
            "clean_mp3_path": self.clean_mp3_path,
            "mix_mp3_path": self.mix_mp3_path,
            "audiobook_id": self.audiobook_id,
            "error_message": self.error_message,
            "cancel_requested": self.cancel_requested,
            "request": request,
            "result": result,
        }
        if include_logs:
            payload["logs"] = logs
        return payload


class LocalServerJobManager:
    DB_SCHEMA_VERSION = 1

    def __init__(
        self,
        service: LocalText2VoiceService | None = None,
        db_path: Path | None = None,
        max_parallel_jobs: int = 1,
    ) -> None:
        self.service = service or LocalText2VoiceService()
        self.db_path = db_path or app_data_root() / "server" / "local_server_jobs.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_parallel_jobs = max(1, int(max_parallel_jobs))
        self._db_lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop_requested = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._active_cancel_events: dict[str, threading.Event] = {}
        self._active_pipelines: dict[str, Any] = {}
        self._active_lock = threading.RLock()
        self._ensure_schema()

    def submit(self, request: dict[str, Any]) -> ServerJob:
        text = str(request.get("text", "")).strip()
        if not text:
            raise ValueError("Text is required.")
        job_id = str(uuid.uuid4())
        now = self._now()
        title = str(request.get("title") or "Audiobook").strip() or "Audiobook"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO server_jobs (
                    job_id, status, title, created_at, updated_at,
                    request_json, logs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "queued",
                    title,
                    now,
                    now,
                    json.dumps(request, ensure_ascii=False, default=str),
                    "[]",
                ),
            )
        self._ensure_worker()
        self._queue.put(job_id)
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> ServerJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_id, status, title, created_at, updated_at,
                       started_at, finished_at, progress_current, progress_total,
                       progress_percent, message, clean_mp3_path, mix_mp3_path,
                       audiobook_id, error_message, request_json, result_json,
                       logs_json, cancel_requested
                FROM server_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[ServerJob]:
        limit = max(1, min(500, int(limit)))
        params: list[Any] = []
        query = """
            SELECT job_id, status, title, created_at, updated_at,
                   started_at, finished_at, progress_current, progress_total,
                   progress_percent, message, clean_mp3_path, mix_mp3_path,
                   audiobook_id, error_message, request_json, result_json,
                   logs_json, cancel_requested
            FROM server_jobs
        """
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_job(row) for row in rows]

    def cancel_job(self, job_id: str) -> ServerJob | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status in {"complete", "failed", "cancelled"}:
            return job
        self._update_job(job_id, cancel_requested=1, message="Cancellation requested.")
        if job.status == "queued":
            self._finish_job(job_id, "cancelled", message="Generation cancelled.")
            return self.get_job(job_id)
        with self._active_lock:
            event = self._active_cancel_events.get(job_id)
            pipeline = self._active_pipelines.get(job_id)
        if event is not None:
            event.set()
        if pipeline is not None:
            self._cancel_operation(pipeline)
        return self.get_job(job_id)

    def shutdown(self) -> None:
        self._stop_requested.set()
        self._queue.put(None)
        with self._active_lock:
            events = list(self._active_cancel_events.values())
            pipelines = list(self._active_pipelines.values())
        for event in events:
            event.set()
        for pipeline in pipelines:
            self._cancel_operation(pipeline)
        thread = self._worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def _ensure_worker(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_requested.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="LocalText2VoiceServerJobs",
            daemon=True,
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        while not self._stop_requested.is_set():
            try:
                job_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job_id is None:
                break
            try:
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None or job.status == "cancelled":
            return
        if job.cancel_requested:
            self._finish_job(job_id, "cancelled", message="Generation cancelled.")
            return
        request = json.loads(job.request_json)
        cancel_event = threading.Event()
        with self._active_lock:
            self._active_cancel_events[job_id] = cancel_event
        self._update_job(
            job_id,
            status="running",
            started_at=self._now(),
            updated_at=self._now(),
            message="Starting generation...",
        )

        def progress(current: int, total: int, message: str) -> None:
            percent = round((current / total) * 100, 2) if total else 0.0
            self._update_job(
                job_id,
                progress_current=max(0, int(current)),
                progress_total=max(0, int(total)),
                progress_percent=percent,
                message=message,
            )

        def log(message: str) -> None:
            self._append_log(job_id, message)

        def on_pipeline(pipeline: Any) -> None:
            with self._active_lock:
                self._active_pipelines[job_id] = pipeline
            if cancel_event.is_set():
                self._cancel_operation(pipeline)

        try:
            result = self.service.generate_audio(
                request,
                progress_callback=progress,
                log_callback=log,
                on_pipeline=on_pipeline,
            )
            if cancel_event.is_set():
                self._finish_job(job_id, "cancelled", message="Generation cancelled.")
                return
            self._update_job(
                job_id,
                status="complete",
                finished_at=self._now(),
                progress_percent=100.0,
                message="Generation complete.",
                clean_mp3_path=str(result.get("clean_mp3", "")),
                mix_mp3_path=str(result.get("mix_mp3", "")),
                audiobook_id=result.get("audiobook_id"),
                result_json=json.dumps(result, ensure_ascii=False, default=str),
            )
        except GenerationCancelled:
            self._finish_job(job_id, "cancelled", message="Generation cancelled.")
        except (AudioPipelineError, ValueError, OSError) as exc:
            self._finish_job(job_id, "failed", message=str(exc), error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive guard for background jobs
            traceback.print_exc()
            self._finish_job(
                job_id,
                "failed",
                message=f"Unexpected server generation error: {exc}",
                error_message=str(exc),
            )
        finally:
            with self._active_lock:
                self._active_cancel_events.pop(job_id, None)
                self._active_pipelines.pop(job_id, None)

    def _finish_job(
        self,
        job_id: str,
        status: str,
        *,
        message: str = "",
        error_message: str = "",
    ) -> None:
        self._update_job(
            job_id,
            status=status,
            finished_at=self._now(),
            updated_at=self._now(),
            message=message,
            error_message=error_message,
        )

    @staticmethod
    def _cancel_operation(operation: Any) -> None:
        cancel = getattr(operation, "cancel", None)
        if callable(cancel):
            cancel()
            return
        request_cancel = getattr(operation, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()

    def _append_log(self, job_id: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}"
        with self._db_lock, self._connect_unlocked() as connection:
            row = connection.execute(
                "SELECT logs_json FROM server_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            try:
                logs = json.loads(row["logs_json"] if row else "[]")
            except json.JSONDecodeError:
                logs = []
            logs.append(line)
            logs = logs[-800:]
            connection.execute(
                """
                UPDATE server_jobs
                SET logs_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(logs, ensure_ascii=False), self._now(), job_id),
            )

    def _update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields.setdefault("updated_at", self._now())
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values())
        values.append(job_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE server_jobs SET {assignments} WHERE job_id = ?",
                values,
            )

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS server_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS server_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    progress_percent REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    clean_mp3_path TEXT NOT NULL DEFAULT '',
                    mix_mp3_path TEXT NOT NULL DEFAULT '',
                    audiobook_id INTEGER,
                    error_message TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    logs_json TEXT NOT NULL DEFAULT '[]',
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO server_meta (key, value)
                VALUES ('schema_version', ?)
                """,
                (str(self.DB_SCHEMA_VERSION),),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._db_lock:
            with self._connect_unlocked() as connection:
                yield connection

    @contextmanager
    def _connect_unlocked(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ServerJob:
        return ServerJob(
            job_id=str(row["job_id"]),
            status=str(row["status"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]),
            progress_current=int(row["progress_current"]),
            progress_total=int(row["progress_total"]),
            progress_percent=float(row["progress_percent"]),
            message=str(row["message"]),
            clean_mp3_path=str(row["clean_mp3_path"]),
            mix_mp3_path=str(row["mix_mp3_path"]),
            audiobook_id=int(row["audiobook_id"]) if row["audiobook_id"] is not None else None,
            error_message=str(row["error_message"]),
            request_json=str(row["request_json"]),
            result_json=str(row["result_json"]),
            logs_json=str(row["logs_json"]),
            cancel_requested=bool(row["cancel_requested"]),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wait_for_job(
    manager: LocalServerJobManager,
    job_id: str,
    timeout_seconds: float,
) -> ServerJob | None:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job is None or job.status in {"complete", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    return manager.get_job(job_id)

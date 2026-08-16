from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.core.audiobook_store import AudiobookStore
from app.core.bulk_audiobook_store import (
    BulkAudiobookBatch,
    BulkAudiobookItem,
    BulkAudiobookStore,
)
from app.server.engine_host_client import EngineHostClient, EngineHostClientError


def bulk_generation_request(
    batch: BulkAudiobookBatch,
    item: BulkAudiobookItem,
    audiobook_store: AudiobookStore,
) -> dict[str, Any]:
    audiobook = audiobook_store.get_audiobook(item.audiobook_id)
    if audiobook is None:
        raise ValueError(f"Audiobook project not found for bulk item {item.id}.")
    config = batch.config()
    voice_config_value = config.get("voice_config", {})
    voice_config = (
        dict(voice_config_value) if isinstance(voice_config_value, dict) else {}
    )
    generation_value = config.get("generation_settings", {})
    generation_settings = (
        dict(generation_value) if isinstance(generation_value, dict) else {}
    )
    generation_settings["bulk_task"] = {
        "batch_id": batch.id,
        "batch_uuid": batch.uuid,
        "batch_title": batch.title,
        "item_id": item.id,
        "source_path": item.source_path,
        "source_sha256": item.source_sha256,
    }
    engine_id = str(voice_config.get("engine", "piper") or "piper")
    request: dict[str, Any] = {
        "text": audiobook.source_text,
        "title": item.title,
        "engine_id": engine_id,
        "voice": str(
            voice_config.get("voice") or voice_config.get("voice_id") or ""
        ),
        "language": str(
            voice_config.get("language") or voice_config.get("lang") or ""
        ),
        "speed": float(voice_config.get("speed", 1.0) or 1.0),
        "voice_config": voice_config,
        "generation_settings": generation_settings,
        "output_dir": str(audiobook.output_dir),
        "split_mode": str(config.get("split_mode", audiobook.split_mode)),
        "export_mode": "single",
        "audio_format": str(generation_settings.get("audio_format", "mp3")),
        "audio_quality": str(generation_settings.get("audio_quality", "standard")),
        "chunk_size": int(config.get("chunk_size", 300) or 300),
        "project_audiobook_id": audiobook.id,
        "background_music": item.music_path,
        "mix_policy": "always" if item.music_path else "clean_only",
        # Batches created before Creation flow existed always skipped review.
        # Keep that behavior when resuming an old persisted task.
        "review_policy": str(config.get("review_policy", "off") or "off"),
        "client": "desktop_bulk",
    }
    for key in (
        "reference_audio_path",
        "reference_text",
        "instruct",
        "use_stress",
    ):
        if key in voice_config:
            request[key] = voice_config[key]
    return request


class BulkAudiobookWorker(QObject):
    """Run one persistent bulk batch sequentially through the shared engine host."""

    batchUpdated = Signal(int)
    itemUpdated = Signal(int)
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(int, str)

    def __init__(
        self,
        store: BulkAudiobookStore,
        audiobook_store: AudiobookStore,
        client: EngineHostClient,
        batch_id: int,
    ) -> None:
        super().__init__()
        self.store = store
        self.audiobook_store = audiobook_store
        self.client = client
        self.batch_id = batch_id
        self._pause_requested = threading.Event()
        self._cancel_requested = threading.Event()
        self._stop_requested = threading.Event()
        self._current_job_id = ""

    @Slot()
    def run(self) -> None:
        batch = self.store.get_batch(self.batch_id)
        if batch is None:
            self.finished.emit(self.batch_id, "failed")
            return
        self.store.set_batch_status(self.batch_id, "running")
        self.batchUpdated.emit(self.batch_id)
        self.log.emit(f"Bulk task started: {batch.title}")
        try:
            while True:
                if self._cancel_requested.is_set():
                    self.store.cancel_pending(self.batch_id)
                    self.store.set_batch_status(self.batch_id, "cancelled")
                    self.batchUpdated.emit(self.batch_id)
                    self.finished.emit(self.batch_id, "cancelled")
                    return
                if self._stop_requested.is_set():
                    self.store.set_batch_status(self.batch_id, "paused")
                    self.batchUpdated.emit(self.batch_id)
                    self.finished.emit(self.batch_id, "paused")
                    return
                if self._pause_requested.is_set():
                    self.store.set_batch_status(self.batch_id, "paused")
                    self.batchUpdated.emit(self.batch_id)
                    self.finished.emit(self.batch_id, "paused")
                    return
                item = self.store.next_queued_item(self.batch_id)
                if item is None:
                    status = self.store.finish_batch_from_items(self.batch_id)
                    self.batchUpdated.emit(self.batch_id)
                    self.finished.emit(self.batch_id, status)
                    return
                self._run_item(batch, item)
        except Exception as exc:  # pragma: no cover - defensive worker guard
            traceback.print_exc()
            self.log.emit(f"Unexpected Bulk Audiobooks error: {exc}")
            self.store.set_batch_status(self.batch_id, "paused")
            self.batchUpdated.emit(self.batch_id)
            self.finished.emit(self.batch_id, "paused")

    def request_pause(self) -> None:
        self._pause_requested.set()
        self.store.set_batch_status(self.batch_id, "pausing")
        self.batchUpdated.emit(self.batch_id)

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def request_stop(self) -> None:
        """Stop immediately while keeping the current and pending items resumable."""

        self._stop_requested.set()

    def _run_item(
        self,
        batch: BulkAudiobookBatch,
        item: BulkAudiobookItem,
    ) -> None:
        self.store.update_item(
            item.id,
            status="running",
            message="Connecting to the shared generation engine...",
            error_message="",
            progress_current=0,
            progress_total=0,
        )
        self.itemUpdated.emit(item.id)
        self.batchUpdated.emit(self.batch_id)
        self.log.emit(f"Bulk item {item.position + 1}: {item.title}")
        try:
            request = bulk_generation_request(batch, item, self.audiobook_store)
            job = self.client.submit_job(request)
            job_id = str(job.get("job_id", ""))
            if not job_id:
                raise EngineHostClientError("The engine host did not return a job id.")
            self._current_job_id = job_id
            self.store.update_item(item.id, engine_job_id=job_id)
            seen_logs = 0
            cancel_sent = False
            while True:
                if (
                    self._cancel_requested.is_set() or self._stop_requested.is_set()
                ) and not cancel_sent:
                    self.client.cancel_job(job_id)
                    cancel_sent = True
                job = self.client.get_job(job_id)
                logs = job.get("logs", [])
                if isinstance(logs, list):
                    for line in logs[seen_logs:]:
                        self.log.emit(f"{item.title}: {line}")
                    seen_logs = len(logs)
                progress = job.get("progress", {})
                if not isinstance(progress, dict):
                    progress = {}
                current = int(progress.get("current", 0) or 0)
                total = int(progress.get("total", 0) or 0)
                progress_message = str(
                    progress.get("message", "") or "Generating audio..."
                )
                message = progress_message
                metrics = self._segment_metrics(item.audiobook_id)
                metrics_message = self._metrics_message(metrics)
                if metrics_message:
                    message = f"{message}\n{metrics_message}"
                self.store.update_item(
                    item.id,
                    progress_current=current,
                    progress_total=total,
                    message=message,
                    metrics_json=json.dumps(metrics, ensure_ascii=False),
                )
                self.progress.emit(
                    current,
                    total,
                    f"{item.title}: {progress_message}",
                )
                self.itemUpdated.emit(item.id)
                status = str(job.get("status", "")).casefold()
                if status == "complete":
                    self._complete_item(item, job)
                    return
                if status == "cancelled":
                    if self._stop_requested.is_set():
                        self.store.update_item(
                            item.id,
                            status="queued",
                            engine_job_id="",
                            progress_current=0,
                            progress_total=0,
                            message="Stopped; ready to resume.",
                        )
                    else:
                        self.store.update_item(
                            item.id,
                            status="cancelled",
                            message="Generation cancelled.",
                        )
                    self.itemUpdated.emit(item.id)
                    return
                if status == "failed":
                    error = str(
                        job.get("error_message")
                        or progress.get("message", "")
                        or "Generation failed."
                    )
                    self._fail_item(item, error)
                    return
                time.sleep(0.25)
        except Exception as exc:
            if self._stop_requested.is_set():
                self.store.update_item(
                    item.id,
                    status="queued",
                    engine_job_id="",
                    progress_current=0,
                    progress_total=0,
                    message="Stopped; ready to resume.",
                    error_message="",
                )
                self.itemUpdated.emit(item.id)
            elif self._cancel_requested.is_set():
                self.store.update_item(
                    item.id,
                    status="cancelled",
                    message="Generation cancelled.",
                    error_message="",
                )
                self.itemUpdated.emit(item.id)
            else:
                self._fail_item(item, str(exc))
        finally:
            self._current_job_id = ""
            self.batchUpdated.emit(self.batch_id)

    def _complete_item(
        self,
        item: BulkAudiobookItem,
        job: dict[str, Any],
    ) -> None:
        result_value = job.get("result", {})
        result = dict(result_value) if isinstance(result_value, dict) else {}
        audiobook_id = result.get("audiobook_id", job.get("audiobook_id"))
        try:
            resolved_id = int(audiobook_id) if audiobook_id is not None else None
        except (TypeError, ValueError):
            resolved_id = item.audiobook_id
        metrics = self._segment_metrics(resolved_id)
        review_value = result.get("review", {})
        if isinstance(review_value, dict):
            for source_key, target_key in (
                ("segments", "segments"),
                ("reviewed", "reviewed"),
                ("approved", "approved"),
                ("needs_attention", "needs_attention"),
                ("errors", "errors"),
                ("retry_attempts", "retry_attempts"),
            ):
                if source_key in review_value:
                    metrics[target_key] = int(review_value.get(source_key, 0) or 0)
        summary = self._metrics_message(metrics)
        completion_message = "Audiobook complete."
        if summary:
            completion_message = f"{completion_message}\n{summary}"
        self.store.update_item(
            item.id,
            status="complete",
            audiobook_id=resolved_id,
            progress_current=1,
            progress_total=1,
            message=completion_message,
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            clean_audio_path=str(
                result.get("clean_audio")
                or result.get("clean_mp3")
                or job.get("clean_audio_path", "")
                or job.get("clean_mp3_path", "")
                or ""
            ),
            mix_audio_path=str(
                result.get("mix_audio")
                or result.get("mix_mp3")
                or job.get("mix_audio_path", "")
                or job.get("mix_mp3_path", "")
                or ""
            ),
        )
        self.itemUpdated.emit(item.id)
        self.log.emit(f"Bulk item complete: {item.title}")

    def _fail_item(self, item: BulkAudiobookItem, error: str) -> None:
        message = error.strip() or "Generation failed."
        metrics = self._segment_metrics(item.audiobook_id)
        metrics_message = self._metrics_message(metrics)
        self.store.update_item(
            item.id,
            status="failed",
            message=(
                f"Generation failed. {metrics_message}"
                if metrics_message
                else "Generation failed."
            ),
            error_message=message,
            metrics_json=json.dumps(metrics, ensure_ascii=False),
        )
        self.itemUpdated.emit(item.id)
        self.log.emit(f"Bulk item failed ({item.title}): {message}")

    def _segment_metrics(self, audiobook_id: int | None) -> dict[str, int]:
        if audiobook_id is None:
            return {}
        try:
            segments = self.audiobook_store.list_segments(audiobook_id)
        except Exception:
            return {}
        reviewed = sum(
            1
            for segment in segments
            if segment.verification_status not in {"", "not_verified"}
        )
        return {
            "segments": len(segments),
            "reviewed": reviewed,
            "approved": sum(
                1
                for segment in segments
                if segment.verification_status == "approved"
            ),
            "needs_attention": sum(
                1
                for segment in segments
                if segment.verification_status in {"retry_needed", "review"}
            ),
            "errors": sum(
                1
                for segment in segments
                if segment.status == "failed" or bool(segment.error_message)
            ),
            "retry_attempts": sum(
                max(0, int(segment.attempt_count) - 1) for segment in segments
            ),
        }

    @staticmethod
    def _metrics_message(metrics: dict[str, int]) -> str:
        segments = int(metrics.get("segments", 0) or 0)
        if segments <= 0:
            return ""
        reviewed = int(metrics.get("reviewed", 0) or 0)
        approved = int(metrics.get("approved", 0) or 0)
        attention = int(metrics.get("needs_attention", 0) or 0)
        errors = int(metrics.get("errors", 0) or 0)
        retries = int(metrics.get("retry_attempts", 0) or 0)
        parts = [
            f"Segments: {segments} created",
            f"{reviewed} reviewed",
            f"{approved} approved",
            f"{attention} need attention",
            f"{retries} retries",
            f"{errors} generation errors",
        ]
        if not reviewed:
            parts.append("review pending or skipped")
        return " · ".join(parts)

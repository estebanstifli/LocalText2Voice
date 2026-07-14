from __future__ import annotations

import threading
import time
import traceback
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.server.engine_host_client import EngineHostClient, EngineHostClientError


class EngineHostGenerationWorker(QObject):
    """Submit and follow one UI generation job through the shared engine host."""

    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, client: EngineHostClient, request: dict[str, Any]) -> None:
        super().__init__()
        self.client = client
        self.request = request
        self.job_id = ""
        self._cancel_requested = threading.Event()

    @Slot()
    def run(self) -> None:
        seen_logs = 0
        cancel_sent = False
        try:
            self.log.emit("Connecting to the shared LocalText2Voice engine host...")
            job = self.client.submit_job(self.request)
            self.job_id = str(job.get("job_id", ""))
            if not self.job_id:
                raise EngineHostClientError("The engine host did not return a job id.")
            self.log.emit(f"Engine host job: {self.job_id}")
            while True:
                if self._cancel_requested.is_set() and not cancel_sent:
                    self.client.cancel_job(self.job_id)
                    cancel_sent = True
                job = self.client.get_job(self.job_id)
                logs = job.get("logs", [])
                if isinstance(logs, list):
                    for line in logs[seen_logs:]:
                        self.log.emit(str(line))
                    seen_logs = len(logs)
                progress = job.get("progress", {})
                if isinstance(progress, dict):
                    self.progress.emit(
                        int(progress.get("current", 0) or 0),
                        int(progress.get("total", 0) or 0),
                        str(progress.get("message", "") or "Generating audio..."),
                    )
                status = str(job.get("status", "")).casefold()
                if status == "complete":
                    result = job.get("result", {})
                    payload = dict(result) if isinstance(result, dict) else {}
                    payload.setdefault("audiobook_id", job.get("audiobook_id"))
                    payload.setdefault("clean_mp3", job.get("clean_mp3_path", ""))
                    payload.setdefault("mix_mp3", job.get("mix_mp3_path", ""))
                    self.finished.emit(payload)
                    return
                if status == "cancelled":
                    self.cancelled.emit()
                    return
                if status == "failed":
                    message = str(
                        job.get("error_message")
                        or progress.get("message", "")
                        or "Engine host generation failed."
                    )
                    self.failed.emit(message)
                    return
                time.sleep(0.25)
        except EngineHostClientError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected engine host generation error: {exc}")

    def request_cancel(self) -> None:
        self._cancel_requested.set()

from __future__ import annotations

import threading
from datetime import datetime
from copy import deepcopy

from PySide6.QtCore import QObject, Signal, Slot

from app.core.video_storyboard_install import (
    StoryboardInstallCancelled, install_comfy, install_ollama, probe_engines,
)
from app.utils.paths import large_assets_root


class StoryboardInstallWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, role: str, options: dict):
        super().__init__()
        self.role = role
        self.options = deepcopy(options)
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        log = None
        try:
            if self.role == "probe":
                result = {"inventory": probe_engines(self.options)}
            else:
                directory = large_assets_root() / "logs/storyboard-installs"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"{self.role}-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
                log = path.open("w", encoding="utf-8", buffering=1)
                def report(event):
                    message = str(event.get("message", ""))
                    token = str(self.options.get("headers", {}).get("Authorization", ""))
                    if token:
                        message = message.replace(token, "[redacted]").replace(token.removeprefix("Bearer "), "[redacted]")
                    event = {**event, "message": message}
                    log.write(f"[{datetime.now():%H:%M:%S}] {message} {event.get('current', '')}/{event.get('total', '')}\n")
                    self.progress.emit(event)
                report({"message": f"Installation log: {path}"})
                if self.role == "llm":
                    result = install_ollama(self.options, report, self.cancel_event)
                else:
                    result = install_comfy(self.role, self.options, report, self.cancel_event)
                result["log_path"] = str(path)
            self.finished.emit(result)
        except StoryboardInstallCancelled as exc:
            if log:
                log.write(str(exc) + "\n")
            self.failed.emit(str(exc))
        except Exception as exc:
            if log:
                log.write(f"{type(exc).__name__}: {exc}\n")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if log:
                log.close()

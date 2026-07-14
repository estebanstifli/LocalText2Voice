from __future__ import annotations

import traceback
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.server.engine_host_client import EngineHostClient, EngineHostClientError


class EngineHostMemoryWorker(QObject):
    log = Signal(str)
    finished = Signal(str, bool)
    failed = Signal(str)

    def __init__(
        self,
        client: EngineHostClient,
        engine_id: str,
        load: bool,
        voice_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.engine_id = engine_id
        self.load = load
        self.voice_config = dict(voice_config or {})

    @Slot()
    def run(self) -> None:
        try:
            action = "preload" if self.load else "unload"
            self.log.emit(
                f"{'Loading' if self.load else 'Unloading'} {self.engine_id} "
                "in the shared engine host..."
            )
            result = self.client.request_json(
                "POST",
                f"/engines/{self.engine_id}/{action}",
                self.voice_config if self.load else {},
                timeout=300.0 if self.load else 30.0,
            )
            loaded = bool(result.get("loaded", self.load)) if isinstance(result, dict) else self.load
            self.finished.emit(self.engine_id, loaded)
        except EngineHostClientError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"Unexpected engine memory error: {exc}")

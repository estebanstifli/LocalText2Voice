from __future__ import annotations

from typing import Any

from app.core.settings_manager import SettingsManager

from .engine_host_client import EngineHostClient


class LocalServerController:
    """Lifecycle wrapper for the shared out-of-process engine/MCP host."""

    def __init__(self, settings_manager: SettingsManager) -> None:
        self.settings_manager = settings_manager
        self.client = EngineHostClient(settings_manager)
        self._startup_error = ""

    @property
    def startup_error(self) -> str:
        return self._startup_error

    def is_running(self) -> bool:
        return self.client.health(timeout=0.25)

    def endpoint_url(self) -> str:
        return self.client.base_url()

    def start(self) -> None:
        if self.is_running():
            return
        self._startup_error = ""
        try:
            self.client.ensure_running()
        except Exception as exc:
            self._startup_error = str(exc)
            raise

    def stop(self) -> bool:
        return self.client.shutdown()

    def engine_memory(self) -> dict[str, dict[str, Any]]:
        return self.client.engine_memory()

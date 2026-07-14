from __future__ import annotations

import threading
import time
from typing import Any

from app.core.settings_manager import SettingsManager


class LocalServerController:
    """Small lifecycle wrapper used by the PySide UI."""

    def __init__(self, settings_manager: SettingsManager) -> None:
        self.settings_manager = settings_manager
        self._thread: threading.Thread | None = None
        self._server: Any | None = None
        self._startup_error: str = ""
        self._running_host: str = ""
        self._running_port: int = 0

    @property
    def startup_error(self) -> str:
        return self._startup_error

    def is_running(self) -> bool:
        thread = self._thread
        server = self._server
        return bool(
            thread is not None
            and thread.is_alive()
            and server is not None
            and getattr(server, "started", False)
            and not getattr(server, "should_exit", False)
        )

    def endpoint_url(self) -> str:
        settings = self._settings()
        host = self._running_host or str(settings.get("host", "127.0.0.1") or "127.0.0.1")
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        port = self._running_port or int(settings.get("port", 8765) or 8765)
        return f"http://{host}:{port}/mcp"

    def start(self) -> None:
        if self.is_running():
            return
        self.stop()
        self._startup_error = ""
        settings = self._settings()
        host = str(settings.get("host", "127.0.0.1") or "127.0.0.1")
        if not bool(settings.get("allow_lan", False)):
            host = "127.0.0.1"
        port = int(settings.get("port", 8765) or 8765)

        def run_server() -> None:
            try:
                import uvicorn

                from app.server.http_app import create_http_app

                app = create_http_app(self.settings_manager)
                config = uvicorn.Config(
                    app,
                    host=host,
                    port=port,
                    log_level="warning",
                    access_log=False,
                    log_config=None,
                    lifespan="on",
                )
                self._server = uvicorn.Server(config)
                self._server.run()
            except Exception as exc:  # pragma: no cover - surfaced in UI
                self._startup_error = str(exc)

        self._thread = threading.Thread(
            target=run_server,
            name="LocalText2VoiceMCPServer",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.is_running() or self._startup_error:
                break
            thread = self._thread
            if thread is not None and not thread.is_alive():
                break
            time.sleep(0.05)
        if self._startup_error:
            raise RuntimeError(self._startup_error)
        if self.is_running():
            self._running_host = host
            self._running_port = port

    def stop(self) -> None:
        server = self._server
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None
        self._server = None
        self._running_host = ""
        self._running_port = 0

    def _settings(self) -> dict[str, Any]:
        value = self.settings_manager.settings.get("local_server", {})
        return dict(value) if isinstance(value, dict) else {}

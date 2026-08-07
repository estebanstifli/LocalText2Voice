from __future__ import annotations

"""LocalText2Voice persistent engine host.

This process owns the long-lived HTTP/MCP server used by desktop bridges. It is
intentionally separate from the PySide UI and from the stdio MCP bridge so heavy
TTS engines can stay loaded in memory across multiple jobs.
"""

import argparse
import sys

import uvicorn

from app.core.settings_manager import SettingsManager
from app.server.engine_host_config import (
    ENGINE_HOST_ADDRESS,
    engine_host_stderr_log_path,
    internal_engine_host_port,
)
from app.server.http_app import create_http_app
from app.utils.gpu_detection import configure_gpu_device, detect_gpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LocalText2Voice engine host.")
    parser.add_argument("--port", type=int, default=0, help="Bind port. Defaults to app settings.")
    args = parser.parse_args()

    settings_manager = SettingsManager()
    configure_gpu_device(
        settings_manager.settings.get("gpu_device_index", "auto"),
        detect_gpus(),
    )
    host = ENGINE_HOST_ADDRESS
    port = args.port or internal_engine_host_port(settings_manager.settings)

    server_holder: dict[str, uvicorn.Server] = {}

    def request_shutdown() -> None:
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    app = create_http_app(
        settings_manager,
        shutdown_callback=request_shutdown,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    server.run()
    return 0


if __name__ == "__main__":
    log_path = engine_host_stderr_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr = log_path.open("a", encoding="utf-8", buffering=1)
    raise SystemExit(main())

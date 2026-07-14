from __future__ import annotations

"""LocalText2Voice persistent engine host.

This process owns the long-lived HTTP/MCP server used by desktop bridges. It is
intentionally separate from the PySide UI and from the stdio MCP bridge so heavy
TTS engines can stay loaded in memory across multiple jobs.
"""

import argparse
import sys
from typing import Any

import uvicorn

from app.core.settings_manager import SettingsManager
from app.server.http_app import create_http_app


def _server_settings(settings_manager: SettingsManager) -> dict[str, Any]:
    value = settings_manager.settings.get("local_server", {})
    return dict(value) if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LocalText2Voice engine host.")
    parser.add_argument("--host", default="", help="Bind host. Defaults to app settings.")
    parser.add_argument("--port", type=int, default=0, help="Bind port. Defaults to app settings.")
    parser.add_argument(
        "--allow-lan",
        action="store_true",
        help="Allow binding to non-localhost addresses when configured.",
    )
    args = parser.parse_args()

    settings_manager = SettingsManager()
    settings = _server_settings(settings_manager)
    host = args.host or str(settings.get("host", "127.0.0.1") or "127.0.0.1")
    if not args.allow_lan and not bool(settings.get("allow_lan", False)):
        host = "127.0.0.1"
    port = args.port or int(settings.get("port", 8765) or 8765)

    app = create_http_app(settings_manager)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
        lifespan="on",
    )
    uvicorn.Server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

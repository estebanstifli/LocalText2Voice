from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.paths import app_data_root


DEFAULT_INTERNAL_ENGINE_HOST_PORT = 8765
DEFAULT_REMOTE_MCP_PORT = 8766
ENGINE_HOST_ADDRESS = "127.0.0.1"


def valid_port(value: object, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1024 <= port <= 65535 else fallback


def internal_engine_host_port(settings: dict[str, Any]) -> int:
    return valid_port(
        settings.get("internal_engine_host_port"),
        DEFAULT_INTERNAL_ENGINE_HOST_PORT,
    )


def internal_engine_host_url(settings: dict[str, Any]) -> str:
    return f"http://{ENGINE_HOST_ADDRESS}:{internal_engine_host_port(settings)}"


def engine_host_stderr_log_path() -> Path:
    return app_data_root() / "logs" / "engine_host_stderr.log"

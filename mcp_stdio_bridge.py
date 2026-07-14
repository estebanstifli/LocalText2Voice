from __future__ import annotations

"""LocalText2Voice MCP stdio bridge.

This entry point is designed for Claude Desktop and other local MCP clients
that launch servers over stdio. The stdio process stays intentionally light:
it starts or reuses the persistent LocalText2Voice engine host and delegates
all real work to the local HTTP API.

Tools exposed here:
- server_info -> GET /info
- list_engines -> GET /engines
- engine_memory -> GET /engines/memory
- preload_engine -> POST /engines/{engine_id}/preload
- unload_engine -> POST /engines/{engine_id}/unload
- list_voices -> GET /voices
- list_background_music -> GET /background-music
- list_sfx -> GET /sfx
- get_markup_help -> docs/LTV_MARKUP.md fallback tool
- create_audiobook -> POST /jobs, optional polling with GET /jobs/{job_id}
- generate_audio -> alias of create_audiobook
- get_job -> GET /jobs/{job_id}
- get_jobs -> GET /jobs
- cancel_job -> POST /jobs/{job_id}/cancel

The MCP protocol uses stdout, so do not add print() debug statements here.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TypeVar

from mcp.server.fastmcp import FastMCP

from app.core.settings_manager import SettingsManager
from app.utils.paths import application_root

T = TypeVar("T")

mcp = FastMCP(
    "LocalText2Voice",
    instructions=(
        "Create audiobooks, podcast-ready narration, and audio mixes using "
        "the local LocalText2Voice persistent engine host."
    ),
    log_level="ERROR",
)

_settings_manager = SettingsManager()
_engine_host_process: subprocess.Popen[Any] | None = None


def _read_text_resource(relative_path: str) -> str:
    path = application_root() / relative_path
    if not path.is_file():
        return (
            f"# Missing resource\n\nThe LocalText2Voice resource was not found: "
            f"`{relative_path}`."
        )
    return path.read_text(encoding="utf-8", errors="replace")


def _server_settings() -> dict[str, Any]:
    _settings_manager.settings = _settings_manager.load()
    value = _settings_manager.settings.get("local_server", {})
    return dict(value) if isinstance(value, dict) else {}


def _base_url() -> str:
    settings = _server_settings()
    host = str(settings.get("host", "127.0.0.1") or "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(settings.get("port", 8765) or 8765)
    return f"http://{host}:{port}"


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = str(_server_settings().get("auth_token", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _engine_host_command() -> list[str]:
    root = application_root()
    if getattr(sys, "frozen", False):
        host_exe = root / "LocalText2VoiceEngineHost.exe"
        if not host_exe.is_file():
            raise RuntimeError(
                "LocalText2VoiceEngineHost.exe is missing from the application folder."
            )
        return [str(host_exe)]
    return [sys.executable, str(root / "engine_host.py")]


def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    _ensure_engine_host()
    url = f"{_base_url()}{path}"
    if query:
        clean_query = {
            key: value for key, value in query.items() if value is not None
        }
        if clean_query:
            url += "?" + urllib.parse.urlencode(clean_query)
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from LocalText2Voice host: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not contact LocalText2Voice host: {exc}") from exc
    return json.loads(raw) if raw else {}


def _health(timeout: float = 1.0) -> bool:
    url = f"{_base_url()}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _ensure_engine_host() -> None:
    global _engine_host_process
    if _health():
        return
    process = _engine_host_process
    if process is not None and process.poll() is None:
        _wait_for_engine_host()
        return

    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    _engine_host_process = subprocess.Popen(
        _engine_host_command(),
        cwd=str(application_root()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    _wait_for_engine_host()


def _wait_for_engine_host(timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _health(timeout=0.5):
            return
        process = _engine_host_process
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"LocalText2Voice engine host exited with code {process.returncode}."
            )
        time.sleep(0.15)
    raise RuntimeError("Timed out waiting for LocalText2Voice engine host to start.")


def _safe(action: Callable[[], T]) -> T | dict[str, str]:
    try:
        return action()
    except Exception as exc:
        return {
            "error": str(exc),
            "hint": (
                "Check that LocalText2VoiceEngineHost.exe exists, the configured "
                "local server port is free, and the selected engine/model is installed."
            ),
        }


def _with_file_uris(payload: dict[str, Any]) -> dict[str, Any]:
    for key, uri_key in (
        ("clean_mp3_path", "clean_mp3_file_uri"),
        ("mix_mp3_path", "mix_mp3_file_uri"),
    ):
        value = str(payload.get(key, "") or "")
        if value:
            try:
                payload[uri_key] = Path(value).resolve().as_uri()
            except Exception:
                pass
    return payload


@mcp.resource("localtext2voice://docs/markup")
def markup_documentation() -> str:
    """Return the LocalText2Voice markup manual."""
    return _read_text_resource("docs/LTV_MARKUP.md")


@mcp.resource("localtext2voice://docs/markup/examples")
def markup_examples() -> str:
    """Return concise LocalText2Voice markup examples."""
    manual = _read_text_resource("docs/LTV_MARKUP.md")
    marker = "## Examples"
    index = manual.find(marker)
    if index >= 0:
        return manual[index:]
    return manual


@mcp.resource("localtext2voice://docs/engines")
def engine_documentation() -> str:
    """Return a compact guide for engine-aware audiobook generation."""
    return (
        "# LocalText2Voice Engines\n\n"
        "Use `list_engines` to see installed engines and `list_voices` to see "
        "compatible voices for an engine. Use `engine_memory` to check which "
        "engines are loaded in the persistent host, and `preload_engine` before "
        "long jobs when using heavy local engines such as Qwen3 TTS, OmniVoice, "
        "or Chatterbox.\n\n"
        "For advanced text control, read `localtext2voice://docs/markup` before "
        "calling `create_audiobook`."
    )


@mcp.tool(description="Return LocalText2Voice host status, engines, and public settings.")
def server_info() -> dict[str, Any]:
    return _safe(
        lambda: {
            **_request_json("GET", "/info"),
            "transport": "stdio",
            "bridge": "engine_host",
            "host_url": _base_url(),
        }
    )


@mcp.tool(description="List available TTS engines.")
def list_engines() -> list[dict[str, Any]] | dict[str, str]:
    return _safe(lambda: _request_json("GET", "/engines"))


@mcp.tool(description="Return TTS engines currently loaded in the persistent host memory.")
def engine_memory() -> dict[str, Any]:
    return _safe(lambda: _request_json("GET", "/engines/memory"))


@mcp.tool(description="Load a TTS engine into the persistent host memory.")
def preload_engine(
    engine_id: str | None = None,
    voice: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        engine = engine_id or str(_settings_manager.settings.get("tts_engine", "piper"))
        payload = {
            "engine_id": engine,
            "voice": voice,
            "language": language,
        }
        return _request_json("POST", f"/engines/{urllib.parse.quote(engine)}/preload", payload)

    return _safe(run)


@mcp.tool(description="Unload a TTS engine from the persistent host memory.")
def unload_engine(engine_id: str | None = None) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        engine = engine_id or str(_settings_manager.settings.get("tts_engine", "piper"))
        return _request_json("POST", f"/engines/{urllib.parse.quote(engine)}/unload", {})

    return _safe(run)


@mcp.tool(description="List voices for the selected or requested TTS engine.")
def list_voices(
    engine_id: str | None = None,
    installed_only: bool = True,
) -> list[dict[str, Any]] | dict[str, str]:
    return _safe(
        lambda: _request_json(
            "GET",
            "/voices",
            query={"engine_id": engine_id, "installed_only": str(installed_only).lower()},
        )
    )


@mcp.tool(description="List background music tracks available for podcast mixes.")
def list_background_music() -> list[dict[str, Any]] | dict[str, str]:
    return _safe(lambda: _request_json("GET", "/background-music"))


@mcp.tool(description="List sound effects available to PLAY markup commands.")
def list_sfx() -> list[dict[str, Any]] | dict[str, str]:
    return _safe(lambda: _request_json("GET", "/sfx"))


@mcp.tool(
    description=(
        "Return the LocalText2Voice markup manual. Use this before composing "
        "advanced audiobook scripts with voice, language, pause, speed, volume, "
        "and model parameter commands."
    )
)
def get_markup_help(section: str | None = None) -> str | dict[str, str]:
    def run() -> str:
        manual = _read_text_resource("docs/LTV_MARKUP.md")
        selected = str(section or "").strip().casefold()
        if selected in {"", "all", "manual"}:
            return manual
        heading = f"## {selected}"
        lower_manual = manual.casefold()
        index = lower_manual.find(heading)
        if index < 0:
            return manual
        next_index = lower_manual.find("\n## ", index + 1)
        return manual[index:] if next_index < 0 else manual[index:next_index]

    return _safe(run)


@mcp.tool(
    description=(
        "Create a complete audiobook generation job from plain text or "
        "LocalText2Voice markup. For advanced scripts, first read the "
        "localtext2voice://docs/markup resource or call get_markup_help. "
        "Returns a job id immediately unless wait_until_complete is true."
    )
)
def create_audiobook(
    text: str,
    title: str = "Audiobook",
    engine_id: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    background_music: str | None = None,
    mix_policy: str = "always",
    export_mode: str | None = None,
    split_mode: str | None = None,
    review_policy: str = "default",
    wait_until_complete: bool = False,
    wait_timeout_seconds: int = 0,
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        request = {
            "text": text,
            "title": title,
            "engine_id": engine_id,
            "voice": voice,
            "language": language,
            "background_music": background_music,
            "mix_policy": mix_policy,
            "export_mode": export_mode,
            "split_mode": split_mode,
            "review_policy": review_policy,
        }
        clean_request = {
            key: value for key, value in request.items() if value is not None
        }
        job = _request_json("POST", "/jobs", clean_request)
        if wait_until_complete:
            job_id = str(job.get("job_id", ""))
            timeout = max(1, int(wait_timeout_seconds or 3600))
            deadline = time.monotonic() + timeout
            while job_id and time.monotonic() < deadline:
                job = _request_json("GET", f"/jobs/{urllib.parse.quote(job_id)}")
                if str(job.get("status", "")) in {"complete", "failed", "cancelled"}:
                    break
                time.sleep(1.0)
        return _with_file_uris(job)

    return _safe(run)


@mcp.tool(description="Alias of create_audiobook for simpler MCP clients.")
def generate_audio(
    text: str,
    title: str = "Audiobook",
    engine_id: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    background_music: str | None = None,
    mix_policy: str = "always",
    review_policy: str = "default",
) -> dict[str, Any]:
    return create_audiobook(
        text=text,
        title=title,
        engine_id=engine_id,
        voice=voice,
        language=language,
        background_music=background_music,
        mix_policy=mix_policy,
        review_policy=review_policy,
    )


@mcp.tool(description="Get one generation job by id.")
def get_job(job_id: str) -> dict[str, Any]:
    return _safe(
        lambda: _with_file_uris(
            _request_json("GET", f"/jobs/{urllib.parse.quote(job_id)}")
        )
    )


@mcp.tool(description="List recent generation jobs.")
def get_jobs(status: str | None = None, limit: int = 25) -> list[dict[str, Any]] | dict[str, str]:
    return _safe(
        lambda: [
            _with_file_uris(job)
            for job in _request_json(
                "GET",
                "/jobs",
                query={"status": status, "limit": limit},
            )
        ]
    )


@mcp.tool(description="Cancel a queued or running generation job.")
def cancel_job(job_id: str) -> dict[str, Any]:
    return _safe(
        lambda: _with_file_uris(
            _request_json("POST", f"/jobs/{urllib.parse.quote(job_id)}/cancel", {})
        )
    )


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        # Stdio MCP uses stdout as the JSON-RPC transport. Keep shutdown noise
        # off stderr for desktop clients that treat any extra output as errors.
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    mcp.run(transport="stdio")

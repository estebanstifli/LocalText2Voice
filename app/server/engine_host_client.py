from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.core.settings_manager import SettingsManager
from app.utils.paths import application_root


class EngineHostClientError(RuntimeError):
    pass


class EngineHostClient:
    """Small HTTP client that starts or reuses the persistent engine host."""

    def __init__(self, settings_manager: SettingsManager | None = None) -> None:
        self.settings_manager = settings_manager or SettingsManager()
        self._process: subprocess.Popen[Any] | None = None

    def ensure_running(self, timeout_seconds: float = 20.0) -> None:
        if self.health():
            return
        if self._process is not None and self._process.poll() is None:
            self._wait_until_ready(timeout_seconds)
            return
        command = self._host_command()
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(application_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise EngineHostClientError(
                f"Could not start the LocalText2Voice engine host: {exc}"
            ) from exc
        self._wait_until_ready(timeout_seconds)

    def health(self, timeout: float = 0.75) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url()}/health", timeout=timeout) as response:
                return response.status == 200
        except Exception:
            return False

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        self.ensure_running()
        url = f"{self.base_url()}{path}"
        if query:
            values = {key: value for key, value in query.items() if value is not None}
            if values:
                url += "?" + urllib.parse.urlencode(values)
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EngineHostClientError(
                f"Engine host returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EngineHostClientError(
                f"Could not contact the LocalText2Voice engine host: {exc}"
            ) from exc
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise EngineHostClientError(
                "The LocalText2Voice engine host returned invalid JSON."
            ) from exc

    def submit_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self.request_json("POST", "/jobs", payload, timeout=30.0))

    def get_job(self, job_id: str) -> dict[str, Any]:
        return dict(self.request_json("GET", f"/jobs/{job_id}", timeout=10.0))

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return dict(
            self.request_json("POST", f"/jobs/{job_id}/cancel", {}, timeout=10.0)
        )

    def base_url(self) -> str:
        settings = self._server_settings()
        host = str(settings.get("host", "127.0.0.1") or "127.0.0.1")
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        port = int(settings.get("port", 8765) or 8765)
        return f"http://{host}:{port}"

    def _server_settings(self) -> dict[str, Any]:
        value = self.settings_manager.settings.get("local_server", {})
        return dict(value) if isinstance(value, dict) else {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = str(self._server_settings().get("auth_token", "") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _host_command() -> list[str]:
        root = application_root()
        if getattr(sys, "frozen", False):
            executable = root / "LocalText2VoiceEngineHost.exe"
            if not executable.is_file():
                raise EngineHostClientError(
                    "LocalText2VoiceEngineHost.exe is missing from the application folder."
                )
            return [str(executable)]
        script = root / "engine_host.py"
        if not script.is_file():
            raise EngineHostClientError(f"Engine host script not found: {script}")
        return [sys.executable, str(script)]

    def _wait_until_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            if self.health(timeout=0.5):
                return
            if self._process is not None and self._process.poll() is not None:
                raise EngineHostClientError(
                    "The LocalText2Voice engine host stopped during startup "
                    f"(exit code {self._process.returncode})."
                )
            time.sleep(0.15)
        raise EngineHostClientError(
            "Timed out waiting for the LocalText2Voice engine host to start."
        )

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from app.utils.paths import app_data_root, application_root
from app.tts.install_logging import (
    ProcessOutputCallback,
    communicate_with_live_output,
    detailed_pip_args,
    progress_output_callback,
    report_process_command,
)
from app.tts.model_cache import format_file_size


class PythonRuntimeError(RuntimeError):
    pass


class PythonRuntimeCancelled(PythonRuntimeError):
    pass


PythonRuntimeProgress = Callable[[int, int, str], None]


class PythonRuntimeManager:
    """Install a private embedded Python runtime for optional Python TTS engines."""

    PYTHON_VERSION = "3.11.9"
    RUNTIME_VERSION = f"python-{PYTHON_VERSION}-embed-amd64-v3"
    INSTALL_FILENAME = "python-runtime-install.json"
    PYTHON_ZIP_URL = (
        "https://www.python.org/ftp/python/"
        f"{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
    )
    GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
    CORE_PACKAGES = (
        "setuptools==80.9.0",
        "requests==2.32.5",
        "huggingface-hub==0.36.1",
        "hf-xet==1.5.0",
    )
    PYTHON_ZIP_MIN_SIZE = 8 * 1024 * 1024
    GET_PIP_MIN_SIZE = 1 * 1024 * 1024

    def __init__(
        self,
        runtime_dir: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.runtime_dir = (runtime_dir or self._default_runtime_dir()).resolve()
        self.python_dir = self.runtime_dir / "python"
        self._is_windows = sys.platform.startswith("win")
        # Windows: скачиваемый embeddable-пакет (python.exe в корне).
        # Linux/macOS: venv от системного интерпретатора (bin/python).
        if self._is_windows:
            self.python_exe = self.python_dir / "python.exe"
            self.runtime_version = self.RUNTIME_VERSION
            self.python_version = self.PYTHON_VERSION
        else:
            self.python_exe = self.python_dir / "bin" / "python"
            vi = sys.version_info
            self.python_version = f"{vi.major}.{vi.minor}.{vi.micro}"
            self.runtime_version = f"python-{self.python_version}-venv-{sys.platform}-v1"
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        manifest = self.install_manifest()
        return (
            manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.runtime_version
            and self.python_exe.is_file()
            and self.pip_module_path.is_dir()
        )

    def has_python(self) -> bool:
        return self.python_exe.is_file()

    def install_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.manifest_path)

    def install(
        self,
        progress_callback: PythonRuntimeProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        if self.is_installed():
            progress(100, 100, "Python runtime already installed.")
            return self.runtime_dir
        if not self._is_windows:
            return self._install_venv(progress, cancel_token)
        downloads_dir = self.runtime_dir / ".downloads"
        staging_dir = self.runtime_dir / ".staging"
        python_zip = downloads_dir / "python-embed.zip"
        get_pip = downloads_dir / "get-pip.py"
        self._remove_path(downloads_dir)
        self._remove_path(staging_dir)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest("installing")

        try:
            self._download_file(
                self.PYTHON_ZIP_URL,
                python_zip,
                self.PYTHON_ZIP_MIN_SIZE,
                progress,
                cancel_token,
                0,
                25,
                "Downloading embedded Python runtime...",
            )
            self._validate_zip(python_zip, "embedded Python runtime")
            progress(30, 100, "Extracting embedded Python runtime...")
            staging_python_dir = staging_dir / "python"
            self._extract_zip_safe(python_zip, staging_python_dir)
            self._enable_site_packages(staging_python_dir)

            self._download_file(
                self.GET_PIP_URL,
                get_pip,
                self.GET_PIP_MIN_SIZE,
                progress,
                cancel_token,
                35,
                45,
                "Downloading pip bootstrap...",
            )

            progress(55, 100, "Installing pip...")
            report_process_command(
                progress,
                55,
                "pip bootstrap",
                [str(staging_python_dir / "python.exe"), str(get_pip)],
            )
            self._run_process(
                [str(staging_python_dir / "python.exe"), str(get_pip)],
                cancel_token,
                cwd=staging_python_dir,
                output_callback=progress_output_callback(
                    progress, 55, "pip bootstrap"
                ),
            )

            progress(75, 100, "Installing Python engine core packages...")
            core_pip_args = detailed_pip_args(
                [
                    "install",
                    "--no-warn-script-location",
                    *self.CORE_PACKAGES,
                ]
            )
            report_process_command(progress, 75, "pip", ["pip", *core_pip_args])
            self._run_process(
                [
                    str(staging_python_dir / "python.exe"),
                    "-m",
                    "pip",
                    *core_pip_args,
                ],
                cancel_token,
                cwd=staging_python_dir,
                output_callback=progress_output_callback(progress, 75, "pip"),
            )

            self._remove_path(self.python_dir)
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging_python_dir), str(self.python_dir))
            self._write_manifest("installed")
            progress(100, 100, "Embedded Python runtime installed.")
            return self.runtime_dir
        except PythonRuntimeCancelled:
            self._write_manifest("cancelled")
            raise
        except Exception:
            self._write_manifest("failed")
            raise
        finally:
            self._remove_path(downloads_dir)
            self._remove_path(staging_dir)

    def _install_venv(
        self,
        progress: PythonRuntimeProgress,
        cancel_token: threading.Event | None,
    ) -> Path:
        """Linux/macOS: приватный рантайм = venv от системного Python.

        Windows-сборка качает embeddable-пакет с python.org; на остальных
        платформах системный интерпретатор уже есть, нужен только venv.
        """
        staging_python_dir = self.runtime_dir / ".staging" / "python"
        self._remove_path(staging_python_dir.parent)
        staging_python_dir.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest("installing")
        try:
            progress(20, 100, f"Creating virtual environment (Python {self.python_version})...")
            self._run_process(
                [sys.executable, "-m", "venv", str(staging_python_dir)],
                cancel_token,
                cwd=staging_python_dir.parent,
            )
            staging_exe = staging_python_dir / "bin" / "python"
            progress(60, 100, "Installing Python engine core packages...")
            self._run_process(
                [
                    str(staging_exe),
                    "-m",
                    "pip",
                    "install",
                    "--no-warn-script-location",
                    *self.CORE_PACKAGES,
                ],
                cancel_token,
                cwd=staging_python_dir,
            )
            progress(85, 100, "Finalizing Python runtime...")
            self._remove_path(self.python_dir)
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging_python_dir), str(self.python_dir))
            self._write_manifest("installed")
            progress(100, 100, "Python runtime installed.")
            return self.runtime_dir
        except PythonRuntimeCancelled:
            self._write_manifest("cancelled")
            raise
        except Exception:
            self._write_manifest("failed")
            raise
        finally:
            self._remove_path(staging_python_dir.parent)

    def uninstall(self) -> None:
        self.cancel()
        self._remove_path(self.runtime_dir)

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def python_command(self, *args: str) -> list[str]:
        return [str(self.python_exe), *args]

    def run_python(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
        cwd: Path | None = None,
        output_callback: ProcessOutputCallback | None = None,
    ) -> str:
        if not self.is_installed():
            raise PythonRuntimeError(
                "Embedded Python runtime is not installed. Install it from "
                "Settings > TTS Engines first."
            )
        return self._run_process(
            [str(self.python_exe), *args],
            cancel_token,
            cwd=cwd or self.python_dir,
            output_callback=output_callback,
        )

    def is_bundled(self) -> bool:
        try:
            self.runtime_dir.resolve().relative_to(self.bundled_runtime_dir().resolve())
            return True
        except ValueError:
            return False

    @property
    def manifest_path(self) -> Path:
        return self.runtime_dir / self.INSTALL_FILENAME

    @property
    def pip_module_path(self) -> Path:
        if self._is_windows:
            return self.python_dir / "Lib" / "site-packages" / "pip"
        matches = sorted(self.python_dir.glob("lib/python*/site-packages/pip"))
        if matches:
            return matches[0]
        return self.python_dir / "lib" / "site-packages" / "pip"

    def _download_file(
        self,
        url: str,
        target: Path,
        minimum_size: int,
        progress: PythonRuntimeProgress,
        cancel_token: threading.Event | None,
        progress_start: int,
        progress_end: int,
        message: str,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._download_once(
                    url,
                    target,
                    minimum_size,
                    progress,
                    cancel_token,
                    progress_start,
                    progress_end,
                    message,
                )
                return
            except PythonRuntimeCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    progress(
                        progress_start,
                        100,
                        f"{message} Retry {attempt}/3...",
                    )
        raise PythonRuntimeError(f"Could not download {url}: {last_error}")

    def _download_once(
        self,
        url: str,
        target: Path,
        minimum_size: int,
        progress: PythonRuntimeProgress,
        cancel_token: threading.Event | None,
        progress_start: int,
        progress_end: int,
        message: str,
    ) -> None:
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "LocalText2Voice/0.6"},
        )
        downloaded = 0
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                total = int(response.headers.get("Content-Length", "0") or "0")
                if total and total < minimum_size:
                    raise PythonRuntimeError(
                        f"Download is unexpectedly small ({total} bytes): {url}"
                    )
                size_text = format_file_size(total) if total else "unknown size"
                progress(
                    progress_start,
                    100,
                    f"{message} {target.name} ({size_text})",
                )
                with temporary.open("wb") as output:
                    while True:
                        self._check_cancelled(cancel_token)
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = progress_start + int(
                                (downloaded / total) * (progress_end - progress_start)
                            )
                            progress(
                                min(progress_end, percent),
                                100,
                                f"Downloading {target.name}: "
                                f"{format_file_size(downloaded)} / "
                                f"{format_file_size(total)}",
                            )
        except urllib.error.HTTPError as exc:
            raise PythonRuntimeError(
                f"Download failed with HTTP {exc.code}: {url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise PythonRuntimeError(f"Download failed: {exc}") from exc
        if downloaded < minimum_size:
            raise PythonRuntimeError(
                f"Download is too small ({downloaded} bytes): {url}"
            )
        temporary.replace(target)
        progress(
            progress_end,
            100,
            f"OK: {target.name} ({format_file_size(downloaded)})",
        )

    def _run_process(
        self,
        command: list[str],
        cancel_token: threading.Event | None,
        cwd: Path,
        output_callback: ProcessOutputCallback | None = None,
    ) -> str:
        self._check_cancelled(cancel_token)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise PythonRuntimeError(f"Could not start embedded Python: {exc}") from exc
        with self._lock:
            self._process = process
        try:
            stdout, stderr = communicate_with_live_output(
                process,
                lambda: self._check_cancelled(cancel_token),
                output_callback,
            )
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            if not details:
                details = stdout.decode("utf-8", errors="replace").strip()
            raise PythonRuntimeError(
                "Embedded Python command failed with exit code "
                f"{process.returncode}: {details or 'No error details returned.'}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    def _write_manifest(self, state: str) -> None:
        manifest = {
            "runtime": "python",
            "runtime_version": self.runtime_version,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "python_version": self.python_version,
            "python_url": self.PYTHON_ZIP_URL if self._is_windows else f"venv:{sys.executable}",
            "get_pip_url": self.GET_PIP_URL if self._is_windows else "stdlib-venv",
            "core_packages": list(self.CORE_PACKAGES),
            "python_path": str(self.python_exe),
        }
        self._write_json_atomic(self.manifest_path, manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None = None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise PythonRuntimeCancelled("Embedded Python runtime installation cancelled.")

    @staticmethod
    def _enable_site_packages(python_dir: Path) -> None:
        pth_files = sorted(python_dir.glob("python*._pth"))
        if not pth_files:
            raise PythonRuntimeError("Embedded Python ._pth configuration was not found.")
        pth_file = pth_files[0]
        lines = pth_file.read_text(encoding="utf-8").splitlines()
        enabled = False
        updated: list[str] = []
        for line in lines:
            if line.strip() == "#import site":
                updated.append("import site")
                enabled = True
            else:
                if line.strip() == "import site":
                    enabled = True
                updated.append(line)
        if not enabled:
            updated.append("import site")
        pth_file.write_text("\n".join(updated) + "\n", encoding="utf-8")

    @staticmethod
    def _validate_zip(path: Path, label: str) -> None:
        if not zipfile.is_zipfile(path):
            raise PythonRuntimeError(f"The downloaded {label} is not a ZIP file.")

    @classmethod
    def _extract_zip_safe(cls, archive_path: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        destination_resolved = destination.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if not cls._is_relative_to(target, destination_resolved):
                    raise PythonRuntimeError(
                        "The embedded Python archive contains unsafe paths."
                    )
            archive.extractall(destination)

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _is_relative_to(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    @classmethod
    def _default_runtime_dir(cls) -> Path:
        bundled = cls.bundled_runtime_dir()
        if (bundled / "python" / "python.exe").is_file():
            return bundled
        return app_data_root() / "runtimes" / "python311"

    @staticmethod
    def bundled_runtime_dir() -> Path:
        return application_root() / "runtimes" / "python311"

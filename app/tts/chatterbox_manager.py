from __future__ import annotations

import hashlib
import importlib.util
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.utils.paths import app_data_root, application_root


class ChatterboxError(RuntimeError):
    pass


class ChatterboxCancelled(ChatterboxError):
    pass


ChatterboxProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ChatterboxModel:
    model_id: str
    display_name: str
    requires_reference: bool
    supports_language: bool


@dataclass(frozen=True)
class ChatterboxLanguage:
    language_id: str
    display_name: str


class ChatterboxManager:
    VERSION = "v1"
    RUNTIME_VERSION = "chatterbox-cuda-v2"
    INSTALL_FILENAME = "chatterbox-install.json"
    RUNTIME_INSTALL_FILENAME = "chatterbox-runtime-install.json"
    DEFAULT_RUNTIME_PACK_URL = (
        "https://github.com/estebanstifli/LocalText2Voice/releases/download/"
        "chatterbox-runtime-v2/LocalText2Voice-Chatterbox-CUDA.zip"
    )
    DEFAULT_RUNTIME_PACK_URLS = (
        "https://github.com/estebanstifli/LocalText2Voice/releases/download/"
        "chatterbox-runtime-v2/LocalText2Voice-Chatterbox-CUDA.zip.part01",
        "https://github.com/estebanstifli/LocalText2Voice/releases/download/"
        "chatterbox-runtime-v2/LocalText2Voice-Chatterbox-CUDA.zip.part02",
    )
    RUNTIME_PACK_MIN_SIZE = 100 * 1024 * 1024

    MODELS: tuple[ChatterboxModel, ...] = (
        ChatterboxModel("multilingual_v3", "Chatterbox Multilingual V3", False, True),
        ChatterboxModel("english", "Chatterbox English", False, False),
        ChatterboxModel("turbo", "Chatterbox Turbo (English, voice clone)", True, False),
    )

    LANGUAGES: tuple[ChatterboxLanguage, ...] = (
        ChatterboxLanguage("ar", "Arabic"),
        ChatterboxLanguage("da", "Danish"),
        ChatterboxLanguage("de", "German"),
        ChatterboxLanguage("el", "Greek"),
        ChatterboxLanguage("en", "English"),
        ChatterboxLanguage("es", "Spanish"),
        ChatterboxLanguage("fi", "Finnish"),
        ChatterboxLanguage("fr", "French"),
        ChatterboxLanguage("he", "Hebrew"),
        ChatterboxLanguage("hi", "Hindi"),
        ChatterboxLanguage("it", "Italian"),
        ChatterboxLanguage("ja", "Japanese"),
        ChatterboxLanguage("ko", "Korean"),
        ChatterboxLanguage("ms", "Malay"),
        ChatterboxLanguage("nl", "Dutch"),
        ChatterboxLanguage("no", "Norwegian"),
        ChatterboxLanguage("pl", "Polish"),
        ChatterboxLanguage("pt", "Portuguese"),
        ChatterboxLanguage("ru", "Russian"),
        ChatterboxLanguage("sv", "Swedish"),
        ChatterboxLanguage("sw", "Swahili"),
        ChatterboxLanguage("tr", "Turkish"),
        ChatterboxLanguage("zh", "Chinese"),
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        runtime_path: Path | None = None,
        runtime_dir: Path | None = None,
        runtime_pack_url: str | None = None,
        runtime_pack_urls: list[str] | tuple[str, ...] | None = None,
        runtime_pack_sha256: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "chatterbox"
        self.cache_dir = self.install_dir / "hf-cache"
        self.runtime_dir = runtime_dir or app_data_root() / "runtimes" / "chatterbox"
        self.bundled_runtime_path = (
            application_root()
            / "engines"
            / "chatterbox"
            / "chatterbox_engine"
            / "chatterbox_engine.exe"
        )
        self.runtime_path = runtime_path or self._default_runtime_path()
        if runtime_pack_urls is not None:
            self.runtime_pack_urls = [url for url in runtime_pack_urls if url]
        elif runtime_pack_url:
            self.runtime_pack_urls = [runtime_pack_url]
        else:
            self.runtime_pack_urls = list(self.DEFAULT_RUNTIME_PACK_URLS)
        self.runtime_pack_url = (
            self.runtime_pack_urls[0]
            if len(self.runtime_pack_urls) == 1
            else self.DEFAULT_RUNTIME_PACK_URL
        )
        self.runtime_pack_sha256 = runtime_pack_sha256.strip().lower()
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        manifest = self.install_manifest()
        return (
            manifest.get("state") == "installed"
            and manifest.get("version") == self.VERSION
            and self.cache_dir.exists()
        )

    def isInstalled(self) -> bool:
        return self.is_installed()

    def has_runtime(self) -> bool:
        if self.runtime_path.is_file():
            return True
        if getattr(sys, "frozen", False):
            return False
        return (
            importlib.util.find_spec("chatterbox") is not None
            and importlib.util.find_spec("torchaudio") is not None
        )

    def runtime_is_current(self) -> bool:
        if not self.has_runtime():
            return False
        if not self.runtime_path.is_file():
            return True
        if self.runtime_path == self.bundled_runtime_path:
            return True
        manifest = self.runtime_manifest()
        return (
            manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
        )

    def install_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.manifest_path)

    def runtime_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.runtime_manifest_path)

    def install(
        self,
        model: str = "multilingual_v3",
        device: str = "auto",
        progress_callback: ChatterboxProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        if not self.has_runtime() or not self.runtime_is_current():
            self.install_runtime(progress, cancel_token)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest("installing", model, device)
        progress(1, 2, "Preparing Chatterbox model cache...")
        try:
            resolved_device = device
            try:
                self._run_runtime(
                    ["--warmup", "--model", model, "--device", device],
                    cancel_token,
                )
            except ChatterboxError as exc:
                if device == "cuda" and self._is_cuda_unavailable_error(str(exc)):
                    resolved_device = "auto"
                    progress(
                        1,
                        2,
                        "CUDA GPU was not available. Retrying with Auto/CPU...",
                    )
                    self._run_runtime(
                        ["--warmup", "--model", model, "--device", resolved_device],
                        cancel_token,
                    )
                else:
                    raise
            self._write_manifest("installed", model, resolved_device)
            progress(2, 2, "Chatterbox is ready.")
            return self.install_dir
        except ChatterboxCancelled:
            self._write_manifest("cancelled", model, device)
            raise
        except Exception:
            self._write_manifest("failed", model, device)
            raise

    def install_runtime(
        self,
        progress_callback: ChatterboxProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        downloads_dir = self.runtime_dir / ".downloads"
        staging_dir = self.runtime_dir / ".staging"
        archive_path = downloads_dir / "LocalText2Voice-Chatterbox-CUDA.zip.tmp"
        self._remove_path(downloads_dir)
        self._remove_path(staging_dir)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            downloaded = self._download_runtime_pack(
                archive_path,
                progress,
                cancel_token,
            )
            self._validate_runtime_archive(archive_path, downloaded)
            progress(downloaded, downloaded, "Extracting Chatterbox runtime...")
            self._extract_runtime_archive(archive_path, staging_dir)
            runtime_exe = self._find_runtime_exe(staging_dir)
            if runtime_exe is None:
                raise ChatterboxError(
                    "The Chatterbox runtime pack did not contain "
                    "chatterbox_engine.exe."
                )
            target_dir = self.runtime_dir / "chatterbox_engine"
            self._remove_path(target_dir)
            shutil.move(str(runtime_exe.parent), str(target_dir))
            self.runtime_path = target_dir / "chatterbox_engine.exe"
            self._write_runtime_manifest("installed")
            progress(downloaded, downloaded, "Chatterbox runtime installed.")
            return self.runtime_dir
        except ChatterboxCancelled:
            self._write_runtime_manifest("cancelled")
            raise
        except Exception:
            self._write_runtime_manifest("failed")
            raise
        finally:
            self._remove_path(downloads_dir)
            self._remove_path(staging_dir)

    def uninstall(self) -> None:
        self.cancel()
        self._remove_path(self.install_dir)

    def uninstall_runtime(self) -> None:
        self.cancel()
        self._remove_path(self.runtime_dir)
        self.runtime_path = self._default_runtime_path()

    def list_models(self) -> list[ChatterboxModel]:
        return list(self.MODELS)

    def list_languages(self) -> list[ChatterboxLanguage]:
        return list(self.LANGUAGES)

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        from .chatterbox_engine import ChatterboxTTSEngine

        engine = ChatterboxTTSEngine(self)
        return engine.synthesize_to_wav(text, output_path, voice_config)

    def cuda_info(self) -> dict[str, Any]:
        if not self.has_runtime():
            return {}
        try:
            output = self._run_runtime(["--cuda-info"])
            data = json.loads(output)
        except (ChatterboxError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

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

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def runtime_manifest_path(self) -> Path:
        return self.runtime_dir / self.RUNTIME_INSTALL_FILENAME

    def runtime_command(self) -> list[str]:
        if self.runtime_path.is_file():
            return [str(self.runtime_path)]
        if not getattr(sys, "frozen", False) and self.has_runtime():
            return [sys.executable, "-m", "app.tts.chatterbox_cli"]
        return [str(self.runtime_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["HF_HOME"] = str(self.cache_dir)
        env["HUGGINGFACE_HUB_CACHE"] = str(self.cache_dir / "hub")
        env["TRANSFORMERS_CACHE"] = str(self.cache_dir / "transformers")
        return env

    def _download_runtime_pack(
        self,
        archive_path: Path,
        progress: ChatterboxProgress,
        cancel_token: threading.Event | None,
    ) -> int:
        if not self.runtime_pack_urls:
            raise ChatterboxError("No Chatterbox runtime download URL is configured.")
        if len(self.runtime_pack_urls) == 1:
            return self._download_runtime_url(
                self.runtime_pack_urls[0],
                archive_path,
                progress,
                cancel_token,
                "Downloading Chatterbox GPU runtime...",
            )

        downloaded_total = 0
        part_paths: list[Path] = []
        for index, url in enumerate(self.runtime_pack_urls, start=1):
            part_path = archive_path.with_name(
                f"{archive_path.name}.part{index:02d}.tmp"
            )
            part_paths.append(part_path)
            downloaded_total += self._download_runtime_url(
                url,
                part_path,
                progress,
                cancel_token,
                (
                    "Downloading Chatterbox GPU runtime "
                    f"part {index}/{len(self.runtime_pack_urls)}..."
                ),
                downloaded_total,
            )
        progress(downloaded_total, downloaded_total, "Joining Chatterbox runtime...")
        with archive_path.open("wb") as output:
            for part_path in part_paths:
                self._check_cancelled(cancel_token)
                with part_path.open("rb") as part:
                    shutil.copyfileobj(part, output)
        return downloaded_total

    def _download_runtime_url(
        self,
        url: str,
        archive_path: Path,
        progress: ChatterboxProgress,
        cancel_token: threading.Event | None,
        message: str,
        offset: int = 0,
    ) -> int:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "LocalText2Voice/0.5"},
        )
        downloaded = 0
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                total = int(response.headers.get("Content-Length", "0") or "0")
                if total and total < self.RUNTIME_PACK_MIN_SIZE:
                    raise ChatterboxError(
                        "The Chatterbox runtime pack reported an unexpectedly "
                        f"small size ({total} bytes)."
                    )
                progress(offset, offset + total or 1, message)
                with archive_path.open("wb") as output:
                    while True:
                        self._check_cancelled(cancel_token)
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        progress(
                            offset + downloaded,
                            offset + total or max(offset + downloaded, 1),
                            message,
                        )
        except urllib.error.HTTPError as exc:
            raise ChatterboxError(
                "Could not download the Chatterbox runtime pack. "
                f"GitHub returned HTTP {exc.code}. URL: {url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ChatterboxError(
                f"Could not download the Chatterbox runtime pack: {exc}"
            ) from exc
        return downloaded

    def _validate_runtime_archive(self, archive_path: Path, downloaded: int) -> None:
        if downloaded < self.RUNTIME_PACK_MIN_SIZE:
            raise ChatterboxError(
                "The downloaded Chatterbox runtime pack is too small "
                f"({downloaded} bytes)."
            )
        if self.runtime_pack_sha256:
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if digest.lower() != self.runtime_pack_sha256:
                raise ChatterboxError(
                    "The Chatterbox runtime pack checksum did not match."
                )
        if not zipfile.is_zipfile(archive_path):
            raise ChatterboxError("The Chatterbox runtime pack is not a ZIP file.")

    def _extract_runtime_archive(self, archive_path: Path, destination: Path) -> None:
        destination_resolved = destination.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if not self._is_relative_to(target, destination_resolved):
                    raise ChatterboxError(
                        "The Chatterbox runtime pack contains unsafe paths."
                    )
            archive.extractall(destination)

    def _run_runtime(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
    ) -> str:
        if not self.has_runtime():
            raise ChatterboxError(
                "chatterbox_engine.exe was not found. Click Install to download "
                "the Chatterbox runtime pack, or build it with "
                "build_chatterbox_engine.bat."
            )
        command = [
            *self.runtime_command(),
            *args,
            "--cache-dir",
            str(self.cache_dir),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.runtime_environment(),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise ChatterboxError(f"Could not start Chatterbox: {exc}") from exc
        with self._lock:
            self._process = process
        stdout = b""
        stderr = b""
        try:
            while True:
                self._check_cancelled(cancel_token)
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            if not details:
                details = stdout.decode("utf-8", errors="replace").strip()
            raise ChatterboxError(
                f"Chatterbox failed with exit code {process.returncode}: "
                f"{details or 'No error details were returned.'}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    def _write_manifest(self, state: str, model: str, device: str) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "chatterbox",
            "version": self.VERSION,
            "state": state,
            "model": model,
            "device": device,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cache_dir": str(self.cache_dir),
        }
        self._write_json_atomic(self.manifest_path, manifest)

    def _write_runtime_manifest(self, state: str) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "chatterbox",
            "runtime_version": self.RUNTIME_VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": self.runtime_pack_url,
            "urls": self.runtime_pack_urls,
            "sha256": self.runtime_pack_sha256,
            "runtime_path": str(self.runtime_path),
        }
        self._write_json_atomic(self.runtime_manifest_path, manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None = None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise ChatterboxCancelled("Chatterbox operation cancelled.")

    @staticmethod
    def _default_runtime_path() -> Path:
        local = (
            app_data_root()
            / "runtimes"
            / "chatterbox"
            / "chatterbox_engine"
            / "chatterbox_engine.exe"
        )
        if local.is_file():
            return local
        root = application_root() / "engines" / "chatterbox"
        onedir_runtime = root / "chatterbox_engine" / "chatterbox_engine.exe"
        if onedir_runtime.is_file():
            return onedir_runtime
        return local

    @staticmethod
    def _find_runtime_exe(root: Path) -> Path | None:
        direct = root / "chatterbox_engine" / "chatterbox_engine.exe"
        if direct.is_file():
            return direct
        for candidate in root.rglob("chatterbox_engine.exe"):
            return candidate
        return None

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

    @staticmethod
    def _is_cuda_unavailable_error(message: str) -> bool:
        lowered = message.lower()
        return "cuda was requested" in lowered and (
            "cannot see a cuda gpu" in lowered
            or "not available" in lowered
            or "no cuda" in lowered
        )

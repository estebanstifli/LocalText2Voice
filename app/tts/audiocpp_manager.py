from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from app.utils.paths import large_assets_root
from .audiocpp_assets import ASSETS
from .audiocpp_config import MODELS, VERSION
from .base import TTSCancelled, TTSEngineError

Progress = Callable[[int, int, str], None]


def runtime_key(backend: str) -> str:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        suffix = {"cpu": "cpu-portable", "vulkan": "vulkan-portable", "cuda": "cuda12.4"}.get(backend)
        if suffix:
            return f"windows-x64-{suffix}"
    if system == "Linux" and machine in {"amd64", "x86_64"} and backend in {"cpu", "vulkan"}:
        return f"ubuntu-x64-{backend}-portable"
    if system == "Darwin" and backend in {"cpu", "metal"}:
        return f"macos-{'arm64' if machine in {'arm64', 'aarch64'} else 'x64'}-metal"
    raise TTSEngineError(f"No managed audio.cpp {backend} runtime for {system}/{machine}.")


class AudioCppManager:
    """Install verified native releases and standalone GGUFs, without pip/torch."""

    def __init__(self, root: Path | None = None):
        self.root = root or large_assets_root() / "audiocpp"
        self.install_dir = self.root
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def reset_cancel(self):
        """Called before starting a new worker, never after cancellation is possible."""
        self._cancel.clear()

    def _check(self):
        if self._cancel.is_set():
            raise TTSCancelled("audio.cpp installation cancelled; completed files were preserved.")

    def model_path(self, model: str) -> Path:
        return self.root / "models" / MODELS[model].file

    def runtime_dir(self, backend: str) -> Path:
        return self.root / "runtime" / VERSION / runtime_key(backend)

    def executable(self, backend: str) -> Path:
        root = self.runtime_dir(backend)
        name = "audiocpp_server.exe" if platform.system() == "Windows" else "audiocpp_server"
        matches = list(root.rglob(name)) if root.exists() else []
        return matches[0] if matches else root / name

    def has_runtime(self, backend: str = "cpu") -> bool:
        try:
            root = self.runtime_dir(backend)
            manifest = json.loads((root / "installed.json").read_text(encoding="utf-8"))
            return self.executable(backend).is_file() and all(
                (root / name).is_file() and (root / name).stat().st_size == size
                for name, size in manifest["files"].items()
            )
        except (OSError, ValueError, KeyError, TTSEngineError):
            return False

    def is_installed(self, model: str = "higgs_audio_tts_4b_q8_0", backend: str = "cpu") -> bool:
        try:
            spec = MODELS[model]
            path = self.model_path(model)
            return (self.has_runtime(backend) and path.is_file() and path.stat().st_size == spec.size
                    and (path.parent / "LICENSE.txt").is_file() and (path.parent / "NOTICE.txt").is_file())
        except (OSError, KeyError):
            return False

    def download(self, url: str, destination: Path, size: int, digest: str, progress: Progress):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == size:
            if self._hash(destination, progress) == digest:
                return
        partial = destination.with_name(destination.name + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            partial.unlink()
            offset = 0
        if offset < size:
            headers = {"User-Agent": "LocalText2Voice/audio.cpp"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            self._check()
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                resume = offset > 0 and response.status == 206
                if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise TTSEngineError("Invalid download range; retry the installation.")
                if not resume:
                    offset = 0
                with partial.open("ab" if resume else "wb") as output:
                    while True:
                        self._check()
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        offset += len(block)
                        if offset > size:
                            raise TTSEngineError("Download exceeded the expected package size.")
                        progress(offset, size, f"Downloading {destination.name}")
        if partial.stat().st_size != size:
            raise TTSEngineError("Incomplete download. Install again to resume.")
        if self._hash(partial, progress) != digest:
            partial.unlink(missing_ok=True)
            raise TTSEngineError("SHA-256 verification failed. Install again to retry.")
        self._check()
        os.replace(partial, destination)

    def _hash(self, path: Path, progress: Progress) -> str:
        progress(0, 0, f"Verifying {path.name}")
        sha = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(4 * 1024 * 1024):
                self._check()
                sha.update(block)
        return sha.hexdigest()

    def _extract(self, archive: Path, target: Path):
        """Extract regular files only; reject traversal, symlinks and device entries."""
        def output_path(name):
            path = (target / name.replace("\\", "/")).resolve()
            if not path.is_relative_to(target.resolve()):
                raise TTSEngineError("Unsafe path in runtime archive.")
            return path

        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    self._check()
                    path = output_path(item.filename)
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise TTSEngineError("Unsupported symlink in runtime archive.")
                    if item.is_dir():
                        path.mkdir(parents=True, exist_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(item) as source, path.open("wb") as output:
                            shutil.copyfileobj(source, output)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                for item in bundle:
                    self._check()
                    path = output_path(item.name)
                    if item.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    elif item.isfile():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(item) as source, path.open("wb") as output:
                            shutil.copyfileobj(source, output)
                        path.chmod(item.mode & 0o755)
                    else:
                        raise TTSEngineError("Unsupported runtime archive entry.")

    def install(self, model: str, backend: str, progress: Progress):
        self._check()
        spec = MODELS[model]
        runtime = self.runtime_dir(backend)
        runtime.parent.mkdir(parents=True, exist_ok=True)
        if not self.has_runtime(backend):
            key = runtime_key(backend)
            assets = [a for a in ASSETS if key in a["name"]]
            if not assets:
                raise TTSEngineError("No pinned runtime package for this backend.")
            # CUDA DLLs can be in a separate subdirectory; the engine adds all DLL
            # directories to the child PATH, never to the desktop process PATH.
            with tempfile.TemporaryDirectory(prefix="audiocpp-install-", dir=runtime.parent) as staging:
                stage = Path(staging)
                for asset in assets:
                    archive = self.root / "downloads" / asset["name"]
                    self.download(asset["browser_download_url"], archive, asset["size"], asset["digest"].split(":")[1], progress)
                    progress(0, 0, f"Extracting {asset['name']}")
                    self._extract(archive, stage)
                executable_name = "audiocpp_server.exe" if platform.system() == "Windows" else "audiocpp_server"
                if not list(stage.rglob(executable_name)):
                    raise TTSEngineError("Runtime archive does not contain audiocpp_server.")
                manifest = {str(p.relative_to(stage)): p.stat().st_size for p in stage.rglob("*") if p.is_file()}
                (stage / "installed.json").write_text(json.dumps({"version": VERSION, "files": manifest}), encoding="utf-8")
                self._check()
                # Only replace our own version/backend directory, never user model files.
                if runtime.exists():
                    if not runtime.resolve().is_relative_to((self.root / "runtime").resolve()):
                        raise TTSEngineError("Runtime directory is outside managed storage.")
                    shutil.rmtree(runtime)
                shutil.copytree(stage, runtime)
        path = self.model_path(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve upstream terms alongside the converted weights.
        license_url = spec.license_url.replace("/blob/", "/raw/")
        self._check()
        with urllib.request.urlopen(license_url, timeout=30) as response:
            license_text = response.read(256 * 1024).decode("utf-8")
        (path.parent / "LICENSE.txt").write_text(license_text, encoding="utf-8")
        (path.parent / "NOTICE.txt").write_text(spec.notice + "\nConverted GGUF package published by audio-cpp/audio.cpp-gguf.\n" + spec.license_url, encoding="utf-8")
        self.download(spec.url, path, spec.size, spec.sha256, progress)
        progress(1, 1, f"Installed {spec.name}")
        return path

    def remove_model(self, model: str):
        """Remove only the selected managed weights, leaving shared runtime intact."""
        path = self.model_path(model)
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)

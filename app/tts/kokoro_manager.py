from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.utils.paths import app_data_root


class KokoroError(RuntimeError):
    pass


class KokoroDownloadCancelled(KokoroError):
    pass


KokoroProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class KokoroAsset:
    name: str
    filename: str
    url: str
    minimum_size: int
    expected_size: int


@dataclass(frozen=True)
class KokoroVoice:
    voice_id: str
    display_name: str
    language: str


class KokoroManager:
    VERSION = "v1.0-auto-cpu-gpu"
    INSTALL_FILENAME = "kokoro-install.json"
    CPU_MODEL_FILENAME = "kokoro-v1.0.int8.onnx"
    GPU_MODEL_FILENAME = "kokoro-v1.0.fp16-gpu.onnx"
    MODEL_FILENAME = CPU_MODEL_FILENAME
    VOICES_FILENAME = "voices-v1.0.bin"
    ASSETS: tuple[KokoroAsset, ...] = (
        KokoroAsset(
            name="Kokoro CPU ONNX model",
            filename=CPU_MODEL_FILENAME,
            url=(
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                "model-files-v1.0/kokoro-v1.0.int8.onnx"
            ),
            minimum_size=80 * 1024 * 1024,
            expected_size=92361271,
        ),
        KokoroAsset(
            name="Kokoro GPU ONNX model",
            filename=GPU_MODEL_FILENAME,
            url=(
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                "model-files-v1.0/kokoro-v1.0.fp16-gpu.onnx"
            ),
            minimum_size=160 * 1024 * 1024,
            expected_size=177464787,
        ),
        KokoroAsset(
            name="Kokoro voices",
            filename=VOICES_FILENAME,
            url=(
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                "model-files-v1.0/voices-v1.0.bin"
            ),
            minimum_size=20 * 1024 * 1024,
            expected_size=28214398,
        ),
    )
    VOICES: tuple[KokoroVoice, ...] = (
        KokoroVoice("af_heart", "American English - Heart", "en-us"),
        KokoroVoice("af_sarah", "American English - Sarah", "en-us"),
        KokoroVoice("af_bella", "American English - Bella", "en-us"),
        KokoroVoice("af_nicole", "American English - Nicole", "en-us"),
        KokoroVoice("af_sky", "American English - Sky", "en-us"),
        KokoroVoice("am_adam", "American English - Adam", "en-us"),
        KokoroVoice("am_michael", "American English - Michael", "en-us"),
        KokoroVoice("bf_emma", "British English - Emma", "en-gb"),
        KokoroVoice("bf_isabella", "British English - Isabella", "en-gb"),
        KokoroVoice("bm_george", "British English - George", "en-gb"),
        KokoroVoice("bm_lewis", "British English - Lewis", "en-gb"),
        KokoroVoice("ef_dora", "Spanish - Dora", "es"),
        KokoroVoice("em_alex", "Spanish - Alex", "es"),
        KokoroVoice("em_santa", "Spanish - Santa", "es"),
        KokoroVoice("ff_siwis", "French - Siwis", "fr-fr"),
        KokoroVoice("hf_alpha", "Hindi - Alpha", "hi"),
        KokoroVoice("hf_beta", "Hindi - Beta", "hi"),
        KokoroVoice("if_sara", "Italian - Sara", "it"),
        KokoroVoice("im_nicola", "Italian - Nicola", "it"),
        KokoroVoice("pf_dora", "Portuguese - Dora", "pt-br"),
        KokoroVoice("pm_alex", "Portuguese - Alex", "pt-br"),
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "kokoro"
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._external_cancel_token: threading.Event | None = None

    def is_installed(self) -> bool:
        manifest = self.install_manifest()
        if manifest.get("state") != "installed":
            return False
        if manifest.get("version") != self.VERSION:
            return False
        return all(
            (self.install_dir / asset.filename).is_file()
            for asset in self.ASSETS
        )

    def isInstalled(self) -> bool:
        return self.is_installed()

    def has_runtime(self) -> bool:
        return False

    def install_manifest(self) -> dict[str, Any]:
        path = self.manifest_path
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def install(
        self,
        progress_callback: KokoroProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self._external_cancel_token = cancel_token
        self.install_dir.mkdir(parents=True, exist_ok=True)
        downloads_dir = self.install_dir / ".downloads"
        self._remove_path(downloads_dir)
        downloads_dir.mkdir(parents=True, exist_ok=True)

        total_size = sum(asset.expected_size for asset in self.ASSETS)
        completed = 0
        files: list[dict[str, Any]] = []
        try:
            self._write_manifest("installing", [])
            for asset in self.ASSETS:
                target = self.install_dir / asset.filename
                if self._asset_is_valid(asset, target):
                    completed += asset.expected_size
                    progress(
                        completed,
                        total_size,
                        f"Using existing {asset.name}.",
                    )
                else:
                    temporary = downloads_dir / f"{asset.filename}.tmp"
                    completed = self._download_asset(
                        asset,
                        temporary,
                        completed,
                        total_size,
                        progress,
                    )
                    temporary.replace(target)
                files.append(
                    {
                        "name": asset.name,
                        "filename": asset.filename,
                        "size": target.stat().st_size,
                        "url": asset.url,
                    }
                )
            self._write_manifest("installed", files)
            self._remove_path(downloads_dir)
            progress(total_size, total_size, "Kokoro installed.")
            return self.install_dir
        except KokoroDownloadCancelled:
            self._write_manifest("cancelled", files)
            self._remove_path(downloads_dir)
            raise
        except Exception:
            self._write_manifest("failed", files)
            self._remove_path(downloads_dir)
            raise
        finally:
            self._external_cancel_token = None

    def uninstall(self) -> None:
        self._remove_path(self.install_dir)

    def list_voices(self) -> list[KokoroVoice]:
        return list(self.VOICES)

    def listVoices(self) -> list[KokoroVoice]:
        return self.list_voices()

    def synthesize(
        self,
        text: str,
        voice: str,
        lang: str,
        speed: float,
        output_path: Path,
        provider: str = "cpu",
    ) -> Path:
        raise KokoroError(
            "The dedicated Kokoro executable engine has been removed. "
            "Use KokoroPythonManager for synthesis."
        )

    def cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def model_path(self) -> Path:
        return self.install_dir / self.MODEL_FILENAME

    @property
    def cpu_model_path(self) -> Path:
        return self.install_dir / self.CPU_MODEL_FILENAME

    @property
    def gpu_model_path(self) -> Path:
        return self.install_dir / self.GPU_MODEL_FILENAME

    @property
    def voices_path(self) -> Path:
        return self.install_dir / self.VOICES_FILENAME

    def model_path_for_provider(self, provider: str = "auto") -> Path:
        requested = provider.lower().strip()
        if requested == "cuda":
            if self.gpu_model_path.is_file():
                return self.gpu_model_path
            return self.cpu_model_path
        if requested == "auto":
            manifest = self.runtime_dependency_manifest()
            backend = str(manifest.get("backend", "")).lower()
            if "cuda" in backend and self.gpu_model_path.is_file():
                return self.gpu_model_path
        return self.cpu_model_path

    def runtime_dependency_manifest(self) -> dict[str, Any]:
        return {}

    def _download_asset(
        self,
        asset: KokoroAsset,
        temporary: Path,
        completed_before: int,
        total_size: int,
        progress: KokoroProgress,
    ) -> int:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self._check_cancelled()
            if temporary.exists():
                temporary.unlink()
            try:
                return self._download_once(
                    asset,
                    temporary,
                    completed_before,
                    total_size,
                    progress,
                    attempt,
                )
            except KokoroDownloadCancelled:
                raise
            except (OSError, urllib.error.URLError, KokoroError) as exc:
                last_error = exc
                progress(
                    completed_before,
                    total_size,
                    f"{asset.name} download failed, retry {attempt}/3...",
                )
                time.sleep(min(2, attempt))
        raise KokoroError(f"Could not download {asset.name}: {last_error}")

    def _asset_is_valid(self, asset: KokoroAsset, path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size >= asset.minimum_size
        except OSError:
            return False

    def _download_once(
        self,
        asset: KokoroAsset,
        temporary: Path,
        completed_before: int,
        total_size: int,
        progress: KokoroProgress,
        attempt: int,
    ) -> int:
        request = urllib.request.Request(
            asset.url,
            headers={"User-Agent": "LocalText2Voice/0.4"},
        )
        downloaded = 0
        progress(
            completed_before,
            total_size,
            f"Downloading {asset.name} (attempt {attempt}/3)...",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            with temporary.open("wb") as output:
                while True:
                    self._check_cancelled()
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    progress(
                        completed_before + min(downloaded, asset.expected_size),
                        total_size,
                        f"Downloading {asset.name}...",
                    )
        if downloaded < asset.minimum_size:
            raise KokoroError(
                f"{asset.name} is too small ({downloaded} bytes)."
            )
        return completed_before + asset.expected_size

    def _write_manifest(
        self,
        state: str,
        files: list[dict[str, Any]],
    ) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "kokoro",
            "version": self.VERSION,
            "state": state,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": "auto",
            "cpu_model": self.CPU_MODEL_FILENAME,
            "gpu_model": self.GPU_MODEL_FILENAME,
            "files": files,
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set() or (
            self._external_cancel_token is not None
            and self._external_cancel_token.is_set()
        ):
            raise KokoroDownloadCancelled("Kokoro installation cancelled.")

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

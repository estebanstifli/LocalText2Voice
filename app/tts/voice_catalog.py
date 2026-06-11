from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable


class VoiceCatalogError(RuntimeError):
    pass


class VoiceDownloadCancelled(VoiceCatalogError):
    pass


@dataclass(frozen=True)
class RemoteVoice:
    voice_id: str
    language: str
    speaker: str
    quality: str
    model_path: str
    config_path: str
    model_size: int
    config_size: int
    model_sha256: str = ""
    sample_path: str = ""
    sample_size: int = 0

    @property
    def display_name(self) -> str:
        return f"{self.language} - {self.speaker} ({self.quality})"

    @property
    def total_size(self) -> int:
        return self.model_size + self.config_size

    @property
    def has_sample(self) -> bool:
        return bool(self.sample_path)

    @property
    def install_relative_dir(self) -> Path:
        return Path(self.language) / self.speaker / self.quality


CatalogProgress = Callable[[int, str], None]
DownloadProgress = Callable[[int, int, str], None]


class HuggingFaceVoiceCatalog:
    REPOSITORY = "rhasspy/piper-voices"
    REVISION = "main"
    API_URL = (
        "https://huggingface.co/api/models/"
        f"{REPOSITORY}/tree/{REVISION}?recursive=true&expand=false&limit=1000"
    )
    REPOSITORY_URL = f"https://huggingface.co/{REPOSITORY}/tree/{REVISION}"
    _next_link_pattern = re.compile(r'<([^>]+)>;\s*rel="next"')

    def __init__(
        self,
        voices_root: Path,
        timeout_seconds: int = 30,
    ) -> None:
        self.voices_root = voices_root
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def fetch_catalog(
        self,
        progress_callback: CatalogProgress | None = None,
    ) -> list[RemoteVoice]:
        progress = progress_callback or (lambda page, message: None)
        next_url: str | None = self.API_URL
        page = 0
        files: dict[str, dict[str, Any]] = {}

        while next_url:
            self._check_cancelled()
            page += 1
            progress(page, f"Loading voice catalog page {page}...")
            request = self._request(next_url)
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    payload = json.load(response)
                    link_header = response.headers.get("Link", "")
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise VoiceCatalogError(
                    f"Could not load the Piper voice catalog: {exc}"
                ) from exc

            if not isinstance(payload, list):
                raise VoiceCatalogError(
                    "Hugging Face returned an unexpected catalog response."
                )
            for entry in payload:
                if (
                    isinstance(entry, dict)
                    and entry.get("type") == "file"
                    and isinstance(entry.get("path"), str)
                ):
                    files[entry["path"]] = entry
            match = self._next_link_pattern.search(link_header)
            next_url = match.group(1) if match else None

        voices: list[RemoteVoice] = []
        for model_path, model_entry in files.items():
            if not model_path.endswith(".onnx"):
                continue
            config_path = f"{model_path}.json"
            config_entry = files.get(config_path)
            if config_entry is None:
                continue
            sample_entry = self._find_sample_entry(model_path, files)
            voice = self._parse_voice(
                model_path,
                model_entry,
                config_entry,
                sample_entry,
            )
            if voice is not None:
                voices.append(voice)

        if not voices:
            raise VoiceCatalogError(
                "No downloadable Piper voices were found in the catalog."
            )
        return sorted(
            voices,
            key=lambda voice: (
                voice.language.casefold(),
                voice.speaker.casefold(),
                voice.quality.casefold(),
            ),
        )

    def is_installed(self, voice: RemoteVoice) -> bool:
        destination = self.voices_root / voice.install_relative_dir
        model = destination / PurePosixPath(voice.model_path).name
        config = destination / PurePosixPath(voice.config_path).name
        return (
            model.is_file()
            and config.is_file()
            and model.stat().st_size == voice.model_size
            and config.stat().st_size == voice.config_size
        )

    def install(
        self,
        voice: RemoteVoice,
        progress_callback: DownloadProgress | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self.voices_root.mkdir(parents=True, exist_ok=True)
        destination_dir = self._safe_destination(voice.install_relative_dir)
        temporary_dir = self.voices_root / ".downloads" / voice.voice_id
        self._remove_path(temporary_dir)
        temporary_dir.mkdir(parents=True, exist_ok=True)

        model_name = PurePosixPath(voice.model_path).name
        config_name = PurePosixPath(voice.config_path).name
        temporary_model = temporary_dir / model_name
        temporary_config = temporary_dir / config_name
        total_size = voice.total_size
        completed = 0

        try:
            completed = self._download_file(
                voice.model_path,
                temporary_model,
                voice.model_size,
                completed,
                total_size,
                progress,
                "Downloading voice model...",
            )
            self._verify_sha256(temporary_model, voice.model_sha256)
            completed = self._download_file(
                voice.config_path,
                temporary_config,
                voice.config_size,
                completed,
                total_size,
                progress,
                "Downloading voice configuration...",
            )
            self._validate_config(temporary_config)
            self._check_cancelled()

            destination_dir.mkdir(parents=True, exist_ok=True)
            temporary_model.replace(destination_dir / model_name)
            temporary_config.replace(destination_dir / config_name)
            self._remove_path(temporary_dir)
            progress(total_size, total_size, "Voice installed.")
            return destination_dir
        except Exception:
            self._remove_path(temporary_dir)
            raise
        finally:
            self._prune_empty(self.voices_root / ".downloads")

    def remove(self, voice: RemoteVoice) -> None:
        destination_dir = self._safe_destination(voice.install_relative_dir)
        for filename in (
            PurePosixPath(voice.model_path).name,
            PurePosixPath(voice.config_path).name,
        ):
            path = destination_dir / filename
            if path.is_file():
                path.unlink()
        self._prune_empty(destination_dir)

    @classmethod
    def sample_url(cls, voice: RemoteVoice) -> str:
        if not voice.sample_path:
            return ""
        quoted_path = urllib.parse.quote(voice.sample_path, safe="/")
        return (
            f"https://huggingface.co/{cls.REPOSITORY}/resolve/"
            f"{cls.REVISION}/{quoted_path}?download=true"
        )

    def _download_file(
        self,
        repository_path: str,
        destination: Path,
        expected_size: int,
        completed_before: int,
        total_size: int,
        progress: DownloadProgress,
        message: str,
    ) -> int:
        quoted_path = urllib.parse.quote(repository_path, safe="/")
        url = (
            f"https://huggingface.co/{self.REPOSITORY}/resolve/"
            f"{self.REVISION}/{quoted_path}?download=true"
        )
        request = self._request(url)
        downloaded = 0
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response, destination.open("wb") as output:
                while True:
                    self._check_cancelled()
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    progress(
                        completed_before + downloaded,
                        total_size,
                        message,
                    )
        except VoiceDownloadCancelled:
            raise
        except (OSError, urllib.error.URLError) as exc:
            raise VoiceCatalogError(f"Voice download failed: {exc}") from exc

        if downloaded != expected_size:
            raise VoiceCatalogError(
                f"Downloaded file has the wrong size: {repository_path} "
                f"({downloaded} bytes, expected {expected_size})."
            )
        return completed_before + downloaded

    @staticmethod
    def _verify_sha256(path: Path, expected_hash: str) -> None:
        if not expected_hash:
            return
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_hash.lower():
            raise VoiceCatalogError(
                "The downloaded model failed its SHA-256 integrity check."
            )

    @staticmethod
    def _validate_config(path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceCatalogError(
                "The downloaded voice configuration is not valid JSON."
            ) from exc
        if not isinstance(data, dict):
            raise VoiceCatalogError(
                "The downloaded voice configuration has an invalid format."
            )

    @classmethod
    def _parse_voice(
        cls,
        model_path: str,
        model_entry: dict[str, Any],
        config_entry: dict[str, Any],
        sample_entry: dict[str, Any] | None = None,
    ) -> RemoteVoice | None:
        parts = PurePosixPath(model_path).parts
        if len(parts) != 5:
            return None
        _, language, speaker, quality, filename = parts
        if not filename.endswith(".onnx"):
            return None
        model_size = cls._entry_size(model_entry)
        config_size = cls._entry_size(config_entry)
        if model_size <= 0 or config_size <= 0:
            return None
        lfs = model_entry.get("lfs")
        model_sha256 = (
            str(lfs.get("oid", ""))
            if isinstance(lfs, dict)
            else ""
        )
        voice_id = hashlib.sha1(
            model_path.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]
        return RemoteVoice(
            voice_id=voice_id,
            language=language,
            speaker=speaker,
            quality=quality,
            model_path=model_path,
            config_path=f"{model_path}.json",
            model_size=model_size,
            config_size=config_size,
            model_sha256=model_sha256,
            sample_path=(
                str(sample_entry.get("path", ""))
                if isinstance(sample_entry, dict)
                else ""
            ),
            sample_size=(
                cls._entry_size(sample_entry)
                if isinstance(sample_entry, dict)
                else 0
            ),
        )

    @classmethod
    def _find_sample_entry(
        cls,
        model_path: str,
        files: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        parent = str(PurePosixPath(model_path).parent)
        preferred_path = f"{parent}/samples/speaker_0.mp3"
        if preferred_path in files:
            return files[preferred_path]
        prefix = f"{parent}/samples/"
        candidates = [
            entry
            for path, entry in files.items()
            if path.startswith(prefix) and path.lower().endswith(".mp3")
        ]
        return sorted(candidates, key=lambda item: str(item.get("path", "")))[0] if candidates else None

    @staticmethod
    def _entry_size(entry: dict[str, Any]) -> int:
        lfs = entry.get("lfs")
        if isinstance(lfs, dict) and isinstance(lfs.get("size"), int):
            return int(lfs["size"])
        size = entry.get("size")
        return int(size) if isinstance(size, int) else 0

    def _safe_destination(self, relative_dir: Path) -> Path:
        root = self.voices_root.resolve()
        destination = (root / relative_dir).resolve()
        if destination != root and root not in destination.parents:
            raise VoiceCatalogError("Unsafe voice destination path.")
        return destination

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise VoiceDownloadCancelled("Voice download cancelled.")

    @staticmethod
    def _request(url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/octet-stream",
                "User-Agent": "LocalText2Voice/0.2",
            },
        )

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def _prune_empty(self, start: Path) -> None:
        root = self.voices_root.resolve()
        current = start.resolve()
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

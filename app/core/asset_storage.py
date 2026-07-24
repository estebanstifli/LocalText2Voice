from __future__ import annotations

import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.utils.paths import (
    ASSETS_DIRECTORY_NAME,
    ASSETS_MARKER_FILENAME,
    engine_dependencies_root,
    ensure_assets_marker,
    large_assets_root,
)


class AssetStorageError(RuntimeError):
    pass


class AssetStorageCancelled(AssetStorageError):
    pass


StorageProgress = Callable[[int, int, str], None]
MANAGED_DIRECTORIES = ("models", "engine-deps", "voice-gallery", "downloads")


@dataclass(frozen=True)
class AssetTransferResult:
    base_dir: Path
    assets_root: Path
    previous_assets_root: Path
    source_directories: tuple[Path, ...]
    copied_bytes: int


class AssetStorageManager:
    """Safely copy LocalText2Voice-owned large assets to a new drive."""

    def __init__(
        self,
        current_root: Path | None = None,
        current_dependencies_root: Path | None = None,
    ) -> None:
        self.current_root = (current_root or large_assets_root()).resolve()
        self.current_dependencies_root = (
            current_dependencies_root or engine_dependencies_root()
        ).resolve()

    @staticmethod
    def assets_root_for(base_dir: Path) -> Path:
        return base_dir.expanduser().resolve() / ASSETS_DIRECTORY_NAME

    def transfer(
        self,
        base_dir: Path,
        progress_callback: StorageProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> AssetTransferResult:
        progress = progress_callback or (lambda current, total, message: None)
        destination = self.assets_root_for(base_dir)
        if destination == self.current_root:
            ensure_assets_marker(destination)
            return AssetTransferResult(
                base_dir=destination.parent,
                assets_root=destination,
                previous_assets_root=self.current_root,
                source_directories=(),
                copied_bytes=0,
            )

        sources = self._source_directories(destination)
        for source, _directory_name in sources:
            try:
                destination.relative_to(source)
            except ValueError:
                continue
            raise AssetStorageError(
                "The destination cannot be inside an existing model or engine "
                "dependency folder. Select its parent or another drive."
            )
        total_bytes = sum(self._directory_size(path) for path, _name in sources)
        self._validate_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._validate_writable(destination.parent)

        staging = destination.parent / (
            f".{ASSETS_DIRECTORY_NAME}-moving-{uuid.uuid4().hex}"
        )
        copied = 0
        try:
            staging.mkdir(parents=True, exist_ok=False)
            progress(0, max(total_bytes, 1), "Preparing AI asset storage...")
            for source, directory_name in sources:
                copied = self._copy_directory(
                    source,
                    staging / directory_name,
                    copied,
                    max(total_bytes, 1),
                    progress,
                    cancel_token,
                )
            self._check_cancelled(cancel_token)
            ensure_assets_marker(staging)
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        progress(
            max(total_bytes, 1),
            max(total_bytes, 1),
            "AI assets copied. Updating LocalText2Voice...",
        )
        return AssetTransferResult(
            base_dir=destination.parent,
            assets_root=destination,
            previous_assets_root=self.current_root,
            source_directories=tuple(source for source, _name in sources),
            copied_bytes=copied,
        )

    @staticmethod
    def cleanup_sources(result: AssetTransferResult) -> None:
        """Delete copied source folders only after settings were committed."""
        for source in result.source_directories:
            try:
                resolved = source.resolve()
                if resolved == result.assets_root.resolve():
                    continue
                if resolved.is_dir():
                    shutil.rmtree(resolved)
            except OSError:
                # The new location is already active; leftovers are harmless
                # and safer than a broad cleanup retry.
                continue

    def managed_size(self) -> int:
        return sum(
            self._directory_size(source)
            for source, _name in self._source_directories(Path("__unused__"))
        )

    def _source_directories(self, destination: Path) -> list[tuple[Path, str]]:
        sources: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for directory_name in MANAGED_DIRECTORIES:
            source = (self.current_root / directory_name).resolve()
            if source == destination.resolve() or not source.is_dir():
                continue
            if source not in seen:
                sources.append((source, directory_name))
                seen.add(source)

        dependencies = self.current_dependencies_root
        expected = (self.current_root / "engine-deps").resolve()
        if (
            dependencies != expected
            and dependencies.is_dir()
            and dependencies not in seen
        ):
            sources.append((dependencies, "engine-deps"))
        return sources

    @staticmethod
    def _validate_destination(destination: Path) -> None:
        if not destination.exists():
            return
        if not destination.is_dir():
            raise AssetStorageError(
                f"The selected AI asset location is not a folder: {destination}"
            )
        entries = [
            entry
            for entry in destination.iterdir()
            if entry.name != ASSETS_MARKER_FILENAME
        ]
        if entries:
            raise AssetStorageError(
                "The destination data folder is not empty. Select an empty folder "
                "so installed engines cannot be overwritten."
            )
        marker = destination / ASSETS_MARKER_FILENAME
        if marker.exists():
            marker.unlink()
        destination.rmdir()

    @staticmethod
    def _validate_writable(parent: Path) -> None:
        probe = parent / f".ltv-write-test-{uuid.uuid4().hex}.tmp"
        try:
            probe.write_bytes(b"LocalText2Voice")
        except OSError as exc:
            raise AssetStorageError(
                f"LocalText2Voice cannot write to the selected folder: {parent}. {exc}"
            ) from exc
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    def _copy_directory(
        self,
        source: Path,
        destination: Path,
        copied: int,
        total: int,
        progress: StorageProgress,
        cancel_token: threading.Event | None,
    ) -> int:
        for root, directory_names, file_names in os.walk(source):
            self._check_cancelled(cancel_token)
            source_root = Path(root)
            relative = source_root.relative_to(source)
            destination_root = destination / relative
            destination_root.mkdir(parents=True, exist_ok=True)
            directory_names.sort()
            file_names.sort()
            for file_name in file_names:
                self._check_cancelled(cancel_token)
                source_file = source_root / file_name
                destination_file = destination_root / file_name
                copied = self._copy_file(
                    source_file,
                    destination_file,
                    copied,
                    total,
                    progress,
                    cancel_token,
                )
        return copied

    def _copy_file(
        self,
        source: Path,
        destination: Path,
        copied: int,
        total: int,
        progress: StorageProgress,
        cancel_token: threading.Event | None,
    ) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as source_stream, destination.open("wb") as target_stream:
            while True:
                self._check_cancelled(cancel_token)
                block = source_stream.read(4 * 1024 * 1024)
                if not block:
                    break
                target_stream.write(block)
                copied += len(block)
                progress(copied, total, f"Moving {source.name}...")
        shutil.copystat(source, destination)
        if destination.stat().st_size != source.stat().st_size:
            raise AssetStorageError(f"Could not verify copied asset: {source}")
        return copied

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        if not path.is_dir():
            return total
        for root, _directories, files in os.walk(path):
            for file_name in files:
                try:
                    total += (Path(root) / file_name).stat().st_size
                except OSError:
                    continue
        return total

    @staticmethod
    def _check_cancelled(cancel_token: threading.Event | None) -> None:
        if cancel_token is not None and cancel_token.is_set():
            raise AssetStorageCancelled("AI asset move cancelled.")

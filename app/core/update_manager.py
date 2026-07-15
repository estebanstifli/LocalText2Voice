from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from packaging.version import InvalidVersion, Version

from app import __version__
from app.utils.paths import app_data_root


GITHUB_OWNER = "estebanstifli"
GITHUB_REPO = "LocalText2Voice"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
INSTALLER_ASSET_NAME = "LocalText2Voice-Setup.exe"
CHECKSUM_ASSET_NAME = f"{INSTALLER_ASSET_NAME}.sha256"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024

ProgressCallback = Callable[[int, int], None]


class UpdateError(RuntimeError):
    """Raised when an update cannot be checked, downloaded, or verified."""


class UpdateCancelled(UpdateError):
    """Raised when the user cancels an update download."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    release_name: str
    release_notes: str
    published_at: str
    installer_url: str
    installer_size: int
    checksum_url: str


def normalize_version(version: str) -> str:
    normalized = str(version).strip().lstrip("vV")
    if not normalized:
        raise UpdateError("The release does not have a valid version tag.")
    return normalized


def find_asset(release: dict[str, object], asset_name: str) -> dict[str, object] | None:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == asset_name:
            return asset
    return None


def update_info_from_release(
    release: dict[str, object],
    current_version: str = __version__,
) -> UpdateInfo | None:
    tag_name = release.get("tag_name")
    if not isinstance(tag_name, str):
        raise UpdateError("GitHub returned a release without a version tag.")

    remote_version = normalize_version(tag_name)
    local_version = normalize_version(current_version)
    try:
        if Version(remote_version) <= Version(local_version):
            return None
    except InvalidVersion as exc:
        raise UpdateError(f"Invalid release version: {exc}") from exc

    installer = find_asset(release, INSTALLER_ASSET_NAME)
    if installer is None:
        raise UpdateError(
            f"Release {remote_version} does not contain {INSTALLER_ASSET_NAME}."
        )
    checksum = find_asset(release, CHECKSUM_ASSET_NAME)
    if checksum is None:
        raise UpdateError(
            f"Release {remote_version} does not contain {CHECKSUM_ASSET_NAME}."
        )

    installer_url = installer.get("browser_download_url")
    checksum_url = checksum.get("browser_download_url")
    if not isinstance(installer_url, str) or not installer_url.startswith("https://"):
        raise UpdateError("GitHub returned an invalid installer download URL.")
    if not isinstance(checksum_url, str) or not checksum_url.startswith("https://"):
        raise UpdateError("GitHub returned an invalid checksum download URL.")

    try:
        installer_size = max(0, int(installer.get("size", 0)))
    except (TypeError, ValueError):
        installer_size = 0

    release_name = release.get("name")
    release_notes = release.get("body")
    published_at = release.get("published_at")
    return UpdateInfo(
        version=remote_version,
        release_name=(
            release_name.strip()
            if isinstance(release_name, str) and release_name.strip()
            else remote_version
        ),
        release_notes=release_notes if isinstance(release_notes, str) else "",
        published_at=published_at if isinstance(published_at, str) else "",
        installer_url=installer_url,
        installer_size=installer_size,
        checksum_url=checksum_url,
    )


class UpdateManager:
    def __init__(
        self,
        current_version: str = __version__,
        api_url: str = LATEST_RELEASE_API,
    ) -> None:
        self.current_version = current_version
        self.api_url = api_url

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"LocalText2Voice/{self.current_version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def fetch_latest_release(self) -> dict[str, object]:
        try:
            with urllib.request.urlopen(self._request(self.api_url), timeout=15) as response:
                payload = json.load(response)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateError(f"Could not contact GitHub Releases: {exc}") from exc
        if not isinstance(payload, dict):
            raise UpdateError("GitHub returned an invalid release response.")
        return payload

    def check_for_update(self) -> UpdateInfo | None:
        return update_info_from_release(
            self.fetch_latest_release(),
            self.current_version,
        )

    def download_update(
        self,
        info: UpdateInfo,
        destination_root: Path | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        destination = (destination_root or app_data_root() / "updates") / info.version
        destination.mkdir(parents=True, exist_ok=True)
        installer_path = destination / INSTALLER_ASSET_NAME
        checksum_path = destination / CHECKSUM_ASSET_NAME
        partial_path = installer_path.with_suffix(installer_path.suffix + ".part")

        checksum_bytes = self._download_checksum(info.checksum_url, cancel_event)
        expected_hash = self._parse_checksum(checksum_bytes)
        checksum_path.write_bytes(checksum_bytes)

        if installer_path.is_file():
            current_hash = self._sha256_file(installer_path, cancel_event)
            if hmac.compare_digest(current_hash, expected_hash):
                if progress_callback is not None:
                    progress_callback(info.installer_size, info.installer_size)
                return installer_path
            installer_path.unlink(missing_ok=True)

        partial_path.unlink(missing_ok=True)
        digest = hashlib.sha256()
        downloaded = 0
        total = info.installer_size
        try:
            with urllib.request.urlopen(
                self._request(info.installer_url),
                timeout=60,
            ) as response, partial_path.open("wb") as output:
                try:
                    response_size = int(response.headers.get("Content-Length", 0) or 0)
                except (TypeError, ValueError):
                    response_size = 0
                total = total or response_size
                while True:
                    self._raise_if_cancelled(cancel_event)
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)
        except UpdateCancelled:
            partial_path.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, OSError) as exc:
            partial_path.unlink(missing_ok=True)
            raise UpdateError(f"Could not download the installer: {exc}") from exc

        if info.installer_size and downloaded != info.installer_size:
            partial_path.unlink(missing_ok=True)
            raise UpdateError(
                "The downloaded installer size does not match the GitHub release asset."
            )
        if not hmac.compare_digest(digest.hexdigest(), expected_hash):
            partial_path.unlink(missing_ok=True)
            raise UpdateError("SHA-256 verification failed for the downloaded installer.")

        os.replace(partial_path, installer_path)
        return installer_path

    def _download_checksum(
        self,
        url: str,
        cancel_event: threading.Event | None,
    ) -> bytes:
        self._raise_if_cancelled(cancel_event)
        try:
            with urllib.request.urlopen(self._request(url), timeout=30) as response:
                payload = response.read(MAX_CHECKSUM_BYTES + 1)
        except (urllib.error.URLError, OSError) as exc:
            raise UpdateError(f"Could not download the SHA-256 checksum: {exc}") from exc
        self._raise_if_cancelled(cancel_event)
        if len(payload) > MAX_CHECKSUM_BYTES:
            raise UpdateError("The SHA-256 checksum asset is unexpectedly large.")
        return payload

    @staticmethod
    def _parse_checksum(payload: bytes) -> str:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UpdateError("The SHA-256 checksum asset is not valid UTF-8 text.") from exc
        match = re.search(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text)
        if match is None:
            raise UpdateError("The SHA-256 checksum asset does not contain a valid hash.")
        return match.group(0).lower()

    @classmethod
    def _sha256_file(
        cls,
        path: Path,
        cancel_event: threading.Event | None,
    ) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
                cls._raise_if_cancelled(cancel_event)
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled("The update download was cancelled.")


def launch_installer(installer_path: Path) -> None:
    if not sys.platform.startswith("win"):
        raise UpdateError("Automatic installation is only available on Windows.")
    path = installer_path.resolve()
    if not path.is_file() or path.name != INSTALLER_ASSET_NAME:
        raise UpdateError("The verified update installer could not be found.")
    try:
        subprocess.Popen([str(path)], cwd=str(path.parent), close_fds=True)
    except OSError as exc:
        raise UpdateError(f"Could not launch the update installer: {exc}") from exc

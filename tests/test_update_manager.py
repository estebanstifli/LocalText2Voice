from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.update_manager import (
    CHECKSUM_ASSET_NAME,
    INSTALLER_ASSET_NAME,
    UpdateCancelled,
    UpdateError,
    UpdateInfo,
    UpdateManager,
    update_info_from_release,
)


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, content_length: int | None = None) -> None:
        super().__init__(payload)
        self.headers = {
            "Content-Length": str(
                len(payload) if content_length is None else content_length
            )
        }

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _release(version: str, *, checksum: bool = True) -> dict[str, object]:
    assets: list[dict[str, object]] = [
        {
            "name": INSTALLER_ASSET_NAME,
            "size": 123,
            "browser_download_url": "https://github.com/example/installer",
        }
    ]
    if checksum:
        assets.append(
            {
                "name": CHECKSUM_ASSET_NAME,
                "browser_download_url": "https://github.com/example/checksum",
            }
        )
    return {
        "tag_name": version,
        "name": f"LocalText2Voice {version}",
        "body": "Release notes",
        "published_at": "2026-07-15T10:00:00Z",
        "assets": assets,
    }


class UpdateManagerTests(unittest.TestCase):
    def test_newer_stable_release_is_parsed(self) -> None:
        info = update_info_from_release(_release("v1.2.0"), "1.1.0")

        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.installer_size, 123)
        self.assertTrue(info.installer_url.startswith("https://"))
        self.assertTrue(info.checksum_url.startswith("https://"))

    def test_equal_or_older_release_does_not_offer_an_update(self) -> None:
        self.assertIsNone(update_info_from_release(_release("v1.1.0"), "1.1.0"))
        self.assertIsNone(update_info_from_release(_release("v1.0.9"), "1.1.0"))

    def test_new_release_requires_checksum_asset(self) -> None:
        with self.assertRaisesRegex(UpdateError, CHECKSUM_ASSET_NAME):
            update_info_from_release(_release("v1.2.0", checksum=False), "1.1.0")

    def test_download_verifies_checksum_and_uses_stable_filename(self) -> None:
        installer_payload = b"verified LocalText2Voice installer"
        expected_hash = hashlib.sha256(installer_payload).hexdigest()
        checksum_payload = f"{expected_hash}  {INSTALLER_ASSET_NAME}\n".encode()
        info = UpdateInfo(
            version="1.2.0",
            release_name="LocalText2Voice 1.2.0",
            release_notes="",
            published_at="",
            installer_url="https://github.com/example/installer",
            installer_size=len(installer_payload),
            checksum_url="https://github.com/example/checksum",
        )
        progress: list[tuple[int, int]] = []

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "app.core.update_manager.urllib.request.urlopen",
                side_effect=[_Response(checksum_payload), _Response(installer_payload)],
            ):
                path = UpdateManager("1.1.0").download_update(
                    info,
                    Path(temporary_directory),
                    lambda downloaded, total: progress.append((downloaded, total)),
                )

            self.assertEqual(path.name, INSTALLER_ASSET_NAME)
            self.assertEqual(path.read_bytes(), installer_payload)
            self.assertTrue(path.with_suffix(".exe.sha256").is_file())
            self.assertEqual(progress[-1], (len(installer_payload), len(installer_payload)))

    def test_download_rejects_bad_checksum_and_removes_partial_file(self) -> None:
        installer_payload = b"tampered installer"
        checksum_payload = f"{'0' * 64}  {INSTALLER_ASSET_NAME}\n".encode()
        info = UpdateInfo(
            version="1.2.0",
            release_name="LocalText2Voice 1.2.0",
            release_notes="",
            published_at="",
            installer_url="https://github.com/example/installer",
            installer_size=len(installer_payload),
            checksum_url="https://github.com/example/checksum",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch(
                "app.core.update_manager.urllib.request.urlopen",
                side_effect=[_Response(checksum_payload), _Response(installer_payload)],
            ):
                with self.assertRaisesRegex(UpdateError, "SHA-256"):
                    UpdateManager("1.1.0").download_update(info, root)

            version_root = root / info.version
            self.assertFalse((version_root / INSTALLER_ASSET_NAME).exists())
            self.assertFalse((version_root / f"{INSTALLER_ASSET_NAME}.part").exists())

    def test_cancelled_download_does_not_open_network_connection(self) -> None:
        info = UpdateInfo(
            version="1.2.0",
            release_name="LocalText2Voice 1.2.0",
            release_notes="",
            published_at="",
            installer_url="https://github.com/example/installer",
            installer_size=1,
            checksum_url="https://github.com/example/checksum",
        )
        cancel_event = threading.Event()
        cancel_event.set()

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "app.core.update_manager.urllib.request.urlopen"
            ) as urlopen_mock:
                with self.assertRaises(UpdateCancelled):
                    UpdateManager("1.1.0").download_update(
                        info,
                        Path(temporary_directory),
                        cancel_event=cancel_event,
                    )
            urlopen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

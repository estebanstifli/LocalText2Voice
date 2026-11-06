from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

import pytest

from tools.ci.prepare_windows_assets import download, extract_checked
from tools.write_windows_version_info import EXECUTABLES, ROOT, app_version, version_resource


@pytest.mark.parametrize("name", EXECUTABLES)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE resource round trip")
def test_windows_metadata_has_product_and_version(name, tmp_path):
    from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
    file = tmp_path / "version.txt"
    file.write_text(version_resource(name, app_version()), encoding="utf-8")
    info = load_version_info_from_text_file(str(file))
    values = {item.name: item.val for item in info.kids[0].kids[0].kids}
    assert values["ProductName"] == "LocalText2Voice"
    assert values["ProductVersion"] == app_version()
    assert values["OriginalFilename"] == name + ".exe"
    assert info.toRaw()


@pytest.mark.parametrize("version", ["2.2.0rc1", "65536.0.0", "2.2", "2.2.0\nmalicious"])
def test_invalid_windows_version_is_rejected(version, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text(f"__version__ = {version!r}", encoding="utf-8")
    with pytest.raises(ValueError):
        app_version(tmp_path)


def test_download_requires_matching_hash_and_cleans_temporary_file(tmp_path):
    target = tmp_path / "tool.zip"
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"valid")):
        download("https://example.org/tool", hashlib.sha256(b"valid").hexdigest(), target)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"tampered")):
        with pytest.raises(ValueError, match="SHA-256"):
            download("https://example.org/tool", hashlib.sha256(b"valid").hexdigest(), target)
    assert target.read_bytes() == b"valid"
    assert not target.with_suffix(".zip.tmp").exists()


@pytest.mark.parametrize("name", ["../outside.exe", "/outside.exe", "C:/outside.exe", "..\\outside.exe"])
def test_zip_rejects_paths_outside_destination(name, tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr(name, b"bad")
    with pytest.raises(ValueError):
        extract_checked(archive, tmp_path / "extracted")
    assert not (tmp_path / "outside.exe").exists()


def test_zip_accepts_valid_runtime(tmp_path):
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("piper/piper.exe", b"runtime")
    extract_checked(archive, tmp_path / "extracted")
    assert (tmp_path / "extracted/piper/piper.exe").read_bytes() == b"runtime"

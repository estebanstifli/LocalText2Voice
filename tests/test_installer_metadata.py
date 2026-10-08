from pathlib import Path
import shutil
import struct
import sys
from unittest.mock import patch

import pytest

from tools.normalize_installer_metadata import metadata_edits, normalize
from tools.write_windows_version_info import version_resource

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PE packaging")


@pytest.fixture
def installer(tmp_path):
    import PyInstaller
    from PyInstaller.utils.win32 import versioninfo

    bootloader = Path(PyInstaller.__file__).parent / "bootloader/Windows-64bit-intel/run.exe"
    path = tmp_path / "LocalText2Voice-Setup.exe"
    shutil.copyfile(bootloader, path)
    resource = tmp_path / "version.txt"
    resource.write_text(
        version_resource("LocalText2Voice", "2.2.0")
        .replace("'LocalText2Voice'", "'LocalText2Voice     '")
        .replace("'2.2.0'", "'2.2.0     '"), encoding="utf-8"
    )
    versioninfo.write_version_info_to_executable(str(path), versioninfo.load_version_info_from_text_file(str(resource)))
    with path.open("ab") as out:
        out.write(b"Inno payload stand-in: do not move or modify" * 64)
    return path


def test_metadata_is_exact_and_all_other_bytes_are_preserved(installer):
    import pefile

    before = installer.read_bytes()
    edits, checksum_offset = metadata_edits(installer, "2.2.0")
    assert normalize(installer, "2.2.0") == 2
    after = installer.read_bytes()
    expected = bytearray(before)
    for offset, data in edits:
        expected[offset:offset + len(data)] = data
    expected[checksum_offset:checksum_offset + 4] = after[checksum_offset:checksum_offset + 4]
    assert after == expected
    with pefile.PE(str(installer), fast_load=True) as pe:
        assert pe.verify_checksum()
        pe.parse_data_directories(directories=[2])
        values = pe.FileInfo[0][0].StringTable[0].entries
        assert values[b"ProductName"] == b"LocalText2Voice"
        assert values[b"ProductVersion"] == b"2.2.0"
    assert normalize(installer, "2.2.0") == 0
    assert installer.read_bytes() == after


def test_wrong_version_leaves_installer_untouched(installer):
    before = installer.read_bytes()
    with pytest.raises(ValueError, match="Unexpected ProductVersion"):
        normalize(installer, "9.9.9")
    assert installer.read_bytes() == before


def test_signed_file_is_never_modified(installer):
    import pefile
    with pefile.PE(str(installer), fast_load=True) as pe:
        offset = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].get_file_offset()
    with installer.open("r+b") as out:
        out.seek(offset)
        out.write(struct.pack("<II", installer.stat().st_size - 16, 16))
    before = installer.read_bytes()
    with pytest.raises(ValueError, match="already signed"):
        normalize(installer, "2.2.0")
    assert installer.read_bytes() == before


def test_failed_write_preserves_original_and_removes_temporary(installer):
    before = installer.read_bytes()
    with patch("tools.normalize_installer_metadata.update_checksum", side_effect=OSError("test failure")):
        with pytest.raises(OSError, match="test failure"):
            normalize(installer, "2.2.0")
    assert installer.read_bytes() == before
    assert not list(installer.parent.glob("installer-metadata-*.tmp"))

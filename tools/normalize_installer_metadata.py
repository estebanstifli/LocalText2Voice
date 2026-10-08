"""Remove Inno's fixed-width PE string padding before signing, without repacking.

Only our unsigned installer is accepted. Resource allocation sizes and the Inno
overlay stay unchanged; Windows version strings get a proper early terminator.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import shutil
import struct
import tempfile

import pefile


def metadata_edits(path: Path, version: str) -> tuple[list[tuple[int, bytes]], int]:
    expected = {b"ProductName": "LocalText2Voice", b"ProductVersion": version}
    edits = []
    found = set()
    with pefile.PE(str(path), fast_load=True) as pe:
        security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]]
        if security.VirtualAddress or security.Size:
            raise ValueError("Refusing to modify an already signed installer")
        checksum_offset = pe.OPTIONAL_HEADER.get_field_absolute_offset("CheckSum")
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for group in getattr(pe, "FileInfo", []):
            for info in group:
                for table in getattr(info, "StringTable", []):
                    for key, value in expected.items():
                        if key not in table.entries:
                            raise ValueError(f"Missing installer metadata: {key!r}")
                        actual = table.entries[key].decode("utf-8")
                        if actual.rstrip(" ") != value:
                            raise ValueError(f"Unexpected {key.decode()}: {actual!r}")
                        found.add(key)
                        if actual == value:
                            continue
                        key_offset, offset = table.entries_offsets[key]
                        capacity = struct.unpack_from("<H", pe.__data__, key_offset - 4)[0]
                        old = (actual + "\0").encode("utf-16-le")
                        if len(old) != capacity * 2 or pe.__data__[offset:offset + len(old)] != old:
                            raise ValueError("Unexpected PE version string layout")
                        section = pe.get_section_by_rva(pe.get_rva_from_offset(offset))
                        if section is None or offset + len(old) > section.PointerToRawData + section.SizeOfRawData:
                            raise ValueError("Version string extends outside its PE section")
                        # wLength stays unchanged: other resources and the payload never move.
                        encoded = (value + "\0").encode("utf-16-le")
                        edits.append((offset, encoded.ljust(len(old), b"\0")))
                        edits.append((key_offset - 4, struct.pack("<H", len(encoded) // 2)))
        if found != set(expected):
            raise ValueError("Installer has no expected version resource")
    return edits, checksum_offset


def update_checksum(path: Path, offset: int) -> None:
    function = ctypes.WinDLL("imagehlp").MapFileAndCheckSumW
    function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    function.restype = wintypes.DWORD
    header, checksum = wintypes.DWORD(), wintypes.DWORD()
    if function(str(path), ctypes.byref(header), ctypes.byref(checksum)):
        raise OSError("Windows could not calculate the installer PE checksum")
    with path.open("r+b") as file:
        file.seek(offset)
        file.write(struct.pack("<I", checksum.value))


def normalize(path: Path, version: str) -> int:
    path = path.resolve(strict=True)
    if path.name != "LocalText2Voice-Setup.exe":
        raise ValueError("Only LocalText2Voice-Setup.exe may be normalized")
    edits, checksum_offset = metadata_edits(path, version)
    if not edits:
        return 0
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="installer-metadata-", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        shutil.copyfile(path, temporary)
        with temporary.open("r+b") as file:
            for offset, data in edits:
                file.seek(offset)
                file.write(data)
        update_checksum(temporary, checksum_offset)
        remaining, _ = metadata_edits(temporary, version)
        if remaining or temporary.stat().st_size != path.stat().st_size:
            raise ValueError("Installer metadata normalization failed verification")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(edits) // 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    count = normalize(args.installer, args.version)
    print(f"Installer metadata verified: LocalText2Voice {args.version}; normalized {count} string(s).")

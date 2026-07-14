from __future__ import annotations

import ctypes
import sys
from pathlib import Path


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_paths() -> list[tuple[int, str]]:
    if not sys.platform.startswith("win"):
        return []
    psapi = ctypes.WinDLL("Psapi.dll")
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    process_ids = (ctypes.c_ulong * 4096)()
    bytes_returned = ctypes.c_ulong()
    if not psapi.EnumProcesses(
        ctypes.byref(process_ids),
        ctypes.sizeof(process_ids),
        ctypes.byref(bytes_returned),
    ):
        return []
    count = bytes_returned.value // ctypes.sizeof(ctypes.c_ulong)
    rows: list[tuple[int, str]] = []
    for pid in process_ids[:count]:
        if not pid:
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                rows.append((int(pid), buffer.value))
        finally:
            kernel32.CloseHandle(handle)
    return rows


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_dist_not_running.py <dist-dir>")
        return 2
    dist_dir = Path(sys.argv[1])
    if not dist_dir.exists():
        return 0
    root = str(dist_dir.resolve()).casefold()
    running = [
        (pid, path)
        for pid, path in _process_paths()
        if path.casefold().startswith(root)
    ]
    if not running:
        return 0
    print("Close LocalText2Voice and its local engine workers before building:")
    for pid, path in running:
        print(f"  PID {pid}: {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Serialize project snapshots across threads and engine-host processes."""

from __future__ import annotations

import os
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks_guard = threading.Lock()
_locks: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)


@contextmanager
def project_manifest_lock(project_dir: Path) -> Iterator[None]:
    key = os.path.normcase(str(project_dir.resolve()))
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    # Keep this file: unlinking an unlocked lock file races other open handles.
    project_dir.mkdir(parents=True, exist_ok=True)
    with lock, (project_dir / ".manifest.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (13, 36):
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

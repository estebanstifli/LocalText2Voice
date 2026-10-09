"""Serialize project snapshots across threads and engine-host processes."""

from __future__ import annotations

import os
import threading
import time
import weakref
from functools import wraps
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks_guard = threading.Lock()
_locks: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_store_depth = threading.local()


@contextmanager
def store_operation_lock(db_path: Path) -> Iterator[None]:
    """A reentrant, cross-process lock outside movable project directories."""
    key = os.path.normcase(str(db_path.resolve()))
    depths = getattr(_store_depth, "depths", None)
    if depths is None:
        depths = _store_depth.depths = {}
    if depths.get(key, 0):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    with project_manifest_lock(db_path.parent / f".{db_path.name}.operations"):
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key)


def serialized_project_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with store_operation_lock(self.db_path):
            return method(self, *args, **kwargs)
    return wrapped


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

"""Run Windows packaging without DLL search paths inherited from other apps.

Issue #24: a Poppler directory on PATH supplied an incompatible icuuc.dll
instead of the Windows ICU library required by Qt6Core.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def build_environment() -> dict[str, str]:
    environment = dict(os.environ)
    windows_directory = Path(os.environ["SystemRoot"])
    environment["PATH"] = os.pathsep.join(
        str(path)
        for path in (
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            windows_directory / "System32",
            windows_directory,
        )
    )
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML_IMPORT_PATH",
        "QML2_IMPORT_PATH",
    ):
        environment.pop(name, None)
    return environment


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This packaging helper is for Windows builds only.")
    return subprocess.call(
        [sys.executable, "-I", "-m", "PyInstaller", *sys.argv[1:]],
        env=build_environment(),
    )


if __name__ == "__main__":
    raise SystemExit(main())

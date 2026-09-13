"""Reject bundled ICU DLLs that shadow the Windows ICU contract used by Qt.

Run before publishing a portable folder or compiling an installer, including
when reusing a previously built folder with -SkipAppBuild. The current Qt
Windows wheels use the OS ICU library; redistributing a different icuuc.dll
under that name can make the very first QtGui import fail (issue #24).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def check_bundle(directory: Path) -> list[str]:
    errors = []
    internal = directory / "_internal"
    if not (internal / "PySide6" / "Qt6Core.dll").is_file():
        errors.append(f"Qt6Core.dll is missing from {internal / 'PySide6'}")
    # Check only the GUI's library locations, not optional engine environments.
    for location in (directory, internal, internal / "PySide6", internal / "shiboken6"):
        if not location.is_dir():
            continue
        for candidate in location.iterdir():
            if candidate.is_file() and candidate.name.casefold() == "icuuc.dll":
                errors.append(
                    f"Unexpected bundled Windows ICU replacement: {candidate}. "
                    "Rebuild using tools/run_pyinstaller.py; Qt must use the OS ICU DLL."
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Portable application folder")
    arguments = parser.parse_args()
    errors = check_bundle(arguments.directory.resolve())
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Qt bundle check passed: no bundled ICU overrides the Windows library.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

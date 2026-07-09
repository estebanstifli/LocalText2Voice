from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tts.python_runtime_manager import PythonRuntimeManager


def main() -> int:
    runtime_dir = Path("build") / "python_runtime" / "python311"
    manager = PythonRuntimeManager(runtime_dir)

    def progress(current: int, total: int, message: str) -> None:
        print(f"[{current}/{total}] {message}")

    if manager.is_installed():
        print(f"Embedded Python runtime already prepared: {manager.python_exe}")
    else:
        print("Preparing embedded Python runtime for the portable build...")
        manager.install(progress)
    print(f"Python runtime: {manager.python_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

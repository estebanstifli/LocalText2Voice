from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: stamp_python_runtime.py <runtime-dir>")
        return 2
    runtime_dir = Path(sys.argv[1]).resolve()
    manifest_path = runtime_dir / "python-runtime-install.json"
    if not manifest_path.is_file():
        print(f"Python runtime manifest not found: {manifest_path}")
        return 1
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["python_path"] = str(runtime_dir / "python" / "python.exe")
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Stamped Python runtime manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

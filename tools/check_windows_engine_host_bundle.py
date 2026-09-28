"""Validate resources required by the frozen headless storyboard workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def check_bundle(directory: Path) -> list[str]:
    executable = directory / "LocalText2VoiceEngineHost.exe"
    if not executable.is_file():
        return [f"EngineHost is missing: {executable}"]
    try:
        archive = CArchiveReader(str(executable))
        names = {name.replace("\\", "/"): name for name in archive.toc}
        required = [
            "assets/video_storyboard_styles.json",
            "assets/comfyui/wan22_rapid_aio_q4_i2v_640x360_api.json",
            "assets/comfyui/ltx_2_3_i2v_h100_api.json",
            "litellm/model_prices_and_context_window_backup.json",
            *(f"assets/storyboard_engines/{role}/manifest.json"
              for role in ("llm", "image", "video", "edit")),
        ]
        errors = [f"EngineHost resource is missing: {name}"
                  for name in required if name not in names]
        for name in required:
            if name in names:
                document = json.loads(archive.extract(names[name]).decode("utf-8-sig"))
                if not isinstance(document, (dict, list)) or not document:
                    errors.append(f"EngineHost resource is empty or invalid: {name}")
        for name in names:
            if Path(name).name.casefold() == "icuuc.dll":
                errors.append(f"EngineHost bundles an incompatible Windows ICU override: {name}")
        return errors
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Cannot validate EngineHost resources: {exc}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    errors = check_bundle(args.directory.resolve())
    if errors:
        for error in errors:
            print(error)
        return 1
    print("EngineHost bundle check passed: storyboard and LiteLLM resources are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
